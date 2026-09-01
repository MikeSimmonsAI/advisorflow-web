"""
GATE 36 - SOURCE RECONCILIATION

Proves the reconciliation engine's safety properties, and proves the gate can
fail by breaking each property in a child process and requiring a non-zero exit.

  python scripts/probe_reconciliation.py

A checker that cannot fail is decoration. Every property here has a revert.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tokenize
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services import source_reconciliation as sr        # noqa: E402
from app.services import source_adapters as sa              # noqa: E402

REVERT = os.environ.get("RECON_GATE_REVERT", "")
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
    Source with comments and docstrings removed.

    A gate that reads raw source flags a module for the COMMENT explaining why
    it does not do the thing. This suite has been bitten by that twice; it
    strips first and asserts second.
    """
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    out = []
    prev_type = tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev_type in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.NL,
                    tokenize.DEDENT, tokenize.ENCODING):
                prev_type = tok.type
                continue
            out.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev_type = tok.type
    except tokenize.TokenError:
        return src
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Reverts - each breaks ONE property. The parent requires each to fail.
# ---------------------------------------------------------------------------

def apply_revert(name: str) -> None:
    if name == "R1_polarity_ignores_value":
        def bad(v, polarity):
            b = sa.parse_bool(v)
            if b is None:
                s = str(v).strip().lower()
                b = s.startswith("allow")
            return b if polarity == "positive" else (not b)
        sa.parse_permission = bad
    elif name == "R2_compliance_can_loosen":
        def bad(t, s):
            out = {"channels": {}, "findings": [], "discovered_restriction": False,
                   "suppressed": False}
            for ch in sr.CHANNELS:
                hist = getattr(s, f"allow_{ch}", None) if s else None
                cur = getattr(t, f"allow_{ch}", None)
                out["channels"][ch] = hist if hist is not None else cur
            return out
        sr.reconcile_compliance = bad
    elif name == "R3_enrichment_overwrites":
        def bad(t, s, confidence):
            fills = []
            for f in sr.ENRICHABLE_FIELDS:
                hist = sr._value(s, f)
                if not sr._blank(hist):
                    fills.append({"field": f, "current": sr._value(t, f),
                                  "proposed": hist, "confidence": confidence,
                                  "auto_applicable": True,
                                  "reason_code": "blank_fill"})
            return {"fills": fills, "conflicts": []}
        sr.propose_enrichment = bad
    elif name == "R4_weak_match_auto":
        sr.AUTO_CONFIDENCE_FLOOR = 30
    elif name == "R5_multiple_collapses":
        real = sr.match_record

        def bad(t, index):
            f = real(t, index)
            if f["match_status"] == sr.MULTIPLE_MATCHES:
                f["match_status"] = sr.MATCHED_HIGH_CONFIDENCE
            return f
        sr.match_record = bad
    elif name == "R6_date_column_as_action":
        sa.ALIASES = dict(sa.ALIASES)
        sa.ALIASES["last_action"] = ("last action", "last logged activity")
    else:
        raise SystemExit(f"unknown revert {name}")


if CHILD:
    apply_revert(REVERT)


# ---------------------------------------------------------------------------
# 1. Permission polarity - the trap
# ---------------------------------------------------------------------------

def s1_polarity():
    # A column named for a denial whose CELLS state permission.
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Do not allow Bulk Emails": "Allow"})
    check("bulk 'Allow' is permission", r.allow_bulk_email is True,
          f"got {r.allow_bulk_email}")
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Do not allow Bulk Emails": "Do Not Allow"})
    check("bulk 'Do Not Allow' is denial", r.allow_bulk_email is False,
          f"got {r.allow_bulk_email}")
    # A bare yes/no in the same column reads through the column's polarity.
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Do not allow Bulk Emails": "Yes"})
    check("bulk 'Yes' inverts on a do-not column", r.allow_bulk_email is False,
          f"got {r.allow_bulk_email}")
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Do not allow Bulk Emails": "No"})
    check("bulk 'No' inverts on a do-not column", r.allow_bulk_email is True,
          f"got {r.allow_bulk_email}")
    # Silence is never permission.
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Allow Text Message?": ""})
    check("blank permission cell is not consent", r.allow_sms is None,
          f"got {r.allow_sms}")
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com"})
    check("absent permission column is not consent", r.allow_voice is None,
          f"got {r.allow_voice}")
    # Bulk denial does not deny direct email.
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Allow Emails?": "Allow",
                          "Do not allow Bulk Emails": "Do Not Allow"})
    check("bulk opt-out is not an email opt-out",
          r.allow_email is True and r.allow_bulk_email is False,
          f"email={r.allow_email} bulk={r.allow_bulk_email}")


