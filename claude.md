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

- **Last completed:** **Stop HTTP poller from invalidating the li_at.**
  - **Default provider switched from `hybrid` → `playwright`.** Confirmed
    via logs (2026-05-13 19:17–19:19) that the hybrid HTTP poller hits
    LinkedIn from the Docker host's datacenter IP every 5 min with the
    user's home-IP-bound `li_at`. LinkedIn flags the cookie globally,
    so the next Playwright action fails with "session expired" and the
    user has to re-paste. Playwright-only sends everything through the
    browser session so the cookie stays valid.
  - **`_ensure_authenticated` no longer falls back to password login**
    when stored cookies fail to authenticate. Headless password login
    almost always trips LinkedIn's bot-detection challenge. Now raises
    `ChallengeRequired` immediately when cookies are stored but invalid;
    password login only runs on true first-time setup (no stored cookies).
  - **`_run` now keeps the full Playwright `storage_state`** (minus
    `JSESSIONID` only) across sessions. Previously stripped everything
    except `li_at`, which meant LinkedIn saw a "new browser" every run
    (no `bcookie`/`bscookie` continuity) and trip-redirected.
  - **`_run` auto-clears `pending_challenge_url` and resets status**
    from `CHALLENGED|FAILED|UNTESTED → OK` on a successful action.
  - **`_ensure_authenticated` navigates to root → feed** in two hops
    rather than straight to `/feed/` — lets LinkedIn issue a fresh
    `JSESSIONID` + `bcookie` before we ask for protected content.
  - Three provider impls remain behind `LinkedInProvider` ABC:
    - `"playwright"` **(default)** — all actions via headless Chromium.
      Safe without a residential proxy.
    - `"hybrid"` — Playwright writes + HTTP reads. **Only safe with
      `LINKEDIN_PROXY_URL` set;** otherwise the poller poisons the cookie.
    - `"http"` — legacy `linkedin-api` HTTP (testing/fallback only).
  Tests: **backend 365 passed**.

- **Previously:** **Hybrid LinkedIn provider + anti-detection hardening.**
  - **Three provider impls**, all behind `LinkedInProvider` ABC, selectable
    via `LINKEDIN_PROVIDER` env var:
    - `"hybrid"` (was default — see above) — HTTP for reads, Playwright for writes.
    - `"playwright"` — all actions via headless Chromium.
    - `"http"` — legacy `linkedin-api` HTTP (kept for testing/fallback).
  - **`hybrid_impl.py`** (`HybridLinkedInProvider`): all user-visible
    actions go to Playwright (test_connection, view_profile, follow_profile,
    react_to_post, all writes). Only `latest_post_urn` and
    `inbox_recent_events` use HTTP — they fail silently (skip/[]), so a
    server-IP JSESSIONID failure doesn't halt the sequence. Without
    `LINKEDIN_PROXY_URL`, LinkedIn won't issue JSESSIONID to datacenter
    IPs even with a valid li_at, so the HTTP bucket is intentionally small.
  - **`playwright_impl.py`** (`PlaywrightLinkedInProvider`): real headless
    Chromium + `playwright-stealth`. All Voyager API calls go through
    `page.evaluate()` fetch() so they run from the browser's IP/session.
    Adds 1.5–4s jitter after page load + 1–3s before each write action.
  - **`linkedin_api_impl.py`** updated: `_build_client_sync` now detects
    Playwright `storage_state` format (`{"cookies":[...],"origins":[...]}`)
    and extracts `li_at` from it, so HTTP reads work seamlessly after a
    Playwright write has stored the full browser session.
  - **Session format round-trip**: Playwright writes → stores `storage_state`;
    HTTP reads → extracts `li_at`, writes back `[{"name":"li_at",...}]`;
    Playwright writes → re-bootstraps from `li_at`, stores `storage_state`.
  - **Sequencer jitter**: `advance_sequences` dispatches LinkedIn steps
    with `apply_async(countdown=random.uniform(2, 8))` — avoids burst
    patterns when multiple leads fire simultaneously.
  - `LINKEDIN_DAILY_CONNECT_CAP` bumped from 15 → 20 (safe ceiling for
    established accounts; LinkedIn enforces ~100/week).
  - `playwright==1.49.0` + `playwright-stealth==1.0.6` in
    `requirements.txt`; Chromium baked into Docker image at
    `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`.
  Tests: **backend 365 passed**.

