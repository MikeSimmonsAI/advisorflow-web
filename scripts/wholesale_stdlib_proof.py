"""Dependency-free Wholesale intake proof (SYNTHETIC LOGIC only).

Runs with stdlib only (no pytest/fastapi/db). Loads the pure intake modules by
path so app/__init__ side effects are avoided. Exit 0 = all checks passed.
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app" / "services" / "intake"


def load(name):
    spec = importlib.util.spec_from_file_location(f"_intake_{name}", ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


N = load("normalize")
fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails.append(label)


rows = [
    # Valid NANP numbers: 555-010-xxxx normalizes to None (invalid area code),
    # which made the phone checks below vacuous (None == None).
    {"phone": "(212) 555-0101", "email": "A.Seller@Example.com ", "addr": "12 Oak St.", "name": "Jane  Doe"},
    {"phone": "212-555-0102", "email": "b@example.com", "addr": "9 Elm Ave", "name": "Bob Roe"},
]


def keys(r):
    return (N.phone(r["phone"]), N.email(r["email"]), N.address_key(r["addr"]), N.name_key(r["name"]))


first = [keys(r) for r in rows]
second = [keys(dict(r)) for r in rows]
check("re-import yields identical normalized keys", first == second)
check("distinct rows yield distinct keys", first[0] != first[1])
check("exact re-import adds zero new unique keys", len(set(first + second)) == len(first))
check("fixture phones are real keys (non-vacuous)", all(k[0] for k in first))
check("formatting variants of one phone match",
      N.phone("212.555.0101")[0] is not None and N.phone("212.555.0101")[0] == N.phone("(212) 555-0101")[0])
check("email case/whitespace variants match", N.email(" X@Y.COM ")[0] == N.email("x@y.com")[0])
check("blank phone is not a key", N.phone("")[0] is None)

sys.exit(1 if fails else 0)
