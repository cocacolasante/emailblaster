# Email Blaster — Claude session context

Running state of the project so any Claude session (this one or a future one)
can pick up where the last one left off. The remaining-work roadmap is in
[`docs/roadmap.md`](docs/roadmap.md); this file is the snapshot.

> **Convention:** update this file at the end of every meaningful task.
> Move finished work into "Last completed", pull the next item from the
> roadmap into "Next up", note anything surprising under "Conventions and
> gotchas", and refresh the test counts.

---

## Mission

AI-powered cold email + multi-channel outreach. Upload CSV → research each
lead → Claude composes a personalized email → multi-step sequence (email +
delays + later LinkedIn) → schedule + send → track replies → analyze.

Phase 1 (email-only) shipped. We're now in **Phase 1.5 — multi-step
sequences + LinkedIn outreach**, broken into M1–M5 in the roadmap.

## Where we are

- **Last completed:** **Validated every Unipile endpoint live + fixed 7 endpoint-shape bugs.**
  Ran the full action suite against a real Unipile account
  (`Anthony Colasante`, Sales Nav premium, unipile_id
  `eU5rAYqAQo-6yDRdjgNAiw`) targeting the throwaway profile
  `ant-cola-a5898840a` + the public Bill Gates profile.  Working live:
  `test_connection`, `view_profile`, `latest_post_urn`,
  `inbox_recent_events`, `follow_profile`, `send_connect_request`,
  `react_to_post`, `comment_on_post`.  `send_dm` correctly returns
  `no_connection_with_recipient` against a non-1st-degree target.
  - **Bug fixes in `unipile_impl.py`:** (1) `inbox_recent_events` was
    sending `isoformat()` with microseconds + `+00:00`; Unipile requires
    strict `YYYY-MM-DDTHH:MM:SS.sssZ`. (2) New `_resolve_provider_id`
    helper — most endpoints (`/users/invite`, `/users/{id}/posts`,
    `/chats`) reject LinkedIn public slugs with
    `errors/invalid_recipient`; they need the canonical `ACoAAA...`
    member token via `GET /users/{slug}` first. The resolver caches
    the result on the ProfileRef. (3) `follow_profile` rewritten to
    use Unipile's raw Voyager passthrough at `POST /api/v1/linkedin`
    with a `followingStates` patch — Unipile doesn't package follow.
    (4) `react_to_post` moved from `POST /posts/{urn}/reactions`
    (404) to `POST /api/v1/posts/reaction` body-based with
    `{account_id, post_id, type}`. (5) `comment_on_post` needed
    `account_id` in the body, not the query. (6) `send_inmail`
    rewritten to use form-encoded body with bracket-notation
    `linkedin[api]=sales_navigator` + `linkedin[inmail]=true`; live
    test returns Unipile 403 `errors/resource_access_restricted`
    (Sales Nav API access not enabled on the Unipile workspace, not a
    code bug — endpoint shape is correct). (7) `_raise_for_special_codes`
    tightened: was grepping error bodies for "restricted" and
    false-positively raising `AccountRestricted` on
    `errors/resource_access_restricted` (a Unipile plan error). Now
    matches only specific LinkedIn-account-state codes.
  - **`_request` extended** to support form-encoded bodies via `data=`
    (pre-encoded as bytes through `content=` so httpx ships an
    AsyncByteStream).
  - **`invite_to_page` is blocked on Unipile's passthrough whitelist.**
    HAR-captured the exact LinkedIn Voyager request that powers their
    admin-UI "Invite connections to follow" action:
    `POST /voyager/api/voyagerRelationshipsDashInvitations?inviter=(organizationUrn:urn:li:fsd_company:<PAGE_ID>)`
    with `x-restli-method: batch_create` + body
    `{"elements":[{"inviteeMember":"urn:li:fsd_profile:<MEMBER>","genericInvitationType":"ORGANIZATION"}]}`.
    Tried routing via Unipile's raw passthrough at `POST /api/v1/linkedin`
    in every documented shape (inlined query string, separate
    `query_params` field per Unipile's "Raw Data" docs, `encoding=true/false`,
    `headers` dict, with/without `x-restli-method`, URL-encoded vs raw
    URN colons) — every variant returns Unipile's
    `errors/malformed_request` from their forwarder, BEFORE the request
    reaches LinkedIn (a plain GET to `identity/profiles/me` via the
    passthrough fails the same way).  `follow_profile` works through the
    same passthrough only because `feed/dash/followingStates` happens to
    be on Unipile's allowlist.  The impl now returns a clean
    `unipile_passthrough_blocked` ActionResult and
    `LINKEDIN_INVITE_TO_PAGE` is gated out of `PUBLISHABLE_KINDS_M1`.
    Next step: open a Unipile support ticket asking them to allowlist
    `voyagerRelationshipsDashInvitations`, then restore the impl + gate
    from git history (it lives in commits prior to the gating revert).
  - Added a `_resolve_provider_id` unit test, a `resource_access_restricted`
    error-mapping regression test, a form-encoded InMail body test, and
    rewrote `test_publish_rejects_non_numeric_page_id` →
    `test_publish_rejects_linkedin_invite_to_page` to assert the new gate.
  - Ad-hoc smoke scripts at `backend/scripts/test_unipile_endpoints.py`
    and `backend/scripts/test_unipile_post_actions.py` — handy for
    re-validating against the live API.
  Tests: **backend 405 passed**, **frontend 150 passed**.

