/**
 * ExecutiveCustomerHealth — portfolio-level health for every customer organization.
 *
 * Data source: GET /executive/customer-health (server-side scoped to executive's platform).
 * Read-only. No workspace links.
 *
 * Health classifications:
 *   healthy    — active users + leads + operational activity within 14 days
 *   watch      — activity slowing (15–30 days)
 *   at_risk    — activity 31–60 days ago
 *   inactive   — no operational activity or >60 days ago
 *   onboarding — org under 30 days old and not yet healthy
 */

import { useState, useEffect } from 'react'
import { api } from '../../api/client'

const HEALTH_ORDER = ['healthy', 'watch', 'at_risk', 'inactive', 'onboarding']

const HEALTH_CONFIG = {
  healthy:    { label: 'Healthy',    bg: '#d1fae5', color: '#065f46', dot: '#10b981' },
  watch:      { label: 'Watch',      bg: '#fef3c7', color: '#92400e', dot: '#f59e0b' },
  at_risk:    { label: 'At Risk',    bg: '#ffedd5', color: '#9a3412', dot: '#f97316' },
  inactive:   { label: 'Inactive',   bg: '#fee2e2', color: '#991b1b', dot: '#ef4444' },
  onboarding: { label: 'Onboarding', bg: '#dbeafe', color: '#1e40af', dot: '#3b82f6' },
}

function useCustomerHealth() {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    api.get('/executive/customer-health')
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(() => setState({ loading: false, data: null, error: 'Failed to load customer health data.' }))
  }, [])
  return state
}

function HealthBadge({ health }) {
  const cfg = HEALTH_CONFIG[health] || { label: health, bg: '#f3f4f6', color: '#374151', dot: '#9ca3af' }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      background: cfg.bg, color: cfg.color,
      fontSize: 11, fontWeight: 700, padding: '3px 9px',
      borderRadius: 10, letterSpacing: '0.04em', whiteSpace: 'nowrap',
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: cfg.dot, flexShrink: 0 }} />
      {cfg.label.toUpperCase()}
    </span>
  )
}

function SummaryChip({ label, count, health, active, onClick }) {
  const cfg = HEALTH_CONFIG[health]
  const isAll = !health
  const bg    = active ? (cfg ? cfg.bg : '#e0e7ff')    : '#fff'
  const color = active ? (cfg ? cfg.color : '#3730a3') : '#6b7280'
  const border = active ? '2px solid ' + (cfg ? cfg.dot : '#6366f1') : '1px solid #e5e7eb'
  return (
    <button
      onClick={onClick}
      style={{
        background: bg, color, border,
        borderRadius: 10, padding: '8px 16px',
        fontSize: 13, fontWeight: 700, cursor: 'pointer',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
        minWidth: 80,
      }}
    >
      <span style={{ fontSize: 20, fontWeight: 800 }}>{count}</span>
      <span style={{ fontSize: 11, fontWeight: 600 }}>{label}</span>
    </button>
  )
}

function fmtDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return '—' }
}

function fmtDaysAgo(iso) {
  if (!iso) return '—'
  try {
    const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
    if (d === 0) return 'Today'
    if (d === 1) return 'Yesterday'
    return `${d}d ago`
  } catch { return '—' }
}

const SORT_KEYS = {
  name:    (a, b) => a.name.localeCompare(b.name),
  health:  (a, b) => HEALTH_ORDER.indexOf(a.health) - HEALTH_ORDER.indexOf(b.health),
  last_op: (a, b) => {
    if (!a.last_operational_activity && !b.last_operational_activity) return 0
    if (!a.last_operational_activity) return 1
    if (!b.last_operational_activity) return -1
    return new Date(b.last_operational_activity) - new Date(a.last_operational_activity)
  },
}

