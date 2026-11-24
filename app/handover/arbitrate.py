"""按货架组仲裁缺货结论。

仲裁只依据两个维度，与到达顺序（arrival_seq）完全无关：

1. 来源等级（高者优先）；
2. 同一等级内的业务时点（晚者优先，代表最新确认结果）。

落选记录不删除，全部带着原因留作未采信依据：

- LOWER_SOURCE：来源等级低于采信记录，即使晚到也不能改写；
- EARLIER_BUSINESS_TIME：同等级但业务时点更早；
- REPLAY：同等级、同时点、同内容的重复回放，保留一份采信；
- 同等级、同时点但内容不一致 → 整组 PENDING_CONFLICT，退出交接。
"""
from collections import defaultdict

from app.handover.sources import OUTCOME_LABEL, level

# 组状态
CONFIRMED = "CONFIRMED"
PENDING_CONFLICT = "PENDING_CONFLICT"
NO_EVIDENCE = "NO_EVIDENCE"

# 阻断签收的原因
BLOCKER_CONFLICT = "PENDING_CONFLICT"
BLOCKER_INCOMPLETE = "INCOMPLETE_EVIDENCE"
BLOCKER_NO_EVIDENCE = "NO_EVIDENCE"


def _view(record, reason=None):
    item = {
        "record_id": record["record_id"],
        "source": record["source"],
        "source_level": level(record["source"]),
        "business_time": record["business_time"].isoformat(),
        "outcome": record["outcome"],
        "arrival_seq": record["arrival_seq"],
    }
    if reason is not None:
        item["reason"] = reason
    return item


def _reject_reason(record, top_level, latest_time):
    if level(record["source"]) < top_level:
        return "LOWER_SOURCE"
    if record["business_time"] < latest_time:
        return "EARLIER_BUSINESS_TIME"
    # 同等级、同时点、同内容：重复回放
    return "REPLAY"


def _conserved(counts):
    return (
        counts["input"]
        == counts["accepted"]
        + counts["rejected"]
        + counts["disputed"]
        + counts["isolated"]
    )


def arbitrate_group(shelf, records, isolated):
    """对单个货架组形成结论。

    records 为该组全部有效记录，isolated 为属于该组的隔离行。
    counts 满足输入守恒，保证每条来源都可追溯。
    """
    input_count = len(records) + len(isolated)
    group = {
        "shelf": shelf,
        "status": NO_EVIDENCE,
        "conclusion": None,
        "conclusion_label": None,
        "blockers": [],
        "accepted": [],
        "rejected": [],
        "disputed": [],
        "isolated": isolated,
        "counts": {
            "input": input_count,
            "accepted": 0,
            "rejected": 0,
            "disputed": 0,
            "isolated": len(isolated),
        },
        "fully_traced": False,
        "signoff_eligible": False,
    }

    if isolated:
        group["blockers"].append(BLOCKER_INCOMPLETE)
    if not records:
        group["blockers"].append(BLOCKER_NO_EVIDENCE)
        group["fully_traced"] = _conserved(group["counts"])
        return group

    top_level = max(level(r["source"]) for r in records)
    top_records = [r for r in records if level(r["source"]) == top_level]
    latest_time = max(r["business_time"] for r in top_records)
    # 同等级 + 同业务时点的候选，按 record_id 排序保证结论与到达顺序无关
    candidates = sorted(
        (r for r in top_records if r["business_time"] == latest_time),
        key=lambda r: r["record_id"],
    )
    candidate_ids = {r["record_id"] for r in candidates}
    outcomes = {r["outcome"] for r in candidates}

    if len(outcomes) > 1:
        # 规则 2：同等来源同一时点内容不一致 → 待核并退出交接
        group["status"] = PENDING_CONFLICT
        group["blockers"].append(BLOCKER_CONFLICT)
        group["disputed"] = [_view(r) for r in candidates]
        group["rejected"] = sorted(
            (
                _view(r, _reject_reason(r, top_level, latest_time))
                for r in records
                if r["record_id"] not in candidate_ids
            ),
            key=lambda item: item["record_id"],
        )
    else:
        # 规则 1：高等级/晚时点确认结果，其余只能留作未采信依据
        adopted = candidates[0]
        group["status"] = CONFIRMED
        group["conclusion"] = adopted["outcome"]
        group["conclusion_label"] = OUTCOME_LABEL[adopted["outcome"]]
        group["accepted"] = [_view(adopted)]
        group["rejected"] = sorted(
            (
                _view(r, _reject_reason(r, top_level, latest_time))
                for r in records
                if r["record_id"] != adopted["record_id"]
            ),
            key=lambda item: item["record_id"],
        )

    group["counts"]["accepted"] = len(group["accepted"])
    group["counts"]["rejected"] = len(group["rejected"])
    group["counts"]["disputed"] = len(group["disputed"])
    group["fully_traced"] = _conserved(group["counts"])
    # 规则 4：只有已确认、无隔离、来源守恒的组才能签收
    group["signoff_eligible"] = (
        group["status"] == CONFIRMED
        and not isolated
        and group["fully_traced"]
        and bool(group["accepted"])
    )
    return group


def build_report(valid, isolated):
    """汇总全部货架组的交班结论。"""
    by_shelf = defaultdict(list)
    for record in valid:
        by_shelf[record["shelf"]].append(record)

    isolated_by_shelf = defaultdict(list)
    orphan_isolation = []
    for item in isolated:
        shelf = item.get("shelf")
        if shelf:
            isolated_by_shelf[shelf].append(item)
        else:
            orphan_isolation.append(item)

    shelves = sorted(set(by_shelf) | set(isolated_by_shelf))
    groups = [
        arbitrate_group(
            shelf,
            by_shelf.get(shelf, []),
            isolated_by_shelf.get(shelf, []),
        )
        for shelf in shelves
    ]

    confirmed = sum(g["status"] == CONFIRMED for g in groups)
    pending = sum(g["status"] == PENDING_CONFLICT for g in groups)
    no_evidence = sum(g["status"] == NO_EVIDENCE for g in groups)
    signable = sum(g["signoff_eligible"] for g in groups)

    # 全量守恒：每条输入记录都必须落在采信/未采信/争议/隔离四类之一
    total_input = len(valid) + len(isolated)
    total_accounted = sum(
        g["counts"]["accepted"]
        + g["counts"]["rejected"]
        + g["counts"]["disputed"]
        + g["counts"]["isolated"]
        for g in groups
    ) + len(orphan_isolation)

    return {
        "schema": "handover-conclusion/1",
        "totals": {
            "groups": len(groups),
            "confirmed": confirmed,
            "pending_conflict": pending,
            "no_evidence": no_evidence,
            "signoff_eligible": signable,
            "valid_records": len(valid),
            "isolated_records": len(isolated),
            "orphan_isolation": len(orphan_isolation),
            "fully_traced": total_input == total_accounted
            and all(g["fully_traced"] for g in groups)
            and not orphan_isolation,
        },
        "groups": groups,
        "handoff_ready": bool(groups)
        and signable == len(groups)
        and not orphan_isolation
        and total_input == total_accounted,
        "orphan_isolation": orphan_isolation,
    }
