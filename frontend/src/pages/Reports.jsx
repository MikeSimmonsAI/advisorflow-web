import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Reports.css'

function pct(value) {
  if (value === null || value === undefined) return '0%'
  const n = Number(value)
  return `${n % 1 === 0 ? n : n.toFixed(1)}%`
}

function RateBar({ value, max = 100, color = 'var(--signal-blue)' }) {
  const w = Math.min((Number(value) / max) * 100, 100)
  return (
    <div className="rpt-bar-track">
      <div className="rpt-bar-fill" style={{ width: `${w}%`, background: color }} />
    </div>
  )
}

export default function Reports() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/admin/dashboard/metrics')
      .then(setData)
      .catch(() => setError('Could not load report data.'))
      .finally(() => setLoading(false))
  }, [])

  const totals = data?.totals || {}
  const advisors = (data?.advisors || []).filter((a) => a.advisor_id !== 'org_total')

  const maxBookingRate = Math.max(...advisors.map((a) => Number(a.booking_rate || 0)), 1)
  const maxReplyRate = Math.max(...advisors.map((a) => Number(a.reply_rate || 0)), 1)

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Reports</h1>
          <p className="page-subtitle">Performance snapshots across your team and leads.</p>
        </div>
      </header>

      {error && <div className="rpt-error">{error}</div>}

      <div className="rpt-hero-kpi">
        <div className="panel rpt-hero-card">
          <span className="rpt-hero-label">Total leads</span>
          <strong className="rpt-hero-value" style={{ color: 'var(--text-primary)' }}>
            {loading ? '—' : (totals.leads_owned || 0).toLocaleString()}
          </strong>
          <span className="rpt-hero-sub">Org-wide</span>
        </div>
        <div className="panel rpt-hero-card">
          <span className="rpt-hero-label">Messages sent</span>
          <strong className="rpt-hero-value" style={{ color: 'var(--signal-blue)' }}>
            {loading ? '—' : (totals.messages_sent || 0).toLocaleString()}
          </strong>
          <span className="rpt-hero-sub">All time</span>
        </div>
        <div className="panel rpt-hero-card">
          <span className="rpt-hero-label">Replies received</span>
          <strong className="rpt-hero-value" style={{ color: 'var(--signal-purple)' }}>
            {loading ? '—' : (totals.replies || 0).toLocaleString()}
          </strong>
          <span className="rpt-hero-sub">{totals.hot_replies || 0} hot / callback</span>
        </div>
        <div className="panel rpt-hero-card">
          <span className="rpt-hero-label">Reply rate</span>
          <strong className="rpt-hero-value" style={{ color: 'var(--signal-blue)' }}>
            {loading ? '—' : pct(totals.reply_rate)}
          </strong>
          <span className="rpt-hero-sub">Replies / sent</span>
        </div>
        <div className="panel rpt-hero-card">
          <span className="rpt-hero-label">Hot reply rate</span>
          <strong className="rpt-hero-value" style={{ color: 'var(--signal-red)' }}>
            {loading ? '—' : pct(totals.hot_reply_rate)}
          </strong>
          <span className="rpt-hero-sub">Interested + callback</span>
        </div>
        <div className="panel rpt-hero-card">
          <span className="rpt-hero-label">Booking rate</span>
          <strong className="rpt-hero-value" style={{ color: 'var(--signal-green)' }}>
            {loading ? '—' : pct(totals.booking_rate)}
          </strong>
          <span className="rpt-hero-sub">{totals.booked_leads || 0} appointments set</span>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading report data…</div>
      ) : advisors.length === 0 ? (
        <div className="empty-state">No advisor data yet. Send some messages to see performance data here.</div>
      ) : (
        <>
          <section className="panel rpt-section">
            <div className="panel-header">
              <h2 className="panel-title">Advisor breakdown</h2>
              <span className="panel-count">{advisors.length} advisors</span>
            </div>
            <table className="data-table rpt-table">
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
                {advisors.map((a) => (
                  <tr key={a.advisor_id}>
                    <td>
                      <div className="rpt-advisor-cell">
                        <div className="rpt-avatar">{(a.advisor_name || 'A').charAt(0)}</div>
                        {a.advisor_name}
                      </div>
                    </td>
                    <td className="mono">{a.leads_owned}</td>
                    <td className="mono">{a.messages_sent}</td>
                    <td className="mono">{a.replies}</td>
                    <td>
                      <div className="rpt-bar-cell">
                        <RateBar value={a.reply_rate} max={maxReplyRate} color="var(--signal-blue)" />
                        <span className="mono">{pct(a.reply_rate)}</span>
                      </div>
                    </td>
                    <td>
                      <div className="rpt-bar-cell">
                        <RateBar value={a.hot_reply_rate} max={maxReplyRate} color="var(--signal-red)" />
                        <span className="mono" style={{ color: a.hot_replies > 0 ? 'var(--signal-red)' : undefined }}>{pct(a.hot_reply_rate)}</span>
                      </div>
                    </td>
                    <td>
                      <div className="rpt-bar-cell">
                        <RateBar value={a.booking_rate} max={maxBookingRate} color="var(--signal-green)" />
                        <span className="mono" style={{ color: a.booked_leads > 0 ? 'var(--signal-green)' : undefined }}>{pct(a.booking_rate)}</span>
                      </div>
                    </td>
                    <td className="mono" style={{ color: Number(a.dnc_rate) > 5 ? 'var(--signal-red)' : 'var(--text-secondary)' }}>
                      {pct(a.dnc_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <div className="rpt-bottom-grid">
            <section className="panel rpt-section">
              <div className="panel-header">
                <h2 className="panel-title">Top performers</h2>
              </div>
              <div className="rpt-top-list">
                {[...advisors]
                  .sort((a, b) => Number(b.booking_rate) - Number(a.booking_rate))
                  .slice(0, 5)
                  .map((a, i) => (
                    <div key={a.advisor_id} className="rpt-top-row">
                      <span className="rpt-top-rank" style={{ color: i === 0 ? 'var(--signal-amber)' : 'var(--text-tertiary)' }}>
                        #{i + 1}
                      </span>
                      <div className="rpt-top-avatar">{(a.advisor_name || 'A').charAt(0)}</div>
                      <span className="rpt-top-name">{a.advisor_name}</span>
                      <span className="rpt-top-rate" style={{ color: 'var(--signal-green)' }}>
                        {pct(a.booking_rate)} booked
                      </span>
                    </div>
                  ))}
              </div>
            </section>

            <section className="panel rpt-section">
              <div className="panel-header">
                <h2 className="panel-title">Engagement summary</h2>
              </div>
              <div className="rpt-summary-list">
                {[
                  { label: 'Total messages sent', value: totals.messages_sent || 0, color: 'var(--signal-blue)' },
                  { label: 'Total replies', value: totals.replies || 0, color: 'var(--signal-purple)' },
                  { label: 'Hot / callback replies', value: totals.hot_replies || 0, color: 'var(--signal-red)' },
                  { label: 'Appointments booked', value: totals.booked_leads || 0, color: 'var(--signal-green)' },
                  { label: 'DNC leads', value: totals.dnc_leads || 0, color: 'var(--signal-amber)' },
                  { label: 'Dupes prevented', value: totals.duplicate_leads_prevented || 0, color: 'var(--signal-green)' },
                ].map((item) => (
                  <div key={item.label} className="rpt-summary-row">
                    <span className="rpt-summary-label">{item.label}</span>
                    <span className="rpt-summary-value" style={{ color: item.color }}>{item.value.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  )
}
