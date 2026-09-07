import { useEffect, useState } from 'react'
import { api } from '../api/client'
import RichEmailComposer from './RichEmailComposer'
import './EmailReview.css'

/**
 * Review-before-send for email — the same pattern MessageReview.jsx already
 * gives SMS. POST /email/preview-batch drafts the real subject + body per
 * lead without writing anything; the advisor edits, drops individuals, then
 * POST /email/confirm-send-batch sends exactly what was reviewed.
 *
 * TWO DIFFERENT THINGS COME BACK FROM THE PREVIEW AND THEY ARE NOT THE SAME:
 *
 *   skip_reason      This lead CANNOT be sent (qualification EXCLUDED, or no
 *                    address on file). It is never put in the confirm payload.
 *
 *   review_reasons   This lead CAN be sent, but the engine flagged it
 *                    (REVIEW_REQUIRED). Surfacing the reasons next to the
 *                    draft IS the review that bucket is asking for, which is
 *                    why /email/confirm-send-batch lets it through.
 *
 * The same distinction is honoured in the result: `blocked` (a compliance
 * refusal) is reported separately from `skipped` (not visible / no address).
 */

// The confirm endpoint returns skip reasons as stable codes, not sentences.
const SKIP_CODE_LABELS = {
  not_found: 'Not visible to you — may have been reassigned or removed',
  no_email_address: 'No email address on file',
}

function skipLabel(code) {
  return SKIP_CODE_LABELS[code] || code || 'Not sent'
}

function reasonText(reasons) {
  return (reasons || [])
    .map((r) => (typeof r === 'string' ? r : r?.label || r?.code || ''))
    .filter(Boolean)
    .join('; ')
}

