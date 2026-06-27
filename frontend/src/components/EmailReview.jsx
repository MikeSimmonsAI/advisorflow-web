import { useEffect, useState } from 'react'
import { api } from '../api/client'
import RichEmailComposer from './RichEmailComposer'
import './EmailReview.css'

/**
 * Review-before-send for email, the same pattern MessageReview.jsx
 * already gives SMS. Previously /email/send-batch sent immediately with
 * zero preview - the advisor never saw the actual subject/body before it
 * went out. This shows the drafted subject + body per lead, lets the
 * advisor edit either, exclude individuals, then confirms via
 * /email/confirm-send-batch.
 */
export default function EmailReview({ leadIds, onClose, onSent }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [excludedIds, setExcludedIds] = useState(new Set())
  const [editedSubjects, setEditedSubjects] = useState({})
  const [editedBodies, setEditedBodies] = useState({})
  const [result, setResult] = useState(null)

  useEffect(() => {
    api.post('/email/preview-batch', { lead_ids: leadIds })
      .then((data) => {
        setItems(data)
        const subjects = {}
        const bodies = {}
        const autoExcluded = new Set()
        data.forEach((item) => {
          subjects[item.lead_id] = item.draft_subject
          bodies[item.lead_id] = item.draft_body_html
          if (item.skip_reason) autoExcluded.add(item.lead_id)
        })
        setEditedSubjects(subjects)
        setEditedBodies(bodies)
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
      .map((item) => ({
        lead_id: item.lead_id,
        subject: editedSubjects[item.lead_id],
        body_html: editedBodies[item.lead_id],
      }))

    if (toSend.length === 0) {
      alert('Nothing selected to send.')
      return
    }

    setSending(true)
    try {
      const response = await api.post('/email/confirm-send-batch', { items: toSend })
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
      <div className="message-review-modal email-review-modal">
        <div className="message-review-header">
          <div>
            <h2 className="panel-title">Review emails before sending</h2>
            <p className="message-review-subtitle">
              Each lead's subject and message were drafted automatically based on their tier. Edit
              anything that needs a personal touch, uncheck anyone you don't want to email yet, then send.
            </p>
          </div>
          <button className="back-link" onClick={onClose}>Close</button>
        </div>

        {loading ? (
          <div className="empty-state">Drafting emails…</div>
        ) : result ? (
          <div className="message-review-result">
            <h3 style={{ color: 'var(--signal-green)' }}>Sent {result.sent_count} email{result.sent_count !== 1 ? 's' : ''}</h3>
            {result.failed_count > 0 && (
              <p style={{ color: 'var(--signal-red)', fontSize: 13 }}>{result.failed_count} failed to send.</p>
            )}
            {result.skipped_count > 0 && (
              <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{result.skipped_count} skipped (no email on file).</p>
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
                      <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{item.email || 'no email'}</span>
                    </div>
                    {isSkippable ? (
                      <p className="message-review-skip-reason">{item.skip_reason}</p>
                    ) : (
                      <div className="email-review-fields">
                        <input
                          className="settings-input email-review-subject-input"
                          placeholder="Subject"
                          value={editedSubjects[item.lead_id] || ''}
                          onChange={(e) => setEditedSubjects((prev) => ({ ...prev, [item.lead_id]: e.target.value }))}
                        />
                        <RichEmailComposer
                          value={editedBodies[item.lead_id] || ''}
                          onChange={(html) => setEditedBodies((prev) => ({ ...prev, [item.lead_id]: html }))}
                        />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            <div className="message-review-footer">
              <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{sendableCount} will be sent</span>
              <div style={{ flex: 1 }} />
              <button className="btn btn--primary" onClick={handleSendAll} disabled={sending || sendableCount === 0}>
                {sending ? 'Sending…' : `Send ${sendableCount} email${sendableCount !== 1 ? 's' : ''}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
