"""Corroborated matching of a county clerk's foreclosure notice to a property.

The clerks do not always write a street on a notice or in their index:
Dallas often indexes a notice by city only, Tarrant and Johnson by the legal
description ("LOT 21 BLOCK 24A CRAWFORD FARMS"), and many notices name the
house only by legal description, parcel number and borrower. A street-only
match misses them (28 of the Top 100 on 2026-10-24).

This module adds the other ways a notice can name a house, and decides how
much evidence is enough:

  * parcel  - the county account / parcel number written in the notice;
  * legal   - subdivision + lot + block matching the appraisal district's
              legal description of the property;
  * name    - the owner of record or the list's borrower named in the notice;
  * city    - the clerk index city equals the property's city.

A notice is VERIFIED for a property only with two independent kinds of
evidence, one of them parcel or the full legal description (subdivision, lot
and block). A name, a city, or a subdivision on its own makes a CANDIDATE for a
person to review - never a verified sale. The same subdivision with a
different lot or block REJECTS the notice for this property (a neighbour's
house), unless the exact parcel number is also in the notice.

Nothing here reads the network; the clerk reader supplies the notice text.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set

VERIFIED = "verified"
CANDIDATE = "candidate"
REJECTED = "rejected"
NONE = "none"

# ── text helpers ─────────────────────────────────────────────────────────────

_NUM_WORDS = {"ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5", "SIX": "6", "SEVEN": "7",
              "EIGHT": "8", "NINE": "9", "TEN": "10", "ELEVEN": "11", "TWELVE": "12", "FIRST": "1", "SECOND": "2",
              "THIRD": "3", "FOURTH": "4", "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7", "EIGHTH": "8", "NINTH": "9",
              "TENTH": "10", "1ST": "1", "2ND": "2", "3RD": "3", "4TH": "4", "5TH": "5", "6TH": "6", "7TH": "7",
              "8TH": "8", "9TH": "9", "10TH": "10"}
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9",
          "X": "10"}
# words that describe a plat, not which plat it is
_GENERIC = {"ADDITION", "ADDN", "ADD", "ADDIT", "PHASE", "PH", "PHS", "SECTION", "SEC", "SECT", "UNIT", "NO", "NUMBER",
            "THE", "OF", "AN", "AT", "TO", "IN", "REV", "REVISED", "REVISION", "REPLAT", "REP", "AMENDED", "AMD",
            "AMENDMENT", "CITY", "INSTALLMENT", "INST", "FILING", "BLOCKS", "BLOCK", "BLK", "LOT", "LOTS", "LT",
            "SUBDIVISION", "SUBD", "SUB", "PART", "PT", "TRACT", "TR", "ACRES", "AC", "ACRE", "AND", "THRU",
            "COUNTY", "TEXAS", "TX", "ABST", "ABSTRACT", "SURVEY", "HOUSE", "CORRECTED", "FINAL", "PLAT"}
_NAME_STOP = {"AND", "ET", "AL", "ETAL", "UX", "VIR", "ETUX", "ETVIR", "TRUST", "TRUSTEE", "TRUSTEES", "REVOCABLE",
              "LIVING", "FAMILY", "ESTATE", "ESTATE OF", "OF", "THE", "LLC", "INC", "LP", "LTD", "CO", "CORP",
              "JR", "SR", "II", "III", "IV", "MR", "MRS", "MS", "DBA", "LIFE", "LE", "HUSBAND", "WIFE", "SINGLE",
              "MARRIED", "PERSON", "MAN", "WOMAN", "SPOUSE", "UNMARRIED", "AKA", "FKA", "NULL", "NONE", "TR",
              # business words: an LLC's name is not a person, and 'DEVELOPMENT'
              # or 'HOMES' appears in half the notices (read live 2026-10-24)
              "DEVELOPMENT", "DEVELOPMENTS", "HOMES", "HOME", "PROPERTIES", "PROPERTY", "INVESTMENTS", "INVESTMENT",
              "HOLDINGS", "HOLDING", "GROUP", "CAPITAL", "REALTY", "REAL", "ENTERPRISES", "VENTURES", "PARTNERS",
              "MANAGEMENT", "SERVICES", "SOLUTIONS", "INTERESTS", "ASSETS", "FUND", "COMPANY", "LIMITED",
              "PARTNERSHIP", "ASSOCIATION", "BANK", "MORTGAGE", "FINANCIAL", "TEXAS", "DALLAS", "NATIONAL"}
# a surname this common is never evidence on its own
_COMMON = {"SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA", "MILLER", "DAVIS", "RODRIGUEZ", "MARTINEZ",
           "HERNANDEZ", "LOPEZ", "GONZALEZ", "WILSON", "ANDERSON", "THOMAS", "TAYLOR", "MOORE", "JACKSON", "MARTIN",
           "LEE", "PEREZ", "THOMPSON", "WHITE", "HARRIS", "SANCHEZ", "CLARK", "RAMIREZ", "LEWIS", "ROBINSON",
           "WALKER", "YOUNG", "ALLEN", "KING", "WRIGHT", "SCOTT", "TORRES", "NGUYEN", "HILL", "FLORES", "GREEN",
           "ADAMS", "NELSON", "BAKER", "HALL", "RIVERA", "CAMPBELL", "MITCHELL", "CARTER", "ROBERTS", "GOMEZ",
           "JOSE", "MARIA", "JUAN", "JAMES", "JOHN", "ROBERT", "MICHAEL", "DAVID", "MARY", "LINDA"}


def norm(text: Optional[str]) -> str:
    """Upper case, possessives dropped, punctuation to spaces (keeping '/' in
    a block like 'D/6985'), single spaces."""
    t = (text or "").upper().replace("<EM>", "").replace("</EM>", "")
    t = t.replace("’", "'").replace("“", " ").replace("”", " ")
    t = re.sub(r"'S\b", "S", t)
    t = re.sub(r"[^A-Z0-9/ ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _num(tok: str) -> str:
    tok = tok.upper()
    return _NUM_WORDS.get(tok) or _ROMAN.get(tok) or tok


def _close(a: str, b: str) -> bool:
    """Same word allowing one OCR / spelling slip: one edit, or two letters
    swapped (KEIST / KIEST, CAYMAN / CAYMEN). Short words must be equal."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b) and sorted(a) == sorted(b) and sum(x != y for x, y in zip(a, b)) == 2:
        return True
    # one edit
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


