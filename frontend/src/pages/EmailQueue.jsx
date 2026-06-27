import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import EmailReview from '../components/EmailReview'
import StatCard from '../components/StatCard'
import { TierBadge } from '../components/StatusBadge'
import '../styles/shared.css'
import './EmailQueue.css'

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

export default function EmailQueue() {
  const navigate = useNavigate()
  const [view, setView] = useState('queue') // 'queue' | 'sent'
  const [leads, setLeads] = useState([])
  const [sentRows, setSentRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(new Set())
  const [searchQuery, setSearchQuery] = useState('')
  const [reviewLeadIds, setReviewLeadIds] = useState(null)
  // Real scorecard numbers, per Mike's direct feedback that this page
  // "looks way too simple" - same pattern already proven on Replies'
  // action center, a SEPARATE call from the queue/sent lists so the
  // numbers reflect true totals, not whatever's currently filtered/paged.
  const [counts, setCounts] = useState(null)

  function loadCounts() {
    api.get('/email/counts').then(setCounts).catch(() => {})
  }

  function load(query = searchQuery) {
    setLoading(true)
    const trimmed = query.trim()
    const path = view === 'sent'
      ? (trimmed ? `/email/sent?search=${encodeURIComponent(trimmed)}` : '/email/sent')
      : (trimmed ? `/email/queue?search=${encodeURIComponent(trimmed)}` : '/email/queue')

    api.get(path)
      .then((rows) => {
        if (view === 'sent') setSentRows(rows)
        else setLeads(rows)
        setSelected(new Set())
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const timer = setTimeout(() => load(searchQuery), 250)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, view])

  useEffect(() => { loadCounts() }, [])

  function toggle(id) {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  function toggleAll() {
    if (leads.length > 0 && selected.size === leads.length) setSelected(new Set())
    else setSelected(new Set(leads.map((l) => l.id)))
  }

  function handleReviewSelected() {
    if (selected.size === 0) return
    setReviewLeadIds(Array.from(selected))
  }

  function handleReviewSingle(leadId) {
    setReviewLeadIds([leadId])
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Email queue</h1>
          <p className="page-subtitle">Anyone with an email on file — send a real promo, follow-up, or note with images, formatting, and open/click tracking.</p>
        </div>
        {view === 'queue' && (
          <button className="btn btn--primary" onClick={handleReviewSelected} disabled={selected.size === 0}>
            Review &amp; send {selected.size || ''} selected
          </button>
        )}
      </header>

      <div className="email-queue-scorecard-grid">
        <StatCard label="In queue" value={counts ? counts.queued : '—'} accent="blue" />
        <StatCard label="Sent today" value={counts ? counts.sent_today : '—'} accent="green" />
        <StatCard
          label="Open rate"
          value={counts && counts.open_rate_pct !== null ? `${counts.open_rate_pct}%` : '—'}
          sublabel={counts && counts.open_rate_pct === null ? 'No sends in last 30 days' : 'Last 30 days'}
          accent="purple"
        />
        <StatCard label="Clicks" value={counts ? counts.total_clicks_30d : '—'} sublabel="Last 30 days" accent="amber" />
      </div>

      <div className="email-queue-tabs">
        <button className={`tab ${view === 'queue' ? 'tab--active' : ''}`} onClick={() => setView('queue')}>
          Queue
        </button>
        <button className={`tab ${view === 'sent' ? 'tab--active' : ''}`} onClick={() => setView('sent')}>
          Sent
        </button>
      </div>

      <div className="email-queue-filter-bar">
        <input
          type="text"
          placeholder={view === 'sent' ? 'Search sent emails by name or email…' : 'Search by name or email…'}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="email-queue-search-input search-input"
        />
        <span className="filter-count mono">{view === 'sent' ? sentRows.length : leads.length} shown</span>
      </div>

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : view === 'sent' ? (
          sentRows.length === 0 ? (
            <div className="empty-state">
              {searchQuery.trim() ? 'No sent emails match your search.' : "Nothing sent yet. Once you email a lead, it'll show up here."}
            </div>
          ) : (
            <table className="data-table email-queue-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Subject</th>
                  <th>Status</th>
                  <th>Opened</th>
                  <th>Clicks</th>
                  <th>Sent</th>
                </tr>
              </thead>
              <tbody>
                {sentRows.map((row, idx) => (
                  <tr key={`${row.lead_id}-${idx}`} style={{ cursor: 'pointer' }} onClick={() => navigate(`/leads/${row.lead_id}`)}>
                    <td data-label="Name">{row.first_name} {row.last_name}</td>
                    <td data-label="Email" className="mono">{row.email || '—'}</td>
                    <td data-label="Subject">{row.subject}</td>
                    <td data-label="Status">
                      <span className={`badge ${row.status === 'sent' ? 'badge--green' : 'badge--red'}`}>{row.status}</span>
                    </td>
                    <td data-label="Opened">
                      {row.opened_at ? (
                        <span className="badge badge--green" title={formatDate(row.opened_at)}>Opened</span>
                      ) : (
                        <span className="badge badge--neutral-dim">Not yet</span>
                      )}
                    </td>
                    <td data-label="Clicks" className="mono">
                      {row.click_count > 0 ? row.click_count : '—'}
                    </td>
                    <td data-label="Sent" className="mono" style={{ fontSize: 12 }}>{formatDate(row.sent_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : leads.length === 0 ? (
          <div className="empty-state">
            {searchQuery.trim() ? 'No leads match your search.' : 'Nothing queued. Any lead with an email on file will show up here.'}
          </div>
        ) : (
          <>
            <div className="email-queue-list-header">
              <label className="compose-checkbox">
                <input type="checkbox" checked={leads.length > 0 && selected.size === leads.length} onChange={toggleAll} />
                Select all
              </label>
              <span className="filter-count mono">{leads.length} shown</span>
            </div>
            <ul className="email-queue-card-list">
              {leads.map((lead) => (
                <li key={lead.id} className={`email-queue-card ${selected.has(lead.id) ? 'email-queue-card--selected' : ''}`}>
                  <label className="compose-checkbox email-queue-card-checkbox" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggle(lead.id)} />
                  </label>
                  <div className="email-queue-card-main" onClick={() => navigate(`/leads/${lead.id}`)}>
                    <div className="email-queue-card-top">
                      <strong>{lead.first_name} {lead.last_name}</strong>
                      {lead.tier && <TierBadge tier={lead.tier} />}
                    </div>
                    <div className="email-queue-card-contact">
                      <span className="mono">{lead.email}</span>
                      {lead.phone && <span className="mono email-queue-card-phone">{lead.phone}</span>}
                    </div>
                  </div>
                  <button className="btn btn--secondary email-queue-row-action" onClick={() => handleReviewSingle(lead.id)}>
                    Review &amp; send
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {reviewLeadIds && (
        <EmailReview
          leadIds={reviewLeadIds}
          onClose={() => { setReviewLeadIds(null); load(); loadCounts() }}
          onSent={() => { load(); loadCounts() }}
        />
      )}
    </div>
  )
}
