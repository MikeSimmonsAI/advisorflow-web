"""Where an unauthenticated public lead is allowed to land.

WHAT THIS REPLACES, AND WHY IT MATTERED. The public demo-request endpoint chose
its destination like this:

    org = db.query(Organization).filter(
        Organization.name.ilike('%bookaboost%')).first()
    if not org:
        org = db.query(Organization).first()

Two separate defects in four lines. The `ilike` made a customer-facing routing
decision out of a display name, so renaming a brand silently redirects public
leads and any organization whose name happens to contain the string can capture
them. The fallback then took WHATEVER ROW CAME BACK FIRST - an arbitrary paying
customer's workspace - and wrote a stranger's name, phone number and email into
it. Nobody would have seen that happen; the form would have said "thanks".

THE RULE. A public intake path never GUESSES a customer. It resolves an
explicitly configured destination or it refuses. There is no ordering by
convenience, no name matching, no "first active organization", and no falling
back to the platform's own workspace.

THE ORDER, and why each step is allowed to be authoritative:

  1. An explicit `platform_slug` in the payload. The caller names the brand; the
     configuration still decides where that brand's leads go, so naming a brand
     grants nothing beyond what an operator already configured for it.
  2. The request Origin/Referer host, matched EXACTLY against the platform's own
     configured `website_url` or `domain`. Origin is spoofable, which is exactly
     why it only selects among destinations an operator configured - it can move
     a lead between two of Mike's own brands and nowhere else. Matching is on the
     parsed host and is never a substring test.
  3. Exactly one platform has an intake destination configured. Unambiguous by
     construction: there is only one answer, so choosing it is not a guess. The
     moment a second brand is configured this step stops applying and the caller
     must say which brand it is - the ambiguity surfaces as a refusal rather
     than as a lead in the wrong workspace.
  4. Refuse.

A configured destination is also VERIFIED, not trusted: the organization must
exist, be active, and belong to the platform that named it. A stale id left
behind by a deleted organization refuses like an unset one.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.models import Organization, Platform

log = logging.getLogger(__name__)


class IntakeDestinationError(Exception):
    """No configured destination could be resolved. Carries an operator-facing
    reason; the public caller never sees it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _host(value: Optional[str]) -> str:
    """Host of a URL or bare hostname, lowercased, without a port or www."""
    if not value:
        return ""
    raw = value.strip().lower()
    if "//" not in raw:
        raw = "//" + raw
    host = (urlparse(raw).hostname or "").strip()
    return host[4:] if host.startswith("www.") else host


def _configured_platforms(db: Session):
    return (db.query(Platform)
            .filter(Platform.public_intake_organization_id.isnot(None))
            .order_by(Platform.slug)
            .all())


def _verify(db: Session, platform: Platform) -> Organization:
    """The configured organization, or a refusal. Never a substitute."""
    org_id = platform.public_intake_organization_id
    if not org_id:
        raise IntakeDestinationError(
            "platform %r has no public intake organization configured" % platform.slug)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise IntakeDestinationError(
            "platform %r names intake organization %s, which does not exist"
            % (platform.slug, org_id))
    if org.platform_id != platform.id:
        # A destination belonging to another brand is a misconfiguration that
        # would cross a brand boundary on every public submission.
        raise IntakeDestinationError(
            "intake organization %s does not belong to platform %r"
            % (org_id, platform.slug))
    if getattr(org, "is_active", True) is False:
        raise IntakeDestinationError(
            "intake organization %s for platform %r is not active"
            % (org_id, platform.slug))
    return org


def resolve_public_intake(db: Session,
                          *,
                          platform_slug: Optional[str] = None,
                          origin: Optional[str] = None
                          ) -> Tuple[Platform, Organization]:
    """(platform, organization) for a public submission, or raise.

    Raising is a supported outcome and the caller must treat it as one. There is
    no return value meaning "we could not tell, so here is a guess".
    """
    # 1. The caller named a brand.
    if platform_slug:
        platform = (db.query(Platform)
                    .filter(Platform.slug == platform_slug.strip().lower())
                    .first())
        if platform is None:
            raise IntakeDestinationError(
                "no platform with slug %r" % platform_slug)
        return platform, _verify(db, platform)

    configured = _configured_platforms(db)

    # 2. The browser said where it came from, matched exactly against a
    #    configured host.
    origin_host = _host(origin)
    if origin_host:
        for platform in configured:
            for candidate in (platform.website_url, platform.domain):
                if candidate and _host(candidate) == origin_host:
                    return platform, _verify(db, platform)

    # 3. Exactly one destination is configured, so there is nothing to choose
    #    between.
    if len(configured) == 1:
        platform = configured[0]
        return platform, _verify(db, platform)

    if not configured:
        raise IntakeDestinationError(
            "no platform has a public intake organization configured")
    raise IntakeDestinationError(
        "%d platforms have intake configured and the request named none of them "
        "(origin=%r)" % (len(configured), origin_host or "")
    )
