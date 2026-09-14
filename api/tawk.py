import base64
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler


TAWK_SECRET = os.environ.get("TAWK_SECRET", "")
FRESHDESK_API_KEY = os.environ.get("FRESHDESK_API_KEY", "")
FRESHDESK_DOMAIN = os.environ.get("FRESHDESK_DOMAIN", "snel.freshdesk.com")

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

# These are service/relay addresses and must never become the Freshdesk requester.
TAWK_EMAIL_DOMAINS = {
    "tawk.to",
    "tawk.email",
}


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


def _normalise_email(value):
    return (value or "").strip().strip("<>[](){}.,;:\"'").lower()


def _is_tawk_service_email(email):
    email = _normalise_email(email)
    if "@" not in email:
        return False

    domain = email.rsplit("@", 1)[1]
    return domain in TAWK_EMAIL_DOMAINS or domain.endswith(".tawk.to") or domain.endswith(".tawk.email")


def _email_candidates_from_text(value):
    if not value:
        return []
    return [_normalise_email(match) for match in EMAIL_RE.findall(str(value))]


def _resolve_customer_email(payload):
    """Return the visitor/customer email, never a Tawk relay address.

    Tawk's official ticket:create payload exposes requester.email, but chatbot or
    forwarding flows can make that a Tawk-owned relay address.  We therefore
    inspect all visitor/contact-style fields we may receive and finally the
    ticket message, where bots commonly include captured form answers.
    """
    requester = payload.get("requester") or {}
    ticket = payload.get("ticket") or {}
    visitor = payload.get("visitor") or {}
    contact = payload.get("contact") or {}

    candidates = [
        visitor.get("email"),
        contact.get("email"),
        ticket.get("requesterEmail"),
        ticket.get("email"),
        requester.get("email"),
    ]

    # Prefer explicit structured customer fields first.
    for candidate in candidates:
        email = _normalise_email(candidate)
        if email and EMAIL_RE.fullmatch(email) and not _is_tawk_service_email(email):
            return email, "payload"

    # If Tawk used a generated/relay requester, recover the address the customer
    # supplied to the bot when it is present in the ticket message/subject.
    text_candidates = []
    text_candidates.extend(_email_candidates_from_text(ticket.get("message")))
    text_candidates.extend(_email_candidates_from_text(ticket.get("subject")))

    for email in text_candidates:
        if not _is_tawk_service_email(email):
            return email, "ticket_text"

    original = _normalise_email(requester.get("email"))
    if original and _is_tawk_service_email(original):
        raise ValueError(
            "Tawk supplied only a generated/relay email. "
            "Customer email was not available in the ticket payload."
        )

    raise ValueError("Tawk ticket does not contain a usable customer email")


def _create_freshdesk_ticket(payload, event_id=""):
    requester = payload.get("requester") or {}
    ticket = payload.get("ticket") or {}
    property_data = payload.get("property") or {}

    email, email_source = _resolve_customer_email(payload)
    name = (requester.get("name") or "").strip()
    original_requester_email = _normalise_email(requester.get("email"))

    subject = (ticket.get("subject") or "Tawk support request").strip()
    message = ticket.get("message") or "Ticket created from Tawk"
    tawk_ticket_id = ticket.get("id") or ""
    tawk_human_id = ticket.get("humanId") or ""
    property_name = property_data.get("name") or ""

    metadata = [
        "",
        "---",
        "Source: Tawk API webhook",
        f"Tawk property: {property_name}",
        f"Tawk ticket ID: {tawk_ticket_id}",
        f"Tawk ticket number: {tawk_human_id}",
        f"Tawk webhook event ID: {event_id}",
        f"Customer email: {email}",
        f"Customer email source: {email_source}",
    ]

    if original_requester_email and original_requester_email != email:
        metadata.append(f"Original Tawk requester email ignored: {original_requester_email}")

    description = str(message) + "\n" + "\n".join(metadata)

    freshdesk_payload = {
        "email": email,
        "subject": subject,
        "description": description,
        "status": 2,
        "priority": 1,
        "tags": ["tawk", "tawk-api", "customer-email"],
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
            "User-Agent": "Snel-Tawk-Freshdesk-Bridge/1.1",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
            result["_resolved_requester_email"] = email
            result["_email_source"] = email_source
            return result
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
                "version": "1.1",
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

        print(
            "Freshdesk ticket created:",
            result.get("id"),
            result.get("_resolved_requester_email", ""),
        )

        _json_response(
            self,
            200,
            {
                "success": True,
                "freshdesk_ticket_id": result.get("id"),
                "requester_email": result.get("_resolved_requester_email", ""),
                "email_source": result.get("_email_source", ""),
            },
        )
