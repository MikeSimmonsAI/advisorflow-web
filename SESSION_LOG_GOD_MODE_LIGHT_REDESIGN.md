# GOD MODE — PERMANENT LIGHT CONTROL PLANE

**Branch** `fix/god-mode-visibility` · **Worktree** `C:\Dev\advisorflow-god-visibility`
**Merged to main** `b72c3e2`, `ffe444e` · **Deployed and verified in production** Sep 11 2026

---

## THE DECISION

God Mode is **permanently light**. The near-black control plane is rejected as a
product decision, not softened, not toggled. There is no dark variant in the God
token sheet, no `[data-appearance]` rule inside it, and the appearance control
has been removed from the God rail.

The control remains in **tenant Settings**, where it still means something. A
person who runs the tenant app dark keeps it. That case is live in production
right now and is the isolation proof below.

This applies to the **AdvisorFlow God Mode / root control plane only**. Customer
workspaces stay white-label and configuration-driven.

---

## ROOT CAUSE — IT WAS NEVER JUST "THE THEME IS DARK"

God Mode had **three colour vocabularies** and roughly **890 loose literals**.

| Vocabulary | Declared where | What actually happened |
|---|---|---|
| `--gm-*` | inside `GodStyles.jsx`, six names | dark values, unreachable from any stylesheet |
| `--go-*` | `GodOps.css`, on `.go-scope` **only** | **six screens use `go-` classes and never render that class** |
| `--god-*` | **nowhere** — 105 references across ten screens | every one fell through to a hard-coded **light** fallback |

Two consequences that a colour-only reading of the problem would have missed:

**An unresolvable `var()` does not fall back — it invalidates its whole
declaration.** `border: 1px solid var(--go-line)` on a page with no `.go-scope`
ancestor is not a dark border. It is **no border at all**. That is why the Create
Customer fields had no visible boundary.

**The `--god-*` fallbacks were light.** Launches, Maintenance Ops, the Roadmap
board and every drilldown table were rendering white cards with grey-on-grey text
*inside* the black shell. "Tables reverting dark" and "tables reverting light"
were the same bug seen from two sides.

**Create Customer had a second, separate defect.** It renders ten class names
that exist in no stylesheet in the repository — `go-wrap`, `go-card`, `go-h1`,
`go-h2`, `go-sub`, `go-err`, `go-pill`, `go-live`, `go-btn-primary`,
`go-check--off`. It was unstyled markup on a black page, not a dark form.

And the reason the existing toggle could never have fixed any of it: ~890 colour
values were written as **React inline styles**, which beat every stylesheet rule
short of `!important`. The toggle flipped `data-appearance`, the tenant app went
light, and the control plane stayed black.

---

## THE SHARED SYSTEM

**`frontend/src/pages/god/godTokens.css`** — new, and the only file in God Mode
that holds a colour value. One palette declared on `.gm-shell`, `.gm-scope`,
`.go-scope`, with `--go-*` and `--god-*` **aliased onto it** so all three
vocabularies resolve against one source. `color-scheme: light` is pinned here,
which is what stops a black `<select>` list opening over a white God form for
anyone whose tenant app is dark.

**`godTheme.js`** — `T` now points at custom properties instead of holding hex,
which made sixteen components' inline styles themeable without touching them.

**`GodStyles.jsx`** — rebuilt on tokens: white surfaces, 12px radius, soft
directional shadow, navy type, premium blue primary, restrained gold for God
actions. Nav rail, cards, tables, pills, buttons, inputs, menus, metrics,
filters, focus and disabled states all defined once here.

**`GodOps.css`** — tokens throughout, the ten missing classes defined, and the
`.god-*` drilldown block taken off undeclared names.

**`GodShell.jsx`** — white rail with the identity block, section headings, a
selected-nav pill carried by fill *and* rule *and* weight, the God Mode card in
the footer, and the authenticated God Admin identity in the header.

**40 God components** — every colour literal replaced by a token, by scripted
pass with an explicit rule set, then hand-corrected where the rules could not
know the intent.

---

## ORGANIZATIONS — THE REFERENCE IMPLEMENTATION