- **Previously:** **Stripped the dead Playwright / DIY code paths.**
  Unipile is the only LinkedIn provider now.  Deleted
  `services/linkedin/playwright_impl.py`, `hybrid_impl.py`,
  `linkedin_api_impl.py`; deleted `tests/test_phase21_voyager_error_parser.py`
  and `tests/test_phase22_human_dwell.py`; dropped `_AccountLock` /
  `AccountLockBusy` from `base.py` + the corresponding sequencer except
  branch; deleted `LINKEDIN_PROVIDER`, `LINKEDIN_PROXY_URL`, and
  `LINKEDIN_PROFILES_DIR` from `app/config.py`; dropped `playwright`,
  `playwright-stealth`, and `linkedin-api` from `requirements.txt`;
  removed the Chromium install + runtime libs from `backend/Dockerfile`
  (saves ~170MB image bloat); deleted the legacy
  `POST /linkedin-accounts/` create endpoint + the password/li_at
  PATCH fields; collapsed `ConnectLinkedInModal` to Unipile-only (no
  more "Local (legacy)" toggle); updated the `encryption.decrypt`
  allowlist in `test_phase15_hardening.py` to `{imap_client.py,
  encryption.py}` (the DIY consumer is gone).  The
  `password_encrypted` / `session_cookies_encrypted` / `proxy_url`
  columns on `LinkedInAccount` are kept nullable for legacy rows but
  nothing in the code touches them.
  Tests: **backend 402 passed**, **frontend 150 passed**.

- **Previously:** **Unipile hosted-API integration.**  Switched the
  default provider to Unipile (real desktop Chrome on residential IPs),
  added `UnipileLinkedInProvider`, hosted-auth flow endpoints
  (`POST /linkedin-accounts/connect-via-unipile`, `/sync-unipile`,
  `/discoverable`, `/import-from-unipile`), webhook handler at
  `POST /webhooks/unipile` with static-custom-header auth, frontend
  modal redesign, and 33 new provider tests
  (`test_phase23_unipile_provider.py`).  See the **Unipile setup
  runbook** below for fresh-dev-box wiring.

