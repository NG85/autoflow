"""Sanitize values before writing to Excel worksheets via openpyxl."""

from typing import Any

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


def sanitize_excel_value(value: Any) -> Any:
    """Strip XML/Excel-illegal control characters from string cell values."""
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def sanitize_excel_row(row: list[Any]) -> list[Any]:
    return [sanitize_excel_value(v) for v in row]
