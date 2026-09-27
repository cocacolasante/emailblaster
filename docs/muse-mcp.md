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
| Daily brief, campaigns + stats, lead search/detail, inbound replies, deals, tasks, team, notifications | Send email or LinkedIn messages |
| Create/update CRM leads, log calls/meetings/notes, create/complete tasks, convert leads, create/move deals, assign owners | Launch or approve a campaign |
| Pause a campaign; resume a paused one | Resume a circuit-breaker pause (human only) |
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
