"""
Email Poller Service
Polls Microsoft 365 inbox every 2 minutes for new replies from leads.
Matches emails to leads by sender address, saves as Reply records,
and triggers the AI pipeline to respond automatically.

Called by the Render cron job (advisorflow-email-poller) hitting
POST /email/poll-inbox/all every 2 minutes.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _get_fresh_access_token(advisor) -> str:
    """Get fresh Microsoft Graph access token from stored refresh token."""
    from app.utils.crypto import decrypt_value
    import httpx

    if not advisor.microsoft_oauth_refresh_token_encrypted:
        raise ValueError(f"Advisor {advisor.id} has no Microsoft 365 refresh token")

    refresh_token = decrypt_value(advisor.microsoft_oauth_refresh_token_encrypted)
    client_id = os.environ.get("MICROSOFT_CLIENT_ID")
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET")

    response = httpx.post(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "offline_access Mail.Read Mail.Send User.Read",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _fetch_recent_emails(access_token: str, since_minutes: int = 5) -> list:
    """
    Fetch emails received in the last N minutes from Microsoft Graph.
    Polls inbox; looks for any new message (not just Re: prefixed — leads
    often reply without keeping the subject line).
    """
    import httpx

    since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()

    response = httpx.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "$filter": f"receivedDateTime ge {since}",
            "$select": "id,subject,from,receivedDateTime,body,bodyPreview,conversationId,categories",
            "$orderby": "receivedDateTime desc",
            "$top": 50,
        },
        timeout=20,
    )
    if response.status_code != 200:
        logger.error("Graph inbox fetch failed: %s %s", response.status_code, response.text)
        return []
    return response.json().get("value", [])


def _mark_email_processed(access_token: str, message_id: str, tag: str = "bookaboost-processed"):
    """Add a category tag to processed emails so we don't process them twice."""
    import httpx
    try:
        httpx.patch(
            f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"categories": [tag]},
            timeout=10,
        )
    except Exception as e:
        logger.error("Failed to tag email %s: %s", message_id, e)


def poll_inbox_for_replies(db: Session, advisor_id: str) -> dict:
    """
    Main polling function. Called every 2 minutes per advisor via cron.

    1. Get fresh access token
    2. Fetch inbox emails from last 5 minutes
    3. Match sender to leads by email address
    4. Save unprocessed replies
    5. Trigger AI pipeline for each match

    Returns: {"checked": int, "matched": int, "errors": int}
    """
    from app.models.models import User, Lead, Reply, Organization
    from app.services.pipeline_service import process_inbound_reply

    advisor = db.query(User).filter(User.id == advisor_id).first()
    if not advisor:
        return {"checked": 0, "matched": 0, "errors": 1, "error": "Advisor not found"}

    if not advisor.microsoft_365_connected or not advisor.microsoft_oauth_refresh_token_encrypted:
        return {"checked": 0, "matched": 0, "errors": 0, "note": "Microsoft 365 not connected"}

    try:
        access_token = _get_fresh_access_token(advisor)
    except Exception as e:
        logger.error("Failed to get access token for advisor %s: %s", advisor_id, e)
        return {"checked": 0, "matched": 0, "errors": 1, "error": str(e)}

    emails = _fetch_recent_emails(access_token, since_minutes=5)
    checked = len(emails)
    matched = 0
    errors = 0

    for email in emails:
        try:
            # Skip emails already tagged as processed
            categories = email.get("categories", [])
            if "bookaboost-processed" in categories:
                continue

            sender_email = email.get("from", {}).get("emailAddress", {}).get("address", "").lower().strip()
            if not sender_email:
                continue

            # Skip our own outbound emails bouncing back
            if advisor.microsoft_email_address and sender_email == advisor.microsoft_email_address.lower():
                continue

            # Match sender to a lead by email address within same org
            lead = db.query(Lead).filter(
                Lead.organization_id == advisor.organization_id,
                Lead.email.ilike(sender_email),
            ).first()

            if not lead:
                continue

            # Parse received timestamp
            received_at_str = email.get("receivedDateTime", "")
            try:
                received_at = datetime.fromisoformat(received_at_str.replace("Z", "+00:00"))
            except Exception:
                received_at = datetime.utcnow()

            # Extract plain text from body
            body_full = email.get("body", {}).get("content", email.get("bodyPreview", "")).strip()
            import re
            body_text = re.sub(r'<[^>]+>', '', body_full).strip()
            body_text = re.sub(r'\s+', ' ', body_text).strip()
            if len(body_text) > 1000:
                body_text = body_text[:1000] + "..."

            # Deduplicate by body content
            existing = db.query(Reply).filter(
                Reply.lead_id == lead.id,
                Reply.body == body_text,
            ).first()
            if existing:
                _mark_email_processed(access_token, email["id"])
                continue

            # Save as Reply record
            reply = Reply(
                lead_id=lead.id,
                body=body_text,
                source="email",
                received_at=received_at,
                classification="neutral",
                is_hot=False,
            )
            db.add(reply)
            db.flush()

            # Advance lead status
            if lead.status in ("new", "sent"):
                lead.status = "replied"

            # Trigger the full AI pipeline — same path as inbound SMS reply
            try:
                process_inbound_reply(db, lead, advisor, reply)
            except Exception as pe:
                logger.error("Pipeline error for email reply lead=%s: %s", lead.id, pe)

            # Fire alert email if reply is hot
            if reply.is_hot:
                try:
                    _send_hot_reply_alert(advisor, lead, body_text)
                except Exception as he:
                    logger.error("Hot reply alert email error lead=%s: %s", lead.id, he)

            # Tag email so we skip it on the next poll cycle
            _mark_email_processed(access_token, email["id"])

            matched += 1
            logger.info("Email reply captured: %s → lead %s", sender_email, lead.id)

        except Exception as e:
            logger.error("Error processing email %s: %s", email.get("id", "?"), e)
            errors += 1

    db.commit()
    return {"checked": checked, "matched": matched, "errors": errors}


