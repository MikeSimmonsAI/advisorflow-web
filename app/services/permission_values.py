"""
CANONICAL PERMISSION INTERPRETATION - one place, explicit tables, tested values.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
An import may RESTRICT. It may never RELEASE.

Nothing arriving in a spreadsheet can turn a prior denial into permission, and
nothing ambiguous may be read as consent. A value this module does not
recognise resolves to UNKNOWN, and UNKNOWN is not a yes.

WHY POLARITY IS NOT INFERRED FROM COLUMN NAMES
----------------------------------------------
A real production export carries a column named "Do not allow Bulk Emails"
whose cells read "Allow" and "Do Not Allow". Inverting on the column NAME turns
85,751 permissions into denials. The column name says what the flag is FOR; the
cell says which way it points.

So: a SELF-DESCRIBING value ("Allow", "Do Not Allow", "Opted Out") states the
permission itself and the column's polarity is not consulted. Only a bare
boolean ("Yes", "No", "1", "0", "true", "false") is read through the polarity
declared for that column - and that polarity is declared in a table, per column,
never guessed from the words in the header.

FOUR PERMISSIONS, NOT ONE
-------------------------
  EMAIL       - may this person be emailed at all
  BULK_EMAIL  - may this person be included in a campaign or bulk send
  SMS         - may this person be texted
  VOICE       - may this person be called

They are separate facts and are never collapsed. An advisor writing to one
family is not a bulk send; a person who opted out of campaigns has not
necessarily refused correspondence. Folding them together either blocks
correspondence nobody objected to, or lets a campaign reach somebody who opted
out of campaigns.

There is deliberately NO function here that returns "permitted" for an absent
column. Absence is UNKNOWN.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# The three states. UNKNOWN is a state, not a missing value.
# ---------------------------------------------------------------------------

ALLOW = "allow"
DENY = "deny"
UNKNOWN = "unknown"

STATES = (ALLOW, DENY, UNKNOWN)

# Permission kinds
EMAIL = "email"
BULK_EMAIL = "bulk_email"
SMS = "sms"
VOICE = "voice"

PERMISSIONS = (EMAIL, BULK_EMAIL, SMS, VOICE)


# ---------------------------------------------------------------------------
# Value tables. EXPLICIT, EXHAUSTIVE FOR EVERY VALUE OBSERVED IN PRODUCTION.
# Anything not listed is UNKNOWN. Growing these lists is a deliberate act with
# a test attached; it is not something a regex does on the platform's behalf.
# ---------------------------------------------------------------------------

# Values that state the permission themselves, whatever column they sit in.
SELF_ALLOW = frozenset({
    "allow", "allowed", "allow contact", "permitted", "permit",
    "opt in", "opt-in", "optin", "opted in", "opted-in",
    "subscribe", "subscribed", "consented", "consent",
})

SELF_DENY = frozenset({
    "do not allow", "do not allow contact", "donotallow", "do-not-allow",
    "not allowed", "not permitted", "deny", "denied", "blocked", "block",
    "opt out", "opt-out", "optout", "opted out", "opted-out",
    "unsubscribe", "unsubscribed", "suppressed", "suppress",
    "do not contact", "do not call", "do not email", "do not text",
    "do not mail", "dnc", "removed", "revoked", "withdrawn",
})

# Bare booleans. Meaningless on their own - they only acquire a direction from
# the polarity declared for the column they were found in.
BOOL_TRUE = frozenset({"yes", "y", "true", "t", "1", "1.0", "on", "checked", "x"})
BOOL_FALSE = frozenset({"no", "n", "false", "f", "0", "0.0", "off", "unchecked"})

# Values that mean the cell is empty. Blank is UNKNOWN, never consent.
BLANK_VALUES = frozenset({"", "null", "none", "n/a", "na", "nan", "-", "--",
                          "unknown", "not set", "notset", "(blank)", "#n/a"})


# ---------------------------------------------------------------------------
# Column table. polarity says how to read a BARE BOOLEAN in that column:
#   "grant"  - true means allowed        ("Allow Emails?" = Yes  -> ALLOW)
#   "deny"   - true means NOT allowed    ("Do not email"  = Yes  -> DENY)
# Self-describing values ignore this entirely.
# ---------------------------------------------------------------------------

COLUMN_TABLE: dict[str, tuple[tuple[str, str], ...]] = {
    EMAIL: (
        ("allow emails?", "grant"),
        ("allow emails", "grant"),
        ("allow email", "grant"),
        ("allow e-mail", "grant"),
        ("email permission", "grant"),
        ("do not email", "deny"),
        ("do not allow emails", "deny"),
        ("email opt out", "deny"),
        ("email opt-out", "deny"),
        ("unsubscribed", "deny"),
    ),
    BULK_EMAIL: (
        ("do not allow bulk emails", "deny"),
        ("do not allow bulk email", "deny"),
        ("do not bulk email", "deny"),
        ("bulk email opt out", "deny"),
        ("allow bulk emails", "grant"),
        ("allow bulk email", "grant"),
        ("allow marketing emails", "grant"),
        ("do not allow marketing emails", "deny"),
    ),
    SMS: (
        ("allow text message?", "grant"),
        ("allow text messages", "grant"),
        ("allow text message", "grant"),
        ("allow texts", "grant"),
        ("allow sms", "grant"),
        ("sms permission", "grant"),
        ("do not text", "deny"),
        ("do not allow text messages", "deny"),
        ("sms opt out", "deny"),
        ("text opt out", "deny"),
    ),
    VOICE: (
        ("allow phone calls?", "grant"),
        ("allow phone calls", "grant"),
        ("allow calls", "grant"),
        ("allow phone", "grant"),
        ("do not call", "deny"),
        ("do not phone", "deny"),
        ("do not allow phone calls", "deny"),
        ("dnc", "deny"),
        ("on do not call list", "deny"),
    ),
}

# Every column name this module knows about, in any permission.
ALL_PERMISSION_COLUMNS = frozenset(
    col for cols in COLUMN_TABLE.values() for col, _ in cols
)


def normalize_cell(value) -> str:
    """Lowercase, collapse whitespace, and strip trailing punctuation."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value).strip().lower()
    s = " ".join(s.split())
    while s and s[-1] in ".;,":
        s = s[:-1].strip()
    return s