Built to the approved mockup: page head with gold **Create Organization** and
**Refresh**, five summary tiles, a filter bar carrying search + state pills +
grouping + count, and the grouped brand table with package, health, billing,
onboarding and state.

**Row actions consolidated.** Each row carried up to seven equally weighted
buttons, which is why the one that matters — Enter — was unfindable in a table
forty rows long. There is now one filled blue **Enter** and an overflow menu.
Nothing was removed: every action is in the menu, each still hidden when the row
has no target for it, and each still authorized by the server.

**The summary tiles are counts of the same authoritative rows the table renders.**
Health comes from the backend's `_compute_health_score`. Rows it has never scored
are **excluded from the attention count and reported beside it** — "1 not yet
scored" — and if nothing is scored the tile reads `—`, not `0`. Unknown is not
zero.

---

## NOT BUILT, ON PURPOSE

Two elements of the mockup have no authoritative source and were **not faked**:

- **Global search** ("organizations, customers, or anything…") — there is no
  cross-entity search endpoint. A box that searches nothing under that promise is
  worse than no box. Every screen keeps its own real search.
- **Notification bell** — the only notifications API in the product is
  tenant-scoped (`GET /notifications/`). Rendering it in the platform header
  would show the god account's own tenant notifications wearing a platform badge.

Both are follow-ups needing backend work, not CSS.

---

## TWO DELIBERATE DEPARTURES FROM THE MOCKUP

1. The gold **Create Organization** fill is a step deeper (`#8f6a10`). White on
   the mockup's gold measures about **2.6:1** — the most important button on the
   screen would have shipped below the floor for readable text, which is the
   exact class of problem this work exists to end.
2. The primary blue is `#1d63d1`, one shade down, so white on it clears 4.5:1.

Both are within a shade and neither changes the design language.

---

## ACCESSIBILITY

`frontend/scripts/god_contrast_check.py` reads the values **out of the shipped
sheet** and fails if any text token is below 4.5:1 on a surface it can land on,
or any control boundary below 3:1. **63 pairs, 0 below target.**

- Focus is a 2px ring in the accent, on every God surface, never invisible.
- Disabled controls are a flat grey surface with **readable** grey ink — dimmed,
  never faded out, because the operator still has to read what they cannot do.
- No critical state is carried by colour alone: badges carry the word, the
  selected filter carries `aria-pressed`, severity carries its label, the
  required marker carries an asterisk and an accessible "(required)".
- The row overflow menu is a real `<button>` list: keyboard reachable, Escape
  closes, named per organization rather than forty identical "…".

---

## CUSTOMER BOUNDARY — MEASURED IN PRODUCTION

Read live from `app.evosyspro.live/god/organizations`:

```
document root : data-appearance="dark"  color-scheme: dark
                --bg-base      #060b18      (tenant, still dark)
                --text-primary #f2f7ff
                --signal-blue  #087cff      (EvoSys Pro brand accent, intact)
                --gm-bg, --go-bg, --god-card, --gm-head  →  ALL EMPTY
.gm-shell     : --gm-bg #f7f9fc   --god-card #ffffff   color-scheme: light
                background rgb(247, 249, 252)
```

The owner's tenant preference is **dark** and untouched. No God token exists on
`:root`. God Mode is light regardless. That is the boundary, demonstrated rather
than asserted.

---

## TESTS

`frontend/tests/godTheme.test.mjs` — 12 checks guarding the four things that were
untrue before: God Mode is light and reads no appearance preference; every token
a God file uses is declared; no colour literals in God components; every selector
stays anchored inside the God scope.

**It found two real defects while being written**: an undeclared
`--gm-pill-amber-*` family that would have shipped badges with **no fill and no
border**, and a token regex that read only the first declaration per line.

A later sweep added a thirteenth check — **no God file may read a tenant
token** — after `/god/workspaces` turned out to be painting from `--bg-panel`,
`--text-primary` and `--border-subtle`. That screen contained **no colour
literal at all**; every value was already a variable, it was just the wrong one,
so the literal check passed on it the whole time.

