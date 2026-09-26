"""Argon2id password storage and read-only migration of legacy Passlib hashes."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

# Explicit parameters, independent of future library default changes.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
_MAX_PASSWORD_BYTES = 1024


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 1 <= len(password.encode("utf-8")) <= _MAX_PASSWORD_BYTES:
        raise ValueError("Password must contain between 1 and 1024 UTF-8 bytes")
    return _hasher.hash(password)


def _ab64_decode(value: str) -> bytes:
    # Passlib's adapted Base64 uses '.' in place of '+', and omits padding.
    raw = value.replace(".", "+")
    return base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        return False
    if not isinstance(encoded, str) or len(encoded) > 1024:
        return False
    try:
        if encoded.startswith("$argon2id$"):
            return _hasher.verify(encoded, password)
        if encoded.startswith("$pbkdf2-sha256$"):
            _, algorithm, rounds, salt, checksum = encoded.split("$")
            iterations = int(rounds)
            if not 1000 <= iterations <= 2_000_000:
                return False
            salt_bytes, expected = _ab64_decode(salt), _ab64_decode(checksum)
            if len(expected) != 32 or not 1 <= len(salt_bytes) <= 128:
                return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
            return hmac.compare_digest(actual, expected)
    except (InvalidHashError, VerificationError, ValueError, TypeError, binascii.Error):
        return False
    return False


def needs_rehash(encoded: str) -> bool:
    return not encoded.startswith("$argon2id$") or _hasher.check_needs_rehash(encoded)
