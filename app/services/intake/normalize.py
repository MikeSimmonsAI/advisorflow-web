"""Field normalization for the universal intake engine.

Every function here is PURE: value in, (normalized value, problem) out. No
database, no organization, no vertical. The original value is never
discarded by this module - callers store it beside the normalized one, because
"what did the file actually say?" is the first question anyone asks when a
record looks wrong.

Rules that matter:
  * A phone number that merely has ten digits is VALID-FORMAT, nothing more.
    Validity of format says nothing about whether the line is mobile, active,
    or allowed to receive a text. See eligibility.py.
  * An ambiguous date is never guessed. 03/04/2021 is left unparsed and
    flagged unless the column (or the operator) has settled the order.
  * A ZIP code is a string. Leading zeros are preserved; a four-digit ZIP
    that lost its zero in a spreadsheet is repaired AND reported.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Iterable, Optional, Tuple

_WS = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")
_EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
                       r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_BLANKS = {"", "nan", "none", "null", "n/a", "na", "#n/a", "-", "--", "(blank)", "undefined"}


def blank(v) -> bool:
    return v is None or str(v).strip().lower() in _BLANKS


def clean_text(v) -> Optional[str]:
    """Trim, collapse internal whitespace, strip control characters."""
    if blank(v):
        return None
    s = "".join(ch for ch in str(v) if ch == " " or unicodedata.category(ch)[0] != "C")
    s = _WS.sub(" ", s).strip()
    return s or None


# ── names ───────────────────────────────────────────────────────────────────

def name(v) -> Optional[str]:
    """Trim and normalize spacing. Case is left exactly as given: "McDonald",
    "de la Cruz" and "DJ" are all things a title-caser would break."""
    return clean_text(v)


def name_key(v) -> Optional[str]:
    """Matching key only - accents stripped, lowercase, letters and digits."""
    s = clean_text(v)
    if not s:
        return None
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", s) or None


# ── company ─────────────────────────────────────────────────────────────────

_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "l l c", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "lp", "llp", "pllc", "pc", "pa", "gmbh", "sa", "the",
}


def company(v) -> Optional[str]:
    """Display value: spacing normalized, legal name otherwise untouched."""
    return clean_text(v)


def company_key(v) -> Optional[str]:
    """Matching key: 'The Acme Co., Inc.' and 'ACME COMPANY' collapse together.
    Never shown to anyone and never written over the display name."""
    s = clean_text(v)
    if not s:
        return None
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [t for t in s.split() if t not in _COMPANY_SUFFIXES]
    key = "".join(toks)
    return key or None


# ── phone ───────────────────────────────────────────────────────────────────

PHONE_VALID = "valid"
PHONE_INVALID = "invalid"
PHONE_MISSING = "missing"


def phone(v, default_country: str = "US") -> Tuple[Optional[str], str]:
    """Return (E.164 or None, valid|invalid|missing).

    NANP numbers are checked structurally (area code and exchange cannot start
    with 0 or 1, a run of one repeated digit is not a number). International
    numbers written with a leading '+' are kept if they have 8-15 digits.
    An extension ("x204", "ext. 12") is dropped from the E.164 value; the raw
    value keeps it.
    """
    if blank(v):
        return None, PHONE_MISSING
    s = str(v).strip()
    # Several numbers in one cell: take the first, the raw value keeps the rest.
    s = re.split(r"[;,/|]| or ", s, maxsplit=1)[0]
    s = re.split(r"(?i)\s*(?:x|ext\.?|extension)\s*\d+\s*$", s)[0]
    had_plus = s.strip().startswith("+")
    if re.fullmatch(r"\d+(\.0+)?", s.strip()):       # 2145551234.0 from a spreadsheet
        s = s.strip().split(".")[0]
    d = _NON_DIGIT.sub("", s)
    if not d:
        return None, PHONE_MISSING
    if had_plus and not d.startswith("1"):
        if 8 <= len(d) <= 15:
            return "+" + d, PHONE_VALID
        return None, PHONE_INVALID
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10 or default_country.upper() not in ("US", "CA"):
        return None, PHONE_INVALID
    if d[0] in "01" or d[3] in "01" or len(set(d)) == 1:
        return None, PHONE_INVALID
    return "+1" + d, PHONE_VALID


# ── email ───────────────────────────────────────────────────────────────────

def email(v) -> Tuple[Optional[str], Optional[str]]:
    """Return (normalized address or None, problem or None).

    Lowercased and trimmed; a 'mailto:' prefix or a display-name wrapper
    ("Jane <jane@x.com>") is removed. A malformed address is returned as None
    with the problem 'invalid_format' - the raw value is kept by the caller.
    """
    if blank(v):
        return None, None
    s = str(v).strip()
    m = re.search(r"<([^>]+)>", s)
    if m:
        s = m.group(1)
    s = s.strip().strip(";,").lower()
    if s.startswith("mailto:"):
        s = s[7:]
    s = s.split(";")[0].split(",")[0].strip()
    if not _EMAIL_RE.match(s):
        return None, "invalid_format"
    return s, None


# ── state / zip / country ───────────────────────────────────────────────────

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "washington dc": "DC", "washington d c": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "puerto rico": "PR", "guam": "GU", "virgin islands": "VI",
    "us virgin islands": "VI", "american samoa": "AS", "northern mariana islands": "MP",
    "tex": "TX", "calif": "CA", "fla": "FL", "penn": "PA", "mass": "MA",
}
_STATE_CODES = set(US_STATES.values())


def state(v) -> Tuple[Optional[str], Optional[str]]:
    """'Texas', 'texas', 'TX', 'Tx.' -> 'TX'. Unknown values are kept as
    given (a Canadian province or a foreign region is not an error) and
    reported as 'unrecognized_state'."""
    s = clean_text(v)
    if not s:
        return None, None
    k = re.sub(r"[^a-z ]", " ", s.lower())
    k = _WS.sub(" ", k).strip()
    if k.upper() in _STATE_CODES and len(k) == 2:
        return k.upper(), None
    if k in US_STATES:
        return US_STATES[k], None
    return s, "unrecognized_state"


def zip_code(v) -> Tuple[Optional[str], Optional[str]]:
    """Return (zip, note). '75201', '75201-1234', '752011234' -> ZIP / ZIP+4.
    '7501' (lost its leading zero in a spreadsheet) -> '07501', note
    'zip_leading_zero_restored'. Anything else is kept verbatim with a note."""
    s = clean_text(v)
    if not s:
        return None, None
    raw = s
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    d = _NON_DIGIT.sub("", s)
    if re.fullmatch(r"\d{5}", s):
        return s, None
    if re.fullmatch(r"\d{5}[- ]\d{4}", s) or (len(d) == 9 and re.fullmatch(r"\d{9}", s)):
        return f"{d[:5]}-{d[5:]}", None
    if re.fullmatch(r"\d{3,4}", s):
        return s.zfill(5), "zip_leading_zero_restored"
    if re.fullmatch(r"\d{7,8}", s):
        return f"{d.zfill(9)[:5]}-{d.zfill(9)[5:]}", "zip_leading_zero_restored"
    return raw, "nonstandard_zip"


def zip5(z: Optional[str]) -> Optional[str]:
    if not z:
        return None
    d = _NON_DIGIT.sub("", z)
    return d[:5] if len(d) >= 5 else None


def address(v) -> Optional[str]:
    s = clean_text(v)
    if not s:
        return None
    return re.sub(r"\s*,\s*", ", ", s)


_ADDR_ABBR = {"street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
              "boulevard": "blvd", "lane": "ln", "court": "ct", "suite": "ste",
              "parkway": "pkwy", "highway": "hwy", "north": "n", "south": "s",
              "east": "e", "west": "w", "place": "pl", "circle": "cir",
              "freeway": "fwy", "expressway": "expy", "apartment": "apt"}


def address_key(v) -> Optional[str]:
    s = clean_text(v)
    if not s:
        return None
    toks = re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
    return " ".join(_ADDR_ABBR.get(t, t) for t in toks) or None


# ── booleans ────────────────────────────────────────────────────────────────

_TRUE = {"yes", "y", "true", "t", "1", "x", "checked", "on"}
_FALSE = {"no", "n", "false", "f", "0", "unchecked", "off"}


def boolean(v) -> Optional[bool]:
    if blank(v):
        return None
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


# ── dates ───────────────────────────────────────────────────────────────────

DATE_AUTO = "auto"
DATE_MDY = "mdy"
DATE_DMY = "dmy"

_ISO_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y/%m/%d", "%Y%m%d")
_TEXT_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%b %d %Y",
                 "%B %d %Y", "%d-%b-%Y", "%d-%b-%y")
_SLASH = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?$")


def slash_date_order_hint(values: Iterable) -> Optional[str]:
    """Look at a whole column and decide the day/month order if the column
    itself proves it (any first part > 12 -> DMY; any second part > 12 -> MDY).
    Returns None when the column does not settle it."""
    mdy = dmy = False
    for v in values:
        if blank(v):
            continue
        m = _SLASH.match(str(v).strip())
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            dmy = True
        elif b > 12 and a <= 12:
            mdy = True
    if mdy and not dmy:
        return DATE_MDY
    if dmy and not mdy:
        return DATE_DMY
    return None


def date(v, order: str = DATE_AUTO) -> Tuple[Optional[datetime], Optional[str]]:
    """Return (naive UTC datetime or None, problem or None).

    Problems: 'ambiguous_date' (e.g. 03/04/2021 with no order settled),
    'unparsed_date' (not a date we recognize). Never guesses."""
    if blank(v):
        return None, None
    s = str(v).strip()
    for fmt in _ISO_FORMATS:
        try:
            return datetime.strptime(s, fmt), None
        except ValueError:
            pass
    m = _SLASH.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        ap = (m.group(7) or "").lower()
        if ap == "pm" and hh < 12:
            hh += 12
        elif ap == "am" and hh == 12:
            hh = 0
        if a > 12 and b > 12:
            return None, "unparsed_date"
        if a > 12:
            month, day = b, a
        elif b > 12:
            month, day = a, b
        elif a == b:
            month, day = a, b
        elif order == DATE_MDY:
            month, day = a, b
        elif order == DATE_DMY:
            month, day = b, a
        else:
            return None, "ambiguous_date"
        try:
            return datetime(y, month, day, hh, mm, ss), None
        except ValueError:
            return None, "unparsed_date"
    for fmt in _TEXT_FORMATS:
        try:
            return datetime.strptime(s, fmt), None
        except ValueError:
            pass
    # Excel serial day number (e.g. 45123)
    if re.fullmatch(r"\d{5}(\.\d+)?", s):
        try:
            from datetime import timedelta
            n = float(s)
            if 20000 < n < 80000:
                return datetime(1899, 12, 30) + timedelta(days=n), None
        except Exception:  # noqa: BLE001
            pass
    return None, "unparsed_date"


def tags(v) -> list:
    s = clean_text(v)
    if not s:
        return []
    out, seen = [], set()
    for t in re.split(r"[;,|]", s):
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def slug(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:60] or "field"