| Gate | Result |
|---|---|
| `godTheme.test.mjs` | 13 / 13 |
| `workspaceGuard.test.mjs` | 46 checks, 27,648 lifecycles |
| Contrast gate | 63 pairs, 0 below target |
| Frontend build | clean |
| Backend regression | 3,373 passed · 14 skipped · 1 realigned (below) |

**The one backend failure was a test asserting the rejected design.**
`test_context_boundaries.py` required the light/dark control to be present in
the God shell. That was correct until this week. It inverts for the God shell,
is unchanged everywhere else, and a second check was added beside it: God Mode
must not consult the appearance preference at all.

---

## PRODUCTION VERIFICATION

Walked live in the browser after deploy, signed in as god_admin:

Command Center · Platform · Organizations · Customers · Create Customer ·
Workspaces · Users & Identity · Manage Access · Demo Suite · Training ·
Implementations · Customer Launches · Billing & Revenue · AI Workforce ·
AI Operations · AI Workforce Deployment — **all light, all readable, no
remaining black or near-black surface.**

Also verified live: the row overflow menu, the Enter-organization confirm dialog,
and the mobile layout at 375px (rail becomes a drawer, tiles stack, primary
actions stay visible, nothing hidden).

### Found by walking production, not by a test

- **All three modal backdrops were wrong.** The scripted pass read them as colour
  rather than function: two became a pale blue wash and the Maintenance Ops
  confirm became an **opaque white sheet**. Nothing looked modal. All three take
  the scrim token now.
- **The Enter-organization dialog had two identical buttons.** It asked a question
  and offered two answers of the same weight, on a control that assumes a
  customer's identity. One filled primary in the action's tone now, Escape
  cancels, focus lands on confirm.
- **Create Customer's Cancel was the loudest thing on the form** — `.go-btn` *is*
  the filled blue in that sheet, so the way out was the primary while the submit,
  correctly disabled on an empty form, read as the quiet one. Same failure Mike
  reported, inverted. Fixed, plus required markers and a disabled submit that
  says which field is missing.
- The secondary button outline used the hairline **divider** token, so a white
  button on a white card had no edge. It uses the control-boundary token now.
- The organization column had no width floor and wrapped long names to three
  lines. It has one; the table scrolls sideways instead.

### Then measured, rather than looked at

A script walked **36 God routes** and read the computed background of every
element inside the shell. Four more things were dark, and two of them were never
in a God file:

- **Two unscoped rules in `index.css`** — `select { color-scheme: dark }` and
  `select option { background: var(--bg-panel) }`. A bare type selector beats
  inheritance, so `color-scheme: light` on `.gm-shell` never reached the selects
  inside it; and `--bg-panel` is a tenant token God Mode deliberately does not
  declare, so **nine God screens opened a near-black dropdown over a light
  page**. No amount of tokenising God Mode's own files could have fixed this.
  `.gm-shell select` and `.gm-shell option` out-rank a bare type and settle it.
- **The Roadmap board kept a private dark palette** — its own
  `[data-appearance="dark"]` block, a `prefers-color-scheme` fallback, *and* a
  `useDark()` helper picking the dark half of every badge. All three were firing
  and putting #131c2b cards on the **Command Center**.
- **The Compensation Command Center rendered as a 36-element dark island** — it
  is the Sales Workspace embedded at `/god/compensation`, keyed off the same
  tenant attribute. Its dark rules are now excluded inside `.gm-shell`;
  standalone at `/sales/*` it is unchanged.
- **`/god/workspaces` painted from the tenant palette** — see the test note
  above.

**Final sweep: 36 routes, 14,428 elements measured, 0 dark surfaces.** The only
two elements the threshold still flags are a purple progress fill at 0% width
and a red status dot — both empty, both intentional solid accents, both above
3:1 against the page.

---

## FOLLOW-UPS

1. Cross-entity search endpoint, to make the mockup's global search real.
2. A God-scoped notification source, to make the header bell real.
3. `index.css` still carries the two unscoped `select` rules. God Mode now
   out-ranks them, but they reach every surface in the product and should be
   scoped at source rather than out-specified from three places.
4. The Sales Workspace's dark branch is excluded inside the God shell by
   selector. If more surfaces get embedded in the control plane, that exclusion
   wants to become one rule they all share rather than a `:not()` per sheet.