# ---------------------------------------------------------------------------
# 2. Compliance resolves in ONE direction
# ---------------------------------------------------------------------------

def s2_compliance():
    states = (True, False, None)
    loosened = []
    for ch in sr.DNC_CHANNELS:
        for cur in states:
            for hist in states:
                t = sr.Record(key="t", last_name="x", **{f"allow_{ch}": cur})
                s = sr.Record(key="s", last_name="x", **{f"allow_{ch}": hist})
                out = sr.reconcile_compliance(t, s)
                got = out["channels"][ch]
                if (cur is False or hist is False) and got is not False:
                    loosened.append((ch, cur, hist, got))
    check("a denial on either side always wins", not loosened, str(loosened[:4]))

    # Suppression discovered on the historical side is carried, never dropped.
    t = sr.Record(key="t", last_name="x")
    s = sr.Record(key="s", last_name="x", suppressed=True)
    out = sr.reconcile_compliance(t, s)
    check("historical suppression is carried", out["suppressed"] is True)
    check("historical suppression is reported",
          out["discovered_restriction"] is True)

    # Historical permission cannot revive a locally restricted channel.
    t = sr.Record(key="t", last_name="x", allow_email=False)
    s = sr.Record(key="s", last_name="x", allow_email=True)
    out = sr.reconcile_compliance(t, s)
    check("history cannot reactivate a restricted channel",
          out["channels"]["email"] is False, str(out["channels"]))


# ---------------------------------------------------------------------------
# 3. Enrichment is blank-fill only
# ---------------------------------------------------------------------------

def s3_enrichment():
    t = sr.Record(key="t", first_name="Ann", last_name="Lee",
                  emails=("ann@example.com",), zip_code="75001")
    s = sr.Record(key="s", first_name="Ann", last_name="Lee",
                  emails=("other@example.com",), zip_code="75002",
                  city="Dallas", state="TX")
    out = sr.propose_enrichment(t, s, 96)
    fields = {f["field"] for f in out["fills"]}
    check("populated email is never proposed for fill", "email" not in fields)
    check("populated zip is never proposed for fill", "zip_code" not in fields)
    check("blank city is proposed", "city" in fields)
    conflicts = {c["field"] for c in out["conflicts"]}
    check("disagreeing email is a conflict", "email" in conflicts)
    check("disagreeing zip is a conflict", "zip_code" in conflicts)

    for f in out["fills"]:
        check(f"fill {f['field']} has no current value", f["current"] is None)

    protected = set(sr.PROTECTED_FIELDS)
    check("no protected field is enrichable",
          not (protected & set(sr.ENRICHABLE_FIELDS)),
          str(protected & set(sr.ENRICHABLE_FIELDS)))
    check("no protected field is a conflict field",
          not (protected & set(sr.CONFLICT_FIELDS)))
    for f in ("assigned_to_id", "organization_id", "status", "sms_consent",
              "created_at", "manual_flag", "last_messaged_at"):
        check(f"{f} is protected", f in protected)

    # Below the auto floor nothing may be applied without a human.
    out = sr.propose_enrichment(t, s, 55)
    check("low-confidence fills are not auto-applicable",
          all(not f["auto_applicable"] for f in out["fills"]))
    out = sr.propose_enrichment(t, s, sr.AUTO_CONFIDENCE_FLOOR)
    check("floor-confidence fills are auto-applicable",
          all(f["auto_applicable"] for f in out["fills"]))


