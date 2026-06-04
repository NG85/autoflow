"""
Password hashing for users.hashed_password (pwdlib Argon2 + bcrypt).
"""

from __future__ import annotations

import secrets
from typing import Optional, Union

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

_password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_and_update_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, Optional[str]]:
    verified, updated = _password_hash.verify_and_update(plain_password, hashed_password)
    return verified, updated


def hash_for_timing_attack_mitigation(password: str) -> str:
    """Run hasher when user is missing (mitigate timing attacks)."""
    return hash_password(password)


def generate_random_password() -> str:
    return secrets.token_urlsafe()
