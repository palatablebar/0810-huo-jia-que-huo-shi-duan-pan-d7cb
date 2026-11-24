"""交班仲裁规则测试。

四条交接口径逐条覆盖：
- 规则 1：低等级/早时点只留作未采信依据，不能改写当前结果；
- 规则 2：同等级同时点内容不一致 → 待核并退出交接；
- 规则 3：每条生效结论可追溯全部采信与未采信来源（输入守恒）；
- 规则 4：依据不全的组不能签收。
另含到达顺序无关性、回放、隔离与读取异常测试。
"""
import io
from csv import writer
from pathlib import Path

import pytest

from app.business import DomainIssue
from app.handover.arbitrate import (
    CONFIRMED,
    NO_EVIDENCE,
    PENDING_CONFLICT,
    arbitrate_group,
    build_report,
)
from app.handover.schema import HEADER, parse_records
from app.handover.service import handover

ROOT = Path(__file__).resolve().parents[1]


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer(stream).writerows([HEADER, *rows])
    return stream.getvalue().encode()


def row(shelf, source, ts, outcome, seq, rid, note=""):
    return [shelf, source, ts, outcome, str(seq), rid, note]


def group_for(report, shelf):
    return next(g for g in report["groups"] if g["shelf"] == shelf)


def reasons(group):
    return {item["record_id"]: item["reason"] for item in group["rejected"]}


# ---------- 规则 1：低等级/早时点不得改写当前结果 ----------