# ---------------------------------------------------------------------------
# 4. Matching hierarchy
# ---------------------------------------------------------------------------

def _rec(**kw):
    kw.setdefault("last_name", "Smith")
    return sr.Record(**kw)


def s4_matching():
    a = _rec(key="t", source_key="C1", first_name="Jo", emails=("jo@x.com",),
             phones=("2145551212",))
    b = _rec(key="s", source_key="C1", first_name="Different",
             emails=("nope@y.com",))
    check("source id beats everything", sr.score_pair(a, b) == sr.RULE_SOURCE_ID)

    a = _rec(key="t", first_name="Jo", emails=("jo@x.com",))
    b = _rec(key="s", first_name="Jo", emails=("jo@x.com",))
    check("email + last name is high", sr.score_pair(a, b) == sr.RULE_EMAIL_LAST)

    a = _rec(key="t", first_name="Jo", phones=("2145551212",))
    b = _rec(key="s", first_name="Jo", phones=("12145551212",))
    check("phone normalizes across formats",
          sr.score_pair(a, b) == sr.RULE_PHONE_LAST)

    a = _rec(key="t", last_name="Smith", first_name="Jo", phones=("2145551212",))
    b = _rec(key="s", last_name="Jones", first_name="Pat", phones=("2145551212",))
    rule = sr.score_pair(a, b)
    check("shared phone with disagreeing names is review only",
          rule == sr.RULE_PHONE_ONLY and rule[2] == sr.MATCHED_REVIEW, str(rule))
    check("shared phone alone is below the auto floor",
          sr.RULE_PHONE_ONLY[1] < sr.AUTO_CONFIDENCE_FLOOR)

    a = _rec(key="t", first_name="Jo")
    b = _rec(key="s", first_name="Jo")
    rule = sr.score_pair(a, b)
    check("name alone is review only", rule == sr.RULE_NAME_ONLY)
    check("name alone is below the auto floor",
          sr.RULE_NAME_ONLY[1] < sr.AUTO_CONFIDENCE_FLOOR)

    a = _rec(key="t", first_name="Jo", last_name="Zzz")
    b = _rec(key="s", first_name="Pat", last_name="Qqq")
    check("nothing in common does not match", sr.score_pair(a, b) is None)

    for code, conf, status in sr.MATCH_RULES:
        if status == sr.MATCHED_HIGH_CONFIDENCE:
            check(f"{code} is at or above the auto floor",
                  conf >= sr.AUTO_CONFIDENCE_FLOOR, f"{conf}")
        else:
            check(f"{code} is below the auto floor",
                  conf < sr.AUTO_CONFIDENCE_FLOOR, f"{conf}")

    # Two DIFFERENT people equally matched is an answer, not a coin toss.
    t = _rec(key="t", first_name="Jo", phones=("2145551212",))
    idx = sr.SourceIndex([
        _rec(key="s1", last_name="Smith", first_name="Jo", phones=("2145551212",),
             source_key="A"),
        _rec(key="s2", last_name="Smith", first_name="Jo", phones=("2145551212",),
             source_key="B"),
    ])
    f = sr.match_record(t, idx)
    check("two distinct equal matches is MULTIPLE_MATCHES",
          f["match_status"] == sr.MULTIPLE_MATCHES, f["match_status"])

    # The SAME person twice in the source is a source duplicate, not ambiguity.
    idx = sr.SourceIndex([
        _rec(key="s1", last_name="Smith", first_name="Jo", phones=("2145551212",),
             source_key="A"),
        _rec(key="s2", last_name="Smith", first_name="Jo", phones=("2145551212",),
             source_key="A"),
    ])
    f = sr.match_record(t, idx)
    check("the same source record twice is not ambiguity",
          f["match_status"] == sr.MATCHED_HIGH_CONFIDENCE, f["match_status"])
    check("source duplication is reported",
          "duplicate_rows_in_source" in f["reason_codes"], str(f["reason_codes"]))

    f = sr.match_record(_rec(key="t", first_name="Nobody", last_name="Nowhere"),
                        sr.SourceIndex([]))
    check("empty source is NO_MATCH", f["match_status"] == sr.NO_MATCH)
    check("NO_MATCH carries zero confidence", f["match_confidence"] == 0)


