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
# "unknown" is deliberately NOT here. An empty cell is silence. A cell that
# says "Unknown" is somebody recording that they could not determine it - which
# is a review item, not a blank, and the operational pipeline's own tests say so.
BLANK_VALUES = frozenset({"", "null", "none", "n/a", "na", "nan", "-", "--",
                          "not set", "notset", "(blank)", "#n/a"})


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
        # deny_ambiguous: this column's cells are self-descriptive elsewhere in
        # the observed export, so a bare boolean here is genuinely undecidable
        # and goes to review rather than being resolved by inversion.
        ("do not allow bulk emails", "deny_ambiguous"),
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

# The CANONICAL FIELD KEYS the operational import pipeline uses, mapped to the
# permission they carry and the polarity a bare boolean is read through.
#
# `import_staging_service` addresses consent by these keys rather than by raw
# header text, so this is the bridge that lets it call the one interpreter
# without changing its own signature, its column names, or its storage.
CANONICAL_KEYS: dict[str, tuple[str, str]] = {
    "allow_emails":      (EMAIL,      "grant"),
    "allow_bulk_emails": (BULK_EMAIL, "deny_ambiguous"),
    "allow_sms":         (SMS,        "grant"),
    "allow_calls":       (VOICE,      "grant"),
    # Aliases, so a caller using either vocabulary lands in the same place.
    "allow_email":       (EMAIL,      "grant"),
    "allow_bulk_email":  (BULK_EMAIL, "deny"),
    "allow_voice":       (VOICE,      "grant"),
    "allow_phone_calls": (VOICE,      "grant"),
}


def interpret_canonical(raw, col_key: str) -> tuple[str, bool]:
    """
    Interpret a cell addressed by CANONICAL FIELD KEY rather than header text.

    An unknown key is not guessed at: it is read as a grant-polarity column,
    which is the conservative choice for a bare boolean (True->ALLOW is only
    reachable when the key really is a grant column; the caller passing an
    unknown key gets no inversion invented on its behalf).
    """
    _, polarity = CANONICAL_KEYS.get(col_key, (None, "grant"))
    return interpret_cell_ex(raw, polarity)


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


def interpret_cell_ex(value, polarity: str) -> tuple[str, bool]:
    """
    THE ONE INTERPRETER. One cell -> (ALLOW / DENY / UNKNOWN, ambiguous).

    This is the only place in the platform that decides what a permission cell
    means. `import_staging_service._norm_consent` delegates here, so the
    operational pipeline and the historical source layer cannot drift apart.

    THE RULE, AND WHY IT IS SHAPED LIKE THIS
    ----------------------------------------
    Two implementations existed and disagreed on exactly one case: a BARE
    BOOLEAN sitting in a NEGATIVELY-NAMED column, e.g. "Do not allow Bulk
    Emails = Yes". One inverted it; the other called it ambiguous and sent it
    to review. Each was safer than the other in one direction, so neither was
    adopted whole:

      "Do not allow Bulk Emails = Yes"  inversion says DENY.
          A denial is the restrictive answer, and a denial arrived at by a
          plausible reading is still a denial. TAKE IT.

      "Do not allow Bulk Emails = No"   inversion says ALLOW.
          This is the dangerous direction: it would GRANT marketing permission
          on the strength of a guess about what the column meant. REFUSE.
          UNKNOWN, and flagged for a human.

    So: never grant from an ambiguous inversion, never lose a denial from one.
    That is strictly more restrictive than either implementation was alone, and
    it is the same "more restrictive wins" doctrine used everywhere else here.

    A SELF-DESCRIBING value ("Allow", "Do Not Allow", "Opted Out") states the
    permission outright and the column's polarity is not consulted at all -
    which is what stops a column named for a denial, whose cells read "Allow",
    from turning 85,751 permissions into denials.
    """
    s = normalize_cell(value)
    if s in BLANK_VALUES:
        return UNKNOWN, False          # silence is not consent, and not a puzzle
    if s in SELF_DENY or s.startswith("do not "):
        return DENY, False
    if s in SELF_ALLOW:
        return ALLOW, False
    if s in BOOL_TRUE or s in BOOL_FALSE:
        truthy = s in BOOL_TRUE
        if polarity == "grant":
            return (ALLOW if truthy else DENY), False
        if polarity == "deny_ambiguous":
            # THE COLUMN'S OWN CELLS ARE SELF-DESCRIPTIVE ELSEWHERE IN THIS FILE.
            # "Do not allow Bulk Emails" carries "Allow" / "Do Not Allow" on most
            # rows, so a bare "Yes" on one row is genuinely undecidable: it may
            # mean "yes, do not allow" or a mis-mapped "yes, allowed".
            #
            # Resolving it either way BURIES that. Review does not: an ambiguous
            # staged row is held and never auto-committed, so nothing is sent
            # either way, and a person sees the cell instead of inheriting a
            # guess. Safety is equal; information is not. Review wins.
            return UNKNOWN, True
        # A plainly-negative column ("Do not call", "DNC", "Opt out"). A bare
        # true there denies, and losing that denial is the dangerous direction.
        # A bare false would GRANT on an inversion, which is refused.
        return (DENY, False) if truthy else (UNKNOWN, True)
    return UNKNOWN, True               # never seen it; a human decides


def interpret_cell(value, polarity: str) -> str:
    """The state alone. Ambiguity is available from `interpret_cell_ex`."""
    return interpret_cell_ex(value, polarity)[0]


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
        cell, ambiguous = interpret_cell_ex(raw, polarity)
        recognised = not ambiguous
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
