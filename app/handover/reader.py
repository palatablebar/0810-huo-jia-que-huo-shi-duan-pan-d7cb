"""交班记录 CSV 读取：复用项目对 BOM、严格引号与空文件的处理口径。"""
import csv
import io

from app.business import DomainIssue
from app.config import MAX_FILE, MAX_ROWS
from app.handover.schema import HEADER


def read_csv(data):
    """读取字节流，返回带物理行号的字符串行（不含表头）。"""
    if len(data) > MAX_FILE:
        raise DomainIssue("FILE_TOO_LARGE")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DomainIssue("ENCODING_INVALID") from error
    if not text:
        raise DomainIssue("FILE_EMPTY")
    if not text.strip():
        raise DomainIssue("FILE_BLANK")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = tuple(next(reader))
    except StopIteration as error:
        raise DomainIssue("FILE_EMPTY") from error
    if header != HEADER:
        raise DomainIssue("HEADER_MISMATCH")
    rows = []
    for number, row in enumerate(reader, 1):
        if number > MAX_ROWS:
            raise DomainIssue("ROW_LIMIT")
        rows.append((reader.line_num, row))
    if not rows:
        raise DomainIssue("HEADER_ONLY")
    return rows
