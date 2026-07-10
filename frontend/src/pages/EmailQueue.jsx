import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './EmailQueue.css'

const TONE_OPTIONS = [
  { key: 'cold',   label: '❄️ Cold',   desc: 'Soft intro, no pressure, just opening a door' },
  { key: 'warm',   label: '☀️ Warm',   desc: 'Friendly, suggest a conversation, low-key CTA' },
  { key: 'hot',    label: '🔥 Hot',    desc: 'Direct, confident, clear ask for the appointment' },
  { key: 'urgent', label: '⚡ Urgent', desc: 'Brief, time-sensitive, gentle urgency' },
]

const STATUS_CONFIG = {
  new:     { label: 'Cold',   color: 'var(--signal-blue)',  dim: 'var(--signal-blue-dim)' },
  sent:    { label: 'Warm',   color: 'var(--signal-amber)', dim: 'var(--signal-amber-dim)' },
  replied: { label: 'Hot',    color: 'var(--signal-red)',   dim: 'var(--signal-red-dim)' },
  booked:  { label: 'Booked', color: 'var(--signal-green)', dim: 'var(--signal-green-dim)' },
}

export default function EmailQueue() {
  const [leads, setLeads]           = useState([])
  const [loading, setLoading]       = useState(true)
  const [selected, setSelected]     = useState(new Set())
  const [sending, setSending]       = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [tone, setTone]             = useState('warm')
  const [aiDirection, setAiDirection] = useState('')
  const [sendResult, setSendResult] = useState(null)

  // Per-lead draft panel
  const [draftLead, setDraftLead]         = useState(null)
  const [drafting, setDrafting]           = useState(false)
  const [draftResult, setDraftResult]     = useState(null)
  const [draftError, setDraftError]       = useState('')
  const [selectedOption, setSelectedOption] = useState(null)  // which of the 3 options
  const [editedSubject, setEditedSubject] = useState('')
  const [editedBody, setEditedBody]       = useState('')
  const [sendingDraft, setSendingDraft]   = useState(false)
  const [draftSentMsg, setDraftSentMsg]   = useState('')

  function load(query = searchQuery) {
    setLoading(true)
    const trimmed = query.trim()
    const path = trimmed ? `/email/queue?search=${encodeURIComponent(trimmed)}` : '/email/queue'
    api.get(path)
      .then((rows) => { setLeads(rows || []); setSelected(new Set()) })
      .catch(() => setLeads([]))
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

  async function handleBatchSend() {
    if (selected.size === 0) return
    setSending(true)
    setSendResult(null)
    try {
      const result = await api.post('/email/send-batch', { lead_ids: Array.from(selected) })
      setSendResult(result)
      setSelected(new Set())
      load()
    } catch (err) {
      setSendResult({ error: err.message })
    } finally {
      setSending(false)
    }
  }

  async function handleOpenDraft(lead) {
    if (draftLead?.id === lead.id) {
      setDraftLead(null)
      setDraftResult(null)
      return
    }
    setDraftLead(lead)
    setDraftResult(null)
    setDraftError('')
    setSelectedOption(null)
    setEditedSubject('')
    setEditedBody('')
    setDraftSentMsg('')
  }

  async function handleGenerateDraft() {
    if (!draftLead) return
    setDrafting(true)
    setDraftResult(null)
    setDraftError('')
    setSelectedOption(null)
    try {
      const result = await api.post(`/email/draft/${draftLead.id}`, {
        tone,
        ai_direction: aiDirection || null,
      })
      setDraftResult(result)
    } catch (err) {
      setDraftError(err.message || 'AI draft failed.')
    } finally {
      setDrafting(false)
    }
  }

  function handleSelectOption(option) {
    setSelectedOption(option)
    setEditedSubject(option.subject)
    setEditedBody(option.body)
  }

  async function handleSendDraft() {
    if (!draftLead || !editedBody.trim()) return
    setSendingDraft(true)
    setDraftSentMsg('')
    try {
      const formData = new FormData()
      formData.append('subject', editedSubject || `Hi ${draftLead.first_name || 'there'}`)
      formData.append('body_html', editedBody.replace(/\n/g, '<br>'))
      await api.upload(`/email/send-with-attachment/${draftLead.id}`, formData)
      setDraftSentMsg(`✓ Email sent to ${draftLead.first_name} ${draftLead.last_name}`)
      setDraftLead(null)
      setDraftResult(null)
      load()
    } catch (err) {
      setDraftSentMsg(`Failed: ${err.message}`)
    } finally {
      setSendingDraft(false)
    }
  }

  const counts = {
    total: leads.length,
    cold:  leads.filter((l) => l.status === 'new').length,
    warm:  leads.filter((l) => l.status === 'sent').length,
    hot:   leads.filter((l) => l.status === 'replied' || l.status === 'booked').length,
  }

  const currentTone = TONE_OPTIONS.find(t => t.key === tone) || TONE_OPTIONS[1]

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Email queue</h1>
          <p className="page-subtitle">Leads routed to email outreach. Click a name to get AI-drafted options with talking points.</p>
        </div>
        <button className="btn btn--primary" onClick={handleBatchSend} disabled={sending || selected.size === 0}>
          {sending ? 'Sending…' : selected.size > 0 ? `Batch send to ${selected.size}` : 'Select leads to batch send'}
        </button>
      </header>

      {/* KPI row */}
      <div className="eq-kpi-row">
        {[
          { label: 'Total in queue', value: counts.total, color: 'var(--text-primary)' },
          { label: 'Cold — never contacted', value: counts.cold, color: 'var(--signal-blue)' },
          { label: 'Warm — emailed once', value: counts.warm, color: 'var(--signal-amber)' },
          { label: 'Hot — replied or booked', value: counts.hot, color: 'var(--signal-red)' },
        ].map(({ label, value, color }) => (
          <div key={label} className="panel eq-kpi-card">
            <span className="eq-kpi-label">{label}</span>
            <strong className="eq-kpi-value" style={{ color }}>{loading ? '—' : value}</strong>
          </div>
        ))}
      </div>

      {/* Tone + AI Direction controls */}
      <div className="panel eq-tone-panel">
        <div className="eq-tone-header">
          <span className="eq-tone-title">AI message settings</span>
          <span className="eq-tone-desc">These apply when you click ✨ Generate options on any lead</span>
        </div>
        <div className="eq-tone-pills">
          {TONE_OPTIONS.map((t) => (
            <button
              key={t.key}
              className={`lead-tone-pill ${tone === t.key ? 'lead-tone-pill--active' : ''}`}
              onClick={() => setTone(t.key)}
              title={t.desc}
            >
              {t.label}
            </button>
          ))}
        </div>
        <p className="settings-help" style={{ marginTop: 6 }}>{currentTone.desc}</p>
        <input
          className="settings-input"
          style={{ marginTop: 10 }}
          placeholder="AI direction (optional): e.g. file check — ask if they still need pre-need planning"
          value={aiDirection}
          onChange={(e) => setAiDirection(e.target.value)}
        />
      </div>

      {sendResult && (
        <div className={`eq-send-result ${sendResult.error ? 'eq-send-result--error' : 'eq-send-result--success'}`}>
          {sendResult.error
            ? `Send failed: ${sendResult.error}`
            : `Sent: ${sendResult.sent_count ?? 0} · Failed: ${sendResult.failed_count ?? 0} · Skipped: ${sendResult.skipped_count ?? 0}`}
        </div>
      )}

      {draftSentMsg && (
        <div className={`eq-send-result ${draftSentMsg.startsWith('Failed') ? 'eq-send-result--error' : 'eq-send-result--success'}`}>
          {draftSentMsg}
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
          {selected.size > 0 && <span className="eq-selected-badge">{selected.size} selected</span>}
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
                <th>Tier</th>
                <th>Status</th>
                <th>Source year</th>
                <th>Last action</th>
                <th>AI draft</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => {
                const cfg = STATUS_CONFIG[lead.status] || STATUS_CONFIG.new
                const isOpen = draftLead?.id === lead.id
                return (
                  <>
                    <tr key={lead.id} className={selected.has(lead.id) ? 'eq-row--selected' : ''} style={{ borderBottom: isOpen ? 'none' : undefined }}>
                      <td><input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggle(lead.id)} /></td>
                      <td>
                        <span className="eq-lead-name" onClick={() => handleOpenDraft(lead)} style={{ cursor: 'pointer', fontWeight: 600 }}>
                          {`${lead.first_name || ''} ${lead.last_name || ''}`.trim() || '—'}
                        </span>
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>{lead.email || '—'}</td>
                      <td style={{ fontSize: 12 }}>{lead.tier || '—'}</td>
                      <td>
                        <span className="eq-status-pill" style={{ color: cfg.color, background: cfg.dim }}>{cfg.label}</span>
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>{lead.source_year || '—'}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-secondary)', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {lead.last_action_raw || '—'}
                      </td>
                      <td>
                        <button className="btn btn--secondary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => handleOpenDraft(lead)}>
                          {isOpen ? '✕ Close' : '✨ Draft'}
                        </button>
                      </td>
                    </tr>

                    {/* Inline AI Draft Panel */}
                    {isOpen && (
                      <tr key={`${lead.id}-draft`}>
                        <td colSpan={8} style={{ padding: 0 }}>
                          <div className="eq-draft-panel">
                            <div className="eq-draft-header">
                              <div>
                                <strong>AI email draft</strong> for {lead.first_name} {lead.last_name}
                                {lead.tier && <span className="tier-chip" style={{ marginLeft: 8, fontSize: 11 }}>{lead.tier}</span>}
                                {lead.source_year && <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 8 }}>({lead.source_year})</span>}
                              </div>
                              <button className="btn btn--primary" onClick={handleGenerateDraft} disabled={drafting} style={{ fontSize: 13 }}>
                                {drafting ? '⏳ Generating…' : '✨ Generate options'}
                              </button>
                            </div>

                            {draftError && (
                              <div className="compose-error" style={{ margin: '8px 0' }}>⚠️ {draftError}</div>
                            )}

                            {draftResult && (
                              <div className="eq-draft-body">
                                {/* Talking points */}
                                {draftResult.talking_points?.length > 0 && (
                                  <div className="eq-talking-points">
                                    <div className="eq-talking-label">💡 Talking points for this lead</div>
                                    <ul className="eq-talking-list">
                                      {draftResult.talking_points.map((pt, i) => (
                                        <li key={i}>{pt}</li>
                                      ))}
                                    </ul>
                                  </div>
                                )}

                                {/* 3 options */}
                                <div className="eq-options-label">Choose a message to start from:</div>
                                <div className="eq-options-grid">
                                  {draftResult.options?.map((opt, i) => (
                                    <div
                                      key={i}
                                      className={`eq-option-card ${selectedOption === opt ? 'eq-option-card--selected' : ''}`}
                                      onClick={() => handleSelectOption(opt)}
                                    >
                                      <div className="eq-option-label">{opt.label}</div>
                                      <div className="eq-option-subject">Subject: {opt.subject}</div>
                                      <div className="eq-option-preview">{opt.body.slice(0, 120)}…</div>
                                    </div>
                                  ))}
                                </div>

                                {/* Edit & send */}
                                {selectedOption && (
                                  <div className="eq-edit-section">
                                    <div className="eq-edit-label">Edit before sending:</div>
                                    <input
                                      className="compose-subject"
                                      value={editedSubject}
                                      onChange={(e) => setEditedSubject(e.target.value)}
                                      placeholder="Subject"
                                    />
                                    <textarea
                                      className="compose-textarea"
                                      rows={7}
                                      value={editedBody}
                                      onChange={(e) => setEditedBody(e.target.value)}
                                    />
                                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 8 }}>
                                      <button className="btn btn--secondary" onClick={() => { setSelectedOption(null); setEditedBody(''); setEditedSubject('') }}>
                                        ← Back to options
                                      </button>
                                      <button className="btn btn--primary" onClick={handleSendDraft} disabled={sendingDraft || !editedBody.trim()}>
                                        {sendingDraft ? 'Sending…' : `Send to ${lead.first_name || lead.email}`}
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            )}

                            {!draftResult && !drafting && (
                              <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '12px 0 0' }}>
                                Click "Generate options" to get AI-crafted talking points and 3 email drafts personalized to this lead's history.
                              </p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
