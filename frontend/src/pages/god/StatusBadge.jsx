/**
 * StatusBadge / SectionLabel / Dot — shared God Mode primitives.
 * Fill + saturated border + bright text, always three values of the same hue.
 */
import { T } from './godTheme'

const TONES = {
  ok:   { bg: '#0a2b22', bd: '#176f58', fg: '#44efbd' },
  warn: { bg: '#251e08', bd: '#70591d', fg: '#f4c652' },
  bad:  { bg: '#2a1017', bd: '#723142', fg: '#ff829b' },
  off:  { bg: '#0c1727', bd: '#2a3f57', fg: '#5d7697' },
  pend: { bg: '#0a1220', bd: '#243a52', fg: '#4a637f' },
  gold: { bg: '#201a08', bd: '#62501b', fg: T.gold },
  blue: { bg: '#0b1a2a', bd: '#1b3c59', fg: '#83a9cc' },
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
      fontSize: 10, color: '#7d9dbc', letterSpacing: '.16em', fontWeight: 800,
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

/** Grey italic marker for anything with no backing data source. Never fake a number. */
export function NoSource({ children = 'no source' }) {
  return <span style={{ color: T.ghost, fontStyle: 'italic' }}>{children}</span>
}