- **In flight:** nothing.
- **Next up:** open.  Suggested directions:
  - **Open a Unipile support ticket** for two passthrough/permission
    issues at once: (a) allowlist `voyagerRelationshipsDashInvitations`
    on `/api/v1/linkedin` so `invite_to_page` can fire; (b) enable Sales
    Navigator API access on the workspace so `send_inmail` (POST
    `/api/v1/chats` with `linkedin[api]=sales_navigator`) stops returning
    `errors/resource_access_restricted`.  Both impls are HAR / docs-
    verified — neither is a code bug.
  - **Unlock InMail in Unipile.** Live test returned 403
    `errors/resource_access_restricted` — the impl is correct but the
    Unipile workspace doesn't have Sales Nav API access enabled.
    Either upgrade the Unipile plan or contact their support.
  - **Webhook idempotency.** Unipile's "at-least-once" delivery means
    we may double-process retries.  Add a `webhook_events` table keyed
    on event_id when this becomes a real issue.
  - **Drop legacy DB columns.**  `password_encrypted` /
    `session_cookies_encrypted` / `proxy_url` on `linkedin_accounts`
    are dead.  Schedule a migration to drop them once the legacy DIY
    rows are gone from production.
  - **Cross-cutting cleanup** from `docs/roadmap.md#cross-cutting-tasks`
    (sequence templates, multi-tenant readiness, named node_modules
    volume).
  - **Phase 2** — whatever you have in mind next.

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · async SQLAlchemy 2 · Celery 5 |
| DB / cache | PostgreSQL 15 · Redis 7 |
| AI compose | Anthropic Claude Sonnet 4.6 |
| Email | Brevo transactional API + webhooks |
| Reply tracking | IMAP poller (Celery beat, every 20 min) |
| Frontend | React 18 · Vite 6 · TanStack Query · `@xyflow/react` |
| Infra | docker-compose: postgres, redis, backend, worker, beat, frontend |

## Architecture cheat sheet

- **Routers** in `backend/app/routers/`: `campaigns`, `leads`, `preview`,
  `analytics`, `settings`, `webhooks`, `connected_accounts`,
  **`sequences`** (M1), **`linkedin_accounts`** (M2).
- **Workers** in `backend/app/workers/`:
  - Legacy email pipeline: `ingest → research → compose → send`.
  - **M1 additions:** `sequencer.advance_sequences` (beat, every 60s) +
    `sequencer.send_email_step` (channel handler for follow-up emails).
  - **M2 additions:** `sequencer.send_linkedin_step` (LinkedIn action
    handler — view/follow/react) + `linkedin_poller.poll_all` (beat,
    every 5 min) for inbound events (DM replies, connection accepts).
  - **M3 additions:** same `send_linkedin_step` now also handles
    `linkedin_connect`, `linkedin_dm`, `linkedin_invite_to_page`. Has
    per-kind subcaps + 1st-degree check for DM + page invites.
  - **M4 additions:** `linkedin_inmail` (requires Premium — returns
    `skipped` with `premium_required` meta when credits run out) and
    `linkedin_comment_post`. InMail counts against the DM cap.
  - `reply_poller` runs separately (IMAP email replies, every 20 min).
- **Sequence engine:**
  - `app/services/sequence_conditions.py` — JSON expression language
    (`always` / `not` / `and` / `or` / `replied` / `opened` / `clicked` /
    `bounced` / `linkedin_connection` / `days_since_entered_node`) +
    evaluator + DAG cycle detector.
  - `app/services/sequence_service.py` — `ensure_default_sequence`,
    `enroll_leads`, `validate_graph`, `replace_graph`.
    `PUBLISHABLE_KINDS_M1` whitelist gates which kinds can be published
    (M2 added `linkedin_view_profile`, `linkedin_follow_profile`,
    `linkedin_react_post`; write actions still rejected).
  - `app/workers/sequencer.py` — beat task that walks per-lead state
    cursors; dispatches email + LinkedIn channel handlers.
  - **5 sequence tables:** `sequences`, `sequence_nodes`,
    `sequence_edges`, `lead_sequence_states`, `lead_step_executions`.
- **LinkedIn engine:**
  - `app/services/linkedin/base.py` — `LinkedInProvider` ABC +
    `ProfileRef`, `ActionResult`, `InboundEvent`, `ChallengeRequired`,
    `AccountRestricted` types.
  - `app/services/linkedin/unipile_impl.py` — the only concrete impl.
    Async httpx wrapper around Unipile's REST API; maps Unipile error
    envelopes onto our domain types.  Reads `UNIPILE_DSN` +
    `UNIPILE_API_KEY` from settings.
  - `LinkedInAccount` table: rows are created either via the hosted-
    auth flow (`POST /linkedin-accounts/connect-via-unipile`) or by
    binding a pre-existing Unipile account (`POST /linkedin-accounts/
    import-from-unipile`).  Status flips to `challenged` when Unipile
    surfaces a checkpoint — user completes it in Unipile's hosted
    browser, then we `/resolve-challenge` to clear the local flag.
  - Per-account rate limits via Redis: `LINKEDIN_DAILY_ACTION_CAP`
    (default 20) + `LINKEDIN_MIN_ACTION_DELAY_SECONDS` (default 30).
    Unipile humanises on its side; these are our burst-control floor.
  - **Per-kind subcaps:** `LINKEDIN_DAILY_CONNECT_CAP` (20),
    `LINKEDIN_DAILY_DM_CAP` (30), `LINKEDIN_MONTHLY_PAGE_INVITE_CAP`
    (250 per page). Redis keys: `li-rate:{aid}:day:connect`,
    `li-rate:{aid}:day:dm`, `li-rate:page:{page_id}:month`. Page invite
    cap is keyed by page_id, NOT account, since LinkedIn enforces per
    company page.
