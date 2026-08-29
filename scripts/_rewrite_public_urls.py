"""One-shot rewrite of every customer-facing URL to the shared resolver.

Run once, then deleted. Kept out of the app package deliberately: this is a
migration of call sites, not behaviour anyone should import.

Every replacement is exact-match and asserted, so a site that has drifted
fails loudly instead of being silently skipped.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IMPORT_BOOK = ("from app.services.public_identity import "
               "booking_url as public_booking_url")
IMPORT_SURVEY = ("from app.services.public_identity import "
                 "survey_url as public_survey_url")

EDITS = [
    # (relative path, old, new, expected occurrences)
    # Both SMS senders carry the identical two lines and both have `lead` in
    # scope, so one rule fixes both. Asserted as exactly 2 rather than "all",
    # so a third sender appearing later fails the run instead of being
    # silently swept along.
    ("app/services/sms_service.py",
     '        booking_link = create_booking_link(db, lead, advisor)\n'
     '        booking_url = f"{BOOKING_BASE_URL}/book/{booking_link.token}"',
     '        booking_link = create_booking_link(db, lead, advisor)\n'
     '        ' + IMPORT_BOOK + '\n'
     '        booking_url = public_booking_url(db, lead.organization_id,\n'
     '                                         booking_link.token)', 2),

    ("app/routers/voice_router.py",
     '    booking_url = f"{BOOKING_BASE_URL}/book/{booking_link.token}"',
     '    ' + IMPORT_BOOK + '\n'
     '    booking_url = public_booking_url(db, lead.organization_id,\n'
     '                                     booking_link.token)'),

    ("app/services/ai_conversation_service.py",
     '    return f"{BOOKING_BASE_URL}/book/{link.token}"',
     '    ' + IMPORT_BOOK + '\n'
     '    return public_booking_url(db, lead.organization_id, link.token)'),

    ("app/services/cadence_service.py",
     '            import os\n'
     '            booking_url = f"{os.environ.get(\'BOOKING_BASE_URL\', \'\')}/book/{booking.token}"',
     '            ' + IMPORT_BOOK + '\n'
     '            booking_url = public_booking_url(db, lead.organization_id,\n'
     '                                             booking.token)'),

    ("app/services/pipeline_service.py",
     '    booking_url = f"{BOOKING_BASE_URL}/book/{booking.token}"',
     '    ' + IMPORT_BOOK + '\n'
     '    booking_url = public_booking_url(db, lead.organization_id,\n'
     '                                     booking.token)'),

    ("app/routers/leads_router.py",
     '    booking_url = f"{BOOKING_BASE_URL}/book/{link.token}"',
     '    ' + IMPORT_BOOK + '\n'
     '    booking_url = public_booking_url(db, lead.organization_id, link.token)'),

    ("app/routers/leads_router.py",
     '    placeholder_booking_url = f"{BOOKING_BASE_URL}/book/preview"',
     '    ' + IMPORT_BOOK + '\n'
     '    placeholder_booking_url = public_booking_url(\n'
     '        db, current_user.organization_id, "preview")'),

    ("app/routers/email_router.py",
     '            booking_url = f"{os.environ.get(\'BOOKING_BASE_URL\', \'https://advisorflow-booking.vercel.app\')}/book/{booking_link.token}"',
     '            ' + IMPORT_BOOK + '\n'
     '            booking_url = public_booking_url(db, lead.organization_id,\n'
     '                                             booking_link.token)'),

    ("app/services/post_appointment_service.py",
     '    survey_url = f"{BACKEND_URL}/survey/{survey_token}"',
     '    ' + IMPORT_SURVEY + '\n'
     '    survey_url = public_survey_url(db, advisor.organization_id, survey_token)'),
]


def main():
    changed = {}
    for edit in EDITS:
        rel, old, new = edit[0], edit[1], edit[2]
        expect = edit[3] if len(edit) > 3 else 1
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        with io.open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        n = src.count(old)
        if n != expect:
            print("FAIL  %s  expected %d occurrence(s), found %d"
                  % (rel, expect, n))
            print("      looking for: %r" % (old[:110],))
            sys.exit(1)
        src = src.replace(old, new, expect)
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(src)
        changed[rel] = changed.get(rel, 0) + 1
        print("ok    %s" % rel)
    print("\nrewrote %d call sites across %d files"
          % (len(EDITS), len(changed)))


if __name__ == "__main__":
    main()