def interpret_cell(value, polarity: str) -> str:
    """
    One cell -> ALLOW / DENY / UNKNOWN.

    Self-describing values win over the column's polarity. Bare booleans are
    read through it. Everything else - blanks, empties, and anything this module
    has never seen - is UNKNOWN, which is not consent.
    """
    s = normalize_cell(value)
    if s in BLANK_VALUES:
        return UNKNOWN
    if s in SELF_DENY:
        return DENY
    if s in SELF_ALLOW:
        return ALLOW
    if s in BOOL_TRUE:
        return ALLOW if polarity == "grant" else DENY
    if s in BOOL_FALSE:
        return DENY if polarity == "grant" else ALLOW
    return UNKNOWN


def read_permission(row_lowercased: dict, permission: str) -> tuple[str, list[dict]]:
    """
    Resolve ONE permission from every column in a row that speaks to it.

    Returns (state, evidence). Evidence names each column consulted, the raw
    value, and what that cell alone resolved to, so a decision can always be
    explained by pointing at a cell.

    Resolution: any DENY wins. Otherwise any ALLOW. Otherwise UNKNOWN. A cell
    this module cannot read never becomes an ALLOW, and is reported so a human
    can look at it.
    """
    evidence: list[dict] = []
    state = UNKNOWN
    for col, polarity in COLUMN_TABLE[permission]:
        if col not in row_lowercased:
            continue
        raw = row_lowercased[col]
        cell = interpret_cell(raw, polarity)
        norm = normalize_cell(raw)
        if norm == "" or norm in BLANK_VALUES:
            recognised = True          # a blank cell is legitimately silent
        else:
            recognised = cell != UNKNOWN
        evidence.append({
            "column": col,
            "polarity": polarity,
            "raw": None if raw is None else str(raw),
            "resolved": cell,
            "recognised": recognised,
        })
        if cell == DENY:
            state = DENY
        elif cell == ALLOW and state != DENY:
            state = ALLOW
    return state, evidence


def read_all(row_lowercased: dict) -> dict:
    """
    Every permission for one row, plus whether a human needs to look.

    `needs_review` is True when a permission column was PRESENT and held a value
    this module could not interpret. That is the ambiguity case, and it is
    surfaced rather than resolved: an unreadable permission cell is a question,
    and answering it with "allow" is the failure this whole module prevents.
    """
    out: dict = {"permissions": {}, "evidence": {}, "unreadable": [],
                 "needs_review": False}
    for p in PERMISSIONS:
        state, evidence = read_permission(row_lowercased, p)
        out["permissions"][p] = state
        out["evidence"][p] = evidence
        for e in evidence:
            if not e["recognised"]:
                out["unreadable"].append({"permission": p, **e})
                out["needs_review"] = True
    return out


# ---------------------------------------------------------------------------
# Merging - the one-way valve
# ---------------------------------------------------------------------------

def more_restrictive(current: str, incoming: str) -> str:
    """
    Combine an existing permission state with one arriving from an import.

    DENY beats everything. ALLOW beats only UNKNOWN. UNKNOWN never overwrites a
    state that is already known. There is no argument ordering, no flag, and no
    caller that can make this function return ALLOW where either side said DENY.
    """
    if current == DENY or incoming == DENY:
        return DENY
    if current == ALLOW or incoming == ALLOW:
        return ALLOW
    return UNKNOWN


def merge_permissions(current: dict | None, incoming: dict) -> dict:
    """Apply `more_restrictive` across all four permissions."""
    current = current or {}
    return {
        p: more_restrictive(current.get(p, UNKNOWN), incoming.get(p, UNKNOWN))
        for p in PERMISSIONS
    }


# ---------------------------------------------------------------------------
# Storage bridge - the tri-state travels as a nullable boolean
# ---------------------------------------------------------------------------

def to_bool(state: str) -> bool | None:
    """ALLOW -> True, DENY -> False, UNKNOWN -> None."""
    if state == ALLOW:
        return True
    if state == DENY:
        return False
    return None


def from_bool(value) -> str:
    """True -> ALLOW, False -> DENY, None -> UNKNOWN. Nothing else is a yes."""
    if value is True:
        return ALLOW
    if value is False:
        return DENY
    return UNKNOWN


def vocabulary() -> dict:
    """Published so callers and tests read the same tables the parser reads."""
    return {
        "states": list(STATES),
        "permissions": list(PERMISSIONS),
        "self_allow_values": sorted(SELF_ALLOW),
        "self_deny_values": sorted(SELF_DENY),
        "boolean_true_values": sorted(BOOL_TRUE),
        "boolean_false_values": sorted(BOOL_FALSE),
        "blank_values": sorted(BLANK_VALUES),
        "columns": {p: [{"column": c, "polarity": pol}
                        for c, pol in COLUMN_TABLE[p]] for p in PERMISSIONS},
    }
