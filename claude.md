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

- **Last completed:** **M5 — Sequence analytics + soft-delete.**
  - **Soft-delete:** new `sequence_nodes.deleted_at` column +
    partial index on live nodes. `replace_graph` now soft-deletes the
    old topology instead of cascade-deleting it; `lead_step_executions`
    pointing at retired nodes are preserved (analytics-friendly).
    Scheduler halts any lead whose `current_node_id` references a
    soft-deleted node, with `halt_reason="current node deleted
    (sequence was rebuilt)"`.
  - **Analytics endpoint:**
    `GET /campaigns/{id}/sequence/analytics` returns per-node
    `attempted / sent / skipped / failed / currently_here` counts plus
    overall lead-status breakdown (active / halted / completed /
    pending). Computed on demand — no caching beat task yet.
  - **Frontend funnel overlay:** `SequenceBuilder` fetches analytics
    on a 30s interval and decorates each node with a compact stats
    footer + color-coded success border (green ≥70%, amber ≥30%, red
    <30%). Analytics merge happens in a separate effect so it doesn't
    clobber in-flight user edits to the canvas.
  Tests: **backend 335 passed**, **frontend 119 passed**.
- **In flight:** nothing — **Phase 1.5 is complete (M1–M5).**
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
- **Rebuild ALL THREE Python services when you change `requirements.txt`.**
  `backend`, `worker`, and `beat` all build from the same Dockerfile but
  docker-compose tags them as separate images. `docker compose build
  backend` does NOT rebuild `worker` / `beat`. Symptom: backend boots
  fine but campaigns get stuck because tasks never run; `docker compose
  logs worker` shows `ModuleNotFoundError`. Always run
  `docker compose build backend worker beat` after editing requirements.
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

_Last updated: 2026-05-13 — **Phase 1.5 complete** (M1–M5). Last work: M5 sequence analytics + soft-delete._
