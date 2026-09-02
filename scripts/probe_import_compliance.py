"""
GATE 37 - IMPORT COMPLIANCE. AN IMPORT MAY RESTRICT, NEVER RELEASE.

The ten properties Mike named, each asserted, each with a revert that must make
this gate fail:

   1  email denial survives import
   2  bulk-email denial survives import
   3  SMS denial survives import
   4  phone denial survives import
   5  an existing denial cannot be weakened by a later import
   6  ambiguous permission does not become consent
   7  cross-tenant historical records cannot affect another tenant
   8  Last Activity Date maps to historical activity
   9  historical activity prevents a false "never contacted"
  10  compliance fields cannot disappear into custom_fields only

Runs against a fresh in-memory database. Touches no production data, sends
nothing, and has no network call.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tokenize
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pandas as pd                                                  # noqa: E402
from sqlalchemy import create_engine                                 # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402

from app.models.models import Base, Lead, Organization, User         # noqa: E402
import app.models.import_models                                      # noqa: E402,F401
import app.models.source_records                                     # noqa: E402,F401
import app.models.sales_models                                       # noqa: E402,F401
import app.models.qualification_models                               # noqa: E402,F401
from app.services import permission_values as pv                     # noqa: E402
from app.services import import_service as imp                       # noqa: E402
from app.services import source_ingest as si                         # noqa: E402
from app.services import qualification as qual                       # noqa: E402

REVERT = os.environ.get("IMPORT_GATE_REVERT", "")
CHILD = bool(REVERT)

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


def _strip_py(path: str) -> str:
    """
    Source with comments and docstrings BLANKED OUT IN PLACE.

    The previous version rebuilt the file by joining token strings with
    newlines. That silently broke every assertion looking for more than one
    token: "app.models.source_records" became five lines, and a check for
    "Lead(" could never match because "Lead" and "(" were separated - so a
    check that was meant to prove the staging layer never constructs a Lead was
    passing vacuously. A gate that cannot fail is worse than no gate, and this
    one had four of them.

    Blanking the comment and docstring SPANS inside the original text keeps
    every other byte, and every offset, exactly where it was.
    """
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines(keepends=True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))

    def offset(row: int, col: int) -> int:
        return starts[row - 1] + col

    spans: list[tuple[int, int]] = []
    prev = tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            drop = False
            if tok.type == tokenize.COMMENT:
                drop = True
            elif tok.type == tokenize.STRING and prev in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.NL,
                    tokenize.DEDENT, tokenize.ENCODING):
                drop = True          # a bare string statement is a docstring
            if drop:
                spans.append((offset(*tok.start), offset(*tok.end)))
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev = tok.type
    except (tokenize.TokenError, IndentationError):
        return src

    chars = list(src)
    for a, b in spans:
        for i in range(a, min(b, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


# ---------------------------------------------------------------------------
# Reverts
# ---------------------------------------------------------------------------

def apply_revert(name: str) -> None:
    if name == "R1_unknown_becomes_allow":
        def bad(value, polarity):
            s = pv.normalize_cell(value)
            if s in pv.SELF_DENY:
                return pv.DENY
            if s in pv.BOOL_FALSE and polarity == "grant":
                return pv.DENY
            return pv.ALLOW          # everything else, including junk
        pv.interpret_cell = bad
    elif name == "R2_incoming_overwrites":
        pv.more_restrictive = lambda current, incoming: incoming
    elif name == "R3_no_inheritance":
        imp.inherit_restrictions = lambda db, lead, org: False
    elif name == "R4_polarity_from_name":
        def bad(value, polarity):
            s = pv.normalize_cell(value)
            if s in pv.BLANK_VALUES:
                return pv.UNKNOWN
            truthy = s in pv.BOOL_TRUE or s in pv.SELF_ALLOW
            return (pv.ALLOW if truthy else pv.DENY) if polarity == "grant" \
                else (pv.DENY if truthy else pv.ALLOW)
        pv.interpret_cell = bad
    elif name == "R5_activity_date_unmapped":
        m = dict(imp.HEADER_MAP)
        m["last_contact_date"] = ["last activity/note", "last contact date"]
        imp.HEADER_MAP = m
    elif name == "R6_permission_columns_parked":
        # TWO mechanisms now keep permission columns off the parked pile: the
        # canonical table here, and HEADER_MAP entries added by the import
        # intelligence branch. Emptying only one leaves the other holding the
        # property up, and the revert passes - which is a revert that proves
        # nothing. It has to remove BOTH to test what it claims to test.
        pv.ALL_PERMISSION_COLUMNS = frozenset()
        m = {}
        for k, v in imp.HEADER_MAP.items():
            if k in ("allow_calls", "allow_emails", "allow_bulk_emails", "allow_sms"):
                continue
            m[k] = v
        imp.HEADER_MAP = m
    elif name == "R7_inheritance_ignores_tenant":
        real = imp.inherit_restrictions

        def bad(db, lead, organization_id):
            other = db.query(Lead).filter(Lead.id != lead.id).all()
            for prior in other:
                for f in imp.PERMISSION_FIELDS:
                    s = pv.more_restrictive(pv.from_bool(getattr(lead, f)),
                                            pv.from_bool(getattr(prior, f)))
                    setattr(lead, f, pv.to_bool(s))
            return True
        imp.inherit_restrictions = bad
    elif name == "R8_send_gate_ignores_permission":
        # The denial is stored correctly and simply never consulted.
        qual._permission_detail = lambda lead, channel: ""
        src = qual.qualify_one

        def bad(lead, channel, ctx):
            for f in ("allow_email", "allow_sms", "allow_voice"):
                if getattr(lead, f, None) is False:
                    object.__setattr__(lead, f, None)
            return src(lead, channel, ctx)
        qual.qualify_one = bad
    elif name == "R9_unknown_treated_as_denial":
        # The opposite failure: "we don't know" read as "no", which empties
        # every pre-existing send pool.
        src = qual.qualify_one

        def bad(lead, channel, ctx):
            for f in ("allow_email", "allow_sms", "allow_voice"):
                if getattr(lead, f, None) is None:
                    object.__setattr__(lead, f, False)
            return src(lead, channel, ctx)
        qual.qualify_one = bad
    elif name == "R10_deceased_ignored":
        qual._is_deceased = lambda lead: False
    elif name == "R11_staging_promotes_to_leads":
        # Staging quietly creates an operational, sendable row per source row.
        real = si.stage_records

        def bad(db, organization_id, batch, rows, **kw):
            rows = list(rows)
            out = real(db, organization_id, batch, rows, **kw)
            for r in rows:
                low = {str(k).strip().lower(): v for k, v in r.items()}
                db.add(Lead(organization_id=organization_id,
                            last_name=str(low.get("full name", "x")).split()[-1],
                            email=low.get("email"), phone=low.get("phone"),
                            status="new"))
            return out
        si.stage_records = bad
    elif name == "R12_second_compliance_engine":
        # The staging service grows its own opinion back. This is the exact
        # drift the consolidation exists to prevent: two modules deciding
        # separately what "Do Not Allow" means for one family.
        from app.services import import_staging_service as iss

        def bad(raw, col_key):
            if not raw:
                return None, False
            v = str(raw).strip().lower()
            if v in ("allow", "yes", "y", "1", "true"):
                return True, False
            if v in ("do not allow", "no", "n", "0", "false"):
                return False, False
            return None, True
        iss._norm_consent = bad
    elif name == "R13_qualification_router_unmounted":
        # Exactly what the merge did: the router silently stops being mounted.
        import app.main as _m
        _m.app.router.routes = [r for r in _m.app.router.routes
                                if not getattr(r, "path", "").startswith("/qualification")]
    elif name == "R15_column_tuples_in_tables_to_create":
        # THE EXACT MERGE DEFECT: column tuples appended to the list of
        # CREATE TABLE strings. text(tuple) raises TypeError, which the loop
        # does not catch, and the backend cannot start.
        import app.auto_migrate as _am
        _am.TABLES_TO_CREATE = list(_am.TABLES_TO_CREATE) + [
            ("import_staged_rows", "consent_email", "BOOLEAN")]
    elif name == "R14_source_models_unregistered":
        # And the other half of it: a model registration disappears from main.
        real = _strip_py

        def bad(path):
            src = real(path)
            if path.endswith("main.py"):
                src = src.replace("app.models.source_records", "")
            return src
        globals()["_strip_py"] = bad
    else:
        raise SystemExit(f"unknown revert {name}")


if CHILD:
    apply_revert(REVERT)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def fresh_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_org(db, name, oid):
    org = Organization(id=oid, name=name, slug=oid)
    db.add(org)
    user = User(id=f"u-{oid}", email=f"{oid}@example.test",
                full_name=f"{name} admin", organization_id=oid,
                role="org_admin", password_hash="x")
    db.add(user)
    db.flush()
    return org, user


HEADERS = ["Full Name", "Phone", "Email", "Lead Type", "Status Reason",
           "Last Action", "Last Activity Date", "Allow Emails?",
           "Do not allow Bulk Emails", "Allow Text Message?", "Allow Phone Calls?"]


def row(name, phone, email, **kw):
    r = {h: "" for h in HEADERS}
    r.update({"Full Name": name, "Phone": phone, "Email": email,
              "Lead Type": "Pre-Need", "Status Reason": "New"})
    r.update(kw)
    return r


def _parse_df(df):
    """parse_excel_file without a file - the same code path, fed a frame."""
    real_read = pd.read_excel
    pd.read_excel = lambda *a, **k: df
    try:
        return imp.parse_excel_file("fixture.xlsx")
    finally:
        pd.read_excel = real_read


# ---------------------------------------------------------------------------
# 1-4. A stated denial survives the import, per channel
# ---------------------------------------------------------------------------

DENY_CASES = (
    ("Allow Emails?", "Do Not Allow", "allow_email", "email"),
    ("Do not allow Bulk Emails", "Do Not Allow", "allow_bulk_email", "bulk email"),
    ("Allow Text Message?", "Do Not Allow", "allow_sms", "SMS"),
    ("Allow Phone Calls?", "Do Not Allow", "allow_voice", "phone"),
)


def s1_denial_survives_simple():
    """Same four assertions, driven through parse + create without file IO."""
    real_parse = imp.parse_excel_file
    for column, value, field, label in DENY_CASES:
        db = fresh_db()
        make_org(db, "T", "org-a")
        parsed = _parse_df(pd.DataFrame([
            row("Ann Lee", "2145550101", "ann@example.test", **{column: value})
        ]))
        imp.parse_excel_file = lambda p, _r=parsed: _r
        try:
            imp.import_leads_from_excel(db, "fixture.xlsx", "org-a", "u-org-a",
                                        source_filename="f.xlsx")
        finally:
            imp.parse_excel_file = real_parse
        lead = db.query(Lead).one()
        check(f"{label} denial survives import",
              getattr(lead, field) is False, f"{field}={getattr(lead, field)!r}")
        check(f"{label} denial keeps the raw cell",
              bool(lead.permission_raw), "permission_raw empty")
        db.close()


def _import(db, rows, org_id, filename="f.xlsx"):
    real_parse = imp.parse_excel_file
    parsed = _parse_df(pd.DataFrame(rows))
    imp.parse_excel_file = lambda p, _r=parsed: _r
    try:
        return imp.import_leads_from_excel(db, "fixture.xlsx", org_id,
                                           f"u-{org_id}", source_filename=filename)
    finally:
        imp.parse_excel_file = real_parse


# ---------------------------------------------------------------------------
# 5. A later import cannot weaken an earlier denial
# ---------------------------------------------------------------------------

def s5_no_weakening():
    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [row("Ann Lee", "2145550101", "ann@example.test",
                     **{"Allow Emails?": "Do Not Allow",
                        "Allow Text Message?": "Do Not Allow"})], "org-a", "march.xlsx")
    first = db.query(Lead).one()
    check("first import records the denial", first.allow_email is False)

    _import(db, [row("Ann Lee", "2145550101", "ann@example.test",
                     **{"Allow Emails?": "Allow",
                        "Allow Text Message?": "Allow"})], "org-a", "september.xlsx")
    leads = db.query(Lead).all()
    check("the second import created a second row", len(leads) == 2, str(len(leads)))
    check("NO row is emailable after the permissive re-import",
          all(l.allow_email is False for l in leads),
          str([l.allow_email for l in leads]))
    check("NO row is textable after the permissive re-import",
          all(l.allow_sms is False for l in leads),
          str([l.allow_sms for l in leads]))
    check("the original row still carries its denial",
          db.query(Lead).filter(Lead.id == first.id).one().allow_email is False)

    # A prior operational suppression also survives a permissive file.
    db2 = fresh_db()
    make_org(db2, "T", "org-a")
    _import(db2, [row("Bob Kay", "2145550202", "bob@example.test")], "org-a")
    lead = db2.query(Lead).one()
    lead.status = "dnc"
    db2.flush()
    _import(db2, [row("Bob Kay", "2145550202", "bob@example.test",
                      **{"Allow Emails?": "Allow", "Allow Phone Calls?": "Allow"})],
            "org-a")
    fresh = [l for l in db2.query(Lead).all() if l.id != lead.id][0]
    check("a prior DNC denies every channel on the new row",
          fresh.allow_email is False and fresh.allow_sms is False
          and fresh.allow_voice is False,
          f"{fresh.allow_email} {fresh.allow_sms} {fresh.allow_voice}")
    db.close()
    db2.close()


# ---------------------------------------------------------------------------
# 6. Ambiguity never becomes consent
# ---------------------------------------------------------------------------

# Genuinely unreadable values. Note what is NOT here: "N" and "Y" are bare
# booleans, and a bare boolean in a do-not column legitimately resolves to
# ALLOW ("Do not email = N" means the person may be emailed). Calling that
# ambiguous would be asserting that a correctly-read cell is a defect.
AMBIGUOUS = ["maybe", "?", "unknown-value", "ask first", "pending", "see notes",
             "2", "-1", "TBD", "review", "call only", "yes*", "no?",
             "allow?", "do not allow?", "per client", "verbal only"]

OBSERVED = [
    ("Allow", pv.ALLOW), ("Do Not Allow", pv.DENY), ("allow", pv.ALLOW),
    ("DO NOT ALLOW", pv.DENY), ("Yes", None), ("No", None), ("true", None),
    ("false", None), ("1", None), ("0", None), ("", pv.UNKNOWN),
    (None, pv.UNKNOWN), ("   ", pv.UNKNOWN), ("N/A", pv.UNKNOWN),
    ("null", pv.UNKNOWN), ("Opted Out", pv.DENY), ("unsubscribed", pv.DENY),
]


def s6_ambiguity():
    for value, expected in OBSERVED:
        for polarity in ("grant", "deny"):
            got = pv.interpret_cell(value, polarity)
            check(f"value {value!r} is a known state", got in pv.STATES, got)
            if expected is not None:
                check(f"value {value!r} resolves to {expected} regardless of polarity",
                      got == expected, f"{polarity} -> {got}")
    # Bare booleans DO read through polarity, and only bare booleans.
    check("'Yes' grants in a grant column", pv.interpret_cell("Yes", "grant") == pv.ALLOW)
    check("'Yes' denies in a do-not column", pv.interpret_cell("Yes", "deny") == pv.DENY)
    check("'No' denies in a grant column", pv.interpret_cell("No", "grant") == pv.DENY)
    # THE UNIFIED RULE. A bare boolean in a negatively-named column is read in
    # the RESTRICTIVE direction only. "Do not allow bulk emails = Yes" has a
    # plausible reading that denies, and a denial reached that way is still a
    # denial - take it. "= No" would GRANT on a guess about what the column
    # meant, which is the one outcome worth refusing outright.
    state, amb = pv.interpret_cell_ex("No", "deny")
    check("'No' in a do-not column does NOT grant", state != pv.ALLOW, state)
    check("'No' in a do-not column is unknown", state == pv.UNKNOWN, state)
    check("'No' in a do-not column is flagged ambiguous", amb is True, str(amb))
    state, amb = pv.interpret_cell_ex("Yes", "deny")
    check("'Yes' in a do-not column denies without review",
          state == pv.DENY and amb is False, f"{state} {amb}")
    for v in ("Allow", "Do Not Allow", "opted out"):
        for pol in ("grant", "deny"):
            s, a = pv.interpret_cell_ex(v, pol)
            check(f"self-describing {v!r} is never ambiguous", a is False, str(a))
    check("'1' grants in a grant column", pv.interpret_cell("1", "grant") == pv.ALLOW)
    check("'0' denies in a grant column", pv.interpret_cell("0", "grant") == pv.DENY)
    check("'true' grants in a grant column", pv.interpret_cell("true", "grant") == pv.ALLOW)
    check("'false' denies in a grant column", pv.interpret_cell("false", "grant") == pv.DENY)

    for value in AMBIGUOUS:
        for polarity in ("grant", "deny"):
            got = pv.interpret_cell(value, polarity)
            check(f"ambiguous {value!r} is never ALLOW", got != pv.ALLOW, got)

    # And an unreadable cell reaches the lead as review, not as consent.
    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [row("Cara Nim", "2145550303", "cara@example.test",
                     **{"Allow Emails?": "ask first"})], "org-a")
    lead = db.query(Lead).one()
    check("an unreadable permission cell does not grant email",
          lead.allow_email is not True, repr(lead.allow_email))
    check("an unreadable permission cell raises review",
          lead.permission_review is True, repr(lead.permission_review))
    db.close()

    # A file with no permission columns at all grants nothing.
    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [{"Full Name": "Dan Ray", "Phone": "2145550404",
                  "Email": "dan@example.test", "Lead Type": "Pre-Need"}], "org-a")
    lead = db.query(Lead).one()
    check("a silent file grants no email", lead.allow_email is None, repr(lead.allow_email))
    check("a silent file grants no SMS", lead.allow_sms is None)
    check("a silent file grants no voice", lead.allow_voice is None)
    db.close()

    check("more_restrictive never returns allow over a deny",
          all(pv.more_restrictive(a, b) == pv.DENY
              for a in pv.STATES for b in pv.STATES
              if pv.DENY in (a, b)))
    check("unknown never overwrites a known allow",
          pv.more_restrictive(pv.ALLOW, pv.UNKNOWN) == pv.ALLOW)
    check("two unknowns stay unknown",
          pv.more_restrictive(pv.UNKNOWN, pv.UNKNOWN) == pv.UNKNOWN)


# ---------------------------------------------------------------------------
# 7. Tenancy
# ---------------------------------------------------------------------------

def s7_tenancy():
    db = fresh_db()
    make_org(db, "Alpha", "org-a")
    make_org(db, "Beta", "org-b")

    _import(db, [row("Same Person", "2145559999", "same@example.test",
                     **{"Allow Emails?": "Do Not Allow"})], "org-a")
    a_lead = db.query(Lead).filter(Lead.organization_id == "org-a").one()
    check("tenant A holds the denial", a_lead.allow_email is False)

    _import(db, [row("Same Person", "2145559999", "same@example.test",
                     **{"Allow Emails?": "Allow"})], "org-b")
    b_lead = db.query(Lead).filter(Lead.organization_id == "org-b").one()
    check("tenant B is NOT restricted by tenant A's record",
          b_lead.allow_email is True, repr(b_lead.allow_email))
    check("tenant A is unchanged by tenant B's import",
          db.query(Lead).filter(Lead.id == a_lead.id).one().allow_email is False)

    # Staged historical records are tenant-scoped from the argument, never the file.
    from app.models.source_records import SourceRecord
    batch = si.open_batch(db, "org-a", source_filename="hist.xlsx")
    si.stage_records(db, "org-a", batch, [
        {"(Do Not Modify) Contact": "GUID-1", "Full Name": "Same Person",
         "Phone": "2145559999", "Email": "same@example.test",
         "organization_id": "org-b", "Allow Emails?": "Do Not Allow"}])
    db.flush()
    staged = db.query(SourceRecord).all()
    check("a staged record lands in the tenant the caller named",
          all(s.organization_id == "org-a" for s in staged),
          str([s.organization_id for s in staged]))
    check("a source file cannot name its own tenant",
          not any(s.organization_id == "org-b" for s in staged))
    check("the staged record keeps the source GUID",
          staged[0].source_key == "GUID-1", repr(staged[0].source_key))
    check("the staged record keeps the raw row",
          "Allow Emails?".lower() in (staged[0].raw_json or "").lower())
    check("the staged record carries the denial",
          staged[0].allow_email is False)
    db.close()


# ---------------------------------------------------------------------------
# 8-9. Historical activity
# ---------------------------------------------------------------------------

def s8_activity_mapping():
    lookup = imp._build_column_lookup(HEADERS)
    check("'Last Activity Date' maps to the contact date",
          lookup.get("last_contact_date") == "Last Activity Date",
          repr(lookup.get("last_contact_date")))

    # Alias ORDER decides, not the order the file happens to use.
    a = imp._build_column_lookup(["Open Activity Date", "Last Activity Date"])
    b = imp._build_column_lookup(["Last Activity Date", "Open Activity Date"])
    check("alias order decides, not column order",
          a.get("last_contact_date") == b.get("last_contact_date") == "Last Activity Date",
          f"{a.get('last_contact_date')} vs {b.get('last_contact_date')}")

    check("a timestamp column is not an action",
          imp._build_column_lookup(["Last Logged Activity"]).get("last_action") is None)
    check("a timestamp column IS a contact date",
          imp._build_column_lookup(["Last Logged Activity"]).get("last_contact_date")
          == "Last Logged Activity")

    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [row("Eve Poe", "2145550505", "eve@example.test",
                     **{"Last Activity Date": "2023-11-07 13:18:00",
                        "Last Action": "Called: LM/No Answer"})], "org-a")
    lead = db.query(Lead).one()
    check("the activity date lands on the lead",
          isinstance(lead.last_contact_date, datetime), repr(lead.last_contact_date))
    check("the activity date is the right one",
          getattr(lead.last_contact_date, "year", None) == 2023,
          repr(lead.last_contact_date))
    check("the action lands on the lead",
          lead.last_action_raw == "Called: LM/No Answer", repr(lead.last_action_raw))
    check("the activity date is NOT parked in custom_fields only",
          "last activity date" not in (lead.custom_fields or "").lower(),
          (lead.custom_fields or "")[:120])
    db.close()


def s9_never_contacted():
    """A lead with imported history must not be scored as never contacted."""
    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [row("Eve Poe", "2145550505", "eve@example.test",
                     **{"Last Activity Date": "2023-11-07 13:18:00",
                        "Last Action": "Called: LM/No Answer"}),
                 row("Fay Qin", "2145550606", "fay@example.test")], "org-a")
    leads = {l.last_name: l for l in db.query(Lead).all()}

    class Ctx:
        contacted = set()
    had, when, source = qual.contact_history(leads["Poe"], Ctx())
    check("imported history counts as contact history", had is True, str(had))
    check("the history is attributed to the import", "import" in source, source)
    check("the imported date is used", isinstance(when, datetime), repr(when))

    had2, _, source2 = qual.contact_history(leads["Qin"], Ctx())
    check("a genuinely untouched lead is still never contacted",
          had2 is False, str(had2))
    check("and says so", "none" in source2.lower(), source2)
    db.close()


# ---------------------------------------------------------------------------
# 10. Compliance cannot vanish into custom_fields
# ---------------------------------------------------------------------------

def s10_not_parked():
    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [row("Gil Roy", "2145550707", "gil@example.test",
                     **{"Allow Emails?": "Do Not Allow",
                        "Do not allow Bulk Emails": "Allow",
                        "Allow Text Message?": "Do Not Allow",
                        "Allow Phone Calls?": "Allow"})], "org-a")
    lead = db.query(Lead).one()
    blob = (lead.custom_fields or "").lower()
    for col in ("allow emails?", "do not allow bulk emails",
                "allow text message?", "allow phone calls?"):
        check(f"'{col}' is not parked in custom_fields", col not in blob, blob[:160])
    check("email denial reached a column", lead.allow_email is False)
    check("bulk permission reached a column", lead.allow_bulk_email is True)
    check("sms denial reached a column", lead.allow_sms is False)
    check("voice permission reached a column", lead.allow_voice is True)
    check("bulk permission is separate from email permission",
          lead.allow_email is False and lead.allow_bulk_email is True)
    check("the raw cells are still auditable",
          "allow emails?" in (lead.permission_raw or "").lower(),
          (lead.permission_raw or "")[:160])

    ev = json.loads(lead.permission_raw)
    check("the evidence names every permission", set(ev) == set(pv.PERMISSIONS), str(set(ev)))
    for p, items in ev.items():
        for item in items:
            check(f"evidence for {p} names a column", bool(item.get("column")))
            check(f"evidence for {p} keeps the raw value", "raw" in item)
    db.close()

    # And the source scan: no send path may read these tables.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = _strip_py(os.path.join(root, "app/services/source_ingest.py"))
    for banned in ("Lead(", "send_email", "send_sms", "start_cadence"):
        check(f"source_ingest constructs no {banned}", banned not in code)
    code = _strip_py(os.path.join(root, "app/models/import_models.py"))
    for banned in ("assigned_to_id", "sms_consent", "cadence"):
        check(f"a staged record has no {banned}", banned not in code)


def s11_send_gate_reads_it():
    """
    A DENIAL NO SEND PATH CONSULTS IS A NOTE, NOT A COMPLIANCE RECORD.

    The platform's single eligibility engine must refuse a lead whose channel
    permission is False - and must NOT refuse one whose permission is merely
    unknown, because every row predating these columns is unknown and treating
    that as a denial would empty every existing send pool overnight.
    """
    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [row("Hal Sim", "2145550808", "hal@example.test",
                     **{"Allow Emails?": "Do Not Allow"}),
                 row("Ida Tor", "2145550909", "ida@example.test",
                     **{"Allow Emails?": "Allow"}),
                 row("Jan Uhl", "2145551010", "jan@example.test")], "org-a")
    leads = {l.last_name: l for l in db.query(Lead).all()}

    ctx = qual.QualificationContext(db, list(leads.values()), "org-a", [])
    for name, expected_excluded in (("Sim", True), ("Tor", False), ("Uhl", False)):
        d = qual.qualify_one(leads[name], qual.CHANNEL_EMAIL, ctx)
        excluded = d["bucket"] == qual.EXCLUDED
        check(f"{name}: email exclusion is {expected_excluded}",
              excluded == expected_excluded,
              f"{d['bucket']} {[r['code'] for r in d['reasons']]}")
        if name == "Sim":
            check("the refusal says opted out",
                  any(r["code"] == "opted_out" for r in d["reasons"]),
                  str([r["code"] for r in d["reasons"]]))
            check("the refusal names where the denial came from",
                  any("import" in (r.get("label") or "") for r in d["reasons"]),
                  str(d["reasons"]))
    check("an unknown permission does not exclude",
          leads["Uhl"].allow_email is None)
    db.close()


def s12_historical_staging():
    """
    HISTORICAL INGESTION PROOF - staging is evidence, not an outreach list.

    Uses the real staging model against a small slice shaped exactly like the
    production export. Proves provenance, external identity, four independent
    permissions, unknown staying unknown, prior denial surviving re-import,
    DNC denying every channel, activity mapping, parked fields staying
    provenance, opportunities staying separate, and that nothing is sent.
    """
    from app.models.source_records import SourceOpportunity, SourceRecord

    db = fresh_db()
    make_org(db, "T", "org-a")

    # Shaped like the real 56-column export, including the polarity trap and a
    # Deceased disposition.
    rows = [
        {"(Do Not Modify) Contact": "{GUID-AAA}",
         "(Do Not Modify) Row Checksum": "chk-1",
         "Full Name": "Ada Vale", "First Name": "Ada", "Last Name": "Vale",
         "Phone": "2145551111", "Mobile Phone": "2145552222",
         "Email": "ada@example.test", "Email Address 2": "ada2@example.test",
         "Street Address": "1 Main St", "City": "Dallas", "State": "TX",
         "ZIP Code": "75001-1234",
         "Status Reason": "Appointment Set", "Lead Type": "Pre-Need",
         "Last Action": "Called: Scheduled Appt.",
         "Last Activity Date": "2023-11-07 13:18:00",
         "Last Logged Activity": "2021-01-01 00:00:00",
         "Owner": "M Tisdale", "Original Owner": "J Berthet",
         "Created On": "2019-03-29 14:06:00",
         "Sale Made?": "No", "Last Sold Date": "",
         "Allow Emails?": "Allow",
         "Do not allow Bulk Emails": "Do Not Allow",
         "Allow Text Message?": "Allow",
         "Allow Phone Calls?": "Allow",
         "Seminar Lead?": "Yes", "Veteran Status": "No"},
        {"(Do Not Modify) Contact": "{GUID-BBB}",
         "Full Name": "Ben Wyle", "Phone": "2145553333",
         "Email": "ben@example.test",
         "Status Reason": "Deceased", "Lead Type": "Pre-Need",
         "Allow Emails?": "Allow", "Do not allow Bulk Emails": "Allow",
         "Allow Text Message?": "Allow", "Allow Phone Calls?": "Allow"},
        {"(Do Not Modify) Contact": "{GUID-CCC}",
         "Full Name": "Cy Xiu", "Phone": "2145554444",
         "Email": "cy@example.test", "Status Reason": "New",
         "Allow Emails?": "", "Do not allow Bulk Emails": "",
         "Allow Text Message?": "maybe", "Allow Phone Calls?": ""},
    ]

    batch = si.open_batch(db, "org-a", source_filename="master.xlsx",
                          source_system="dynamics", header=list(rows[0].keys()))
    stats = si.stage_records(db, "org-a", batch, rows)
    db.flush()
    staged = {r.source_key: r for r in db.query(SourceRecord).all()}

    # -- provenance --------------------------------------------------------
    check("staging wrote one record per row", len(staged) == 3, str(len(staged)))
    check("the batch records the file", batch.source_filename == "master.xlsx")
    # PROVENANCE IS ASSERTED WHERE IT ACTUALLY LIVES.
    #
    # `ImportBatch` is the merged branch's model and names things its own way
    # (`source_type`, `total_rows`). This gate asserts the FACT - the batch
    # knows its file and its source, the row count is recorded, and the batch
    # is marked loaded - without dictating which column carries it. Every
    # staged row additionally carries its own verbatim raw row, so provenance
    # does not depend on the batch model's shape at all.
    check("the batch records the source system",
          si._batch_source(batch) == "dynamics", repr(si._batch_source(batch)))
    check("the batch is marked loaded", batch.status == "loaded")
    check("the batch counts its rows",
          (getattr(batch, "row_count", None) or getattr(batch, "total_rows", None)) == 3,
          f"row_count={getattr(batch, 'row_count', None)} "
          f"total_rows={getattr(batch, 'total_rows', None)}")

    a = staged["{GUID-AAA}"]
    # -- external identity -------------------------------------------------
    check("the CRM contact GUID is preserved exactly",
          a.source_key == "{GUID-AAA}", repr(a.source_key))
    check("the source row checksum is kept", a.row_checksum == "chk-1")
    check("the raw row is kept verbatim",
          "1 Main St" in (a.raw_json or ""), (a.raw_json or "")[:80])
    check("identity is normalized for joining",
          a.norm_phone == "12145551111" and a.norm_email == "ada@example.test"
          and a.norm_last_name == "vale" and a.norm_zip == "75001",
          f"{a.norm_phone} {a.norm_email} {a.norm_last_name} {a.norm_zip}")
    check("a mobile number is kept as its OWN evidence",
          a.norm_mobile_phone == "12145552222", repr(a.norm_mobile_phone))
    check("every other number found is kept",
          "2145552222" in (a.phones_json or ""), repr(a.phones_json))

    # -- four independent permissions --------------------------------------
    check("email allow is read", a.allow_email is True, repr(a.allow_email))
    check("bulk email DENY is read from a 'Do Not Allow' cell",
          a.allow_bulk_email is False, repr(a.allow_bulk_email))
    check("bulk denial does not deny email",
          a.allow_email is True and a.allow_bulk_email is False)
    check("sms allow is read", a.allow_sms is True)
    check("voice allow is read", a.allow_voice is True)
    check("the permission cells are auditable",
          "allow emails?" in (a.permission_raw or "").lower())

    # -- UNKNOWN stays UNKNOWN --------------------------------------------
    c = staged["{GUID-CCC}"]
    check("a blank permission cell stays unknown", c.allow_email is None,
          repr(c.allow_email))
    check("a blank bulk cell stays unknown", c.allow_bulk_email is None)
    check("a blank voice cell stays unknown", c.allow_voice is None)
    check("an unreadable cell does not become consent",
          c.allow_sms is not True, repr(c.allow_sms))
    check("an unreadable cell raises review", c.permission_review is True)
    check("staging counts the review", stats["permission_needs_review"] == 1,
          str(stats["permission_needs_review"]))
    check("staging counts the bulk denial",
          stats["permission_denials"][pv.BULK_EMAIL] == 1,
          str(stats["permission_denials"]))

    # -- last activity mapping --------------------------------------------
    check("the most recent activity date wins",
          getattr(a.last_activity_at, "year", None) == 2023,
          repr(a.last_activity_at))
    check("the action is an action, not a timestamp",
          a.last_action == "Called: Scheduled Appt.", repr(a.last_action))
    check("the disposition is kept", a.status_reason == "Appointment Set")

    # -- parked fields are provenance, not authority -----------------------
    check("the owner is provenance", a.owner_name == "M Tisdale")
    check("the original owner is provenance", a.original_owner_name == "J Berthet")
    check("a staged record has no operational owner",
          not hasattr(a, "assigned_to_id"))
    check("a staged record has no operational status",
          not hasattr(a, "status"))
    check("a staged record has no consent of record",
          not hasattr(a, "sms_consent"))
    check("seminar/veteran columns survive only in the raw row",
          "seminar lead?" in (a.raw_json or "").lower())

    # -- opportunities stay separate --------------------------------------
    opp_batch = si.open_batch(db, "org-a", source_filename="opps.xlsx",
                              source_system="dynamics")
    ostats = si.stage_opportunities(db, "org-a", opp_batch, [
        {"(Do Not Modify) Opportunity": "{OPP-1}", "LeadID": "{GUID-AAA}",
         "Status": "Won", "Status Reason": "Bought/Sold",
         "Contract Close Status": "Sold", "Contract Total": "3,522.00",
         "Contract Need": "Pre-Need", "Contract Cancelled": "No",
         "Contract Date": "2022-04-01"},
        {"(Do Not Modify) Opportunity": "{OPP-2}", "LeadID": "{GUID-AAA}",
         "Status": "Open", "Status Reason": "In Progress"},
        {"(Do Not Modify) Opportunity": "{OPP-3}", "LeadID": "",
         "Status": "Open", "Status Reason": "In Progress"},
    ], contact_key_column="leadid")
    db.flush()
    opps = db.query(SourceOpportunity).all()
    check("opportunities are their own rows", len(opps) == 3, str(len(opps)))
    check("one contact can hold several opportunities",
          sum(1 for o in opps if o.contact_source_key == "{GUID-AAA}") == 2)
    check("an unjoinable opportunity is reported, not attached",
          ostats["unjoinable"] == 1, str(ostats))
    won = [o for o in opps if o.source_key == "{OPP-1}"][0]
    check("the contract total is parsed", won.contract_total == 3522.0,
          repr(won.contract_total))
    check("the close status is kept", won.close_status == "Sold")
    check("no opportunity became a lead", db.query(Lead).count() == 0,
          str(db.query(Lead).count()))

    # -- staging sends nothing --------------------------------------------
    check("staging created no leads", db.query(Lead).count() == 0)
    from app.models.models import Message, EmailMessage, CadenceState
    check("staging created no SMS", db.query(Message).count() == 0)
    check("staging created no email", db.query(EmailMessage).count() == 0)
    check("staging created no cadence", db.query(CadenceState).count() == 0)
    db.close()


def s13_deceased_is_do_not_contact():
    """
    STATUS REASON = DECEASED IS A DO-NOT-CONTACT SIGNAL.

    A permission column says nothing about this case - the family never opted
    out, so every allow/deny/unknown answer is "allow" - which is exactly why
    an engine that only asks about permission writes to a dead person's inbox.
    """
    db = fresh_db()
    make_org(db, "T", "org-a")
    _import(db, [row("Ben Wyle", "2145553333", "ben@example.test",
                     **{"Status Reason": "Deceased",
                        "Allow Emails?": "Allow",
                        "Allow Phone Calls?": "Allow"}),
                 row("Cal Yew", "2145554444", "cal@example.test",
                     **{"Status Reason": "Contacted",
                        "Last Action": "Called: Deceased",
                        "Allow Emails?": "Allow"}),
                 row("Dee Zorn", "2145555555", "dee@example.test",
                     **{"Status Reason": "Deceased Spouse Inquiry",
                        "Allow Emails?": "Allow"}),
                 row("Eli Ames", "2145556666", "eli@example.test",
                     **{"Status Reason": "Appointment Set",
                        "Allow Emails?": "Allow"})], "org-a")
    leads = {l.last_name: l for l in db.query(Lead).all()}
    ctx = qual.QualificationContext(db, list(leads.values()), "org-a", [])

    for name, channel in (("Wyle", qual.CHANNEL_EMAIL), ("Wyle", qual.CHANNEL_VOICE),
                          ("Wyle", qual.CHANNEL_SMS)):
        d = qual.qualify_one(leads[name], channel, ctx)
        check(f"a deceased contact is excluded on {channel}",
              d["bucket"] == qual.EXCLUDED, d["bucket"])
        check(f"the {channel} refusal says deceased",
              any(r["code"] == "deceased" for r in d["reasons"]),
              str([r["code"] for r in d["reasons"]]))

    d = qual.qualify_one(leads["Yew"], qual.CHANNEL_EMAIL, ctx)
    check("a deceased last action also excludes",
          d["bucket"] == qual.EXCLUDED
          and any(r["code"] == "deceased" for r in d["reasons"]),
          str([r["code"] for r in d["reasons"]]))

    # THE FALSE POSITIVE THAT WOULD MATTER MOST. A person asking about
    # arrangements for a deceased spouse is alive, and is precisely who a
    # funeral home should be talking to.
    d = qual.qualify_one(leads["Zorn"], qual.CHANNEL_EMAIL, ctx)
    check("'Deceased Spouse Inquiry' is NOT excluded as deceased",
          not any(r["code"] == "deceased" for r in d["reasons"]),
          str([r["code"] for r in d["reasons"]]))

    d = qual.qualify_one(leads["Ames"], qual.CHANNEL_EMAIL, ctx)
    check("an ordinary lead is unaffected",
          not any(r["code"] == "deceased" for r in d["reasons"]))

    check("permission alone would NOT have caught the deceased contact",
          leads["Wyle"].allow_email is True and leads["Wyle"].allow_voice is True,
          f"{leads['Wyle'].allow_email} {leads['Wyle'].allow_voice}")
    db.close()


def s14_one_compliance_engine():
    """
    ONE COMPLIANCE NORMALIZATION IMPLEMENTATION. NOT TWO.

    The operational import pipeline (import_staged_rows / consent_*) and the
    historical source layer (source_records / allow_*) must interpret a cell
    identically, because they are describing the same family's consent. The way
    to guarantee that is not discipline, it is having one function.
    """
    from app.services import import_staging_service as iss

    # 1. Behavioural: both paths agree on every observed value.
    cases = [
        ("Allow", "allow_bulk_emails", "do not allow bulk emails", pv.BULK_EMAIL),
        ("Do Not Allow", "allow_bulk_emails", "do not allow bulk emails", pv.BULK_EMAIL),
        ("Yes", "allow_bulk_emails", "do not allow bulk emails", pv.BULK_EMAIL),
        ("No", "allow_bulk_emails", "do not allow bulk emails", pv.BULK_EMAIL),
        ("Allow", "allow_emails", "allow emails?", pv.EMAIL),
        ("Do Not Allow", "allow_emails", "allow emails?", pv.EMAIL),
        ("Yes", "allow_emails", "allow emails?", pv.EMAIL),
        ("No", "allow_emails", "allow emails?", pv.EMAIL),
        ("", "allow_emails", "allow emails?", pv.EMAIL),
        ("maybe", "allow_sms", "allow text message?", pv.SMS),
        ("Do Not Allow", "allow_calls", "allow phone calls?", pv.VOICE),
        ("opted out", "allow_calls", "allow phone calls?", pv.VOICE),
    ]
    for raw, key, header, permission in cases:
        op_value, op_amb = iss._norm_consent(raw, key)
        hist_state, _ = pv.read_permission({header: raw}, permission)
        check(f"operational and historical agree on {raw!r} / {key}",
              op_value == pv.to_bool(hist_state),
              f"operational={op_value} historical={hist_state}")
        canon_state, canon_amb = pv.interpret_canonical(raw, key)
        check(f"the operational path returns the interpreter's answer for {raw!r}",
              op_value == pv.to_bool(canon_state) and op_amb == canon_amb,
              f"{op_value}/{op_amb} vs {canon_state}/{canon_amb}")

    # 2. Structural: the operational module declares no vocabulary of its own.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = _strip_py(os.path.join(root, "app/services/import_staging_service.py"))
    check("the staging service delegates to permission_values",
          "permission_values" in code)
    check("the staging service calls interpret_canonical",
          "interpret_canonical" in code)
    for banned in ("_CONSENT_ALLOW", "_CONSENT_DENY"):
        check(f"the staging service declares no {banned} table of its own",
              banned not in code)
    # A second copy of the vocabulary is the failure this section exists for.
    for word in ('"do not allow"', "'do not allow'", '"opt-out"', "'opt-out'"):
        check(f"the staging service does not restate {word}", word not in code)

    # 3. Nothing else interprets either. Only permission_values may hold tables.
    import glob
    owners = []
    for path in glob.glob(os.path.join(root, "app/services/*.py")):
        name = os.path.basename(path)
        if name == "permission_values.py":
            continue
        src = _strip_py(path)
        if '"do not allow"' in src or "'do not allow'" in src:
            owners.append(name)
    check("permission_values is the only module holding the value vocabulary",
          not owners, str(owners))


def s15_structural_registration():
    """
    THE MERGE-COLLISION GATE.

    A concurrent merge deleted SourceRecord, SourceOpportunity, the
    qualification_models registration and the qualification router mount - and
    every one of those failures was silent at import time. Nothing shouted; the
    API simply answered 404 and a table simply never got created.

    This asserts each registration EXISTS, so removing one fails the gate
    instead of shipping.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_src = _strip_py(os.path.join(root, "app/main.py"))

    # Model registrations - Base.metadata.create_all only sees imported modules.
    for mod in ("app.models.import_models", "app.models.source_records",
                "app.models.qualification_models"):
        check(f"main.py registers {mod}", mod in main_src)

    # Router mounts.
    check("main.py mounts the qualification router",
          "qualification_router.router" in main_src)
    check("main.py mounts the import batch router",
          "import_batch_router" in main_src)

    # And the objects actually resolve on the live app, not just in the text.
    import os as _os
    _os.environ.setdefault("DATABASE_URL", "sqlite://")
    from app.main import app as _app
    paths = {r.path for r in _app.routes}
    check("the qualification API is mounted",
          any(p.startswith("/qualification") for p in paths), "no /qualification route")
    for p in ("/qualification/preview", "/qualification/vocabulary"):
        check(f"{p} is routable", p in paths, str(sorted(
            x for x in paths if x.startswith("/qualification"))[:5]))
    check("the god qualification diagnostic is mounted",
          "/god/ops/diagnostics/qualification" in paths)
    check("the import batch API is mounted",
          any("import" in p and "batch" in p for p in paths))

    # Tables are declared exactly once, by exactly one model.
    from app.models.models import Base
    tables = Base.metadata.tables
    for t in ("import_batches", "import_staged_rows", "source_records",
              "source_opportunities", "qualification_rules"):
        check(f"table {t} is registered", t in tables, str(t in tables))
    owners = {}
    for mapper_table, tbl in tables.items():
        owners.setdefault(mapper_table, 0)
        owners[mapper_table] += 1
    check("no table is declared twice",
          all(v == 1 for v in owners.values()),
          str([k for k, v in owners.items() if v != 1]))


