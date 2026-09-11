"""Import + route smoke test for the Support Intelligence build.

Not a test file: this is the fast local check that the app still boots and
that the new routers actually mounted, run before the pytest suite so an
import error is one second rather than one minute.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-32chars!!")
os.environ.setdefault("BOOKING_BASE_URL", "https://book.example.com")
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from app.main import app                                    # noqa: E402
from app.services import (                                  # noqa: E402
    support_ai, support_authority, support_branding, support_brief,
    support_diagnostics, support_entitlements, support_incidents,
    support_knowledge, support_remediation, support_sla, support_tickets,
)

support_routes = sorted(
    "%-6s %s" % (",".join(sorted(r.methods - {"HEAD", "OPTIONS"})), r.path)
    for r in app.routes
    if getattr(r, "path", "").startswith("/support")
    or getattr(r, "path", "").startswith("/god/support"))

print("TOTAL ROUTES        :", len(app.routes))
print("SUPPORT ROUTES      :", len(support_routes))
for line in support_routes:
    print("   ", line)
print("DIAGNOSTIC CHECKS   :", len(support_diagnostics.REGISTRY))
print("REMEDIATIONS        :", len(support_remediation.REGISTRY))
for entry in support_remediation.list_registry():
    print("    %-22s %s" % (entry["risk_class"], entry["action_key"]))
print("AI TOOL DEFS        :",
      [t["function"]["name"] for t in support_diagnostics.tool_definitions()])
print("SUPPORT FEATURES    :", support_entitlements.ALL_SUPPORT_FEATURE_KEYS)
print("STARTER ARTICLES    :", len(support_knowledge.STARTER_ARTICLES))
print("OK")
