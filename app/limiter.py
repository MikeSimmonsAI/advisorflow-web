"""
Shared slowapi rate-limiter instance.

Defined here (not in main.py) so routers can import it without
causing a circular dependency with the FastAPI app object.
Wire it into the app in main.py:
    from app.limiter import limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
