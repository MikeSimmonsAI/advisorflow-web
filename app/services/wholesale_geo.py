"""Geographic normalization for buy-box matching — practical, not a GIS project.

THE PROBLEM THIS SOLVES, AND THE ONE IT REFUSES TO
---------------------------------------------------
Phase 1 compared geography with a lowercased string equality. That is correct
and useless at the same time: a buyer whose buy box says "Dallas County, TX"
never matched a property whose county column says "Dallas", and a ZIP typed as
"76107-1234" never matched "76107". People type the way people type, and a
matching engine that only works when two humans chose identical spellings is a
matching engine nobody trusts twice.

So this module normalizes SPELLING. It does not invent GEOGRAPHY.

    Dallas / dallas / DALLAS / " Dallas "        same city
    Dallas, TX / Dallas TX                       same city, with a state stated
    Dallas County / Dallas Co. / Dallas Parish   same county
    76107 / 76107-1234                           same ZIP
    Texas / TX / tx                              same state
    Ft. Worth / Fort Worth                       same city

    Dallas (city) vs Dallas County (county)      NOT asserted to be the same

That last line is the whole discipline. Dallas the city sits inside Dallas
County, and Kansas City sits in two counties and two states — but this module
holds no table that knows either fact, so it claims neither. A city criterion is
answered by the property's CITY, a county criterion by its COUNTY. If a richer
geographic dataset is ever added, `resolve_containment` below is where it plugs
in; until then the honest answer to "is this city in that county" is that we do
not know, and the engine says so rather than guessing.

STATE IS A REAL SIGNAL, NOT NOISE. "Dallas, TX" and "Dallas, GA" are different
places, and both exist. A criterion that names a state only matches a property
in that state; a criterion that names none is answered on the name alone.

EVERY MATCH EXPLAINS ITSELF. `match_place` returns the sentence the UI shows —
"Property county Dallas matches the buy box entry 'Dallas County, TX'" — because
a score somebody cannot check is a score somebody eventually stops believing.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# ── US states. Factual reference data, not an inference. ────────────────────
STATE_ABBR: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN",
    "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR", "guam": "GU", "virgin islands": "VI",
}
VALID_ABBR = frozenset(STATE_ABBR.values())

# Suffixes that mean "this is a county", stripped before comparing county names.
# Louisiana has parishes and Alaska has boroughs; both are the same field.
_COUNTY_SUFFIX = re.compile(
    r"\s+(county|co|cty|parish|borough|census\s+area|municipality)\.?$", re.I)

# The same vocabulary as a set, for the one place that needs to ask "is this
# trailing token county-speak?" before asking "is it a state?". See the note in
# `normalize_place`: `co` means County far more often than it means Colorado at
# the end of a place somebody typed.
_COUNTY_WORDS = frozenset(
    ("county", "co", "cty", "parish", "borough", "municipality"))

# Abbreviations that are unambiguous in a US place name. Deliberately short:
# every entry is one somebody would be annoyed to have to spell out, and none of
# them changes which place is meant.
_PLACE_ABBREV = (
    (re.compile(r"^ft\.?\s+", re.I), "fort "),
    (re.compile(r"^st\.?\s+", re.I), "saint "),
    (re.compile(r"^mt\.?\s+", re.I), "mount "),
    (re.compile(r"^n\.?\s+", re.I), "north "),
    (re.compile(r"^s\.?\s+", re.I), "south "),
    (re.compile(r"^e\.?\s+", re.I), "east "),
    (re.compile(r"^w\.?\s+", re.I), "west "),
)

_PUNCT = re.compile(r"[.’']")
_WS = re.compile(r"\s+")
_ZIP = re.compile(r"^(\d{5})(?:-?\d{4})?$")


def normalize_state(value: Any) -> Optional[str]:
    """'Texas', 'texas', 'TX', ' tx ' -> 'TX'. Anything unrecognised -> None.

    None rather than the raw string: a two-letter code this module does not
    recognise is not a state, and passing it through would let a typo match
    another typo.
    """
    if value is None:
        return None
    text = _WS.sub(" ", _PUNCT.sub("", str(value))).strip().lower()
    if not text:
        return None
    if len(text) == 2 and text.upper() in VALID_ABBR:
        return text.upper()
    return STATE_ABBR.get(text)


def normalize_zip(value: Any) -> Optional[str]:
    """'76107', '76107-1234', '761071234' -> '76107'.

    ZIP+4 is the same postal area as its five-digit prefix, so comparing on the
    prefix is a fact about the postal system rather than a guess. A value that
    is not a US ZIP at all returns None.
    """
    if value is None:
        return None
    text = _WS.sub("", str(value)).strip()
    m = _ZIP.match(text)
    return m.group(1) if m else None


def _base_clean(value: Any) -> str:
    text = _PUNCT.sub("", str(value or ""))
    text = text.replace("-", " ")
    return _WS.sub(" ", text).strip().lower()


def normalize_place(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Split a typed place into (name, state) and normalize both.

        "Dallas"              -> ("dallas", None)
        "Dallas, TX"          -> ("dallas", "TX")
        "Dallas County, TX"   -> ("dallas", "TX")     county suffix stripped
        "Ft. Worth, Texas"    -> ("fort worth", "TX")
        "  DALLAS  CO. "      -> ("dallas", None)

    The county suffix is stripped from BOTH sides of a comparison, so a buy box
    that says "Dallas County" and a property whose county column says "Dallas"
    are the same county — which they are — while a city criterion is still only
    ever compared against a city.
    """
    text = _base_clean(value)
    if not text:
        return None, None

    state = None
    # A trailing state, with or without the comma people forget.
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        candidate = normalize_state(parts[-1])
        if candidate:
            state = candidate
            text = " ".join(parts[:-1]).strip()
        else:
            text = " ".join(parts).strip()
    else:
        tokens = text.split()
        if len(tokens) >= 2:
            last = tokens[-1]
            # "Dallas Co." IS NOT DALLAS, COLORADO. `co` is both Colorado's
            # abbreviation and the commonest way people shorten "County", and a
            # test caught this reading it the wrong way round. A trailing token
            # that is county vocabulary is county vocabulary; the state peel
            # only considers what is left after that reading is ruled out.
            if last in _COUNTY_WORDS:
                candidate = None
            else:
                candidate = normalize_state(last)
            # Only peel a trailing state off a name with something left over,
            # so "Washington" stays the city/county Washington rather than
            # becoming an empty name with a state.
            if candidate:
                remainder = " ".join(tokens[:-1]).strip()
                if remainder:
                    state = candidate
                    text = remainder

    text = _COUNTY_SUFFIX.sub("", text).strip()
    for pattern, replacement in _PLACE_ABBREV:
        if pattern.match(text):
            text = pattern.sub(replacement, text, count=1)
            break
    text = _WS.sub(" ", text).strip()
    return (text or None), state


