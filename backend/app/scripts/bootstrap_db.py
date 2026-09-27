"""Create/refresh the non-owner runtime DB role that RLS applies to.

    python -m app.scripts.bootstrap_db

Runs as the OWNER role (``DATABASE_URL``) before the backend starts
(see docker-compose).  Idempotent.  Reads the role credentials from
``APP_DATABASE_URL``; does nothing when that isn't set (dev/test on the
owner role — RLS bypassed, app-level scoping still applies).

The role is LOGIN NOSUPERUSER NOBYPASSRLS with DML on all tables and
sequences, plus default privileges so tables created by LATER migrations
are covered without re-running grants by hand.  Role passwords live here
(env), never in migration history.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from urllib.parse import unquote, urlparse

import asyncpg

from app.config import settings

logger = logging.getLogger("bootstrap_db")


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def bootstrap() -> int:
    if not settings.APP_DATABASE_URL:
        logger.warning("APP_DATABASE_URL not set — runtime uses the owner role; RLS is bypassed.")
        return 0
    app = urlparse(_dsn(settings.APP_DATABASE_URL))
    owner = urlparse(_dsn(settings.DATABASE_URL))
    role, password = unquote(app.username or ""), unquote(app.password or "")
    if not role or not password:
        logger.error("APP_DATABASE_URL must include a username and password")
        return 1
    if role == unquote(owner.username or ""):
        logger.error("APP_DATABASE_URL uses the owner role %r — RLS would be bypassed", role)
        return 1

    conn = await asyncpg.connect(_dsn(settings.DATABASE_URL))
    try:
        r, pw = _quote_ident(role), _quote_literal(password)
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
        if exists:
            await conn.execute(f"ALTER ROLE {r} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE "
                               f"NOCREATEDB PASSWORD {pw}")
        else:
            await conn.execute(f"CREATE ROLE {r} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE "
                               f"NOCREATEDB PASSWORD {pw}")
        db = _quote_ident(owner.path.lstrip("/"))
        owner_role = _quote_ident(unquote(owner.username or ""))
        for stmt in (
            f"GRANT CONNECT ON DATABASE {db} TO {r}",
            f"GRANT USAGE ON SCHEMA public TO {r}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {r}",
            # Alembic's bookkeeping table is the owner's business only.
            f"REVOKE ALL ON TABLE alembic_version FROM {r}",
        ):
            try:
                await conn.execute(stmt)
            except asyncpg.UndefinedTableError:
                pass  # alembic_version not created yet (fresh DB)
        logger.info("runtime role %s ready", role)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    sys.exit(asyncio.run(bootstrap()))
