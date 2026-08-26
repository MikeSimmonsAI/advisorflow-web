"""
cleanup_test_bookings.py
========================
One-off cleanup for the three test BookingLink records created during
the Retell live-flow test (call_5e2a3c147e1d784fa7711da5ee2).

Run this from the Render Shell tab on advisorflow-backend:
    python3 scripts/cleanup_test_bookings.py

The script is READ-FIRST: it prints a full inspection report, then pauses
and asks for confirmation before deleting anything.

DO NOT delete unrelated records — this script is scoped to the three
specific booking IDs and will refuse to touch anything else.
"""

import os
import sys

# ── Target record IDs ────────────────────────────────────────────────────────
LEAD_ID      = "28440f7a-d38a-409a-949e-dd9ff4887310"
BOOKING_IDS  = [
    "471b5593-68b2-48ce-8a4e-776b1755f358",
    "0df98d56-6fba-4875-8501-6481b6e111e0",
    "32a31fea-964e-4027-8db9-bfb1097d664b",  # note: last char corrected
]
EXPECTED_TIMES = {
    "2026-08-31 14:00:00",
    "2026-08-26 14:00:00",
    "2026-08-27 14:00:00",
}
ORG_NAME     = "Greenland Cemetery and Funeral Home"
ADVISOR_NAME = "Mike Simmons"
RETELL_CALL  = "call_5e2a3c147e1d784fa7711da5ee2"

# ── DB connection ─────────────────────────────────────────────────────────────
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 not available — trying sqlalchemy path...")
    psycopg2 = None

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL not set. Run this inside the Render Shell.")

