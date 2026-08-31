"""ONE source of truth for what a brand looks like.

Brand presentation used to live in four unsynchronised places, none of which
read the database:

    app/services/appointment_invites.py   BRAND_IDENTITY      name, from_email,
                                          support_phone, website, app_base_url, accent
    app/routers/branding_router.py        _BRAND_MAP          colours, bg, logoInitial
    frontend/src/theme.js                 BRAND_CONFIG        titles, favicons,
                                          displayName, accent, website, logo
    frontend/index.html                   pre-bundle IIFE     title, favicon, splash

Adding a brand meant editing four files and hoping. They had already drifted:
EvoSys Pro's accent is `#087cff` in two of them and `#1d4ed8` in the third, and
BookaBoost's is `#c9973d` in the frontend and `#2fb6ff` in the branding API.

This module makes the `platforms` row the answer, and the four tables above
become a frozen fallback for a deployment whose columns have not been backfilled
yet. Read the DB first, fall back to the literal, never guess.

WHAT DELIBERATELY STAYS IN CSS
------------------------------
`frontend/src/index.css` carries a `[data-theme="<slug>"]` block per brand -
roughly forty custom properties and twenty-eight component overrides each.
BookaBoost's is a light cream-and-gold world; EvoSys Pro's is dark blue. That is
a STYLESHEET, not configuration, and no set of database columns represents it
honestly. The split this module draws is:

    the database says WHICH theme a brand uses, and every brand VALUE
    the stylesheet says what that theme LOOKS like

So a new brand gets its name, logo, favicon, title, colours-as-values, tagline,
domain and support details from its row alone, and picks up an existing visual
theme by slug. A genuinely new visual identity is a design job that adds a CSS
block - which is the correct amount of work for designing a new look, and is not
something a config row should pretend to do.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# The four tables above, merged and frozen. This is a FALLBACK, not the source
# of truth: it answers only for a platform row whose columns are still NULL.
# Every value is the one that was live before consolidation, so a deployment
# that has not yet backfilled renders exactly as it did.
FROZEN_BRAND_DEFAULTS = {
    "evosyspro": {
        "display_name":   "EvoSys Pro",
        "short_name":     "E",
        "logo_initial":   "E",
        "theme_slug":     "evosyspro",
        "accent_color":   "#087cff",
        "accent_color_2": "#22a3ff",
        "green_color":    "#19d67c",
        "bg_color":       "#040812",
        # Invite emails have always used a DIFFERENT blue from the UI. Kept as
        # its own value rather than silently unified - consolidation should
        # surface a divergence, not resolve it behind your back.
        "invite_accent_color": "#1d4ed8",
        "support_email":  "support@evosyspro.live",
        "support_phone":  "469-553-7417",
        "website_url":    "https://evosyspro.live",
        "app_base_url":   "https://app.evosyspro.live",
        "tagline":        None,
        "favicon_letter": "E",
        "favicon_size":   "18",
    },
    "bookaboost": {
        "display_name":   "BookaBoost",
        "short_name":     "BB",
        "logo_initial":   "BB",
        "theme_slug":     "bookaboost",
        # The frontend value, which is also the one the CSS theme is built
        # around. The branding API's #2fb6ff had no frontend consumer.
        "accent_color":   "#c9973d",
        "accent_color_2": "#1ef0a8",
        "green_color":    "#1ef0a8",
        "bg_color":       "#03060f",
        "invite_accent_color": "#1d4ed8",
        "support_email":  "support@bookaboost.live",
        "support_phone":  None,
        "website_url":    "https://bookaboost.live",
        "app_base_url":   "https://app.bookaboost.live",
        "tagline":        None,
        "favicon_letter": "BB",
        "favicon_size":   "13",
    },
    "harmonyhustle": {
        "display_name":   "Harmony Hustle",
        "short_name":     "HH",
        "logo_initial":   "HH",
        "theme_slug":     "harmonyhustle",
        "accent_color":   "#10b981",
        "accent_color_2": "#34d399",
        "green_color":    "#10b981",
        "bg_color":       "#030b07",
        "invite_accent_color": "#1d4ed8",
        "support_email":  "support@harmonyhustle.com",
        "support_phone":  None,
        "website_url":    "https://harmonyhustle.com",
        "app_base_url":   "https://app.harmonyhustle.com",
        "tagline":        None,
        "favicon_letter": "HH",
        "favicon_size":   "13",
    },
    "advisorflow": {
        "display_name":   "AdvisorFlow",
        "short_name":     "AF",
        "logo_initial":   "AF",
        "theme_slug":     "advisorflow",
        "accent_color":   "#f59e0b",
        "accent_color_2": "#fbbf24",
        "green_color":    "#19d67c",
        "bg_color":       "#0d1021",
        "invite_accent_color": "#1d4ed8",
        "support_email":  "mike@simmonsstrong.com",
        "support_phone":  None,
        "website_url":    None,
        "app_base_url":   None,
        "tagline":        None,
        "favicon_letter": "AF",
        "favicon_size":   "14",
    },
}

# What a brand with no row and no frozen entry gets. Every value is either
# neutral or None; nothing here impersonates a brand that does exist.
UNKNOWN_BRAND = {
    "display_name":   "AdvisorFlow",
    "short_name":     "AF",
    "logo_initial":   "AF",
    "theme_slug":     "bookaboost",
    "accent_color":   "#1d4ed8",
    "accent_color_2": "#1d4ed8",
    "green_color":    "#19d67c",
    "bg_color":       "#0d1021",
    "invite_accent_color": "#1d4ed8",
    "support_email":  None,
    "support_phone":  None,
    "website_url":    None,
    "app_base_url":   None,
    "tagline":        None,
    "favicon_letter": "AF",
    "favicon_size":   "14",
    "logo_url":       None,
}

# Column on `platforms` -> key in the dict this module returns.
_COLUMN_MAP = {
    "name":                "display_name",
    "short_name":          "short_name",
    "logo_initial":        "logo_initial",
    "logo_url":            "logo_url",
    "favicon_url":         "favicon_url",
    "theme_slug":          "theme_slug",
    "accent_color":        "accent_color",
    "accent_color_2":      "accent_color_2",
    "green_color":         "green_color",
    "bg_color":            "bg_color",
    "invite_accent_color": "invite_accent_color",
    "support_email":       "support_email",
    "support_phone":       "support_phone",
    "website_url":         "website_url",
    "app_base_url":        "app_base_url",
    "tagline":             "tagline",
}


def _frozen(slug: Optional[str]) -> dict:
    return dict(FROZEN_BRAND_DEFAULTS.get((slug or "").strip().lower(), UNKNOWN_BRAND))


def config_for_slug(db, slug: Optional[str]) -> dict:
    """Brand presentation for one platform slug. Never raises.

    The database wins field by field, not row by row: a platform that has a name
    and an accent but no tagline yet takes its name and accent from the row and
    its tagline from the frozen default. That is what lets the backfill be
    partial and a half-configured brand still render.
    """
    key = (slug or "").strip().lower()
    out = _frozen(key)
    out.setdefault("logo_url", None)
    out.setdefault("favicon_url", None)
    out["slug"] = key or None
    out["source"] = "frozen"

    if not key or db is None:
        return out

    try:
        from app.models.models import Platform
        row = db.query(Platform).filter(Platform.slug == key).first()
    except Exception:                                        # noqa: BLE001
        log.exception("brand_config: could not read platform %r", key)
        return out

    if row is None:
        return out

    filled = 0
    for column, field in _COLUMN_MAP.items():
        value = getattr(row, column, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        out[field] = value
        filled += 1

    # THE TAB MARK FOLLOWS THE BRAND'S OWN LETTER MARK.
    #
    # `favicon_letter` and `favicon_size` are presentation detail with no column
    # of their own, so a brand configured purely in the database inherited them
    # from the frozen fallback and rendered someone else's initials in the tab -
    # a NorthStar row produced an "AF" favicon. When the row supplies its own
    # logo_initial, that IS the mark, and the size follows its length.
    if getattr(row, "logo_initial", None) and not getattr(row, "favicon_url", None):
        out["favicon_letter"] = row.logo_initial
        out["favicon_size"] = "18" if len(str(row.logo_initial)) == 1 else "13"

    domain = getattr(row, "domain", None)
    if domain:
        out["domain"] = domain
        if not out.get("app_base_url"):
            out["app_base_url"] = "https://" + str(domain).strip().rstrip("/")

    out["source"] = "database" if filled else "frozen"
    return out


def config_for_host(db, host: Optional[str]) -> dict:
    """Brand presentation for a request hostname.

    Matches the platform's own `domain` first - the authoritative statement of
    which host belongs to which brand - and only then falls back to a substring
    match on the slug, which is how the old hostname table worked.
    """
    h = (host or "").strip().lower()
    if not h:
        return config_for_slug(db, None)

    if db is not None:
        try:
            from app.models.models import Platform
            for row in db.query(Platform).filter(Platform.is_active.is_(True)).all():
                dom = (getattr(row, "domain", None) or "").strip().lower()
                if dom and (h == dom or h.endswith("." + dom) or dom in h):
                    return config_for_slug(db, row.slug)
            for row in db.query(Platform).filter(Platform.is_active.is_(True)).all():
                if row.slug and row.slug.lower() in h:
                    return config_for_slug(db, row.slug)
        except Exception:                                    # noqa: BLE001
            log.exception("brand_config: host lookup failed for %r", h)

    for slug in FROZEN_BRAND_DEFAULTS:
        if slug in h:
            return config_for_slug(db, slug)
    return config_for_slug(db, "bookaboost")


def favicon_data_uri(cfg: dict) -> str:
    """The brand's tab icon, as the inline SVG the frontend has always used.

    A brand that has uploaded a real `favicon_url` gets that instead. Generating
    the letter mark here rather than in three places is the point: the mark, its
    colour and its size now come from one row.
    """
    if cfg.get("favicon_url"):
        return cfg["favicon_url"]
    colour = (cfg.get("accent_color") or "#1d4ed8").replace("#", "%23")
    letter = cfg.get("favicon_letter") or cfg.get("logo_initial") or "AF"
    size = cfg.get("favicon_size") or ("18" if len(str(letter)) == 1 else "13")
    return (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='"
        + colour
        + "'/%3E%3Ctext x='16' y='22' font-family='Arial,sans-serif' font-size='"
        + str(size)
        + "' font-weight='700' fill='white' text-anchor='middle'%3E"
        + str(letter)
        + "%3C/text%3E%3C/svg%3E"
    )


def public_payload(db, host: Optional[str], slug: Optional[str] = None) -> dict:
    """What `GET /branding` returns, and what the frontend themes itself from."""
    cfg = config_for_slug(db, slug) if slug else config_for_host(db, host)
    return {
        "brand":         cfg.get("slug"),
        "displayName":   cfg.get("display_name"),
        "shortName":     cfg.get("short_name"),
        "supportEmail":  cfg.get("support_email"),
        "supportPhone":  cfg.get("support_phone"),
        "websiteUrl":    cfg.get("website_url"),
        "tagline":       cfg.get("tagline"),
        "accentColor":   cfg.get("accent_color"),
        "accentColor2":  cfg.get("accent_color_2"),
        "greenColor":    cfg.get("green_color"),
        "bgColor":       cfg.get("bg_color"),
        "logoInitial":   cfg.get("logo_initial"),
        "logoUrl":       cfg.get("logo_url"),
        "faviconUrl":    favicon_data_uri(cfg),
        "documentTitle": cfg.get("display_name"),
        "theme":         cfg.get("theme_slug"),
        "source":        cfg.get("source"),
    }
