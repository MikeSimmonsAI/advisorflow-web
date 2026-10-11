"""Ready-made wholesale contracts, filled from the deal, ready to sign.

Three documents a Texas assignment deal needs:

    purchase_agreement    seller -> you ("and/or assigns"): as-is, cash,
                          option period, assignable, Texas law
    assignment_agreement  you -> the end buyer: your fee, their deposit,
                          they step into the purchase contract
    assignee_disclosure   the written notice Texas requires (Occupations
                          Code 1101.0045) that you are selling a CONTRACT
                          (an equitable interest), not the house itself

These are STARTER TEMPLATES. They were written to be clear and fair to both
sides, not by a Texas attorney. Every screen that shows one says so, and the
operator decides when they are good enough to use. The documents themselves
carry no such banner - a contract that argues with itself is worse than none.

Every value comes from the deal (or from what the person typed on the send
screen). A blank stays a visible blank in a preview, and a document cannot be
sent for signature while a REQUIRED blank is open - a contract with
"$________" in the price is not a contract.

Signature, initials and date boxes are DocuSeal field tags
(<signature-field role=...>). A printed copy shows them as plain lines.
"""
from __future__ import annotations

import html as _h
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

STARTER_NOTICE = ("Starter template written for EvoSys, not by an attorney. Have a Texas real "
                  "estate attorney review it once before you rely on it - most charge a flat fee "
                  "to approve a wholesale contract set.")

ROLE_SELLER, ROLE_BUYER = "Seller", "Buyer"
ROLE_ASSIGNOR, ROLE_ASSIGNEE = "Assignor", "Assignee"

# The deal's document type for each kind (WholesaleDocument.doc_type vocabulary).
DOC_TYPE = {"purchase_agreement": "purchase_contract",
            "assignment_agreement": "assignment_agreement",
            "assignee_disclosure": "buyer_doc"}

KINDS: Dict[str, Dict[str, Any]] = {
    "purchase_agreement": {
        "label": "Purchase agreement (seller to you)",
        "short": "Purchase agreement",
        "when": "When the seller says yes to your price.",
        "roles": [ROLE_SELLER, ROLE_BUYER],
        "required": ["seller_name", "buyer_entity", "address", "county", "price",
                     "earnest_money", "title_company", "closing_date", "option_days"],
    },
    "assignment_agreement": {
        "label": "Assignment of contract (you to your buyer)",
        "short": "Assignment",
        "when": "When a cash buyer agrees to take the deal.",
        "roles": [ROLE_ASSIGNOR, ROLE_ASSIGNEE],
        "required": ["buyer_entity", "assignee_name", "seller_name", "address", "county",
                     "contract_date", "price", "assignment_fee", "assignee_deposit",
                     "title_company", "closing_date"],
    },
    "assignee_disclosure": {
        "label": "Buyer disclosure (Texas: you sell a contract, not the house)",
        "short": "Buyer disclosure",
        "when": "Send it with the deal to a buyer, before they sign the assignment.",
        "roles": [ROLE_ASSIGNOR, ROLE_ASSIGNEE],
        "required": ["buyer_entity", "assignee_name", "address", "county"],
    },
}

LABELS = {
    "seller_name": "Seller's full legal name (as on the deed)",
    "seller_email": "Seller's email",
    "buyer_entity": "Your company (the buyer on the purchase contract)",
    "buyer_signer": "Who signs for your company",
    "buyer_email": "Your email",
    "assignee_name": "End buyer (assignee)",
    "assignee_signer": "Who signs for the end buyer",
    "assignee_email": "End buyer's email",
    "address": "Property address",
    "city": "City", "zip": "ZIP", "county": "County",
    "parcel": "Appraisal district account",
    "price": "Purchase price",
    "earnest_money": "Earnest money",
    "option_days": "Option period (days)",
    "option_fee": "Option fee",
    "title_company": "Title company",
    "closing_date": "Closing date",
    "contract_date": "Date of the purchase contract",
    "assignment_fee": "Assignment fee",
    "assignee_deposit": "End buyer's deposit",
    "closing_costs": "Who pays closing costs",
    "possession": "Possession",
    "additional_terms": "Additional terms",
}