- **Default sequences are auto-created** on campaign creation, and every
  enrolled lead gets a `lead_sequence_state` row. Old data was backfilled
  in `alembic/versions/0002_sequences.py`.
- **The legacy `compose → send_lead` path is still the source of truth
  for the FIRST email.** The new sequencer skips email entry nodes whose
  `lead.send_status` is already SENT and advances to the next node.
  Don't remove the legacy path without a migration plan.

## Conventions and gotchas

- **Connected accounts:** `email_address` is for display; `username` is
  the IMAP login. Gmail aliases share their parent mailbox — set
  `email_address` to the alias and `username` to the primary mailbox.
  The connect modal has an inline hint about this
  ([`ConnectInboxModal.jsx`](frontend/src/components/ConnectInboxModal.jsx)).
- **Encryption invariant:** only `app/services/imap_client.py` may call
  `encryption.decrypt(` (allowlist is `{imap_client.py, encryption.py}`
  — the DIY LinkedIn consumer was stripped along with Playwright).  A
  test in `test_phase15_hardening.py` greps the codebase to enforce.
- **Docker network quirk:** if Docker Desktop restarts while containers
  are up, postgres can become detached from `emailblaster_default`
  (you'll see `socket.gaierror: Name or service not known` in backend
  logs). Fix without a restart:
  ```bash
  docker network connect --alias postgres emailblaster_default emailblaster-postgres-1
  ```
- **CORS-on-error:** 500 responses go through Starlette's
  `ServerErrorMiddleware` which sits OUTSIDE `CORSMiddleware`. The
  `unhandled_exception_handler` in `app/main.py` manually attaches CORS
  headers so the browser shows the 500 instead of masking it as CORS.
- **Migrations:** run `docker compose exec backend alembic upgrade head`
  after pulling. Migrations use `postgresql.ENUM(..., create_type=False)`
  for column references; pre-create the enum types up front (see
  `0002_sequences.py` for the pattern).
- **LinkedIn enum kinds are already in the DB.** `publish()` rejects
  sequences containing any LinkedIn kind in M1
  (`sequence_service.PUBLISHABLE_KINDS_M1`). M2 starts removing that
  restriction.
- **Test DB truncate list** in `tests/conftest.py` must include every
  table. Update it when you create new ones.
- **Frontend node_modules quirk:** the running `frontend` container has
  an anonymous `/app/node_modules` volume. After `npm install` on the
  host, the running container won't see new packages until
  `docker compose exec frontend npm install` also runs. Fresh
  `docker compose run --rm` containers create a NEW empty anonymous
  volume — rebuild the image with `docker compose build frontend` if
  you need that path to work too.
- **Sequence builder handles:** custom xyflow nodes need explicit
  `<Handle>` components to be connectable. `SequenceBuilder.NodeCard`
  has source on the right + target on the left; entry nodes hide
  their target. Edges use `MarkerType.ArrowClosed` for direction.
- **LinkedIn rate-limit singleton + pytest:** `sequencer._LI_REDIS_CLIENT`
  is a module-level cache. pytest-asyncio gives each test a fresh event
  loop; tests that exercise `_li_redis()` must monkeypatch the global
  back to None at the start. `test_phase18_linkedin_write.py` uses an
  `autouse` fixture that does this for every test in the file — copy
  that pattern in any new file exercising LinkedIn rate limits.
- **`encryption.decrypt` allowlist:** the hardening grep test in
  `test_phase15_hardening.py` lists `imap_client.py` + `encryption.py`.
  Adding a new credential consumer means updating this list AND keeping
  plaintext within the new module's local scope (delete before return).
