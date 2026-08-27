"""Creating, resolving and revoking a hosted demo mockup.

The public resolve path is deliberately narrow: it takes a token, and it either
returns one demo's title and HTML or it refuses. It never accepts an id, never
lists anything, and never reveals whether a token that failed was wrong,
expired or revoked in a way that would let somebody probe for live ones.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.demo_site_models import DemoSite, mint_token, DEFAULT_TTL_DAYS
from app.models.sales_models import Opportunity

# A demo is a page, not a payload. Well past any real mockup and far short of
# something that would hurt the database.
MAX_HTML_BYTES = 2 * 1024 * 1024


def public_url(base_url: str, token: str) -> str:
    """The link a rep hands to a prospect — on the BRAND's domain.

    Built from the brand's own configured base url rather than a constant, so a
    BookaBoost demo arrives on a BookaBoost address and nothing here hardcodes
    one brand's host.
    """
    return "%s/demo/%s" % ((base_url or "").rstrip("/"), token)


def for_opportunity(db: Session, opportunity_id: str) -> List[DemoSite]:
    return (db.query(DemoSite)
              .filter(DemoSite.opportunity_id == opportunity_id)
              .order_by(DemoSite.created_at.desc()).all())


def current(db: Session, opportunity_id: str) -> Optional[DemoSite]:
    """The live demo for this deal, if there is one."""
    now = datetime.utcnow()
    for d in for_opportunity(db, opportunity_id):
        if d.is_live(now):
            return d
    return None


DEFAULT_SLOT = "platform"
_SLOT_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def normalize_slot(value) -> str:
    """Fail toward the default rather than minting an unbounded slot space.

    A slot is a shelf, not a label. Anything unrecognisable goes on the default
    shelf, where the existing "one live link" behaviour applies unchanged.
    """
    v = (value or "").strip().lower().replace(" ", "_")
    return v if _SLOT_OK.match(v) else DEFAULT_SLOT


def create(db: Session, opp: Opportunity, actor, *, title: str, html: str,
           slot: str = DEFAULT_SLOT,
           ttl_days: int = DEFAULT_TTL_DAYS, now=None) -> Dict[str, Any]:
    """Publish a mockup for this deal and mint its link.

    Replacing a demo REVOKES the previous one IN THE SAME SLOT rather than
    leaving it live. Two working links to two different versions of the same
    pitch is how a prospect ends up looking at the design you already moved on
    from — but a product walkthrough and a website concept are not two versions
    of one pitch, and retiring one because the other shipped would be wrong.
    """
    now = now or datetime.utcnow()
    html = html or ""
    if not html.strip():
        return {"ok": False, "error": "The demo has no content to publish."}
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        return {"ok": False,
                "error": "That page is larger than %d MB. Trim it and try again."
                         % (MAX_HTML_BYTES // (1024 * 1024))}
    if not (title or "").strip():
        return {"ok": False, "error": "Give the demo a title the prospect will see."}

    slot = normalize_slot(slot)
    for old in for_opportunity(db, opp.id):
        if old.is_live(now) and (old.slot or DEFAULT_SLOT) == slot:
            old.revoked_at = now
            old.is_active = False

    row = DemoSite(
        opportunity_id=opp.id,
        brand_sales_org_id=opp.brand_sales_org_id,
        title=title.strip(),
        slot=slot,
        html=html,
        token=mint_token(),
        created_by=getattr(actor, "id", None),
        created_at=now,
    )
    row.expires_at = row.default_expiry(now)
    db.add(row)
    db.flush()
    return {"ok": True, "error": None, "demo": row}


def revoke(db: Session, demo: DemoSite, now=None) -> None:
    now = now or datetime.utcnow()
    demo.revoked_at = now
    demo.is_active = False


def resolve(db: Session, token: str, now=None) -> Optional[DemoSite]:
    """Token -> demo, or None. Records the view as a side effect.

    Returns None for every failure mode alike. Distinguishing "expired" from
    "never existed" in the response would turn this into an oracle for guessing
    live tokens.
    """
    now = now or datetime.utcnow()
    row = db.query(DemoSite).filter(DemoSite.token == token).first()
    if row is None or not row.is_live(now):
        return None
    row.view_count = int(row.view_count or 0) + 1
    if row.first_viewed_at is None:
        row.first_viewed_at = now
    row.last_viewed_at = now
    return row


def out(demo: DemoSite, base_url: str = None) -> Dict[str, Any]:
    """The internal shape. Never carries `html` — a list of demos does not need
    a megabyte of markup per row."""
    return {
        "id": demo.id,
        "title": demo.title,
        "slot": demo.slot or DEFAULT_SLOT,
        "opportunity_id": demo.opportunity_id,
        "url": public_url(base_url, demo.token) if base_url else None,
        "is_live": demo.is_live(),
        "expires_at": demo.expires_at,
        "revoked_at": demo.revoked_at,
        "view_count": int(demo.view_count or 0),
        "first_viewed_at": demo.first_viewed_at,
        "last_viewed_at": demo.last_viewed_at,
        "created_at": demo.created_at,
        "size_kb": round(len((demo.html or "").encode("utf-8")) / 1024.0, 1),
    }
