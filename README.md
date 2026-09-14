# Snel Tawk → Freshdesk Bridge

Serverless Python webhook bridge for creating Freshdesk tickets from Tawk while preserving the customer's real email address as the Freshdesk requester.

## Required flow

Customer → Tawk chat/chatbot → Tawk `ticket:create` webhook → `https://tawk.t8t.bar/` → Vercel Python function → Freshdesk API

Do **not** use Tawk's Ticket Forwarding Email to create the same Freshdesk tickets. The forwarding route uses Tawk relay addresses and can create a duplicate ticket with the wrong requester.

## What the bridge does

For every `ticket:create` webhook:

1. Verify the Tawk HMAC signature.
2. Resolve the customer's real email from visitor/contact/ticket/requester fields.
3. If Tawk supplied a generated `@tawk.to` or `@tawk.email` requester, ignore it.
4. If the customer email is present in the ticket text (for example a chatbot answer such as `Email: customer@example.com`), use that email instead.
5. Create the Freshdesk ticket with the resolved customer email in Freshdesk's `email` field.
6. If only a Tawk relay/generated email is available, return HTTP 422 and do **not** create a Freshdesk ticket under the wrong address.

## Vercel environment variables

Add these in **Vercel → Project → Settings → Environment Variables**:

- `TAWK_SECRET` — Tawk webhook signing secret
- `FRESHDESK_API_KEY` — Freshdesk API key
- `FRESHDESK_DOMAIN` — `snel.freshdesk.com`

Do not commit secrets to this repository.

## Deploy

1. Import this GitHub repository into Vercel or keep the existing Git integration enabled.
2. Add the environment variables above.
3. Attach the custom domain `tawk.t8t.bar` to the Vercel project.
4. Redeploy after changing environment variables.
5. Check `https://tawk.t8t.bar/`.

Expected health response includes:

```json
{
  "status": "ok",
  "service": "snel-tawk-freshdesk",
  "freshdesk_domain": "snel.freshdesk.com",
  "version": "1.1"
}
```

## Tawk configuration

In **Tawk → Administration → Settings → Webhooks** create or edit the webhook:

- URL: `https://tawk.t8t.bar/`
- Event: **New Ticket**
- Secret: same value configured as `TAWK_SECRET` in Vercel

The chatbot/ticket workflow should collect the customer's email before creating the ticket. Prefer including the captured email in the ticket/requester data. If the bot template supports adding form answers to the ticket message, include a line such as:

```text
Customer email: {{customer email}}
```

This gives the bridge an additional safe source when Tawk itself substitutes a generated requester address.

## Freshdesk configuration

Freshdesk should receive tickets from this bridge through the Freshdesk API.

Disable any old route where Freshdesk receives Tawk tickets by email forwarding. In particular, do not keep a Tawk Ticket Forwarding Email feeding the same Freshdesk support address, because that bypasses this bridge and may show the Tawk relay address as the requester.

## Test

1. Start a new Tawk chat as a test visitor.
2. Enter a real test email address when the chatbot asks for it.
3. Ask the chatbot to create a support ticket.
4. Verify only one new ticket is created in Freshdesk.
5. Verify **Requester** in Freshdesk is the test visitor's email, not a Tawk-generated address.
6. Reply from Freshdesk and confirm the reply goes directly to the customer email.

The Freshdesk ticket description includes the Tawk ticket ID, webhook event ID, resolved customer email, and how the email was resolved for troubleshooting.

## Notes

Persistent duplicate-event storage is not included yet. Tawk may retry failed webhook events using the same `X-Hook-Event-Id`; adding persistent idempotency storage can prevent rare duplicates caused by retry timing.
