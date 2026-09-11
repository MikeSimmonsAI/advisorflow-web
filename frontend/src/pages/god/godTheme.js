/**
 * godTheme.js — God Mode design tokens.
 *
 * Source of truth: AdvisorFlow_GOD_MODE_Command_Center_V2.html (approved Aug 25 2026).
 * If the design changes, change it here and nowhere else.
 *
 * GOD MODE IS PERMANENTLY LIGHT (Sep 11 2026). The near-black V2 palette these
 * names used to hold was rejected as a product decision, not softened. There is
 * no dark counterpart to keep in sync any more — `godTokens.css` declares one
 * palette and this file points at it.
 *
 * ───────────────────────────────────────────────────────────────────────────
 * WHY THESE ARE var() REFERENCES AND NOT HEX
 * ───────────────────────────────────────────────────────────────────────────
 *
 * `T` is consumed almost entirely as React INLINE STYLES. An inline style beats
 * every stylesheet rule short of !important, so while these were literal hex
 * values there was no selector anywhere — not `[data-appearance="light"]`, not
 * a scoped override, not a whole second sheet — that could repaint God Mode.
 * The appearance control in the rail flipped the attribute, the tenant app went
 * light, and the control plane stayed near-black. That was the bug.
 *
 * Pointing each name at a custom property moves the decision from this file to
 * `godTokens.css`, where light and dark are both declared, WITHOUT any of the
 * sixteen components that import `T` needing to change. The variables resolve
 * against the nearest ancestor that declares them — `.gm-shell`, `.gm-scope` or
 * `.go-scope` — which is also what keeps the palette from leaking onto a
 * customer workspace.
 *
 * The names and their MEANINGS are unchanged: `T.bg` is still the page ground,
 * `T.teal` is still "healthy". Only the value is now late-bound.
 *
 * Each var() carries the LIGHT literal as its fallback. A component that somehow
 * renders outside the God shell then degrades to the current design rather than
 * to an empty string — an unresolvable var() takes its whole declaration with
 * it, and an element with no background at all is the worse failure. That is
 * not hypothetical here: it is exactly what happened to the Create Customer
 * fields, whose `border: 1px solid var(--go-line)` resolved to no border.
 */

export const T = {
  bg:     'var(--gm-bg, #f7f9fc)',
  panel:  'var(--gm-panel, #ffffff)',
  panel2: 'var(--gm-panel-2, #f4f7fb)',
  panel3: 'var(--gm-panel-3, #e9eff7)',
  line:   'var(--gm-card-line, #e4e9f1)',
  line2:  'var(--gm-card-line-hover, #a8c4e8)',
  blue:   'var(--gm-blue, #1d63d1)',
  blue2:  'var(--gm-blue2, #1a56b8)',
  teal:   'var(--gm-teal, #067a55)',
  amber:  'var(--gm-amber, #8a5d0a)',
  red:    'var(--gm-red, #c0203f)',
  purple: 'var(--gm-purple, #6438bd)',
  gold:   'var(--gm-gold, #7a5a0c)',
  text:   'var(--gm-text, #1f2f45)',
  head:   'var(--gm-head, #0f1b2d)',
  dim:    'var(--gm-dim, #4a5b70)',
  ghost:  'var(--gm-ghost, #55667c)',
  // Ink that sits ON a saturated fill rather than on the page. It contrasts
  // with the ACCENT, not with the ground.
  onAccent: 'var(--gm-ink-on-accent, #ffffff)',
  field:  'var(--gm-field, #ffffff)',
  fieldLine: 'var(--gm-field-line, #7386a0)',
}


/** Mirrors _compute_health_score() in app/routers/god_router.py.
 *  80-100 healthy · 60-79 attention · <60 critical. Keep in sync with the backend. */
export function healthBand(score) {
  if (score === null || score === undefined) return 'unknown'
  if (score >= 80) return 'healthy'
  if (score >= 60) return 'attention'
  return 'critical'
}

export function healthColor(score) {
  const b = healthBand(score)
  return b === 'healthy' ? T.teal : b === 'attention' ? T.amber
       : b === 'critical' ? T.red : T.ghost
}

export function fmt(n) {
  if (n === null || n === undefined) return '—'
  return Number(n).toLocaleString('en-US')
}

export function daysAgo(iso) {
  if (!iso) return null
  const d = (Date.now() - new Date(iso).getTime()) / 86400000
  return d < 0 ? 0 : Math.floor(d)
}

export function lastActivityLabel(iso) {
  const d = daysAgo(iso)
  if (d === null) return 'never'
  if (d === 0) return 'today'
  if (d === 1) return 'yesterday'
  return d + 'd ago'
}
