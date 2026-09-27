"""Engine, session factory, and the request DB dependencies.

Three dependencies, one rule — **every feature router uses ``get_db``**:

- ``get_db``        authenticates the request (session cookie → user +
                    active workspace), binds the tenant context and the
                    workspace's credential bundle, THEN opens the DB
                    session.  No anonymous path exists through it.
- ``get_tenant_context`` binds that context without opening a session;
                    attached router-wide to every feature router.
- ``get_identity``  the resolved ``Identity`` (user / tenant / role) for
                    handlers that need the caller; cached per request so
                    it shares the auth lookup with ``get_db``.
- ``get_public_db`` unauthenticated session for the auth endpoints,
                    webhooks and unsubscribe links ONLY (allowlisted by a
                    test).  Those handlers resolve the tenant themselves.

``get_session_factory`` is the single override point tests use to point
all three at the test database.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

if TYPE_CHECKING:
    from app.auth.deps import Identity


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.APP_DATABASE_URL or settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return AsyncSessionLocal


async def get_public_db(
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncGenerator[AsyncSession, None]:
    async with factory() as session:
        yield session


async def get_identity(
    request: Request,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> "Identity":
    # Lazy import: app.auth.deps imports models, which import Base from here.
    from app.auth.deps import authenticate_request

    return await authenticate_request(request, factory)


async def get_tenant_context(
    identity: "Identity" = Depends(get_identity),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncGenerator["Identity", None]:
    """Bind tenant/user context + credential bundle for the request.
    Attached router-wide to every feature router (see app.main), so even
    endpoints that never open a DB session are authenticated and scoped."""
    from app.auth.deps import bind_identity

    async with bind_identity(identity, factory):
        yield identity


async def get_db(
    identity: "Identity" = Depends(get_tenant_context),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncGenerator[AsyncSession, None]:
    async with factory() as session:
        yield session
