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
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    out, prev = [], tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.NL,
                    tokenize.DEDENT, tokenize.ENCODING):
                prev = tok.type
                continue
            out.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev = tok.type
    except tokenize.TokenError:
        return src
    return "\n".join(out)


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
        pv.ALL_PERMISSION_COLUMNS = frozenset()
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
    check("'No' grants in a do-not column", pv.interpret_cell("No", "deny") == pv.ALLOW)
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
    from app.models.import_models import SourceRecord
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


SECTIONS = (s1_denial_survives_simple, s5_no_weakening, s6_ambiguity, s7_tenancy,
            s8_activity_mapping, s9_never_contacted, s10_not_parked,
            s11_send_gate_reads_it)

REVERTS = ("R1_unknown_becomes_allow", "R2_incoming_overwrites",
           "R3_no_inheritance", "R4_polarity_from_name",
           "R5_activity_date_unmapped", "R6_permission_columns_parked",
           "R7_inheritance_ignores_tenant", "R8_send_gate_ignores_permission",
           "R9_unknown_treated_as_denial")


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
