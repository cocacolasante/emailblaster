"""SQL for the API-key verifier, shared by migration 0050 and the test
schema (metadata after_create)."""

API_KEY_VERIFY_SQL = """
CREATE OR REPLACE FUNCTION api_key_verify(p_prefix text, p_hash text)
RETURNS TABLE (key_id uuid, tenant_id uuid, user_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $fn$
  UPDATE api_keys k
     SET last_used_at = now()
    FROM memberships m, tenants t
   WHERE k.prefix = p_prefix
     AND k.token_hash = p_hash
     AND k.revoked_at IS NULL
     -- The key acts as its creator: if they've left the workspace, or the
     -- workspace is suspended, the key stops working with them.
     AND m.tenant_id = k.tenant_id AND m.user_id = k.created_by
     AND t.id = k.tenant_id AND t.status = 'active'
  RETURNING k.id, k.tenant_id, k.created_by;
$fn$;
"""


def install_api_key_ddl(metadata) -> None:
    from sqlalchemy import DDL, event

    event.listen(metadata, "after_create", DDL(API_KEY_VERIFY_SQL.replace("%", "%%")))