DEFAULT_CLOSING_COSTS = ("Buyer pays the customary closing costs, including the escrow fee and the "
                         "owner's title policy premium. Seller's loans, liens and unpaid property taxes "
                         "are paid from Seller's proceeds at closing.")
DEFAULT_POSSESSION = "At closing, with the property vacant and free of personal items, unless agreed otherwise in writing."


# ── values ─────────────────────────────────────────────────────────────────

def _money(v) -> Optional[str]:
    if v is None or v == "":
        return None
    try:
        n = float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None
    return "${:,.2f}".format(n).replace(".00", "")


def _day(v) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                v = datetime.strptime(v[:10], fmt).date()
                break
            except ValueError:
                continue
        else:
            return v
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return "%s %d, %d" % (v.strftime("%B"), v.day, v.year)
    return str(v)

# Things a person may type on the send screen. Anything else is ignored.
OVERRIDABLE = ("seller_name", "seller_email", "buyer_entity", "buyer_signer", "buyer_email",
               "assignee_name", "assignee_signer", "assignee_email", "price", "earnest_money",
               "option_days", "option_fee", "title_company", "closing_date", "contract_date",
               "assignment_fee", "assignee_deposit", "closing_costs", "possession",
               "additional_terms")
MONEY_KEYS = ("price", "earnest_money", "option_fee", "assignment_fee", "assignee_deposit")
DATE_KEYS = ("closing_date", "contract_date")


