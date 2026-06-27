import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import StatCard from '../components/StatCard'
import '../styles/shared.css'
import '../components/StatusBadge.css'
import './AutoSendQueue.css'

/**
 * Phase 1 (training-wheels) review queue for the auto-send feature -
 * per the explicit, careful design agreed on: nothing here ever sends
 * without an advisor reading the AI draft and explicitly confirming,
 * editing, or declining it. There is no "send all" or bulk-confirm
 * action anywhere on this page, on purpose - every candidate gets a
 * real, individual look.
 */
export default function AutoSendQueue() {
  const navigate = useNavigate()
  const [candidates, setCandidates] = useState([])
  const [history, setHistory] = useState([])
  const [view, setView] = useState('queue') // 'queue' | 'history'
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editedBody, setEditedBody] = useState('')
  const [error, setError] = useState('')

  function load() {
    setLoading(true)
    setError('')
    Promise.all([
      api.get('/auto-send/queue').catch(() => []),
      api.get('/auto-send/history').catch(() => []),
    ]).then(([queueData, historyData]) => {
      setCandidates(queueData)
      setHistory(historyData)
    }).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleConfirm(candidateId) {
    setBusyId(candidateId)
    setError('')
    try {
      await api.post(`/auto-send/queue/${candidateId}/confirm`, {})
      load()
    } catch (err) {
      setError(err.message || 'Could not send this reply.')
    } finally {
      setBusyId(null)
    }
  }

  function startEditing(candidate) {
    setEditingId(candidate.candidate_id)
    setEditedBody(candidate.ai_drafted_body)
  }

  async function handleEditAndSend(candidateId) {
    setBusyId(candidateId)
    setError('')
    try {
      await api.post(`/auto-send/queue/${candidateId}/edit-and-send`, { body: editedBody })
      setEditingId(null)
      load()
    } catch (err) {
      setError(err.message || 'Could not send this reply.')
    } finally {
      setBusyId(null)
    }
  }

  async function handleOverride(candidateId) {
    setBusyId(candidateId)
    setError('')
    try {
      await api.post(`/auto-send/queue/${candidateId}/override`, {})
      load()
    } catch (err) {
      setError(err.message || 'Could not decline this draft.')
    } finally {
      setBusyId(null)
    }
  }

  const STATUS_LABELS = {
    confirmed: { label: 'Sent as drafted', color: 'green' },
    edited_sent: { label: 'Sent (edited)', color: 'blue' },
    overridden: { label: 'Declined', color: 'neutral-dim' },
    expired: { label: 'Expired', color: 'amber' },
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Auto-send queue</h1>
          <p className="page-subtitle">
            AI drafts a reply for simple, low-stakes questions — you read it and decide. Nothing sends without you.
          </p>
        </div>
      </header>

      <div className="auto-send-scorecard-grid">
        <StatCard label="Waiting for review" value={loading ? '—' : candidates.length} accent="purple" />
        <StatCard label="Resolved" value={loading ? '—' : history.length} accent="neutral" sublabel="Last 100" />
      </div>

      <div className="auto-send-tabs">
        <button className={`tab ${view === 'queue' ? 'tab--active' : ''}`} onClick={() => setView('queue')}>
          Queue {candidates.length > 0 && `(${candidates.length})`}
        </button>
        <button className={`tab ${view === 'history' ? 'tab--active' : ''}`} onClick={() => setView('history')}>
          History
        </button>
      </div>

      {error && <div className="panel auto-send-error">{error}</div>}

      {loading ? (
        <div className="panel"><div className="empty-state">Loading…</div></div>
      ) : view === 'queue' ? (
        candidates.length === 0 ? (
          <div className="panel">
            <div className="empty-state">
              Nothing waiting right now. Eligible replies — simple scheduling questions on leads you've already heard from — will show up here for your review.
            </div>
          </div>
        ) : (
          <ul className="auto-send-list">
            {candidates.map((c) => (
              <li key={c.candidate_id} className="panel auto-send-card">
                <div className="auto-send-card-top">
                  <button className="auto-send-lead-link" onClick={() => navigate(`/leads/${c.lead_id}`)}>
                    {c.lead_name}
                  </button>
                  {c.classification_confidence && (
                    <span className="badge badge--purple">{c.classification_confidence} confidence</span>
                  )}
                </div>

                {c.eligibility_reasoning && (
                  <p className="auto-send-reasoning">Why this is eligible: {c.eligibility_reasoning}</p>
                )}

                <div className="auto-send-draft-box">
                  <span className="auto-send-draft-label">AI-drafted reply</span>
                  {editingId === c.candidate_id ? (
                    <textarea
                      className="compose-textarea"
                      rows={3}
                      value={editedBody}
                      onChange={(e) => setEditedBody(e.target.value)}
                      autoFocus
                    />
                  ) : (
                    <p className="auto-send-draft-text">{c.ai_drafted_body || '(No draft available — write a reply yourself.)'}</p>
                  )}
                </div>

                <div className="auto-send-card-actions">
                  {editingId === c.candidate_id ? (
                    <>
                      <button className="btn btn--secondary" onClick={() => setEditingId(null)} disabled={busyId === c.candidate_id}>
                        Cancel
                      </button>
                      <button className="btn btn--primary" onClick={() => handleEditAndSend(c.candidate_id)} disabled={busyId === c.candidate_id || !editedBody.trim()}>
                        {busyId === c.candidate_id ? 'Sending…' : 'Send edited reply'}
                      </button>
                    </>
                  ) : (
                    <>
                      <button className="btn btn--secondary" onClick={() => handleOverride(c.candidate_id)} disabled={busyId === c.candidate_id}>
                        Decline — I'll reply myself
                      </button>
                      <button className="btn btn--secondary" onClick={() => startEditing(c)} disabled={busyId === c.candidate_id || !c.ai_drafted_body}>
                        Edit
                      </button>
                      <button className="btn btn--primary" onClick={() => handleConfirm(c.candidate_id)} disabled={busyId === c.candidate_id || !c.ai_drafted_body}>
                        {busyId === c.candidate_id ? 'Sending…' : 'Send as drafted'}
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )
      ) : history.length === 0 ? (
        <div className="panel"><div className="empty-state">No resolved candidates yet.</div></div>
      ) : (
        <ul className="auto-send-list">
          {history.map((c) => {
            const statusInfo = STATUS_LABELS[c.status] || { label: c.status, color: 'neutral' }
            return (
              <li key={c.candidate_id} className="panel auto-send-card auto-send-card--compact">
                <div className="auto-send-card-top">
                  <button className="auto-send-lead-link" onClick={() => navigate(`/leads/${c.lead_id}`)}>
                    {c.lead_name}
                  </button>
                  <span className={`badge badge--${statusInfo.color}`}>{statusInfo.label}</span>
                </div>
                {c.final_sent_body && <p className="auto-send-draft-text">{c.final_sent_body}</p>}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
