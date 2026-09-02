/**
 * ExecutiveCommandCenter — brand KPI dashboard.
 *
 * Data source: GET /executive/command-center (server-side scoped to executive's platform).
 * No customer workspace data. No owner controls. No cross-brand data.
 */

import { useState, useEffect } from 'react'
import { api } from '../../api/client'

function useCCData() {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    api.get('/executive/command-center')
      .then(r => setState({ loading: false, data: r.data, error: null }))
      .catch(() => setState({ loading: false, data: null, error: 'Failed to load KPI data.' }))
  }, [])
  return state
}

function StatCard({ label, value, sub }) {
  return (
    <div style={card.wrap}>
      <span style={card.label}>{label}</span>
      <span style={card.value}>{value}</span>
      {sub && <span style={card.sub}>{sub}</span>}
    </div>
  )
}

const card = {
  wrap: {
    background: '#fff', border: '1px solid #e8ecf4', borderRadius: 12,
    padding: '24px 28px', display: 'flex', flexDirection: 'column', gap: 6, minWidth: 180,
  },
  label: { fontSize: 12, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.06em' },
  value: { fontSize: 32, fontWeight: 800, color: '#1a1f36', lineHeight: 1 },
  sub: { fontSize: 12, color: '#9ca3af', marginTop: 2 },
}

function fmt$$(n) {
  if (n >= 1_000_000) return '$' + (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return '$' + (n / 1_000).toFixed(0) + 'K'
  return '$' + n.toFixed(0)
}

export default function ExecutiveCommandCenter() {
  const { loading, data, error } = useCCData()

  if (loading) return <PageWrap><p style={styles.muted}>Loading…</p></PageWrap>
  if (error || !data) return <PageWrap><p style={styles.error}>{error}</p></PageWrap>

  const opp = data.opportunities || {}
  const winRate = opp.total ? Math.round((opp.won / opp.total) * 100) : 0

  return (
    <PageWrap>
      <h1 style={styles.heading}>Command Center</h1>
      <p style={styles.sub}>{data.platform_name} — executive overview</p>
      <div style={styles.grid}>
        <StatCard label="Active Customers" value={data.active_customer_orgs} />
        <StatCard label="Pipeline Value" value={fmt$$(opp.pipeline_value || 0)}
          sub={`${opp.total || 0} total opportunities`} />
        <StatCard label="Won Value" value={fmt$$(opp.won_value || 0)}
          sub={`${opp.won || 0} won`} />
        <StatCard label="Win Rate" value={winRate + '%'} />
        <StatCard label="Team Size" value={data.team_headcount}
          sub={`across ${data.brand_sales_org_count} sales org${data.brand_sales_org_count !== 1 ? 's' : ''}`} />
      </div>
    </PageWrap>
  )
}

function PageWrap({ children }) {
  return <div style={styles.page}>{children}</div>
}

const styles = {
  page: { padding: '40px', maxWidth: 900 },
  heading: { fontSize: 26, fontWeight: 800, color: '#1a1f36', margin: '0 0 4px' },
  sub: { fontSize: 14, color: '#6b7280', margin: '0 0 32px' },
  grid: { display: 'flex', flexWrap: 'wrap', gap: 16 },
  muted: { color: '#9ca3af', fontSize: 14 },
  error: { color: '#ef4444', fontSize: 14 },
}