# ── legal descriptions ───────────────────────────────────────────────────────

def subdivision_core(name: Optional[str]) -> List[str]:
    """'RESERVE AT HIGH POINTE PH 3' -> ['RESERVE', 'HIGH', 'POINTE']: the words
    that say which plat, without phase / section / numbers / 'ADDITION'."""
    out = []
    for w in norm(name).replace("/", " ").split():
        if w in _GENERIC or w.isdigit() or w in _ROMAN or w in _NUM_WORDS or len(w) < 3:
            continue
        if re.fullmatch(r"\d+[A-Z]{0,2}", w):
            continue
        out.append(w)
    return out


def phase_of(name: Optional[str], trailing: bool = True) -> Optional[str]:
    """The phase / section / number of a plat when one is written: 'PH 1B',
    'PHASE ONE', 'SECTION TWO', 'NO. 2', '4TH SEC', or a bare trailing number
    ('FOREST RIDGE 2'). None when none is written."""
    t = norm(name).replace("/", " ")
    for m in re.finditer(r"\b(?:PHASE|PH|PHS|SECTION|SEC|SECT|UNIT|NO|NUMBER|FILING|INSTALLMENT)\s+([A-Z0-9]{1,6})\b", t):
        v = _num(m.group(1))
        if v in ("A", "AN", "THE", "OF"):
            continue
        if v.isdigit() and int(v) > 30:          # 'SECTION 51.002 OF THE TEXAS PROPERTY CODE' is not a phase
            continue
        return v
    m = re.search(r"\b(1ST|2ND|3RD|[4-9]TH|10TH|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH)\s+(?:SECTION|SEC|PHASE|INSTALLMENT|FILING)\b", t)
    if m:
        return _num(m.group(1))
    m = re.search(r"\b(\d{1,2}[A-Z]?)\s*$", t) if trailing else None
    if m:
        return m.group(1)
    return None


