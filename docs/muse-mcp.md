# Muse / MCP agent access

Muse (Meta's assistant) reaches Email Blaster as a **custom MCP
connector**: a hosted Streamable HTTP endpoint plus a bearer key. Meta
doesn't review custom connectors, so it works as soon as the URL is
reachable and a key is pasted in. Same design as growth-agent's Muse
integration (`~/Projects/socialgrowthagent`, commit `4f9f469`).

## Connect Muse

1. Settings → **Agent access** → create a key ("Muse on my phone"). Copy
   it — it's shown once (keys are stored hashed).
2. In Muse, add a custom connector:
   - URL: `https://outreach.blueprintautomation.tech/mcp`
   - Auth: bearer token = the key.
3. Ask Muse for your "daily brief".

The key acts as **the member who created it** in their workspace ("me"
in owner fields). Revoke it anytime from the same page; it also stops
working if that member leaves the workspace or the workspace is
suspended.

## What it can and can't do

| Can | Can't |
|---|---|
| Daily brief, campaigns + stats, lead search (name / company / exact email), lead detail + notes + activity log, inbound replies, deal search + activity, tasks, team, notifications | Send anything without your approval (see below) |
| Create/update CRM leads, log calls/meetings/notes, create/complete tasks, convert leads, create/move deals, assign owners | Launch or approve a campaign |
| Pause a campaign; resume a paused one | Resume a circuit-breaker pause (human only) |
| Reports: CRM overview, deals, activities; report builder — list fields, run custom reports, run/save/edit saved reports (owner ids shown as names) | Delete a saved report |
| Campaign analytics: counts + rates, open rate by send week (first emails + follow-ups), best subjects, sequence step funnel, deliverability headroom | |
| Add a lead to the ignore list (suppress + halt everywhere) | Un-ignore a lead (dashboard only) |
| Research a lead from a LinkedIn URL, draft an email / LinkedIn DM / connection note, redraft, and send **after your approval** | Send without the confirm step + your explicit approval |
| | Delete anything; manage team, integrations or API keys |

The tool list is pinned by `tests/test_mcp.py` — widening it is a
deliberate edit there.

## How it works

- `POST /mcp` (`app/routers/mcp.py`) — stateless JSON-RPC over Streamable
  HTTP (`initialize`, `tools/list`, `tools/call`, `ping`; JSON responses,
  no SSE; `GET`/`DELETE` → 405). Protocol versions 2025-06-18 /
  2025-03-26 / 2024-11-05.
- Auth: `Authorization: Bearer eb_…`. Keys (`api_keys`, migration 0050)
  are SHA-256 hashed and verified by the SECURITY DEFINER
  `api_key_verify()` — the key identifies the workspace, so the lookup
  can't run inside RLS. The same bearer key also authenticates the normal
  API (`app/auth/deps.py`), but `require_session` / `require_manager`
  refuse keys for key/team/integration/profile management.
- Tools (`app/mcp/tools.py`) call the dashboard's own routes in-process
  with the caller's key, so tenant scoping, RLS, owner validation,
  notifications and audit rows all apply unchanged.

## Public origin (named Cloudflare tunnel)

`outreach.blueprintautomation.tech` → tunnel `outreach` →
`http://backend:8000`, forwarding ONLY `/mcp`, `/webhooks/*`,
`/unsubscribe/*` and `/health` (everything else 404s at Cloudflare's
edge; the dashboard stays on localhost).

```bash
./scripts/named-tunnel-setup.sh     # once (after `cloudflared tunnel login`)
# .env: PUBLIC_ORIGIN=… and WEBHOOK_BASE_URL=https://outreach.blueprintautomation.tech
docker compose -f docker-compose.yml -f docker-compose.named-tunnel.yml up -d
```

Always include `docker-compose.named-tunnel.yml` when bringing the stack
up, or the `cloudflared` container isn't started. The hostname never
changes, so the Muse connector, Unipile webhook URLs and unsubscribe
links stay valid across restarts (unlike ngrok).

## Research a lead → draft → approve → send

Same research + compose as the GUI's "Research a client" page
(`routers/research_client.research_and_compose`), stored as an
`outreach_drafts` row (migration 0051, RLS) so nothing lives only in the
chat:

1. `research_prospect(linkedin_url, goal, channel)` — channel `email`,
   `linkedin_dm` (1st-degree connections) or `linkedin_connect`
   (connection request with a ≤200-char note). **Sends nothing.**
   Pre-fills the recipient from an existing CRM lead or Hunter, and the
   sender from your default inbox / LinkedIn account.
2. `redraft_outreach(feedback)` (reuses the research — no new cost) /
   `edit_outreach_draft` — both void any earlier confirmation.
3. `confirm_outreach` — pins recipient + sender + the exact text; refuses
   ignore-listed recipients and unknown senders; returns a summary and a
   one-time `confirmation_code`. **Sends nothing.**
4. `send_outreach(confirmation_code, user_approved=true)` — only after you
   say "send it". Fails if the draft changed after confirming; sends at
   most once; logs the email / LinkedIn touch to the CRM lead (creating
   one if needed). Marked destructive/open-world so MCP clients that
   honour annotations also ask you before it runs.

Honest limit: the server can require the two steps and the approval flag,
but it can't see your chat — the "ask before sending" behaviour relies on
Muse following the tool instructions (and on Muse's own confirm prompt for
destructive tools). LinkedIn sends need a working Unipile connection and
share the campaigns' daily LinkedIn caps.