export default function ExecutiveCustomerHealth() {
  const { loading, data, error } = useCustomerHealth()
  const [filter, setFilter]     = useState(null)   // null = show all
  const [sort, setSort]         = useState('health')
  const [sortDir, setSortDir]   = useState(1)       // 1 = asc, -1 = desc

  if (loading) return <PageWrap><p style={styles.muted}>Loading customer health…</p></PageWrap>
  if (error || !data) return <PageWrap><p style={styles.error}>{error}</p></PageWrap>

  const orgs = data.organizations || []
  const sum  = data.summary || {}

  const visible = orgs
    .filter(o => !filter || o.health === filter)
    .sort((a, b) => sortDir * SORT_KEYS[sort](a, b))

  function handleSort(key) {
    if (sort === key) setSortDir(d => -d)
    else { setSort(key); setSortDir(1) }
  }

  function sortIndicator(key) {
    if (sort !== key) return ' ↕'
    return sortDir === 1 ? ' ↑' : ' ↓'
  }

  return (
    <PageWrap>
      <h1 style={styles.heading}>Customer Health</h1>
      <p style={styles.sub}>{data.platform_name} — {sum.total} customer organization{sum.total !== 1 ? 's' : ''}</p>

      {/* Summary filter chips */}
      <div style={styles.chipRow}>
        <SummaryChip
          label="All" count={sum.total} health={null}
          active={filter === null}
          onClick={() => setFilter(null)}
        />
        {HEALTH_ORDER.map(h => (
          <SummaryChip
            key={h}
            label={HEALTH_CONFIG[h].label}
            count={sum[h] || 0}
            health={h}
            active={filter === h}
            onClick={() => setFilter(filter === h ? null : h)}
          />
        ))}
      </div>

      {visible.length === 0 ? (
        <p style={styles.empty}>No organizations match the selected filter.</p>
      ) : (
        <div style={styles.tableWrap}>
          <table style={styles.table}>
            <thead>
              <tr>
                <Th onClick={() => handleSort('name')}>
                  Organization{sortIndicator('name')}
                </Th>
                <Th onClick={() => handleSort('health')}>
                  Health{sortIndicator('health')}
                </Th>
                <th style={styles.th}>Reason</th>
                <th style={{ ...styles.th, textAlign: 'right' }}>Users</th>
                <th style={{ ...styles.th, textAlign: 'right' }}>Leads</th>
                <th style={{ ...styles.th, textAlign: 'right' }}>Hot</th>
                <th style={{ ...styles.th, textAlign: 'right' }}>Booked</th>
                <Th onClick={() => handleSort('last_op')} style={{ whiteSpace: 'nowrap' }}>
                  Last Op. Activity{sortIndicator('last_op')}
                </Th>
                <th style={styles.th}>Plan</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(org => (
                <tr key={org.id} style={styles.row}>
                  <td style={styles.td}>
                    <div style={styles.orgName}>{org.name}</div>
                    <div style={styles.orgSub}>Since {fmtDate(org.provisioned_at)}</div>
                  </td>
                  <td style={styles.td}><HealthBadge health={org.health} /></td>
                  <td style={{ ...styles.td, ...styles.reasonCell }}>{org.reason}</td>
                  <td style={{ ...styles.td, textAlign: 'right', fontWeight: 600 }}>{org.active_users}</td>
                  <td style={{ ...styles.td, textAlign: 'right' }}>{org.total_leads}</td>
                  <td style={{ ...styles.td, textAlign: 'right' }}>{org.hot_leads}</td>
                  <td style={{ ...styles.td, textAlign: 'right' }}>{org.booked_count}</td>
                  <td style={{ ...styles.td, whiteSpace: 'nowrap' }}>
                    <div>{fmtDaysAgo(org.last_operational_activity)}</div>
                    {org.last_operational_activity && (
                      <div style={styles.orgSub}>{fmtDate(org.last_operational_activity)}</div>
                    )}
                  </td>
                  <td style={{ ...styles.td, ...styles.mono }}>{org.plan || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Definitions footer */}
      <div style={styles.legend}>
        {HEALTH_ORDER.map(h => {
          const cfg = HEALTH_CONFIG[h]
          return (
            <span key={h} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginRight: 16 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: cfg.dot }} />
              <span style={{ fontWeight: 700, color: cfg.color }}>{cfg.label}</span>
            </span>
          )
        })}
        <span style={{ color: '#9ca3af', marginLeft: 4 }}>
          · Operational activity = outbound message, inbound reply, or booking
        </span>
      </div>
    </PageWrap>
  )
}

function PageWrap({ children }) { return <div style={styles.page}>{children}</div> }

function Th({ children, onClick, style }) {
  return (
    <th
      onClick={onClick}
      style={{ ...styles.th, cursor: onClick ? 'pointer' : 'default', userSelect: 'none', ...style }}
    >
      {children}
    </th>
  )
}

const styles = {
  page:      { padding: '40px', maxWidth: 1100 },
  heading:   { fontSize: 26, fontWeight: 800, color: '#1a1f36', margin: '0 0 4px' },
  sub:       { fontSize: 14, color: '#6b7280', margin: '0 0 24px' },
  chipRow:   { display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 28 },
  empty:     { color: '#9ca3af', fontSize: 14 },
  tableWrap: { overflowX: 'auto', background: '#fff', border: '1px solid #e8ecf4', borderRadius: 12 },
  table:     { width: '100%', borderCollapse: 'collapse' },
  th: {
    textAlign: 'left', padding: '11px 14px', fontSize: 11, fontWeight: 700,
    color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.06em',
    borderBottom: '1px solid #e8ecf4', background: '#f9fafb',
  },
  row:       { borderBottom: '1px solid #f0f2f8' },
  td:        { padding: '13px 14px', fontSize: 13, color: '#1a1f36', verticalAlign: 'top' },
  orgName:   { fontWeight: 600, fontSize: 14 },
  orgSub:    { fontSize: 11, color: '#9ca3af', marginTop: 2 },
  reasonCell: { fontSize: 12, color: '#4b5563', maxWidth: 260, lineHeight: 1.45 },
  mono:      { fontFamily: 'monospace', fontSize: 12, color: '#6b7280' },
  muted:     { color: '#9ca3af', fontSize: 14 },
  error:     { color: '#ef4444', fontSize: 14 },
  legend:    { marginTop: 20, fontSize: 12, color: '#6b7280' },
}