def _lots(fragment: str) -> Set[str]:
    out = {_num(x) for x in re.findall(r"\b(?:LOTS?|LT)\s+(?:NO\s+)?(\d{1,4}[A-Z]?|[A-Z]{3,9})\b", fragment)
           if _num(x).isalnum() and (_num(x)[0].isdigit())}
    # 'LOTS 16 AND 17, BLOCK B' / 'LOTS 4, 5 & 6' / 'LOTS 19-20': every lot named
    for m in re.finditer(r"\bLOTS\s+((?:\d{1,4}[A-Z]?\s*(?:,|&|AND|-|THROUGH|THRU)\s*)+\d{1,4}[A-Z]?)\b", fragment):
        out |= set(re.findall(r"\d{1,4}[A-Z]?", m.group(1)))
    return out


def _block(b: str) -> str:
    b = _num(b.strip().upper())
    return b.split("/")[0] if "/" in b and b.split("/")[0] else b


def _same_block(a: Optional[str], b: Optional[str]) -> bool:
    """Blocks agree; a scanned 'O' and '0', 'I' and '1' are the same mark."""
    if a is None or b is None:
        return True
    t = str.maketrans("OI", "01")
    return a.translate(t) == b.translate(t)


def _lot_hit(want: Set[str], found: Set[str]) -> str:
    """'exact' - a lot agrees; 'base' - only as a replat ('20A' / '20');
    '' - no lot agrees."""
    if want & found:
        return "exact"
    base = lambda v: re.sub(r"[A-Z]+$", "", v)
    if {base(x) for x in want} & {base(x) for x in found}:
        return "base"
    return ""


def cad_legal(subdivision: Optional[str], block: Optional[str], lots: Iterable[str], raw: str = "") -> Optional[Dict[str, Any]]:
    core = subdivision_core(subdivision)
    lots = {_num(str(x).strip().upper()) for x in lots if str(x or "").strip()}
    if not core or not lots:
        return None
    return {"subdivision": (subdivision or "").strip(), "core": core, "phase": phase_of(subdivision),
            "block": _block(block) if block else None, "lots": sorted(lots), "raw": raw or None}


def parse_cad_legal_text(text: str) -> Optional[Dict[str, Any]]:
    """A legal description as an appraisal roll writes it, in any of the
    forms seen: 'CANDLERIDGE ADDITION Block 603 Lot 23', 'LOT 1 BLK 17 INDIAN
    HILLS #1', 'COUNTRY OAKS, LOT 16; & HOUSE', '10 1 LEWIS ADD # 1'."""
    t = norm(text)
    m = re.search(r"^(.*?)\s+BLOCK\s+(\S+)\s+LOTS?\s+(\d{1,4}[A-Z]?)", t)
    if m and subdivision_core(m.group(1)):
        return cad_legal(m.group(1), m.group(2), _lots(t[m.start(3) - 4:]) or {m.group(3)}, text)
    m = re.search(r"\bLOTS?\s+(\d{1,4}[A-Z]?)\s+(?:BLK|BLOCK)\s+(\S+)\s+(.+)$", t)
    if m:
        return cad_legal(m.group(3), m.group(2), {m.group(1)}, text)
    m = re.search(r"^(.*?)\s+LOTS?\s+(\d{1,4}[A-Z]?)\b", t)
    if m and subdivision_core(m.group(1)):
        return cad_legal(m.group(1), None, {m.group(2)}, text)
    m = re.match(r"^(\d{1,4}[A-Z]?)\s+(\d{1,4}[A-Z]?)\s+(.+)$", t)
    if m and subdivision_core(m.group(3)):
        return cad_legal(m.group(3), m.group(2), {m.group(1)}, text)
    return None