def resolve_containment(city: Optional[str], county: Optional[str],
                        state: Optional[str]) -> None:
    """The seam for a real geographic dataset, deliberately empty.

    "Is Fort Worth in Tarrant County" is a question with a correct answer that
    this platform does not currently hold the data to answer. A lookup table
    typed from memory would be wrong somewhere and wrong invisibly, so there
    isn't one: a city criterion is answered by the city column and a county
    criterion by the county column, and nothing here pretends otherwise.

    When a county/place dataset is added — the Census place-to-county file is
    the obvious one — this is the single function the matcher calls, and
    `match_place` grows one more explained reason. Nothing above it changes.
    """
    return None


# ── Matching ────────────────────────────────────────────────────────────────

FIELD_LABELS = {
    "zips": ("ZIP", "zip_code"),
    "cities": ("city", "city"),
    "counties": ("county", "county"),
    "markets": ("market", "market"),
    "states": ("state", "state"),
}


def match_place(field: str, criteria: List[str], prop_value: Any,
                prop_state: Any = None) -> Dict[str, Any]:
    """Does the property satisfy one geography criterion list, and why?

    Returns {"verdict", "detail", "matched_on"} where verdict is one of:

        "match"    the property is inside the stated geography
        "outside"  the property is stated to be somewhere else — a real
                   disqualifier, because the buyer told us and the property
                   told us and they disagree
        "unknown"  the property has nothing on file for this field. NOT a
                   disqualifier: missing is missing, and refusing a buyer
                   because a county column is blank would be a wrong answer
                   dressed as a strict one
        "unconstrained"  the buy box says nothing about this field

    `prop_state` narrows a city or county criterion that names a state. A
    property in Dallas, GA does not satisfy a buy box that says "Dallas, TX",
    and the detail says which state disagreed.
    """
    label, _column = FIELD_LABELS.get(field, (field, field))
    entries = [c for c in (criteria or []) if str(c).strip()]
    if not entries:
        return {"verdict": "unconstrained", "matched_on": None,
                "detail": "This buy box names no %s." % label}

    if field == "zips":
        want = {}
        for raw in entries:
            z = normalize_zip(raw)
            if z:
                want[z] = str(raw).strip()
        have = normalize_zip(prop_value)
        if not want:
            return {"verdict": "unconstrained", "matched_on": None,
                    "detail": "The buy box's ZIP list holds no readable ZIP codes."}
        if not have:
            return {"verdict": "unknown", "matched_on": None,
                    "detail": ("The buy box is limited to %d ZIP code(s) and this "
                               "property has no usable ZIP on file."
                               % len(want))}
        if have in want:
            shown = want[have]
            extra = "" if shown == have else " (written as %s)" % shown
            return {"verdict": "match", "matched_on": have,
                    "detail": "ZIP %s matches the buy box's ZIP list%s."
                              % (have, extra)}
        return {"verdict": "outside", "matched_on": have,
                "detail": "ZIP %s is not in the buy box's list: %s."
                          % (have, ", ".join(sorted(want))[:120])}

    if field == "states":
        want = {}
        for raw in entries:
            s = normalize_state(raw)
            if s:
                want[s] = str(raw).strip()
        have = normalize_state(prop_value)
        if not want:
            return {"verdict": "unconstrained", "matched_on": None,
                    "detail": "The buy box's state list holds no recognisable states."}
        if not have:
            return {"verdict": "unknown", "matched_on": None,
                    "detail": "This property has no usable state on file."}
        if have in want:
            return {"verdict": "match", "matched_on": have,
                    "detail": "The property is in %s, which the buy box includes." % have}
        return {"verdict": "outside", "matched_on": have,
                "detail": "The property is in %s; the buy box covers %s."
                          % (have, ", ".join(sorted(want)))}

    # city / county / market — a name, optionally qualified by a state.
    have_name, have_state_in_value = normalize_place(prop_value)
    have_state = normalize_state(prop_state) or have_state_in_value
    if not have_name:
        return {"verdict": "unknown", "matched_on": None,
                "detail": ("The buy box is limited by %s and this property has "
                           "none on file." % label)}

    mismatched_state = None
    for raw in entries:
        want_name, want_state = normalize_place(raw)
        if not want_name or want_name != have_name:
            continue
        if want_state and have_state and want_state != have_state:
            mismatched_state = (raw, want_state)
            continue
        qualifier = ""
        if want_state:
            qualifier = " in %s" % want_state
        written = str(raw).strip()
        extra = "" if written.lower() == have_name else " (written as '%s')" % written
        return {"verdict": "match", "matched_on": have_name,
                "detail": "Property %s %s matches the buy box's %s criteria%s%s."
                          % (label, _title(have_name), label, qualifier, extra)}

    if mismatched_state:
        raw, want_state = mismatched_state
        return {"verdict": "outside", "matched_on": have_name,
                "detail": ("The %s name matches '%s', but that entry is in %s and "
                           "this property is in %s. Same name, different state."
                           % (label, str(raw).strip(), want_state,
                              have_state or "an unknown state"))}

    return {"verdict": "outside", "matched_on": have_name,
            "detail": "Property %s %s is not in the buy box's list: %s."
                      % (label, _title(have_name),
                         ", ".join(sorted(str(e).strip() for e in entries))[:120])}