# Render's internal postgres:// must be postgresql:// for psycopg2
conn_str = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def run(conn_str: str):
    if psycopg2:
        conn = psycopg2.connect(conn_str)
        conn.autocommit = False
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        # fallback: psql subprocess (DATABASE_URL is already in env)
        import subprocess, json
        def pg(sql):
            r = subprocess.run(
                ["psql", DATABASE_URL, "-c", sql, "--csv", "-q"],
                capture_output=True, text=True,
            )
            print(r.stdout); return r.returncode == 0
        pg(f"SELECT id, scheduled_at, status, google_event_id, calendly_event_id "
           f"FROM booking_links WHERE id IN {tuple(BOOKING_IDS)};")
        pg(f"SELECT id, first_name, last_name, email, phone, organization_id, "
           f"created_at FROM leads WHERE id = '{LEAD_ID}';")
        pg(f"SELECT COUNT(*) as other_activity FROM sms_messages "
           f"WHERE lead_id = '{LEAD_ID}';")
        print("\nFallback mode: cannot do transactional delete via psql subprocess.")
        print("Please run the DELETE statements manually in the Render psql shell.")
        return

    # ── PHASE 1: INSPECT ──────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("PHASE 1 — INSPECTION (read-only, no changes yet)")
    print("="*70)

    # 1a. Check the three bookings exist and match expectations
    placeholders = ",".join(["%s"] * len(BOOKING_IDS))
    cur.execute(
        f"""
        SELECT
            bl.id,
            bl.scheduled_at,
            bl.status,
            bl.google_event_id,
            bl.calendly_event_id,
            bl.lead_id,
            bl.user_id,
            u.full_name  AS advisor,
            o.name       AS org
        FROM booking_links bl
        LEFT JOIN users         u ON u.id = bl.user_id
        LEFT JOIN organizations o ON o.id = bl.organization_id
        WHERE bl.id IN ({placeholders})
        """,
        BOOKING_IDS,
    )
    bookings = cur.fetchall()
    print(f"\n[1a] Bookings found: {len(bookings)} of {len(BOOKING_IDS)} expected")
    calendar_event_ids = []
    for b in bookings:
        print(f"     ID          : {b['id']}")
        print(f"     scheduled_at: {b['scheduled_at']}")
        print(f"     status      : {b['status']}")
        print(f"     google_evt  : {b['google_event_id']}")
        print(f"     calendly_evt: {b['calendly_event_id']}")
        print(f"     lead_id     : {b['lead_id']}")
        print(f"     advisor     : {b['advisor']}")
        print(f"     org         : {b['org']}")
        print()
        if b["google_event_id"]:
            calendar_event_ids.append(("google", b["google_event_id"]))
        if b["calendly_event_id"]:
            calendar_event_ids.append(("calendly", b["calendly_event_id"]))

    # Safety check — confirm lead IDs are all the expected test lead
    unexpected = [b for b in bookings if str(b["lead_id"]) != LEAD_ID]
    if unexpected:
        print("SAFETY ABORT: One or more bookings belong to a DIFFERENT lead.")
        print("Unexpected lead IDs:", [b["lead_id"] for b in unexpected])
        conn.close(); sys.exit(1)

    # 1b. Inspect the lead itself
    cur.execute(
        """
        SELECT id, first_name, last_name, email, phone,
               organization_id, created_at,
               (SELECT COUNT(*) FROM sms_messages    WHERE lead_id = leads.id) AS sms_count,
               (SELECT COUNT(*) FROM booking_links   WHERE lead_id = leads.id) AS booking_count,
               (SELECT COUNT(*) FROM email_messages  WHERE lead_id = leads.id) AS email_count
        FROM leads WHERE id = %s
        """,
        (LEAD_ID,),
    )
    lead = cur.fetchone()
    print(f"[1b] Lead record:")
    if lead:
        print(f"     Name        : {lead['first_name']} {lead['last_name']}")
        print(f"     Email       : {lead['email']}")
        print(f"     Phone       : {lead['phone']}")
        print(f"     Created     : {lead['created_at']}")
        print(f"     SMS msgs    : {lead['sms_count']}")
        print(f"     Bookings    : {lead['booking_count']}")
        print(f"     Email msgs  : {lead['email_count']}")
        is_test_only = (
            lead['sms_count'] == 0
            and lead['email_count'] == 0
            and lead['booking_count'] == len(BOOKING_IDS)
        )
        print(f"     Test-only?  : {'YES — safe to delete lead' if is_test_only else 'NO — has other activity, KEEP lead'}")
    else:
        print("     Lead NOT FOUND in DB.")
        is_test_only = False

    # 1c. Calendar event IDs found
    print(f"\n[1c] Calendar event IDs attached to these bookings:")
    if calendar_event_ids:
        for provider, eid in calendar_event_ids:
            print(f"     {provider}: {eid}")
    else:
        print("     None (no Google or Calendly event IDs on these records)")

    print("\n" + "="*70)
    print("PHASE 1 COMPLETE — nothing changed yet.")
    print("="*70)

    # ── PHASE 2: CONFIRM ──────────────────────────────────────────────────────
    print("\nPLAN:")
    print(f"  DELETE {len(BOOKING_IDS)} booking_links rows (the three test bookings)")
    if is_test_only:
        print(f"  DELETE 1 lead row (test lead has no other activity)")
    else:
        print(f"  KEEP   lead row (lead has other activity — not test-only)")
    print(f"  KEEP   all SMS, email, and other production records")
    if calendar_event_ids:
        print(f"  NOTE:  {len(calendar_event_ids)} external calendar event ID(s) recorded above.")
        print(f"         External Google/Calendly events must be cancelled manually")
        print(f"         (this script has no OAuth token to call those APIs).")

    confirm = input("\nType YES to execute, anything else to abort: ").strip()
    if confirm != "YES":
        print("Aborted. No changes made.")
        conn.close()
        return

    # ── PHASE 3: DELETE ───────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("PHASE 3 — EXECUTING DELETIONS")
    print("="*70)

    try:
        # Delete the bookings
        cur.execute(
            f"DELETE FROM booking_links WHERE id IN ({placeholders}) AND lead_id = %s RETURNING id",
            BOOKING_IDS + [LEAD_ID],  # extra lead_id guard
        )
        deleted_bookings = cur.fetchall()
        print(f"  Deleted booking_links: {[r['id'] for r in deleted_bookings]}")

        # Delete lead only if test-only
        if is_test_only:
            cur.execute(
                "DELETE FROM leads WHERE id = %s RETURNING id, first_name, last_name",
                (LEAD_ID,),
            )
            deleted_lead = cur.fetchone()
            print(f"  Deleted lead: {deleted_lead['id']} ({deleted_lead['first_name']} {deleted_lead['last_name']})")
        else:
            print(f"  Lead {LEAD_ID} kept (has non-test activity).")

        conn.commit()
        print("\n✅ COMMITTED. Changes are live.")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ ERROR — rolled back. Nothing was changed.\n{e}")
        conn.close()
        sys.exit(1)

    # ── PHASE 4: VERIFY ───────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("PHASE 4 — POST-DELETE VERIFICATION")
    print("="*70)
    cur.execute(
        f"SELECT COUNT(*) AS remaining FROM booking_links WHERE id IN ({placeholders})",
        BOOKING_IDS,
    )
    remaining = cur.fetchone()["remaining"]
    print(f"  booking_links remaining with target IDs: {remaining} (expected 0)")

    if is_test_only:
        cur.execute("SELECT COUNT(*) AS remaining FROM leads WHERE id = %s", (LEAD_ID,))
        remaining_lead = cur.fetchone()["remaining"]
        print(f"  leads remaining with target ID: {remaining_lead} (expected 0)")

    print("\n✅ CLEANUP COMPLETE.")
    if calendar_event_ids:
        print("\n⚠️  ACTION REQUIRED — External calendar events:")
        for provider, eid in calendar_event_ids:
            print(f"    {provider.upper()} event ID: {eid}")
        print("   Cancel these manually in Google Calendar / Calendly admin.")

    conn.close()


if __name__ == "__main__":
    run(conn_str)
