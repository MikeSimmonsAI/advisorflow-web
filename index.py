"""
AdvisorFlow Booking API — Vercel Serverless
Tokens are self-contained (base64-encoded JSON) — no database needed.
Token format: base64(json({lead, appt_type, expires_at, sig}))

Calls BookaBoost backend after booking confirmation to:
1. Save booking to database
2. Create Microsoft 365 Outlook calendar event
3. Send FSA SMS notification
"""

import json
import base64
import hashlib
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ADVISOR = {
    "name":    os.environ.get("ADVISOR_NAME", "Mike Simmons"),
    "title":   os.environ.get("ADVISOR_TITLE", "Family Service Advisor"),
    "company": os.environ.get("ADVISOR_COMPANY", "Restland Cemetery & Funeral Home"),
    "address": os.environ.get("ADVISOR_ADDRESS", "13005 Greenville Ave, Dallas, TX 75243"),
    "phone":   os.environ.get("ADVISOR_PHONE", "214-550-1234"),
    "email":   os.environ.get("ADVISOR_EMAIL", ""),
}

SECRET = os.environ.get("BOOKING_SECRET", "advisorflow2026restland")

# Read BACKEND_URL at call time (not module load time) so Vercel env vars
# are always resolved — module-level reads can silently get empty strings
# in some serverless cold-start scenarios.
_BACKEND_URL_DEFAULT = "https://advisorflow-backend.onrender.com"

SLOTS_DAYS_AHEAD = 14
SLOT_TIMES = ["9:00 AM", "10:00 AM", "11:00 AM", "1:00 PM", "2:00 PM", "3:00 PM", "4:00 PM"]
SKIP_DAYS = [6]  # Sunday


def _get_backend_url() -> str:
    """Read BACKEND_URL fresh each call — avoids cold-start env var issues."""
    url = os.environ.get("BACKEND_URL", "").strip()
    if not url:
        print(
            f"[booking] WARNING: BACKEND_URL env var is empty or not set. "
            f"Falling back to {_BACKEND_URL_DEFAULT}. "
            f"Set BACKEND_URL in Vercel project settings.",
            file=sys.stderr,
        )
        return _BACKEND_URL_DEFAULT
    return url.rstrip("/")


def _sign(payload: str) -> str:
    return hashlib.sha256(f"{SECRET}:{payload}".encode()).hexdigest()[:16]


def _decode_token(token: str):
    try:
        parts = token.rsplit("~", 1)
        if len(parts) != 2:
            parts = token.rsplit(".", 1)
        if len(parts) != 2:
            return None, "invalid"
        payload, sig = parts
        if _sign(payload) != sig:
            return None, "invalid"
        padding = 4 - len(payload) % 4
        padded = payload + ("=" * padding) if padding != 4 else payload
        data = json.loads(base64.urlsafe_b64decode(padded).decode())
        expires = datetime.fromisoformat(data["expires"])
        if datetime.utcnow() > expires:
            return None, "expired"
        return data, None
    except Exception as e:
        print(f"[booking] Token decode error: {e}", file=sys.stderr)
        return None, "invalid"


def _generate_slots():
    slots = []
    today = datetime.now()
    days_added = 0
    check_day = today + timedelta(days=1)

    while days_added < SLOTS_DAYS_AHEAD:
        if check_day.weekday() not in SKIP_DAYS:
            day_label = check_day.strftime("%A, %b %d").replace(" 0", " ")
            for t in SLOT_TIMES:
                slot_id = f"{check_day.strftime('%Y%m%d')}_{t.replace(':', '').replace(' ', '')}"
                slots.append({
                    "slot_id": slot_id,
                    "label":   f"{day_label} at {t}",
                    "date":    check_day.strftime("%m/%d/%Y"),
                    "time":    t,
                })
            days_added += 1
        check_day += timedelta(days=1)
    return slots


