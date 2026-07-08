import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import '../styles/shared.css'
import './WorkQueue.css'

const emptyQueue = {
  needs_text: [],
  needs_reply: [],
  cadence_due: [],
  outcomes_needed: [],
}

const sections = [
  {
    key: 'needs_text',
    title: 'Needs Text',
    subtitle: 'New assigned leads not contacted yet.',
    accent: 'blue',
    empty: 'No new leads waiting for first contact.',
  },
  {
    key: 'needs_reply',
    title: 'Needs Reply',
    subtitle: 'Interested and callback replies still unreviewed.',
    accent: 'red',
    empty: 'No hot replies need review right now.',
  },
  {
    key: 'cadence_due',
    title: 'Cadence Due',
    subtitle: 'Active cadence touches due now.',
    accent: 'amber',
    empty: 'No cadence touches are due.',
  },
  {
    key: 'outcomes_needed',
    title: 'Outcomes Needed',
    subtitle: 'Booked leads missing appointment outcomes.',
    accent: 'green',
    empty: 'No booked leads are missing outcomes.',
  },
]

function formatDate(value) {
  if (!value) return '–'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function itemTimestamp(sectionKey, item) {
  if (sectionKey === 'needs_reply') return item.received_at
  if (sectionKey === 'cadence_due') return item.next_touch_due_at
  if (sectionKey === 'outcomes_needed') return item.updated_at
  return item.created_at
}

function itemMeta(sectionKey, item) {
  if (sectionKey === 'needs_reply') return item.classification?.replaceAll('_', ' ') || 'reply'
  if (sectionKey === 'cadence_due') return `Touch ${(item.current_touch_number ?? 0) + 1}`
  if (sectionKey === 'outcomes_needed') return 'Booked'
  return item.tier?.replaceAll('_', ' ') || item.status || 'lead'
}

function QueueItem({ item, sectionKey, accent, selected, onToggle, onOpen, lastSelectedRef, allItems }) {
  function handleClick(e) {
    if (e.shiftKey && lastSelectedRef.current !== null) {
      // Shift+click: select range
      const lastIdx = allItems.findIndex(i => i.lead_id === lastSelectedRef.current)
      const thisIdx = allItems.findIndex(i => i.lead_id === item.lead_id)
      if (lastIdx !== -1 && thisIdx !== -1) {
        const [start, end] = lastIdx < thisIdx ? [lastIdx, thisIdx] : [thisIdx, lastIdx]
        const rangeIds = allItems.slice(start, end + 1).map(i => i.lead_id)
        onToggle(rangeIds, true)
        return
      }
    }
    if (e.ctrlKey || e.metaKey) {
      // Ctrl+click: toggle single
      lastSelectedRef.current = item.lead_id
      onToggle([item.lead_id])
      return
    }
    // Plain click: open lead
    onOpen(item.lead_id)
  }

  function handleCheckbox(e) {
    e.stopPropagation()
    lastSelectedRef.current = item.lead_id
    onToggle([item.lead_id])
  }

  return (
    <div
      className={`workqueue-item ${selected ? 'workqueue-item--selected' : ''}`}
      onClick={handleClick}
    >
      <div className="workqueue-item-check" onClick={e => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          onChange={handleCheckbox}
        />
      </div>
      <div className={`workqueue-item-signal workqueue-item-signal--${accent}`} />
      <div className="workqueue-item-main">
        <div className="workqueue-item-topline">
          <strong>{item.name || 'Unnamed lead'}</strong>
          <span className="mono">{item.phone || 'No phone'}</span>
        </div>
        <p>{item.context || item.body || 'Open lead for details.'}</p>
        {item.body ? <small>{item.body}</small> : null}
      </div>
      <div className="workqueue-item-meta">
        <span className={`workqueue-pill workqueue-pill--${accent}`}>{itemMeta(sectionKey, item)}</span>
        <span className="mono">{formatDate(itemTimestamp(sectionKey, item))}</span>
      </div>
    </div>
  )
}

export default function WorkQueue() {
  const navigate = useNavigate()
  const [queue, setQueue] = useState(emptyQueue)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Multi-select state
  const [selected, setSelected] = useState(new Set())
  const [showBulkCompose, setShowBulkCompose] = useState(false)
  const [bulkMessage, setBulkMessage] = useState('')
  const [bulkIncludeBooking, setBulkIncludeBooking] = useState(true)
  const [bulkSending, setBulkSending] = useState(false)
  const [bulkResult, setBulkResult] = useState(null)

  // Tracks last selected item per section for shift+click range
  const lastSelectedRefs = useMemo(() => {
    const refs = {}
    sections.forEach(s => { refs[s.key] = { current: null } })
    return refs
  }, [])

  const total = useMemo(
    () => sections.reduce((sum, section) => sum + (queue[section.key]?.length || 0), 0),
    [queue]
  )

  async function loadQueue() {
    setError('')
    setLoading(true)
    try {
      const data = await api.get('/workqueue/today')
      setQueue({ ...emptyQueue, ...data })
    } catch (err) {
      setError(err.message || "Could not load today's work.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadQueue() }, [])

  function openLead(leadId) {
    if (leadId) navigate(`/leads/${leadId}`)
  }

  function handleToggle(ids, forceOn = false) {
    setSelected(prev => {
      const next = new Set(prev)
      if (forceOn) {
        ids.forEach(id => next.add(id))
      } else {
        ids.forEach(id => next.has(id) ? next.delete(id) : next.add(id))
      }
      return next
    })
  }

  async function handleBulkSend() {
    if (!bulkMessage.trim() || selected.size === 0) return
    setBulkSending(true)
    setBulkResult(null)
    try {
      const result = await api.post('/sms/send-batch', {
        lead_ids: Array.from(selected),
        template: bulkMessage,
        include_booking_link: bulkIncludeBooking,
      })
      setBulkResult(result)
      setSelected(new Set())
      setBulkMessage('')
      setShowBulkCompose(false)
      loadQueue()
    } catch (err) {
      alert(`Bulk send failed: ${err.message}`)
    } finally {
      setBulkSending(false)
    }
  }

  const selectedCount = selected.size

  return (
    <div className="workqueue-page">
      <header className="page-header workqueue-header">
        <div>
          <p className="workqueue-eyebrow">Advisor Command Queue</p>
          <h1 className="page-title">Today&apos;s Work</h1>
          <p className="page-subtitle">The leads, replies, cadence touches, and missing outcomes that need action now.</p>
        </div>
        <div className="workqueue-summary panel">
          <SignalPulse color={total > 0 ? 'blue' : 'green'} label={total > 0 ? 'Action needed' : 'Clear'} />
          <strong>{loading ? '–' : total}</strong>
          <span>open work items</span>
          <button className="btn btn--secondary" onClick={loadQueue} disabled={loading}>Refresh</button>
        </div>
      </header>

      {error ? <div className="workqueue-alert">{error}</div> : null}

      {/* Bulk action bar */}
      {selectedCount > 0 && (
        <div className="bulk-bar">
          <span className="bulk-bar-count">{selectedCount} selected</span>
          <button className="btn btn--secondary" onClick={() => setSelected(new Set())}>Clear</button>
          <button className="btn btn--primary" onClick={() => setShowBulkCompose(true)}>Send to selected</button>
        </div>
      )}

      {/* Bulk compose panel */}
      {showBulkCompose && (
        <section className="panel bulk-compose-panel">
          <div className="panel-header">
            <h2 className="panel-title">Send to {selectedCount} leads</h2>
            <button className="back-link" onClick={() => setShowBulkCompose(false)}>Cancel</button>
          </div>
          <textarea
            className="compose-textarea"
            placeholder="Hi {first_name}, this is..."
            value={bulkMessage}
            onChange={(e) => setBulkMessage(e.target.value)}
            rows={4}
          />
          <p className="settings-help">
            Use <code>&#123;'first_name'&#125;</code>, <code>&#123;'advisor_name'&#125;</code>, and <code>&#123;'booking_link'&#125;</code> as placeholders.
          </p>
          <div className="compose-footer">
            <label className="compose-checkbox">
              <input type="checkbox" checked={bulkIncludeBooking} onChange={(e) => setBulkIncludeBooking(e.target.checked)} />
              Include booking link
            </label>
            <button className="btn btn--primary" onClick={handleBulkSend} disabled={bulkSending || !bulkMessage.trim()}>
              {bulkSending ? 'Sending…' : `Send to ${selectedCount} leads`}
            </button>
          </div>
          {bulkResult && (
            <div className="bulk-result mono">
              Sent: {bulkResult.sent_count} · Skipped: {bulkResult.skipped_count}
            </div>
          )}
        </section>
      )}

      <div className="workqueue-grid">
        {sections.map((section) => {
          const items = queue[section.key] || []
          return (
            <section key={section.key} className={`panel workqueue-section workqueue-section--${section.accent}`}>
              <div className="panel-header">
                <div>
                  <h2 className="panel-title">{section.title}</h2>
                  <p className="workqueue-section-subtitle">{section.subtitle}</p>
                </div>
                <span className={`workqueue-count workqueue-count--${section.accent}`}>{loading ? '–' : items.length}</span>
              </div>

              {loading ? (
                <div className="empty-state">Loading {section.title.toLowerCase()}...</div>
              ) : items.length === 0 ? (
                <div className="empty-state">{section.empty}</div>
              ) : (
                <div className="workqueue-list">
                  {items.map((item) => (
                    <QueueItem
                      key={`${section.key}-${item.reply_id || item.cadence_state_id || item.lead_id}`}
                      item={item}
                      sectionKey={section.key}
                      accent={section.accent}
                      selected={selected.has(item.lead_id)}
                      onToggle={handleToggle}
                      onOpen={openLead}
                      lastSelectedRef={lastSelectedRefs[section.key]}
                      allItems={items}
                    />
                  ))}
                </div>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
