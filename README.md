# Email Blaster

AI-powered cold email platform. Upload a CSV of leads → each lead gets researched (web search + optional LinkedIn enrichment + email verification) → personalized email composed by Claude → reviewed in a sample preview → sent on your schedule with rate limiting → replies tracked via your own inbox.

## What you get

- **Per-lead research**: Anthropic web search + company site scrape + optional Apollo.io (LinkedIn, seniority, employee count) + optional Hunter.io (deliverability)
- **Personalized composition**: subject + body written by Claude, tailored to the research; falls back to a generic template when research yields little
- **Style-correction feedback loop**: edits you make to sample emails are saved as exemplars that improve the rest of the batch
- **Pre-send preview**: review N sample emails, approve or reject each, edit inline
- **Scheduled sending**: days-of-week + time window + timezone + hourly cap + daily cap + min delay between sends
- **Compliance**: CAN-SPAM unsubscribe link in every email, suppression list, bounce/spam auto-suppression
- **Reply tracking**: IMAP polling against your own inbox (Gmail/Outlook/Yahoo/custom) — credentials encrypted at rest with Fernet
- **Analytics**: open / click / reply / bounce rates, sender reputation score, research-quality breakdown, best subject lines, timeline chart, per-lead drilldown

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI (Python 3.12), Celery 5, async SQLAlchemy 2 |
| Datastore | Postgres 15, Redis 7 |
| AI | Anthropic Claude Sonnet 4.6 (web search tool) |
| Email | Brevo transactional API + webhooks |
| Enrichment | Apollo.io, Hunter.io *(both optional)* |
| Reply tracking | Stdlib IMAP poller, Fernet-encrypted credentials |
| Frontend | React 18, Vite 6, TanStack Query, Recharts, React Router |

---

## Setup

### Prerequisites

- **Docker Desktop** running
- An **Anthropic API key** — https://console.anthropic.com
- A **Brevo account + API key** — https://app.brevo.com/settings/keys/api
- *(Optional)* Apollo.io API key for richer enrichment
- *(Optional)* Hunter.io API key for email verification
- *(Optional)* An inbox you control — needed for reply tracking only

### 1. Clone and create the env file

```bash
git clone <repo-url> emailblaster
cd emailblaster
cp backend/.env.example .env
```

### 2. Generate the encryption key

This key encrypts stored inbox passwords. **If you lose it, all saved inbox passwords are unrecoverable** — back it up the same way you'd back up a database password.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the 44-character output into `.env`:

```
ENCRYPTION_KEY=put-the-key-here
```

### 3. Fill in `.env`

Minimum required to send mail:

```env
ANTHROPIC_API_KEY=sk-ant-...
BREVO_API_KEY=xkeysib-...
BREVO_SENDER_EMAIL=you@yourdomain.com
BREVO_SENDER_NAME=Your Name
```

Optional but recommended:

```env
APOLLO_API_KEY=...               # LinkedIn / company enrichment
HUNTER_API_KEY=...               # email verification before send
WEBHOOK_BASE_URL=https://...     # required for Brevo webhooks + unsubscribe links
```

Anything left blank degrades gracefully — the app still runs, with less data.

### 4. Start everything

```bash
docker compose up --build
```

This brings up: postgres, redis, FastAPI backend, Celery worker, Celery beat scheduler, Vite frontend.

In a separate terminal, apply the database schema:

```bash
docker compose exec backend alembic upgrade head
```

When it's done:

- **App UI** — http://localhost:5173
- **API** — http://localhost:8000
- **OpenAPI docs** — http://localhost:8000/docs

### 5. Wire up Brevo webhooks (optional but recommended)

Brevo POSTs event notifications (delivered / opened / clicked / bounced / spam / unsubscribed) to your `WEBHOOK_BASE_URL`.

In the Brevo dashboard:

1. **Settings → Webhooks → Add new webhook**
2. URL: `{WEBHOOK_BASE_URL}/webhooks/brevo`
3. Enable events: `delivered`, `opened`, `clicked`, `soft_bounce`, `hard_bounce`, `spam`, `unsubscribed`

For local development without a public URL, run `ngrok http 8000` and put the ngrok URL in `WEBHOOK_BASE_URL`. Without webhooks, the app still works — you just won't have open/click/bounce metrics.

---

## Using the app

### Connect an inbox *(optional — required only for reply tracking)*

Sidebar → **Settings** → **Connected inboxes** → **+ Connect inbox**.

Pick the right preset and fill in your credentials. The form auto-tests the connection after you save.

