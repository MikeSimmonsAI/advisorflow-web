import { useEffect, useState } from 'react'
import { api } from '../api/client'
import './MessageReview.css'

/**
 * The "AI drafts a message per lead, advisor reviews and confirms before
 * anything sends" screen Mike specifically asked for - replacing silent
 * auto-send on import. Shown right after a batch of leads is imported
 * (or whenever a caller wants to review/send a specific set of leads).
 */
export default function MessageReview({ leadIds, onClose, onSent }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [excludedIds, setExcludedIds] = useState(new Set())
  const [editedMessages, setEditedMessages] = useState({})
  const [includeBookingLink, setIncludeBookingLink] = useState(true)
  const [result, setResult] = useState(null)

  useEffect(() => {
    api.post('/leads/preview-messages', { lead_ids: leadIds })
      .then((data) => {
        setItems(data)
        // Pre-fill the editable text with each draft, and auto-exclude
        // anything that came back with a skip_reason (DNC, no phone, etc.)
        const initial = {}
        const autoExcluded = new Set()
        data.forEach((item) => {
          initial[item.lead_id] = item.draft_message
          if (item.skip_reason) autoExcluded.add(item.lead_id)
        })
        setEditedMessages(initial)
        setExcludedIds(autoExcluded)
      })
      .finally(() => setLoading(false))
  }, [leadIds])

  function toggleExclude(leadId) {
    setExcludedIds((prev) => {
      const next = new Set(prev)
      if (next.has(leadId)) next.delete(leadId)
      else next.add(leadId)
      return next
    })
  }

  async function handleSendAll() {
    const toSend = items
      .filter((item) => !excludedIds.has(item.lead_id) && !item.skip_reason)
      .map((item) => ({ lead_id: item.lead_id, message: editedMessages[item.lead_id] }))

    if (toSend.length === 0) {
      alert('Nothing selected to send.')
      return
    }

    setSending(true)
    try {
      const response = await api.post('/leads/confirm-send-batch', {
        items: toSend, include_booking_link: includeBookingLink,
      })
      setResult(response)
      onSent?.(response)
    } catch (err) {
      alert(`Send failed: ${err.message}`)
    } finally {
      setSending(false)
    }
  }

  const sendableCount = items.filter((i) => !excludedIds.has(i.lead_id) && !i.skip_reason).length

  return (
    <div className="message-review-overlay">
      <div className="message-review-modal">
        <div className="message-review-header">
          <div>
            <h2 className="panel-title">Review messages before sending</h2>
            <p className="message-review-subtitle">
              Each lead's message was drafted automatically based on their tier. Edit anything that
              needs a personal touch, uncheck anyone you don't want to message yet, then send.
            </p>
          </div>
          <button className="back-link" onClick={onClose}>Close</button>
        </div>

        {loading ? (
          <div className="empty-state">Drafting messages…</div>
        ) : result ? (
          <div className="message-review-result">
            <h3 style={{ color: 'var(--signal-green)' }}>Sent {result.sent_count} message{result.sent_count !== 1 ? 's' : ''}</h3>
            {result.skipped_count > 0 && (
              <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{result.skipped_count} skipped (see details below).</p>
            )}
            <button className="btn btn--primary" onClick={onClose}>Done</button>
          </div>
        ) : (
          <>
            <div className="message-review-list">
              {items.map((item) => {
                const isExcluded = excludedIds.has(item.lead_id)
                const isSkippable = !!item.skip_reason
                return (
                  <div key={item.lead_id} className={`message-review-card ${isExcluded || isSkippable ? 'message-review-card--excluded' : ''}`}>
                    <div className="message-review-card-top">
                      <label className="compose-checkbox">
                        <input
                          type="checkbox"
                          checked={!isExcluded && !isSkippable}
                          disabled={isSkippable}
                          onChange={() => toggleExclude(item.lead_id)}
                        />
                        <strong>{item.lead_name}</strong>
                      </label>
                      <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{item.phone || 'no phone'}</span>
                    </div>
                    {isSkippable ? (
                      <p className="message-review-skip-reason">{item.skip_reason}</p>
                    ) : (
                      <textarea
                        className="compose-textarea"
                        value={editedMessages[item.lead_id] || ''}
                        onChange={(e) => setEditedMessages((prev) => ({ ...prev, [item.lead_id]: e.target.value }))}
                        rows={3}
                      />
                    )}
                  </div>
                )
              })}
            </div>

            <div className="message-review-footer">
              <label className="compose-checkbox">
                <input type="checkbox" checked={includeBookingLink} onChange={(e) => setIncludeBookingLink(e.target.checked)} />
                Include booking link
              </label>
              <div style={{ flex: 1 }} />
              <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{sendableCount} will be sent</span>
              <button className="btn btn--primary" onClick={handleSendAll} disabled={sending || sendableCount === 0}>
                {sending ? 'Sending…' : `Send ${sendableCount} message${sendableCount !== 1 ? 's' : ''}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
