"""Short booking links that start with the family's own location:

    https://easterngate.evosyspro.live/k7q2x

The location name comes first (a sub-address of the platform domain), then a
5-character code. The link only opens when the name in front belongs to the
family's own location. Switched on by LOCATION_LINK_DOMAIN (e.g.
"evosyspro.live") once *.evosyspro.live points at the backend; until then the
regular booking link is used.
"""
import os
import re
import secrets
from typing import Optional

# Words every location shares; dropping them leaves the part people recognise.
_GENERIC = {"memorial", "gardens", "garden", "funeral", "home", "homes", "cemetery", "chapel",
            "park", "mortuary", "mausoleum", "and", "the", "of", "parlors", "parlor", "care",
            "center", "centre", "west", "east", "mobile"}
_RESERVED = {"app", "links", "www", "api", "mail", "admin", "god"}
# Names the word-dropping rule reads badly, spelled out once.
_NAMED = {
    "memory park cemetery": "memorypark",
    "white chapel-greenwood funeral home": "whitechapel",
    "ridout's-brown-service prattville chapel": "ridoutsprattville",
}
_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"   # no 0/o, 1/l/i look-alikes


def location_key(name: Optional[str], override: Optional[str] = None) -> str:
    if override and re.fullmatch(r"[a-z0-9]{2,40}", override.strip().lower()):
        return override.strip().lower()
    if (name or "").strip().lower() in _NAMED:
        return _NAMED[(name or "").strip().lower()]
    raw = (name or "").lower().replace("&", " ").replace("'", "")
    words = [w for w in re.split(r"[^a-z0-9]+", raw) if w]
    keep = [w for w in words if w not in _GENERIC] or words
    key = "".join(keep)[:40]
    return key if key not in _RESERVED else key + "loc"


def profile_key(prof) -> str:
    return location_key(getattr(prof, "official_name", None), getattr(prof, "link_key", None))


def link_domain() -> str:
    d = (os.environ.get("LOCATION_LINK_DOMAIN") or "").strip().lower().strip(".")
    return d if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d or "") else ""


def new_code(n: int = 5) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def ensure_code(db, link) -> str:
    """The link's short code, minted once (unique among booking links)."""
    if getattr(link, "short_code", None):
        return link.short_code
    from app.models.models import BookingLink
    for _ in range(12):
        code = new_code()
        if not db.query(BookingLink.id).filter(BookingLink.short_code == code).first():
            link.short_code = code
            db.flush()
            return code
    link.short_code = new_code(7)
    db.flush()
    return link.short_code


def short_url(db, prof, link) -> str:
    domain = link_domain()
    if not (domain and prof is not None and link is not None):
        return ""
    return "https://%s.%s/%s" % (profile_key(prof), domain, ensure_code(db, link))


def parse_host(host: str) -> Optional[str]:
    """'easterngate.evosyspro.live' -> 'easterngate' (None for any other host)."""
    domain = link_domain()
    h = (host or "").lower().split(":", 1)[0]
    if not domain or not h.endswith("." + domain):
        return None
    key = h[: -len(domain) - 1]
    if not key or "." in key or key in _RESERVED:
        return None
    return key