- **Previously:** **Activity tab — LinkedIn + sequence visibility +
  halted-lead re-enrollment.**
  - **Expanded `/activity` endpoint** now returns four extra scalar
    counts (`sequence_active/halted/completed/pending`) plus three new
    lists: `recent_sequence_steps` (last 30 `lead_step_executions`
    rows joined with node kind), `halted_leads` (leads whose sequence
    is halted, with halt reason), `upcoming_steps` (next 10 active
    leads sorted by `next_run_at`).
  - **New `POST /campaigns/{id}/re-enroll-halted`** resets all halted
    `lead_sequence_states` back to the live entry node (`next_run_at =
    now`). The sequencer auto-skips the already-sent email entry node
    on its next beat tick. Returns `{"re_enrolled": N}`.
  - **Activity tab** now shows: Sequence state counters (4 numbers),
    Halted leads panel with "Re-enroll all" button, Recent sequence
    steps table (step kind badge + result pill + error + when),
    Upcoming scheduled steps table — plus the existing email pipeline
    counters and feed, now relabelled "Email pipeline".
  - **Fixed pre-existing test break**: `test_phase9_send_task.py`
    patched the removed `_get_redis` symbol; updated to patch
    `_new_redis` instead.
  Tests: **backend 365 passed**, **frontend 153 passed** (1 pre-existing
  failure in `ConnectLinkedInModal.test.jsx`, unrelated).
- **In flight:** nothing.
- **Next up:** Test the hybrid provider end-to-end:
  1. Settings → LinkedIn Accounts → Test (Playwright does fresh browser login,
     stores `storage_state`).
  2. Run a campaign with a `linkedin_view_profile` step first — this uses
     the HTTP provider with the `li_at` extracted from the stored state.
  3. Run a `linkedin_connect` step — this uses the Playwright browser.
  If sessions still expire, consider a `"playwright"` provider for reads
  too, or increase `LINKEDIN_MIN_ACTION_DELAY_SECONDS` to space out actions.
- **Next up:** open. Suggested directions:
  - **Cross-cutting cleanup** from `docs/roadmap.md#cross-cutting-tasks`
    (sequence templates, multi-tenant readiness, named node_modules
    volume).
  - **Hybrid LinkedIn provider** — wire a hosted impl (Unipile etc.)
    behind the existing `LinkedInProvider` ABC if customer-account ban
    risk becomes a concern.
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
- **LinkedIn engine (M2):**
  - `app/services/linkedin/base.py` — `LinkedInProvider` ABC +
    `ProfileRef`, `ActionResult`, `InboundEvent`, `ChallengeRequired`,
    `AccountRestricted` types.
  - `app/services/linkedin/linkedin_api_impl.py` — concrete impl
    wrapping the `linkedin-api` PyPI package. **Only this module +
    `imap_client.py` may call `encryption.decrypt(`** (hardening test
    enforces). Decrypted password / cookies live in local scope only.
    Sessions auto-refresh via stored Fernet-encrypted cookies; on
    cookie expiry we re-login from the stored encrypted password.
  - `LinkedInAccount` table: per-account password + cookies + proxy +
    status (`untested|ok|failed|challenged|restricted`). Status flips
    to `challenged` when LinkedIn demands a captcha/PIN — user resolves
    in their own browser, posts `/resolve-challenge`, then re-tests.
  - Per-account rate limits via Redis: `LINKEDIN_DAILY_ACTION_CAP`
    (default 20) + `LINKEDIN_MIN_ACTION_DELAY_SECONDS` (default 90).
  - **M3 per-kind subcaps:** `LINKEDIN_DAILY_CONNECT_CAP` (15),
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
  `encryption.decrypt(`. A test in `test_phase15_hardening.py` greps the
  codebase to enforce this. Don't violate it.
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
  `test_phase15_hardening.py` lists `imap_client.py`,
  `encryption.py`, `linkedin_api_impl.py`. Adding a new credential
  consumer means updating this list AND keeping plaintext within
  the new module's local scope (delete before return).
- **linkedin-api library raw Voyager calls:** the upstream library
  doesn't expose `follow_profile`, `react_to_post`, `invite_to_page`,
  `send_inmail`, or `comment_on_post` helpers, so
  `linkedin_api_impl.py` makes raw `POST` requests against
  `voyager.linkedin.com/api/feed/...`,
  `.../growth/normInvitations`,
  `.../voyagerMessagingDashMessengerMessages?action=createMessage`
  (InMail), and `.../feed/dash/socialActions/{urn}/comments`. These
  endpoints aren't part of a stable contract and may break when
  LinkedIn changes their web app. Symptom: 4xx from those endpoints
  after weeks of working. Fix: inspect requests in the linkedin.com
  web app, update the endpoint/body shapes.