# ---------------------------------------------------------------------------
# 5. Viability ordering
# ---------------------------------------------------------------------------

def s5_viability():
    idx = sr.SourceIndex([])

    t = sr.Record(key="t", last_name="A", emails=("a@x.com",), suppressed=True)
    out = sr.reconcile([t], idx)[0]
    check("suppressed is DO_NOT_CONTACT", out["viability"] == sr.DO_NOT_CONTACT)

    t = sr.Record(key="t", last_name="A", emails=("a@x.com",),
                  allow_email=False, allow_sms=False, allow_voice=False)
    out = sr.reconcile([t], idx)[0]
    check("every channel restricted is DO_NOT_CONTACT",
          out["viability"] == sr.DO_NOT_CONTACT)

    # Bulk-only restriction must NOT read as do-not-contact.
    t = sr.Record(key="t", last_name="A", emails=("a@x.com",),
                  allow_bulk_email=False, status_reason="Contacted")
    out = sr.reconcile([t], idx)[0]
    check("bulk opt-out alone is not DO_NOT_CONTACT",
          out["viability"] != sr.DO_NOT_CONTACT, out["viability"])

    t = sr.Record(key="t", last_name="A", emails=("a@x.com",),
                  status_reason="Non Viable")
    out = sr.reconcile([t], idx)[0]
    check("a non-viable marker is DO_NOT_CONTACT",
          out["viability"] == sr.DO_NOT_CONTACT)

    t = sr.Record(key="t", last_name="A")
    out = sr.reconcile([t], idx)[0]
    check("no email and no phone is BAD_CONTACT_DATA or INSUFFICIENT_DATA",
          out["viability"] in (sr.BAD_CONTACT_DATA, sr.INSUFFICIENT_DATA),
          out["viability"])

    t = sr.Record(key="t", last_name="A", emails=("a@x.com",))
    out = sr.reconcile([t], idx)[0]
    check("reachable with no history at all is INSUFFICIENT_DATA",
          out["viability"] == sr.INSUFFICIENT_DATA, out["viability"])

    t = sr.Record(key="t", last_name="A", emails=("a@x.com",),
                  status_reason="Appointment Set")
    out = sr.reconcile([t], idx)[0]
    check("a real disposition is VIABLE_READY",
          out["viability"] == sr.VIABLE_READY, out["viability"])
    check("the disposition is named in the reasons",
          any("appointment_set" in r for r in out["viability_reasons"]),
          str(out["viability_reasons"]))

    # 'New' is not a disposition. Being in a file is not engagement.
    src = sr.Record(key="s", last_name="A", first_name="B", emails=("a@x.com",),
                    status_reason="New")
    t = sr.Record(key="t", last_name="A", first_name="B", emails=("a@x.com",),
                  status_reason="New")
    out = sr.reconcile([t], sr.SourceIndex([src]))[0]
    check("status 'New' is not engagement evidence",
          out["viability"] == sr.VIABLE_LOWER_PRIORITY, out["viability"])

    # A conflict outranks a good disposition.
    src = sr.Record(key="s", last_name="A", first_name="B",
                    emails=("a@x.com",), zip_code="75002",
                    status_reason="Appointment Set")
    t = sr.Record(key="t", last_name="A", first_name="B",
                  emails=("a@x.com",), zip_code="75001")
    out = sr.reconcile([t], sr.SourceIndex([src]))[0]
    check("a conflict forces REVIEW_REQUIRED",
          out["viability"] == sr.REVIEW_REQUIRED, out["viability"])


# ---------------------------------------------------------------------------
# 6. A date is not an action
# ---------------------------------------------------------------------------

