"""County clerk foreclosure notices - the clerks' own public record search.

    https://dallas.tx.publicsearch.us    department FC ("Foreclosures")
    https://tarrant.tx.publicsearch.us   department FC
    https://denton.tx.publicsearch.us    department FC
    https://johnson.tx.publicsearch.us   department FC

In Texas a non-judicial foreclosure (deed-of-trust trustee or substitute
trustee sale, and an HOA assessment-lien sale under a declaration) must be
posted and filed with the county clerk at least 21 days before the sale, which
is held on the first Tuesday of the month (Tex. Prop. Code 51.002). These
three clerks publish every filed notice in a free, anonymous search (the
GovOS / Kofile "publicsearch" portal): document number, recorded date, sale
date (Dallas, Denton, Johnson: the day; Tarrant: the month), the property address
the clerk indexed (Dallas, Tarrant, Johnson), and the notice's own OCR text, which the
portal's full-text search reads.

The portal's browser client talks to the server over one websocket
(wss://<county>.tx.publicsearch.us/ws) with an anonymous session token the
home page hands every visitor. This reader does exactly what a visitor's
browser does: one page load, then paged "fetch documents" requests, politely
spaced. No login, no paid image, no CAPTCHA, nothing a visitor cannot do.

What a notice proves, and what it does not:
  * PROVES a sale was SCHEDULED: a notice for this address was filed with the
    clerk for that sale date (a VERIFIED notice).
  * Does NOT prove the sale happened. Sales are cancelled (paid, loan
    modified) or postponed (a new notice for a later month) all the time; a
    later notice for the same address is shown as a re-posting.
  * No notice found is NOT proof there is no foreclosure (an unindexed
    address, an OCR miss, a notice filed after the last check).

Classification is read from the notice itself (document type and text):
substitute trustee sale, trustee sale, HOA / assessment-lien sale, judicial
(sheriff / constable order of sale) and tax sale. A voided filing is never a
scheduled sale.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.evosense import fc_match as M
from app.services.evosense.sources import base as B

ADAPTER_VERSION = "tx_clerk_fc/3"
PAGE = 250                       # the portal's own page size limit
MAX_PAGES = 16                   # 4,000 notices per county per read - a hard stop
SPACING = 0.6                    # seconds between requests (a person paging is slower; still gentle)

# What each clerk's portal offers, as read live on 2026-10-23.
#   window: the query field that filters by the date we want (Dallas and
#           Denton filter the sale date; Tarrant filters the recorded date)
#   sale:   where the sale date lives on a notice ("day" or "month")
#   address: the clerk indexes the property address (Dallas, Tarrant) or not
COUNTIES: Dict[str, Dict[str, Any]] = {
    "dallas": {"host": "dallas.tx.publicsearch.us", "label": "Dallas County Clerk",
               "window": "instrumentDateRange", "window_on": "sale", "sale": "day", "address": True},
    "tarrant": {"host": "tarrant.tx.publicsearch.us", "label": "Tarrant County Clerk",
                "window": "recordedDateRange", "window_on": "recorded", "sale": "month", "address": True},
    "denton": {"host": "denton.tx.publicsearch.us", "label": "Denton County Clerk",
               "window": "instrumentDateRange", "window_on": "sale", "sale": "day", "address": False},
    # read live 2026-10-23: 44 notices for the 2026-11-03 sale, every one with its address
    "johnson": {"host": "johnson.tx.publicsearch.us", "label": "Johnson County Clerk",
                "window": "instrumentDateRange", "window_on": "sale", "sale": "day", "address": True},
}
# Counties whose official notices EvoSense does not read automatically (bot
# protection or scanned files): each says where the official notices are and
# to record them by hand - reported as such, never as "no foreclosure".
NO_PORTAL = {
    "collin": "Collin County posts its notices on its own page, https://apps2.collincountytx.gov/"
              "ForeclosureNotices (checked 2026-10-23). It is behind bot protection, so EvoSense does not "
              "read it automatically: look the house up there and use Record a notice with its notice number.",
    "ellis": "Ellis County Clerk posts one scanned file per sale date in its Archive Center, "
             "https://www.elliscountytx.gov/ArchiveCenter/ (\"County Clerk Foreclosures\"; checked 2026-10-23, "
             "re-checked 2026-10-24). It is a scanned image with no searchable text, so EvoSense does not read it "
             "automatically: find the house in the file for the sale date and use Record a notice with the page.",
    "kaufman": "Kaufman County Clerk posts one scanned file per sale month on its Foreclosures page, "
               "https://www.kaufmancounty.net/383/Foreclosures (checked 2026-10-23, re-checked 2026-10-24). It is "
               "a scanned image with no searchable text, so EvoSense does not read it automatically: find the house "
               "in the month's file and use Record a notice with the page.",
    "rockwall": "Rockwall County posts each notice as its own scanned file, listed by grantor name under the sale "
                "month, at https://www.rockwallcountytexas.com/792/Foreclosure-Notices (checked 2026-10-23, "
                "re-checked 2026-10-24). EvoSense does not read the scans automatically: open the owner's notice "
                "and use Record a notice with its file number.",
}

MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
                                      "OCT", "NOV", "DEC"), 1)}

# ── foreclosure categories ────────────────────────────────────────────────
SUBSTITUTE_TRUSTEE = "substitute_trustee_sale"
TRUSTEE = "trustee_sale"
HOA = "hoa_assessment_lien_sale"
JUDICIAL = "judicial_foreclosure_sale"
TAX = "tax_foreclosure_sale"
NOTICE = "foreclosure_sale_notice"          # a filed notice whose kind the text does not say
CATEGORY_LABELS = {
    SUBSTITUTE_TRUSTEE: "Substitute trustee sale (deed of trust)",
    TRUSTEE: "Trustee sale (deed of trust)",
    HOA: "HOA / assessment-lien foreclosure sale",
    JUDICIAL: "Judicial foreclosure sale (court order of sale)",
    TAX: "Tax foreclosure sale (sheriff / constable)",
    NOTICE: "Foreclosure sale notice",
}

SCHEDULED, VOID, CANCELED = "scheduled", "void", "canceled"


def classify(doc_type: str, text: str) -> Tuple[str, str]:
    """(category, status) from the document type and the notice's own words."""
    dt = (doc_type or "").upper()
    t = re.sub(r"\s+", " ", (text or "").upper())
    if dt.startswith("VOID") or " VOID" in dt:
        status = VOID
    elif re.search(r"RESCI|WITHDRAW|CANCEL", dt):
        status = CANCELED
    else:
        status = SCHEDULED
    if re.search(r"\bTAX(ES)?\b", t) and re.search(r"CONSTABLE|SHERIFF|ORDER OF SALE|TAX SALE", t):
        cat = TAX
    elif re.search(r"ORDER OF SALE|CONSTABLE'?S? SALE|SHERIFF'?S? SALE|WRIT OF EXECUTION", t):
        cat = JUDICIAL
    elif re.search(r"(HOMEOWNERS?'?|PROPERTY OWNERS?'?|OWNERS'?|CONDOMINIUM|COMMUNITY) ASSOCIATION", t) \
            and re.search(r"ASSESSMENT|LIEN|DECLARATION", t):
        cat = HOA
    elif re.search(r"SUBSTITUTE\]? ?TRUSTEE|SUBSTITUTE TRUSTEE", t):
        cat = SUBSTITUTE_TRUSTEE
    elif "TRUSTEE" in t:
        cat = TRUSTEE
    else:
        cat = NOTICE
    return cat, status


