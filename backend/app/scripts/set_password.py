"""Set (or reset) a user's password from the command line.

    docker compose exec backend python -m app.scripts.set_password you@example.com

Prompts twice (no echo).  Used to give the bootstrap workspace owner —
created passwordless by migration 0044 — their first password.
"""
from __future__ import annotations

import asyncio
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth import passwords
from app.config import settings
from app.models.identity import User


async def _main(email: str) -> int:
    email = email.strip().lower()
    pw = getpass.getpass(f"New password for {email}: ")
    if len(pw) < passwords.MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {passwords.MIN_PASSWORD_LENGTH} characters.")
        return 1
    if getpass.getpass("Repeat: ") != pw:
        print("Passwords don't match.")
        return 1
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine) as db:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None:
                print(f"No user with email {email}.")
                return 1
            user.password_hash = passwords.hash_password(pw)
            await db.commit()
    finally:
        await engine.dispose()
    print(f"Password set for {email}.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(_main(sys.argv[1])))
