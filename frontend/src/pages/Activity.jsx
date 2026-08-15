import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'

const DELIVERY_CONFIG = {
  delivered:   { label: '✓ Delivered',   color: 'var(--signal-green)',  bg: 'rgba(30,240,168,0.12)',  border: 'rgba(30,240,168,0.3)' },
  sent:        { label: '✓ Sent',         color: 'var(--signal-green)',  bg: 'rgba(30,240,168,0.08)',  border: 'rgba(30,240,168,0.2)' },
  pending:     { label: '⏳ Pending',      color: 'var(--signal-amber)',  bg: 'rgba(255,200,0,0.1)',    border: 'rgba(255,200,0,0.3)' },
  queued:      { label: '⏳ Queued',       color: 'var(--signal-amber)',  bg: 'rgba(255,200,0,0.1)',    border: 'rgba(255,200,0,0.3)' },
  sending:     { label: '⏳ Sending',      color: 'var(--signal-amber)',  bg: 'rgba(255,200,0,0.1)',    border: 'rgba(255,200,0,0.3)' },
  failed:      { label: '✗ Failed',        color: 'var(--signal-red)',    bg: 'rgba(231,76,60,0.1)',    border: 'rgba(231,76,60,0.3)' },
  undelivered: { label: '✗ Undelivered',  color: 'var(--signal-red)',    bg: 'rgba(231,76,60,0.1)',    border: 'rgba(231,76,60,0.3)' },
}

