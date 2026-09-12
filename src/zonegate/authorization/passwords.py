"""Console password hashing.

Deliberately stdlib-only: `hashlib.pbkdf2_hmac` ships with CPython, so the
console gains sign-in without the deployment picking up a native crypto
dependency. The stored form carries its own parameters, so raising the
iteration count later still verifies passwords hashed with the old one.
"""

import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 240_000
SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    """Returns `pbkdf2_sha256$<iterations>$<salt-hex>$<hash-hex>`."""
    if not password:
        raise ValueError("A console password cannot be empty")

    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Whether `password` produces `stored`. False for anything malformed.

    A malformed record is a failed sign-in rather than an exception: storage
    that has been tampered with must not be able to crash the auth path.
    """
    try:
        algorithm, raw_iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        iterations = int(raw_iterations)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)
