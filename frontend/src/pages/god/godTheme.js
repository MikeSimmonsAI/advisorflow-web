/**
 * godTheme.js — God Mode design tokens.
 *
 * Source of truth: AdvisorFlow_GOD_MODE_Command_Center_V2.html (approved Aug 25 2026).
 * If the design changes, change it here and nowhere else.
 *
 * The "glow" in this design is NOT box-shadow driven — it comes from saturated
 * hairline borders on near-black fills plus two large radial gradients. Keep that
 * mechanism; piling on shadows breaks the look.
 */

export const T = {
  bg: '#02050a', panel: '#07111f', panel2: '#0a1627', panel3: '#0d1d31',
  line: 'rgba(88,169,225,.20)', line2: 'rgba(99,194,255,.36)',
  blue: '#39bdf8', blue2: '#6fd5ff', teal: '#23efb2', amber: '#ffc75a',
  red: '#ff5d7d', purple: '#a96bff', gold: '#ffd968',
  text: '#dceafb', head: '#f8fbff', dim: '#7c91a8', ghost: '#496078',
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