def _notify_backend(token: str, slot_id: str, slot_display: str, lead: dict, confirmation: dict):
    """
    Notify BookaBoost backend of confirmed booking.
    Backend creates Outlook calendar event + sends FSA SMS notification.

    Errors are logged in full to stderr so they appear in Vercel function
    logs (Functions tab → select function → view logs). Non-fatal — booking
    confirmation page is shown to the lead regardless of backend outcome.
    """
    backend_url = _get_backend_url()
    endpoint = f"{backend_url}/calendar/booking-confirmed"

    payload_data = {
        "booking_token": token,
        "slot_id":       slot_id,
        "slot_display":  slot_display,
        "lead_name":     f"{lead.get('First Name', '')} {lead.get('Last Name', '')}".strip(),
        "lead_phone":    lead.get("Phone", ""),
        "appt_label":    confirmation.get("appt_label", "Family File Review"),
        "advisor_name":  ADVISOR["name"],
        "advisor_phone": ADVISOR["phone"],
        "confirmation":  confirmation,
    }

    print(f"[booking] Notifying backend: POST {endpoint}", file=sys.stderr)
    print(f"[booking] Payload: token={token[:20]}... appt={payload_data['appt_label']} slot={slot_display}", file=sys.stderr)

    try:
        encoded = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=encoded,
            headers={
                "Content-Type":   "application/json",
                "Content-Length": str(len(encoded)),
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(body)
            except Exception:
                result = {"raw": body}

            if status in (200, 201):
                print(f"[booking] Backend notified OK ({status}): {result}", file=sys.stderr)
            else:
                print(
                    f"[booking] Backend returned unexpected status {status}: {body[:500]}",
                    file=sys.stderr,
                )

    except urllib.error.HTTPError as e:
        # HTTP 4xx/5xx from the backend — read and log the body for diagnosis
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = "(could not read error body)"
        print(
            f"[booking] Backend HTTP error {e.code} at {endpoint}: {error_body[:500]}",
            file=sys.stderr,
        )

    except urllib.error.URLError as e:
        # Network-level failure: DNS, connection refused, timeout, etc.
        print(
            f"[booking] Backend URLError at {endpoint}: {e.reason}",
            file=sys.stderr,
        )

    except Exception as e:
        # Catch-all: serialisation errors, unexpected exceptions, etc.
        print(
            f"[booking] Backend notification unexpected error at {endpoint}: {type(e).__name__}: {e}",
            file=sys.stderr,
        )


def _booking_page_html():
    html_path = os.path.join(os.path.dirname(__file__), "..", "booking_page.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "<html><body>Booking page not found.</body></html>"


class handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def _send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/health":
            backend_url = _get_backend_url()
            self._send_json(200, {
                "status":      "ok",
                "advisor":     ADVISOR["name"],
                "backend_url": backend_url,
            })
            return

        if path == "/policy/privacy":
            self._send_html(200, PRIVACY_HTML)
            return

        if path == "/policy/terms":
            self._send_html(200, TERMS_HTML)
            return

        if path.startswith("/book/"):
            self._send_html(200, _booking_page_html())
            return

        if path.startswith("/api/book/"):
            token = path[len("/api/book/"):]
            data, err = _decode_token(token)
            if err:
                self._send_json(404, {
                    "error":   "not_found",
                    "message": "This booking link is not valid or has expired.",
                    "advisor": ADVISOR,
                })
                return

            lead = data.get("lead", {})
            first = str(lead.get("First Name", "") or "").strip() or "there"
            last  = str(lead.get("Last Name",  "") or "").strip()
            full  = f"{first} {last}".strip()
            appt_label = data.get("appt_label") or data.get("appt_type") or "Family Services Appointment"
            duration   = data.get("duration", "20-30")

            self._send_json(200, {
                "customer_name":  full,
                "customer_first": first,
                "appt_label":     appt_label,
                "appt_type":      data.get("appt_type", "general"),
                "duration":       duration,
                "advisor":        ADVISOR,
                "slots":          _generate_slots(),
                "token":          token,
            })
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/token/create":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            lead      = body.get("lead", {})
            appt_type = body.get("appt_type", "file_review")

            expires = (datetime.utcnow() + timedelta(days=14)).isoformat()
            data    = {"lead": lead, "appt_type": appt_type, "expires": expires}
            payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
            sig     = _sign(payload)
            token   = f"{payload}~{sig}"

            booking_url = f"https://advisorflow-booking.vercel.app/book/{token}"
            self._send_json(200, {
                "token":      token,
                "booking_url": booking_url,
                "appt_label": "Family File Review",
                "expires_at": expires,
            })
            return

        if "/api/book/" in path and path.endswith("/confirm"):
            token    = path.split("/api/book/")[1].replace("/confirm", "")
            data, err = _decode_token(token)
            if err:
                self._send_json(404, {"error": "invalid", "message": "Invalid or expired link."})
                return

            length   = int(self.headers.get("Content-Length", 0))
            body     = json.loads(self.rfile.read(length)) if length else {}
            slot_id  = body.get("slot_id", "")

            lead  = data.get("lead", {})
            first = str(lead.get("First Name", "") or "").strip() or "there"
            last  = str(lead.get("Last Name",  "") or "").strip()
            full  = f"{first} {last}".strip()

            # Build human-readable slot display string
            display_time = slot_id
            try:
                raw_date     = slot_id.split("_")[0]
                dt           = datetime.strptime(raw_date, "%Y%m%d")
                display_date = dt.strftime("%A, %B %d, %Y")
                for s in _generate_slots():
                    if s["slot_id"] == slot_id:
                        display_time = f"{display_date} at {s['time']}"
                        break
            except Exception as e:
                print(f"[booking] Slot display parse error for slot_id={slot_id!r}: {e}", file=sys.stderr)

            appt_label = data.get("appt_label") or data.get("appt_type") or "Family Services Appointment"
            duration   = data.get("duration", "20-30")

            confirmation = {
                "full_display":  display_time,
                "appt_label":    appt_label,
                "duration":      duration,
                "advisor_name":  ADVISOR["name"],
                "advisor_phone": ADVISOR["phone"],
                "company":       ADVISOR["company"],
                "address":       ADVISOR["address"],
                "customer_name": full,
            }

            # Notify BookaBoost backend — creates Outlook calendar event + FSA SMS.
            # Errors are logged to Vercel function logs; booking page still confirms.
            _notify_backend(token, slot_id, display_time, lead, confirmation)

            self._send_json(200, {"success": True, "confirmation": confirmation})
            return

        self._send_json(404, {"error": "not_found"})


PRIVACY_HTML = """<!DOCTYPE html><html><body><h1>Privacy Policy</h1>
<p>We collect your name and phone number to send appointment reminders via SMS.
Reply STOP to opt out. We never sell your data.</p>
<p>Restland Cemetery &amp; Funeral Home — 13005 Greenville Ave, Dallas TX 75243</p>
</body></html>"""

TERMS_HTML = """<!DOCTYPE html><html><body><h1>Terms &amp; Conditions</h1>
<p>By providing your phone number you consent to receive SMS messages regarding
appointment scheduling. Message frequency varies. Msg &amp; data rates may apply.
Reply STOP to opt out. Reply HELP for help.</p>
</body></html>"""