def s6_dates():
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Last Logged Activity": datetime(2023, 9, 18, 14, 2),
                          "Last Action": "Called: LM/No Answer"})
    check("a timestamp column never becomes last_action",
          r.last_action == "Called: LM/No Answer", repr(r.last_action))
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Last Logged Activity": datetime(2023, 9, 18, 14, 2)})
    check("a timestamp alone leaves last_action empty",
          r.last_action == "", repr(r.last_action))
    check("a timestamp column is read as a date",
          isinstance(r.last_contact_date, datetime), repr(r.last_contact_date))

    # The most recent of several date columns wins; none is silently preferred.
    r = sa.row_to_record({"Full Name": "A B", "Email": "a@b.com",
                          "Last Activity Date": datetime(2022, 1, 1),
                          "Open Activity Date": datetime(2024, 5, 5)})
    check("the most recent contact date wins",
          r.last_contact_date == datetime(2024, 5, 5), repr(r.last_contact_date))
    # The header that started this: an export writes "Last Activity Date" and
    # the operational importer's exact-match list does not name it.
    check("'Last Activity Date' is recognised",
          "last activity date" in sa.DATE_FIELDS)


# ---------------------------------------------------------------------------
# 7. No tenant, customer or population is written into the engine
# ---------------------------------------------------------------------------

FORBIDDEN = ("restland", "jason", "mcclellan", "evosys", "bookaboost",
             "harmony hustle", "nsmg", "tisdale", "berthet")

def s7_no_hardcoding():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for mod in ("app/services/source_reconciliation.py",
                "app/services/source_adapters.py",
                "scripts/reconcile_offline.py"):
        code = _strip_py(os.path.join(root, mod)).lower()
        for word in FORBIDDEN:
            check(f"{mod} does not name '{word}'", word not in code)
        # Population sizes are facts about one run, never constants in an engine.
        for n in ("93434", "93,434", "10576"):
            check(f"{mod} does not hardcode the count {n}", n not in code)
        # Positive control: the scan can find something that IS there.
    code = _strip_py(os.path.join(root, "app/services/source_reconciliation.py"))
    check("positive control - the scan reads real source",
          "MATCHED_HIGH_CONFIDENCE" in code)


# ---------------------------------------------------------------------------
# 8. Read-only by construction
# ---------------------------------------------------------------------------

def s8_read_only():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for mod in ("app/services/source_reconciliation.py",
                "app/services/source_adapters.py"):
        code = _strip_py(os.path.join(root, mod))
        for banned in ("db.commit", "db.add", "session.commit", "send_email",
                       "send_sms", "requests.post", "httpx.post"):
            check(f"{mod} contains no {banned}", banned not in code)

    v = sr.vocabulary()
    for k in ("match_statuses", "viability_classes", "match_rules",
              "auto_confidence_floor", "enrichable_fields", "protected_fields",
              "channels", "dnc_channels"):
        check(f"vocabulary publishes {k}", k in v)
    check("bulk_email is not a do-not-contact channel",
          "bulk_email" not in sr.DNC_CHANNELS)
    check("every DNC channel is a channel",
          set(sr.DNC_CHANNELS) <= set(sr.CHANNELS))


SECTIONS = (s1_polarity, s2_compliance, s3_enrichment, s4_matching,
            s5_viability, s6_dates, s7_no_hardcoding, s8_read_only)

REVERTS = ("R1_polarity_ignores_value", "R2_compliance_can_loosen",
           "R3_enrichment_overwrites", "R4_weak_match_auto",
           "R5_multiple_collapses", "R6_date_column_as_action")


def run_sections() -> int:
    for fn in SECTIONS:
        try:
            fn()
        except Exception as exc:  # a section that explodes is a failure
            check(f"section {fn.__name__} completed", False,
                  f"{type(exc).__name__}: {exc}")
    return FAIL


def main() -> int:
    run_sections()
    if CHILD:
        print(f"[revert {REVERT}] pass={PASS} fail={FAIL}")
        return 1 if FAIL else 0

    print(f"GATE 36 - SOURCE RECONCILIATION: {PASS} checks, {FAIL} failed")
    for f in FAILURES:
        print("  FAIL:", f)
    if FAIL:
        return 1

    print("\nrevert proofs (each must FAIL the gate):")
    bad = 0
    for r in REVERTS:
        env = dict(os.environ, RECON_GATE_REVERT=r)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
