# Email Blaster — Claude session context

Running state of the project so any Claude session (this one or a future one)
can pick up where the last one left off. The remaining-work roadmap is in
[`docs/roadmap.md`](docs/roadmap.md); this file is the snapshot.

> **Convention:** update this file at the end of every meaningful task.
> Move finished work into "Last completed", pull the next item from the
> roadmap into "Next up", note anything surprising under "Conventions and
> gotchas", and refresh the test counts.  When a new task displaces
> "Last completed", move the old entry to
> [`docs/claude-history.md`](docs/claude-history.md) (top of the task
> log) instead of stacking "Previously" entries here — keep this file
> a snapshot, not a changelog.

---

## Mission

AI-powered cold email + multi-channel outreach. Upload CSV → research each
lead → Claude composes a personalized email → multi-step sequence (email +
delays + later LinkedIn) → schedule + send → track replies → analyze.

Phase 1 (email-only) shipped. We're now in **Phase 1.5 — multi-step
sequences + LinkedIn outreach**, broken into M1–M5 in the roadmap.

## Where we are

- **Last completed:** **Multi-tenancy — workspaces, users, owners,
  per-workspace credentials.**  Full design + checklist in
  [`docs/tenancy.md`](docs/tenancy.md).  Branch `multitenancy`
  (commits P0–P7):
  - **Identity (0044):** `tenants` (UI "Workspace"), `users` (argon2id),
    `memberships` (owner/admin/member + per-member email/digest prefs),
    `user_sessions` (httpOnly `eb_session`, SHA-256 stored), `invitations`,
    `auth_tokens`.  `/auth/*` (register/login/logout/me/switch-workspace/
    forgot/reset/invite) + `/team/*`.  Existing data → bootstrap
    workspace owned by `OWNER_NOTIFY_EMAIL` (password via
    `python -m app.scripts.set_password <email>`).
  - **Isolation:** `TenantMixin.tenant_id` (NOT NULL, 0045/0047) on 42
    tables; ORM auto-scoping (`app/tenancy/scoping.py`); Postgres RLS
    (0049) under the non-owner `APP_DATABASE_URL` role
    (`app.scripts.bootstrap_db`); workers via `@in_record_tenant` /
    `run_per_tenant`; per-workspace webhook URLs.
  - **Owners (0046):** `OwnedMixin.owner_id` on leads/campaigns/opps/
    activities/accounts/contacts/reports, composite FK to memberships;
    `POST /owners/assign` bulk, `?owner=me|unassigned|<id>` filters,
    notifications route to the record owner; per-member digest;
    `OWNER_NOTIFY_*` retired.
  - **Credentials (0048):** `tenant_provider_keys` (Fernet); Settings →
    Integrations UI (save/test/remove, webhook secret, register Unipile
    webhooks).  No `.env` fallback — provider keys are gone from
    `Settings`; 0048 seeded the bootstrap workspace from `.env`.
  - Verified end-to-end on a copy of the real DB under RLS (all 10
    campaigns / 9,168 leads / 6 integrations intact; a new signup is
    empty + isolated; invite → teammate sees data; assign → "My leads").
  - Applied to the dev DB 2026-09-27 (backup in `backups/`); counts
    verified identical before/after.
  - **Then: Muse over MCP** — `/mcp` + workspace API keys (0050), 42
    pinned tools (incl. reports + report builder, campaign analytics, human-gated research→draft→confirm→send via
    `outreach_drafts`, 0051), named tunnel `outreach.blueprintautomation.tech`; see
    [`docs/muse-mcp.md`](docs/muse-mcp.md).

