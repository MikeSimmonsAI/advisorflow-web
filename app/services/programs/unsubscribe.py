"""One-click email unsubscribe for program emails.

The token is an HMAC of the lead id under the deployment secret: it names one
lead, cannot be forged, and carries nothing else. Using it records an email
opt-out of record (lead.allow_email = False), which every send path's
compliance preflight already refuses.
"""
import base64
import hashlib
import hmac
import os
from typing import Optional


def _key() -> bytes:
    return ("unsubscribe:" + (os.environ.get("JWT_SECRET") or "")).encode()


def make_token(lead_id: str) -> str:
    sig = hmac.new(_key(), lead_id.encode(), hashlib.sha256).digest()[:16]
    raw = lead_id.encode() + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def read_token(token: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        lead_id, _, sig = raw.rpartition(".")
    except Exception:
        return None
    if not lead_id:
        return None
    good = make_token(lead_id)
    return lead_id if hmac.compare_digest(good, token) else None


def url_for(lead_id: str) -> str:
    base = (os.environ.get("PROGRAM_ASSET_BASE_URL") or "").strip().rstrip("/")
    if not base:
        from app.services.twilio_callbacks import public_api_base
        base = public_api_base()
    return "%s/email/unsubscribe/%s" % (base, make_token(lead_id))


def footer_html(lead_id: str, postal: Optional[str] = None) -> str:
    """Plain, small footer: the sender's postal address (CAN-SPAM requires a
    valid physical address in commercial email) and the one-click opt-out.
    Deliberately unstyled beyond size/colour - heavy footers read as bulk mail."""
    addr = ""
    if postal:
        import html as _h
        addr = "%s<br>" % _h.escape(postal)
    return ('<p style="font-size:12px;color:#6b7f8c;margin-top:24px">%s'
            'Prefer not to receive these emails? '
            '<a href="%s">Unsubscribe</a>.</p>' % (addr, url_for(lead_id)))


def postal_line(db, lead) -> Optional[str]:
    """'<Location>, <street>, <city>, <ST> <zip>' for a program lead whose
    location has a verified address on file; None otherwise (never invented)."""
    try:
        from app.models.location_models import Location
        from app.services.programs import identity
        prof = identity.location_profile_for_lead(db, lead)
        if prof is None:
            return None
        loc = db.query(Location).filter(Location.id == prof.location_id).first()
        return postal_for(prof.official_name, loc)
    except Exception:                                    # noqa: BLE001
        return None


def postal_for(name: str, loc) -> Optional[str]:
    if loc is None or not (loc.address_line1 and loc.city and loc.state):
        return None
    street = ", ".join(x for x in (loc.address_line1, loc.address_line2) if x)
    return "%s, %s, %s, %s %s" % (name, street, loc.city, loc.state, loc.postal_code or "")
