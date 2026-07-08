import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './EmailQueue.css'

const TONE_LABELS = ['Ice cold', 'Cool', 'Warm', 'Hot', 'Urgent']
const TONE_COLORS = ['var(--signal-blue)', 'var(--signal-blue)', 'var(--signal-amber)', 'var(--signal-red)', 'var(--signal-red)']

const STATUS_CONFIG = {
  new: { label: 'Cold', color: 'var(--signal-blue)', dim: 'var(--signal-blue-dim)' },
  sent: { label: 'Warm', color: 'var(--signal-amber)', dim: 'var(--signal-amber-dim)' },
  replied: { label: 'Hot', color: 'var(--signal-red)', dim: 'var(--signal-red-dim)' },
  booked: { label: 'Booked', color: 'var(--signal-green)', dim: 'var(--signal-green-dim)' },
}

function getStatusConfig(status) {
  return STATUS_CONFIG[status] || STATUS_CONFIG.new
}

export default function EmailQueue() {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(new Set())
  const [sending, setSending] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [tone, setTone] = useState(2)
  const [previewLead, setPreviewLead] = useState(null)
  const [sendResult, setSendResult] = useState(null)

  function load(query = searchQuery) {
    setLoading(true)
    const trimmed = query.trim()
    const path = trimmed ? `/email/queue?search=${encodeURIComponent(trimmed)}` : '/email/queue'
    api.get(path)
      .then((rows) => { setLeads(rows); setSelected(new Set()) })
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
    setSendResult(null)
    try {
      const result = await api.post('/email/send-batch', { lead_ids: Array.from(selected), tone_level: tone })
      setSendResult(result)
      setSelected(new Set())
      load(searchQuery)
    } catch (err) {
      setSendResult({ error: err.message })
    } finally {
      setSending(false)
    }
  }

  const counts = {
    total: leads.length,
    cold: leads.filter((l) => l.status === 'new').length,
    warm: leads.filter((l) => l.status === 'sent').length,
    hot: leads.filter((l) => l.status === 'replied' || l.status === 'booked').length,
  }

  const toneColor = TONE_COLORS[tone]
  const toneLabel = TONE_LABELS[tone]

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Email queue</h1>
          <p className="page-subtitle">Leads routed to email outreach. Select leads and control message tone before sending.</p>
        </div>
        <button className="btn btn--primary" onClick={handleSend} disabled={sending || selected.size === 0}>
          {sending ? 'Sending…' : selected.size > 0 ? `Send to ${selected.size} selected` : 'Select leads to send'}
        </button>
      </header>

      <div className="eq-kpi-row">
        <div className="panel eq-kpi-card">
          <span className="eq-kpi-label">Total in queue</span>
          <strong className="eq-kpi-value" style={{ color: 'var(--text-primary)' }}>{loading ? '—' : counts.total}</strong>
        </div>
        <div className="panel eq-kpi-card">
          <span className="eq-kpi-label">Cold — never contacted</span>
          <strong className="eq-kpi-value" style={{ color: 'var(--signal-blue)' }}>{loading ? '—' : counts.cold}</strong>
        </div>
        <div className="panel eq-kpi-card">
          <span className="eq-kpi-label">Warm — emailed once</span>
          <strong className="eq-kpi-value" style={{ color: 'var(--signal-amber)' }}>{loading ? '—' : counts.warm}</strong>
        </div>
        <div className="panel eq-kpi-card">
          <span className="eq-kpi-label">Hot — replied or booked</span>
          <strong className="eq-kpi-value" style={{ color: 'var(--signal-red)' }}>{loading ? '—' : counts.hot}</strong>
        </div>
      </div>

      <div className="panel eq-tone-panel">
        <div className="eq-tone-header">
          <div>
            <span className="eq-tone-title">AI message tone</span>
            <span className="eq-tone-desc">Controls how urgent and direct the outreach message is</span>
          </div>
          <span className="eq-tone-badge" style={{ color: toneColor, borderColor: toneColor, background: `${toneColor}18` }}>
            {toneLabel}
          </span>
        </div>
        <div className="eq-tone-slider-row">
          <span className="eq-tone-end">❄️ Cool</span>
          <input
            type="range"
            min="0"
            max="4"
            step="1"
            value={tone}
            onChange={(e) => setTone(Number(e.target.value))}
            className="eq-tone-slider"
            style={{ '--tone-color': toneColor }}
          />
          <span className="eq-tone-end">🔥 Urgent</span>
        </div>
        <div className="eq-tone-preview">
          <span className="eq-tone-preview-label">Preview tone:</span>
          {tone === 0 && <span className="eq-tone-preview-text">"Hi [Name], just a gentle note to let you know I'm available whenever you're ready to talk."</span>}
          {tone === 1 && <span className="eq-tone-preview-text">"Hi [Name], I wanted to reach out and introduce myself — no pressure, just here when you need me."</span>}
          {tone === 2 && <span className="eq-tone-preview-text">"Hi [Name], I'd love to connect and walk you through your options. Here's a link to book a time."</span>}
          {tone === 3 && <span className="eq-tone-preview-text">"Hi [Name], I'm following up because I think now is a good time to talk. I have availability this week."</span>}
          {tone === 4 && <span className="eq-tone-preview-text">"[Name], I want to make sure you have the information you need right now. Please reach out today."</span>}
        </div>
      </div>

      {sendResult && (
        <div className={`eq-send-result ${sendResult.error ? 'eq-send-result--error' : 'eq-send-result--success'}`}>
          {sendResult.error
            ? `Send failed: ${sendResult.error}`
            : `Sent: ${sendResult.sent_count} · Failed: ${sendResult.failed_count} · Skipped: ${sendResult.skipped_count}`}
        </div>
      )}

      <section className="panel">
        <div className="panel-header">
          <div className="eq-filter-bar">
            <input
              type="text"
              placeholder="Search by name or email…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="search-input"
              style={{ width: 280 }}
            />
            <span className="panel-count">{leads.length} shown</span>
          </div>
          {selected.size > 0 && (
            <span className="eq-selected-badge">{selected.size} selected</span>
          )}
        </div>

        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : leads.length === 0 ? (
          <div className="empty-state">No leads in email queue.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input type="checkbox" checked={selected.size === leads.length && leads.length > 0} onChange={toggleAll} />
                </th>
                <th>Name</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Status</th>
                <th>Track</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => {
                const cfg = getStatusConfig(lead.status)
                return (
                  <tr key={lead.id} className={selected.has(lead.id) ? 'eq-row--selected' : ''}>
                    <td>
                      <input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggle(lead.id)} />
                    </td>
                    <td>
                      <span
                        className="eq-lead-name"
                        onClick={() => setPreviewLead(previewLead?.id === lead.id ? null : lead)}
                      >
                        {`${lead.first_name || ''} ${lead.last_name || ''}`.trim() || '—'}
                      </span>
                    </td>
                    <td className="mono">{lead.email || '—'}</td>
                    <td className="mono">{lead.phone || '—'}</td>
                    <td>
                      <span className="eq-status-pill" style={{ color: cfg.color, background: cfg.dim }}>
                        {cfg.label}
                      </span>
                    </td>
                    <td className="mono" style={{ color: 'var(--text-secondary)', fontSize: 11 }}>
                      {lead.message_track || 'email_only_nurture'}
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
