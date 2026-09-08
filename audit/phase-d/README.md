# Phase D capability audit — evidence

Generated against commit `5092028` on 8 Sep 2026. These scripts read the repository and
write the accompanying `.txt` files. Re-run any of them from the repository root
(`python audit/phase-d/audit_systems.py`) to re-derive a claim in
`claude/PLATFORM_CAPABILITY_AUDIT_AND_MASTER_ROADMAP.md`.

They are read-only. None of them writes to the database, calls a provider, or modifies
application code.

| Script | Answers |
|---|---|
| `audit_inventory.py` | How many routers, routes, services, models, pages, tests exist |
| `audit_systems.py` | Per router: every route, its auth dependency, the services and models it imports, which frontend files call it, which tests reference it |
| `audit_reach.py` | Which routes no frontend file calls, matched on literal path segments |
| `audit_p0.py` | Six defect classes: missing auth, cross-tenant reads, destructive operations, secret exposure, payment integrity, executive boundary |
| `audit_orphans.py` | Which services nothing imports, using an AST import graph |
| `audit_frontend.py` | Which pages nothing in `frontend/src` imports |
| `audit_markers.py` | Where the source deliberately says it is unfinished (TODO, POLICY REQUIRED, NotImplementedError, not implemented) |

## Two corrections worth keeping

Both original passes used line regexes, and both were wrong in the same way.

**Services.** A first pass reported `billing_webhook` as orphaned. It is not — `billing_router`
imports it inside a multi-line parenthesised import, which a line regex cannot see.
`audit_orphans.py` parses with `ast` instead and gives the real answer: four dead services,
not five.

**Pages.** A first pass checked only `App.jsx` and reported two orphaned pages. The Sales, God
and Executive shells import their own panels, so `App.jsx` alone is not the whole picture.
`audit_frontend.py` checks every file under `frontend/src` and finds one orphan, not two.

`audit_p0.py` has a known false-positive mode of its own that is left in deliberately: it detects
an auth dependency by matching `Depends(require_*|get_current_*|...)` in the handler signature,
so it cannot see an aliased dependency. `calendar_connections_router.py` sets
`_caller = get_current_user` and every one of its endpoints is gated; the scanner reports all
three as ungated. Read the handler before believing the scanner.
