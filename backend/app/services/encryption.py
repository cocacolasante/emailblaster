"""Symmetric credential encryption.

Stored inbox passwords and workspace provider keys MUST go through this
module and nowhere else.
Decrypted plaintext lives only in the local scope of the caller — never log,
return from API responses, or persist it.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings

_fernet: MultiFernet | None = None


def _get_fernet() -> MultiFernet:
    """Encrypt with ENCRYPTION_KEY; decrypt with it or ENCRYPTION_KEY_OLD.

    Key rotation: move the current key to ENCRYPTION_KEY_OLD, set a new
    ENCRYPTION_KEY, then re-save secrets at leisure (old tokens keep
    decrypting until then)."""
    global _fernet
    if _fernet is None:
        key = settings.ENCRYPTION_KEY
        if not key:
            raise RuntimeError(
                "ENCRYPTION_KEY is not configured. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        keys = [key] + ([settings.ENCRYPTION_KEY_OLD] if settings.ENCRYPTION_KEY_OLD else [])
        _fernet = MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string; return a URL-safe base64 Fernet token."""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a Fernet token. Raises cryptography.fernet.InvalidToken on failure."""
    return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


__all__ = ["encrypt", "decrypt", "InvalidToken"]