def notice_legals(text: str) -> List[Dict[str, Any]]:
    """Every 'lot / block' a notice text states, each with the words that
    follow it (where the subdivision is named) and any phase written there."""
    t = norm(text)
    t = re.sub(r"\b[A-Z][A-Z\- ]{2,20}?\s*\(\s*(\d{1,4}[A-Z]?)\s*\)", r"\1", t)   # 'THIRTY SEVEN (37)' -> '37'
    out: List[Dict[str, Any]] = []
    for m in re.finditer(r"\b(?:BLOCK|BLK)\s+(?:NO\s+)?([A-Z0-9]{1,4}(?:/\d{2,5})?)\b", t):
        before, after = t[max(0, m.start() - 90):m.start()], t[m.end():m.end() + 140]
        lots = _lots(before[-60:]) | _lots(after[:40])
        if not lots:
            lead = re.search(r"(?:^|\s)(\d{1,4}[A-Z]?)\s+(?:IN\s+)?$", before)
            if lead:
                lots = {lead.group(1)}
        if not lots:
            continue
        out.append({"block": _block(m.group(1)), "lots": lots, "context": before[-70:] + " " + after})
    return out


def subdivision_in(core: List[str], text: str) -> bool:
    """All the plat's distinctive words are in the text (one slip allowed per
    word), or written run together ('GLEN CREST' / 'GLENCREST')."""
    long_words = [w for w in core if len(w) >= 4] or core
    words = norm(text).replace("/", " ").split()
    if long_words and all(any(_close(w, x) for x in words) for w in long_words):
        return True
    joined = "".join(core)
    return len(joined) >= 6 and joined in "".join(words)


def legal_match(legal: Optional[Dict[str, Any]], text: str) -> Dict[str, Any]:
    """'full' - subdivision, lot and block agree; 'partial' - subdivision and
    lot agree but one side states no block; 'conflict' - the subdivision is
    named but the lot/block are another house's; '' - nothing to compare.
    'phase_conflict' is set when both sides state different phases."""
    res = {"result": "", "phase_conflict": False, "detail": None}
    if not legal:
        return res
    # the clerk's search snippet often stops mid-name ('LOT 1, BLOCK D, GARDEN'):
    # a lot/block whose context starts the plat name counts when the same
    # notice names the whole plat elsewhere
    whole = subdivision_in(legal["core"], text)
    words = lambda ctx: set(norm(ctx).replace("/", " ").split())
    lead = [w for w in legal["core"] if len(w) >= 4][:1]
    found = [x for x in notice_legals(text)
             if subdivision_in(legal["core"], x["context"])
             or (whole and lead and lead[0] in words(x["context"]))]
    if not found:
        return res
    want_lots, want_block = set(legal["lots"]), legal.get("block")
    for x in found:
        lot_ok = _lot_hit(want_lots, x["lots"])
        block_ok = _same_block(want_block, x["block"])
        if lot_ok and block_ok:
            res["result"] = "full" if (want_block and x["block"] and lot_ok == "exact") else "partial"
            ph_n, ph_c = phase_of(x["context"], trailing=False), legal.get("phase")
            readable = lambda v: bool(v and re.fullmatch(r"\d{1,2}[A-Z]?", v))   # 'HI' is an OCR slip, not a phase
            res["phase_conflict"] = readable(ph_n) and readable(ph_c) and ph_n != ph_c
            res["detail"] = "lot %s block %s %s" % ("/".join(sorted(x["lots"])), x["block"], " ".join(legal["core"]))
            return res
    x = found[0]
    res["result"] = "conflict"
    res["detail"] = "notice: lot %s block %s; property: lot %s block %s" % (
        "/".join(sorted(x["lots"])), x["block"], "/".join(legal["lots"]), want_block)
    return res


# ── parcel numbers and names ────────────────────────────────────────────────