- **InMail premium-required path:** when LinkedIn returns 402/403
  with "InMail" or "premium" in the body, `_send_inmail_sync` returns
  `ok=False, premium_required=True` rather than raising. The
  sequencer maps that to a `skipped` execution row with a clear error
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
- **`session_cookies_encrypted` is now polymorphic**: three possible formats
  in the same column — all detected at read time:
  1. `{"cookies":[...],"origins":[...]}` — Playwright `storage_state` (has
     `"origins"` key). Written by Playwright/hybrid writes.
  2. `[{"name":"li_at","value":"..."}]` — HTTP cookie list. Written by
     HTTP reads (strips everything except `li_at` before persisting).
  3. `[{"name":"li_at","value":"..."}, {"name":"JSESSIONID",...}, ...]` —
     legacy full cookie jar (pre-hybrid). Handled by `_extract_li_at`.
  `_is_playwright_state()` in `playwright_impl.py` and the updated
  `_build_client_sync` in `linkedin_api_impl.py` both detect and handle
  all three formats gracefully. Don't add a 4th format without updating both.
- **Playwright browser binary is baked into the Docker image** via
  `playwright install chromium` in the Dockerfile. `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`.
  The binary is ~170MB — expect a slow first build. Subsequent builds are
  cached unless `requirements.txt` or the Dockerfile changes.
- **Playwright `--single-process` removed** from browser args — it
  disables process isolation and crashes under load. Use
  `--no-sandbox --disable-dev-shm-usage --disable-gpu` instead.
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
- **linkedin-api inverted booleans:** `add_connection` and
  `send_message` return `True` on FAILURE and `False` on success
  (yes, really — see upstream README). `linkedin_api_impl.py` wraps
  these and inverts so the rest of our code can treat truthy=success.
- **Concurrent playwright launches poison `li_at` sessions.** Two
  browser instances launched within the same ~10s window for the same
  LinkedIn account look to LinkedIn like the same cookie used from two
  fresh browser fingerprints — that trips bot-detection and
  invalidates the session, even when both individual requests look
  fine. Fix: `PlaywrightLinkedInProvider._run` wraps every browser
  session in a Redis lock keyed on `linkedin-acct-lock:{account_id}`
  (`playwright_impl._AccountLock`). Poll fires while sequencer holds
  the lock → poll waits up to 90s, then raises `AccountLockBusy`. The
  sequencer maps `AccountLockBusy` to a `rate_limited` skip, which is
  in `TRANSIENT_SKIP_STATUSES` and triggers retry-in-5min behavior
  rather than advancing the cursor. Symptom before the fix: cookie
  works on Test, but the very next poll-tick + sequencer-tick collision
  (~5 min later) flagged the session as expired.
- **Sequential playwright launches also drift the cookie.** Even
  serialized via the lock, repeated fresh Chromium launches against
  the same LinkedIn account accumulate enough canvas/WebGL fingerprint
  variance to make LinkedIn invalidate `li_at`. Mitigations in place:
  (1) `LINKEDIN_POLL_INTERVAL_MINUTES` defaults to 30 min (was 5);
  (2) `linkedin_poller._account_has_work` skips the playwright launch
  entirely when no leads in this account's campaigns are in
  `INVITED`/`CONNECTED` connection status — i.e., when there's nothing
  inbound to detect. The poller bumps `last_polled_at` regardless so
  it doesn't spam-skip.
- **Persistent Chrome profile per LinkedIn account** is the real fix
  for fingerprint drift. `PlaywrightLinkedInProvider._run_locked` uses
  `chromium.launch_persistent_context(user_data_dir=...)` so the same
  Chrome profile (cookies, localStorage, fonts cache, fingerprint
  state) is reopened each run. Profile dirs live under
  `LINKEDIN_PROFILES_DIR` (default `/app/_linkedin_profiles/{account_id}`).
  First launch for a fresh profile dir is seeded by extracting the
  bare `li_at` from `session_cookies_encrypted`; thereafter the
  profile is authoritative. When the user pastes a new `li_at` via
  the modal, the linkedin_accounts router calls
  `playwright_impl.clear_profile(account_id)` to wipe the dir so the
  next `_run` reseeds with the new cookie. Same on account delete.
  The `session_cookies_encrypted` column is still mirrored post-run
  as a backup for re-seeding if the profile is ever lost.
