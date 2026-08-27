"""Every table that references leads.id, and whether the FK cascades.

The cleanup delete failed on production with the request never returning, which
is what an unhandled IntegrityError looks like from a browser: the exception
escapes before the CORS middleware adds its headers, so fetch() reports
"Failed to fetch" rather than a 500. Nothing was deleted - the transaction rolled
back, and the re-preview confirmed the same 86 records still there.

The likely cause is a child table the delete does not clear and whose FK does
not cascade. This lists them all so the fix is complete rather than one-more-
table-at-a-time.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("DATABASE_URL", "sqlite:///./_scratch_leadrefs.db")

from app.models.models import Base                      # noqa: E402
import app.models.sales_models                          # noqa: F401,E402
import app.models.implementation_models                 # noqa: F401,E402
import app.models.scheduling_models                     # noqa: F401,E402
import app.models.calendar_models                       # noqa: F401,E402
import app.models.meeting_models                        # noqa: F401,E402
import app.models.integration_models                    # noqa: F401,E402
import app.models.staff_models                          # noqa: F401,E402
import app.models.location_models                       # noqa: F401,E402

CLEARED = {"lead_outcomes", "replies", "messages", "cadence_states", "leads"}

print("%-28s %-22s %-10s %-10s %s" % ("TABLE", "COLUMN", "NULLABLE", "ONDELETE", "CLEANUP CLEARS IT?"))
print("-" * 96)
rows = []
for table in Base.metadata.sorted_tables:
    for col in table.columns:
        for fk in col.foreign_keys:
            if fk.column.table.name != "leads":
                continue
            ondelete = (fk.ondelete or "").upper() or "-"
            cleared = table.name in CLEARED
            safe = cleared or ondelete in ("CASCADE", "SET NULL") or col.nullable
            rows.append((table.name, col.name, col.nullable, ondelete, cleared, safe))

for t, c, nullable, ondelete, cleared, safe in sorted(rows):
    print("%-28s %-22s %-10s %-10s %s%s" % (
        t, c, "yes" if nullable else "NO", ondelete,
        "yes" if cleared else "no",
        "" if safe else "   <-- BLOCKS THE DELETE"))

blockers = [r for r in rows if not r[5]]
print("\n%d table(s) reference leads.id; %d would block a lead delete."
      % (len(rows), len(blockers)))
if blockers:
    print("\nMust be cleared (or given ON DELETE CASCADE) before deleting a lead:")
    for t, c, _n, _o, _cl, _s in sorted(blockers):
        print("  - %s.%s" % (t, c))