def _pid(value: Optional[str]) -> Optional[str]:
    v = re.sub(r"[^A-Z0-9]", "", str(value or "").upper()).lstrip("0")
    return v if len(v) >= 7 else None          # short ids (5-6 digits) collide too easily


def parcel_in(parcel_ids: Iterable[str], text: str) -> Optional[str]:
    ids = {p for p in (_pid(x) for x in parcel_ids) if p}
    if not ids:
        return None
    for tok in re.findall(r"[A-Z0-9][A-Z0-9.\-]{5,}", (text or "").upper()):
        if _pid(tok) in ids:
            return tok
    return None


def name_groups(owner: Optional[str]) -> List[List[str]]:
    """'BERNAL JOSE T & WILIANA PORTILLO' -> [['BERNAL', 'JOSE'], ['WILIANA',
    'PORTILLO']]: each person's distinctive words. Initials, 'ET AL',
    'TRUST', 'LLC', 'JR' and the like are dropped."""
    groups = []
    for seg in re.split(r"\s*(?:&|/|\bAND\b|,|;|\bET\s?AL\b|\bETUX\b|\bETVIR\b)\s*", (owner or "").upper()):
        words = [w for w in norm(seg).replace("/", " ").split() if len(w) >= 3 and w not in _NAME_STOP]
        if words:
            groups.append(words)
    # 'JOSE A/MARIA SANCHEZ': the shared surname belongs to the first person too
    if len(groups) >= 2 and len(groups[0]) == 1 and len(groups[-1]) >= 2:
        groups[0] = groups[0] + [groups[-1][-1]]
    return groups


def name_match(names: Iterable[str], text: str) -> str:
    """'strong' - two of one person's words are in the notice; 'weak' - one
    uncommon surname is; '' - neither."""
    words = set(norm(text).replace("/", " ").split())
    best = ""
    for owner in names:
        for g in name_groups(owner):
            hit = [w for w in dict.fromkeys(g) if w in words]
            if len(hit) >= 2:
                return "strong"
            if hit and len(hit[0]) >= 4 and hit[0] not in _COMMON and len(g) >= 1:
                best = "weak"
    return best


# ── decision ────────────────────────────────────────────────────────────────

