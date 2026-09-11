import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler


TAWK_SECRET = os.environ.get("TAWK_SECRET", "")
FRESHDESK_API_KEY = os.environ.get("FRESHDESK_API_KEY", "")
FRESHDESK_DOMAIN = os.environ.get("FRESHDESK_DOMAIN", "snel.freshdesk.com")


def _json_response(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _verify_tawk_signature(raw_body, signature):
    if not TAWK_SECRET or not signature:
        return False

    expected = hmac.new(
        TAWK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha1,
    ).hexdigest()

    return hmac.compare_digest(expected, signature.strip())


def _create_freshdesk_ticket(payload, event_id=""):
    requester = payload.get("requester") or {}
    ticket = payload.get("ticket") or {}
    property_data = payload.get("property") or {}

    email = (requester.get("email") or "").strip()
    name = (requester.get("name") or "").strip()

    if not email:
        raise ValueError("Tawk ticket does not contain requester.email")

    subject = (ticket.get("subject") or "Tawk support request").strip()
    message = ticket.get("message") or "Ticket created from Tawk"
    tawk_ticket_id = ticket.get("id") or ""
    tawk_human_id = ticket.get("humanId") or ""
    property_name = property_data.get("name") or ""

    metadata = [
        "",
        "---",
        "Source: Tawk",
        f"Tawk property: {property_name}",
        f"Tawk ticket ID: {tawk_ticket_id}",
        f"Tawk ticket number: {tawk_human_id}",
        f"Tawk webhook event ID: {event_id}",
        f"Customer email: {email}",
    ]

    description = str(message) + "\n" + "\n".join(metadata)

    freshdesk_payload = {
        "email": email,
        "subject": subject,
        "description": description,
        "status": 2,
        "priority": 1,
        "tags": ["tawk", "tawk-api"],
    }

    if name:
        freshdesk_payload["name"] = name

    auth = base64.b64encode(
        f"{FRESHDESK_API_KEY}:X".encode("utf-8")
    ).decode("ascii")

    request = urllib.request.Request(
        f"https://{FRESHDESK_DOMAIN}/api/v2/tickets",
        data=json.dumps(freshdesk_payload).encode("utf-8"),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Snel-Tawk-Freshdesk-Bridge/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Freshdesk returned HTTP {exc.code}: {error_body}"
        ) from exc


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}")

    def do_GET(self):
        _json_response(
            self,
            200,
            {
                "status": "ok",
                "service": "snel-tawk-freshdesk",
                "freshdesk_domain": FRESHDESK_DOMAIN,
            },
        )

    def do_POST(self):
        if not TAWK_SECRET or not FRESHDESK_API_KEY:
            _json_response(
                self,
                500,
                {"error": "Server environment variables are not configured"},
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(self, 400, {"error": "Invalid Content-Length"})
            return

        if length <= 0 or length > 5_000_000:
            _json_response(self, 400, {"error": "Invalid request size"})
            return

        raw_body = self.rfile.read(length)
        signature = self.headers.get("X-Tawk-Signature", "")

        if not _verify_tawk_signature(raw_body, signature):
            _json_response(self, 401, {"error": "Invalid Tawk signature"})
            return

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "Invalid JSON body"})
            return

        event_name = payload.get("event")

        if event_name != "ticket:create":
            _json_response(
                self,
                200,
                {"status": "ignored", "event": event_name},
            )
            return

        event_id = self.headers.get("X-Hook-Event-Id", "")

        try:
            result = _create_freshdesk_ticket(payload, event_id=event_id)
        except ValueError as exc:
            print(f"Validation error: {exc}")
            _json_response(self, 422, {"error": str(exc)})
            return
        except Exception as exc:
            print(f"Webhook processing failed: {exc}")
            _json_response(self, 502, {"error": "Freshdesk ticket creation failed"})
            return

        requester = payload.get("requester") or {}
        print(
            "Freshdesk ticket created:",
            result.get("id"),
            requester.get("email", ""),
        )

        _json_response(
            self,
            200,
            {
                "success": True,
                "freshdesk_ticket_id": result.get("id"),
                "requester_email": requester.get("email", ""),
            },
        )
