"""argon2id password hashing (argon2-cffi defaults = OWASP-recommended)."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# Pre-computed so the "unknown email" login path does the same argon2
# work as a real password check (no timing-based account enumeration).
_BURNER_HASH = _hasher.hash("burner-password-for-timing-parity")


def burn_verify(password: str) -> None:
    verify_password(_BURNER_HASH, password)
