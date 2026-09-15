"""Best-effort emails for public website demo requests.

The public capture layer owns the lead record. This module is deliberately
notification-only: it runs after a lead has been persisted, uses the existing
email provider adapter, and never raises back into the request path.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead, Platform

log = logging.getLogger(__name__)


@dataclass
class DemoNotificationResult:
    internal_sent: bool = False
    customer_sent: bool = False
    internal_error: Optional[str] = None
    customer_error: Optional[str] = None


class DemoSendingIdentity(object):
    """Duck-type for email_service.send_email_via_provider(org=...).

    Demo request notices are brand-level mail, like support acknowledgements:
    they should come from the platform's public support address, not from the
    destination customer's own sending domain.
    """

    __slots__ = ("from_email", "reply_to_email", "cc_email", "resend_api_key",
                 "resolved", "platform_id", "from_name", "audit_bcc_email")

    def __init__(self, *, from_email: Optional[str], platform_id: Optional[str],
                 from_name: Optional[str]):
        self.from_email = from_email
        self.reply_to_email = from_email
        self.cc_email = None
        self.resend_api_key = None
        self.resolved = True
        self.platform_id = platform_id
        self.from_name = from_name
        self.audit_bcc_email = None


def _brand(db: Session, platform: Platform) -> Dict[str, Optional[str]]:
    cfg: Dict[str, Any] = {}
    try:
        from app.services import support_branding
        cfg = support_branding.brand_for_platform(db, getattr(platform, "id", None))
    except Exception:  # noqa: BLE001
        log.exception("public demo notification brand lookup failed for %s",
                      getattr(platform, "id", None))
    return {
        "name": (cfg.get("display_name") or getattr(platform, "name", None)
                 or "EvoSys Pro"),
        "support_email": (cfg.get("support_email")
                          or getattr(platform, "support_email", None)),
        "website": (getattr(platform, "website_url", None)
                    or cfg.get("app_base_url")),
        "platform_id": getattr(platform, "id", None),
    }


def _esc(value: Any) -> str:
    text = "" if value is None else str(value)
    return html.escape(text.strip() or "—")


def _field_rows(rows: list[tuple[str, Any]]) -> str:
    cells = []
    for label, value in rows:
        cells.append(
            "<tr>"
            "<td style='padding:7px 14px 7px 0;font-weight:700;color:#445;"
            "width:165px;vertical-align:top'>%s</td>"
            "<td style='padding:7px 0;color:#172033;vertical-align:top'>%s</td>"
            "</tr>" % (_esc(label), _esc(value))
        )
    return "".join(cells)


def _submission_fields(payload, lead: Lead, platform: Platform) -> list[tuple[str, Any]]:
    extra = payload.model_extra or {}
    submitted_at = payload.submitted_at or datetime.utcnow().isoformat() + "Z"
    message = payload.message or payload.goals
    return [
        ("Contact name", " ".join(p for p in (lead.first_name, lead.last_name) if p)),
        ("Company", payload.company),
        ("Email", payload.email or lead.email),
        ("Phone", payload.phone or lead.phone),
        ("Package / interest", extra.get("package") or extra.get("interest") or extra.get("plan")),
        ("Industry", payload.industry),
        ("Locations", extra.get("locations")),
        ("Lead volume", extra.get("leads") or extra.get("lead_volume")),
        ("Current system", extra.get("current_system")),
        ("Business details", extra.get("business_details") or extra.get("business") or message),
        ("Message / notes", message),
        ("Lead source", "%s Website · Request Demo"
         % (getattr(platform, "name", None) or "Website")),
        ("Page URL", payload.page_url or payload.source_url),
        ("Referrer", payload.referrer),
        ("Submission timestamp", submitted_at),
        ("Lead ID", lead.id),
    ]


def _shell(brand_name: str, headline: str, body_html: str) -> str:
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;color:#172033;"
        "max-width:680px;margin:0 auto;padding:24px'>"
        "<p style='margin:0 0 4px;font-size:12px;color:#667085'>%s</p>"
        "<div style='height:3px;width:48px;background:#2b6ef3;margin:0 0 16px'></div>"
        "<h2 style='font-size:20px;line-height:1.3;margin:0 0 16px'>%s</h2>"
        "%s"
        "<p style='font-size:12px;color:#667085;margin:24px 0 0'>%s</p>"
        "</div>"
    ) % (_esc(brand_name), _esc(headline), body_html, _esc(brand_name))


def _send(to_email: str, subject: str, html_body: str,
          identity: DemoSendingIdentity) -> dict:
    from app.services.email_service import send_email_via_provider
    return send_email_via_provider(
        to_email,
        subject,
        html_body,
        org=identity,
        message_type="public_demo_request",
        sensitivity="operational",
    )


def notify_demo_request(db: Session, *, platform: Platform, lead: Lead,
                        payload) -> DemoNotificationResult:
    """Send internal + submitter emails. Never raises."""
    brand = _brand(db, platform)
    identity = DemoSendingIdentity(
        from_email=brand["support_email"],
        platform_id=brand["platform_id"],
        from_name=brand["name"],
    )
    result = DemoNotificationResult()

    if not identity.from_email:
        msg = "no platform support_email configured"
        result.internal_error = msg
        result.customer_error = msg
        log.error("public demo notification refused for lead %s: %s",
                  getattr(lead, "id", None), msg)
        return result

    rows = _submission_fields(payload, lead, platform)
    internal_body = _shell(
        brand["name"] or "EvoSys Pro",
        "New EvoSys Pro Demo Request",
        "<table style='border-collapse:collapse;width:100%%;font-size:14px'>%s</table>"
        % _field_rows(rows),
    )
    internal_to = brand["support_email"]
    try:
        sent = _send(
            internal_to,
            "New EvoSys Pro Demo Request — %s"
            % (" ".join(p for p in (lead.first_name, lead.last_name) if p)
               or payload.company or payload.email or "Website Lead"),
            internal_body,
            identity,
        )
        result.internal_sent = bool(sent and sent.get("success"))
        if not result.internal_sent:
            result.internal_error = (sent or {}).get("error") or "provider returned failure"
    except Exception as exc:  # noqa: BLE001
        result.internal_error = str(exc)
    if not result.internal_sent:
        log.warning("public demo internal notification failed for lead %s: %s",
                    getattr(lead, "id", None), result.internal_error)

    customer_to = (payload.email or lead.email or "").strip()
    if customer_to:
        customer_body = _shell(
            brand["name"] or "EvoSys Pro",
            "We received your demo request",
            "<p style='font-size:15px;line-height:1.6;margin:0 0 12px'>"
            "Thank you for reaching out to %s. We received your demo request "
            "and an %s team member will follow up with you shortly.</p>"
            "<p style='font-size:15px;line-height:1.6;margin:0'>"
            "If you need to add anything before then, you can reply directly "
            "to this email.</p>"
            % (_esc(brand["name"] or "EvoSys Pro"),
               _esc(brand["name"] or "EvoSys Pro")),
        )
        try:
            sent = _send(customer_to,
                         "EvoSys Pro — We Received Your Demo Request",
                         customer_body, identity)
            result.customer_sent = bool(sent and sent.get("success"))
            if not result.customer_sent:
                result.customer_error = ((sent or {}).get("error")
                                         or "provider returned failure")
        except Exception as exc:  # noqa: BLE001
            result.customer_error = str(exc)
    else:
        result.customer_error = "no customer email on submission"

    if not result.customer_sent:
        log.warning("public demo customer confirmation failed for lead %s: %s",
                    getattr(lead, "id", None), result.customer_error)
    return result
