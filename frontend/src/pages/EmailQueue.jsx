import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './EmailQueue.css'

export default function EmailQueue() {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(new Set())
  const [sending, setSending] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  function load(query = searchQuery) {
    setLoading(true)
    const trimmed = query.trim()
    const path = trimmed ? `/email/queue?search=${encodeURIComponent(trimmed)}` : '/email/queue'
    api.get(path)
      .then((rows) => {
        setLeads(rows)
        setSelected(new Set())
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const timer = setTimeout(() => load(searchQuery), 250)
    return () => clearTimeout(timer)
  }, [searchQuery])

  function toggle(id) {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  function toggleAll() {
    if (leads.length > 0 && selected.size === leads.length) setSelected(new Set())
    else setSelected(new Set(leads.map((l) => l.id)))
  }

  async function handleSend() {
    if (selected.size === 0) return
    setSending(true)
    try {
      const result = await api.post('/email/send-batch', { lead_ids: Array.from(selected) })
      alert(`Sent: ${result.sent_count}, Failed: ${result.failed_count}, Skipped: ${result.skipped_count}`)
      setSelected(new Set())
      load(searchQuery)
    } catch (err) {
      alert(`Send failed: ${err.message}`)
    } finally {
      setSending(false)
    }
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Email queue</h1>
          <p className="page-subtitle">Leads routed to email outreach. Some may still have a phone on file from the original import.</p>
        </div>
        <button className="btn btn--primary" onClick={handleSend} disabled={sending || selected.size === 0}>
          {sending ? 'Sending…' : `Send to ${selected.size || ''} selected`}
        </button>
      </header>

      <div className="email-queue-filter-bar">
        <input
          type="text"
          placeholder="Search by name or email…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="email-queue-search-input search-input"
        />
        <span className="filter-count mono">{leads.length} shown</span>
      </div>

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading queue…</div>
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
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <tr key={lead.id}>
                  <td data-label="Select"><input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggle(lead.id)} /></td>
                  <td data-label="Name">{lead.first_name} {lead.last_name}</td>
                  <td data-label="Email" className="mono">{lead.email || '—'}</td>
                  <td data-label="Phone" className="mono">{lead.phone || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
