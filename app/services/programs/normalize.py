"""Normalisation and data-note detection for customer source rows.

Matching keys are DERIVED here and never written back over the originals.
"""
import re
from typing import List, Optional

# Operational notes customers type into name fields. Detected and FLAGGED -
# never acted on: a name field saying "DISQUALIFIED" does not change a status.
DATA_NOTE_PATTERNS = (
    ("NOT INTERESTED", r"NOT\s+INTERESTED"),
    ("DISQUALIFIED", r"DIS\s*QUALIFIED"),
    ("FULLY PREPLANNED", r"FULLY\s+PRE[\s-]*PLANNED"),
    ("PREPLANNED", r"PRE[\s-]*PLANNED"),
    ("DECEASED", r"DECEASED"),
    ("DO NOT CONTACT", r"DO\s+NOT\s+(CALL|CONTACT|TEXT|EMAIL)|\bDNC\b"),
    ("WRONG NUMBER", r"WRONG\s+(NUMBER|PERSON)"),
    ("DUPLICATE", r"\bDUP(LICATE)?\b"),
)
DATA_NOTE_LABEL = "SOURCE DATA NOTE DETECTED — REVIEW"
_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def data_notes(*fields: Optional[str]) -> List[str]:
    text = " ".join(f or "" for f in fields).upper()
    found = []
    for label, pat in DATA_NOTE_PATTERNS:
        if re.search(pat, text):
            if label == "PREPLANNED" and "FULLY PREPLANNED" in found:
                continue
            found.append(label)
    return found


def _strip_notes(s: str) -> str:
    up = (s or "").upper()
    for _, pat in DATA_NOTE_PATTERNS:
        up = re.sub(pat, " ", up)
    return up


def person_key(first: Optional[str], last: Optional[str]) -> str:
    """'HENRY FULLY PREPLANNED' + 'EXAMPLE JR' -> 'HENRY EXAMPLE JR'.

    Generational suffixes are KEPT: a father and son sharing a household phone
    are two people, and "same name" must not make them one."""
    words = re.sub(r"[^A-Z ]", " ", _strip_notes(f"{first or ''} {last or ''}")).split()
    return " ".join(words)


def phone_key(phone: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def email_key(email: Optional[str]) -> Optional[str]:
    e = (email or "").strip().lower()
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e) else None


def location_key(name: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()