def decide(ident: Dict[str, Any], text: str, index_city: Optional[str] = None) -> Dict[str, Any]:
    """How strongly THIS notice text names THIS property (see the module
    doc). Returns {"decision", "signals", "matched_by", "reason"}."""
    sig: Dict[str, Any] = {}
    p = parcel_in(ident.get("parcel_ids") or [], text)
    if p:
        sig["parcel"] = p
    lg = legal_match(ident.get("legal"), text)
    if lg["result"]:
        sig["legal"] = lg["result"]
        if lg["phase_conflict"]:
            sig["phase_conflict"] = True
    if not lg["result"] and ident.get("legal") and subdivision_in(ident["legal"]["core"], text):
        # the plat is named but no lot/block can be read (a poor scan): never
        # proof, but worth a person's look when the city or the name agrees
        sig["subdivision"] = True
    nm = name_match(ident.get("names") or [], text)
    if nm:
        sig["name"] = nm
    city = (ident.get("city") or "").strip().upper()
    ic = norm(index_city or "")
    if city and ic and (ic == city or ic.startswith(city + " ") or ic.replace(" ", "") == city.replace(" ", "")):
        sig["city"] = True
    elif city and not ic and len(city) >= 4 and re.search(r"\b%s\b" % re.escape(norm(city)), norm(text)):
        # no city on the clerk's index: the notice's own 'addition to the City
        # of Cedar Hill' says the same thing
        sig["city"] = True
    has_parcel, legal = "parcel" in sig, sig.get("legal")
    kinds = []
    if has_parcel:
        kinds.append("parcel number")
    if legal in ("full", "partial"):
        kinds.append("legal description (%s)" % lg["detail"])
    if nm:
        kinds.append("%s name" % ("owner/borrower" if nm == "strong" else "surname"))
    if sig.get("subdivision"):
        kinds.append("subdivision named (lot/block unreadable)")
    if sig.get("city"):
        kinds.append("index city")
    out = {"signals": sig, "matched_by": " + ".join(kinds) + " (notice text)" if kinds else ""}
    if legal == "conflict" and not has_parcel:
        if nm == "strong":
            # the borrower's full name with a lot/block that reads differently
            # is far more often a scan slip ('LOT 1]', 'BLOCK 43' for 13) than a
            # neighbour with the same name: a person checks it, it is never
            # dropped and never auto-verified
            return dict(out, decision=CANDIDATE, reason="borrower/owner name matches but the notice's lot/block "
                                                        "reads differently (%s) - confirm on the notice" % lg["detail"])
        return dict(out, decision=REJECTED, reason="same subdivision, different lot/block: %s" % lg["detail"])
    verified = ((has_parcel and (nm or sig.get("city") or legal in ("full", "partial")))
                or (legal == "full" and (nm or sig.get("city")))
                or (legal == "partial" and nm == "strong"))
    if verified and sig.get("phase_conflict") and not has_parcel:
        return dict(out, decision=CANDIDATE, reason="lot/block match but the notice states a different phase "
                                                    "(often an OCR slip) - confirm on the notice")
    if verified:
        return dict(out, decision=VERIFIED, reason="")
    if sig.get("subdivision") and not (has_parcel or legal):
        if nm or sig.get("city"):
            return dict(out, decision=CANDIDATE,
                        reason="the notice names the subdivision%s but its lot/block cannot be read - confirm on "
                               "the notice" % (" and the owner" if nm else " in the same city"))
        return dict(out, decision=NONE, reason="")
    if not (has_parcel or legal) and nm != "strong":
        # one surname (or a city) alone names nobody: not even a candidate
        return dict(out, decision=NONE, reason="")
    if sig:
        missing = "a parcel number or the lot/block/subdivision" if not (has_parcel or legal) else "a second, independent match"
        return dict(out, decision=CANDIDATE, reason="only %s - needs %s" % (", ".join(kinds) or "a partial match", missing))
    return dict(out, decision=NONE, reason="")


# ── a property's identity from the records already on file ───────────────────

LEGAL_SOURCES = ("collin_cad", "dcad", "tad", "tarrant_tax_roll", "txgio_parcels", "denton_gis", "johnson_cad")


def _js(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw) if raw else None
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