#### Gmail
- IMAP host: `imap.gmail.com`, port 993, SSL on (auto-filled by the preset)
- **You need an App Password — not your regular Google password.** Gmail blocks regular passwords for IMAP.
  1. Enable 2-Step Verification on your Google account first
  2. Go to https://myaccount.google.com/apppasswords
  3. Create a new app password, label it "Email Blaster"
  4. Paste the 16-character app password into the form

#### Outlook / Office 365
- IMAP host: `outlook.office365.com`, port 993, SSL on
- Use your regular password, or an app password if MFA is enabled

#### Yahoo
- IMAP host: `imap.mail.yahoo.com`, port 993, SSL on
- Generate an app password from **Yahoo Account Security**

#### Any other IMAP server
- Use the **Custom** preset and fill in your provider's host/port/SSL settings

A green **Connected** badge after saving means it worked. A red **Failed** badge surfaces the IMAP error inline — usually authentication.

---

### Create a campaign (3-step wizard)

Sidebar → **Campaigns** → **+ New campaign**.

#### Step 1 — Details

- **Name** — internal label
- **Goal** — what you want from the recipient (e.g. "Book a 30-minute discovery call")
- **Tone** — Professional / Friendly / Direct / Conversational / Formal
- **Sender name + email** — must be authorized in Brevo as a sender
- **Sample count** — how many emails you want to review before the batch goes out (default 5)
- **Research mode**
  - **Fast** — web search + company scrape, ~10s/lead
  - **Deep** — adds Apollo enrichment, ~45s/lead (requires `APOLLO_API_KEY`)
- **Reply tracking** *(optional)* — pick a connected inbox or leave on "No reply tracking"
- **Schedule**
  - Days of week (multi-select pills)
  - Send window (start/end time + timezone)
  - Optional caps: max per hour, max per day
  - Minimum delay between sends

#### Step 2 — Upload leads

Drop in a CSV. The app shows the detected columns + suggested mapping. **One column must map to `email`** — that's the only required field. Other supported fields: `first_name`, `last_name`, `company`, `job_title`, `linkedin_url`, `phone`. Unmapped columns are preserved in the raw row for audit but not used in composition.

The system:
- Lowercases all emails, deduplicates within the CSV
- Skips any email in the suppression list (previous unsubs/bounces)
- Picks `sample_count` leads spread evenly across the deduped list as samples
- Kicks off background research for every lead

#### Step 3 — Auto-research

The wizard shows a live progress bar (`composed / total`). When all samples are composed, it auto-advances to the preview page.

---

### Review samples

Each sample card shows:

- The lead's name, title, company
- A **research quality badge** (Rich / Partial / Generic)
- An expandable **research summary** — exactly what the system found
- The composed **subject** and **body** (both editable)
- **Approve** / **Reject** buttons

Edits to the body auto-save on blur. Any edit you make is recorded as a **style correction** and fed into the prompt for the remaining leads — so the first samples teach the composer your voice.

When you're happy with all samples:

- **Approve and launch campaign** → status flips to **running**, every already-composed lead is dispatched to send, and remaining leads continue research/compose with sends triggered automatically as they finish.
- **Reject and reconfigure** → status flips back to **draft**, all composed bodies cleared. You can fix the campaign and start again.

---

### Monitor the campaign

#### Campaign list (sidebar → Campaigns)

Each campaign card: name, status badge, sent/total progress bar, open/click/reply rates, created date, quick actions (View / Pause / Resume / Delete).

#### Campaign detail page

Three tabs:

- **Overview** — status with Pause/Resume control, quick stats, connected inbox info, campaign config summary
- **Leads** — paginated table; search by name/email, filter by send status, export to CSV
- **Analytics** — live metrics that refresh every 30 seconds:
  - Sent, Delivered, Open rate, Click rate, Reply rate, Bounce rate, Spam, Unsubscribed
  - Timeline chart (Recharts) — opens (teal), clicks (purple), replies (amber)
  - **Sender reputation score** 0–100 (green ≥80, amber 50–79, red <50)
  - Research quality breakdown — open rate per research tier (rich/partial/generic)
  - Best subject lines (min 5 sends to qualify)
  - **Failed-leads banner** — appears when any lead failed research/compose/send, with a single-click "Retry failed" button

#### Pause / resume mid-flight

- Click **Pause** on the campaign card or detail page → in-flight sends finish their current attempt, then new sends are deferred (re-enqueued every 5 minutes)
- Click **Resume** → scheduled re-enqueues drain immediately

