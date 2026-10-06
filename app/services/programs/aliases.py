"""Location email aliases: one address per location, one central inbox.

    easterngategardens@evosyspro.live  ->  the central monitored mailbox
                                           (e.g. support@evosyspro.live)
                                           ->  EvoSys inbound reader
                                           ->  the family's Contact / Lead,
                                               tagged with the location

NOT 39 MAILBOXES. Each alias is an extra address ON the central mailbox
(Microsoft 365 "email alias" / proxy address). Nothing here creates mail
infrastructure; scripts/m365_location_aliases.ps1 adds them in Microsoft 365.

FROM OR REPLY-TO - decided per send, never weakening authentication:
  * Resend is verified for the whole sending domain (DKIM d=<domain>, SPF via
    its return-path subdomain), so an alias ON THAT SAME DOMAIN as the From is
    DKIM- and SPF-aligned for DMARC. auth_ok() checks exactly that: the
    alias's domain equals the domain of the verified address we send from.
    Any other domain -> the verified sender stays the From and the alias is
    only the Reply-To.
  * Either way an alias is used ONLY once it RECEIVES mail. An alias that
    does not exist in Microsoft 365 yet would bounce every reply, so until it
    is seen arriving in the central mailbox (alias_verified_at) or a person
    confirms the aliases were created (aliases_receiving_confirmed_at), the
    send goes out exactly as before: verified From, "Kerry Allan | <Location>"
    display name, replies to the verified address - still routed to the right
    family by the sender's address.
"""
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.program_models import LocationProfile, OutreachProgram

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

MODE_FROM, MODE_REPLY_TO, MODE_NONE = "from", "reply_to", "none"


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


def sending_address(db: Session, org_id: str) -> Optional[str]:
    from app.services.public_identity import sending_identity_for_org
    try:
        return getattr(sending_identity_for_org(db, org_id), "from_email", None)
    except Exception:                                    # noqa: BLE001
        return None


def alias_domain(db: Session, prog: OutreachProgram) -> Optional[str]:
    return (prog.alias_domain or "").strip().lower() or domain_of(sending_address(db, prog.organization_id))


def validate_local(local: str) -> Optional[str]:
    """None when acceptable, else the reason."""
    local = (local or "").strip().lower()
    if not LOCAL_RE.match(local) or ".." in local:
        return "use lower-case letters, digits, dots or hyphens"
    if local in RESERVED:
        return "%s is reserved" % local
    return None


def taken(db: Session, address: str, except_profile_id: Optional[str] = None) -> bool:
    q = db.query(LocationProfile.id).filter(func.lower(LocationProfile.email_alias) == address.lower())
    if except_profile_id:
        q = q.filter(LocationProfile.id != except_profile_id)
    return q.first() is not None


def assign(db: Session, prog: OutreachProgram, *, overwrite: bool = False) -> Dict[str, str]:
    """Give every real location an alias (never the review bucket). Keeps
    aliases already set unless overwrite. Returns official name -> alias."""
    domain = alias_domain(db, prog)
    if not domain:
        raise ValueError("No alias domain: the program has no verified sending address yet.")
    profs = (db.query(LocationProfile)
             .filter(LocationProfile.organization_id == prog.organization_id,
                     LocationProfile.is_review_bucket.is_(False)).all())
    wanted = slugs_for([p.official_name for p in profs])
    out = {}
    for p in sorted(profs, key=lambda x: x.official_name):
        if p.email_alias and not overwrite:
            out[p.official_name] = p.email_alias
            continue
        local = wanted[p.official_name]
        if validate_local(local):
            local = "loc-" + re.sub(r"[^a-z0-9]", "", local)[:50]
        addr, n = "%s@%s" % (local, domain), 2
        while taken(db, addr, p.id):
            addr, n = "%s%d@%s" % (local, n, domain), n + 1
        if p.email_alias != addr:
            p.email_alias, p.alias_verified_at = addr, None
        out[p.official_name] = addr
    db.flush()
    return out


def receiving(prog: OutreachProgram, prof: LocationProfile) -> bool:
    return bool(prof is not None and prof.email_alias
                and (prof.alias_verified_at or prog.aliases_receiving_confirmed_at))


def auth_ok(alias: Optional[str], verified_from: Optional[str]) -> bool:
    """The alias may be the From only on the verified sending domain itself."""
    a, f = domain_of(alias), domain_of(verified_from)
    return bool(a and f and a == f)


def effective_mode(prog: OutreachProgram, prof: Optional[LocationProfile],
                   verified_from: Optional[str]) -> str:
    if prog is None or prof is None or (prog.alias_mode or "from") == "off" or not receiving(prog, prof):
        return MODE_NONE
    if (prog.alias_mode or "from") == "from" and auth_ok(prof.email_alias, verified_from):
        return MODE_FROM
    return MODE_REPLY_TO


def apply(prog: OutreachProgram, prof: Optional[LocationProfile], ident) -> str:
    """Put the alias on a resolved SendingIdentity per effective_mode."""
    if ident is None:
        return MODE_NONE
    mode = effective_mode(prog, prof, getattr(ident, "from_email", None))
    if mode == MODE_FROM:
        ident.from_email = prof.email_alias
        ident.reply_to_email = prof.email_alias
    elif mode == MODE_REPLY_TO:
        ident.reply_to_email = prof.email_alias
    return mode


# ── inbound ─────────────────────────────────────────────────────────────────

def _candidates(address: str) -> List[str]:
    """The address itself, plus the plus-address form (central+slug@domain)."""
    a = (address or "").strip().lower()
    out = [a] if a else []
    if "@" in a:
        local, domain = a.rsplit("@", 1)
        if "+" in local:
            out.append("%s@%s" % (local.split("+", 1)[1], domain))
    return out


def resolve(db: Session, addresses: Iterable[str]) -> Optional[LocationProfile]:
    """The location whose alias one of these recipient addresses is, or None."""
    wanted = []
    for a in addresses or []:
        wanted.extend(_candidates(a))
    if not wanted:
        return None
    return (db.query(LocationProfile)
            .filter(func.lower(LocationProfile.email_alias).in_(wanted),
                    LocationProfile.is_review_bucket.is_(False)).first())


def mark_verified(prof: LocationProfile, now: Optional[datetime] = None) -> None:
    if prof is None:
        return
    now = now or datetime.utcnow()
    if prof.alias_verified_at is None:
        prof.alias_verified_at = now
    prof.alias_last_seen_at = now


def status(db: Session, prog: OutreachProgram) -> Dict:
    profs = (db.query(LocationProfile)
             .filter(LocationProfile.organization_id == prog.organization_id,
                     LocationProfile.is_review_bucket.is_(False)).all())
    frm = sending_address(db, prog.organization_id)
    assigned = [p for p in profs if p.email_alias]
    seen = [p for p in assigned if p.alias_verified_at]
    modes = defaultdict(int)
    for p in profs:
        modes[effective_mode(prog, p, frm)] += 1
    return {
        "locations": len(profs), "assigned": len(assigned), "seen_receiving": len(seen),
        "confirmed_all": bool(prog.aliases_receiving_confirmed_at),
        "domain": alias_domain(db, prog), "verified_from": frm,
        "auth_ok": bool(assigned) and all(auth_ok(p.email_alias, frm) for p in assigned),
        "configured_mode": prog.alias_mode or "from", "effective": dict(modes),
    }