- **Don't cache aioredis at module scope in worker code.** Celery
  prefork tasks each call `asyncio.run(...)` which builds a fresh
  event loop; a cached `aioredis.Redis` carries connection-pool state
  bound to whichever loop first created it and fails with "Event loop
  is closed" on the second task. `playwright_impl._new_redis()`
  returns a fresh client per call and `_safe_close()`s it before
  return. `sequencer._li_redis()` still caches (legacy) — fine for
  tests that monkeypatch `_LI_REDIS_CLIENT=None`, but rewrite if it
  ever causes issues in production.
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
docker compose run --rm backend pytest             # backend tests (293)
docker compose exec frontend npm test              # frontend tests (119)
docker compose logs -f backend                     # tail logs
```

App: <http://localhost:5173>  ·  API: <http://localhost:8000>  ·  Docs:
<http://localhost:8000/docs>

## Open questions

- **Hosted provider for production scale.** Decision deferred. The
  `LinkedInProvider` ABC supports either DIY or a hosted provider
  (Unipile, HeyReach, etc.) with no schema or worker changes. Triggers
  to actually flip: customer accounts start getting restricted at
  scale, OR external customers move <3 months out.
- **Real-world test of M3/M4 write actions.** With a throwaway
  LinkedIn account, the safe end-to-end test is: build a campaign
  with `[email] → wait 1d → connect (with note)` and run a single
  lead through it. Verify the connection request lands in LinkedIn's
  "My Network → Sent" tab. Stop there until you're confident the
  endpoint shapes still work for your account.
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
- **LinkedIn path for M2: DIY** (revised from earlier plan). User has a
  throwaway LinkedIn account for development + 3-6 months runway before
  external customers, which makes DIY the right cost trade. Stack:
  Python `linkedin-api` library wrapped behind the `LinkedInProvider`
  ABC, residential proxy via `LINKEDIN_PROXY_URL`, encrypted
  credentials in `LinkedInAccount`. Hosted providers (Unipile etc.) can
  be added later as a parallel concrete impl — typically just for
  write actions — without breaking M2 code.

---

_Last updated: 2026-05-14 — Unipile hosted-API integration (foundation).
Even with the human-dwell fix, the established account still got challenged on
the very first /feed/ navigation (9.1s, no chance for dwell to run) —
Cloudflare bot-management was flagging the headless+datacenter fingerprint
on every fresh Chromium launch. Switched to Unipile (hosted browser API,
real Chrome on residential IPs)._

_Backend tests: **383 + 33 new unipile tests** = 416 expected (run pending)._

## Unipile integration (2026-05-14)

The DIY/Playwright path is permanently fighting LinkedIn's bot scorer —
even with persistent profiles, Redis-locked launches, stealth patches,
human dwell, and an established account, every fresh Chromium session
keeps tripping the challenge.  Decision: punt the heavy lifting to
Unipile, which runs real desktop Chrome on residential IPs.

Foundation shipped (tasks #92-#97 in the in-session list):

1. **`UnipileLinkedInProvider`** in `services/linkedin/unipile_impl.py`.
   Async httpx-based; supports all 11 LinkedInProvider methods.
   Maps Unipile error envelopes (status/code/message) onto our
   ChallengeRequired / AccountRestricted / UnipileError domain types.
   Reads ``UNIPILE_DSN`` + ``UNIPILE_API_KEY`` from settings (both empty
   in dev until the user signs up).

2. **`get_provider()` default switched to `"unipile"`** in
   `services/linkedin/__init__.py`.  Playwright / hybrid / http impls
   remain selectable as fallbacks via `LINKEDIN_PROVIDER`.

3. **33 unit tests** in `tests/test_phase23_unipile_provider.py` using
   `httpx.MockTransport`.  Covers happy paths, error mapping
   (checkpoint → ChallengeRequired, restricted → AccountRestricted,
   network → UnipileError), header injection, premium-required InMail
   path, inbox event polling fallback.

4. **Schema + migration `0006`**: added `LinkedInAccount.unipile_account_id`
   (unique, nullable), `LinkedInAccount.provider_kind` (default "diy"),
   and relaxed `password_encrypted` to nullable.

5. **Router endpoints**:
   - `POST /linkedin-accounts/connect-via-unipile` — creates a placeholder
     row, calls Unipile's hosted-link API, returns the URL the frontend
     opens in a new tab.  The placeholder's local UUID is passed to
     Unipile as ``name`` so webhook events can correlate.
   - `POST /linkedin-accounts/{id}/sync-unipile` — polling fallback if
     the webhook hasn't reached us yet.
   - `POST /linkedin-accounts/{id}` (legacy create) now requires
     `linkedin_email + password` for DIY rows and rejects empty values.
   - `DELETE` also calls Unipile's `delete_account` for Unipile rows so
     we don't leak resources on their side.

6. **Webhook handler `POST /webhooks/unipile`** in `routers/webhooks.py`.
   HMAC-SHA256 verifies the body against `UNIPILE_WEBHOOK_SECRET`.
   Routes events: `account.connected` (writes unipile_account_id + email),
   `account.disconnected`, `account.checkpoint`, `message.received` (sets
   `lead.linkedin_last_reply_at` + promotes connection_status to
   CONNECTED), `invitation.accepted` (sets CONNECTED).  Event-name casing
   normalised so the handler tolerates both `account.connected` and
   `ACCOUNT_CONNECTED` etc.

7. **Frontend `ConnectLinkedInModal`** redesigned with a Hosted/Local
   toggle.  Hosted mode shows just a Label field + "Connect via Unipile"
   button; the button POSTs `/connect-via-unipile`, opens the returned
   hosted URL in a new tab, and polls `/sync-unipile` every 3s until
   status flips to OK (10-min timeout).  Local mode preserves the
   password+li_at flow as a fallback, with copy that nudges the user
   toward Hosted.

Open work (next sessions):
- Sequencer integration is automatic — `send_linkedin_step` already
  calls `get_provider()` which now returns Unipile, no changes needed.
  But the rate-limit/lock code in `playwright_impl.py` is now mostly
  inert when provider=unipile — task #98 strips it.
- Webhook idempotency table (currently we don't dedup repeated event
  deliveries; Unipile's "at-least-once" semantics mean we may double-
  process if they retry).  Add a `webhook_events` table keyed on
  event_id when this becomes a real issue.
- ConnectLinkedInModal tests need updates for the new toggle UI.

## Previous: Human-dwell anti-bot pass (2026-05-14)

## Human-dwell anti-bot pass (2026-05-14)

The established account got challenged on the first `view_profile` step
(captured live in worker logs): 9.7s from task receipt to challenge,
meaning auth succeeded but the immediate profile navigation tripped
LinkedIn's bot scorer.  Root cause: Playwright launched → /feed/ →
/in/young-burke/ in <2s of in-page activity.  No human reads the feed
that fast.

Fix shipped in `playwright_impl.py`:

1. **New `_human_dwell()` helper** — sits on the current page for a
   randomized window doing: initial idle, 2-3 small downward scrolls
   with reading pauses, sometimes scroll back partway, 2-4 random
   mouse moves.  Always swallows exceptions (camouflage is
   best-effort, never load-bearing).
2. **`_ensure_authenticated` now dwells 8-15s on `/feed/`** after
   login is confirmed, before returning.  Replaces the old
   `asyncio.sleep(1.5-4)`.
3. **`view_profile` dwells 6-12s on the profile page** before URN
   resolution.  Also helps the URN scrape because LinkedIn's SPA gets
   more time to hydrate the page DOM.
4. **`_scrape_urn_from_page` dwells 5-9s** when it has to navigate to
   the profile page itself (called from follow/DM/connect/etc. for URN
   resolution).  Replaces a bare 1.5-3s sleep.
5. **New test file `tests/test_phase22_human_dwell.py`** — 5 tests
   covering: completes within window, scrolls/moves when asked,
   doesn't scroll when `scroll=False`, swallows mouse errors,
   respects min-sleep budget.

Tradeoff: per-step runtime grows ~10-15s because of the dwells.  This
is well within `LINKEDIN_MIN_ACTION_DELAY_SECONDS=90`, and the slowdown
is exactly what makes the action look human.

## Diagnostic + cleanup pass (2026-05-14)

1. **`.gitignore` cleanup.** Persistent Chrome profile dirs under
   `backend/_linkedin_profiles/` were committed in earlier "still not
   working" commits, leaking the user's active `li_at` cookie + full
   browser cache. `git rm --cached` removed them from index; the dir
   plus `celerybeat-schedule` are now ignored. **The leaked li_at has
   been rotated/will need rotation.**
2. **`_parse_li_error()` in `playwright_impl.py`.** Pulls a structured
   error code + message out of Voyager error envelopes
   (`{status,code,message}` or nested `errorDetails.inputErrors`).
   Every write action (connect / follow / react / DM / invite_to_page
   / InMail / comment_on_post) now formats failures as
   `"CODE: message"` instead of a raw 200-char body slice — surfaces
   exactly what LinkedIn objected to.
3. **`_voyager` logs the response's final URL** on non-2xx, AND emits
   an info-level note on 2xx if the request URL was redirected (likely
   endpoint move). Combined with `_parse_li_error`, an endpoint change
   or payload validation failure is now self-diagnosing in worker logs.
4. **`send_connect_request` logs the resolved URN** before the POST,
   so we can confirm `_resolve_urn` returned a real URN (vs scraping a
   stale page-loaded URN that doesn't belong to the prospect).
5. **`customMessage` trimmed to 200 chars** (was 300). LinkedIn caps
   notes at 200 for Free / 300 for Premium; the safer floor avoids a
   422 for Free accounts.
6. **New test file `tests/test_phase21_voyager_error_parser.py`** —
   7 tests covering the error-envelope shapes we've seen in the wild.

## OPEN: validate send_connect end-to-end

The throwaway dev account (`allseason.crew.yt@gmail.com`) is
permanently challenged and cannot exercise the new
`voyagerRelationshipsDashMemberRelationships?action=verifyQuotaAndCreate`
endpoint. **The other session's note: established LinkedIn accounts
(verified phone, photo, real connection history) are required for any
write action to survive bot-detection.** Next step: connect a real
established LinkedIn account in Settings → LinkedIn Accounts, paste
fresh `li_at`, then run a connect step. If the new payload is wrong,
the worker logs will now print a parsed `code: message` from LinkedIn
that tells us exactly what to fix.

## Previous hardening (2026-05-13/14)

1. **Sequencer transient-retry behavior.** `TRANSIENT_SKIP_STATUSES =
   {"challenged", "restricted", "rate_limited"}` keeps the lead pinned
   on the current node for 5 min retries instead of advancing the
   cursor. `MAX_TRANSIENT_RETRIES=10` caps the budget per visit. Means
   short LinkedIn outages no longer permanently strand sequences.
2. **`AccountLockBusy` exception.** New transient-skip class for the
   per-account Redis lock timeout. Wired into the sequencer to map to
   `rate_limited`.
3. **Per-account Redis lock around every `_run`.** `_AccountLock` in
   `playwright_impl.py` serializes browser launches against the same
   LinkedIn account, fixing the concurrent-poll-+-sequencer collision
   that was poisoning cookies.
4. **Persistent Chrome profile per account** (the real fingerprint
   fix). `chromium.launch_persistent_context(user_data_dir=...)` keeps
   cookies, localStorage, and Chrome's internal state stable across
   runs. `_extract_seed_cookies` bootstraps a fresh profile dir with
   the user-pasted `li_at`. `playwright_impl.clear_profile()` wipes
   the dir on user cookie re-paste or account delete (called from the
   linkedin_accounts router).
5. **Poller throttled.** `LINKEDIN_POLL_INTERVAL_MINUTES` default
   raised 5 → 30. `linkedin_poller._account_has_work` skips the
   playwright launch entirely when no leads are in `invited`/
   `connected` state.
6. **Redis client no longer cached at module scope in
   `playwright_impl`.** Was tripping "Event loop is closed" under
   Celery's per-task `asyncio.run`. Now uses `_new_redis()` +
   `_safe_close()` per call.
7. **`send_connect_request` updated** — switched to LinkedIn's modern
   endpoint `voyagerRelationshipsDashMemberRelationships?action=verifyQuotaAndCreate`
   with payload `{"inviteeProfileUrn": "urn:li:fsd_profile:..."}` and
   optional `customMessage`. URN resolution via existing `_resolve_urn`
   (DOM-scrape from the prospect's profile page). **Untested live —
   throwaway account got challenge-flagged before we could verify.**
8. **`_voyager` now logs response headers + type on non-2xx** so we
   can spot endpoint deprecations (LinkedIn signals these with 30x +
   Location).

