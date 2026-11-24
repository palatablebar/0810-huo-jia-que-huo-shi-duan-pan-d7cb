"""交班仲裁对外服务：原始 CSV 字节 → 可交接结论报告。"""
from csv import Error

from app.business import DomainIssue
from app.handover.arbitrate import build_report
from app.handover.reader import read_csv
from app.handover.schema import parse_records


def handover(data):
    try:
        rows = read_csv(data)
    except Error as error:
        raise DomainIssue("CSV_MALFORMED") from error
    valid, isolated = parse_records(rows)
    return build_report(valid, isolated)
