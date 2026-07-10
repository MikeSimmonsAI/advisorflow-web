"""
Email Poller Service
Polls Microsoft 365 inbox every 5 minutes for new replies from leads.
Matches emails to leads by sender address, saves as Reply records,
and triggers the AI pipeline to respond automatically.

Called by the cadence cron job or a dedicated scheduled endpoint.
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


def _fetch_recent_emails(access_token: str, since_minutes: int = 10) -> list:
    """
    Fetch emails received in the last N minutes from Microsoft Graph.
    Only looks at inbox, filters for replies (has subject starting with Re:).
    """
    import httpx

    since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()

    response = httpx.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "$filter": f"receivedDateTime ge {since}",
            "$select": "id,subject,from,receivedDateTime,body,bodyPreview,conversationId",
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
    Main polling function. Call this every 5 minutes per advisor.
    
    1. Get fresh access token
    2. Fetch recent inbox emails
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

    emails = _fetch_recent_emails(access_token, since_minutes=10)
    checked = len(emails)
    matched = 0
    errors = 0

    for email in emails:
        try:
            # Skip emails we already processed
            categories = email.get("categories", [])
            if "bookaboost-processed" in categories:
                continue

            sender_email = email.get("from", {}).get("emailAddress", {}).get("address", "").lower().strip()
            if not sender_email:
                continue

            # Skip emails from ourselves
            if advisor.microsoft_email_address and sender_email == advisor.microsoft_email_address.lower():
                continue

            # Match sender to a lead by email address
            lead = db.query(Lead).filter(
                Lead.organization_id == advisor.organization_id,
                Lead.email.ilike(sender_email),
            ).first()

            if not lead:
                continue

            # Check if we already saved this exact email
            subject = email.get("subject", "")
            received_at_str = email.get("receivedDateTime", "")
            try:
                received_at = datetime.fromisoformat(received_at_str.replace("Z", "+00:00"))
            except Exception:
                received_at = datetime.utcnow()

            body_preview = email.get("bodyPreview", "").strip()
            body_full = email.get("body", {}).get("content", body_preview).strip()
            # Strip HTML tags for plain text
            import re
            body_text = re.sub(r'<[^>]+>', '', body_full).strip()
            body_text = re.sub(r'\s+', ' ', body_text).strip()
            if len(body_text) > 1000:
                body_text = body_text[:1000] + "..."

            # Check for duplicate reply
            existing = db.query(Reply).filter(
                Reply.lead_id == lead.id,
                Reply.body == body_text,
            ).first()
            if existing:
                _mark_email_processed(access_token, email["id"])
                continue

            # Save as a Reply record
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

            # Update lead status
            if lead.status == "new":
                lead.status = "replied"

            # Trigger AI pipeline
            try:
                process_inbound_reply(db, lead, advisor, reply)
            except Exception as pe:
                logger.error("Pipeline error for email reply: %s", pe)

            # Mark email as processed in Outlook
            _mark_email_processed(access_token, email["id"])

            matched += 1
            logger.info("Email reply captured: %s → lead %s", sender_email, lead.id)

        except Exception as e:
            logger.error("Error processing email %s: %s", email.get("id", "?"), e)
            errors += 1

    db.commit()
    return {"checked": checked, "matched": matched, "errors": errors}


def poll_all_advisors(db: Session, organization_id: str) -> dict:
    """Poll inbox for all M365-connected advisors in an organization."""
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