export default function EmailReview({ leadIds, onClose, onSent }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const [excludedIds, setExcludedIds] = useState(new Set())
  const [editedSubjects, setEditedSubjects] = useState({})
  const [editedBodies, setEditedBodies] = useState({})
  const [result, setResult] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError('')
    api.post('/email/preview-batch', { lead_ids: leadIds })
      .then((data) => {
        if (cancelled) return
        const rows = Array.isArray(data) ? data : []
        const subjects = {}
        const bodies = {}
        rows.forEach((item) => {
          subjects[item.lead_id] = item.draft_subject || ''
          bodies[item.lead_id] = item.draft_body_html || ''
        })
        setItems(rows)
        setEditedSubjects(subjects)
        setEditedBodies(bodies)
        // excludedIds is the ADVISOR'S choice only. A skip_reason lead is not
        // "unchecked" — it is unsendable, and is filtered out separately so it
        // can never reach the confirm payload even if this set is manipulated.
        setExcludedIds(new Set())
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err?.message || 'Could not draft these emails.')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [leadIds])

  function toggleExclude(leadId) {
    setExcludedIds((prev) => {
      const next = new Set(prev)
      if (next.has(leadId)) next.delete(leadId)
      else next.add(leadId)
      return next
    })
  }

  const nameById = Object.fromEntries(items.map((i) => [i.lead_id, i.lead_name]))
  const sendableItems = items.filter((i) => !i.skip_reason && !excludedIds.has(i.lead_id))
  const sendableCount = sendableItems.length
  const skippedItems = items.filter((i) => !!i.skip_reason)
  const flaggedCount = items.filter(
    (i) => !i.skip_reason && (i.review_reasons?.length || 0) > 0
  ).length

  async function handleSendAll() {
    const toSend = sendableItems.map((item) => ({
      lead_id: item.lead_id,
      subject: editedSubjects[item.lead_id] || '',
      body_html: editedBodies[item.lead_id] || '',
    }))

    if (toSend.length === 0) return

    setSending(true)
    setSendError('')
    try {
      const response = await api.post('/email/confirm-send-batch', { items: toSend })
      setResult(response)
      onSent?.(response)
    } catch (err) {
      setSendError(err?.message || 'Send failed.')
    } finally {
      setSending(false)
    }
  }

  // ── Result view ───────────────────────────────────────────────────────────
  function renderResult() {
    const blocked = result.blocked || []
    const skipped = result.skipped || []
    return (
      <div className="email-review-result">
        <h3 className="email-review-result-headline">
          Sent {result.sent_count} email{result.sent_count !== 1 ? 's' : ''}
        </h3>

        <div className="email-review-tallies">
          <span className="email-review-tally email-review-tally--sent">
            {result.sent_count} sent
          </span>
          <span className={`email-review-tally ${result.failed_count ? 'email-review-tally--failed' : ''}`}>
            {result.failed_count} failed
          </span>
          <span className={`email-review-tally ${result.blocked_count ? 'email-review-tally--blocked' : ''}`}>
            {result.blocked_count} blocked
          </span>
          <span className={`email-review-tally ${result.skipped_count ? 'email-review-tally--skipped' : ''}`}>
            {result.skipped_count} skipped
          </span>
        </div>

        {result.failed_count > 0 && (
          <p className="email-review-result-note email-review-result-note--failed">
            {result.failed_count} email{result.failed_count !== 1 ? 's were' : ' was'} rejected by
            the mail provider. Run the Email System Check on the queue page to see why.
          </p>
        )}

        {blocked.length > 0 && (
          <div className="email-review-result-group">
            <div className="email-review-result-group-title email-review-result-group-title--blocked">
              🚫 Blocked — outreach rules refused these ({blocked.length})
            </div>
            <p className="email-review-result-note">
              These were not a delivery problem. The qualification engine refused them, most often
              because the contact is on Do Not Contact or suppression. Nothing was sent to them.
            </p>
            <ul className="email-review-result-list">
              {blocked.map((b) => (
                <li key={b.lead_id}>
                  <strong>{nameById[b.lead_id] || b.lead_id}</strong>
                  <span className="email-review-result-reason">{reasonText(b.reasons) || 'Excluded from email'}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {skipped.length > 0 && (
          <div className="email-review-result-group">
            <div className="email-review-result-group-title email-review-result-group-title--skipped">
              ⏭ Skipped — nothing to send to ({skipped.length})
            </div>
            <ul className="email-review-result-list">
              {skipped.map((s) => (
                <li key={s.lead_id}>
                  <strong>{nameById[s.lead_id] || s.lead_id}</strong>
                  <span className="email-review-result-reason">{skipLabel(s.reason)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <button className="btn btn--primary" onClick={onClose} style={{ marginTop: 18 }}>Done</button>
      </div>
    )
  }

  // ── Review list ───────────────────────────────────────────────────────────
  function renderCard(item) {
    const cannotSend = !!item.skip_reason
    const flags = item.review_reasons || []
    const isFlagged = !cannotSend && flags.length > 0
    const isExcluded = excludedIds.has(item.lead_id)
    const cardClass = [
      'message-review-card',
      cannotSend ? 'message-review-card--excluded email-review-card--blocked' : '',
      !cannotSend && isExcluded ? 'message-review-card--excluded' : '',
      isFlagged ? 'email-review-card--flagged' : '',
    ].filter(Boolean).join(' ')

    return (
      <div key={item.lead_id} className={cardClass}>
        <div className="message-review-card-top">
          <label className="compose-checkbox">
            <input
              type="checkbox"
              checked={!cannotSend && !isExcluded}
              disabled={cannotSend}
              onChange={() => toggleExclude(item.lead_id)}
            />
            <strong>{item.lead_name}</strong>
          </label>
          <span className="email-review-card-meta">
            {item.tier && <span className="tier-chip">{item.tier}</span>}
            <span className="mono">{item.email || 'no email'}</span>
          </span>
        </div>

        {cannotSend ? (
          <p className="email-review-blocked-reason">
            <span className="email-review-badge email-review-badge--blocked">Cannot send</span>
            {item.skip_reason}
          </p>
        ) : (
          <>
            {isFlagged && (
              <div className="email-review-flags">
                <span className="email-review-badge email-review-badge--flagged">Needs review</span>
                <span className="email-review-flags-text">
                  This one can be sent, but read it before you do — {reasonText(flags)}
                </span>
              </div>
            )}
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
          </>
        )}
      </div>
    )
  }

  return (
    <div className="message-review-overlay">
      <div className="message-review-modal email-review-modal">
        <div className="message-review-header">
          <div>
            <h2 className="panel-title">Review emails before sending</h2>
            <p className="message-review-subtitle">
              Each lead's subject and message were drafted from their tier and message track.
              Nothing has been sent and nothing has been written yet. Edit anything that needs a
              personal touch, uncheck anyone you don't want to email, then send.
            </p>
          </div>
          <button className="back-link" onClick={onClose}>Close</button>
        </div>

        {loading ? (
          <div className="empty-state">Drafting emails…</div>
        ) : loadError ? (
          <div className="email-review-result">
            <p className="email-review-result-note email-review-result-note--failed">⚠️ {loadError}</p>
            <button className="btn btn--primary" onClick={onClose}>Close</button>
          </div>
        ) : result ? (
          renderResult()
        ) : items.length === 0 ? (
          <div className="empty-state">
            None of the selected leads could be drafted. They may no longer be visible to you.
          </div>
        ) : (
          <>
            <div className="email-review-summary">
              <span className="email-review-summary-item">
                <strong>{sendableCount}</strong> ready to send
              </span>
              {flaggedCount > 0 && (
                <span className="email-review-summary-item email-review-summary-item--flagged">
                  <strong>{flaggedCount}</strong> flagged for review
                </span>
              )}
              {skippedItems.length > 0 && (
                <span className="email-review-summary-item email-review-summary-item--blocked">
                  <strong>{skippedItems.length}</strong> cannot be sent
                </span>
              )}
            </div>

            <div className="message-review-list">
              {items.map(renderCard)}
            </div>

            <div className="message-review-footer">
              <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {sendableCount} will be sent
                {skippedItems.length > 0 && ` · ${skippedItems.length} excluded by outreach rules`}
              </span>
              <div style={{ flex: 1 }} />
              {sendError && <span className="email-review-send-error">⚠️ {sendError}</span>}
              <button
                className="btn btn--primary"
                onClick={handleSendAll}
                disabled={sending || sendableCount === 0}
              >
                {sending ? 'Sending…' : `Send ${sendableCount} email${sendableCount !== 1 ? 's' : ''}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