#### Reply detection

If you connected an inbox, a Celery beat task polls IMAP every `IMAP_POLL_INTERVAL_MINUTES` (default 20). When a reply is detected (matched via In-Reply-To header, then References, then subject+from), it's recorded as a `replied` event and counted in reply rate.

---

## Project layout

```
emailblaster/
├── backend/
│   ├── app/
│   │   ├── main.py             FastAPI app + middleware + exception handler
│   │   ├── config.py           Settings via pydantic-settings
│   │   ├── database.py         Async SQLAlchemy engine + get_db
│   │   ├── models/             6 ORM models
│   │   ├── schemas/            Pydantic request/response shapes
│   │   ├── routers/            campaigns, leads, preview, analytics, settings,
│   │   │                       webhooks, connected_accounts
│   │   ├── services/           encryption, imap_client, brevo, web_research,
│   │   │                       site_scraper, apollo, hunter, csv_parser,
│   │   │                       email_template
│   │   └── workers/            celery_app, ingest, research, compose, send,
│   │                           reply_poller
│   ├── alembic/                Migrations
│   └── tests/                  261 backend tests
├── frontend/
│   └── src/
│       ├── pages/              Campaigns, CampaignCreate, CampaignDetail,
│       │                       Preview, Analytics, Settings
│       ├── components/         Nav, Toast, ErrorBoundary, EmailPreviewCard,
│       │                       LeadTable, LeadUpload, ScheduleConfig,
│       │                       MetricsGrid, ConnectInboxModal
│       └── api/                axios wrappers per resource
├── docker-compose.yml
└── README.md (this file)
```

---

## Running tests

```bash
# Backend (261 tests; spins up postgres if not already running)
docker compose run --rm backend pytest

# Frontend (119 tests; pure jsdom, no services needed)
docker compose run --rm --no-deps frontend npm test
```

---

## Troubleshooting

### `ENCRYPTION_KEY is not configured`
You skipped setup step 2. Generate a key, paste it into `.env`, then:
```bash
docker compose restart backend worker beat
```

### Migrations fail or the DB looks stale
```bash
docker compose down --volumes      # ⚠️ deletes all campaign data
docker compose up postgres -d
docker compose exec backend alembic upgrade head
```

### IMAP "authentication failed"
- **Gmail** — confirm you're using an **App Password** (16 chars, no spaces), not your Google password. App Passwords require 2-Step Verification to be enabled. Some Google Workspace orgs disable IMAP — ask your admin to enable it.
- **Outlook** — try an app password if MFA is on.
- Watch for stale credentials: the Settings page surfaces the IMAP error from the most recent connection test.

### Brevo "sender not authorized"
Brevo only sends from senders explicitly added in **Settings → Senders & IP** on the Brevo dashboard. Add `BREVO_SENDER_EMAIL` (and any campaign-level sender_email you use) there first.

### Anthropic rate limits
Research and compose workers retry transient failures with exponential backoff. If you hit limits often:
- Lower `max_per_hour` on the campaign
- Switch the campaign from **Deep** to **Fast** research mode
- Upgrade your Anthropic plan

### Webhook events not appearing in analytics
- Confirm `WEBHOOK_BASE_URL` is reachable from the public internet (use ngrok for local dev)
- Confirm Brevo's webhook in the dashboard points at `{WEBHOOK_BASE_URL}/webhooks/brevo`
- Check the backend logs for `POST /webhooks/brevo -> 200`

### "Reply tracking is not configured" banner won't go away
Reply tracking is per-campaign. To enable it on an existing campaign you'd need to recreate the campaign (or extend the PATCH endpoint to accept `connected_account_id` changes — currently only draft/previewing campaigns are editable).

---

## Security notes

- Inbox passwords are encrypted at rest with **Fernet (AES-128-CBC + HMAC-SHA256)**. The key lives in `ENCRYPTION_KEY` (env var only — never committed).
- Decryption is **confined to `app/services/imap_client.py`**. A pytest in the suite greps the codebase to prevent `encryption.decrypt(` from creeping into other modules.
- Plaintext passwords live only inside two wrapper functions (`test_imap_with_account`, `fetch_unseen_with_account`) and are explicitly `del`'d before return.
- Every outbound email has a **CAN-SPAM-compliant unsubscribe link** appended automatically. Clicking it adds the address to the suppression list and prevents all future sends to that address from any campaign.
- Suppression list is also auto-populated by Brevo `hard_bounce`, `spam`, and `unsubscribed` webhook events.