def facts_from_record(provider: str, raw: Any) -> Dict[str, Any]:
    """Parcel ids, legal description and owner name from one stored county
    record (the raw row as the source returned it)."""
    out: Dict[str, Any] = {"parcel_ids": [], "legal": None, "names": []}
    if provider == "dcad":
        d = _js(raw) or {}
        ai = d.get("ACCOUNT_INFO") or {}
        out["parcel_ids"] = [ai.get("ACCOUNT_NUM"), ai.get("GIS_PARCEL_ID")]
        l2 = norm(ai.get("LEGAL2"))
        blk = re.search(r"\bBLK\s+(\S+)", l2)
        out["legal"] = cad_legal(ai.get("LEGAL1"), blk.group(1) if blk else None, _lots(l2),
                                 "%s %s" % (ai.get("LEGAL1") or "", ai.get("LEGAL2") or ""))
        out["names"] = [x for x in (ai.get("OWNER_NAME1"), ai.get("OWNER_NAME2")) if x]
    elif provider == "collin_cad":
        d = _js(raw) or {}
        out["parcel_ids"] = [d.get("geoid"), d.get("gispropid")]
        out["legal"] = cad_legal(d.get("legalabssubname"), d.get("legalabssubblock"), [d.get("legalabssublot")],
                                 d.get("legaldescription") or "")
        out["names"] = [x for x in (d.get("ownername"), d.get("ownernameaddtl")) if x]
    elif provider == "tad":
        parts = str(raw or "").split("|")
        if len(parts) > 6:
            out["parcel_ids"] = [parts[2], parts[5].replace("-", " ").strip()]
            out["names"] = [parts[6]]
        for f in parts:
            if re.search(r"\bBlock\s+\S+\s+Lot\b", f, re.I):
                out["legal"] = parse_cad_legal_text(f)
                break
    elif provider == "tarrant_tax_roll":
        m = re.search(r"([A-Z0-9 '&\-.#]+?\s+BLOCK\s+\S+\s+LOTS?\s+\d+[A-Z]?)", str(raw or "").upper())
        if m:
            out["legal"] = parse_cad_legal_text(re.sub(r"^\d+\s*", "", m.group(1).strip()))
    elif provider == "txgio_parcels":
        d = _js(raw) or {}
        out["parcel_ids"] = [d.get("GEO_ID"), d.get("PROP_ID")]
        if d.get("LEGAL_DESC") and str(d.get("LEGAL_DESC")).upper() != "NULL":
            out["legal"] = parse_cad_legal_text(d["LEGAL_DESC"])
        out["names"] = [d.get("OWNER_NAME")] if d.get("OWNER_NAME") and d.get("OWNER_NAME") != "Null" else []
    elif provider == "johnson_cad":
        d = _js(raw) or {}
        out["parcel_ids"] = [d.get("GEO ACCOUNT NUM"), d.get("ACCOUNT")]
        if d.get("LEGAL"):
            out["legal"] = parse_cad_legal_text(d["LEGAL"])
        if not out["legal"] and d.get("SUBDIVISION") and (d.get("BLOCK") or d.get("LOT")):
            out["legal"] = cad_legal(d.get("SUBDIVISION"), d.get("BLOCK"), [d.get("LOT")],
                                     " ".join(str(x) for x in (d.get("SUBDIVISION"), d.get("BLOCK"), d.get("LOT"))
                                              if x))
        out["names"] = [x for x in (d.get("NAME"), d.get("NAME2")) if x]
    elif provider == "denton_gis":
        d = _js(raw) or {}
        out["parcel_ids"] = [d.get("prop_id"), d.get("PID")]
        out["names"] = [d.get("owner_name")] if d.get("owner_name") else []
    out["parcel_ids"] = [str(x) for x in out["parcel_ids"] if x]
    return out


def identity(db, prop) -> Dict[str, Any]:
    """Everything already on file that can name this house in a notice: its
    parcel ids, the appraisal district's legal description, the current
    owner and any borrower name an imported list gave. Nothing is fetched."""
    from app.models.evosense_models import EvoSenseObservation
    rows = (db.query(EvoSenseObservation.provider_key, EvoSenseObservation.raw_payload, EvoSenseObservation.payload,
                     EvoSenseObservation.observed_at)
            .filter(EvoSenseObservation.organization_id == prop.organization_id,
                    EvoSenseObservation.property_id == prop.id,
                    EvoSenseObservation.provider_key.in_(LEGAL_SOURCES + ("csv_import",)))
            .order_by(EvoSenseObservation.observed_at.desc()).all())
    return identity_from(prop, [(r[0], r[1] if r[1] not in (None, "") else r[2]) for r in rows])


def identity_from(prop, records: List[Any]) -> Dict[str, Any]:
    ids: List[str] = [prop.parcel_apn] if getattr(prop, "parcel_apn", None) else []
    names: List[str] = []
    list_names: List[str] = []
    legal, seen = None, set()
    for provider, raw in records:                   # newest first
        if provider == "csv_import":
            d = _js(raw) or {}
            n = ((d.get("evidence") or {}).get("list_claims") or {}).get("list_owner_name")
            if n and n not in list_names:
                list_names.append(n)
            continue
        if provider in seen:
            continue
        seen.add(provider)
        f = facts_from_record(provider, raw)
        ids += f["parcel_ids"]
        names += [n for n in f["names"] if n not in names]
        legal = legal or f["legal"]
    return {"id": prop.id, "parcel_ids": list(dict.fromkeys(i for i in ids if i)), "legal": legal,
            "names": names + [n for n in list_names if n not in names], "owner_names": names,
            "list_names": list_names, "city": prop.city}
