"""交班记录的读取与逐行校验。

输入列（顺序固定）：
    shelf, source, business_time, outcome, arrival_seq, record_id, note

- business_time 为业务时点（ISO 8601，允许带时区或 UTC 的 Z 后缀）。
- arrival_seq 仅描述断网恢复后的到达顺序，仲裁不得使用该字段排序，
  保留它只是为了证明"晚到不覆盖已确认结果"。
- 校验失败的行进入隔离（isolated），使所属组依据不全、不能签收。
"""
import re
from datetime import datetime, timezone

from app.business import DomainIssue
from app.handover.sources import canonical_outcome, canonical_source

HEADER = (
    "shelf",
    "source",
    "business_time",
    "outcome",
    "arrival_seq",
    "record_id",
    "note",
)

# 完整业务时点必须含时分秒，可选秒级小数与时区；纯日期不予受理
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def parse_business_time(value):
    text = value.strip()
    if not text or not _TIMESTAMP.fullmatch(text):
        raise DomainIssue("BAD_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise DomainIssue("BAD_TIMESTAMP") from error
    if parsed.tzinfo is None:
        # 无时区的业务时点按 UTC 处理，保证同组时点可比较
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_row(values, line):
    """把一行原始字符串转成规范化记录；失败返回隔离条目。"""
    if len(values) != len(HEADER):
        return None, dict(line=line, reason="COLUMN_COUNT", raw=values)
    row = dict(zip(HEADER, (v.strip() for v in values)))
    if not row["shelf"]:
        return None, dict(line=line, reason="EMPTY_SHELF", record_id=row["record_id"])
    try:
        row["source"] = canonical_source(row["source"])
    except KeyError:
        return None, dict(
            line=line, reason="UNKNOWN_SOURCE", shelf=row["shelf"],
            record_id=row["record_id"],
        )
    try:
        row["outcome"] = canonical_outcome(row["outcome"])
    except KeyError:
        return None, dict(
            line=line, reason="UNKNOWN_OUTCOME", shelf=row["shelf"],
            record_id=row["record_id"],
        )
    try:
        row["business_time"] = parse_business_time(row["business_time"])
    except DomainIssue:
        return None, dict(
            line=line, reason="BAD_TIMESTAMP", shelf=row["shelf"],
            record_id=row["record_id"],
        )
    try:
        row["arrival_seq"] = int(row["arrival_seq"])
    except ValueError:
        return None, dict(
            line=line, reason="BAD_ARRIVAL_SEQ", shelf=row["shelf"],
            record_id=row["record_id"],
        )
    if not row["record_id"]:
        return None, dict(line=line, reason="EMPTY_RECORD_ID", shelf=row["shelf"])
    return row, None


def parse_records(rows):
    """rows: 已由 csv.reader 拆出的字符串行（不含表头）。

    返回 (valid, isolated)。valid 为规范化字典，business_time 为
    带 UTC 时区的 datetime；isolated 保留行号与原因。
    """
    valid, isolated = [], []
    for line, values in rows:
        if not any(v.strip() for v in values):
            isolated.append(dict(line=line, reason="BLANK_ROW"))
            continue
        row, error = parse_row(values, line)
        if error is not None:
            isolated.append(error)
        else:
            valid.append(row)
    return valid, isolated
