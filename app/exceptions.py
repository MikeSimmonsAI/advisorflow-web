"""
Shared application exceptions.
Import from here to avoid circular imports between services.
"""


class TokenExpiredError(Exception):
    """Raised when Microsoft rejects the refresh token (HTTP 400) — advisor must re-connect."""
