import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  CartesianGrid, Line, LineChart, Pie, PieChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from '../api/client'
import StatCard from '../components/StatCard'
import '../styles/shared.css'
import './Replies.css'

const CLASSIFICATION_CONFIG = {
  interested:    { label: 'Hot Lead',             color: 'green' },
  callback:      { label: 'Callback Requested',   color: 'blue' },
  question:      { label: 'Question',             color: 'purple' },
  not_interested:{ label: 'Not Interested',       color: 'amber' },
  wrong_number:  { label: 'Wrong Number',         color: 'neutral-dim' },
  dnc:           { label: 'DNC',                  color: 'red' },
  neutral:       { label: 'Neutral',              color: 'neutral' },
}

const CLASSIFICATION_OPTIONS = [
  { value: 'interested',     label: 'Hot Lead' },
  { value: 'callback',       label: 'Callback Requested' },
  { value: 'question',       label: 'Question' },
  { value: 'neutral',        label: 'Neutral' },
  { value: 'not_interested', label: 'Not Interested' },
  { value: 'wrong_number',   label: 'Wrong Number' },
  { value: 'dnc',            label: 'DNC' },
]

// Objection type display config
const OBJECTION_CONFIG = {
  not_interested:   { label: 'Not Interested',    color: 'amber',  icon: '🚫' },
  need_to_think:    { label: 'Needs More Time',   color: 'blue',   icon: '🤔' },
  too_expensive:    { label: 'Price Concern',     color: 'red',    icon: '💰' },
  wrong_time:       { label: 'Wrong Timing',      color: 'purple', icon: '⏳' },
  callback_request: { label: 'Wants Callback',    color: 'blue',   icon: '📞' },
  question:         { label: 'Has a Question',    color: 'purple', icon: '❓' },
  already_have:     { label: 'Already Covered',   color: 'neutral',icon: '✓' },
  interested:       { label: 'Interested',        color: 'green',  icon: '🔥' },
  general:          { label: 'General Reply',     color: 'neutral', icon: '💬' },
}

const BUCKET_CARDS = [
  { key: 'needs_follow_up', label: 'Needs follow-up', accent: 'red' },
  { key: 'hot',             label: 'Hot replies',      accent: 'green' },
  { key: 'callback',        label: 'Callbacks',        accent: 'blue' },
  { key: 'question',        label: 'Questions',        accent: 'purple' },
  { key: 'not_interested',  label: 'Not interested',   accent: 'amber' },
  { key: 'dnc',             label: 'DNC / stop',       accent: 'red' },
  { key: 'reviewed',        label: 'Reviewed',         accent: 'neutral' },
]