- **Recent highlights** (full task-by-task history is archived in
  [`docs/claude-history.md`](docs/claude-history.md)):
  - **Research token-cost cuts:** `RESEARCH_WEB_SEARCH_MAX_USES` 2→1;
    suppression pre-scrub in `research_lead_async` (suppressed leads skip
    research + compose entirely); company-level research cache keyed
    `company::<domain>` so same-domain leads reuse company research.
  - **Sequencer fixes:** inter-step wait re-anchored to the actual send
    time on advance (stopped the follow-up cascade); deferred gate-trips
    no longer write execution rows; atomic Redis `SET NX` claim
    (`seq:emailsent:{lead}:{node}`) makes email sends at-most-once.
  - **Beat-driven first-email pacer** (`send.pace_first_emails`) replaced
    far-future-eta dispatch — any `apply_async(eta=…)` beyond the 300s
    broker visibility_timeout gets redelivered into duplicates.
  - **UI refinement phases 1–6 complete** (tokens → primitives →
    shell → motion → states → a11y/QA); QA report + deferred follow-ups
    in [`docs/ui-refinement-qa.md`](docs/ui-refinement-qa.md); design
    system in [`docs/design-system.md`](docs/design-system.md).
  - **CRM extension phases 1–6 complete** (configurable pipeline +
    Kanban, metadata-driven report builder, unified analytics/dashboard,
    audit log); QA in [`docs/crm-extension-qa.md`](docs/crm-extension-qa.md).
  - **Also shipped** (see archive for detail): CRM & inbox AI agent,
    nonprofit funding discovery + contact enrichment queue, signal
    outreach + bulk add-to-campaign, Social Listening Radar, ICP
    lookalikes, deliverability guard, reply-driven copy insights,
    research cache, template/no-research campaign modes,
    Research-a-client, CRM reporting tab, opportunity record pages.

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
  - **Multi-tenant follow-ups:** per-user read state for workspace-wide
    notifications (read_at is per row today); owners on connected
    inboxes / LinkedIn accounts; email verification on signup; move
    heavy per-tenant beat loops (intent collectors, funding) to fan-out
    tasks once workspaces multiply; drop the legacy global webhook
    routes once every workspace uses its per-workspace URL.
  - **Cross-cutting cleanup** from `docs/roadmap.md#cross-cutting-tasks`
    (sequence templates, named node_modules volume).
  - **Phase 2** — whatever you have in mind next.

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · async SQLAlchemy 2 · Celery 5 |
| DB / cache | PostgreSQL 15 · Redis 7 |
| AI compose | Anthropic Claude Sonnet 4.6 (`ANTHROPIC_MODEL`) |
| AI research | Anthropic Claude Haiku 4.5 (`ANTHROPIC_RESEARCH_MODEL`) — cheaper; extraction only |
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
- **The legacy `compose → send_lead` path is the source of truth for the
  FIRST email — but ONLY when the entry node is an email node.** The new
  sequencer skips email entry nodes whose `lead.send_status` is already
  SENT and advances to the next node.  Since the legacy pipeline isn't
  sequence-aware, it's gated on the entry kind via
  `sequence_service.campaign_sends_legacy_first_email`: a non-email start
  node (LinkedIn / wait / ...) makes `compose` skip the AI call + send
  (`skipped_non_email_entry`) and `confirm_upload` auto-launch into
  RUNNING (no email preview).  Research still runs.  `is_legacy_first_email_node(None)`
  returns True (backward-compatible default) so a campaign with no live
  entry node still composes.  Don't remove the legacy path without a
  migration plan.

## Conventions and gotchas

- **Everything is workspace-scoped (multi-tenancy — read
  [`docs/tenancy.md`](docs/tenancy.md)).**  Rules of thumb:
  - Feature routers are authenticated router-wide (`get_tenant_context`
    in `app/main.py`); keep using `Depends(get_db)`.  Only auth /
    webhooks / unsubscribe may use `get_public_db` (test-enforced).
  - ORM queries are auto-filtered to the current workspace; new rows are
    auto-stamped.  Don't hand-write `tenant_id` filters in feature code,
    and never `skip_tenant_filter` outside the allowlist.
  - New customer-data table → `TenantMixin` (+ `OwnedMixin` if it has an
    accountable member) AND add it to an RLS migration; the schema + RLS
    tests fail otherwise.  Add it to the conftest TRUNCATE list.
  - Celery: record tasks decorate their async body with
    `@in_record_tenant(kind, arg)`; periodic tasks run via
    `run_per_tenant(body)`.  Open DB sessions INSIDE the context (a
    transaction keeps the RLS `app.tenant_id` it began with).
  - Provider keys: `credentials.require("brevo")` / `.get("hunter")` —
    never `settings.*_API_KEY` (those fields no longer exist).  Missing →
    `MissingCredential` → HTTP 409 `integration_not_configured`.
  - Shared-namespace Redis keys (domain / LinkedIn page / funding source /
    Brevo watermark) go through `app/tenancy/keys.py`.
  - Tests run as `DEFAULT_USER` (owner of `DEFAULT_TENANT`) via the real
    cookie path (`client`; `client_factory(token)` / `create_test_user` for
    more users/workspaces).  Tests have NO provider credentials unless
    they call the `set_creds` fixture.  `APP_DATABASE_URL` is forced
    empty in tests (owner role); RLS has its own test DB
    (`test_rls_isolation.py`).
  - Owners: `owner_id` defaults to the acting user, else parent
    (campaign → its leads, lead → converted deal, deal → tasks), else the
    workspace's primary owner (background rows).  Visibility is
    workspace-wide regardless of owner.
