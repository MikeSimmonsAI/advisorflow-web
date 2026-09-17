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

    Built from the brand's own configured base url rather than a constant, so
    each brand's demo arrives on that brand's own address and nothing here
    hardcodes one brand's host. The example used to name a real brand, which is
    a customer's name sitting in platform machinery - an example is how the
    next literal gets in.
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


_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$")

# Names the demo router already owns under /demo. A slug that shadowed one of
# these would be unreachable at best and would hijack a real screen at worst.
_RESERVED_SLUGS = frozenset({
    "environment", "scenarios", "state", "seed", "advance", "reset",
    "suite", "preview", "new", "edit", "admin", "api", "static", "assets",
})


def normalize_slug(value):
    """A vanity name, or None. Never a guess.

    Refuses rather than mangles: `slugify`-ing whatever arrives is how a demo
    ends up at an address nobody expected and the rep reads the wrong URL down
    the phone. An invalid slug is an error the caller sees, not a silent
    substitution.
    """
    if value is None:
        return None
    v = str(value).strip().lower().replace(" ", "-").replace("_", "-")
    while "--" in v:
        v = v.replace("--", "-")
    if not v:
        return None
    if v in _RESERVED_SLUGS:
        raise ValueError(
            "'%s' is reserved by the demo routes and cannot be used as a link "
            "name." % v)
    if not _SLUG_OK.match(v):
        raise ValueError(
            "Link names are 3-48 characters, lowercase letters, numbers and "
            "hyphens, starting and ending with a letter or number.")
    return v


def brand_for_request_host(db: Session, host):
    """The brand serving this hostname, or None.

    Used to scope a vanity-name lookup. Returns None rather than guessing when
    the host is unrecognised, and a None scope means the name is resolved
    across brands - which is correct today, because a slug unique to one brand
    is still unique overall. The parameter exists so that stays true as brands
    multiply rather than needing a migration then.
    """
    if not host:
        return None
    try:
        from app.services import brand_config
        from app.models.sales_models import BrandSalesOrg
        cfg = brand_config.config_for_host(db, host)
        slug = (cfg or {}).get("theme_slug") or (cfg or {}).get("slug")
        if not slug:
            return None
        from app.models.models import Platform
        platform = db.query(Platform).filter(Platform.slug == slug).first()
        if platform is None:
            return None
        row = (db.query(BrandSalesOrg)
               .filter(BrandSalesOrg.platform_id == platform.id).first())
        return getattr(row, "id", None)
    except Exception:                                            # noqa: BLE001
        return None


def slug_owner(db: Session, brand_sales_org_id: str, slug: str, exclude_id=None):
    """The demo currently holding this name for this brand, if any."""
    q = db.query(DemoSite).filter(
        DemoSite.brand_sales_org_id == brand_sales_org_id,
        DemoSite.slug == slug)
    if exclude_id:
        q = q.filter(DemoSite.id != exclude_id)
    return q.first()


def create(db: Session, opp: Opportunity, actor, *, title: str, html: str,
           slot: str = DEFAULT_SLOT, retire_previous: bool = False,
           ttl_days: int = DEFAULT_TTL_DAYS, slug=None, now=None) -> Dict[str, Any]:
    """Publish a mockup for this deal and mint its link.

    A previous version in the same slot is LEFT LIVE by default. A link that
    has been sent to a prospect, put in a proposal, or pasted into a message
    keeps working until somebody decides it should not — killing it silently
    because a newer draft was published is how a prospect opens a dead page in
    front of you.

    Pass `retire_previous=True` to close the older link at the moment the new
    one is minted, which is the right call once a version is genuinely
    superseded and you do not want the old one seen again. Either way, only the
    SAME slot is affected: a product walkthrough and a website concept are not
    two versions of one pitch.

    `opp.demo_url` always follows the newest platform demo, so "this deal's
    demo" means the current one even while older links still open.
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
    if retire_previous:
        for old in for_opportunity(db, opp.id):
            if old.is_live(now) and (old.slot or DEFAULT_SLOT) == slot:
                old.revoked_at = now
                old.is_active = False

    # THE VANITY NAME MOVES TO THE NEW VERSION. THE TOKEN DOES NOT.
    #
    # Republishing always inserts a new row with a new secret, which is why
    # "the link changes every time you republish" was true and why a name that
    # a rep has already said out loud could not survive an edit. A slug is
    # transferred off whichever row in this brand currently holds it, so
    # /demo/countryside keeps opening the current version while every
    # historical token still opens its own historical HTML.
    try:
        slug = normalize_slug(slug)
    except ValueError as bad:
        return {"ok": False, "error": str(bad)}

    if slug:
        holder = slug_owner(db, opp.brand_sales_org_id, slug)
        if holder is not None and holder.opportunity_id != opp.id:
            return {"ok": False,
                    "error": "The link name '%s' is already in use by another "
                             "deal for this brand." % slug}
        if holder is not None:
            holder.slug = None
            db.flush()

    row = DemoSite(
        opportunity_id=opp.id,
        brand_sales_org_id=opp.brand_sales_org_id,
        title=title.strip(),
        slot=slot,
        html=html,
        token=mint_token(),
        slug=slug,
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
    # A retired name becomes available again, and a dead vanity link resolves
    # to the same neutral nothing as a dead token. Leaving the slug on a
    # revoked row would hold a readable name hostage to a dead deal.
    demo.slug = None


def resolve(db: Session, token: str, now=None, brand_sales_org_id=None) -> Optional[DemoSite]:
    """Token OR vanity slug -> demo, or None. Records the view as a side effect.

    THE TOKEN IS TRIED FIRST AND ITS BEHAVIOUR IS BYTE-FOR-BYTE UNCHANGED, so
    every link already sent to a prospect resolves exactly as it did before
    slugs existed. Only a string that is not a live token is considered as a
    name.

    `brand_sales_org_id`, when the caller can resolve one from the request
    host, scopes the slug lookup to that brand - which is what makes the same
    readable name safe for two brands to use. Without it a slug still resolves,
    because a name unique to one brand is still unique overall today; the
    parameter is how that stays true as brands multiply.

    Returns None for every failure mode alike. Distinguishing "expired" from
    "never existed" would turn this into an oracle for guessing live links -
    which matters more now, because a short readable name is guessable in a way
    a 43-character secret is not.
    """
    now = now or datetime.utcnow()
    row = db.query(DemoSite).filter(DemoSite.token == token).first()
    if row is None:
        try:
            slug = normalize_slug(token)
        except ValueError:
            slug = None
        if slug:
            q = db.query(DemoSite).filter(DemoSite.slug == slug)
            if brand_sales_org_id:
                q = q.filter(DemoSite.brand_sales_org_id == brand_sales_org_id)
            row = q.first()
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
        # The vanity name where there is one, because that is the link a rep
        # reads down a phone - and the token URL beside it, always, because
        # that one never changes and is what is already in sent proposals.
        "slug": demo.slug,
        "url": (public_url(base_url, demo.slug or demo.token)
                if base_url else None),
        "token_url": public_url(base_url, demo.token) if base_url else None,
        "is_live": demo.is_live(),
        "expires_at": demo.expires_at,
        "revoked_at": demo.revoked_at,
        "view_count": int(demo.view_count or 0),
        "first_viewed_at": demo.first_viewed_at,
        "last_viewed_at": demo.last_viewed_at,
        "created_at": demo.created_at,
        "size_kb": round(len((demo.html or "").encode("utf-8")) / 1024.0, 1),
    }