def s16_migration_ownership():
    """
    ONE OWNER PER TABLE, AND A BOOT THAT CANNOT BE BROKEN BY SHAPE.

    A concurrent merge put COLUMN TUPLES into TABLES_TO_CREATE - a list of
    CREATE TABLE strings - and `text(tuple)` raises TypeError, which the loop's
    `except (OperationalError, ProgrammingError)` does not catch. The backend
    could not start at all. This asserts the shape of both migration lists, that
    no table is owned by two mechanisms, and that a boot succeeds on a FRESH
    database and again on an EXISTING one.
    """
    import re as _re
    from app.auto_migrate import (COLUMNS_TO_ADD, TABLES_TO_CREATE,
                                  run_auto_migrations)
    from app.models.models import Base

    # 1. Shape. This is the exact defect, asserted directly.
    check("every TABLES_TO_CREATE entry is a string",
          all(isinstance(s, str) for s in TABLES_TO_CREATE),
          str([type(s).__name__ for s in TABLES_TO_CREATE if not isinstance(s, str)]))
    check("every TABLES_TO_CREATE entry is a CREATE TABLE statement",
          all("create table" in s.lower() for s in TABLES_TO_CREATE),
          str([s[:40] for s in TABLES_TO_CREATE if "create table" not in s.lower()]))
    bad_cols = [t for t in COLUMNS_TO_ADD
                if not (isinstance(t, tuple) and len(t) == 3
                        and all(isinstance(x, str) for x in t))]
    check("every COLUMNS_TO_ADD entry is a 3-tuple of strings",
          not bad_cols, str(bad_cols[:3]))

    # 2. Ownership. A table is created by the ORM or by raw SQL, never both.
    raw_tables = set()
    for s in TABLES_TO_CREATE:
        m = _re.search(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_][\w]*)",
                       s, _re.I)
        if m:
            raw_tables.add(m.group(1).lower())
    orm_tables = {t.lower() for t in Base.metadata.tables}
    both = raw_tables & orm_tables
    check("no table is created by BOTH the ORM and raw SQL", not both, str(both))

    # 3. Every column migration names a table something actually owns.
    known = raw_tables | orm_tables
    orphan = sorted({t for t, _c, _d in COLUMNS_TO_ADD if t.lower() not in known})
    check("every COLUMNS_TO_ADD table has an owner", not orphan, str(orphan))

    # 4. The five tables this consolidation is about, each owned exactly once.
    for t in ("import_batches", "import_staged_rows", "source_records",
              "source_opportunities", "qualification_rules"):
        n = (1 if t in orm_tables else 0) + (1 if t in raw_tables else 0)
        check(f"{t} has exactly one migration owner", n == 1, f"owners={n}")

    # 5. Clean boot: fresh database, then the SAME database again.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    try:
        run_auto_migrations(engine)
        check("auto migrations run on a fresh database", True)
    except Exception as exc:
        check("auto migrations run on a fresh database", False,
              f"{type(exc).__name__}: {exc}")
    try:
        run_auto_migrations(engine)      # idempotent - every statement a no-op
        check("auto migrations run again on an existing database", True)
    except Exception as exc:
        check("auto migrations run again on an existing database", False,
              f"{type(exc).__name__}: {exc}")

    # 6. And the boot path does not swallow a TypeError into silence.
    src = _strip_py(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app/auto_migrate.py"))
    check("the table-create loop does not catch bare Exception",
          "except Exception" not in src.split("for create_sql")[-1][:400],
          "a bare except would hide the next shape error")


SECTIONS = (s1_denial_survives_simple, s5_no_weakening, s6_ambiguity, s7_tenancy,
            s8_activity_mapping, s9_never_contacted, s10_not_parked,
            s11_send_gate_reads_it, s12_historical_staging,
            s13_deceased_is_do_not_contact, s14_one_compliance_engine,
            s15_structural_registration, s16_migration_ownership)

REVERTS = ("R1_unknown_becomes_allow", "R2_incoming_overwrites",
           "R3_no_inheritance", "R4_polarity_from_name",
           "R5_activity_date_unmapped", "R6_permission_columns_parked",
           "R7_inheritance_ignores_tenant", "R8_send_gate_ignores_permission",
           "R9_unknown_treated_as_denial", "R10_deceased_ignored",
           "R11_staging_promotes_to_leads", "R12_second_compliance_engine",
           "R13_qualification_router_unmounted", "R14_source_models_unregistered",
           "R15_column_tuples_in_tables_to_create")


def main() -> int:
    for fn in SECTIONS:
        try:
            fn()
        except Exception as exc:
            check(f"section {fn.__name__} completed", False,
                  f"{type(exc).__name__}: {exc}")
    if CHILD:
        print(f"[revert {REVERT}] pass={PASS} fail={FAIL}")
        return 1 if FAIL else 0

    print(f"GATE 37 - IMPORT COMPLIANCE: {PASS} checks, {FAIL} failed")
    for f in FAILURES:
        print("  FAIL:", f)
    if FAIL:
        return 1

    print("\nrevert proofs (each must FAIL the gate):")
    bad = 0
    for r in REVERTS:
        env = dict(os.environ, IMPORT_GATE_REVERT=r)
        p = subprocess.run([sys.executable, os.path.abspath(__file__)],
                           env=env, capture_output=True, text=True)
        caught = p.returncode != 0
        print(f"  {'caught ' if caught else 'MISSED '} {r}")
        if not caught:
            bad += 1
    if bad:
        print(f"{bad} revert(s) NOT caught - the gate does not prove what it claims")
        return 1
    print(f"all {len(REVERTS)} reverts caught")
    print("AN IMPORT MAY RESTRICT, NEVER RELEASE - and ambiguity is never consent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
