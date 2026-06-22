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
  if (!value) return '—'
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

function QueueItem({ item, sectionKey, accent, onOpen }) {
  return (
    <button className="workqueue-item" onClick={() => onOpen(item.lead_id)}>
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
    </button>
  )
}

export default function WorkQueue() {
  const navigate = useNavigate()
  const [queue, setQueue] = useState(emptyQueue)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const total = useMemo(() => sections.reduce((sum, section) => sum + (queue[section.key]?.length || 0), 0), [queue])

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

  useEffect(() => {
    loadQueue()
  }, [])

  function openLead(leadId) {
    if (leadId) navigate(`/leads/${leadId}`)
  }

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
          <strong>{loading ? '—' : total}</strong>
          <span>open work items</span>
          <button className="btn btn--secondary" onClick={loadQueue} disabled={loading}>Refresh</button>
        </div>
      </header>

      {error ? <div className="workqueue-alert">{error}</div> : null}

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
                <span className={`workqueue-count workqueue-count--${section.accent}`}>{loading ? '—' : items.length}</span>
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
                      onOpen={openLead}
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
