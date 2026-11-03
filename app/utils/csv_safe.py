"""Spreadsheet-safe cells for CSV files a PERSON downloads and opens.

A cell that starts with = + - @ (or a tab / carriage return) is run as a
formula by Excel, Google Sheets and LibreOffice ("CSV injection"). Imported
data is somebody else's text, so every human-facing report writes it through
`cell()`, which prefixes a single quote: the spreadsheet then shows the text
exactly as it was and runs nothing.

Do NOT use this for CSV that another program reads back (the importer's own
staging files, vendor uploads, the SCI alias file): there the quote would
change the data. Only for reports opened by people.
"""
from __future__ import annotations

from typing import Any, Iterable, List

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def cell(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    return "'" + s if s.startswith(_TRIGGERS) else s


def row(values: Iterable[Any]) -> List[str]:
    return [cell(v) for v in values]
