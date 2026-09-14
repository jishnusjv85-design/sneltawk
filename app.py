"""WSGI entrypoint for the Tawk to Freshdesk webhook bridge."""

import json

from flask import Flask, jsonify, request

from api.tawk import (
    FRESHDESK_API_KEY,
    FRESHDESK_DOMAIN,
    TAWK_SECRET,
    _create_freshdesk_ticket,
    _verify_tawk_signature,
)


app = Flask(__name__)


@app.get("/")
@app.get("/tawk")
@app.get("/api/tawk")
def health_check():
    return jsonify(
        status="ok",
        service="snel-tawk-freshdesk",
        freshdesk_domain=FRESHDESK_DOMAIN,
        version="1.1",
    )


@app.post("/")
@app.post("/tawk")
@app.post("/api/tawk")
def tawk_webhook():
    if not TAWK_SECRET or not FRESHDESK_API_KEY:
        return jsonify(error="Server environment variables are not configured"), 500

    raw_body = request.get_data(cache=False)
    if not raw_body or len(raw_body) > 5_000_000:
        return jsonify(error="Invalid request size"), 400

    signature = request.headers.get("X-Tawk-Signature", "")
    if not _verify_tawk_signature(raw_body, signature):
        return jsonify(error="Invalid Tawk signature"), 401

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify(error="Invalid JSON body"), 400

    event_name = payload.get("event")
    if event_name != "ticket:create":
        return jsonify(status="ignored", event=event_name), 200

    try:
        result = _create_freshdesk_ticket(
            payload,
            event_id=request.headers.get("X-Hook-Event-Id", ""),
        )
    except ValueError as exc:
        app.logger.warning("Webhook validation failed: %s", exc)
        return jsonify(error=str(exc)), 422
    except Exception:
        app.logger.exception("Freshdesk ticket creation failed")
        return jsonify(error="Freshdesk ticket creation failed"), 502

    return jsonify(
        success=True,
        freshdesk_ticket_id=result.get("id"),
        requester_email=result.get("_resolved_requester_email", ""),
        email_source=result.get("_email_source", ""),
    )
