import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import EmailReview from '../components/EmailReview'
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
          <p className="page-subtitle">Leads routed to email outreach. Some may still have a phone on file from the original import.</p>
        </div>
        {view === 'queue' && (
          <button className="btn btn--primary" onClick={handleReviewSelected} disabled={selected.size === 0}>
            Review &amp; send {selected.size || ''} selected
          </button>
        )}
      </header>

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
                    <td data-label="Sent" className="mono" style={{ fontSize: 12 }}>{formatDate(row.sent_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : leads.length === 0 ? (
          <div className="empty-state">
            {searchQuery.trim() ? 'No email-only leads match your search.' : 'Nothing queued. Email-only leads from your imports will show up here.'}
          </div>
        ) : (
          <table className="data-table email-queue-table">
            <thead>
              <tr>
                <th><input type="checkbox" checked={leads.length > 0 && selected.size === leads.length} onChange={toggleAll} /></th>
                <th>Name</th>
                <th>Email</th>
                <th>Phone</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <tr key={lead.id}>
                  <td data-label="Select" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggle(lead.id)} />
                  </td>
                  <td data-label="Name" style={{ cursor: 'pointer' }} onClick={() => navigate(`/leads/${lead.id}`)}>{lead.first_name} {lead.last_name}</td>
                  <td data-label="Email" className="mono">{lead.email || '—'}</td>
                  <td data-label="Phone" className="mono">{lead.phone || '—'}</td>
                  <td data-label="">
                    <button className="btn btn--secondary email-queue-row-action" onClick={() => handleReviewSingle(lead.id)}>
                      Review &amp; send
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {reviewLeadIds && (
        <EmailReview
          leadIds={reviewLeadIds}
          onClose={() => { setReviewLeadIds(null); load() }}
          onSent={() => load()}
        />
      )}
    </div>
  )
}
