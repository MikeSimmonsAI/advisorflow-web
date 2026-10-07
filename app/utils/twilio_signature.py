"""Pure Twilio signature primitives: stdlib only, no FastAPI, no env, no I/O.

app/utils/twilio_security.py delegates here, and the staging webhook
simulation (app/services/programs/webhook_simulation.py) calls the same
functions, so there is one signing algorithm and one candidate-URL rule.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Iterable, List, Mapping, Optional


def compute_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """HMAC-SHA1(auth_token, url + sorted(k+v)), base64. Twilio's algorithm."""
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode("utf-8"), (url + sorted_params).encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("utf-8")


def candidate_urls(path: str, query: str, headers: Mapping[str, str],
                   configured_base: Optional[str], full_url: str) -> List[str]:
    """Every URL string Twilio could plausibly have signed, from server-side trust only."""
    suffix = path + (("?" + query) if query else "")
    seen, out = set(), []

    def add(u):
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    if proto and host:
        add("%s://%s%s" % (proto, host, suffix))
    if host:
        add("https://%s%s" % (host, suffix))
    if configured_base:
        add(configured_base.rstrip("/") + suffix)
    add(full_url)
    return out


def signature_matches(auth_token: str, candidates: Iterable[str],
                      params: Mapping[str, str], presented: str) -> bool:
    """True only if a non-empty token and signature verify against some candidate URL."""
    if not auth_token or not presented:
        return False
    return any(hmac.compare_digest(compute_signature(auth_token, c, params), presented)
               for c in candidates)