// ── AI Objection Suggestion Panel ─────────────────────────────────────────
function ObjectionSuggestion({ replyId, leadId, replyBody, onUseResponse }) {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [tone, setTone] = useState('standard')

  async function fetchSuggestion() {
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const data = await api.post(`/ai/objection-reply/${replyId}`, { tone })
      setResult(data)
    } catch (err) {
      setError(err.message || 'Could not generate suggestion.')
    } finally {
      setLoading(false)
    }
  }

  function handleCopy() {
    if (!result?.suggested_response) return
    navigator.clipboard.writeText(result.suggested_response).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  function handleUse() {
    if (!result?.suggested_response) return
    onUseResponse(result.suggested_response)
  }

  const objConfig = result ? (OBJECTION_CONFIG[result.objection_type] || OBJECTION_CONFIG.general) : null

  return (
    <div className="objection-panel">
      <div className="objection-panel-header">
        <span className="objection-panel-title">🤖 AI Objection Response</span>
        <div className="objection-controls">
          <select
            className="objection-tone-select"
            value={tone}
            onChange={e => setTone(e.target.value)}
            disabled={loading}
          >
            <option value="soft">Soft</option>
            <option value="standard">Standard</option>
            <option value="direct">Direct</option>
            <option value="urgent">Urgent</option>
          </select>
          <button
            className="btn btn--secondary objection-generate-btn"
            onClick={fetchSuggestion}
            disabled={loading}
          >
            {loading ? 'Analyzing…' : result ? 'Regenerate' : 'Suggest response'}
          </button>
        </div>
      </div>

      {error && <div className="objection-error">{error}</div>}

      {result && (
        <div className="objection-result">
          <div className="objection-type-row">
            <span className="objection-type-icon">{objConfig.icon}</span>
            <span className={`objection-type-pill objection-type-pill--${objConfig.color}`}>
              {objConfig.label}
            </span>
            {result.confidence && (
              <span className="objection-confidence">
                {Math.round(result.confidence * 100)}% confident
              </span>
            )}
          </div>

          {result.objection_reasoning && (
            <p className="objection-reasoning">{result.objection_reasoning}</p>
          )}

          <div className="objection-response-box">
            <p className="objection-response-text">{result.suggested_response}</p>
          </div>

          {result.talking_points && result.talking_points.length > 0 && (
            <div className="objection-talking-points">
              <span className="objection-talking-label">Key points:</span>
              <ul>
                {result.talking_points.map((point, i) => (
                  <li key={i}>{point}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="objection-actions">
            <button className="btn btn--secondary" onClick={handleCopy}>
              {copied ? '✓ Copied' : 'Copy'}
            </button>
            <button className="btn btn--primary" onClick={handleUse}>
              Use this →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Replies() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [replies, setReplies] = useState([])
  const [counts, setCounts] = useState(null)
  const [activeBucket, setActiveBucket] = useState(
    searchParams.get('needs_attention') === 'true' || searchParams.get('hot_only') === 'true'
      ? 'needs_follow_up'
      : null
  )
  const [loading, setLoading] = useState(true)
  const [actionBusyId, setActionBusyId] = useState(null)
  const [error, setError] = useState('')
  const [certificationByLead, setCertificationByLead] = useState({})
  const [replyActivity, setReplyActivity] = useState([])

  // Track which reply cards have the objection panel open
  const [objectionOpenId, setObjectionOpenId] = useState(null)
  // Track pending response to navigate to lead compose
  const [pendingResponse, setPendingResponse] = useState(null)

  function loadActivity() {
    api.get('/sms/replies/activity-by-day?days=7').then((data) => setReplyActivity(data || [])).catch(() => setReplyActivity([]))
  }

  function loadCounts() {
    api.get('/sms/replies/counts').then(setCounts).catch(() => {})
  }

  function loadReplies() {
    setLoading(true)
    setError('')
    const query = activeBucket ? `?bucket=${activeBucket}` : ''
    api.get(`/sms/replies${query}`)
      .then((data) => {
        setReplies(data)
        const uniqueLeadIds = [...new Set(data.map((r) => r.lead_id).filter(Boolean))]
        if (uniqueLeadIds.length > 0) {
          api.get(`/sms/replies/certification-batch?lead_ids=${uniqueLeadIds.join(',')}`)
            .then(setCertificationByLead)
            .catch(() => setCertificationByLead({}))
        } else {
          setCertificationByLead({})
        }
      })
      .catch((err) => setError(err.message || 'Could not load replies.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadCounts()
    loadReplies()
    loadActivity()
  }, [activeBucket])

  function updateReplyInState(updatedReply) {
    setReplies((current) =>
      current.map((reply) => (reply.id === updatedReply.id ? { ...reply, ...updatedReply } : reply))
    )
  }

  async function markReviewed(replyId) {
    setActionBusyId(replyId)
    setError('')
    try {
      const updatedReply = await api.patch(`/sms/replies/${replyId}/mark-reviewed`, {})
      updateReplyInState(updatedReply)
      loadCounts()
    } catch (err) {
      setError(err.message || 'Could not mark reply reviewed.')
    } finally {
      setActionBusyId(null)
    }
  }

  async function reclassify(replyId, classification) {
    setActionBusyId(replyId)
    setError('')
    try {
      const updatedReply = await api.patch(`/sms/replies/${replyId}/reclassify`, { classification })
      updateReplyInState(updatedReply)
      loadCounts()
    } catch (err) {
      setError(err.message || 'Could not reclassify reply.')
    } finally {
      setActionBusyId(null)
    }
  }

  function handleQuickDnc(replyId) {
    const confirmed = window.confirm(
      "Flag this as DNC? This stops any active cadence and blocks this lead's phone number from all future sends across the org."
    )
    if (!confirmed) return
    reclassify(replyId, 'dnc')
  }

  function toggleBucket(key) {
    setActiveBucket((current) => (current === key ? null : key))
  }

  function handleUseResponse(leadId, responseText) {
    // Navigate to lead detail with the suggested response pre-filled
    // We store it in sessionStorage so LeadDetail can pick it up
    if (responseText && leadId) {
      sessionStorage.setItem(`pending_response_${leadId}`, responseText)
      navigate(`/leads/${leadId}`)
    }
  }

  const activeBucketLabel = BUCKET_CARDS.find((c) => c.key === activeBucket)?.label

  const chartTooltipStyle = {
    background: 'rgba(7, 14, 32, 0.96)',
    border: '1px solid rgba(86, 200, 255, 0.42)',
    borderRadius: 12,
    color: '#eef5ff',
    boxShadow: '0 20px 60px rgba(0, 0, 0, 0.45)',
  }

  const formatActivityDate = (value) => {
    const date = new Date(`${value}T00:00:00`)
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }

  const breakdownData = counts ? [
    { name: 'Needs follow-up', value: counts.needs_follow_up || 0, color: 'var(--signal-red)' },
    { name: 'Hot',             value: counts.hot || 0,             color: 'var(--signal-green)' },
    { name: 'Callback',        value: counts.callback || 0,        color: 'var(--signal-blue)' },
    { name: 'Question',        value: counts.question || 0,        color: 'var(--signal-purple)' },
    { name: 'Not interested',  value: counts.not_interested || 0,  color: 'var(--signal-amber)' },
    { name: 'DNC',             value: counts.dnc || 0,             color: 'var(--signal-red)' },
    { name: 'Reviewed',        value: counts.reviewed || 0,        color: 'var(--text-tertiary)' },
  ].filter((entry) => entry.value > 0) : []
  const breakdownTotal = breakdownData.reduce((sum, entry) => sum + entry.value, 0)

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Replies</h1>
          <p className="page-subtitle">
            {activeBucketLabel ? `Showing: ${activeBucketLabel}` : 'Every reply from your leads, newest first.'}
          </p>
        </div>
        {activeBucket && (
          <button className="btn btn--secondary" onClick={() => setActiveBucket(null)}>Clear filter</button>
        )}
      </header>

      <div className="reply-scorecard-grid">
        {BUCKET_CARDS.map((card) => (
          <button
            key={card.key}
            type="button"
            className={`reply-scorecard-btn ${activeBucket === card.key ? 'reply-scorecard-btn--active' : ''}`}
            onClick={() => toggleBucket(card.key)}
          >
            <StatCard label={card.label} value={counts ? counts[card.key] : '—'} accent={card.accent} />
          </button>
        ))}
      </div>

      {error && <div className="panel reply-error">{error}</div>}

      <div className="reply-chart-grid">
        <article className="panel reply-chart-panel reply-chart-panel--wide">
          <div className="panel-header">
            <h2 className="panel-title">Reply activity</h2>
            <span className="chart-subtitle-inline">Last 7 days</span>
          </div>
          <div className="chart-frame chart-frame--line chart-frame--compact">
            {replyActivity.length === 0 ? (
              <div className="empty-state">No reply activity data available yet.</div>
            ) : (
              <ResponsiveContainer width="100%" height={170}>
                <LineChart data={replyActivity} margin={{ top: 16, right: 18, left: -18, bottom: 2 }}>
                  <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="4 8" vertical={false} />
                  <XAxis dataKey="date" tickFormatter={formatActivityDate} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} minTickGap={18} />
                  <YAxis allowDecimals={false} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} width={36} />
                  <Tooltip contentStyle={chartTooltipStyle} labelFormatter={formatActivityDate} />
                  <Line type="monotone" dataKey="count" name="Replies" stroke="var(--signal-blue)" strokeWidth={3} dot={{ r: 3, strokeWidth: 2 }} activeDot={{ r: 6 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </article>

        <article className="panel reply-chart-panel">
          <div className="panel-header">
            <h2 className="panel-title">Reply breakdown</h2>
          </div>
          <div className="chart-frame chart-frame--donut chart-frame--compact">
            {breakdownData.length === 0 ? (
              <div className="empty-state">No replies yet.</div>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={140}>
                  <PieChart>
                    <Pie data={breakdownData} dataKey="value" nameKey="name" innerRadius="60%" outerRadius="86%" paddingAngle={4}>
                      {breakdownData.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                    </Pie>
                    <Tooltip contentStyle={chartTooltipStyle} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="chart-legend chart-legend--compact">
                  {breakdownData.map((entry) => (
                    <span key={entry.name} className="chart-legend-item">
                      <span className="chart-legend-dot" style={{ background: entry.color }} />
                      {entry.name}: {entry.value} ({breakdownTotal > 0 ? Math.round((entry.value / breakdownTotal) * 100) : 0}%)
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        </article>

        {counts && (counts.needs_follow_up > 0 || counts.hot > 0) && (
          <article className="panel reply-insight-panel reply-chart-panel--wide">
            <div className="panel-header">
              <h2 className="panel-title">Today's focus</h2>
            </div>
            <ul className="reply-insight-list">
              {counts.needs_follow_up > 0 && (
                <li><button className="reply-insight-line" onClick={() => setActiveBucket('needs_follow_up')}>{counts.needs_follow_up} {counts.needs_follow_up === 1 ? 'reply needs' : 'replies need'} follow-up right now.</button></li>
              )}
              {counts.hot > 0 && (
                <li><button className="reply-insight-line" onClick={() => setActiveBucket('hot')}>{counts.hot} {counts.hot === 1 ? 'hot reply is' : 'hot replies are'} waiting on you.</button></li>
              )}
              {counts.callback > 0 && (
                <li><button className="reply-insight-line" onClick={() => setActiveBucket('callback')}>{counts.callback} {counts.callback === 1 ? 'lead' : 'leads'} requested a callback.</button></li>
              )}
            </ul>
          </article>
        )}
      </div>

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading replies…</div>
        ) : replies.length === 0 ? (
          <div className="empty-state">
            {activeBucket ? `Nothing in "${activeBucketLabel}" right now.` : "No replies yet. Once a lead responds, it'll land here."}
          </div>
        ) : (
          <ul className="reply-feed">
            {replies.map((r) => {
              const config = CLASSIFICATION_CONFIG[r.classification] || CLASSIFICATION_CONFIG.neutral
              const isBusy = actionBusyId === r.id
              const reviewed = Boolean(r.reviewed_at)
              const certification = r.lead_id ? certificationByLead[r.lead_id] : null
              const objectionOpen = objectionOpenId === r.id

              return (
                <li
                  key={r.id}
                  className={`reply-card ${r.is_hot ? 'reply-card--hot' : ''}`}
                  onClick={() => r.lead_id && navigate(`/leads/${r.lead_id}`)}
                  style={{ cursor: r.lead_id ? 'pointer' : 'default' }}
                >
                  <div className="reply-card-top">
                    <span className={`badge badge--${config.color}`}>{config.label}</span>
                    {certification?.is_certified && (
                      <span className="badge badge--green" title="Solicited, contacted, booked, and confirmed">✓ Certified appointment</span>
                    )}
                    {certification?.current_step === 'booked' && (
                      <span className="badge badge--amber" title="Booked but not yet confirmed">Booked — needs confirmation</span>
                    )}
                    <span className="reply-time mono">{new Date(r.received_at).toLocaleString()}</span>
                  </div>

                  <p className="reply-card-body">{r.body}</p>

                  <div className="reply-actions" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="reply-action-button"
                      disabled={isBusy || reviewed}
                      onClick={() => markReviewed(r.id)}
                    >
                      {reviewed ? 'Reviewed' : 'Mark reviewed'}
                    </button>

                    {r.classification !== 'dnc' && (
                      <button
                        type="button"
                        className="reply-action-button reply-action-button--danger"
                        disabled={isBusy}
                        onClick={() => handleQuickDnc(r.id)}
                        title="One click if this is a stop/do-not-contact request the system missed"
                      >
                        Mark DNC
                      </button>
                    )}

                    <label className="reply-reclassify">
                      <span>Reclassify</span>
                      <select
                        value={r.classification || 'neutral'}
                        disabled={isBusy}
                        onChange={(e) => reclassify(r.id, e.target.value)}
                      >
                        {CLASSIFICATION_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>

                    {/* AI Objection button — only for replies that aren't already DNC or reviewed */}
                    {r.classification !== 'dnc' && (
                      <button
                        type="button"
                        className={`reply-action-button reply-action-button--ai ${objectionOpen ? 'reply-action-button--ai-active' : ''}`}
                        onClick={() => setObjectionOpenId(objectionOpen ? null : r.id)}
                      >
                        🤖 {objectionOpen ? 'Hide AI' : 'AI response'}
                      </button>
                    )}
                  </div>

                  {/* Objection suggestion panel — expands inline */}
                  {objectionOpen && (
                    <div onClick={(e) => e.stopPropagation()}>
                      <ObjectionSuggestion
                        replyId={r.id}
                        leadId={r.lead_id}
                        replyBody={r.body}
                        onUseResponse={(text) => handleUseResponse(r.lead_id, text)}
                      />
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
