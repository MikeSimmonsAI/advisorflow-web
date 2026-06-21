"""
Crypto utility - encrypts sensitive fields (Twilio auth tokens) at rest.
Uses Fernet symmetric encryption. The key MUST come from an environment
variable in production (ENCRYPTION_KEY) - never hardcode it.
"""

import os
from cryptography.fernet import Fernet

def _get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY environment variable not set. "
            "Generate one with: Fernet.generate_key().decode()"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
