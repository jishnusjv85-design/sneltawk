# Snel Tawk → Freshdesk Bridge

Serverless Python webhook bridge for sending Tawk tickets to Freshdesk while preserving the visitor's real email address as the Freshdesk requester.

## Flow

Tawk → `https://t8t.bar/tawk` → Vercel Python function → Freshdesk API

## Vercel environment variables

Add these in **Vercel → Project → Settings → Environment Variables**:

- `TAWK_SECRET` — Tawk webhook signing secret
- `FRESHDESK_API_KEY` — Freshdesk API key
- `FRESHDESK_DOMAIN` — `snel.freshdesk.com`

Do not commit secrets to this repository.

## Deploy

1. Import this GitHub repository into Vercel.
2. Add the environment variables above.
3. Attach the custom domain `t8t.bar` to the Vercel project.
4. Deploy/redeploy after adding the variables.
5. Test:

```bash
curl https://t8t.bar/tawk
```

Expected response:

```json
{
  "status": "ok",
  "service": "snel-tawk-freshdesk",
  "freshdesk_domain": "snel.freshdesk.com"
}
```

## Tawk configuration

Create a webhook in Tawk with:

- URL: `https://t8t.bar/tawk`
- Event: **New Ticket**
- Secret: same value configured as `TAWK_SECRET` in Vercel

The API accepts only signed Tawk requests for POST calls. It processes `ticket:create` events, reads `requester.email`, and creates the Freshdesk ticket using that email as the requester.

## Testing

After configuring Tawk, create a test ticket using a test visitor email. Verify that Freshdesk shows that email as the requester and that replying from Freshdesk sends the reply to the customer.

Keep the old Tawk → Freshdesk forwarding route enabled until this test succeeds. Disable the old route afterward to prevent duplicate tickets.

## Notes

The webhook event ID is included in the Freshdesk ticket description for traceability. Persistent duplicate-event storage is not included yet; if Tawk retries after a network failure, duplicate ticket creation is still possible. A persistent store such as Supabase can be added if needed.
