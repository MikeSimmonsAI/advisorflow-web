/**
 * StatusBadge / SectionLabel / Dot — shared God Mode primitives.
 * Fill + saturated border + bright text, always three values of the same hue.
 */
import { T } from './godTheme'

const TONES = {
  ok:   { bg: 'var(--gm-pill-teal-bg)', bd: 'var(--gm-teal)', fg: 'var(--gm-teal)' },
  warn: { bg: 'var(--gm-pill-amber-bg)', bd: 'var(--gm-amber)', fg: 'var(--gm-amber)' },
  bad:  { bg: 'var(--gm-pill-red-bg)', bd: 'var(--gm-red)', fg: 'var(--gm-red)' },
  off:  { bg: 'var(--gm-pill-blue-bg)', bd: 'var(--gm-blue)', fg: 'var(--gm-blue)' },
  pend: { bg: 'var(--gm-pill-blue-bg)', bd: 'var(--gm-blue)', fg: 'var(--gm-blue)' },
  gold: { bg: 'var(--gm-pill-amber-bg)', bd: 'var(--gm-amber)', fg: T.gold },
  blue: { bg: 'var(--gm-pill-blue-bg)', bd: 'var(--gm-blue)', fg: 'var(--gm-blue)' },
}

export function StatusBadge({ tone = 'off', children, title }) {
  const c = TONES[tone] || TONES.off
  return (
    <span title={title} style={{
      display: 'inline-block', borderRadius: 999, padding: '3px 9px', fontSize: 9,
      fontWeight: 700, border: `1px solid ${c.bd}`, background: c.bg, color: c.fg,
      whiteSpace: 'nowrap',
    }}>{children}</span>
  )
}

/** Maps an org record from /god/orgs into a single honest account state. */
export function orgStateBadge(org) {
  if (!org.is_active) return <StatusBadge tone="bad">SUSPENDED</StatusBadge>
  if (!org.lead_count && !org.messages_30d && !org.last_activity) {
    return org.plan === 'enterprise'
      ? <StatusBadge tone="bad" title="Enterprise plan with no recorded activity">NEVER USED</StatusBadge>
      : <StatusBadge tone="off">DORMANT</StatusBadge>
  }
  if (org.health_score < 60) return <StatusBadge tone="bad">CRITICAL</StatusBadge>
  if (org.health_score < 80) return <StatusBadge tone="warn">NEEDS ATTENTION</StatusBadge>
  if (org.plan === 'trial') return <StatusBadge tone="warn">TRIAL</StatusBadge>
  return <StatusBadge tone="ok">ACTIVE</StatusBadge>
}

export function SectionLabel({ children, note }) {
  return (
    <p style={{
      fontSize: 10, color: 'var(--gm-blue)', letterSpacing: '.16em', fontWeight: 800,
      margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 10,
    }}>
      {children}
      {note && <span style={{ color: T.dim, letterSpacing: 0, fontWeight: 400, fontSize: 10 }}>{note}</span>}
    </p>
  )
}

export function Dot({ color = T.blue, glow = false }) {
  return <span style={{
    width: 7, height: 7, borderRadius: '50%', background: color, flex: 'none',
    display: 'inline-block', boxShadow: glow ? `0 0 10px ${color}` : 'none',
  }} />
}

/** Grey italic marker for a figure nothing can produce. Never fake a number.
 *
 * THE DEFAULT WORDING IS THE OWNER'S, NOT OURS. This used to read "no source",
 * which is accurate about our data model and meaningless to the person who
 * owns the business — it does not say whether the gap is in his setup or in
 * our code. "not tracked yet" says the same true thing about his business.
 *
 * Callers should still pass their own words where they can be more specific;
 * this is the fallback, not the preferred answer.
 */
export function NoSource({ children = 'not tracked yet' }) {
  return <span style={{ color: T.ghost, fontStyle: 'italic' }}>{children}</span>
}
