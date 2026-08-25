/**
 * PlatformHealthStrip — ecosystem totals across every platform.
 * Data: GET /god/stats  (total_platforms, total_orgs, total_users, total_leads)
 *       plus counts derived from GET /god/orgs.
 * Reply rate has no backend source today and renders as "—", not a guess.
 */
import { T, fmt } from './godTheme'

function Cell({ label, value, color, alert, title }) {
  return (
    <div title={title} style={{
      background: alert
        ? 'linear-gradient(90deg,rgba(63,12,24,.58),rgba(29,9,15,.72))'
        : 'rgba(5,14,25,.90)',
      padding: '12px 14px', minWidth: 0,
    }}>
      <span style={{
        display: 'block', fontSize: 8, letterSpacing: '.13em', fontWeight: 800,
        color: alert ? '#c35d74' : '#56718e',
      }}>{label}</span>
      <b style={{
        display: 'block', marginTop: 6, fontSize: 15,
        color: alert ? '#ff8299' : (color || '#eef8ff'),
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{value}</b>
    </div>
  )
}

export default function PlatformHealthStrip({ stats, orgs, criticalCount, loading }) {
  const messages30d = (orgs || []).reduce((a, o) => a + (o.messages_30d || 0), 0)

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit,minmax(112px,1fr))',
      gap: 1, background: 'rgba(74,143,193,.15)',
      border: '1px solid rgba(76,152,206,.20)', borderRadius: 12,
      overflow: 'hidden', marginBottom: 18, boxShadow: '0 14px 34px rgba(0,0,0,.16)',
    }}>
      <Cell label="PLATFORMS" value={loading ? '·' : fmt(stats?.total_platforms)} />
      <Cell label="ORGS"      value={loading ? '·' : fmt(stats?.total_orgs)} />
      <Cell label="USERS"     value={loading ? '·' : fmt(stats?.total_users)} />
      <Cell label="LEADS"     value={loading ? '·' : fmt(stats?.total_leads)} />
      <Cell label="LEADS 30D" value={loading ? '·' : fmt(stats?.new_leads_30d)} color={T.blue} />
      <Cell label="MESSAGES 30D" value={loading ? '·' : fmt(messages30d)}
            title="Sum of messages_30d across all organizations" />
      <Cell label="CRITICAL" value={loading ? '·' : (criticalCount ? `${criticalCount} open` : 'none')}
            alert={!loading && criticalCount > 0} />
    </div>
  )
}
