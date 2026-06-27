"""
Email Open/Click Tracking

Per Mike's explicit request: real engagement signal on email that text
never gives him - knowing whether someone actually opened an email or
clicked a link in it, not just that it was sent.

HOW IT WORKS:
  - inject_tracking(body_html, email_message_id) is called once, right
    before an email actually sends, and does two things to the HTML:
    1. Rewrites every <a href="..."> link to point at this backend's
       own /email-tracking/click/{email_message_id} redirect endpoint
       first, which logs the click then 302-redirects to the real
       original URL - the recipient's experience is unaffected (they
       still land on the right page), the click is just logged on the
       way through.
    2. Appends a 1x1 transparent tracking pixel
       (<img src=".../open/{email_message_id}">) at the very end of
       the body - most email clients load images automatically, and
       that image request is what marks the email as opened.
  - The actual tracking endpoints (email_tracking_router.py) are
    deliberately UNAUTHENTICATED - they're hit directly by the
    recipient's email client/browser, which has no AdvisorFlow login
    at all. Each one only needs the email_message_id from the URL
    itself to know which row to update.

WHY THIS IS SAFE TO INJECT INTO BOTH SEND PROVIDERS WITH NO PROVIDER
CHANGES: both send_email_via_provider (SendGrid) and
send_email_via_microsoft_graph already just take a body_html string -
this only ever modifies that string before it's handed to either
function, never touches the provider integration itself.
"""

import os
import re
from html import escape

TRACKING_BASE_URL = os.environ.get("TRACKING_BASE_URL", "https://advisorflow-backend.onrender.com")

_LINK_PATTERN = re.compile(r'href=(["\'])(https?://[^"\']+)\1', re.IGNORECASE)


def inject_tracking(body_html: str, email_message_id: str) -> str:
    """
    Returns body_html with every link rewritten through the click
    tracker and a tracking pixel appended. Called once, right before
    send, in send_email_to_lead - never stored back onto
    EmailMessage.body_html itself, so the record of what was actually
    drafted/sent stays clean and re-readable without tracking noise
    baked into it permanently (the ORIGINAL body_html, pre-injection,
    is what gets saved to the database).
    """
    def _rewrite_link(match: re.Match) -> str:
        quote = match.group(1)
        original_url = match.group(2)
        tracked_url = f"{TRACKING_BASE_URL}/email-tracking/click/{email_message_id}?url={original_url}"
        return f'href={quote}{escape(tracked_url, quote=False)}{quote}'

    tracked_html = _LINK_PATTERN.sub(_rewrite_link, body_html)

    pixel_url = f"{TRACKING_BASE_URL}/email-tracking/open/{email_message_id}"
    tracking_pixel = f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none;" />'

    return tracked_html + tracking_pixel
