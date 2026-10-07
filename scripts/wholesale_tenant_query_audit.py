"""Static tenant-predicate audit for Wholesale ORM queries (STATIC SOURCE).

For every `db.query(Wholesale<Model>...)` chain in the Wholesale services and
routers, reports those whose full chain carries no `organization_id` / `org_id`
predicate inline. Parses with `ast`; no app imports, no DB.

A chain without an inline predicate is not automatically a leak (the filter may
be added in a later statement, or the row may be a child reached through an
org-checked parent), so each finding is reviewed and either fixed or recorded
in REVIEWED below with the reason. Any NEW unreviewed finding fails the run.

Run: python3 scripts/wholesale_tenant_query_audit.py [-v]
"""
import ast
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FILES = []
for sub in ("app/services", "app/routers"):
    d = os.path.join(ROOT, sub)
    for f in sorted(os.listdir(d)):
        if f.startswith("wholesale") and f.endswith(".py"):
            FILES.append(os.path.join(d, f))

# (file basename, enclosing function) -> reason it is safe. Filled in after review.
REVIEWED = {
    ("wholesale_publication.py", "buyer_room_payload"):
        "deal resolved from an unguessable share token; property read by that deal's property_id",
    ("wholesale_publication.py", "seller_room_payload"):
        "same: the token-resolved deal's own property",
    ("wholesale_seller_intake.py", "resolve_destination"):
        "the public intake key (24-64 char secret) IS the tenant selector; org derived from the row",
    ("wholesale_service.py", "attach_seller"):
        "filtered by property_id of an org-scoped property plus lead_id of an org-checked lead",
    ("wholesale_service.py", "recalculate_analysis"):
        "property of an org-scoped deal passed in by the caller",
    ("wholesale_service.py", "queue_buyer_outreach"):
        "property of an org-scoped deal passed in by the caller",
    ("wholesale_sms.py", "is_program_lead"):
        "lookup by globally unique lead id; a false positive only BLOCKS a send (fail-closed)",
    ("wholesale_buyers_router.py", "preview_disposition"):
        "property of the deal fetched by svc.get_deal(org_id)",
    ("wholesale_rooms_router.py", "_resolve"):
        "public token lookup; token is the credential, audience pinned, uniform refusal",
    ("wholesale_rooms_router.py", "buyer_room"):
        "outreach id taken from the token-resolved link row",
    ("wholesale_rooms_router.py", "buyer_action"):
        "outreach id taken from the token-resolved link row",
    ("wholesale_rooms_router.py", "share_activity"):
        "links filtered by the org-scoped deal id fetched with org_id",
    ("wholesale_router.py", "deal_room"):
        "property/profile ids come from the org-scoped deal row",
    ("wholesale_router.py", "list_deals"):
        "property/profile ids come from rows of an org-filtered deal query",
}


def top_chain(node, parents):
    while id(node) in parents and isinstance(parents[id(node)], (ast.Attribute, ast.Call)):
        node = parents[id(node)]
    return node


total = 0
findings = []
for path in FILES:
    with open(path, encoding="utf-8-sig") as fh:
        tree = ast.parse(fh.read())
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[id(c)] = n
    fn_of = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                fn_of[id(n)] = fn.name
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "query" and n.args):
            first = ast.unparse(n.args[0])
            if "Wholesale" not in first:
                continue
            text = ast.unparse(top_chain(n, parents))
            total += 1
            if "organization_id" not in text and "org_id" not in text:
                key = (os.path.basename(path), fn_of.get(id(n), "<module>"))
                if key not in REVIEWED:
                    findings.append((key, n.lineno, first))

for key, ln, first in findings:
    print("NO inline org predicate: %s::%s line %d  query(%s)" % (key[0], key[1], ln, first))
print("tenant query audit: %d queries, %d unreviewed without an inline org predicate"
      % (total, len(findings)))
sys.exit(1 if findings else 0)
