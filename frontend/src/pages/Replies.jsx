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
  interested: { label: 'Hot Lead', color: 'green' },
  callback: { label: 'Callback Requested', color: 'blue' },
  question: { label: 'Question', color: 'purple' },
  not_interested: { label: 'Not Interested', color: 'amber' },
  wrong_number: { label: 'Wrong Number', color: 'neutral-dim' },
  dnc: { label: 'DNC', color: 'red' },
  neutral: { label: 'Neutral', color: 'neutral' },
}

const CLASSIFICATION_OPTIONS = [
  { value: 'interested', label: 'Hot Lead' },
  { value: 'callback', label: 'Callback Requested' },
  { value: 'question', label: 'Question' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'not_interested', label: 'Not Interested' },
  { value: 'wrong_number', label: 'Wrong Number' },
  { value: 'dnc', label: 'DNC' },
]

// Action-center scorecards - per Mike's explicit request that Replies
// "should not just send me back to the lead sheet... it should feel
// like an action center, not just a message list." Each card's `key`
// matches exactly the field names from GET /sms/replies/counts and the
// bucket= values GET /sms/replies accepts, so clicking a card and the
// number it shows always agree with each other.
const BUCKET_CARDS = [
  { key: 'needs_follow_up', label: 'Needs follow-up', accent: 'red' },
  { key: 'hot', label: 'Hot replies', accent: 'green' },
  { key: 'callback', label: 'Callbacks', accent: 'blue' },
  { key: 'question', label: 'Questions', accent: 'purple' },
  { key: 'not_interested', label: 'Not interested', accent: 'amber' },
  { key: 'dnc', label: 'DNC / stop', accent: 'red' },
  { key: 'reviewed', label: 'Reviewed', accent: 'neutral' },
]

export default function Replies() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [replies, setReplies] = useState([])
  const [counts, setCounts] = useState(null)
  // activeBucket replaces the old single needs_attention checkbox as the
  // way to filter - null means "show everything," matching the
  // pre-existing default behavior exactly.
  const [activeBucket, setActiveBucket] = useState(
    searchParams.get('needs_attention') === 'true' || searchParams.get('hot_only') === 'true'
      ? 'needs_follow_up'
      : null
  )
  const [loading, setLoading] = useState(true)
  const [actionBusyId, setActionBusyId] = useState(null)
  const [error, setError] = useState('')
  // Certification status per lead_id - fetched once per reply LOAD, in
  // one batched call covering every distinct lead currently on screen,
  // not one call per reply. See certification_service.py's
  // get_certification_status_batch for why this matters at 200 replies.
  const [certificationByLead, setCertificationByLead] = useState({})
  // Real reply-activity history, same endpoint already proven on
  // Overview - per the redesign request for a chart on this page,
  // built from genuine data, never fabricated.
  const [replyActivity, setReplyActivity] = useState([])

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
      loadCounts() // the reviewed/needs-follow-up counts just changed
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
      loadCounts() // bucket counts just shifted
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

  // Real reply breakdown by classification - built directly from the
  // same `counts` object the scorecards already render, not a second,
  // separately-fetched (and potentially inconsistent) data source.
  const breakdownData = counts ? [
    { name: 'Needs follow-up', value: counts.needs_follow_up || 0, color: 'var(--signal-red)' },
    { name: 'Hot', value: counts.hot || 0, color: 'var(--signal-green)' },
    { name: 'Callback', value: counts.callback || 0, color: 'var(--signal-blue)' },
    { name: 'Question', value: counts.question || 0, color: 'var(--signal-purple)' },
    { name: 'Not interested', value: counts.not_interested || 0, color: 'var(--signal-amber)' },
    { name: 'DNC', value: counts.dnc || 0, color: 'var(--signal-red)' },
    { name: 'Reviewed', value: counts.reviewed || 0, color: 'var(--text-tertiary)' },
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
          <button className="btn btn--secondary" onClick={() => setActiveBucket(null)}>
            Clear filter
          </button>
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
            {/* Deliberately real numbers only, drawn directly from the
                same counts object the scorecards above use - no
                fabricated revenue/percentage projections. A version of
                this panel with a dollar-value "revenue at risk"
                estimate or a "%X booking rate increase" claim would
                require a real, defensible calculation behind it that
                doesn't exist yet; inventing one would mean shipping a
                business metric that LOOKS computed but isn't. */}
            <ul className="reply-insight-list">
              {counts.needs_follow_up > 0 && (
                <li>
                  <button className="reply-insight-line" onClick={() => setActiveBucket('needs_follow_up')}>
                    {counts.needs_follow_up} {counts.needs_follow_up === 1 ? 'reply needs' : 'replies need'} follow-up right now.
                  </button>
                </li>
              )}
              {counts.hot > 0 && (
                <li>
                  <button className="reply-insight-line" onClick={() => setActiveBucket('hot')}>
                    {counts.hot} {counts.hot === 1 ? 'hot reply is' : 'hot replies are'} waiting on you.
                  </button>
                </li>
              )}
              {counts.callback > 0 && (
                <li>
                  <button className="reply-insight-line" onClick={() => setActiveBucket('callback')}>
                    {counts.callback} {counts.callback === 1 ? 'lead' : 'leads'} requested a callback.
                  </button>
                </li>
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
            {activeBucket
              ? `Nothing in "${activeBucketLabel}" right now.`
              : "No replies yet. Once a lead responds, it'll land here."}
          </div>
        ) : (
          <ul className="reply-feed">
            {replies.map((r) => {
              const config = CLASSIFICATION_CONFIG[r.classification] || CLASSIFICATION_CONFIG.neutral
              const isBusy = actionBusyId === r.id
              const reviewed = Boolean(r.reviewed_at)
              const certification = r.lead_id ? certificationByLead[r.lead_id] : null

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
                      <span className="badge badge--green" title="Solicited, contacted, booked, and confirmed">
                        ✓ Certified appointment
                      </span>
                    )}
                    {certification?.current_step === 'booked' && (
                      <span className="badge badge--amber" title="Booked but not yet confirmed">
                        Booked — needs confirmation
                      </span>
                    )}
                    <span className="reply-time mono">{new Date(r.received_at).toLocaleString()}</span>
                  </div>
                  <p className="reply-card-body">{r.body}</p>

                  <div className="reply-actions" onClick={(event) => event.stopPropagation()}>
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
                        onChange={(event) => reclassify(r.id, event.target.value)}
                      >
                        {CLASSIFICATION_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
