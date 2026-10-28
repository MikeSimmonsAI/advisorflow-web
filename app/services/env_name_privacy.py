"""Server setting NAMES are platform business, not a tenant's (#272).

Provider status screens (the EvoSense provider evaluation screen, Providers &
Controls, the Wholesale settings provider list, the deal-intel provider list)
used to tell a workspace admin which server environment variable was missing
("needs ATTOM_API_KEY"). That names the platform's own configuration to every
customer workspace. Platform admins (God / super admin) still see the names;
everyone else sees that a platform credential is needed, never which one.

`for_viewer(user, payload)` returns the payload unchanged for a platform admin,
otherwise a copy with:
  * every `missing_env` / `env` value emptied (a list becomes [], a string None)
  * every known setting name inside any text replaced by "a platform credential"
Nothing here reads a value; names come from the provider registries.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Set

PLATFORM_ADMIN_ROLES = ("god_admin", "super_admin")
HIDDEN = "a platform credential"
_NAME_KEYS = ("missing_env", "env", "env_names", "required_env")


def is_platform_admin(user) -> bool:
    return getattr(user, "role", None) in PLATFORM_ADMIN_ROLES


def _registry_names() -> Set[str]:
    names: Set[str] = set()

    def take(reg: Optional[dict]):
        for p in (reg or {}).values():
            for n in (getattr(p, "required_env", None) or ()):
                if isinstance(n, str) and n:
                    names.add(n)
    for mod, attr in (("app.services.evosense.providers", "PROVIDERS"),
                      ("app.services.wholesale_enrichment", "PROVIDERS"),
                      ("app.services.wholesale_esign", "PROVIDERS")):
        try:
            module = __import__(mod, fromlist=[attr])
            take(getattr(module, attr, None))
        except Exception:  # noqa: BLE001 - a registry that fails to load hides nothing extra
            continue
    return names


def _collect(payload: Any, out: Set[str]) -> None:
    """Names the payload itself declares (under a name key) - always hidden too."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in _NAME_KEYS:
                if isinstance(v, str) and v:
                    out.add(v)
                elif isinstance(v, (list, tuple)):
                    out.update(x for x in v if isinstance(x, str) and x)
            else:
                _collect(v, out)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _collect(v, out)


def hide(payload: Any, extra: Iterable[str] = ()) -> Any:
    names = _registry_names()
    names.update(extra)
    _collect(payload, names)
    ordered = sorted((n for n in names if len(n) >= 4), key=len, reverse=True)

    def walk(v):
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if k in _NAME_KEYS:
                    out[k] = [] if isinstance(x, (list, tuple)) else None
                else:
                    out[k] = walk(x)
            return out
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, tuple):
            return tuple(walk(x) for x in v)
        if isinstance(v, str):
            for n in ordered:
                if n in v:
                    v = v.replace(n, HIDDEN)
            return v
        return v
    return walk(payload)


def for_viewer(user, payload: Any) -> Any:
    return payload if is_platform_admin(user) else hide(payload)