- **InMail premium-required path:** when Unipile reports the account
  lacks InMail credits, `send_inmail` returns
  `ok=False, premium_required=True` rather than raising.  The sequencer
  maps that to a `skipped` execution row with a clear error
  ("InMail unavailable — account needs Premium / Sales Nav credits")
  so the campaign keeps moving rather than retrying forever.
- **Soft-deleted sequence nodes** (M5): when the user re-edits a
  graph, the OLD nodes get `deleted_at` stamped, not deleted. This
  preserves historical `lead_step_executions` for analytics. The
  trade-offs:
  - Live queries (router, scheduler, analytics) must filter
    `deleted_at IS NULL`. A partial index
    `ix_sequence_nodes_live` is in place for that filter.
  - Leads whose `current_node_id` points at a soft-deleted node get
    auto-halted by the scheduler with a clear reason.
  - The analytics endpoint currently shows ONLY live nodes; retired
    nodes' counts aren't surfaced. If you want lifetime totals, add a
    `?include_deleted=true` flag later.
- **Rebuild ALL THREE Python services when you change `requirements.txt`.**
  `backend`, `worker`, and `beat` all build from the same Dockerfile but
  docker-compose tags them as separate images. `docker compose build
  backend` does NOT rebuild `worker` / `beat`. Symptom: backend boots
  fine but campaigns get stuck because tasks never run; `docker compose
  logs worker` shows `ModuleNotFoundError`. Always run
  `docker compose build backend worker beat` after editing requirements.
- **`docker compose restart` does NOT re-read `.env`.** Env vars get
  substituted into containers at *create* time, not on restart. After
  editing `.env`, a plain restart leaves the old values in the running
  containers. Symptom: a config-only change (new Brevo key, new
  Anthropic key, etc.) appears in `.env` but the app still hits the
  old credential and 401s. Fix: recreate the affected containers —
  ```bash
  docker compose up -d --force-recreate backend worker beat
  ```
  Verify by comparing host `.env` against `docker compose exec backend
  printenv VAR_NAME` after the recreate.
- **Pre-M1 leads have no `lead_sequence_state` row.** Campaigns +
  leads that existed before the M1 migration's backfill ran are fine
  going forward, but if your dev DB had leads inserted via the legacy
  CSV flow that doesn't enroll, the analytics endpoint will show
  `total_leads=0` for them. The fix is a one-off backfill or just
  re-create the campaign.
- **Don't cache aioredis at module scope in worker code.** Celery
  prefork tasks each call `asyncio.run(...)` which builds a fresh
  event loop; a cached `aioredis.Redis` carries connection-pool state
  bound to whichever loop first created it and fails with "Event loop
  is closed" on the second task.  `sequencer._li_redis()` still caches
  (legacy) — fine for tests that monkeypatch `_LI_REDIS_CLIENT=None`,
  but rewrite if it ever causes issues in production.
- **Sequencer transient retries.** A `skipped` step normally advances
  the cursor immediately, but skips with `status` in
  `TRANSIENT_SKIP_STATUSES = {"challenged", "restricted", "rate_limited"}`
  keep the lead pinned on the current node and push `next_run_at` out
  by `TRANSIENT_RETRY_MINUTES` (5). After `MAX_TRANSIENT_RETRIES` (10)
  consecutive transient skips on the same visit, the cursor advances
  like a normal skip so a permanently-broken account doesn't hold the
  lead forever. The retry budget resets when the lead re-enters the
  node (via `entered_current_at`).

## Run / develop

```bash
docker compose up -d                               # bring stack up
docker compose exec backend alembic upgrade head   # apply migrations
docker compose run --rm backend pytest             # backend tests (402)
docker compose exec frontend npm test              # frontend tests (150)
docker compose logs -f backend                     # tail logs
```

App: <http://localhost:5173>  ·  API: <http://localhost:8000>  ·  Docs:
<http://localhost:8000/docs>

## Open questions