def _title(name: Optional[str]) -> str:
    return " ".join(w.capitalize() for w in (name or "").split())


def geography_verdict(box_criteria: Dict[str, List[str]],
                      prop: Any) -> Dict[str, Any]:
    """The one geography answer for a buy box, over every field it constrains.

    MOST SPECIFIC FIRST, AND A MATCH ANYWHERE IS A MATCH. A buy box that names
    both ZIPs and a state is satisfied by either: somebody who lists twelve ZIPs
    *and* "TX" means "these ZIPs, and Texas generally", not "these ZIPs that are
    also in Texas". Phase 1 answered only on the most specific field, which
    quietly discarded every other line the buyer had typed.

    Disqualification is therefore stricter than a single miss: the property has
    to be positively OUTSIDE every field the box constrains, with none unknown.
    One unknown field is enough to make the answer "we cannot say", which scores
    as a miss but never excludes the buyer from the list.
    """
    results = []
    for field in ("zips", "cities", "counties", "markets", "states"):
        _label, column = FIELD_LABELS[field]
        verdict = match_place(field, box_criteria.get(field) or [],
                              getattr(prop, column, None),
                              getattr(prop, "state", None))
        verdict["field"] = field
        results.append(verdict)

    constrained = [r for r in results if r["verdict"] != "unconstrained"]
    if not constrained:
        return {"verdict": "unconstrained", "matched": None,
                "detail": "This buy box names no geography, so it is not scored on one.",
                "fields": results}

    matches = [r for r in constrained if r["verdict"] == "match"]
    if matches:
        best = matches[0]           # already in most-specific-first order
        return {"verdict": "match", "matched": best["field"],
                "detail": best["detail"], "fields": results}

    unknowns = [r for r in constrained if r["verdict"] == "unknown"]
    if unknowns:
        return {"verdict": "unknown", "matched": None,
                "detail": unknowns[0]["detail"], "fields": results}

    return {"verdict": "outside", "matched": None,
            "detail": constrained[0]["detail"], "fields": results}