function DeliveryBadge({ status }) {
  const cfg = DELIVERY_CONFIG[status] || DELIVERY_CONFIG.pending
  return (
    <span style={{
      fontSize: 11, borderRadius: 4, padding: '2px 7px', whiteSpace: 'nowrap',
      color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.border}`,
    }}>
      {cfg.label}
    </span>
  )
}

function formatTime(isoStr) {
  if (!isoStr) return '—'
  const d = new Date(isoStr)
  const now = new Date()
  const today = now.toDateString() === d.toDateString()
  const yesterday = new Date(now - 86400000).toDateString() === d.toDateString()
  const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (today) return `Today ${timeStr}`
  if (yesterday) return `Yesterday ${timeStr}`
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + timeStr
}

export default function Activity() {
  const navigate = useNavigate()
  const [items, setItems]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [channel, setChannel]   = useState('all')   // all | sms | email
  const [days, setDays]         = useState(30)
  const [search, setSearch]     = useState('')

  function load() {
    setLoading(true)
    api.get(`/activity/sent?limit=300&days=${days}`)
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [days])

  const filtered = items.filter((item) => {
    if (channel !== 'all' && item.channel !== channel) return false
    if (search.trim()) {
      const q = search.toLowerCase()
      return (
        item.lead_name?.toLowerCase().includes(q) ||
        item.lead_email?.toLowerCase().includes(q) ||
        item.lead_phone?.includes(q) ||
        item.subject?.toLowerCase().includes(q) ||
        item.body_preview?.toLowerCase().includes(q)
      )
    }
    return true
  })

  const todayCount = items.filter((i) => {
    if (!i.sent_at) return false
    return new Date(i.sent_at).toDateString() === new Date().toDateString()
  }).length

  const deliveredCount = items.filter((i) => i.delivery_status === 'delivered').length
  const failedCount    = items.filter((i) => ['failed', 'undelivered'].includes(i.delivery_status)).length

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 6 }}>
          <h1 className="page-title" style={{ margin: 0 }}>Activity</h1>
          {!loading && <span className="eq-queue-badge">{items.length} sends</span>}
        </div>
        <p className="page-subtitle" style={{ marginTop: 4 }}>
          Every SMS and email you've sent — with delivery confirmation for SMS.
        </p>
      </div>

      {/* Stats strip */}
      <div className="panel" style={{ display: 'flex', gap: 0, padding: 0, overflow: 'hidden', marginBottom: 16 }}>
        {[
          { label: 'Sent today',   value: todayCount,      color: 'var(--accent)',        icon: '📤' },
          { label: 'Total sends',  value: items.length,    color: 'var(--text-primary)',  icon: '📬' },
          { label: 'Delivered',    value: deliveredCount,  color: 'var(--signal-green)',  icon: '✓'  },
          { label: 'Failed',       value: failedCount,     color: failedCount > 0 ? 'var(--signal-red)' : 'var(--text-secondary)', icon: '✗' },
        ].map((s, i, arr) => (
          <div key={s.label} style={{
            flex: 1, padding: '14px 20px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
            borderRight: i < arr.length - 1 ? '1px solid var(--border-subtle)' : 'none',
          }}>
            <div style={{ fontSize: 22, lineHeight: 1, color: s.color }}>{s.icon}</div>
            <strong style={{ fontSize: 22, color: s.color, lineHeight: 1.1 }}>
              {loading ? '—' : s.value}
            </strong>
            <span style={{ fontSize: 11, color: 'var(--text-secondary)', textAlign: 'center' }}>{s.label}</span>
          </div>
        ))}
      </div>

      {/* Controls */}
      <div className="panel" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16, padding: '12px 16px' }}>
        <input
          type="text"
          placeholder="Search by name, email, phone, or subject…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="search-input"
          style={{ width: 320 }}
        />
        <div style={{ display: 'flex', gap: 6 }}>
          {['all', 'sms', 'email'].map((ch) => (
            <button
              key={ch}
              className={`lead-tone-pill ${channel === ch ? 'lead-tone-pill--active' : ''}`}
              onClick={() => setChannel(ch)}
              style={{ fontSize: 12 }}
            >
              {ch === 'all' ? '📬 All' : ch === 'sms' ? '📱 SMS' : '✉️ Email'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', alignSelf: 'center' }}>Last</span>
          {[7, 14, 30, 90].map((d) => (
            <button
              key={d}
              className={`lead-tone-pill ${days === d ? 'lead-tone-pill--active' : ''}`}
              onClick={() => setDays(d)}
              style={{ fontSize: 12 }}
            >
              {d}d
            </button>
          ))}
        </div>
        <button className="btn btn--secondary" style={{ fontSize: 12, padding: '5px 12px' }} onClick={load}>
          ↻ Refresh
        </button>
      </div>

      {/* Activity table */}
      <section className="panel" style={{ padding: 0 }}>
        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">No activity found for the selected filters.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 36 }}>Ch</th>
                <th>Name</th>
                <th>Contact</th>
                <th>Subject / Preview</th>
                <th style={{ whiteSpace: 'nowrap' }}>Sent</th>
                <th>Delivery</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => {
                const isToday = item.sent_at && new Date(item.sent_at).toDateString() === new Date().toDateString()
                return (
                  <tr key={`${item.channel}-${item.id}`}>
                    <td style={{ textAlign: 'center', fontSize: 16 }}>
                      {item.channel === 'sms' ? '📱' : '✉️'}
                    </td>
                    <td>
                      <span
                        style={{ fontWeight: 600, cursor: 'pointer', color: 'var(--accent)', textDecoration: 'underline' }}
                        onClick={() => navigate(`/leads/${item.lead_id}`)}
                      >
                        {item.lead_name}
                      </span>
                      {isToday && (
                        <span style={{
                          marginLeft: 6, fontSize: 10, fontWeight: 700,
                          background: 'rgba(30,240,168,0.15)', color: 'var(--signal-green)',
                          border: '1px solid rgba(30,240,168,0.3)', borderRadius: 3, padding: '1px 5px',
                        }}>
                          TODAY
                        </span>
                      )}
                    </td>
                    <td className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      {item.channel === 'sms' ? item.lead_phone : item.lead_email}
                    </td>
                    <td style={{ fontSize: 12, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {item.subject
                        ? <><strong style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Subj: </strong>{item.subject}</>
                        : <span style={{ color: 'var(--text-secondary)' }}>{item.body_preview || '—'}</span>}
                    </td>
                    <td style={{ fontSize: 12, color: isToday ? 'var(--signal-green)' : 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                      {formatTime(item.sent_at)}
                    </td>
                    <td>
                      <DeliveryBadge status={item.delivery_status} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