def poll_all_advisors(db: Session, organization_id: str) -> dict:
    """Poll inbox for all M365-connected advisors in a single organization."""
    from app.models.models import User

    advisors = db.query(User).filter(
        User.organization_id == organization_id,
        User.microsoft_365_connected == True,
        User.is_active == True,
    ).all()

    total = {"checked": 0, "matched": 0, "errors": 0}
    for advisor in advisors:
        result = poll_inbox_for_replies(db, advisor.id)
        total["checked"] += result.get("checked", 0)
        total["matched"] += result.get("matched", 0)
        total["errors"] += result.get("errors", 0)

    return {**total, "advisors_polled": len(advisors)}


def poll_all_orgs(db: Session) -> dict:
    """
    Poll inbox for all M365-connected advisors across ALL organizations.
    Called by the Render cron job every 2 minutes via POST /email/poll-inbox/all.
    """
    from app.models.models import User, Organization

    # Get all active advisors with M365 connected across every org
    advisors = db.query(User).filter(
        User.microsoft_365_connected == True,
        User.is_active == True,
        User.microsoft_oauth_refresh_token_encrypted.isnot(None),
    ).all()

    total = {"checked": 0, "matched": 0, "errors": 0, "advisors_polled": 0}
    for advisor in advisors:
        try:
            result = poll_inbox_for_replies(db, advisor.id)
            total["checked"] += result.get("checked", 0)
            total["matched"] += result.get("matched", 0)
            total["errors"] += result.get("errors", 0)
            total["advisors_polled"] += 1
        except Exception as e:
            logger.error("poll_all_orgs: error on advisor %s: %s", advisor.id, e)
            total["errors"] += 1

    logger.info(
        "Email poll complete — advisors=%d checked=%d matched=%d errors=%d",
        total["advisors_polled"], total["checked"], total["matched"], total["errors"],
    )
    return total


NOTIFICATION_EMAIL = "michael.simmons@nsmg.com"
URGENT_TIERS = {"at_need", "atneed", "at-need", "imminent", "urgent"}