- **Muse / MCP agent access** ([`docs/muse-mcp.md`](docs/muse-mcp.md)).
  `POST /mcp` (stateless JSON-RPC, Streamable HTTP) authenticated by a
  workspace API key (`Authorization: Bearer eb_…`, hashed in `api_keys`,
  verified via SECURITY DEFINER `api_key_verify()`); a key acts as its
  creator.  Tools in `app/mcp/tools.py` MUST go through the existing API
  routes (in-process), never straight to the DB, and the pinned list in
  `tests/test_mcp.py` must be edited deliberately — nothing that
  launches, deletes, or manages team/keys/integrations.  The ONLY send
  path is `send_outreach`, gated server-side by `/outreach-drafts/{id}/
  confirm` (one-time code bound to the draft version) + `user_approved`.  New endpoints
  that manage credentials/team use `require_session`/`require_manager`
  (they refuse API keys).
- **Public origin = named Cloudflare tunnel** `outreach.blueprintautomation.tech`
  (tunnel `outreach`, `scripts/named-tunnel-setup.sh`,
  `docker-compose.named-tunnel.yml`).  It forwards only `/mcp`,
  `/webhooks/*`, `/unsubscribe/*`, `/health`.  `.env` has
  `PUBLIC_ORIGIN` + `WEBHOOK_BASE_URL` pointing at it — bring the stack up
  with `-f docker-compose.yml -f docker-compose.named-tunnel.yml`.  This
  replaces ngrok / `scripts/dev_tunnel.py` for day-to-day use.
- **Two DB roles.**  `DATABASE_URL` = owner (Alembic, `set_password`,
  `bootstrap_db`); `APP_DATABASE_URL` = non-owner runtime role that RLS
  binds (backend/worker/beat).  The backend runs `bootstrap_db` on start
  to create/refresh the role + default privileges.  Migrations:
  `docker compose run --rm backend alembic upgrade head`.  A startup
  warning fires if the runtime role can bypass RLS.
- **Publishing a sequence never launches a campaign.**  Save & Publish
  only flips `sequences.is_published`; the status machine's sole
  draft-exit is CSV `confirm-upload`.  Campaigns whose leads arrive by
  COPY need an explicit launch — retarget paths call
  `retarget.launch_retarget_campaign` (marks samples, DRAFT →
  PREVIEWING/RUNNING, kicks research).  Leads-page / Signals-page
  "Add to campaign" into a DRAFT target still holds the rows with no
  launch path — same latent gap if anyone ever adds-to-draft there.
- **Connected accounts:** `email_address` is for display; `username` is
  the IMAP login. Gmail aliases share their parent mailbox — set
  `email_address` to the alias and `username` to the primary mailbox.
  The connect modal has an inline hint about this
  ([`ConnectInboxModal.jsx`](frontend/src/components/ConnectInboxModal.jsx)).