- **Per-edge funnel percentages.** Analytics currently shows
  per-node counts only. Per-edge "% of leads who took this branch"
  would need a transition-history log (we don't write one today). Add
  an `edge_id` column to `lead_step_executions` or a new table when
  this becomes useful.
- **Per-step email metrics.** Aggregate email open/reply rates per
  node would need `step_execution_id` on `email_events` so we can
  attribute opens to the specific step that sent them. Punt until
  someone actually asks.

## Recent decisions worth remembering

- **Build the framework, ship email-only first.** User chose this in the
  M1 kickoff. The default 1-node sequence + auto-skip of the entry email
  node means the legacy pipeline and the new sequencer coexist without
  conflict.
- **Rich if/then-tree branching** — user picked the most expressive
  option. Implemented as a JSON expression language with `and/or/not` +
  leaf ops. Visual builder deferred to M4 (a JSON textarea ships in M1).
- **LinkedIn path: Unipile** (final decision, supersedes the earlier DIY
  bet).  Tried DIY Playwright with persistent profiles, Redis locks,
  stealth patches, and human-dwell — every fresh Chromium launch still
  tripped LinkedIn's bot scorer because the headless+datacenter
  fingerprint was unsalvageable.  Unipile runs real desktop Chrome on
  residential IPs and has been steady; the entire DIY codebase was
  stripped in 2026-05-14.

---

_Last updated: 2026-05-14 — Stripped the dead DIY/Playwright code paths.
Unipile is the sole LinkedIn provider; the `LinkedInProvider` ABC stays in
place so a second hosted provider could slot in later without disturbing
callers._

_Backend tests: **405 passing**.  Frontend tests: **150 passing**._

> **🚀 Starting on a fresh dev box?** Jump to
> [Unipile setup runbook](#unipile-setup-runbook-any-computer-local-dev)
> below — it walks through every step (ngrok / cloudflared tunnel,
> Unipile webhook config, .env wiring, force-recreate) needed to get
> the stack talking to Unipile on a new machine.

## Unipile integration

LinkedIn actions are powered by Unipile (real desktop Chrome on
residential IPs).  Key surface:

1. **`UnipileLinkedInProvider`** in `services/linkedin/unipile_impl.py`
   — the only concrete impl behind the `LinkedInProvider` ABC.  Async
   httpx wrapper around Unipile's REST API.  Maps Unipile error
   envelopes (status/code/message) onto our `ChallengeRequired` /
   `AccountRestricted` / `UnipileError` domain types.

2. **Router endpoints** in `routers/linkedin_accounts.py`:
   - `POST /connect-via-unipile` — creates a placeholder row, gets a
     hosted-login URL from Unipile, returns it for the frontend to
     open.  The local UUID rides along as Unipile's `name` field so
     webhook events can correlate back to the row.
   - `POST /{id}/sync-unipile` — polling fallback when the webhook
     hasn't reached us yet.
   - `GET /discoverable` + `POST /import-from-unipile` — bind a
     LinkedIn account that was connected via Unipile's dashboard
     (instead of our hosted-auth flow) to a fresh local row.
   - `DELETE /{id}` — also calls Unipile's `delete_account` so we
     don't leak a session on their side.

3. **Webhook handler** `POST /webhooks/unipile` in `routers/webhooks.py`.
   Unipile doesn't HMAC-sign bodies — auth is a static custom header.
   Handler reads `request.headers[settings.UNIPILE_WEBHOOK_AUTH_HEADER]`
   (default `X-Unipile-Auth`) and constant-time-compares against
   `settings.UNIPILE_WEBHOOK_SECRET`.  Routes events: `account.connected`,
   `account.disconnected`, `account.checkpoint`, `message.received`,
   `invitation.accepted`.  Event-name casing normalised.

4. **`ConnectLinkedInModal`** frontend has two surfaces in create mode:
   - "Connect via Unipile" button → calls `/connect-via-unipile` →
     opens hosted URL in new tab → polls `/sync-unipile` every 3s
     until status flips to OK (10-min timeout).
   - Collapsible "Already connected in Unipile?" panel → lists
     `/discoverable` rows → per-row Import button binds the
     unipile account to a fresh local row.

## Quick-refresh: ngrok + Unipile webhooks

For day-to-day dev (new ngrok URL on every restart) there's a one-shot
script that does the whole dance:

```bash
python3 scripts/dev_tunnel.py
```

It detects (or starts) ngrok pointing at `localhost:8000`, deletes
every Unipile webhook on the workspace, recreates the three canonical
ones (`messaging`, `account_status`, `users`) pointing at the live
tunnel, patches `.env` (`WEBHOOK_BASE_URL`, optionally
`UNIPILE_WEBHOOK_SECRET` with `--rotate-secret`), and only
force-recreates `backend`/`worker`/`beat` when `.env` actually
changed (idempotent — safe to re-run).  Pure stdlib, no pip install.
Useful flags: `--dry-run`, `--rotate-secret`, `--no-recreate`,
`--port N`.

Use the full runbook below only when wiring a fresh dev box from
scratch (Unipile account creation, ngrok install, etc.).

## Unipile setup runbook (any computer, local dev)

> **Multi-machine context:** this whole runbook is the one source of
> truth for getting Unipile wired up on a fresh dev box.  Follow it top
> to bottom on any new machine.  All steps are Windows / PowerShell;
> macOS / Linux equivalents are obvious (`brew install`, etc.).

### 1. Sign up + grab credentials (once per Unipile account)

1. Sign up at <https://www.unipile.com>.
2. Dashboard top bar shows your **DSN** like `api12.unipile.com:13443` —
   copy it.  Each tenant gets a different number (`api3.`, `api12.`, etc.).
3. Sidebar → **Access Tokens** → **Generate** → copy the token.  Unipile
   only shows it once; lose it and you generate a new one.

### 2. Public HTTPS tunnel (every dev box)

Unipile's servers can't reach `localhost`, so we need a tunnel.  Two
options:

**ngrok (recommended — has a request inspector at <http://127.0.0.1:4040>):**

```powershell
winget install Ngrok.Ngrok
# Sign up at ngrok.com, copy the authtoken from
# https://dashboard.ngrok.com/get-started/your-authtoken
ngrok config add-authtoken <YOUR_TOKEN>
ngrok http 8000     # leave running in its own terminal
```

The output line `Forwarding https://<random>.ngrok-free.app -> http://localhost:8000`
is your public URL.

**Cloudflared (no signup):**

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```

Prints a `https://<random>.trycloudflare.com` URL.  Trade-off: no
request inspector.

⚠ **Free ngrok / cloudflared subdomains change on every restart.**  Keep
the tunnel terminal running; if it dies, update all three webhook URLs
in Unipile + `WEBHOOK_BASE_URL` in `.env` + force-recreate containers.

### 3. Create the three Unipile webhooks

Unipile splits webhooks by data source, so you need **three** webhooks
that all POST to the same `/webhooks/unipile` endpoint.  Our handler
dispatches on event-name internally, so it doesn't care which webhook
delivered the event.

**Unipile doesn't HMAC-sign request bodies.** Their auth model is a
**static custom header** — you specify a header key + value when
creating the webhook, and Unipile echoes that exact header (same value)
on every delivery. Our handler reads
`settings.UNIPILE_WEBHOOK_AUTH_HEADER` (default `X-Unipile-Auth`),
constant-time-compares the value against `settings.UNIPILE_WEBHOOK_SECRET`,
and 401s on mismatch. See `_verify_unipile_auth()` in `routers/webhooks.py`
and the Unipile docs "Authentication" section on the Webhooks page.

The dashboard UI for adding custom headers is inconsistent across
Unipile flavours, so the most reliable path is **creating webhooks via
the Unipile API**:

```bash
DSN=$(grep '^UNIPILE_DSN=' .env | cut -d'=' -f2-)
KEY=$(grep '^UNIPILE_API_KEY=' .env | cut -d'=' -f2-)
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
TUNNEL=https://<your-ngrok-or-cloudflared-host>

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

# Save the secret to paste into .env next:
echo "$SECRET"
```

**Verified working `source` values (2026-05-14):**

| `source` | Covers |
|---|---|
| `messaging` | new chat messages (DM replies) |
| `account_status` | Unipile account state changes (`account.connected`, `account.checkpoint`, `account.disconnected`) |
| `users` | new relations (`invitation.accepted`, new connections) |

We don't use Unipile's `mailing` / `mail_tracking` / `calendar` sources.

To list / delete webhooks: `GET` / `DELETE` `https://$DSN/api/v1/webhooks[/<id>]`
with the `X-API-KEY` header.

### 4. Wire `.env`

Open `.env` (project root) and set these four:

```env
UNIPILE_DSN=api12.unipile.com:13443      # your DSN from step 1
UNIPILE_API_KEY=<access token from step 1>
UNIPILE_WEBHOOK_SECRET=<the static header-value secret from step 3>
UNIPILE_WEBHOOK_AUTH_HEADER=X-Unipile-Auth   # optional, this IS the default
WEBHOOK_BASE_URL=https://<tunnel-host-no-trailing-slash>
```

`WEBHOOK_BASE_URL` must point to the SAME tunnel host you gave Unipile
in step 3.  It's used by `POST /linkedin-accounts/connect-via-unipile`
to build the `notify_url` Unipile attaches to the hosted-auth flow.

### 5. Force-recreate so `.env` actually takes effect

`docker compose restart` does NOT re-read `.env` (already a gotcha in
the Conventions section).  Use:

```powershell
docker compose up -d --force-recreate backend worker beat
```

Verify it took:

```powershell
docker compose exec backend printenv UNIPILE_DSN UNIPILE_API_KEY UNIPILE_WEBHOOK_SECRET WEBHOOK_BASE_URL
```

All four should print non-empty values.

### 6. Sanity-check the tunnel is reachable

```powershell
# Without the auth header → 401 (proves auth-check fires AND routing works)
curl.exe -X POST "https://<tunnel>/webhooks/unipile" -H "Content-Type: application/json" -d "{}"
# Expected: 401 {"detail":"invalid auth header"}

# With the right header → 400 invalid JSON (auth passed, just no real event in body)
curl.exe -X POST "https://<tunnel>/webhooks/unipile" `
  -H "Content-Type: application/json" `
  -H "X-Unipile-Auth: <THE_SECRET>" `
  -d "{}"
# Expected: 400 {"detail":"invalid JSON: ..."} or similar — the point is we got PAST auth
```

Both responses confirm routing through the tunnel works.  Then hit the
**Send test event** button on each Unipile webhook and watch:

```powershell
docker compose logs -f backend | findstr /I unipile
```

You should see three `Unipile webhook event='...'` lines, each returning
200.

### 7. End-to-end smoke (link a real LinkedIn account)

1. Open <http://localhost:5173/settings>.
2. **LinkedIn Accounts → Connect new**.  Toggle stays on **Hosted
   (Unipile)** by default.  Enter a label, click **Connect via Unipile**.
3. New tab opens at Unipile's hosted login.  Complete the LinkedIn
   login there.
4. Modal in our app should auto-close as soon as Unipile fires
   `account.connected` → our handler stamps `unipile_account_id` + flips
   status to OK.  (Polling fallback at 3s intervals catches webhook
   delivery delays.)
5. Tail logs to confirm:
   ```powershell
   docker compose logs -f backend | findstr /I unipile
   ```

### 8. Run a campaign step

Re-enroll the lead from the Activity tab and watch:

```powershell
docker compose logs -f worker | findstr /I "send_linkedin_step Unipile"
```

A `view_profile` should complete in 1–3 seconds (single HTTPS call to
Unipile) vs. the old 9-second-then-challenge pattern.

### Troubleshooting

| Symptom | Probable cause | Fix |
|---|---|---|
| `401 invalid auth header` in backend logs after Unipile send-test | `UNIPILE_WEBHOOK_SECRET` in `.env` doesn't match the `X-Unipile-Auth` header value in Unipile's webhook config | Re-copy the secret into `.env`, force-recreate, OR re-create the webhook via the API with the matching value |
| Unipile dashboard shows delivery `timeout` | Tunnel died, or `WEBHOOK_BASE_URL` host doesn't match Unipile's webhook URL host | Restart tunnel, update all three webhook URLs in Unipile + `WEBHOOK_BASE_URL`, force-recreate |
| `account.connected` fires but row never flips OK | Unipile sent the event with an empty `name` | Check the delivery payload in Unipile's dashboard for `name == <our local UUID>`; if blank, ensure the `connect-via-unipile` endpoint successfully called `create_hosted_auth_link` with `name=str(acc.id)` |
| `UnipileError: UNIPILE_DSN not set` | `.env` not loaded into the container | Did you force-recreate?  `docker compose restart` won't do it |