def build_values(*, deal=None, prop=None, seller_name=None, seller_email=None, buyer=None,
                 org_name=None, user=None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every value a document can use, from the deal first and the person's
    typed overrides second. Money and dates are formatted here, once."""
    g = lambda o, a: getattr(o, a, None) if o is not None else None   # noqa: E731
    street = " ".join(x for x in (g(prop, "street_address"), g(prop, "unit")) if x) or None
    v: Dict[str, Any] = {
        "seller_name": seller_name,
        "seller_email": seller_email,
        "buyer_entity": org_name,
        "buyer_signer": g(user, "full_name"),
        "buyer_email": g(user, "email"),
        "assignee_name": (g(buyer, "company_name") or g(buyer, "contact_name")) if buyer else None,
        "assignee_signer": g(buyer, "contact_name") if buyer else None,
        "assignee_email": g(buyer, "email") if buyer else None,
        "address": street,
        "city": g(prop, "city"),
        "zip": g(prop, "zip_code"),
        "county": g(prop, "county"),
        "parcel": g(prop, "parcel_apn"),
        "price": g(deal, "contract_price"),
        "earnest_money": g(deal, "earnest_money"),
        "option_fee": g(deal, "option_fee"),
        "option_days": None,
        "title_company": g(deal, "title_company"),
        "closing_date": g(deal, "closing_date") or g(deal, "close_of_escrow_target"),
        "contract_date": g(deal, "contract_date") or g(deal, "effective_date"),
        "assignment_fee": g(deal, "assignment_fee"),
        "assignee_deposit": None,
        "closing_costs": DEFAULT_CLOSING_COSTS,
        "possession": DEFAULT_POSSESSION,
        "additional_terms": None,
    }
    if v["option_days"] is None and deal is not None:
        insp, eff = g(deal, "inspection_deadline"), g(deal, "effective_date") or g(deal, "contract_date")
        if insp and eff:
            try:
                v["option_days"] = max(1, (insp - eff).days)
            except TypeError:
                pass
    for k, val in (overrides or {}).items():
        if k in OVERRIDABLE and val not in (None, ""):
            v[k] = val.strip() if isinstance(val, str) else val
    for k in MONEY_KEYS:
        v[k] = _money(v[k])
    for k in DATE_KEYS:
        v[k] = _day(v[k])
    if v["option_days"] not in (None, ""):
        m = re.match(r"\s*(\d{1,3})", str(v["option_days"]))
        v["option_days"] = m.group(1) if m else None
    return v


def missing(kind: str, values: Dict[str, Any]) -> List[str]:
    return [LABELS.get(k, k) for k in KINDS[kind]["required"] if not values.get(k)]


# ── rendering ──────────────────────────────────────────────────────────────

CSS = """
body{font-family:Georgia,'Times New Roman',serif;font-size:11.5pt;line-height:1.45;color:#111;margin:0}
h1{font-size:16pt;text-align:center;margin:0 0 4px}
.sub{text-align:center;font-size:10pt;color:#444;margin:0 0 18px}
h2{font-size:11.5pt;margin:14px 0 4px}
p{margin:0 0 7px}
.blank{display:inline-block;min-width:150px;border-bottom:1px solid #b45309;background:#fef3c7;color:#92400e;font-size:9pt;padding:0 4px}
.sig{margin-top:22px;display:flex;gap:28px;flex-wrap:wrap}
.sig>div{flex:1 1 260px;border-top:1px solid #999;padding-top:6px;font-size:10pt}
.sigbox{height:56px}
.small{font-size:9.5pt;color:#333}
"""


class _R:
    """Fills one document: a value, or a visible blank in a preview."""

    def __init__(self, values: Dict[str, Any], for_signature: bool):
        self.v, self.sign = values, for_signature

    def __call__(self, key: str, fallback: Optional[str] = None) -> str:
        val = self.v.get(key) or fallback
        if val:
            return _h.escape(str(val))
        return '<span class="blank">%s</span>' % _h.escape(LABELS.get(key, key))

    def sig(self, role: str, who: str, title: Optional[str] = None) -> str:
        tag = role.lower()
        box = ('<signature-field name="%s signature" role="%s" style="width:240px;height:56px;'
               'display:inline-block"></signature-field>' % (role, role)) if self.sign else \
            '<div class="sigbox"></div>'
        dt = ('<date-field name="%s date" role="%s" format="MM/DD/YYYY" style="width:120px;height:18px;'
              'display:inline-block"></date-field>' % (role, role)) if self.sign else "____________"
        return ('<div><div>%s</div><div><b>%s</b>: %s%s</div><div class="small">Date: %s</div></div>'
                % (box, role, who, (", " + _h.escape(title)) if title else "", dt)) if tag else ""


def _property(r: _R) -> str:
    city = ", ".join(x for x in (r.v.get("city"), "Texas") if x)
    return ("%s, %s %s, in %s County, Texas%s" % (
        r("address"), _h.escape(city), _h.escape(r.v.get("zip") or ""), r("county"),
        (" (appraisal district account %s)" % _h.escape(r.v["parcel"])) if r.v.get("parcel") else ""))


def _purchase(r: _R) -> str:
    return f"""
<h1>Residential Real Estate Purchase Agreement</h1>
<p class="sub">As-is cash purchase &middot; State of Texas</p>
<p>This agreement is made between <b>{r('seller_name')}</b> ("Seller") and <b>{r('buyer_entity')}
and/or assigns</b> ("Buyer").</p>
<h2>1. The property</h2>
<p>Seller agrees to sell and Buyer agrees to buy the land and all improvements and fixtures at
{_property(r)} (the "Property"). The legal description will be the one shown in the title
commitment issued by the title company named below.</p>
<h2>2. Price</h2>
<p>The purchase price is <b>{r('price')}</b>, paid in cash or certified funds at closing. This
purchase is not contingent on Buyer obtaining a loan.</p>
<h2>3. Earnest money</h2>
<p>Buyer will deposit <b>{r('earnest_money')}</b> as earnest money with <b>{r('title_company')}</b>
(the "Title Company") within three (3) business days after both parties sign. It is credited to
Buyer at closing.</p>
<h2>4. Option period (inspections)</h2>
<p>For <b>{r('option_days')}</b> days after both parties sign (the "Option Period"), Buyer may
inspect the Property and may end this agreement for any reason by written notice to Seller before
the Option Period ends; the earnest money is then returned to Buyer.{(' Buyer pays Seller an option fee of <b>' + r('option_fee') + '</b>, credited to Buyer at closing.') if r.v.get('option_fee') else ''}
Seller will give Buyer and Buyer's inspectors, partners and prospective assignees reasonable access
to the Property, with reasonable notice.</p>
<h2>5. Condition: as is</h2>
<p>Buyer buys the Property in its present "as is" condition. Seller is not required to make any
repairs. Seller will tell Buyer in writing about any known material defects, and will deliver the
Seller's Disclosure Notice required by Section 5.008 of the Texas Property Code if it applies.</p>
<h2>6. Title and closing</h2>
<p>Seller will convey good and marketable title by general warranty deed, free of liens other than
those paid at closing. Closing will take place at the Title Company on or before
<b>{r('closing_date')}</b>. Closing costs: {r('closing_costs')} Property taxes for the current
year are prorated to the closing date.</p>
<h2>7. Possession</h2>
<p>{r('possession')}</p>
<h2>8. Assignment</h2>
<p>Buyer may assign this agreement, or its rights under it, without Seller's consent. Seller
understands that Buyer may assign this agreement to another buyer for a fee, and that Buyer may
receive money from that buyer in addition to the purchase price. The price Seller receives does not
change if Buyer assigns.</p>
<h2>9. If someone does not perform</h2>
<p>If Buyer fails to close for a reason not allowed by this agreement, Seller may keep the earnest
money as Seller's only remedy. If Seller fails to close, Buyer may ask a court to require Seller to
sell (specific performance) or may end this agreement and receive the earnest money back.</p>
<h2>10. General</h2>
<p>Notices may be given by email to the addresses the parties use to sign. Electronic signatures
and signed copies are as binding as originals. This is the entire agreement; changes must be in
writing and signed by both parties. Texas law governs. Seller is advised to consult an attorney of
Seller's choosing before signing.</p>
{('<h2>11. Additional terms</h2><p>' + r('additional_terms') + '</p>') if r.v.get('additional_terms') else ''}
<div class="sig">
{r.sig(ROLE_SELLER, r('seller_name'))}
{r.sig(ROLE_BUYER, r('buyer_entity') + ' and/or assigns', r.v.get('buyer_signer') and 'by ' + r.v['buyer_signer'])}
</div>
"""


def _assignment(r: _R) -> str:
    return f"""
<h1>Assignment of Real Estate Purchase Agreement</h1>
<p class="sub">State of Texas</p>
<p><b>{r('buyer_entity')}</b> ("Assignor") assigns to <b>{r('assignee_name')}</b> ("Assignee") all of
Assignor's rights as buyer under the purchase agreement dated <b>{r('contract_date')}</b> between
<b>{r('seller_name')}</b>, as seller, and Assignor, as buyer (the "Purchase Agreement"), for the
property at {_property(r)} (the "Property").</p>
<h2>1. Assignment fee</h2>
<p>Assignee will pay Assignor an assignment fee of <b>{r('assignment_fee')}</b>. It is paid at
closing through <b>{r('title_company')}</b> and shown on the settlement statement. It is in addition
to the purchase price of <b>{r('price')}</b> that Assignee pays the seller under the Purchase
Agreement.</p>
<h2>2. Deposit</h2>
<p>Within two (2) business days after signing, Assignee will deposit <b>{r('assignee_deposit')}</b>
with the title company. It is credited to Assignee at closing. If Assignee fails to close for a
reason not allowed by the Purchase Agreement, Assignor keeps the deposit.</p>
<h2>3. Assignee steps in</h2>
<p>Assignee takes on all of the buyer's obligations under the Purchase Agreement, including closing
on or before <b>{r('closing_date')}</b>, and buys the Property as is. Assignee has had the chance to
review the Purchase Agreement and to inspect the Property, and relies on its own inspection and
judgment, not on any statement by Assignor about value, repair costs or rents.</p>
<h2>4. What Assignor is selling</h2>
<p>Assignor holds only an equitable interest in the Property under the Purchase Agreement. Assignor
does not own the Property and does not hold legal title to it. Assignor is not acting as Assignee's
real estate broker or agent.</p>
<h2>5. If the seller does not close</h2>
<p>If the sale fails because of the seller, the deposit is returned to Assignee and no assignment fee
is owed. Assignor's liability is limited to returning the deposit.</p>
<h2>6. General</h2>
<p>Electronic signatures are as binding as originals. This is the entire agreement between Assignor
and Assignee; changes must be in writing. Texas law governs.</p>
{('<h2>7. Additional terms</h2><p>' + r('additional_terms') + '</p>') if r.v.get('additional_terms') else ''}
<div class="sig">
{r.sig(ROLE_ASSIGNOR, r('buyer_entity'), r.v.get('buyer_signer') and 'by ' + r.v['buyer_signer'])}
{r.sig(ROLE_ASSIGNEE, r('assignee_name'), r.v.get('assignee_signer') and 'by ' + r.v['assignee_signer'])}
</div>
"""


def _disclosure(r: _R) -> str:
    return f"""
<h1>Notice to Buyer: Sale of a Contract Interest</h1>
<p class="sub">Texas Occupations Code Section 1101.0045</p>
<p><b>{r('buyer_entity')}</b> is offering to sell its interest in a contract to buy the property at
{_property(r)}.</p>
<p><b>{r('buyer_entity')} does not own this property and does not hold legal title to it.</b> It holds
only an equitable interest - the right to buy the property under a purchase contract with the
owner. What is being offered to you is that contract right, which you would take over by an
assignment.</p>
<p>{r('buyer_entity')} is not acting as your real estate broker or agent. You should do your own
inspection, check the title and comparable sales, and get any advice you want before agreeing to
anything.</p>
<p>By signing below, <b>{r('assignee_name')}</b> acknowledges receiving this notice before entering into
an assignment agreement.</p>
<div class="sig">
{r.sig(ROLE_ASSIGNOR, r('buyer_entity'), r.v.get('buyer_signer') and 'by ' + r.v['buyer_signer'])}
{r.sig(ROLE_ASSIGNEE, r('assignee_name'), r.v.get('assignee_signer') and 'by ' + r.v['assignee_signer'])}
</div>
"""


_BODIES = {"purchase_agreement": _purchase, "assignment_agreement": _assignment,
           "assignee_disclosure": _disclosure}


def render(kind: str, values: Dict[str, Any], *, for_signature: bool = False) -> str:
    """The whole document as one HTML page. `for_signature` puts DocuSeal
    signature/date boxes where a printed copy has lines."""
    if kind not in KINDS:
        raise KeyError(kind)
    body = _BODIES[kind](_R(values, for_signature))
    title = _h.escape(KINDS[kind]["short"])
    return ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title><style>%s</style></head>"
            "<body>%s</body></html>" % (title, CSS, body))


def document_title(kind: str, values: Dict[str, Any]) -> str:
    return "%s - %s" % (KINDS[kind]["short"], values.get("address") or "property")


def parties(kind: str, values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Who signs, in signing order: the other side first, you last."""
    if kind == "purchase_agreement":
        return [{"role": ROLE_SELLER, "name": values.get("seller_name"), "email": values.get("seller_email")},
                {"role": ROLE_BUYER, "name": values.get("buyer_signer") or values.get("buyer_entity"),
                 "email": values.get("buyer_email")}]
    return [{"role": ROLE_ASSIGNEE, "name": values.get("assignee_signer") or values.get("assignee_name"),
             "email": values.get("assignee_email")},
            {"role": ROLE_ASSIGNOR, "name": values.get("buyer_signer") or values.get("buyer_entity"),
             "email": values.get("buyer_email")}]


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def signer_problems(kind: str, values: Dict[str, Any]) -> List[str]:
    out = []
    for p in parties(kind, values):
        if not p.get("email") or not EMAIL_RE.match(str(p["email"])):
            out.append("%s's email" % p["role"])
    return out


def kits() -> List[Dict[str, Any]]:
    return [{"kind": k, "label": d["label"], "short": d["short"], "when": d["when"], "roles": d["roles"],
             "required": [LABELS[x] for x in d["required"]]} for k, d in KINDS.items()]
