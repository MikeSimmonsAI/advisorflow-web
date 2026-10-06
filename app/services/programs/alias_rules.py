"""Pure alias rules: location name -> email local part, address validation.
Stdlib only; shared by aliases.py and the readiness harness."""
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

# Generic place words, longest first: removed from the slug, and used only to
# tell two locations with the same proper name apart (cemetery vs funeral home).
_GENERIC = [
    (r"memorial funeral home", "funeralhome"), (r"funeral home", "funeralhome"),
    (r"funeral parlors", "funeralhome"), (r"memorial gardens", "gardens"),
    (r"memorial park", "park"), (r"cemetery & mausoleum", "cemetery"),
    (r"cemetery", "cemetery"), (r"mortuary", "mortuary"), (r"care center", ""),
]
RESERVED = {"support", "admin", "administrator", "postmaster", "abuse", "noreply", "no-reply",
            "info", "hostmaster", "webmaster", "security", "billing", "sales", "help", "mail",
            "root", "dmarc", "bounce", "unsubscribe", "privacy", "legal"}
LOCAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")



def _parts(name: str) -> Tuple[str, List[str]]:
    n = (name or "").lower().replace("’", "'").replace("'", "")
    n = re.sub(r"[()]", " ", n)
    types: List[str] = []
    for pat, tok in _GENERIC:
        if re.search(r"\b" + pat + r"\b", n):
            n = re.sub(r"\b" + pat + r"\b", " ", n)
            if tok:
                types.append(tok)
    n = re.sub(r"\s+", " ", n).strip(" -")
    if not types and re.search(r"\bchapel$", n):
        n, types = re.sub(r"\s*\bchapel$", "", n), ["chapel"]
    elif not types and re.search(r"\bgardens$", n):
        n, types = re.sub(r"\s*\bgardens$", "", n), ["gardens"]
    return re.sub(r"[^a-z0-9]", "", n), types


def slugs_for(names: Iterable[str]) -> Dict[str, str]:
    """name -> local part. Deterministic; twins keep their type word."""
    groups = defaultdict(list)
    for nm in names:
        base, types = _parts(nm)
        groups[base].append((nm, types))
    out = {}
    for base, members in groups.items():
        for nm, types in members:
            slug = base if len(members) == 1 else base + "".join(types or ["main"])
            out[nm] = (slug or "location")[:60]
    return out


def domain_of(address: Optional[str]) -> Optional[str]:
    if not address or "@" not in address:
        return None
    return address.rsplit("@", 1)[1].strip().lower() or None


def validate_local(local: str) -> Optional[str]:
    """None when acceptable, else the reason."""
    local = (local or "").strip().lower()
    if not LOCAL_RE.match(local) or ".." in local:
        return "use lower-case letters, digits, dots or hyphens"
    if local in RESERVED:
        return "%s is reserved" % local
    return None
