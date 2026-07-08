import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'

function fmtPct(value) {
  if (value === null || value === undefined) return '–'
  // _safe_rate on the backend already returns a percentage (e.g. 66.7), NOT a 0-1 decimal.
  // Do NOT multiply by 100 again here.
  const n = Number(value)
  return `${n % 1 === 0 ? n : n.toFixed(1)}%`
}

export default function Reports() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/admin/dashboard/metrics')
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Reports</h1>
          <p className="page-subtitle">Performance snapshots across your team and leads.</p>
        </div>
      </header>

      {loading ? (
        <div className="empty-state">Loading reports...</div>
      ) : !data ? (
        <div className="empty-state">No report data available yet.</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14, marginBottom: 20 }}>
            {[
              { label: 'Total leads',    value: data.totals?.leads_owned ?? '–' },
              { label: 'Messages sent',  value: data.totals?.messages_sent ?? '–' },
              { label: 'Replies received', value: data.totals?.replies ?? '–' },
              { label: 'Reply rate',     value: fmtPct(data.totals?.reply_rate) },
              { label: 'Hot reply rate', value: fmtPct(data.totals?.hot_reply_rate) },
              { label: 'Booking rate',   value: fmtPct(data.totals?.booking_rate) },
            ].map(stat => (
              <div key={stat.label} className="panel" style={{ padding: '18px 20px' }}>
                <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)', fontWeight: 700 }}>
                  {stat.label}
                </span>
                <strong style={{ display: 'block', fontSize: 28, color: 'var(--text-primary)', marginTop: 6, letterSpacing: '-0.02em' }}>
                  {stat.value}
                </strong>
              </div>
            ))}
          </div>

          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Advisor breakdown</h2>
            </div>
            {!data.advisors || data.advisors.length === 0 ? (
              <div className="empty-state">No advisor data yet.</div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Advisor</th>
                    <th>Leads</th>
                    <th>Sent</th>
                    <th>Replies</th>
                    <th>Reply rate</th>
                    <th>Hot rate</th>
                    <th>Booking rate</th>
                    <th>DNC rate</th>
                  </tr>
                </thead>
                <tbody>
                  {data.advisors.filter(a => a.advisor_id !== 'org_total').map(a => (
                    <tr key={a.advisor_id}>
                      <td>{a.advisor_name}</td>
                      <td className="mono">{a.leads_owned}</td>
                      <td className="mono">{a.messages_sent}</td>
                      <td className="mono">{a.replies}</td>
                      <td className="mono">{fmtPct(a.reply_rate)}</td>
                      <td className="mono">{fmtPct(a.hot_reply_rate)}</td>
                      <td className="mono">{fmtPct(a.booking_rate)}</td>
                      <td className="mono">{fmtPct(a.dnc_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  )
}
