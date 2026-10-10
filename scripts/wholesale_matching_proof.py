"""Dependency-free exact re-import dedupe proof (SYNTHETIC LOGIC only).

Goes beyond normalized-key equality: drives the real intake matching rules
(app/services/intake/matching.py compare / within_batch / best_match) with
sqlalchemy and the ORM models stubbed out, so it runs on stdlib only.
Synthetic data only. Exit 0 = all checks passed.
"""
import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app" / "services" / "intake"


class MatchType:
    EXACT = "exact"
    POSSIBLE = "possible"
    NEW = "new"


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# matching.py needs these at import time; the pure functions never use them.
_stub("sqlalchemy")
_stub("sqlalchemy.orm", Session=object)
_stub("app")
_stub("app.models")
_stub("app.models.intake_models", MatchType=MatchType, OrgContact=object)
_stub("app.services")
_stub("app.services.intake")


def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


N = load("normalize", "app.services.intake.normalize")
sys.modules["app.services.intake"].normalize = N
M = load("matching", "_intake_matching")

fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails.append(label)


def ident(first, last, email=None, phone=None, company=None, addr=None, zipc=None,
          src=None, order=0):
    return {
        "src_system": "csv" if src else "", "src_id": src,
        "email": N.email(email)[0] if email else None,
        "phone": N.phone(phone)[0] if phone else None, "mobile": None,
        "first_key": N.name_key(first), "last_key": N.name_key(last),
        "company_key": N.company_key(company) if company else None,
        "addr_key": N.address_key(addr) if addr else None,
        "zip5": N.zip5(zipc) if zipc else None, "order": order,
    }


def idx(rows):
    i = M._Index()
    for r in rows:
        i.add(r)
    return i


# --- exact re-import against records the org already has -------------------
raw = [
    # Valid NANP numbers on purpose: 555-010-xxxx has an invalid area code and
    # normalizes to None, which would make every phone comparison vacuous.
    ("Jane", "Doe", "jane@example.com", "(212) 555-0101"),
    ("Bob", "Roe", "bob@example.com", "212-555-0102"),
    ("Cy", "Poe", None, "212 555 0103"),
]
check("fixture phones normalize to real keys (non-vacuous)",
      all(N.phone(p)[0] for _, _, _, p in raw))
existing = idx([ident(f, l, e, p) for f, l, e, p in raw])
# Second import: same people, formatting drift (case, spacing, punctuation).
reimport = [ident(f.upper(), l.lower(), (e or "").upper() or None,
                  p.replace("-", ".").replace("(", "").replace(")", ""))
            for f, l, e, p in raw]
res = [M.match_existing(r, existing)[0] for r in reimport]
check("exact re-import: every row matches an existing record EXACT", res == [MatchType.EXACT] * 3)
check("exact re-import: zero rows would be created as NEW", MatchType.NEW not in res)

# --- a genuinely new person is NEW, not swallowed --------------------------
new_row = ident("Dee", "Fox", "dee@example.com", "212-555-0199")
check("new person is NEW", M.match_existing(new_row, existing)[0] == MatchType.NEW)

# --- same email, different surname: never auto-merged ----------------------
clash = ident("Jane", "Smith", "jane@example.com")
check("shared email, different surname -> POSSIBLE (review), not EXACT",
      M.match_existing(clash, existing)[0] == MatchType.POSSIBLE)

# --- shared phone, different surname: shared line, not a duplicate ---------
spouse = ident("Rick", "Doe-Jones", None, "(212) 555-0101")
mt, _, _, notes = M.match_existing(spouse, existing)
check("shared phone, different surname -> NEW (shared line)", mt == MatchType.NEW)
check("shared line is annotated", "shares_phone_with_other_record" in notes)

# --- phone only, no names on either side: unconfirmed -> POSSIBLE ----------
check("phone-only match with no identity -> POSSIBLE",
      M.match_existing(ident(None, None, None, "212-555-0101"), existing)[0] == MatchType.POSSIBLE)

# --- source record id wins and is exact ------------------------------------
src_existing = idx([ident("Al", "Kim", "al@example.com", src="R-1")])
check("same source record id -> EXACT even if other fields drift",
      M.match_existing(ident("Alan", "Kim", "x@example.com", src="R-1"), src_existing)[0] == MatchType.EXACT)
other_sys = ident("Zed", "Qua", src="R-1")
other_sys["src_system"] = "other"
check("same record id from a different source system is not EXACT by id",
      M.match_existing(other_sys, src_existing)[0] != MatchType.EXACT)

# --- within-file duplicates: first occurrence survives ---------------------
file_rows = [ident("Jane", "Doe", "jane@example.com", order=0),
             ident("Bob", "Roe", "bob@example.com", order=1),
             ident("JANE", "DOE", "Jane@Example.com", order=2)]
M.within_batch(file_rows)
check("within-file: first occurrence is NEW", file_rows[0]["dup_type"] == MatchType.NEW)
check("within-file: unrelated row is NEW", file_rows[1]["dup_type"] == MatchType.NEW)
check("within-file: exact repeat flagged EXACT of row 0",
      file_rows[2]["dup_type"] == MatchType.EXACT and file_rows[2]["dup_of"] == 0)

# --- colleagues at one company are not duplicates --------------------------
colleagues = [ident("Ann", "Lee", company="Acme LLC", addr="1 Main St", order=0),
              ident("Ben", "Ray", company="Acme LLC", addr="1 Main St", order=1)]
M.within_batch(colleagues)
check("same company, different named people -> NEW", colleagues[1]["dup_type"] == MatchType.NEW)

# --- idempotency: matching twice is stable ---------------------------------
check("matching is deterministic across repeated runs",
      [M.match_existing(r, existing)[0] for r in reimport] == res)

sys.exit(1 if fails else 0)