- **Encryption invariant:** only `app/services/imap_client.py` (inbox
  passwords) and `app/services/credentials.py` (workspace provider keys)
  may call `encryption.decrypt(` (allowlist `{imap_client.py,
  encryption.py, credentials.py}`).  A test in `test_phase15_hardening.py`
  greps the codebase to enforce.  `encryption` is a MultiFernet:
  `ENCRYPTION_KEY_OLD` keeps decrypting during a key rotation.
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
  `test_phase15_hardening.py` lists `imap_client.py`, `encryption.py`,
  `credentials.py`.
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
- **A new setting in `.env` alone does NOT reach the containers.**
  `docker-compose.yml` passes env via an explicit per-service
  `environment:` allowlist (`VAR: ${VAR:-default}`), NOT `env_file`,
  so only enumerated vars are injected.  The app's pydantic `Settings`
  then falls back to its *code default* for anything missing — which
  silently masks the problem when the `.env` value happens to equal the
  default.  Symptom: you set `FOO=...` in `.env`, recreate, but
  `docker compose exec worker printenv FOO` is empty and changes to it
  never take effect.  Fix: add the var to the `environment:` block of
  **all three Python services** (`backend`, `worker`, `beat`) as
  `FOO: ${FOO:-<default>}`, then `docker compose up -d --force-recreate
  backend worker beat`.  (This is how `LINKEDIN_STAGGER_SECONDS` was
  wired — it read 120 from the default until added to the compose
  blocks.)  `docker compose exec worker printenv FOO` returning the
  value is the real confirmation; `settings.FOO` matching can be a
  false positive when it equals the default.
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
- **"Open rate by send week" counts every email, attributed by Brevo
  `messageId`.**  `analytics._send_cohorts` buckets first emails
  (`leads.brevo_message_id`) AND sent email/email_reply steps
  (`lead_step_executions.external_id`) by the week they went out; an
  open counts for the email whose `event_data->>'messageId'` it carries
  (old events with no messageId fall back to the lead's first email).
  It used to count first emails only, so a campaign whose first emails
  all went out in one week showed a single row forever.
- **Activity tab dedupes consecutive same-(lead, node) rows.**  The
  `/campaigns/{id}/activity` endpoint pulls 100 raw rows from
  `lead_step_executions`, clusters them by `(lead_id, node_id)`
  preserving desc-by-time order, then returns up to 30 clusters with
  an `attempt_count` field.  The UI shows a small ``×N`` badge next to
  the step name and a tooltip with the earliest + latest attempt time.
  Spec / wire format in `SequenceStepEvent` schema; tests in
  `test_phase29_activity_clustering.py`.
- **LinkedIn step has a stale-dispatch guard.**  After a worker restart
  (or a manual ``next_run_at`` nudge while a Celery task was in
  ``unacked``), the same ``send_linkedin_step`` task could re-deliver
  with its original ``node_id`` even though the lead's cursor had
  advanced.  Without a guard, this fired Unipile a second time (real
  duplicate ghost-view / connect / DM).  Fix: ``_send_linkedin_step_async``
  checks ``state.current_node_id == node_id`` at the top; mismatch →
  returns ``{"status": "stale_dispatch"}``, no API call, no rate-slot
  burn.  ``_record_execution_and_advance`` recognises that status and
  writes no execution row + leaves the cursor untouched.
- **LinkedIn steps honour the campaign schedule window + paused state.**
  `_send_linkedin_step_async` checks `campaign.status == PAUSED` and
  `compute_next_send_window(campaign)` up front; outside-window /
  paused → returns `{"status": "deferred", "reason": "scheduled"|"paused",
  "retry_at": ...}` BEFORE `_li_rate_acquire` runs, so a deferred lead
  doesn't burn a LinkedIn rate slot.  `view_profile` respects the
  window too — even though it's exempt from the daily-action cap, an
  account that only operates in business hours should "look human" to
  LinkedIn's behavioural scorer.  Defers to the next window-open ETA
  the same way the email step does.
- **Follow-up email step honours send gates.**  `_send_email_step_async`
  reuses `send.check_send_gates` so suppression / paused / schedule /
  Brevo rate-limit checks behave identically to the legacy first-email
  path.  A tripped gate returns `{"status": "deferred", "reason": "...",
  "retry_at" | "retry_in": ...}`; `_record_execution_and_advance` parks
  the lead on the current node and reschedules to the exact retry time
  the gate returned WITHOUT consuming `MAX_TRANSIENT_RETRIES`.  A
  campaign paused for a day no longer silently advances past every
  follow-up.  Brevo rate counters bump on the follow-up step too so
  steps are metered together with the first email.
- **`send_lead_async` holds a row-level lock through the Brevo POST.**
  `select(...).with_for_update()` on the lead row keeps a concurrent
  invocation (rate-limit retry colliding with a beat-dispatched task)
  blocked on the lock until the commit; the second one then reads
  `SendStatus.SENT` and short-circuits.  Defends against the duplicate-
  send race the audit flagged.
- **Pause is a HARD STOP.**  When `send_lead_async` returns `{status:
  paused}`, the Celery wrapper does NOT self-re-enqueue (`workers/send.
  py:370` — was `apply_async(countdown=300)`, now an early `return`).
  Previously every paused-campaign queued task was respawning every 5
  min, holding tens of thousands of zombie tasks for as long as the
  campaign stayed paused.  Now the task is acked-and-gone; the lead's
  `send_status` is left at whatever the gate left it (PENDING for
  legacy first-email, SCHEDULED if a prior pass deferred it on the
  window), and `resume_campaign` re-enqueues every composed
  PENDING+SCHEDULED lead via staggered `send_lead.apply_async(eta=...)`
  — same pattern as `_kick_off_full_campaign`.  **Resume IS the
  trigger; pause is the off switch.**  Required because nothing reads
  `lead.scheduled_send_at` (it's a write-only DB stamp); without the
  resume re-enqueue, SCHEDULED leads are orphans forever.  Sequencer-
  driven follow-up / LinkedIn steps don't need this same treatment —
  the beat already filters `Campaign.status == RUNNING`, so paused
  campaigns aren't visited.  Remediation for pre-fix orphans:
  `backend/scripts/reenqueue_stuck_scheduled.py <campaign-id>`.
- **The email min-delay gate is an atomic CLAIM, not a read.**
  `check_rate_limits` does `SET rate:{cid}:min_gate <now> NX EX
  min_delay` — whoever sets it first owns that `min_delay` window; the
  rest get the key's TTL as `retry_in`.  This is what stops Celery
  prefork (N tasks at once) from all passing a read-only check and
  bulk-sending.  Consequences: (1) it has a SIDE EFFECT inside a
  "check" function, so it's the LAST gate (after suppression/paused/
  window) — only claimed when everything else passed; (2) a send that
  then fails at Brevo still "wastes" that window (acceptable over-
  spacing, never under); (3) `increment_rate_counters` only bumps
  hour/day now — the old `rate:{cid}:last_sent` key is gone.
  `SET NX EX` is atomic on fakeredis too, so no Lua / real-Redis test
  swap was needed (unlike the LinkedIn limiter).  approve-all also
  pre-spaces dispatch by `min_delay` (`apply_async eta`) so the gate
  rarely has to bounce in the common in-window case.  The daily cap
  counter (`rate:{cid}:day`) expires at the next local midnight in the
  campaign tz (`_seconds_until_local_midnight`), so "daily" is a
  calendar day — same as LinkedIn; the hourly counter stays rolling 60m.
- **Unsubscribe link is HMAC-signed + POST-confirm.**  GET renders a
  confirm page (no side effect), POST applies the suppression.  Token is
  `hmac_sha256(SECRET_KEY, lead.id.bytes)[:32]` so email-scanner GET
  prefetchers (Outlook/Defender) can't auto-unsubscribe leads, and
  knowing one lead's URL doesn't let an attacker forge another's.
  `make_unsubscribe_url(lead_id)` is the helper for templates that
  embed the link.
- **Unipile webhook is now idempotent.**  ``webhook_events(provider,
  tenant_id, event_id)`` table (unique NULLS NOT DISTINCT) (migration 0007) records every incoming Unipile
  event id and acts as the dedup guard.  The route inserts the row in
  its own transaction up front; UniqueViolation → ``{"ok": True,
  "duplicate": True}`` with no handler dispatch.  When Unipile sends a
  payload with no id we fall back to a SHA-256 of the raw body so
  byte-identical retries still dedup.  Trade-off: a handler crash
  after the dedup commit drops Unipile's retry — at-most-once on our
  side — which is intentional and replaces the previous at-least-once
  with double-apply.
- **Stuck RUNNING leads get swept back to PENDING.**  ``lead_sweeper``
  beat task every 5 min flips ``compose_status`` / ``research_status``
  RUNNING rows whose ``updated_at`` is older than
  ``STALE_AFTER_MINUTES`` (15) back to PENDING and re-enqueues the
  matching Celery worker.  Defends against the "worker crashed
  between RUNNING-commit and final-commit" pattern that previously
  left rows invisible to ``/retry-failed`` (which only sees FAILED).
- **LinkedIn rate-limit acquire is atomic.**  ``_li_rate_acquire``
  runs a Lua script in Redis that does min-delay + daily-cap + per-kind
  subcap + per-page-monthly check + counter bump as ONE operation.
  Two concurrent ticks can no longer both pass under a cap of one.
  Trade-off: a failed action still counts the slot (no refund) — a
  cleaner over-count than the previous race that double-fired.
- **The sequencer beat only drives RUNNING campaigns.**
  `_advance_sequences_async` filters `Campaign.status == RUNNING`, so a
  paused campaign freezes in place (leads keep their node + next_run_at,
  no dispatch, no stagger-slot consumption).  The per-step handlers
  still re-check paused as belt-and-suspenders.  Two consequences:
  (1) a campaign paused mid-flight resumes exactly where it left off;
  (2) the beat auto-resumes campaigns that were cap-auto-paused — it
  runs an `UPDATE campaigns SET status=RUNNING WHERE status=PAUSED AND
  auto_paused_until <= now` at the top of each tick (same transaction
  as the SELECT, so resumed leads run immediately).  Manual pauses have
  `auto_paused_until IS NULL` and are never auto-resumed.
- **LinkedIn cap auto-pauses a campaign (`auto_paused_until`).**  When a
  LinkedIn step hits `daily_cap`/`connect_cap`/`dm_cap` and no email
  node is reachable downstream (`_has_downstream_email`), the campaign
  is paused with `auto_paused_until` = cap-reset time and auto-resumes
  then.  Gated on `node.kind in LI_KINDS` — `daily_cap` is ALSO the
  Brevo email-gate reason, so without that gate an email rate-limit
  would wrongly pause the campaign.  Email-bearing sequences keep
  running at the cap (email isn't LinkedIn-capped).
- **Transient LinkedIn failures park-and-retry; only permanent ones
  skip the lead.**  A failed action's `ActionResult.meta` now carries
  `http_status` (the Unipile HTTP status; network failures use the
  synthetic `0`).  `_send_linkedin_step_async` classifies via
  `_is_transient_failure`: 5xx / 0 (network) / 429 / 3xx (stray
  redirect) → status `transient_error` (in `TRANSIENT_SKIP_STATUSES`,
  so it parks on the node and retries every `TRANSIENT_RETRY_MINUTES`
  up to `MAX_TRANSIENT_RETRIES`, then advances).  Specific 4xx client
  errors (invalid recipient, profile locked, already-invited, ...) and
  unknown/absent status stay `failed` → advance the cursor (permanent
  skip), as before.  `transient_error` maps to a SKIPPED execution row
  so it counts against the per-visit retry budget like the other
  transient statuses.
- **The Unipile httpx client follows redirects (`follow_redirects=True`).**
  A LinkedIn public slug with accented characters (e.g.
  `rahnà-wakę-...`, `josé-...`) makes Unipile **301-redirect**
  `GET /api/v1/users/{slug}` to a Unicode-normalized (NFD +
  percent-encoded) URL.  httpx does NOT follow redirects by default, so
  without this the client returned the 301's HTML "Redirecting" page,
  which surfaced as `{"status": "failed", "error": "<!DOCTYPE html>…"}`
  — and since a failed LinkedIn step *advances the cursor*, the lead
  got skipped past the connect without ever sending it.  If you ever
  rebuild the client kwargs, keep `follow_redirects=True`.  (One-off
  remediation when this bit us: find `lead_step_executions` with
  `result='failed'` and `error ILIKE '%DOCTYPE%'` on connect nodes,
  then reset those leads' `current_node_id` back to the live connect
  node.)
- **The LinkedIn daily cap resets on the calendar day (campaign tz),
  not a rolling 24h.**  `_li_rate_acquire(daily_ttl_seconds=...)` is
  passed `_seconds_until_midnight(campaign.schedule_timezone)`, so the
  `li-rate:{aid}:day[:connect|:dm]` counters expire at local midnight
  (set NX on the day's first action).  A "new day" lifts the cap.  The
  per-account counter's TTL is set by whichever campaign acts first
  that day (its tz wins) — fine for the common single-tz case.  When
  changing this, note the auto-pause `retry_in` (and thus
  `auto_paused_until`) rides on this TTL, so the campaign auto-resumes
  at the next local midnight + a 60s buffer.
- **LinkedIn dispatch is staggered, not bulk — with DISTINCT slots.**
  The sequencer beat releases at most one LinkedIn step per account per
  `LINKEDIN_STAGGER_SECONDS` (default 120; 0 disables).  Due leads
  beyond the open slot are PARKED (`next_run_at` set to a future slot)
  without dispatching.  Slot reservation is an atomic Redis Lua
  (`_LI_STAGGER_LUA` via `_reserve_li_stagger_slot`) using TWO keys per
  account: `li-stagger:{key}:last` (last *dispatch* time — a parked
  lead becomes dispatch-eligible when `now >= last + interval`, so it
  fires when its slot arrives with NO push-back) and
  `li-stagger:{key}:hw` (high-water of slots handed out — new parks go
  to `hw + interval`, giving every waiting lead a DISTINCT timestamp).
  The two-key design matters because the beat runs (60s) more often
  than the interval (120s): a naive single "next slot = last + interval"
  piled every non-dispatch tick's leads onto the same timestamp
  (looked like a simultaneous batch in the UI even though dispatch was
  correctly one-per-interval).  The worker rate limiter
  (`_li_rate_acquire`) is now just a backstop, rarely hit.  Because the
  beat uses Redis, `advance_sequences` resets `_LI_REDIS_CLIENT=None`
  at task entry (fresh client per `asyncio.run` loop) — same caveat as
  the worker tasks.  Staggering is per-account so distinct accounts run
  in parallel; only LinkedIn kinds (`LI_KINDS`) are staggered, email
  dispatch is unaffected.  `view_profile` (and anything in
  `_UNCOUNTED_ACTION_KINDS`) is exempt from BOTH the daily cap and the
  staggering — low-risk reads fire promptly — but the per-action
  min-delay still applies to them as a burst guard.  Note: leads
  newly advanced into a node via `_advance_cursor` get `next_run_at =
  now`, so a batch arriving in one tick momentarily shares a timestamp
  until the next tick re-spreads them — a transient, not a pile.
- **Brevo events come from polling (per workspace, with that workspace's
  key); the per-workspace webhook `/webhooks/brevo/{tenant_id}` is an
  optional real-time add-on.**  The inbound
  `/webhooks/brevo` route was removed; events are pulled from
  `GET /v3/smtp/statistics/events` by `brevo_events_poller.poll` every
  `BREVO_EVENTS_POLL_INTERVAL_MINUTES` (default 10).  Trade-off: up to
  10 min lag from event-at-Brevo to event-row-in-DB.  Reasoning:
  Brevo's outbound event webhook is gated behind paid plans on some
  tiers + needs a public tunnel; polling is free with the regular API
  key and works behind the localhost-only port binding.  Shared
  ``app.services.brevo_events.process_event`` does the lead lookup +
  suppression-table write + event-row insert; the poller is the only
  caller now.  Watermark is in Redis at `brevo:events:last_polled_at`
  with a 24h lookback floor and 5-min overlap between polls.  Per-event
  dedup is built into ``process_event`` so re-fetching the same day
  window (day-granularity API) doesn't double-insert.
- **Brevo terminal events dedup per EMAIL (messageId), not per lead.**
  `process_event` skips a delivered/bounce/spam/unsub/blocked event only
  if the lead already has that type for the SAME messageId (legacy rows
  with no messageId count as the first email's).  Per-lead dedup silently
  dropped every follow-up's "delivered" (fixed 2026-10-01; 3,663 rows
  recovered via `scripts/backfill_brevo_events.py`, now tenant-aware with
  `--dry-run`).  Brevo's `requests` event (accepted, pre-delivery) is no
  longer mapped to DELIVERED.
- **All four ports bound to `127.0.0.1` only.**  `docker-compose.yml`
  uses `"127.0.0.1:8000:8000"` etc. so the unauthenticated API can't
  be reached from LAN.  Tunnel (ngrok/cloudflared) still works because
  it forwards via the host loopback.
- **Sequencer parks on async-event edges.** When an action step succeeds
  but no outgoing edge condition currently matches, the sequencer
  parks the lead on the current node instead of halting — as long as
  at least one unmatched edge uses a "deferrable" op (one of
  `linkedin_connection`, `replied`, `opened`, `clicked`, `bounced`,
  `days_since_entered_node`).  Re-evaluation runs every
  `EDGE_WAIT_RETRY_MINUTES` (30) up to `MAX_EDGE_WAIT_DAYS` (14), then
  halts with a clear reason.  This is how `linkedin_connect → DM
  (when linkedin_connection=connected)` waits for the prospect to
  accept the invite before firing the DM.  Re-dispatch is suppressed
  while parked via `_already_executed_this_visit` (checks for a SENT
  `lead_step_executions` row since `entered_current_at`).  Negations
  (`not replied`) and compound (`and`/`or`) conditions do NOT trigger
  parking — they halt immediately, preserving the "skip on reply" pattern.
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

_Last updated: 2026-09-27 — multi-tenancy (workspaces, users, owners,
per-workspace credentials, RLS) on branch `multitenancy`; see
[`docs/tenancy.md`](docs/tenancy.md).  (2026-07-27: compacted this file;
task-by-task history lives in [`docs/claude-history.md`](docs/claude-history.md).)_

_Backend tests: **1384 passing** (+2 pre-existing failures in phase34/phase56, unrelated — fail on clean checkout).  Frontend tests: **507 passing**._

> **🚀 Starting on a fresh dev box?** Jump to
> [Unipile setup runbook](#unipile-setup-runbook-any-computer-local-dev)
> below — it walks through every step (ngrok / cloudflared tunnel,
> per-workspace Unipile connection + webhooks) needed to get the stack
> talking to Unipile on a new machine.

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

3. **Webhook handler** `POST /webhooks/unipile/{tenant_id}` (per
   workspace; the legacy `POST /webhooks/unipile` resolves the workspace
   from the payload's account) in `routers/webhooks.py`.  Unipile doesn't
   HMAC-sign bodies — auth is a static custom header.  Handler reads
   `request.headers[settings.UNIPILE_WEBHOOK_AUTH_HEADER]` (default
   `X-Unipile-Auth`) and constant-time-compares against THAT WORKSPACE's
   stored Unipile webhook secret.  Routes events: `account.connected`,
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

It detects (or starts) ngrok pointing at `localhost:8000`, sets
`WEBHOOK_BASE_URL` in `.env` and force-recreates `backend`/`worker`/
`beat` when it changed.  Unipile credentials + webhook secrets are per
workspace now, so it no longer touches Unipile: afterwards click
**Settings → Integrations → Unipile → "Register webhooks automatically"**
in each workspace (creates the three webhooks — `messaging`,
`account_status`, `users` — at `<tunnel>/webhooks/unipile/<workspace
id>` with the workspace's secret header).  Flags: `--dry-run`,
`--no-recreate`, `--port N`.

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

### 3. Connect Unipile to the workspace (Settings → Integrations)

Unipile credentials are **per workspace**, not in `.env`:

1. Set `WEBHOOK_BASE_URL=https://<tunnel-host>` in `.env` (or run
   `python3 scripts/dev_tunnel.py`), then
   `docker compose up -d --force-recreate backend worker beat`
   (`restart` does NOT re-read `.env`).
2. Open <http://localhost:5173/settings?tab=integrations> → **Unipile**
   → enter the DSN + access token → Save.  A webhook secret is generated
   automatically.
3. Click **Test connection**, then **Register webhooks automatically** —
   it creates the three Unipile webhooks (`messaging`, `account_status`,
   `users`) pointing at `<WEBHOOK_BASE_URL>/webhooks/unipile/<workspace
   id>` with the `X-Unipile-Auth: <workspace secret>` header.  (Manual
   alternative: the card shows the URL + header, and "Reveal secret".)

**Verified working `source` values (2026-05-14):**

| `source` | Covers |
|---|---|
| `messaging` | new chat messages (DM replies) |
| `account_status` | Unipile account state changes (`account.connected`, `account.checkpoint`, `account.disconnected`) |
| `users` | new relations (`invitation.accepted`, new connections) |

To list / delete webhooks by hand: `GET` / `DELETE`
`https://$DSN/api/v1/webhooks[/<id>]` with the `X-API-KEY` header.

### 4. Sanity-check the tunnel is reachable

```bash
# Wrong secret → 401 (proves auth fires AND routing works)
curl -X POST "https://<tunnel>/webhooks/unipile/<workspace id>" \
  -H "Content-Type: application/json" -H "X-Unipile-Auth: wrong" -d '{}'
# Expected: 401 {"detail":"invalid auth header"}
```

Then hit **Send test event** on each Unipile webhook and watch
`docker compose logs -f backend | grep -i unipile`.

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
| `401 invalid auth header` in backend logs after Unipile send-test | The webhook's `X-Unipile-Auth` value isn't this workspace's stored secret (or the legacy global URL couldn't map the account to a workspace) | Settings → Integrations → Unipile → "Register webhooks automatically" (recreates them with the right URL + secret) |
| Unipile dashboard shows delivery `timeout` | Tunnel died, or `WEBHOOK_BASE_URL` host doesn't match Unipile's webhook URL host | Restart tunnel (`scripts/dev_tunnel.py`), then "Register webhooks automatically" in each workspace |
| `account.connected` fires but row never flips OK | Unipile sent the event with an empty `name` | Check the delivery payload in Unipile's dashboard for `name == <our local UUID>`; if blank, ensure the `connect-via-unipile` endpoint successfully called `create_hosted_auth_link` with `name=str(acc.id)` |
| `UnipileError: UNIPILE_DSN not set` / 409 `integration_not_configured` | The workspace hasn't connected Unipile | Settings → Integrations → Unipile |

