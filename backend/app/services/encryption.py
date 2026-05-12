"""Symmetric credential encryption.

Stored inbox passwords MUST go through this module and nowhere else.
Decrypted plaintext lives only in the local scope of the caller — never log,
return from API responses, or persist it.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.ENCRYPTION_KEY
        if not key:
            raise RuntimeError(
                "ENCRYPTION_KEY is not configured. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string; return a URL-safe base64 Fernet token."""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a Fernet token. Raises cryptography.fernet.InvalidToken on failure."""
    return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


__all__ = ["encrypt", "decrypt", "InvalidToken"]
