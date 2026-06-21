import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'

export default function EmailQueue() {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(new Set())
  const [sending, setSending] = useState(false)

  function load() {
    setLoading(true)
    api.get('/email/queue').then(setLeads).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  function toggle(id) {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  function toggleAll() {
    if (selected.size === leads.length) setSelected(new Set())
    else setSelected(new Set(leads.map((l) => l.id)))
  }

  async function handleSend() {
    if (selected.size === 0) return
    setSending(true)
    try {
      const result = await api.post('/email/send-batch', { lead_ids: Array.from(selected) })
      alert(`Sent: ${result.sent_count}, Failed: ${result.failed_count}, Skipped: ${result.skipped_count}`)
      setSelected(new Set())
      load()
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
          <p className="page-subtitle">Leads with no phone number — routed here instead of the SMS cadence.</p>
        </div>
        <button className="btn btn--primary" onClick={handleSend} disabled={sending || selected.size === 0}>
          {sending ? 'Sending…' : `Send to ${selected.size || ''} selected`}
        </button>
      </header>

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading queue…</div>
        ) : leads.length === 0 ? (
          <div className="empty-state">Nothing queued. Email-only leads from your imports will show up here.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th><input type="checkbox" checked={selected.size === leads.length} onChange={toggleAll} /></th>
                <th>Name</th>
                <th>Email</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <tr key={lead.id}>
                  <td><input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggle(lead.id)} /></td>
                  <td>{lead.first_name} {lead.last_name}</td>
                  <td className="mono">{lead.email}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
