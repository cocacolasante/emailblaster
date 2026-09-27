# Multi-tenancy

Email Blaster is multi-tenant: **workspaces** (`tenants` table; never
call them "orgs" in code — `orgs` is the intent engine's nonprofit
table) with members, per-workspace data isolation, record owners, and
per-workspace provider credentials.

## Model

| Concept | Where | Notes |
|---|---|---|
| Workspace | `tenants` | `status` active/suspended. Bootstrap workspace id `00000000-0000-0000-0000-000000000001` (existing single-tenant data). |
| User | `users` | email unique (lowercased), argon2id `password_hash`, may belong to many workspaces. |
| Membership | `memberships` | role `owner` / `admin` / `member`; per-member `notify_email_enabled`, `digest_enabled`. |
| Session | `user_sessions` | httpOnly `eb_session` cookie holds a random token; DB stores SHA-256. Row carries the ACTIVE workspace (`/auth/switch-workspace` changes it). |
| Invites / resets | `invitations`, `auth_tokens` | hashed single-use tokens. |
| Tenant data | every other table except `linkedin_profile_cache`, `webhook_events` | `TenantMixin.tenant_id` (NOT NULL, FK CASCADE). |
| Owners | leads, campaigns, opportunities, activities, accounts, contacts, reports | `OwnedMixin.owner_id`, composite FK `(tenant_id, owner_id) → memberships ON DELETE SET NULL (owner_id)`. |
| Provider keys | `tenant_provider_keys` | Fernet-encrypted JSON per (workspace, provider). |

**Visibility is workspace-wide.** Every member sees every record in the
workspace; owners are for accountability ("My leads", assignment,
notification routing). Owners/admins manage the team + integrations.

## How isolation is enforced (three layers)

1. **Tenant context** — `app/tenancy/context.py` ContextVars
   (`current_tenant_id`, `current_user_id`).
   - Requests: `get_tenant_context` (attached router-wide in `app/main.py`)
     authenticates the cookie, binds the context and the workspace's
     credential bundle. `get_db` builds on it. Public routes (auth,
     webhooks, unsubscribe) use `get_public_db` (allowlisted by a test).
   - Record tasks: `@in_record_tenant(kind, arg)` resolves the record's
     workspace via `app_tenant_of()` and runs inside it.
   - Beat sweeps: the Celery entry calls `run_per_tenant(body)` — the body
     runs once per active workspace, failures isolated.
   - Webhooks: per-workspace URLs `/webhooks/{unipile|brevo}/{tenant_id}`;
     legacy global URLs resolve the workspace from the payload.
2. **ORM auto-scoping** — `app/tenancy/scoping.py` adds
   `with_loader_criteria(TenantMixin, tenant_id == current)` to every ORM
   SELECT/UPDATE/DELETE (incl. joins, `select_from`, relationship loads).
   New rows are stamped at construction (`init` event) or by the column
   default (Core inserts). Opt-out `execution_options(skip_tenant_filter=
   True)` is allowlisted by a hardening test.
3. **Postgres RLS** (migration 0049) — `tenant_isolation` policy on all
   workspace tables, keyed on the transaction-local `app.tenant_id`
   setting (stamped by an `after_begin` listener). The app runs as the
   non-owner `APP_DATABASE_URL` role (NOBYPASSRLS, created by
   `app.scripts.bootstrap_db`); no setting → zero rows. Alembic/scripts
   use the owner `DATABASE_URL`.

## Credentials

`services/credentials.py`: `credentials.require("brevo")` /
`credentials.get("hunter")` read the bundle bound to the current tenant.
**No `.env` fallback** — unconfigured → `MissingCredential` → HTTP 409
`{"error": "integration_not_configured", "provider": …}`. Only
`credentials.py` decrypts keys. Management API + live tests:
`routers/settings.py` `/settings/integrations*`, `services/integrations.py`.
Platform mail (resets/invites) is the only process-level key
(`PLATFORM_BREVO_API_KEY`, optional).

## Redis keys

Keys namespaced by a record UUID are already unique. Keys namespaced by
something workspaces can share go through `app/tenancy/keys.py`
(domain rate caps, LinkedIn page caps, funding stop flags, Brevo poll
watermark).

## Adding things — checklist

- **New table** with customer data: inherit `TenantMixin` (and `OwnedMixin`
  if it has an accountable member), add it to a new migration's RLS
  policy list, add to `tests/conftest.py` TRUNCATE list.
  `test_schema_tenancy.py` and `test_rls_isolation.py` fail until you do.
- **New Celery task**: record task → decorate the async body with
  `@in_record_tenant`; periodic task → call it via `run_per_tenant`.
  Open DB sessions *inside* the context.
- **New provider**: add a dataclass + fields in `credentials.py`, a spec
  in `integrations.PROVIDERS`, a probe in `integrations._probe`.
- **New Redis key namespaced by a shared value**: add a helper to
  `app/tenancy/keys.py`.

## Tests

- `test_auth.py` — sessions, signup, invites, roles, route guard.
- `test_ownership.py` — defaults, inheritance, assignment, filters,
  member removal, notification routing.
- `test_tenant_isolation_app.py` — workspace B is invisible to A across
  every router (lists, details, writes, aggregates, bulk).
- `test_tenant_workers.py` — per-tenant beat loops, record-task
  resolution, per-workspace credentials.
- `test_credentials.py` — encrypted storage, no env fallback,
  isolation, Integrations API, per-workspace webhook secrets.
- `test_rls_isolation.py` — Alembic-built DB + runtime role: RLS fails
  closed, blocks cross-tenant reads/writes, covers every table.
- `test_tenancy_hardening.py` — static guards (key reads, client
  singletons, engine creation, `skip_tenant_filter`, Redis keys).

## Upgrading an existing single-tenant install

```bash
git pull
docker compose build backend worker beat
docker compose run --rm backend alembic upgrade head    # 0044–0049
docker compose up -d --force-recreate backend worker beat frontend
docker compose exec backend python -m app.scripts.set_password <owner-email>
```

- 0044 creates "My Workspace" owned by `BOOTSTRAP_OWNER_EMAIL` (or
  `OWNER_NOTIFY_EMAIL`), 0045 moves every existing row into it, 0046 makes
  that user the owner of every record, 0048 copies the `.env` provider keys
  into the workspace (encrypted).
- LinkedIn webhooks: the old global `/webhooks/unipile` URL keeps working
  (the workspace is resolved from the account). To move to the
  per-workspace URL, use Settings → Integrations → Unipile → "Register
  webhooks automatically".
- After verifying Settings → Integrations shows every provider connected
  and passing "Test", delete the provider keys from `.env`.