def test_lower_source_late_arrival_keeps_confirmed_snapshot():
    data = csv_bytes(
        [
            row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 1, "r1"),
            row("S1", "巡检", "2026-11-24T08:30:00Z", "有货", 2, "r2"),
            row("S1", "补录", "2026-11-24T08:45:00Z", "有货", 3, "r3"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["status"] == CONFIRMED
    assert group["conclusion"] == "oos"
    assert [a["record_id"] for a in group["accepted"]] == ["r1"]
    assert reasons(group) == {"r2": "LOWER_SOURCE", "r3": "LOWER_SOURCE"}
    assert group["signoff_eligible"] is True


def test_earlier_business_time_same_source_is_superseded():
    data = csv_bytes(
        [
            row("S1", "系统快照", "2026-11-24T08:00:00Z", "有货", 1, "old"),
            row("S1", "系统快照", "2026-11-24T09:00:00Z", "缺货", 2, "new"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["conclusion"] == "oos"
    assert reasons(group) == {"old": "EARLIER_BUSINESS_TIME"}


def test_arrival_order_never_drives_the_conclusion():
    # 每条记录自身的到达序号不变，只改变文件行序，结论与报告必须一致
    ordered = [
        row("S1", "补录", "2026-11-24T08:45:00Z", "有货", 1, "low"),
        row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 2, "high"),
    ]
    forward = csv_bytes(ordered)
    reverse = csv_bytes(list(reversed(ordered)))
    assert handover(forward) == handover(reverse)
    group = group_for(handover(forward), "S1")
    assert group["accepted"][0]["record_id"] == "high"
    assert reasons(group) == {"low": "LOWER_SOURCE"}


def test_shuffled_arrival_seq_does_not_change_report():
    rows = [
        row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 9, "a"),
        row("S1", "巡检", "2026-11-24T08:30:00Z", "有货", 4, "b"),
        row("S1", "补录", "2026-11-24T08:45:00Z", "有货", 7, "c"),
    ]
    base = handover(csv_bytes(rows))
    shuffled = handover(csv_bytes(list(reversed(rows))))
    assert base == shuffled
    # 结论只认来源等级，乱序的到达序号被完整保留在追溯视图中
    group = group_for(base, "S1")
    assert group["accepted"][0]["record_id"] == "a"
    assert {x["arrival_seq"] for x in group["accepted"] + group["rejected"]} == {
        9,
        4,
        7,
    }


# ---------- 规则 2：同等级同时点内容不一致 → 待核退出 ----------


def test_equal_source_equal_time_conflict_forces_pending():
    data = csv_bytes(
        [
            row("S1", "巡检", "2026-11-24T08:30:00Z", "缺货", 1, "x"),
            row("S1", "巡检", "2026-11-24T08:30:00Z", "有货", 2, "y"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["status"] == PENDING_CONFLICT
    assert group["conclusion"] is None
    assert group["signoff_eligible"] is False
    assert "PENDING_CONFLICT" in group["blockers"]
    assert {d["record_id"] for d in group["disputed"]} == {"x", "y"}
    assert group["accepted"] == []


def test_conflict_group_exits_handoff_but_keeps_other_evidence():
    data = csv_bytes(
        [
            row("S1", "巡检", "2026-11-24T08:30:00Z", "缺货", 1, "x"),
            row("S1", "巡检", "2026-11-24T08:30:00Z", "有货", 2, "y"),
            row("S1", "补录", "2026-11-24T07:50:00Z", "有货", 3, "early"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["status"] == PENDING_CONFLICT
    # 更早、更低的记录仍保留为未采信依据，不被争议记录吞掉
    assert reasons(group) == {"early": "LOWER_SOURCE"}
    assert group["counts"]["disputed"] == 2


def test_different_level_same_time_is_not_a_conflict():
    # 规则 2 只适用于"同等来源"；不同等级同时点由高等级直接生效
    data = csv_bytes(
        [
            row("S1", "巡检", "2026-11-24T08:30:00Z", "有货", 1, "low"),
            row("S1", "系统快照", "2026-11-24T08:30:00Z", "缺货", 2, "high"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["status"] == CONFIRMED
    assert group["accepted"][0]["record_id"] == "high"
    assert reasons(group) == {"low": "LOWER_SOURCE"}


def test_identical_replay_is_accepted_once_not_a_conflict():
    data = csv_bytes(
        [
            row("S1", "巡检", "2026-11-24T08:20:00Z", "有货", 1, "r1"),
            row("S1", "巡检", "2026-11-24T08:20:00Z", "有货", 4, "r2"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["status"] == CONFIRMED
    assert len(group["accepted"]) == 1
    assert reasons(group) == {"r2": "REPLAY"}


# ---------- 规则 3：来源可追溯（输入守恒） ----------


def test_every_input_record_is_traced_for_each_group():
    data = csv_bytes(
        [
            row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 1, "a"),
            row("S1", "巡检", "2026-11-24T08:30:00Z", "有货", 2, "b"),
            row("S2", "补录", "2026-11-24T08:00:00Z", "有货", 1, "c"),
        ]
    )
    report = handover(data)
    assert report["totals"]["fully_traced"] is True
    for group in report["groups"]:
        counts = group["counts"]
        assert counts["input"] == (
            counts["accepted"]
            + counts["rejected"]
            + counts["disputed"]
            + counts["isolated"]
        )
        assert group["fully_traced"] is True


def test_confirmed_conclusion_carries_both_accepted_and_rejected_trace():
    data = csv_bytes(
        [
            row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 1, "a"),
            row("S1", "补录", "2026-11-24T08:45:00Z", "有货", 2, "b"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["accepted"] and group["rejected"]
    traced = {x["record_id"] for x in group["accepted"] + group["rejected"]}
    assert traced == {"a", "b"}


# ---------- 规则 4：依据不全不能签收 ----------


def test_isolated_record_blocks_signoff_even_with_confirmed_conclusion():
    data = csv_bytes(
        [
            row("S1", "系统快照", "2026-11-24T08:10:00Z", "缺货", 1, "ok"),
            row("S1", "补录", "2026-11-24T08:50:00Z", "未知状态", 2, "bad"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["status"] == CONFIRMED
    assert group["signoff_eligible"] is False
    assert "INCOMPLETE_EVIDENCE" in group["blockers"]
    assert group["isolated"][0]["reason"] == "UNKNOWN_OUTCOME"


def test_group_with_no_valid_evidence_cannot_sign_off():
    data = csv_bytes(
        [row("S1", "补录", "2026-11-24T08:00:00Z", "未知状态", 1, "bad")]
    )
    group = group_for(handover(data), "S1")
    assert group["status"] == NO_EVIDENCE
    assert group["signoff_eligible"] is False
    assert group["counts"]["isolated"] == 1


def test_handoff_ready_requires_every_group_signable():
    data = (ROOT / "sample_data" / "handover.csv").read_bytes()
    report = handover(data)
    assert report["handoff_ready"] is False
    assert report["totals"]["signoff_eligible"] < report["totals"]["groups"]
    pending = group_for(report, "C-01")
    blocked = group_for(report, "D-01")
    assert pending["signoff_eligible"] is False
    assert blocked["signoff_eligible"] is False


def test_clean_set_is_handoff_ready():
    data = csv_bytes(
        [
            row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 1, "a"),
            row("S2", "巡检", "2026-11-24T08:20:00Z", "有货", 1, "b"),
        ]
    )
    report = handover(data)
    assert report["handoff_ready"] is True
    assert all(g["signoff_eligible"] for g in report["groups"])


# ---------- 解析与隔离 ----------


@pytest.mark.parametrize(
    "bad_row, reason",
    [
        (row("S1", "未知来源", "2026-11-24T08:00:00Z", "缺货", 1, "x"), "UNKNOWN_SOURCE"),
        (row("S1", "巡检", "not-a-timestamp", "缺货", 1, "x"), "BAD_TIMESTAMP"),
        (row("S1", "巡检", "2026-11-24T08:00:00Z", "缺货", "x", "x"), "BAD_ARRIVAL_SEQ"),
    ],
)
def test_invalid_rows_are_isolated(bad_row, reason):
    valid, isolated = parse_records([(2, bad_row)])
    assert valid == [] and isolated[0]["reason"] == reason


def test_naive_timestamp_is_treated_as_utc_and_compared():
    data = csv_bytes(
        [
            row("S1", "系统快照", "2026-11-24T08:00:00", "有货", 1, "old"),
            row("S1", "系统快照", "2026-11-24T09:00:00Z", "缺货", 2, "new"),
        ]
    )
    group = group_for(handover(data), "S1")
    assert group["accepted"][0]["record_id"] == "new"


def test_blank_row_is_isolated_and_breaks_signoff():
    valid, isolated = parse_records([(3, ["", "", "", "", "", "", ""])])
    assert valid == [] and isolated[0]["reason"] == "BLANK_ROW"


def test_wrong_column_count_is_isolated():
    valid, isolated = parse_records([(2, ["S1", "巡检", "2026-11-24T08:00:00Z"])])
    assert isolated[0]["reason"] == "COLUMN_COUNT"


def test_orphan_isolation_blocks_handoff():
    # 无法识别货架的隔离行不能被任何组签收消化
    valid, isolated = parse_records(
        [(2, ["", "巡检", "2026-11-24T08:00:00Z", "缺货", "1", "x", ""])]
    )
    report = build_report(valid, isolated)
    assert report["totals"]["orphan_isolation"] == 1
    assert report["handoff_ready"] is False


# ---------- 读取异常 ----------


@pytest.mark.parametrize(
    "payload, code",
    [
        (b"", "FILE_EMPTY"),
        (b"  \r\n", "FILE_BLANK"),
        (b"shelf,source,business_time,outcome,arrival_seq,record_id,note\n", "HEADER_ONLY"),
    ],
)
def test_empty_states_have_distinct_causes(payload, code):
    with pytest.raises(DomainIssue, match=code):
        handover(payload)


def test_header_mismatch_is_rejected():
    with pytest.raises(DomainIssue, match="HEADER_MISMATCH"):
        handover(b"wrong,header\n1,2\n")


def test_bom_is_accepted():
    payload = b"\xef\xbb\xbf" + csv_bytes(
        [row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 1, "a")]
    )
    report = handover(payload)
    assert group_for(report, "S1")["conclusion"] == "oos"


def test_unclosed_quote_invalidates_source():
    payload = csv_bytes([row("S1", "巡检", "2026-11-24T08:00:00Z", "缺货", 1, "a")])
    with pytest.raises(DomainIssue, match="CSV_MALFORMED"):
        handover(payload + b'"truncated\n')


# ---------- 单元级守恒 ----------


def test_arbitrate_group_conservation_with_mixed_evidence():
    valid, isolated = parse_records(
        [
            (2, row("S1", "系统快照", "2026-11-24T08:00:00Z", "缺货", 1, "a")),
            (3, row("S1", "巡检", "2026-11-24T08:30:00Z", "有货", 2, "b")),
            (4, row("S1", "补录", "2026-11-24T08:40:00Z", "未知状态", 3, "c")),
        ]
    )
    group = arbitrate_group("S1", valid, isolated)
    assert group["counts"] == {
        "input": 3,
        "accepted": 1,
        "rejected": 1,
        "disputed": 0,
        "isolated": 1,
    }
    assert group["fully_traced"] is True
    assert group["signoff_eligible"] is False
