"""Johnson County, Texas: the Central Appraisal District of Johnson County (JCAD)
certified appraisal roll, published free on the district's downloads page.

    https://johnsoncad.com/downloads   "2026 Certified Records" (BATCH_896.all_.zip, ~45 MB)

The zip holds tab-separated tables. EvoSense reads ONE of them, externalnal.tab
("name and address"), streamed out of the zip with HTTP byte ranges - never the
whole download. Per account it carries: the GEO account (126.xxxx.xxxxx, the
same id TxGIO calls GEO_ID), owner name(s), mailing address, situs address,
subdivision / block / lot, the last deed's recording date and instrument number,
the use code, heated living area, year built and the district's values.

WHY THIS SOURCE EXISTS. TxGIO's statewide layer has Johnson County's legal
description and mailing address as "Null", so a foreclosure notice the county
clerk indexed by LEGAL DESCRIPTION (lot / block / subdivision) could never be
tied to a house. JCAD's own roll fills that gap.

What it can NOT tell: a sale price. The sales table lists deed dates and
instrument numbers only - Texas does not disclose sale prices.

MATCHING IS STRICT. By the county account number when one is known; otherwise by
the same house number and street (and city when both sides have one). Two
accounts at one address is ambiguous and matches nothing.

The index (one row per account) is built once from the file into a small SQLite
file next to the other source caches, keyed by the file's size and Last-Modified,
so a new certified roll rebuilds it automatically and lookups cost no memory.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.services.evosense.sources import base as B

ROLL_URL = "https://johnsoncad.com/wp-content/uploads/2026/08/BATCH_896.all_.zip"
PAGE = "https://johnsoncad.com/downloads"
MEMBER = "externalnal.tab"
LEGAL_MEMBER = "externallegal.tab"     # parcel_id, strap (= GEO account), ln_num, lgl_dscr - one line per row
ADAPTER_VERSION = "johnson_cad_roll/1"
KEEP = ("ACCOUNT", "GEO ACCOUNT NUM", "NAME", "NAME2", "MAIL ADDRESS LINE 1", "MAIL ADDRESS LINE 2", "MAIL CITY",
        "MAIL STATE", "MAIL ZIP", "SITUS STREET NUM", "SITUS STREET NAME", "SITUS STREET SFX", "SITUS UNIT",
        "SITUS CITY", "SUBDIVISION", "BLOCK", "LOT", "SECTION", "RECD DATE", "INSTRUMENT NUM", "PARCEL USE CD",
        "USE CD DESC", "BLDG HEAT AREA", "YEAR_BUILT", "MKT VAL", "APPRAISED VAL")
REQUIRED = ("GEO ACCOUNT NUM", "NAME", "SITUS STREET NUM", "SITUS STREET NAME", "SUBDIVISION", "BLOCK", "LOT")

_LOCK = threading.Lock()
_MEMO: Dict[str, Any] = {}


def url() -> str:
    return os.environ.get("EVOSENSE_JOHNSON_ROLL_URL") or ROLL_URL


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return None if not v or v.upper() == "NULL" else v


def street_core(num: Optional[str], name: Optional[str]) -> Tuple[Optional[str], str]:
    """('397', 'FLAGSTONE') from '397' + 'FLAGSTONE DR' - the same core TxGIO matching uses."""
    from app.services.evosense.sources.txgio import _core
    return _core("%s %s" % ((num or "").strip(), (name or "").strip()))


def parse_rows(lines: Iterator[str]) -> Iterator[Dict[str, Any]]:
    """Header row first; yields {field: value} for KEEP fields. Refuses a file
    whose header no longer has the columns matching depends on."""
    header = None
    for line in lines:
        cells = line.split("\t")
        if header is None:
            header = [c.strip().rstrip(",") for c in cells]
            missing = [c for c in REQUIRED if c not in header]
            if missing:
                raise B.SourceError(B.SOURCE_FORMAT_CHANGED,
                                    "JCAD %s no longer has column(s): %s" % (MEMBER, ", ".join(missing)))
            pos = {c: header.index(c) for c in KEEP if c in header}
            continue
        if len(cells) < len(header) // 2:
            continue
        row = {c: _clean(cells[i]) if i < len(cells) else None for c, i in pos.items()}
        if row.get("GEO ACCOUNT NUM"):
            yield row


COLS = list(KEEP) + ["LEGAL", "LEGAL_LINES"]
_NOT_SUBDIVISION = re.compile(r"^(ABST|ABSTRACT|TR|TRACT|ACRES?|AC\b|IMP|MH|MOBILE|SEC\b|SECTION|PT|PART|UND|"
                              r"\d+(\.\d+)?\s*AC)")


def canonical_legal(lines: List[str]) -> Optional[str]:
    """['LOT 22', 'BLK 1', 'SIERRA ESTATES'] -> 'SIERRA ESTATES Block 1 Lot 22' (the form the
    foreclosure matcher parses). None when the lines are not a platted lot (acreage, abstracts)."""
    lots, block, subdiv = [], None, []
    for ln in lines:
        t = (ln or "").strip().upper()
        if not t:
            continue
        m = re.match(r"^LOTS?\s+(.+)$", t)
        if m:
            lots.append(m.group(1).strip())
            continue
        m = re.match(r"^(?:BLK|BLOCK)\s+(\S+)", t)
        if m:
            block = m.group(1)
            continue
        if not _NOT_SUBDIVISION.match(t):
            subdiv.append(t)
    if not lots or not subdiv:
        return None
    return "%s%s Lot %s" % (" ".join(subdiv), " Block %s" % block if block else "", " ".join(lots))


def _addr_key(num: Optional[str], name: Optional[str]) -> Optional[str]:
    n, core = street_core(num, name)
    return "%s|%s" % (n, core) if n and core and n != "0" else None


def legal_rows(lines: Iterator[str]) -> Iterator[Tuple[str, int, str]]:
    header = None
    for line in lines:
        cells = line.split("\t")
        if header is None:
            header = [c.strip() for c in cells]
            for c in ("strap", "ln_num", "lgl_dscr"):
                if c not in header:
                    raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "JCAD %s no longer has column %s" % (LEGAL_MEMBER, c))
            i_s, i_n, i_t = header.index("strap"), header.index("ln_num"), header.index("lgl_dscr")
            continue
        if len(cells) > max(i_s, i_n, i_t):
            try:
                n = int(cells[i_n])
            except ValueError:
                n = 0
            yield cells[i_s].strip(), n, cells[i_t].strip()


def add_legal(con, lines: Iterator[Tuple[str, int, str]]) -> None:
    con.execute("CREATE TABLE legal (geo TEXT, ln INTEGER, txt TEXT)")
    batch = []
    for row in lines:
        batch.append(row)
        if len(batch) >= 10000:
            con.executemany("INSERT INTO legal VALUES (?,?,?)", batch)
            batch = []
    if batch:
        con.executemany("INSERT INTO legal VALUES (?,?,?)", batch)
    con.execute("CREATE INDEX ix_legal ON legal(geo, ln)")
    upd, cur, acc = [], None, []
    for geo, txt in con.execute("SELECT geo, txt FROM legal ORDER BY geo, ln"):
        if geo != cur:
            if cur is not None:
                upd.append((json.dumps(acc), cur))
            cur, acc = geo, []
        acc.append(txt)
    if cur is not None:
        upd.append((json.dumps(acc), cur))
    con.executemany("UPDATE acct SET legal = ? WHERE geo = ?", upd)
    con.execute("DROP TABLE legal")


def build_index(rows: Iterator[Dict[str, Any]], path: str,
                legal_lines: Optional[Iterator[Tuple[str, int, str]]] = None) -> int:
    """Write the accounts to a SQLite file (geo account, address key, city, row).
    On disk, so a 100k-account county costs no memory at lookup time."""
    import sqlite3
    tmp = path + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE acct (geo TEXT PRIMARY KEY, addr TEXT, city TEXT, row TEXT, legal TEXT)")
    n, batch = 0, []
    for r in rows:
        batch.append((r["GEO ACCOUNT NUM"], _addr_key(r.get("SITUS STREET NUM"), r.get("SITUS STREET NAME")),
                      (r.get("SITUS CITY") or "").upper(), json.dumps([r.get(c) for c in KEEP]), None))
        n += 1
        if len(batch) >= 5000:
            con.executemany("INSERT OR REPLACE INTO acct VALUES (?,?,?,?,?)", batch)
            batch = []
    if batch:
        con.executemany("INSERT OR REPLACE INTO acct VALUES (?,?,?,?,?)", batch)
    if legal_lines is not None:
        add_legal(con, legal_lines)
    con.execute("CREATE INDEX ix_addr ON acct(addr)")
    con.commit()
    con.close()
    os.replace(tmp, path)
    return n


def _cache_path(meta: Dict[str, Any]) -> str:
    tag = B.sha256("%s|%s|%s|%s" % (url(), meta.get("size"), meta.get("last_modified"), ADAPTER_VERSION))[:16]
    return os.path.join(B.cache_dir(), "johnson_cad-%s.sqlite" % tag)


class Index:
    def __init__(self, path: str, meta: Dict[str, Any]):
        import sqlite3
        self.path, self.meta = path, meta
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.rows = self.con.execute("SELECT COUNT(*) FROM acct").fetchone()[0]

    def _row(self, data: str, legal: Optional[str]) -> Dict[str, Any]:
        r = dict(zip(KEEP, json.loads(data)))
        lines = json.loads(legal) if legal else []
        r["LEGAL_LINES"] = " | ".join(lines) or None
        r["LEGAL"] = canonical_legal(lines)
        return r

    def by_geo(self, geo: str) -> Optional[Dict[str, Any]]:
        hit = self.con.execute("SELECT row, legal FROM acct WHERE geo = ?", (geo,)).fetchone()
        return self._row(*hit) if hit else None

    def same_subdivision(self, subdivision: str, limit: int = 400) -> List[Dict[str, Any]]:
        """Accounts whose legal description names the same platted subdivision (core words)."""
        from app.services.evosense import fc_match as M
        core = M.subdivision_core(subdivision)
        if not core:
            return []
        like = "%" + " ".join(core) + "%"
        out = []
        for data, legal in self.con.execute("SELECT row, legal FROM acct WHERE legal LIKE ? LIMIT ?",
                                            (like, limit * 3)):
            r = self._row(data, legal)
            parsed = M.parse_cad_legal_text(r["LEGAL"]) if r.get("LEGAL") else None
            if parsed and parsed.get("core") == core:
                out.append(r)
                if len(out) >= limit:
                    break
        return out

    def by_addr(self, key: str) -> List[Dict[str, Any]]:
        return [self._row(*x) for x in self.con.execute("SELECT row, legal FROM acct WHERE addr = ?", (key,)).fetchall()]


def load_index() -> Index:
    """The cached index, building it from the published file when needed."""
    with _LOCK:
        if _MEMO.get("index"):
            return _MEMO["index"]
        oz = B.open_zip("johnson_cad", url())
        try:
            meta = {"url": url(), "size": oz.meta.get("size"), "last_modified": oz.meta.get("last_modified")}
            path = _cache_path(oz.meta)
            if not os.path.exists(path):
                n = build_index(parse_rows(B.text_lines(oz.zip, MEMBER)), path,
                                legal_rows(B.text_lines(oz.zip, LEGAL_MEMBER)))
                if n < 1000:
                    os.remove(path)
                    raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "JCAD %s had only %d accounts" % (MEMBER, n))
                meta["bytes_fetched"] = oz.bytes_fetched()
        finally:
            oz.close()
        meta["built_at"] = datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat()
        _MEMO["index"] = Index(path, meta)
        return _MEMO["index"]


def find(idx: "Index", target: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """(row, how) or (None, why-not)."""
    from app.services.evosense.sources.txgio import _core
    for pid in [target.get("parcel_apn")] + list(target.get("parcel_ids") or []):
        pid = (pid or "").strip()
        if pid:
            r = idx.by_geo(pid)
            if r:
                return r, "county account number"
    num, core = _core(target.get("street_address") or "")
    if not (num and core):
        return None, "no house number and street to match"
    rows = idx.by_addr("%s|%s" % (num, core))
    city = (target.get("city") or "").strip().upper()
    if city and len(rows) > 1:
        rows = [r for r in rows if not r.get("SITUS CITY") or r["SITUS CITY"].upper() == city] or rows
    if len(rows) == 1:
        r = rows[0]
        if city and r.get("SITUS CITY") and r["SITUS CITY"].upper() != city:
            # The roll writes the district's city (e.g. JOSHUA) where the postal city
            # is Crowley. Accepted only because this street address exists ONCE in the
            # whole county - and the difference is written into the evidence.
            return r, ("same house number and street - the only one in Johnson County "
                       "(the county lists the city as %s)" % r["SITUS CITY"].title())
        return r, "same house number and street"
    if len(rows) > 1:
        return None, "%d accounts at this address - ambiguous, not guessed" % len(rows)
    return None, "no account at this address"


def _date(v: Optional[str]) -> Optional[str]:
    try:
        return datetime.strptime((v or "").strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _int(v: Optional[str]) -> Optional[int]:
    try:
        n = int(float(v or ""))
    except ValueError:
        return None
    return n if n > 0 else None


def record(target: Dict[str, Any], r: Dict[str, Any], how: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.evosense.sources.tarrant import state_code
    name = (r.get("NAME") or "").strip()
    owner = None
    if name and "CONFIDENTIAL" not in name.upper():
        mstate = (r.get("MAIL STATE") or "").upper()
        owner = {"name": name + (" " + r["NAME2"] if r.get("NAME2") else ""),
                 "mailing_street": B.mailing_line(r.get("MAIL ADDRESS LINE 1"), r.get("MAIL ADDRESS LINE 2")),
                 "mailing_city": (r.get("MAIL CITY") or "").title() or None,
                 "mailing_state": state_code(mstate) if mstate else None,
                 "mailing_zip": re.sub(r"\D", "", r.get("MAIL ZIP") or "")[:5] or None}
    deed = _date(r.get("RECD DATE"))
    return {
        "source_reference": "JCAD:%s:2026" % r["GEO ACCOUNT NUM"],
        "street_address": target.get("street_address"), "unit": target.get("unit"),
        "city": target.get("city"), "state": "TX", "zip_code": target.get("zip_code"), "county": "Johnson",
        "parcel_apn": target.get("parcel_apn"),
        "square_feet": _int(r.get("BLDG HEAT AREA")), "year_built": _int(r.get("YEAR_BUILT")),
        "deed_transfer_date": deed,
        "owner": owner, "signals": [],
        "_raw": r,
        "_source_url": PAGE, "_source_updated_at": meta.get("last_modified"),
        "_adapter_version": ADAPTER_VERSION,
        "_evidence": {"county_account": r["GEO ACCOUNT NUM"], "matched_by": how,
                      "legal": r.get("LEGAL") or r.get("LEGAL_LINES"),
                      "deed_recorded": deed, "instrument": r.get("INSTRUMENT NUM"),
                      "sale_price": "not published (Texas non-disclosure)", "tax_year": "2026 certified"},
    }


class JohnsonReader:
    adapter_version = ADAPTER_VERSION

    def lookup(self, target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        idx = load_index()
        row, how = find(idx, target)
        return record(target, row, how, idx.meta) if row else None

    def count(self) -> int:
        return load_index().rows