def _send_hot_reply_alert(advisor, lead, reply_body: str):
    """
    Send a 🔥 fire alert email to the advisor when a hot reply comes in.
    Fired on every hot reply — not just the first one.
    """
    import httpx
    import os

    if not advisor.microsoft_365_connected or not advisor.microsoft_oauth_refresh_token_encrypted:
        return

    from app.utils.crypto import decrypt_value
    import httpx as _httpx

    client_id     = os.environ.get("MICROSOFT_CLIENT_ID")
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET")
    refresh_token = decrypt_value(advisor.microsoft_oauth_refresh_token_encrypted)

    token_resp = _httpx.post(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "offline_access Mail.Read Mail.Send User.Read",
        },
        timeout=15,
    )
    if token_resp.status_code != 200:
        logger.error("Hot reply alert: token refresh failed %s", token_resp.text[:200])
        return

    access_token = token_resp.json()["access_token"]

    tier = (lead.tier or "").lower()
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "A lead"
    lead_url = f"{os.environ.get('FRONTEND_URL', 'https://advisorflow-frontend.onrender.com')}/leads/{lead.id}"
    is_urgent = tier in URGENT_TIERS

    subject = f"🔥 HOT REPLY — {lead_name} Just Responded!"
    if is_urgent:
        subject = f"🔥🔥 URGENT HOT REPLY — {lead_name} ({tier.upper()}) Needs You NOW"

    body_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#1a0505;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 16px;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.3);">

      <!-- Fire header -->
      <tr>
        <td style="background:linear-gradient(135deg,#c0392b,#e74c3c);padding:28px 32px;text-align:center;">
          <p style="margin:0;color:#ffd700;font-size:32px;">🔥</p>
          <h1 style="margin:8px 0 0;color:#ffffff;font-size:26px;font-weight:900;letter-spacing:-0.02em;">
            Hot Reply Alert
          </h1>
          <p style="margin:8px 0 0;color:rgba(255,255,255,0.85);font-size:14px;">
            {lead_name} just replied to your outreach. Strike while it's hot.
          </p>
        </td>
      </tr>

      <!-- Body -->
      <tr><td style="padding:32px;">

        <!-- Lead details -->
        <table width="100%" cellpadding="0" cellspacing="0" style="border:2px solid #e74c3c;border-radius:8px;overflow:hidden;margin-bottom:24px;">
          <tr style="background:#fff5f5;">
            <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;width:140px;">Lead</td>
            <td style="padding:12px 16px;color:#1a2a4a;font-weight:700;font-size:16px;">{lead_name}</td>
          </tr>
          <tr>
            <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #fecaca;">Phone</td>
            <td style="padding:12px 16px;color:#1a2a4a;border-top:1px solid #fecaca;">{lead.phone or 'N/A'}</td>
          </tr>
          <tr style="background:#fff5f5;">
            <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #fecaca;">Lead Type</td>
            <td style="padding:12px 16px;color:#1a2a4a;border-top:1px solid #fecaca;">{(lead.tier or 'Unknown').replace('_',' ').title()}</td>
          </tr>
          <tr>
            <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #fecaca;">Their Reply</td>
            <td style="padding:12px 16px;color:#1a2a4a;font-style:italic;border-top:1px solid #fecaca;">"{reply_body[:300]}{"..." if len(reply_body) > 300 else ""}"</td>
          </tr>
        </table>

        <!-- CTA -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="text-align:center;padding-bottom:20px;">
            <a href="{lead_url}"
               style="display:inline-block;background:#c0392b;color:#ffffff;padding:16px 40px;border-radius:8px;text-decoration:none;font-weight:800;font-size:16px;letter-spacing:-0.01em;">
              🔥 Open Lead Now →
            </a>
          </td></tr>
          <tr><td style="text-align:center;">
            <p style="margin:0;color:#94a3b8;font-size:13px;">
              The AI has already drafted a reply. Log in and send it before they go cold.
            </p>
          </td></tr>
        </table>

      </td></tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f8fafc;padding:16px 32px;border-top:1px solid #e2e8f0;">
          <p style="margin:0;color:#94a3b8;font-size:12px;text-align:center;">
            BookaBoost · Automated Hot Reply Alert · Do not reply to this email.
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""

    _httpx.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [{"emailAddress": {"address": NOTIFICATION_EMAIL}}],
            },
            "saveToSentItems": False,
        },
        timeout=15,
    )
    logger.info("Hot reply alert sent to %s for lead %s", NOTIFICATION_EMAIL, lead.id)
