# Email Blaster

AI-powered cold outreach platform — multi-channel (email + LinkedIn), built around per-lead research and Claude-composed personalization.

Upload a CSV → each lead gets researched (web search + optional Apollo + Hunter) → Claude composes a personalized email → reviewed in a sample preview → sent on your schedule with rate limiting + suppression + reply tracking. A visual sequence builder lets you chain email follow-ups, waits, and LinkedIn actions (view, follow, connect, DM, react, comment, page invite, InMail) behind conditional branches.

This README is intentionally exhaustive so it can be fed to an LLM as the single source of truth about the project.

---

## What you get

- **Per-lead research** — Anthropic Claude (Haiku) + web search; optional Apollo.io for company / seniority / employee count; optional Hunter.io for deliverability. Research output cached for 90 days keyed by lowercased email (cuts repeated API spend on overlapping campaigns).
- **AI composition** — subject + body written by Claude Sonnet, tailored to the research. Falls back to a name+company prompt when research is thin. Optional fully-templated mode (no AI, you write the copy with `{{merge_fields}}`).
- **Style-correction feedback loop** — your edits to sample emails become exemplars fed to subsequent composes, so the first few samples teach the model your voice.
- **Pre-send preview** — review N sample emails, edit inline, approve/reject the batch.
- **Reusable campaign signature** — saved on the campaign, swapped in for the AI's sign-off on every email. Bulk-applies to already-composed unsent emails with one click.
- **Scheduled sending** — days-of-week + send window + timezone + min delay between sends + hourly cap + daily cap. All editable on a running/paused campaign; schedule edits automatically re-queue waiting leads under the new window.
- **Compliance** — HMAC-signed CAN-SPAM unsubscribe link in every email; bounce / spam / unsub events auto-populate the suppression list and prevent future sends across all campaigns.
- **Multi-step sequences** — visual DAG builder (React Flow) with email + wait + LinkedIn nodes connected by conditional edges (`replied`, `opened`, `clicked`, `bounced`, `linkedin_connection`, `days_since_entered_node`, plus `and`/`or`/`not` compounds).
- **LinkedIn outreach** — via [Unipile](https://www.unipile.com)'s hosted-Chrome integration (real desktop browser, residential IPs). Eight action kinds: view profile, follow, connect, DM, react to post, comment on post, invite to company page, InMail.
- **Reply tracking** — IMAP polling against your own inbox (Gmail / Outlook / Yahoo / custom). Credentials encrypted at rest with Fernet. Read-only (never marks messages seen in your mailbox).
- **Lite-CRM Leads tab** — global cross-campaign lead view with per-lead notes (editable even after the email has sent). Searchable / filterable by campaign / send status / has-notes.
- **"Research a client" tool** — one-off prospect research generator from a LinkedIn URL (no CSV needed), outputs a draft email OR a LinkedIn DM under a character cap.
- **Social Listening Radar** — type a plain-English topic ("frustrated with our IT provider"), Claude expands it to ~20 LinkedIn search phrases, Anthropic web search finds matching public posts, each post is scored 1-10 for buying intent + categorized, and a suggested comment + connection request + follow-up DM is drafted for each. All LinkedIn writes stay manual — the system never auto-posts. Per-search frequency (manual / 6h / 12h / daily / weekly) and soft cost caps per run.
- **Analytics** — open / click / reply / bounce / spam / unsub rates, sender reputation score (0–100), research-quality breakdown (rich/partial/generic open rates), best subject lines, per-step funnel, timeline chart, per-lead activity drilldown.

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI (Python 3.12) · Celery 5 · async SQLAlchemy 2 |
| Datastore | Postgres 15 · Redis 7 |
| AI compose | Anthropic Claude Sonnet 4.6 (`ANTHROPIC_MODEL`, default `claude-sonnet-4-6`) |
| AI research | Anthropic Claude Haiku 4.5 (`ANTHROPIC_RESEARCH_MODEL`, default `claude-haiku-4-5-20251001`) — cheaper, extraction-only |
| Email send | Brevo transactional API |
| Email events | Brevo polled (`GET /v3/smtp/statistics/events`) — no public webhook required |
| Enrichment | Apollo.io + Hunter.io *(both optional)* |
| Reply tracking | Python stdlib IMAP poller, Fernet-encrypted credentials |
| LinkedIn | Unipile hosted-API (real desktop Chrome on residential IPs) |
| Frontend | React 18 · Vite 6 · TanStack Query · `@xyflow/react` · Recharts |
| Infra | docker-compose: postgres, redis, backend, worker, beat, frontend |

---

## Architecture

### Service topology

`docker-compose.yml` runs six containers, all bound to `127.0.0.1`:

| Container | Process | Role |
|---|---|---|
| `postgres` | Postgres 15 | Source of truth for campaigns / leads / sequences / events |
| `redis` | Redis 7 | Celery broker + result backend; rate-limit counters; min-delay atomic gate; LinkedIn stagger keys |
| `backend` | `uvicorn app.main:app` | FastAPI HTTP server (port 8000) |
| `worker` | `celery -A app.workers worker` | Task executor (prefork pool, 8 by default) |
| `beat` | `celery -A app.workers beat` | Schedules recurring tasks (see beat schedule below) |
| `frontend` | `vite dev` | React app on port 5173 |

### Beat schedule

| Task | Cadence | What it does |
|---|---|---|
| `sequencer.advance_sequences` | every 60s | Walks every running campaign's `lead_sequence_state` cursors, dispatches due steps |
| `reply_poller.poll_all_replies` | `IMAP_POLL_INTERVAL_MINUTES` (default 20) | Polls every connected inbox for replies via IMAP `BODY.PEEK` (read-only) |
| `linkedin_poller.poll_all` | `LINKEDIN_POLL_INTERVAL_MINUTES` (default 30) | Polling fallback for Unipile webhook misses (inbound DMs, accepted invites) |
| `brevo_events_poller.poll` | `BREVO_EVENTS_POLL_INTERVAL_MINUTES` (default 10) | Pulls `delivered`/`opened`/`clicked`/`bounced`/`spam`/`unsubscribed` from Brevo's events API |
| `lead_sweeper.sweep_stale` | every 5min | Resets `compose_status`/`research_status` rows stuck in RUNNING > 15min back to PENDING + re-enqueues |
| `social_listening.scheduled_runner` | every 60s | Dispatcher — selects active+non-manual Social Radar searches whose `next_run_at <= now`, enqueues `run_social_search` for each (sequencer-pattern, per-search frequency) |

### Per-lead pipeline (legacy first-email path)

```
CSV upload  →  ingest  →  research  →  compose  →  send_lead  →  Brevo API
                  │           │            │             │
                  │           │            │             └─ writes EmailEvent(SENT)
                  │           │            └─ writes lead.composed_subject/body, enqueues send_lead
                  │           └─ writes lead.research_data (and caches it for 90d)
                  └─ writes Lead rows from the CSV, kicks off research
```

Each stage is a separate Celery task. The legacy path is the **source of truth for the first email** when the sequence entry node is an email node — gated via `sequence_service.campaign_sends_legacy_first_email`. When a sequence starts with a non-email node (LinkedIn connect, wait, etc.), `compose_lead_async` skips the AI call and `confirm_upload` auto-launches into RUNNING with no preview step.

### Sequence engine (follow-ups, branching, LinkedIn)

- **Tables:** `sequences`, `sequence_nodes`, `sequence_edges`, `lead_sequence_states`, `lead_step_executions`.
- **Node kinds:** `email`, `wait`, `linkedin_view_profile`, `linkedin_follow_profile`, `linkedin_react_post`, `linkedin_comment_post`, `linkedin_connect`, `linkedin_dm`, `linkedin_inmail`, `linkedin_invite_to_page`.
- **Conditional edges** — JSON expression language in `services/sequence_conditions.py`. Leaf ops: `always`, `replied`, `opened`, `clicked`, `bounced`, `linkedin_connection` (value: `connected` / `invited` / `unknown` / `declined`), `days_since_entered_node`. Compounds: `and` / `or` / `not`.
- **Sequencer beat task** (`workers/sequencer.py`) — every 60s, selects `LeadSequenceState` rows where `next_run_at <= now` AND `campaign.status == RUNNING`. For each, evaluates outgoing edges from `current_node_id`, advances cursor or dispatches the next step's channel handler.
- **Dispatch handlers:**
  - `_send_email_step_async` — follow-up email. Honors the same gates as `send_lead`.
  - `_send_linkedin_step_async` — every LinkedIn kind. Uses Unipile + per-account rate limits.
- **Async-event parking** — when an edge condition isn't currently true but uses a "deferrable" op (replied / opened / clicked / linkedin_connection / days_since_entered_node), the lead is parked on the current node and re-evaluated every 30min for up to 14 days. Lets `linkedin_connect → DM (when linkedin_connection=connected)` wait for the invite to be accepted.
- **Lifetime per-node idempotency** — `_already_executed_ever()` short-circuits any node that's ever produced a SENT execution row for a lead. Multi-touch ("view profile 3x") must be modeled as separate nodes.

### LinkedIn engine

- **Provider:** `services/linkedin/unipile_impl.py` — async httpx wrapper around Unipile's REST API.
- **Per-account rate limits** (Redis Lua, atomic):
  - `LINKEDIN_DAILY_ACTION_CAP` (default 20) — total writeable actions / day
  - `LINKEDIN_MIN_ACTION_DELAY_SECONDS` (default 30) — gap between actions
  - `LINKEDIN_DAILY_CONNECT_CAP` (20) — connect requests / day
  - `LINKEDIN_DAILY_DM_CAP` (30) — DMs / day (InMail counts here too)
  - `LINKEDIN_MONTHLY_PAGE_INVITE_CAP` (250 per **page**, not per account)
- **Stagger** — at most one LinkedIn action per account per `LINKEDIN_STAGGER_SECONDS` (default 120). Beat releases one due lead per account per interval; others get parked on a future slot. `view_profile` is exempt (low-risk read).
- **Calendar-day caps** — daily counters expire at next local midnight in the campaign's tz (not a rolling 24h).
- **Auto-pause** — when a LinkedIn step hits a daily cap AND no email node is reachable downstream, the campaign is paused with `auto_paused_until = cap reset time`. The beat auto-resumes at that time. Manual pauses (`auto_paused_until IS NULL`) stay paused.
- **Hosted-auth flow** — `POST /linkedin-accounts/connect-via-unipile` returns a Unipile-hosted login URL. User completes LinkedIn login in Unipile's browser. Unipile fires `account.connected` webhook → our handler flips the local row to OK. 3s polling fallback for missed webhooks.

### Rate / pause semantics (worth knowing)

- **Email min-delay gate** — atomic `SET rate:{cid}:min_gate <now> NX EX min_delay` on Redis. First to set claims the window; rest get TTL back as `retry_in`. Stops Celery prefork (N tasks at once) from bulk-sending.
- **Pause is a hard stop** — when a queued `send_lead` task fires for a paused campaign, the gate returns `paused` and the Celery wrapper acks-and-drops (no self-re-enqueue). Beat already filters paused campaigns out for sequencer-driven steps. Resume re-enqueues every composed PENDING+SCHEDULED lead via staggered `send_lead.apply_async(eta=...)`.
- **Schedule edits are live** — schedule + throughput fields are edit-on-any-status. A schedule change on a running/paused campaign re-queues every composed PENDING+SCHEDULED lead so they pick up the new window immediately. Content fields (goal, tone, sender_*, research_mode, templates) are still 409-gated to draft/previewing (changing voice mid-flight would split it across sent/unsent).
- **Lead sweeper** — every 5min, flips `compose_status`/`research_status` rows stuck in RUNNING >15min back to PENDING and re-enqueues. Catches "worker crashed between RUNNING commit and final commit."

---

## Setup

### Prerequisites

| Required | Used for |
|---|---|
| Docker Desktop | Run the stack |
| **Anthropic API key** — https://console.anthropic.com | Research + compose |
| **Brevo account + API key** — https://app.brevo.com/settings/keys/api | Transactional sends + event polling |

| Optional | Used for |
|---|---|
| Apollo.io API key | Richer company/seniority enrichment in "Deep" research mode |
| Hunter.io API key | Email verification before sends |
| An inbox you control (Gmail / Outlook / Yahoo / custom) | Reply tracking |
| Unipile account + a public tunnel (ngrok/cloudflared) | LinkedIn outreach |

### 1. Clone + env file

```bash
git clone <repo-url> emailblaster
cd emailblaster
cp backend/.env.example .env
```

### 2. Generate the encryption key

This key encrypts stored inbox passwords (Fernet AES-128-CBC + HMAC-SHA256). **If you lose it, all saved inbox passwords are unrecoverable.** Back it up like a DB password.

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the 44-character output into `.env` as `ENCRYPTION_KEY=...`.

### 3. Fill in `.env`

Minimum required to send mail:

```env
ANTHROPIC_API_KEY=sk-ant-...
BREVO_API_KEY=xkeysib-...
BREVO_SENDER_EMAIL=you@yourdomain.com
BREVO_SENDER_NAME=Your Name
ENCRYPTION_KEY=<44-char Fernet key from step 2>
SECRET_KEY=<random 32+ chars — used for HMAC-signed unsubscribe tokens>
```

Optional:

```env
# Enrichment (degrades gracefully without these)
APOLLO_API_KEY=...
HUNTER_API_KEY=...

# Reply tracking IMAP poll cadence
IMAP_POLL_INTERVAL_MINUTES=20

# Brevo event polling cadence
BREVO_EVENTS_POLL_INTERVAL_MINUTES=10

# Anthropic model overrides
ANTHROPIC_MODEL=claude-sonnet-4-6
ANTHROPIC_RESEARCH_MODEL=claude-haiku-4-5-20251001
RESEARCH_WEB_SEARCH_MAX_USES=3
# Social Listening Radar discovery (each query is one Anthropic call
# that ingests web-search results — keep these on Haiku unless you
# really need the quality lift, Sonnet here costs ~4x more)
ANTHROPIC_SOCIAL_DISCOVERY_MODEL=claude-haiku-4-5-20251001
SOCIAL_DISCOVERY_WEB_SEARCH_MAX_USES=2
RESEARCH_CACHE_TTL_DAYS=90

# Public URL for the unsubscribe link + Unipile webhooks (set when you tunnel)
WEBHOOK_BASE_URL=https://your-tunnel.ngrok-free.app

# LinkedIn (Unipile) — see "LinkedIn setup" below
UNIPILE_DSN=api12.unipile.com:13443
UNIPILE_API_KEY=...
UNIPILE_WEBHOOK_SECRET=...
UNIPILE_WEBHOOK_AUTH_HEADER=X-Unipile-Auth

# LinkedIn rate caps
LINKEDIN_DAILY_ACTION_CAP=20
LINKEDIN_MIN_ACTION_DELAY_SECONDS=30
LINKEDIN_STAGGER_SECONDS=120
LINKEDIN_DAILY_CONNECT_CAP=20
LINKEDIN_DAILY_DM_CAP=30
LINKEDIN_MONTHLY_PAGE_INVITE_CAP=250
LINKEDIN_POLL_INTERVAL_MINUTES=30
```

Anything left blank degrades gracefully — the app still runs, with less data.

### 4. Start everything

```bash
docker compose up --build -d
docker compose exec backend alembic upgrade head
```

This brings up postgres + redis + backend + worker + beat + frontend, and applies all 14 migrations.

When it's done:

- **App UI** — http://localhost:5173
- **API** — http://localhost:8000
- **OpenAPI docs** — http://localhost:8000/docs

### 5. Brevo events — no webhook needed

Events are **polled** from `GET /v3/smtp/statistics/events` every `BREVO_EVENTS_POLL_INTERVAL_MINUTES` (default 10). No need to configure an inbound webhook in the Brevo dashboard, no public tunnel required.

Trade-off: up to 10 min lag from event-at-Brevo to event-row-in-DB. Free with the regular API key, works behind the localhost-only port bindings. Per-event dedup is built into the poller.

### 6. LinkedIn (Unipile) setup *(optional)*

Required for any LinkedIn action (view / follow / connect / DM / etc.). If you only need email, skip this section.

#### One-time Unipile signup

1. Sign up at https://www.unipile.com.
2. Top-bar **DSN** like `api12.unipile.com:13443` — copy it.
3. Sidebar **Access Tokens → Generate** — copy the token (only shown once).

#### Public tunnel (required for Unipile to reach us)

Unipile's servers can't reach `localhost`. Two options:

```bash
# ngrok (recommended — has a request inspector at http://127.0.0.1:4040)
ngrok http 8000

# cloudflared (no signup)
cloudflared tunnel --url http://localhost:8000
```

⚠ **Free tunnels rotate URLs on every restart.** Re-run `scripts/dev_tunnel.py` (described below) whenever the URL changes.

#### Quick-refresh script

```bash
python3 scripts/dev_tunnel.py
```

Detects (or starts) ngrok pointing at `localhost:8000`, deletes every Unipile webhook on the workspace, recreates the three canonical ones (`messaging`, `account_status`, `users`) pointing at the live tunnel, patches `.env` with `WEBHOOK_BASE_URL` (and optionally rotates `UNIPILE_WEBHOOK_SECRET` with `--rotate-secret`), and force-recreates `backend`/`worker`/`beat` if `.env` changed. Pure stdlib, no `pip install`. Useful flags: `--dry-run`, `--rotate-secret`, `--no-recreate`, `--port N`.

#### Manual webhook creation (if you'd rather)

```bash
DSN=$(grep '^UNIPILE_DSN=' .env | cut -d'=' -f2-)
KEY=$(grep '^UNIPILE_API_KEY=' .env | cut -d'=' -f2-)
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
TUNNEL=https://<your-tunnel-host>

for SRC in messaging account_status users; do
  curl -s -X POST "https://$DSN/api/v1/webhooks" \
    -H "X-API-KEY: $KEY" -H "content-type: application/json" \
    -d "{
      \"name\": \"emailblaster - $SRC\",
      \"request_url\": \"$TUNNEL/webhooks/unipile\",
      \"source\": \"$SRC\",
      \"headers\": [
        {\"key\": \"Content-Type\", \"value\": \"application/json\"},
        {\"key\": \"X-Unipile-Auth\", \"value\": \"$SECRET\"}
      ]
    }"
done
echo "$SECRET"   # paste into .env as UNIPILE_WEBHOOK_SECRET
```

Then update `.env`:

```env
UNIPILE_DSN=api12.unipile.com:13443
UNIPILE_API_KEY=<token>
UNIPILE_WEBHOOK_SECRET=<the secret you just generated>
WEBHOOK_BASE_URL=<tunnel host, no trailing slash>
```

And force-recreate (plain `restart` doesn't re-read `.env`):

```bash
docker compose up -d --force-recreate backend worker beat
```

#### Connect a LinkedIn account

1. UI → **Settings → LinkedIn accounts → Connect new**.
2. Enter a label, click **Connect via Unipile**.
3. Unipile-hosted login opens in a new tab.
4. Complete LinkedIn login. Webhook flips the local row to OK (3s polling fallback for misses).

---

## Using the app

### Connect an inbox *(optional — required only for reply tracking)*

Sidebar → **Settings → Connected inboxes → + Connect inbox**.

#### Gmail
- Host: `imap.gmail.com`, port 993, SSL on (auto-filled by the preset).
- **You need an App Password — not your regular Google password.** Gmail blocks regular passwords for IMAP.
  1. Enable 2-Step Verification on your Google account first
  2. Go to https://myaccount.google.com/apppasswords
  3. Create a new app password labeled "Email Blaster"
  4. Paste the 16-character app password into the form
- **Gmail aliases share their parent mailbox.** Set `email_address` to the alias, `username` to the primary mailbox.

#### Outlook / Office 365
- Host: `outlook.office365.com`, port 993, SSL on. Use your regular password, or an app password if MFA is enabled.

#### Yahoo
- Host: `imap.mail.yahoo.com`, port 993, SSL on. Generate an app password from Yahoo Account Security.

#### Any other IMAP server
- Use the **Custom** preset; fill in your provider's host/port/SSL settings.

A green **Connected** badge means it worked. Red **Failed** surfaces the IMAP error inline (usually auth).

### Create a campaign

Sidebar → **Campaigns → + New campaign**. The 4-step wizard:

#### Step 1 — Details

- **Name**, **Goal** (e.g. "Book a 30-minute discovery call"), **Tone** (Professional / Friendly / Direct / Conversational / Formal)
- **Sender name + email** — must be a verified sender in Brevo
- **Sample count** — how many emails to review before launch (default 5)
- **Research mode**
  - **Fast** — one Anthropic+web-search call per lead (~10s, `max_uses=3`), ~$0.04/lead
  - **Deep** — same + Apollo enrichment (~45s, requires `APOLLO_API_KEY`), ~$0.06/lead
  - **None** — no research; AI composes from name + company alone, ~$0.005/lead
  - **Template** — no AI; you write the copy with `{{first_name}}` / `{{company}}` merge fields, $0
- **Reply tracking** *(optional)* — pick a connected inbox or leave on "No reply tracking"
- **Schedule** — days-of-week pills, start/end time, timezone, optional max/hour and max/day caps, min delay between sends
- **Signature** *(optional)* — saved on the campaign, swapped in for the AI's sign-off on every email. Bulk-applies to composed unsent emails.

#### Step 2 — Upload leads

Drop a CSV. The app detects columns + suggests mapping. **One column must map to `email`** — that's the only required field. Supported: `first_name`, `last_name`, `company`, `job_title`, `linkedin_url`, `phone`, `company_website`. Unmapped columns are preserved in `lead.raw` for audit but not used in composition.

The system:
- Lowercases all emails, deduplicates within the CSV
- Skips any email in the suppression list (previous unsubs/bounces)
- Picks `sample_count` leads spread evenly across the deduped list as samples
- Kicks off background research for every lead (or skips it for `none`/`template` modes)

#### Step 3 — Live progress

Mode-aware progress screen:
- Fast/Deep → "Researching and composing emails… X of Y composed · Z researched"
- None → "Composing emails… X of Y composed"
- Template → "Rendering your templated emails… X of Y rendered"

When all samples are composed, the wizard auto-advances to the preview page. If the sequence entry node isn't an email (e.g. starts with a LinkedIn connect), it auto-launches into RUNNING and skips preview.

#### Step 4 — Review samples

Each sample card shows:
- Lead name, title, company
- Research quality badge (Rich / Partial / Generic)
- Expandable research summary — exactly what the system found
- Composed subject + body (both editable inline)
- Approve / Reject buttons

Edits to the body auto-save on blur and become **style corrections** fed to subsequent composes — the first samples teach the composer your voice.

When you're happy:
- **Approve all and launch** → status flips to RUNNING. Every composed lead is dispatched with `send_lead.apply_async(eta=base + i*min_delay)` so sends are pre-spaced. Remaining leads continue researching/composing; sends auto-trigger as they finish.
- **Reject and reconfigure** → status flips to DRAFT, all composed bodies cleared. Fix the campaign and start again.

### Monitor / control a campaign

#### Campaign list
Cards: name · status · sent/total progress · open/click/reply rates · created date · View / Pause / Resume / Delete.

#### Campaign detail page (4 tabs)

- **Overview**
  - Pipeline card with live progress (composed / researched / sent counts, ETA)
  - Status panel with Pause/Resume (and auto-paused note if a LinkedIn cap was hit)
  - Quick stats (open / click / reply / bounce rates)
  - Reply tracking info
  - LinkedIn account binding
  - **Schedule & pacing editor** — read-only summary by default; "Edit" toggles a form with day chips, time pickers, timezone dropdown, min_delay / max_per_hour / max_per_day. Editable on any status; schedule changes auto re-queue waiting leads.
  - Campaign config summary (goal / tone / sender / research mode / sample count)
- **Sequence** — the React-Flow DAG builder (see below)
- **Activity** — clustered execution log: 30 most recent `(lead, node)` clusters with `×N attempt count`, earliest+latest attempt time tooltip
- **Leads** — paginated table, search by name/email, filter by send status. Per-row "View email" / Edit composed / **Delete** (cascades the lead + history). The signature editor lives here too.
- **Analytics** — refreshes every 30s
  - Sent / Delivered / Open / Click / Reply / Bounce / Spam / Unsub counts + rates
  - Timeline chart (Recharts) — opens, clicks, replies
  - **Sender reputation score** 0–100 (green ≥80, amber 50–79, red <50)
  - Research-quality breakdown — open rate per research tier
  - Best subject lines (min 5 sends to qualify)
  - Failed-leads banner with single-click **Retry failed**

#### Pause / resume

- **Pause** → campaign frozen in place. All in-flight `send_lead` tasks that fire after pause hit the gate and **acks-and-drops** (no self-re-enqueue). The sequencer beat skips paused campaigns entirely. LinkedIn stagger slots are released for other running campaigns.
- **Resume** → status flips to RUNNING and every composed PENDING+SCHEDULED lead is staggered-dispatched (`apply_async eta`) so the queue gets repopulated without you needing to nudge anything.

### Sequences

Sidebar → campaign **→ Sequence tab**. Built on React Flow:

- Drag nodes from the palette: email, wait, or any LinkedIn kind
- Connect them with edges
- Click a node to edit its config (subject/body for email, duration for wait, action params for LinkedIn)
- Click an edge to set a condition (JSON expression with `op`, leaf args, optional `not`/`and`/`or` wrappers)
- Pick the entry node (any kind — entry no longer has to be an email)
- **Publish** validates the graph (cycles rejected, undefined node refs rejected, LinkedIn kinds gated to `PUBLISHABLE_KINDS` whitelist) and stamps a new `published_at`

When published, every active lead in the campaign gets re-enrolled at the entry node. Old node rows aren't deleted — they're soft-deleted (`deleted_at` stamped) so historical `lead_step_executions` remain queryable.

### Lite-CRM Leads tab (global)

Sidebar → **Leads**. Cross-campaign view of every lead. Filter by:
- Campaign dropdown
- Send status pill
- Search (email / name / company)
- Has-notes toggle

Click a row → opens a modal with:
- Composed email preview (read-only)
- **Notes** textarea — saves via `PATCH /campaigns/{cid}/leads/{lid}` with `{notes}`. Notes are editable **even after the email has sent** (only composed_subject/body are locked when SENT).

### Research a client

Sidebar → **Research a client**. One-off prospect research from a LinkedIn URL. Two modes:

- **Fast** (~10s) — single Anthropic+web-search call, `max_uses=3`
- **Deep** (~30-45s) — `max_uses=8` + richer prompt

Pick output kind (email subject+body, or LinkedIn DM body-only) and a char limit. Char limit is enforced both in the prompt AND by a post-truncate that prefers sentence boundaries.

**No LinkedIn views fire from this tool** — it's pure Anthropic + web search, so it doesn't surface in the prospect's "who viewed your profile" feed.

### Social Listening Radar

Sidebar → **Social Radar**.  An "intent feed" — discover LinkedIn posts where someone is venting about a vendor, asking for tech recommendations, or otherwise signaling buying intent, then surface them with AI-drafted suggested responses you manually approve before posting.

**Two tabs:**

- **Feed** — scored opportunity cards. Each card shows the post author + headline, the post text (truncatable), a 1-10 score (color-coded: red 1-3, amber 4-6, green 7-10), category pill (UCaaS / cybersecurity / MSP / nonprofit tech / …), buying-signal flag, AI pain-summary + qualification-reason, and **Copy comment** / **Copy connect msg** / **Copy follow-up** buttons. A per-row status dropdown lets you mark each lead as `new` / `saved` / `commented` / `connected` / `replied` / `not_relevant` / `archived`.
- **Searches** — table of every configured search with last-run time, post count, opportunity count. Per-row **Run now** + **Delete**. Click a row name to edit. **+ New search** opens the editor modal.

**Editor modal** (create OR edit) fields:

- Name, topic (plain English: *"frustrated with our IT provider"*)
- Niche / audience, geography
- Include / exclude keywords (comma-separated)
- Frequency: `manual` / `every_6h` / `every_12h` / `daily` / `weekly`
- Status, tone, sender name (used in the suggested copy)
- "Preview queries" button — runs the AI expansion synchronously and renders the resulting chips so you can see what Claude generated BEFORE saving. Useful for tuning the topic + include/exclude.
- "Advanced" disclosure exposes the soft cost caps: `max_queries_per_run` (default 20), `max_posts_per_query` (30), `max_qualified_per_run` (100).

**How it works under the hood:**

1. User creates a search.  Backend immediately enqueues `expand_social_topic` (1 Anthropic call, Haiku) which writes ~15-30 expanded LinkedIn search phrases to the search row's `expanded_queries` JSONB.
2. On a manual **Run now** or a beat-driven scheduled tick (`scheduled_runner` selects rows where `status=active AND frequency!=manual AND next_run_at<=now AND last_run_status!=running`), `run_social_search` orchestrates:
   - For each expanded query (concurrency-capped to 4), 1 Anthropic call with `web_search_20250305` tool (Sonnet — discovery quality matters) finds public LinkedIn posts → `DiscoveredPost` dataclass.
   - Each post is upserted into `social_listening_posts` via `ON CONFLICT (provider, post_url) DO NOTHING` — same post discovered in a later run is a no-op.
   - **Only NEW posts** enqueue `qualify_social_post` (capped at `max_qualified_per_run`).
3. `qualify_social_post` makes 1 Anthropic call per post (Haiku — cheap, runs N times) returning strict JSON: score, category, buying_signal, pain_summary, qualification_reason, suggested_comment (≤500 chars, truncated at sentence boundary), suggested_connection_request (≤280 chars), suggested_follow_up (≤600 chars), recommended_action. Upserted into `social_listening_opportunities` keyed on `post_id` — **re-qualification overwrites AI fields but preserves user-set `status` and `notes`**.
4. `next_run_at` is set to `now + frequency_interval` (or `NULL` for manual). The beat picks it up on the next tick when due.

**Cost (per-run, default caps):** 1 expansion + ~20 discovery + ~100 qualification ≈ $0.50-$1 of Anthropic spend.

**Discovery via Anthropic, NOT Unipile.**  Unipile's API has no LinkedIn post search and their raw Voyager passthrough is on a narrow allowlist (`feed/dash/followingStates` allowed for follow; post search isn't). Anthropic web search finds publicly indexable posts.  Trade-off: results limited to what's been crawled by search engines (some recent posts may not appear); upside: works today without a Unipile support ticket.

**LinkedIn writes stay manual.**  This entire feature only **drafts** copy.  Nothing in `app/workers/social_listening.py` ever calls a Unipile write endpoint.  The Copy buttons go to your clipboard so you paste manually on LinkedIn.

---

## Project layout

```
emailblaster/
├── backend/
│   ├── app/
│   │   ├── main.py                  FastAPI app + CORS middleware + 500-handler with CORS
│   │   ├── config.py                pydantic-settings (all env vars)
│   │   ├── database.py              Async SQLAlchemy engine + get_db + AsyncSessionLocal
│   │   ├── models/                  13 ORM models
│   │   │   ├── campaign.py          Campaign + CampaignStatus + ResearchMode enums
│   │   │   ├── connected_account.py Inbox credential record (Fernet-encrypted password)
│   │   │   ├── email_event.py       sent / delivered / opened / clicked / replied / bounced / spam / unsub
│   │   │   ├── lead.py              Lead + ResearchStatus / ComposeStatus / SendStatus / LinkedInConnectionStatus enums
│   │   │   ├── linkedin_account.py  Unipile-bound LinkedIn account row
│   │   │   ├── research_cache.py    Email-keyed JSONB research cache (90d TTL)
│   │   │   ├── social_listening.py  3 models: SocialListeningSearch + Post + Opportunity
│   │   │   ├── sequence.py          Sequence + SequenceNode + SequenceEdge + LeadSequenceState + LeadStepExecution
│   │   │   ├── style_correction.py  User edits to sample emails (fed back into compose prompt)
│   │   │   ├── suppression.py       Per-email suppression list (auto-populated by bounces / spam / unsub)
│   │   │   └── webhook_event.py     Idempotency table for Unipile webhook deliveries
│   │   ├── schemas/                 Pydantic request/response shapes
│   │   ├── routers/
│   │   │   ├── analytics.py         GET /campaigns/{id}/analytics + activity
│   │   │   ├── campaigns.py         CRUD + pause/resume + retry-failed + signature + leads list + delete
│   │   │   ├── connected_accounts.py Inbox CRUD + test connection
│   │   │   ├── leads.py             Upload preview + confirm-upload + global GET /leads
│   │   │   ├── linkedin_accounts.py LinkedIn account CRUD + Unipile hosted-auth + discoverable / import
│   │   │   ├── preview.py           Sample preview + approve-all + reject
│   │   │   ├── research_client.py   POST /research-client (one-off research tool)
│   │   │   ├── sequences.py         GET / replace / publish sequence graphs
│   │   │   ├── social_radar.py      Searches CRUD + opportunities feed + expand-preview
│   │   │   ├── settings.py          App-level settings exposure
│   │   │   └── webhooks.py          /webhooks/unipile (Brevo events are polled, no inbound webhook)
│   │   ├── services/
│   │   │   ├── encryption.py        Fernet wrapper. ONLY consumer is imap_client.py (grep-enforced)
│   │   │   ├── imap_client.py       Stdlib IMAP wrapper; read-only fetch via BODY.PEEK
│   │   │   ├── brevo.py             Brevo send wrapper
│   │   │   ├── brevo_events.py      Event-row writer + per-event dedup
│   │   │   ├── web_research.py      Anthropic+web-search merged person+company research
│   │   │   ├── apollo.py            Apollo.io enrichment client
│   │   │   ├── hunter.py            Hunter.io email verification client
│   │   │   ├── research_cache.py    lookup / upsert helpers
│   │   │   ├── research_client.py   One-off research generator (Anthropic-only, no Unipile)
│   │   │   ├── social_listening_topic_expander.py  Haiku expansion (1 call → 15-30 phrases)
│   │   │   ├── social_listening_discovery.py       Sonnet+web_search → list of DiscoveredPost
│   │   │   ├── social_listening_qualifier.py       Haiku per-post strict-JSON scorer + drafter
│   │   │   ├── _anthropic.py        Shared get_client/extract_text/parse_json helpers
│   │   │   ├── compose_client.py    One-off compose with char-limit enforcement
│   │   │   ├── csv_parser.py        CSV column detection + auto-mapping
│   │   │   ├── email_template.py    HTML+text rendering, unsubscribe link injection
│   │   │   ├── template_render.py   {{merge_field}} substitution for template-mode campaigns
│   │   │   ├── signature.py         Sign-off detection + replacement
│   │   │   ├── sequence_conditions.py JSON expression language + evaluator + DAG cycle detector
│   │   │   ├── sequence_service.py  ensure_default_sequence, enroll_leads, validate_graph, replace_graph
│   │   │   └── linkedin/            Unipile provider
│   │   │       ├── base.py          LinkedInProvider ABC + ProfileRef + ActionResult + ChallengeRequired + AccountRestricted types
│   │   │       └── unipile_impl.py  The only concrete impl (Unipile REST wrapper)
│   │   └── workers/
│   │       ├── celery_app.py        Celery app + beat schedule
│   │       ├── ingest.py            CSV row → Lead row
│   │       ├── research.py          Cache lookup → Apollo/Hunter/web fan-out → cache upsert → enqueue compose
│   │       ├── compose.py           Anthropic call → composed_subject/body → enqueue send_lead (when entry is email)
│   │       ├── send.py              Brevo send + suppression check + atomic rate gate + schedule window
│   │       ├── sequencer.py         Beat task that walks lead_sequence_states; dispatches email + LinkedIn step handlers
│   │       ├── reply_poller.py      IMAP poll + match via Message-ID / In-Reply-To / References / subject+from
│   │       ├── linkedin_poller.py   Unipile webhook fallback (inbox events, accepted invites)
│   │       ├── brevo_events_poller.py Polls Brevo events API every 10min
│   │       ├── social_listening.py  4 tasks: expand_topic, run_search, qualify_post, scheduled_runner
│   │       └── lead_sweeper.py      Resets stale RUNNING rows to PENDING and re-enqueues
│   ├── alembic/versions/            14 migrations (0001 initial → 0014 social listening)
│   ├── scripts/                     One-off remediation scripts (see below)
│   └── tests/                       610 backend tests
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Campaigns.jsx        Campaign list
│       │   ├── CampaignCreate.jsx   4-step wizard
│       │   ├── CampaignDetail.jsx   Overview / Sequence / Activity / Leads / Analytics tabs
│       │   ├── Preview.jsx          Sample review + approve/reject
│       │   ├── SequenceBuilder.jsx  React-Flow DAG editor
│       │   ├── Analytics.jsx        Full-page analytics view
│       │   ├── Leads.jsx            Global cross-campaign leads + notes modal
│       │   ├── ResearchClient.jsx   One-off research tool
│       │   ├── SocialRadar.jsx      Feed + Searches tabs + editor modal (Social Listening Radar)
│       │   └── Settings.jsx         Inboxes + LinkedIn accounts
│       ├── components/              Nav, Toast, ErrorBoundary, EmailPreviewCard, LeadTable, LeadUpload,
│       │                            ScheduleConfig, MetricsGrid, ConnectInboxModal, ConnectLinkedInModal,
│       │                            SignatureEditor (inline in CampaignDetail)
│       └── api/                     axios wrappers per resource
├── docker-compose.yml               6 services, all bound to 127.0.0.1
├── scripts/dev_tunnel.py            ngrok + Unipile webhook resync (pure stdlib)
├── docs/
│   ├── outline.md                   Original product outline
│   └── roadmap.md                   Phase 1.5 milestones (M1-M5)
├── CLAUDE.md                        Running session context for Claude/Codex assistants
└── README.md                        This file
```

### One-off scripts (`backend/scripts/`)

| Script | When to use |
|---|---|
| `backfill_research_cache.py` | Push existing per-lead `research_data` into the cross-campaign cache table. Useful after first deploying the cache. Preserves each lead's `updated_at` as `refreshed_at` so old data correctly falls past the TTL. `--dry-run` supported. |
| `reenqueue_stuck_scheduled.py <campaign_id>` | One-off remediation for leads orphaned in `send_status=SCHEDULED` with `scheduled_send_at` in the past. Stagger-dispatches them. `--all` for every running/paused campaign. |
| `reconcile_linkedin_connections.py` | Pulls `GET /api/v1/users/relations` from Unipile and flips matching INVITED/UNKNOWN leads to CONNECTED. Useful after webhook drops. |
| `unmark_seen_replies.py` | Historical remediation: the old IMAP poller used to mark messages as read. This flips them back to unread. |
| `test_unipile_endpoints.py` / `test_unipile_post_actions.py` | Ad-hoc smoke tests against the live Unipile API. |

---

## Running tests

```bash
# Backend (739 tests; spins up postgres if not already running)
docker compose run --rm backend pytest

# Frontend (251 tests; pure jsdom, no services needed)
docker compose exec frontend npm test --run

# Quick: one specific file
docker compose run --rm backend pytest tests/test_phase4_campaigns.py -v
```

Test DB has a `truncate every table between tests` fixture in `tests/conftest.py`. **Update the truncate list when you add a new table.**

LinkedIn rate-limit tests must monkeypatch `sequencer._LI_REDIS_CLIENT=None` per-test because the global is cached at module scope. `test_phase18_linkedin_write.py` has an `autouse` fixture that does this — copy that pattern in any new LinkedIn-touching test file.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ENCRYPTION_KEY is not configured` | Setup step 2 skipped | Generate a Fernet key, paste into `.env`, `docker compose up -d --force-recreate backend worker beat` |
| Migrations fail / DB looks stale | Schema drift | `docker compose down --volumes` *(⚠️ wipes all campaign data)*, then `up postgres -d`, then `alembic upgrade head` |
| Gmail "authentication failed" | Using regular password | Use a 16-char **App Password** with 2-Step Verification enabled. Some Workspace orgs disable IMAP — ask your admin |
| Brevo "sender not authorized" | Sender not added in Brevo | Add `BREVO_SENDER_EMAIL` in Brevo dashboard → **Senders & IP** |
| Webhook events not appearing | Brevo events poller is async (up to 10min lag) | Wait. If still missing, check `docker compose logs worker \| grep brevo_events` |
| `socket.gaierror: Name or service not known` in backend logs | Docker network detached postgres after Desktop restart | `docker network connect --alias postgres emailblaster_default emailblaster-postgres-1` |
| Worker logs `ModuleNotFoundError` after editing `requirements.txt` | `docker compose build backend` doesn't rebuild worker/beat | `docker compose build backend worker beat` |
| Config change in `.env` not taking effect | `restart` doesn't re-read `.env` | `docker compose up -d --force-recreate backend worker beat`. Verify with `docker compose exec backend printenv VAR_NAME` |
| New `.env` var doesn't reach containers | Not in compose `environment:` allowlist | Add `FOO: ${FOO:-default}` to backend/worker/beat blocks in `docker-compose.yml`, force-recreate |
| `401 invalid auth header` after Unipile send-test | `UNIPILE_WEBHOOK_SECRET` mismatch | Re-copy from Unipile webhook config OR rotate via `scripts/dev_tunnel.py --rotate-secret` |
| Campaign stuck — leads in SCHEDULED, `scheduled_send_at` in past | Pre-fix orphan (before pause = hard stop landed) | `docker compose exec worker python scripts/reenqueue_stuck_scheduled.py <campaign-id>` |
| Anthropic rate limits | Burst of leads | Lower `max_per_hour` on the campaign; switch from Deep to Fast research mode; rely on the 90-day research cache for repeat prospects |
| Pre-M1 campaign showing `total_leads=0` in analytics | Pre-existed before the sequence-state backfill | Recreate the campaign |

---

## Conventions worth knowing

These are gotchas that have bitten enough times to be documented.

- **Encryption allowlist.** `app/services/imap_client.py` and `app/services/encryption.py` are the only modules allowed to call `encryption.decrypt(`. A pytest in `test_phase15_hardening.py` greps the codebase to enforce. When adding a credential consumer, update the allowlist AND keep plaintext local to the function (delete before return).
- **`docker compose restart` does NOT re-read `.env`.** Use `up -d --force-recreate`. Verify with `docker compose exec backend printenv`.
- **Rebuild ALL THREE Python services after `requirements.txt` changes.** `backend`, `worker`, `beat` are separately-tagged images. `docker compose build backend` doesn't rebuild the other two.
- **A new env var must be added to the compose `environment:` block** for backend + worker + beat (not just `.env`). They use an explicit allowlist, not `env_file`.
- **Don't cache aioredis at module scope in worker code.** Celery prefork tasks each call `asyncio.run()`, building a fresh event loop. A cached `aioredis.Redis` carries connection-pool state bound to the FIRST loop and fails with "Event loop is closed" on the second task. The legacy `sequencer._LI_REDIS_CLIENT` cache survives because it resets to None on task entry; don't add new ones.
- **Stale LinkedIn dispatch guard.** After a worker restart, the same `send_linkedin_step` task can re-deliver with its original `node_id` even though the cursor advanced. `_send_linkedin_step_async` checks `state.current_node_id == node_id` at the top; mismatch → `stale_dispatch` (no API call, no slot burn, no execution row).
- **Soft-deleted sequence nodes** (graph edits): old nodes get `deleted_at` stamped, not deleted. Live queries (router / scheduler / analytics) must filter `deleted_at IS NULL` — there's a partial index `ix_sequence_nodes_live` for this. Leads whose `current_node_id` points at a soft-deleted node get auto-halted.
- **Pause is a hard stop.** The legacy `send_lead` wrapper used to self-re-enqueue every 5 min on `{status: paused}` (was line 370 of `workers/send.py`). Now it acks-and-drops. Resume re-enqueues every composed PENDING+SCHEDULED lead. Sequencer-driven follow-ups don't need this — the beat already filters by `Campaign.status == RUNNING`.
- **Schedule edits live re-queue.** Editing any of the 4 schedule fields on a running/paused campaign auto-fires the same re-enqueue. Throughput-only edits (min_delay / hour cap / day cap) don't — they take effect on the next gate claim.
- **`scheduled_send_at` is write-only.** Nothing reads it. Don't add code that depends on a cron sweeper for it — use the resume re-enqueue path instead.

---

## Security notes

- **Inbox passwords are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).** The key lives in `ENCRYPTION_KEY` (env var only — never committed). Decryption is confined to `app/services/imap_client.py`; a pytest greps the codebase to enforce.
- **IMAP fetch is read-only.** The poller uses `BODY.PEEK[HEADER]` (RFC 3501 §6.4.5) on a `readonly=True`-selected mailbox. It does NOT call `STORE +FLAGS \Seen` — your replies stay unread in your inbox.
- **Per-event Unipile webhook idempotency.** `webhook_events(provider, event_id)` table dedups by Unipile event_id (or SHA256 of raw body when no id). UniqueViolation → 200 OK no-op.
- **Unsubscribe link is HMAC-signed.** Token is `hmac_sha256(SECRET_KEY, lead.id.bytes)[:32]`. GET renders a confirm page (no side effect), POST applies the suppression — so email-scanner prefetchers can't auto-unsubscribe leads, and knowing one lead's URL doesn't let an attacker forge another's.
- **Suppression cascade.** Brevo `hard_bounce` / `spam` / `unsubscribed` events auto-add the email to the suppression list. The list is checked by `send.check_send_gates` before every send. A suppressed lead returns `{status: suppressed}` and is permanently advanced past (no retries, no re-enqueue, no future-campaign sends).
- **All four exposed ports bound to `127.0.0.1` only.** The unauthenticated API can't be reached from LAN. Tunnels (ngrok/cloudflared) still work because they forward via the host loopback.

---

## License

Private / proprietary. All rights reserved.
