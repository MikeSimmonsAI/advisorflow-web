"""ONE password-length rule for every place a person chooses a password.

Owner's decision (Oct 14): 4 characters minimum. Set PASSWORD_MIN_LENGTH on a
service to raise it there; nothing else needs to change."""
import os


def min_length() -> int:
    try:
        return max(1, int(os.environ.get("PASSWORD_MIN_LENGTH") or 4))
    except ValueError:
        return 4


def too_short_message() -> str:
    return "Choose a password of at least %d characters." % min_length()