def first_tuesday(year: int, month: int) -> date:
    """Tex. Prop. Code 51.002(a): the first Tuesday of the month; when that is
    January 1 or July 4 the sale is held on the first Wednesday instead."""
    d = date(year, month, 1)
    d += timedelta(days=(1 - d.weekday()) % 7)
    if (d.month, d.day) in ((1, 1), (7, 4)):
        d += timedelta(days=1)
    return d


def sale_date(doc: Dict[str, Any]) -> Tuple[Optional[date], Optional[str]]:
    """(sale date, basis). Dallas / Denton print the day; Tarrant the month."""
    v = (doc.get("instrumentDate") or "").strip()
    if v:
        try:
            return datetime.strptime(v[:10], "%m/%d/%Y").date(), "sale date on the clerk's index"
        except ValueError:
            pass
    v = (doc.get("saleDate") or "").strip().upper()
    m = re.match(r"^([A-Z]{3})[A-Z]*\.?\s+(\d{4})$", v)
    if m and m.group(1) in MONTHS:
        return (first_tuesday(int(m.group(2)), MONTHS[m.group(1)]),
                "sale month on the clerk's index (%s); Texas trustee sales are held the first Tuesday" % v.title())
    return None, None


def recorded_date(doc: Dict[str, Any]) -> Optional[date]:
    try:
        return datetime.strptime((doc.get("recordedDate") or "")[:10].strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


_SUFFIX = {"DR", "DRIVE", "LN", "LANE", "ST", "STREET", "CT", "COURT", "AVE", "AV", "AVENUE", "RD", "ROAD",
           "TRL", "TRAIL", "CIR", "CIRCLE", "WAY", "PL", "PLACE", "GRN", "GREEN", "CRK", "CREEK", "BLVD",
           "PKWY", "PARKWAY", "HWY", "LOOP", "TER", "TERRACE", "BLDG", "UNIT", "APT", "COVE", "CV", "PATH"}
_DIR = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST", "NE", "NW", "SE", "SW"}


def street_key(street: Optional[str]) -> Optional[str]:
    """'1345 DELL OAK DRIVE' -> '1345 DELL OAK'. House number plus the street
    name without directions and suffixes, so 'DR' / 'DRIVE' and '899 S FM
    1138' / '899 FM 1138' agree. None without a house number."""
    words = re.sub(r"[^A-Z0-9 ]", " ", (street or "").upper()).split()
    if not words or not re.match(r"^\d+[A-Z]?$", words[0]):
        return None
    name = [w for w in words[1:] if w not in _DIR]
    cut = next((i for i, w in enumerate(name) if w in ("BLDG", "UNIT", "APT")), None)
    if cut:
        name = name[:cut]
    while len(name) > 1 and name[-1] in _SUFFIX:
        name.pop()
    return " ".join([words[0]] + name) if name else None


_SUFFIX_RE = "|".join(sorted((x for x in _SUFFIX if x not in ("BLDG", "UNIT", "APT")), key=len, reverse=True))
_STREET = re.compile(r"\b(\d{2,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z0-9'\-]+\.?\s+){0,4}?(?:%s))\b\.?" % _SUFFIX_RE)
_CITY_ZIP = re.compile(r"([A-Z][A-Z .]{2,30}?),?\s+(?:TX|TEXAS)\.?,?\s+(7[5-9]\d{3})")


def ocr_address(text: str) -> Optional[Dict[str, str]]:
    """The property address a notice states ('Property Address: 1801 FORSYTHE
    DRIVE, SAVANNAH, TX 76227' / 'Commonly known as: ...'): a house number and
    street ending in a street suffix, followed within a short stretch by a
    Texas city and a 75xxx-79xxx ZIP. Otherwise None (never a guess)."""
    t = re.sub(r"\s+", " ", (text or "").upper().replace("<EM>", "").replace("</EM>", ""))
    for m in _STREET.finditer(t):
        toks = m.group(1).strip(" ,.").split()
        while len(toks) > 2 and toks[1].isdigit():
            toks = toks[1:]               # '26-01888 2905 MARE DRIVE': a file number, then the house number
        street = " ".join(toks)
        if not street_key(street):
            continue
        tail = t[m.end():m.end() + 90]
        cz = _CITY_ZIP.search(tail)
        if cz:
            city = re.sub(r"^(?:[A-Z]{1,2}\s+)+", "", cz.group(1).strip(" ,.")) or cz.group(1).strip(" ,.")
            return {"address1": street, "city": city, "zip": cz.group(2)}
    return None


def addresses(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    out = [a for a in doc.get("propAddress") or [] if isinstance(a, dict) and a.get("address1")]
    if not out:
        a = ocr_address(_text(doc))
        if a:
            out = [dict(a, _from="notice text")]
    return out


def _text(doc: Dict[str, Any]) -> str:
    hl = (doc.get("highlights") or {}).get("ocrText") or []
    return " ".join([doc.get("ocrText") or ""] + [h for h in hl if isinstance(h, str)])


def match_key(street: Optional[str]) -> Optional[str]:
    """street_key with the spaces inside the street name removed: the clerk
    writes "2728 RUNNING BROOK LN" and the appraisal district "2728
    RUNNINGBROOK LN"; "2233 PARK HURST" / "2233 PARKHURST" (both real misses,
    2026-10-24). The house number stays separate."""
    k = street_key(street)
    if not k:
        return None
    num, _, name = k.partition(" ")
    return "%s %s" % (num, name.replace(" ", ""))


def same_place(doc: Dict[str, Any], street: str, zip_code: Optional[str], city: Optional[str]) -> bool:
    """True when the notice names THIS house: same house number and street
    name, and the same ZIP or city. The full-text fallback also accepts the
    address written in the notice's own text."""
    want = street_key(street)
    if not want:
        return False
    want_m = match_key(street)
    z = re.sub(r"\D", "", zip_code or "")[:5]
    c = (city or "").strip().upper()
    for a in addresses(doc):
        if match_key(a.get("address1")) == want_m:
            az = re.sub(r"\D", "", str(a.get("zip") or ""))[:5]
            ac = (a.get("city") or "").strip().upper().replace(", TEXAS", "").replace(" TEXAS", "")
            if (z and az == z) or (c and ac and (ac == c or ac.startswith(c) or c.startswith(ac))):
                return True
    t = re.sub(r"\s+", " ", _text(doc).upper().replace("<EM>", "").replace("</EM>", ""))
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t)
    if (" %s " % want) in (" %s " % t) or t.startswith(want + " "):
        return bool((z and z in t) or (c and c in t))
    num, _, name = want_m.partition(" ")
    tight = re.sub(r"(?<=[A-Z]) (?=[A-Z])", "", t)          # letters run together, numbers kept apart
    if re.search(r"(^| )%s %s( |$)" % (re.escape(num), re.escape(name)), tight):
        return bool((z and z in t) or (c and c.replace(" ", "") in tight))
    return False


class ClerkPortal:
    """One clerk's portal: an anonymous session and paged document reads."""

    def __init__(self, county: str):
        if county not in COUNTIES:
            raise B.SourceError(B.PROVIDER_NOT_CONFIGURED, "No clerk foreclosure portal connected for %s County" % county)
        self.county = county
        self.cfg = COUNTIES[county]
        self.host = self.cfg["host"]
        self._token: Optional[str] = None
        self._cookie = ""
        self.calls = 0

    def doc_url(self, doc: Dict[str, Any]) -> str:
        return "https://%s/doc/%s" % (self.host, doc.get("docId") or doc.get("id"))

    def _session(self):
        if self._token:
            return
        req = urllib.request.Request("https://%s/" % self.host, headers={"User-Agent": B.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=B.TIMEOUT, context=B.ssl_context()) as r:
                html = r.read(3 * 1024 * 1024).decode("utf-8", "replace")
                cookies = r.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise B.SourceError(B.AUTH_FAILED, "%s refused the page (%s)" % (self.host, exc.code))
            if exc.code in (429, 503):
                raise B.SourceError(B.RATE_LIMITED, "%s answered %s" % (self.host, exc.code), retry_after=900)
            raise B.SourceError(B.DOWNLOAD_FAILED, "%s answered %s" % (self.host, exc.code))
        except OSError as exc:
            raise B.SourceError(B.DOWNLOAD_FAILED, "%s: %s" % (self.host, str(exc)[:160]))
        m = re.search(r'authToken\\?"\s*:\s*\\?"([0-9a-f\-]{36})', html)
        if not m:
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "%s did not hand out a visitor session" % self.host)
        self._token = m.group(1)
        self._cookie = "; ".join(c.split(";", 1)[0] for c in cookies)

    def fetch(self, query: Dict[str, Any], *, timeout: float = 30.0) -> List[Dict[str, Any]]:
        """One page of documents for `query` (department FC is always added)."""
        from websockets.sync.client import connect
        from websockets.exceptions import InvalidStatus
        self._session()
        q = {"limit": str(PAGE), "offset": "0", "keywordSearch": False, "searchOcrText": False,
             "searchType": "quickSearch"}
        q.update(query)
        q["department"] = "FC"
        msg = {"type": "@kofile/FETCH_DOCUMENTS/v4",
               "payload": {"query": q, "workspaceID": "evosense"},
               "authToken": self._token, "correlationId": str(uuid.uuid4()), "sync": True}
        headers = {"User-Agent": B.USER_AGENT, "Origin": "https://%s" % self.host}
        if self._cookie:
            headers["Cookie"] = self._cookie
        if self.calls:
            time.sleep(SPACING)
        self.calls += 1
        try:
            with connect("wss://%s/ws" % self.host, additional_headers=headers, open_timeout=B.TIMEOUT,
                         close_timeout=5, max_size=64 * 1024 * 1024, ssl=B.ssl_context()) as ws:
                ws.send(json.dumps(msg))
                deadline = time.time() + timeout
                while time.time() < deadline:
                    raw = ws.recv(timeout=max(1.0, deadline - time.time()))
                    if not isinstance(raw, str) or "FETCH_DOCUMENTS" not in raw[:200]:
                        continue
                    body = json.loads(raw)
                    if "REJECTED" in body.get("type", "") or "FAIL" in body.get("type", ""):
                        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "%s rejected the search: %s"
                                            % (self.host, str(body.get("payload"))[:160]))
                    data = (body.get("payload") or {}).get("data") or {}
                    order, by = data.get("byOrder"), data.get("byHash")
                    if not isinstance(order, list) or not isinstance(by, dict):
                        raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "%s answered without a document list" % self.host)
                    return [by[str(i)] for i in order if str(i) in by]
        except B.SourceError:
            raise
        except InvalidStatus as exc:
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            if code in (401, 403):
                raise B.SourceError(B.AUTH_FAILED, "%s refused the search connection (%s)" % (self.host, code))
            if code in (429, 503):
                raise B.SourceError(B.RATE_LIMITED, "%s answered %s" % (self.host, code), retry_after=900)
            raise B.SourceError(B.DOWNLOAD_FAILED, "%s search connection: %s" % (self.host, code))
        except TimeoutError:
            raise B.SourceError(B.TIMEOUT_ERR, "%s did not answer the search in %ss" % (self.host, int(timeout)))
        except (OSError, ValueError) as exc:
            raise B.SourceError(B.DOWNLOAD_FAILED, "%s search: %s" % (self.host, str(exc)[:160]))
        raise B.SourceError(B.TIMEOUT_ERR, "%s did not answer the search in %ss" % (self.host, int(timeout)))

    def window(self, start: date, end: date) -> List[Dict[str, Any]]:
        """Every foreclosure notice in the date window.

        Read in short date chunks, each small enough for ONE page. Paging a
        large window (offset 250, 500 ...) is not stable on this portal:
        Tarrant's 1,495 paged rows held only 1,288 different notices and
        skipped others (read live 2026-10-24). A chunk that still fills a
        page is split in half until it does not; a single day that fills a
        page falls back to paging, de-duplicated."""
        seen: Dict[Any, Dict[str, Any]] = {}
        step = 7 if self.cfg["window_on"] == "recorded" else 31
        todo: List[Tuple[date, date]] = []
        d = start
        while d <= end:
            e = min(end, d + timedelta(days=step - 1))
            todo.append((d, e))
            d = e + timedelta(days=1)
        reads = 0
        while todo and reads < MAX_PAGES * 8:
            a, b = todo.pop(0)
            rng = "%s,%s" % (a.strftime("%Y%m%d"), b.strftime("%Y%m%d"))
            docs = self.fetch({self.cfg["window"]: rng})
            reads += 1
            if len(docs) >= PAGE and a < b:
                mid = a + timedelta(days=(b - a).days // 2)
                todo[:0] = [(a, mid), (mid + timedelta(days=1), b)]
                continue
            if len(docs) >= PAGE:                       # one day, still full: page it
                for page in range(1, MAX_PAGES):
                    more = self.fetch({self.cfg["window"]: rng, "offset": str(page * PAGE)})
                    reads += 1
                    docs = docs + more
                    if len(more) < PAGE:
                        break
            for x in docs:
                seen[x.get("docId") or x.get("id") or x.get("docNumber")] = x
        return list(seen.values())

    def full_text(self, value: str, limit: int = 50) -> List[Dict[str, Any]]:
        """The portal's full-text search over the notices' OCR text."""
        return self.fetch({"searchValue": value, "searchOcrText": True, "limit": str(limit)})


def notice_facts(portal: ClerkPortal, doc: Dict[str, Any]) -> Dict[str, Any]:
    sd, basis = sale_date(doc)
    cat, status = classify(doc.get("docType") or "", _text(doc))
    rd = recorded_date(doc)
    excerpt = re.sub(r"\s+", " ", re.sub(r"</?em>", "", _text(doc)))[:400]
    return {"county": portal.county.title(), "clerk": portal.cfg["label"],
            "document_number": doc.get("docNumber") or doc.get("documentNumber"),
            "doc_id": doc.get("docId") or doc.get("id"), "doc_type": doc.get("docType"),
            "recorded": rd.isoformat() if rd else None,
            "sale_date": sd.isoformat() if sd else None, "sale_date_basis": basis,
            "category": cat, "category_label": CATEGORY_LABELS[cat], "status": status,
            "url": portal.doc_url(doc), "excerpt": excerpt or None,
            "addresses": [{k: a.get(k) for k in ("address1", "city", "zip")} for a in addresses(doc)]}


def to_record(portal: ClerkPortal, doc: Dict[str, Any], *, target: Optional[Dict[str, Any]] = None,
              matched_by: str = "clerk index address", as_of: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """An ingest record for one notice. With `target` (a known property) the
    record carries that property's address; otherwise the notice's own."""
    f = notice_facts(portal, doc)
    if f["status"] == VOID:
        return None
    addr = (f["addresses"] or [None])[0]
    if target is None and not addr:
        return None
    ref = "TXCLERK:%s:FC:%s" % (portal.county.upper(), f["document_number"])
    stype = "TAX_SALE" if f["category"] == TAX else "PRE_FORECLOSURE"
    if f["status"] == CANCELED:
        what = "%s withdrawn / cancelled - %s document %s" % (f["category_label"], f["clerk"], f["document_number"])
        case_status = "canceled"
    else:
        what = "%s scheduled %s - notice filed with the %s %s (document %s)" % (
            f["category_label"], f["sale_date"] or "(date not on the index)", f["clerk"],
            f["recorded"] or "", f["document_number"])
        case_status = "scheduled"
    sig = {"type": stype, "confidence": 92 if matched_by == "clerk index address" else 85,
           "value": what, "raw": "DOC=%s;TYPE=%s;SALE=%s;RECORDED=%s" % (
               f["document_number"], f["doc_type"], f["sale_date"], f["recorded"]),
           "effective_at": f["recorded"], "observed_days_ago": 0,
           "evidence_basis": "posted foreclosure notice filed with the county clerk (%s)" % matched_by,
           "case_status": case_status, "sale_date": f["sale_date"], "ref": ref,
           "foreclosure_type": f["category"], "document": f["document_number"], "document_url": f["url"],
           "recorded": f["recorded"], "sale_date_basis": f["sale_date_basis"], "matched_by": matched_by,
           "limitations": "A filed notice proves a sale was scheduled, not that it happened; sales are often "
                          "cancelled or postponed."}
    if target is not None:
        street, city, zc = target.get("street_address"), target.get("city"), target.get("zip_code")
    else:
        street, city, zc = addr["address1"], (addr.get("city") or "").replace(", TEXAS", "").title() or None, addr.get("zip")
    raw = {k: doc.get(k) for k in ("docId", "docNumber", "docType", "recordedDate", "instrumentDate", "saleDate",
                                   "propAddress", "ocrText")}
    return {"source_reference": ref, "street_address": re.sub(r"\s+", " ", street or "").strip() or None,
            "city": city, "state": "TX", "zip_code": re.sub(r"\D", "", str(zc or ""))[:5] or None,
            "county": portal.county.title(), "signals": [sig],
            "_raw": raw, "_source_url": f["url"], "_source_updated_at": as_of or datetime.utcnow(),
            "_adapter_version": ADAPTER_VERSION,
            "_evidence": {k: f[k] for k in ("clerk", "document_number", "doc_type", "recorded", "sale_date",
                                            "sale_date_basis", "category", "category_label", "status", "url",
                                            "excerpt")}}


def default_window(today: Optional[date] = None) -> Tuple[date, date]:
    """Sales from two months back (to see re-postings) to five months ahead."""
    today = today or date.today()
    return today - timedelta(days=62), today + timedelta(days=155)



def index_text(doc: Dict[str, Any]) -> str:
    """Everything the clerk returned about a notice that can name the house:
    the index's address lines (a legal description in Tarrant / Johnson) and
    the notice text the search showed."""
    parts = [x for x in (doc.get("propertyAddress") or []) if isinstance(x, str)]
    for a in doc.get("propAddress") or []:
        if isinstance(a, dict):
            parts.append(" ".join(str(a.get(k) or "") for k in ("address1", "address2", "city")))
    return " ".join(parts + [_text(doc)])


def index_city(doc: Dict[str, Any]) -> Optional[str]:
    for a in doc.get("propAddress") or []:
        if isinstance(a, dict) and a.get("city"):
            return str(a["city"]).replace(", TEXAS", "").strip()
    for x in doc.get("propertyAddress") or []:
        if isinstance(x, str) and "," in x:
            parts = [y.strip() for y in x.split(",")]
            city = parts[-2] if len(parts) >= 3 else parts[0]
            if city and not re.search(r"\d", city):
                return city
    return None


def norm_words(text: Optional[str]) -> List[str]:
    return M.norm(text).replace("/", " ").split()


def subdivision_phrase(legal: Dict[str, Any]) -> str:
    """The words of the plat name to search for, as they stand next to each
    other: 'RESERVE AT HIGH POINTE PH 3' -> 'HIGH POINTE' (not 'RESERVE HIGH',
    which no notice contains - the phrase search needs adjacent words)."""
    runs: List[List[str]] = [[]]
    for w in norm_words(legal.get("subdivision")):
        if w in legal["core"]:
            runs[-1].append(w)
        elif runs[-1]:
            runs.append([])
    best = max(runs, key=len)
    return " ".join(best[:2])


def _name_queries(ident: Dict[str, Any]) -> List[str]:
    """What to search the notices for: the list's borrower as written
    (first and last name, the order a notice uses), then the owner of
    record's most distinctive word (an appraisal roll writes LAST FIRST)."""
    out: List[str] = []
    for owner in ident.get("list_names") or []:
        for g in M.name_groups(owner):
            last = g[-1]
            if len(last) >= 4 and last not in M._COMMON and not last.isdigit():
                q = last                                   # an uncommon surname finds the notice on its own
            elif len(g) >= 2:
                q = " ".join(g[-2:])                       # 'ERIC LEE': first and last name as written
            else:
                q = ""
            if q and q not in out:
                out.append(q)
    for owner in ident.get("owner_names") or []:
        for g in M.name_groups(owner):
            rare = sorted([w for w in g if len(w) >= 4 and w not in M._COMMON and not w.isdigit()], key=len, reverse=True)
            if rare and rare[0] not in out and not any(rare[0] in x.split() for x in out):
                out.append(rare[0])
    return out


def _brief(p: "ClerkPortal", x: Dict[str, Any]) -> Dict[str, Any]:
    d = x["doc"]
    sd, _ = sale_date(d)
    return {"document": d.get("docNumber") or d.get("documentNumber"), "url": p.doc_url(d),
            "sale_date": sd.isoformat() if sd else None, "decision": x["decision"], "signals": x["signals"],
            "reason": x.get("reason") or x.get("matched_by"), "text": (x.get("text") or "")[:300]}


class ClerkForeclosureReader:
    """Discovery (every notice in a window) and per-property checks."""
    adapter_version = ADAPTER_VERSION

    def __init__(self, portal_factory=ClerkPortal):
        self._factory = portal_factory
        self._portals: Dict[str, ClerkPortal] = {}

    def portal(self, county: str) -> ClerkPortal:
        county = (county or "").strip().lower().replace(" county", "")
        if county not in self._portals:
            self._portals[county] = self._factory(county)
        return self._portals[county]

    def notices(self, county: str, today: Optional[date] = None) -> List[Dict[str, Any]]:
        p = self.portal(county)
        start, end = default_window(today)
        if p.cfg["window_on"] == "recorded":
            # the window filters the RECORDED date: notices are filed 21+ days
            # (often 1-3 months) before the sale
            docs = p.window(start - timedelta(days=120), today or date.today())
        else:
            docs = p.window(start, end)
        seen, out = set(), []
        for d in docs:
            k = d.get("docId") or d.get("id")
            if k in seen:
                continue
            seen.add(k)
            sd, _ = sale_date(d)
            if sd is None or start <= sd <= end:
                out.append(d)
        return out

    def discover(self, counties: Iterable[str], *, limit: int = 250, today: Optional[date] = None,
                 rotate: Optional[int] = None) -> Dict[str, Any]:
        """Upcoming scheduled sales with an address. Counties take turns
        (Dallas, Tarrant, Denton, Dallas ...) so a small run reaches every
        county, and each day starts at a different place in that order so
        daily runs reach new notices instead of re-reading the same first
        ones. A run that can take everything gets everything."""
        today = today or date.today()
        stats: Dict[str, Any] = {}
        recs: List[Tuple[date, Dict[str, Any]]] = []
        for c in counties:
            c = c.strip().lower().replace(" county", "")
            if c not in COUNTIES:
                stats[c] = {"status": "NOT CONNECTED", "reason": NO_PORTAL.get(c, "no clerk foreclosure portal connected")}
                continue
            p = self.portal(c)
            docs = self.notices(c, today)
            st = {"status": "COMPLETED", "notices": len(docs), "upcoming": 0, "no_address": 0, "void_or_cancelled": 0}
            for d in docs:
                sd, _ = sale_date(d)
                if sd is None or sd < today:
                    continue
                st["upcoming"] += 1
                rec = to_record(p, d)
                if rec is None:
                    st["void_or_cancelled" if classify(d.get("docType") or "", "")[1] != SCHEDULED else "no_address"] += 1
                    continue
                recs.append((sd, rec))
            stats[c] = st
        by_county: Dict[str, List[Tuple[date, Dict[str, Any]]]] = {}
        for sd, r in recs:
            by_county.setdefault(r["county"], []).append((sd, r))
        lanes = [sorted(v, key=lambda t: (t[0], t[1]["source_reference"])) for _, v in sorted(by_county.items())]
        order: List[Dict[str, Any]] = []
        while any(lanes):
            for lane in lanes:
                if lane:
                    order.append(lane.pop(0)[1])
        if len(order) > limit and order:
            day = date.today().timetuple().tm_yday if rotate is None else rotate
            start = (day * limit) % len(order)
            order = order[start:] + order[:start]
        stats["rotation_start"] = 0 if len(recs) <= limit else start
        return {"records": order[:limit], "stats": stats,
                "source_url": "https://%s/" % COUNTIES["dallas"]["host"], "source_updated_at": datetime.utcnow()}

    def check(self, county: str, props: List[Dict[str, Any]], *, today: Optional[date] = None,
              max_searches: Optional[int] = None) -> Dict[str, Any]:
        """Which of THESE properties have a filed notice.

        1. the clerk's indexed address (street + ZIP/city);
        2. the clerk's full-text search of the notices for '<number> <street>';
        3. when the street finds nothing and the property's identity is known
           (``t["identity"]`` from fc_match.identity): the notices whose index
           or text carry its legal description, parcel number or owner /
           borrower name, each judged by fc_match.decide - VERIFIED only with
           two independent kinds of evidence, otherwise a CANDIDATE for a
           person, and REJECTED when the notice names another lot.

        Returns {"matches": {prop id: [(doc, matched_by)]}, "candidates":
        {prop id: [...]}, "rejected": {prop id: [...]}, "stats": {...}}."""
        today = today or date.today()
        p = self.portal(county)
        docs = self.notices(county, today)
        index: Dict[str, List[Dict[str, Any]]] = {}
        for d in docs:
            for a in addresses(d):
                k = match_key(a.get("address1"))
                if k:
                    index.setdefault(k, []).append(d)
        # notices the clerk indexed by LEGAL DESCRIPTION (Tarrant, Johnson):
        # matched locally, without another request
        legal_index = [(d, index_text(d)) for d in docs if M.notice_legals(index_text(d))]
        matches: Dict[str, List[Tuple[Dict[str, Any], str]]] = {}
        candidates: Dict[str, List[Dict[str, Any]]] = {}
        rejected: Dict[str, List[Dict[str, Any]]] = {}
        st = {"status": "COMPLETED", "notices_in_window": len(docs), "properties": len(props),
              "matched_index": 0, "matched_full_text": 0, "matched_evidence": 0, "candidates": 0,
              "rejected": 0, "no_match": 0, "full_text_searches": 0, "evidence_searches": 0,
              "legal_indexed_notices": len(legal_index)}
        start, _end = default_window(today)
        need = sum(1 for t in props if t.get("identity"))
        budget = {"left": max_searches if max_searches is not None else min(400, 40 + 6 * need)}
        for t in props:
            street = t.get("street_address")
            k = street_key(street)
            hit: List[Dict[str, Any]] = []
            how = ""
            if k:
                hit = [d for d in index.get(match_key(street), [])
                       if same_place(d, street, t.get("zip_code"), t.get("city"))]
                how = "clerk index address"
                if not hit:
                    st["full_text_searches"] += 1
                    found = p.full_text(k)
                    hit = [d for d in found if same_place(d, street, t.get("zip_code"), t.get("city"))]
                    hit = [d for d in hit if (sale_date(d)[0] or today) >= start]
                    how = "notice text (clerk full-text search)"
            ev_hits: List[Tuple[Dict[str, Any], str]] = []
            if not hit and t.get("identity"):
                ev = self._corroborate(p, t["identity"], legal_index, start, budget, st)
                ev_hits = [(x["doc"], "evidence: " + x["matched_by"]) for x in ev if x["decision"] == M.VERIFIED]
                cands = [_brief(p, x) for x in ev if x["decision"] == M.CANDIDATE]
                rej = [_brief(p, x) for x in ev if x["decision"] == M.REJECTED]
                if cands:
                    candidates[t["id"]] = cands
                if rej:
                    rejected[t["id"]] = rej
            uniq, seen = [], set()
            for d, h in [(d, how) for d in hit] + ev_hits:
                key = d.get("docId") or d.get("id")
                if key not in seen:
                    seen.add(key)
                    uniq.append((d, h))
            if uniq:
                matches[t["id"]] = uniq
                h0 = uniq[0][1]
                st["matched_index" if h0.startswith("clerk index") else
                   "matched_evidence" if h0.startswith("evidence") else "matched_full_text"] += 1
            else:
                st["no_match"] += 1
                if t["id"] in candidates:
                    st["candidates"] += 1
            if t["id"] in rejected:
                st["rejected"] += 1
        st["requests"] = p.calls
        st["search_budget_left"] = budget["left"]
        return {"matches": matches, "candidates": candidates, "rejected": rejected, "stats": st, "portal": p}

    def _corroborate(self, p: "ClerkPortal", ident: Dict[str, Any], legal_index: List[Tuple[Dict[str, Any], str]],
                     start: date, budget: Dict[str, int], st: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Candidate notices for one property from its legal description,
        parcel number and names; each judged by fc_match.decide."""
        pool: Dict[Any, Dict[str, Any]] = {}

        def add(d: Dict[str, Any], text: str) -> None:
            sd, _ = sale_date(d)
            if sd is not None and sd < start:
                return                                   # an old notice is history, not this sale
            key = d.get("docId") or d.get("id") or d.get("docNumber")
            slot = pool.setdefault(key, {"doc": d, "texts": []})
            if text and text not in slot["texts"]:
                slot["texts"].append(text)

        def search(value: str) -> List[Dict[str, Any]]:
            if budget["left"] <= 0 or not value:
                return []
            budget["left"] -= 1
            st["evidence_searches"] += 1
            try:
                return p.full_text(value, limit=100)
            except B.SourceError:
                return []

        legal = ident.get("legal")
        if legal:
            for d, txt in legal_index:
                if M.subdivision_in(legal["core"], txt):
                    add(d, txt)
        for pid in [x for x in ident.get("parcel_ids") or [] if M._pid(x)][:2]:
            for d in search(pid):
                add(d, index_text(d))
        if legal and not any(M.legal_match(legal, " ".join(v["texts"]))["result"] for v in pool.values()):
            phrase = subdivision_phrase(legal)
            if phrase:
                for d in search('"%s"' % phrase):
                    add(d, index_text(d))
        for value in _name_queries(ident)[:2]:
            for d in search('"%s"' % value):
                add(d, index_text(d))
        # a notice found by name or parcel whose snippet does not show the lot:
        # ask for the block, and read the lot in that notice's snippet
        if legal and legal.get("block"):
            short = [k for k, v in pool.items() if not M.notice_legals(" ".join(v["texts"]))]
            if short:
                b = legal["block"]
                raw_block = re.search(r"\bBLK\s+(\S+)", M.norm(legal.get("raw") or ""))
                q = raw_block.group(1) if raw_block and "/" in raw_block.group(1) else "BLOCK %s" % b
                for d in search('"%s"' % q):
                    key = d.get("docId") or d.get("id") or d.get("docNumber")
                    if key in pool:
                        add(d, index_text(d))
        out = []
        for v in pool.values():
            text = " ".join(v["texts"])
            r = M.decide(ident, text, index_city(v["doc"]))
            if r["decision"] != M.NONE:
                out.append(dict(r, doc=v["doc"], text=text[:600]))
        return out

    def verify(self, county: str = "dallas") -> Dict[str, Any]:
        p = self.portal(county)
        today = date.today()
        docs = p.fetch({p.cfg["window"]: "%s,%s" % ((today - timedelta(days=30)).strftime("%Y%m%d"),
                                                     (today + timedelta(days=60)).strftime("%Y%m%d")),
                        "limit": "5"})
        if not docs:
            raise B.SourceError(B.SOURCE_FORMAT_CHANGED, "%s returned no foreclosure notices" % p.host)
        return {"county": county, "sample_notices": len(docs), "newest_document": docs[0].get("docNumber")}
