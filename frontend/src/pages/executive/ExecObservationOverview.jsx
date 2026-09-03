/**
 * ExecObservationOverview — rich executive observation dashboard.
 *
 * ONE API call: GET /executive/organizations/{orgId}/observe/overview
 * Guarded by require_brand_executive + platform isolation server-side.
 *
 * SECURITY RULES (enforced server-side, mirrored in UI):
 *   - org_id from URL path only — NEVER current_user.organization_id
 *   - NEVER mutates user.organization_id
 *   - All mutation buttons REMOVED (Import, Campaign, Send, Edit, Assign)
 *   - All workspace navigation links REMOVED (no /leads, /replies, /cadence etc.)
 *   - display-only: no onClick handlers that navigate into workspace routes
 *
 * Visual parity with Overview.jsx using shared ../Overview.css classes.
 */

import { useState, useEffect } from 'react'
import { api } from '../../api/client'
import '../Overview.css'

// ── Industry-aware labels (mirrors Overview.jsx) ──────────────────────────────
const INDUSTRY_LABELS = {
  funeral:      { appointments: 'Arrangements', bookingRate: 'Arrangement rate', bookedSub: 'Booked arrangements', recordedVisits: 'Recorded visits' },
  fiber:        { appointments: 'Installs',     bookingRate: 'Install rate',     bookedSub: 'Scheduled installs',  recordedVisits: 'Completed installs' },
  solar:        { appointments: 'Assessments',  bookingRate: 'Assessment rate',  bookedSub: 'Scheduled assessments', recordedVisits: 'Completed assessments' },
  roofing:      { appointments: 'Inspections',  bookingRate: 'Inspection rate',  bookedSub: 'Scheduled inspections', recordedVisits: 'Completed inspections' },
  insurance:    { appointments: 'Consultations', bookingRate: 'Consultation rate', bookedSub: 'Booked consultations', recordedVisits: 'Completed consultations' },
  real_estate:  { appointments: 'Showings',     bookingRate: 'Showing rate',     bookedSub: 'Scheduled showings',  recordedVisits: 'Completed showings' },
  home_services:{ appointments: 'Appointments', bookingRate: 'Booking rate',     bookedSub: 'Booked appointments', recordedVisits: 'Completed appointments' },
  sales:        { appointments: 'Demos',        bookingRate: 'Demo rate',        bookedSub: 'Scheduled demos',     recordedVisits: 'Completed demos' },
}
const DEFAULT_LABELS = INDUSTRY_LABELS.funeral

const STAGE_TONE = {
  new: 'var(--signal-amber)', sent: 'var(--signal-blue)', replied: 'var(--signal-blue)',
  hot: 'var(--signal-red)', booked: 'var(--signal-green)',
}

const STATUS_PILL = {
  new: 'gold', sent: 'blue', replied: 'blue', hot: 'red',
  booked: 'teal', dnc: 'off',
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '—'
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase()
}

function ago(iso) {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms)) return null
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return m + 'm'
  const h = Math.floor(m / 60)
  if (h < 24) return h + 'h'
  const d = Math.floor(h / 24)
  return d + 'd'
}

function num(n) {
  return n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US')
}

function useObserveData(orgId) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    if (!orgId) return
    setState({ loading: true, data: null, error: null })
    api.get(`/executive/organizations/${orgId}/observe/overview`)
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(err => {
        const msg = err?.status === 403
          ? 'Executive access denied for this organization.'
          : err?.status === 404
          ? 'Organization not found under your brand.'
          : 'Could not load observation data. Please try again.'
        setState({ loading: false, data: null, error: msg })
      })
  }, [orgId])
  return state
}

export default function ExecObservationOverview({ orgId, orgName }) {
  const { loading, data, error } = useObserveData(orgId)
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  if (loading) {
    return (
      <div className="ov-page">
        <div className="empty-state" style={{ marginTop: 60 }}>Loading organization data…</div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="ov-page">
        <div className="ov-load-error" role="alert" style={{
          margin: '32px 0 0', padding: '14px 18px', borderRadius: 10,
          background: 'rgba(240,80,80,0.12)',
          border: '1px solid rgba(240,80,80,0.35)', fontSize: 13.5,
        }}>
          <strong>Cannot load observation data.</strong>{' '}{error}
        </div>
      </div>
    )
  }

  const { lead_summary, funnel = [], hot_replies = [], leads_needing_action = [], recent_activity = [] } = data
  const IL = DEFAULT_LABELS   // industry not exposed in executive endpoint; default to funeral

  const totalLeads    = lead_summary?.total_leads ?? null
  const dncCount      = lead_summary?.dnc_count ?? null
  const bookedCount   = lead_summary?.booked ?? 0
  const newCount      = lead_summary?.new ?? 0
  const sentCount     = lead_summary?.sent ?? 0
  const hotCount      = lead_summary?.hot ?? 0
  const replyRate     = lead_summary?.reply_rate ?? null
  const bookingRate   = lead_summary?.booking_rate ?? null
  const cadenceTouches = data.briefing?.cadence_touches ?? 0
  const leads24h      = data.briefing?.leads_last_24h ?? 0
  const bookings7d    = data.briefing?.bookings_7d ?? 0
  const apptsWaiting  = data.briefing?.appts_waiting ?? 0

  // ── Attention items (READ-ONLY — no navigation to workspace) ─────────────
  const attention = []
  if (hot_replies.length > 0) attention.push({
    key: 'hot', tone: 'var(--signal-red)',
    title: `${hot_replies.length} hot ${hot_replies.length === 1 ? 'reply needs' : 'replies need'} a human response`,
    sub: 'High-intent contacts waiting on qualification or scheduling.',
  })
  if (cadenceTouches > 0) attention.push({
    key: 'cadence', tone: 'var(--signal-amber)',
    title: `${cadenceTouches} cadence ${cadenceTouches === 1 ? 'touch is' : 'touches are'} due today`,
    sub: 'Scheduled follow-ups that have reached their send time.',
  })
  if (newCount > 0) attention.push({
    key: 'new', tone: 'var(--signal-blue)',
    title: `${num(newCount)} ${newCount === 1 ? 'lead has' : 'leads have'} never been contacted`,
    sub: leads24h ? `${num(leads24h)} of them arrived in the last 24 hours.` : 'Still at status "new".',
  })
  if (apptsWaiting > 0) attention.push({
    key: 'appts', tone: 'var(--signal-green)',
    title: `${apptsWaiting} appointments confirmed`,
    sub: 'Booked or confirmed, with no outcome recorded yet.',
  })

  const kpis = [
    { label: 'Total leads',       value: num(totalLeads),  color: 'var(--signal-blue)',   trend: leads24h ? `+${num(leads24h)} in 24h` : 'all lists' },
    { label: 'New / unworked',    value: num(newCount),    color: 'var(--signal-amber)',  trend: newCount > 0 ? 'needs attention' : 'nothing waiting' },
    { label: 'Hot replies',       value: num(hot_replies.length), color: 'var(--signal-red)',  trend: hot_replies.length > 0 ? 'awaiting decision' : 'inbox clear' },
    { label: IL.appointments,     value: num(bookedCount), color: 'var(--signal-green)',  trend: IL.bookedSub },
    { label: 'Reply rate',        value: replyRate === null ? '—' : replyRate + '%', color: 'var(--signal-purple)', trend: sentCount > 0 ? `of ${num(sentCount)} contacted` : 'nothing sent yet' },
    { label: IL.bookingRate,      value: bookingRate === null ? '—' : bookingRate + '%', color: 'var(--signal-green)', trend: sentCount > 0 ? `of ${num(sentCount)} contacted` : 'nothing sent yet' },
    { label: 'Cadence touches',   value: num(cadenceTouches), color: 'var(--signal-amber)', trend: 'due today' },
    { label: 'DNC / opted out',   value: num(dncCount),   color: 'var(--text-secondary)', trend: 'suppression active' },
  ]

  return (
    <div className="ov-page">

      {/* ── hero (no search bar, no action buttons) ── */}
      <div className="ov-hero">
        <div>
          <div className="ov-greeting">Observing: {orgName}</div>
          <div className="ov-sub">
            {attention.length
              ? `${attention.length} thing${attention.length === 1 ? '' : 's'} need attention right now.`
              : 'No immediate attention items. Here is the current pipeline state.'}
          </div>
        </div>
        <div className="ov-datebox">
          <div className="ov-clock">
            {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
          <div className="ov-date">
            {now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}
          </div>
        </div>
      </div>

      {/* ── quick row (display only, no click handlers) ── */}
      <div className="ov-quick-row">
        <div className="ov-quick" style={{ cursor: 'default' }}>
          <strong>{num(hot_replies.length)}</strong> replies awaiting review
        </div>
        <div className="ov-quick" style={{ cursor: 'default' }}>
          <strong>{num(cadenceTouches)}</strong> touches due today
        </div>
        <div className="ov-quick" style={{ cursor: 'default' }}>
          <strong>{num(newCount)}</strong> leads never contacted
        </div>
        <div className="ov-quick" style={{ cursor: 'default' }}>
          <strong>{num(bookings7d)}</strong> {IL.appointments.toLowerCase()} this week
        </div>
      </div>

      {/* ── KPI cards (display only, no navigation) ── */}
      <div className="ov-kpis">
        {kpis.map(k => (
          <div key={k.label} className="ov-kpi" style={{ cursor: 'default' }}>
            <span className="ov-kpi-label">{k.label}</span>
            <span className="ov-kpi-value" style={{ color: k.color }}>{k.value}</span>
            <span className="ov-kpi-trend">{k.trend}</span>
          </div>
        ))}
      </div>

      {/* ── attention + hot replies ── */}
      <div className="ov-grid">
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">What needs attention now</h2>
            <span className="panel-count">{attention.length}</span>
          </div>
          {attention.length === 0 ? (
            <div className="empty-state">
              Nothing is waiting on a person right now — no unanswered hot replies,
              no overdue touches, no untouched leads.
            </div>
          ) : (
            <div className="ov-queue">
              {attention.map(a => (
                /* READ-ONLY: no onClick, no navigation */
                <div key={a.key} className="ov-queue-item" style={{ cursor: 'default' }}>
                  <span className="ov-queue-dot" style={{ background: a.tone }} />
                  <span>
                    <span className="ov-queue-title">{a.title}</span>
                    <span className="ov-queue-sub">{a.sub}</span>
                  </span>
                  <span className="ov-queue-go" style={{ color: 'var(--text-secondary)', opacity: 0.4 }}>
                    view only
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">Hot replies</h2>
            <span className="panel-count">{hot_replies.length}</span>
          </div>
          {hot_replies.length === 0 ? (
            <div className="empty-state">No replies are waiting on a decision.</div>
          ) : (
            <>
              {hot_replies.slice(0, 5).map(r => (
                /* READ-ONLY: no onClick, no navigation to workspace */
                <div key={r.id} className="ov-reply" style={{ cursor: 'default' }}>
                  <span className="ov-reply-avatar">{initials(r.lead_name)}</span>
                  <span style={{ minWidth: 0 }}>
                    <span className="ov-reply-name">
                      {r.lead_name}{ago(r.received_at) ? ` · ${ago(r.received_at)} ago` : ''}
                    </span>
                    <span className="ov-reply-meta">
                      {(r.source || 'sms').toUpperCase()}
                      {r.classification ? ` · ${String(r.classification).replace(/_/g, ' ')}` : ''}
                      {r.is_hot ? ' · hot' : ''}
                    </span>
                    <p className="ov-reply-body">
                      {String(r.body || '').slice(0, 160)}
                      {String(r.body || '').length > 160 ? '…' : ''}
                    </p>
                  </span>
                </div>
              ))}
              {hot_replies.length > 5 && (
                <div style={{ padding: '10px 0', fontSize: 13, color: 'var(--text-secondary)' }}>
                  + {hot_replies.length - 5} more hot replies (view only)
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {/* ── lead flow ── */}
      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Lead flow</h2>
          <span className="panel-count">{num(totalLeads)}</span>
        </div>
        {funnel.length === 0 ? (
          <div className="empty-state">No pipeline data yet.</div>
        ) : (
          <div className="ov-pipeline">
            {funnel.map(s => (
              /* READ-ONLY: display div, not button */
              <div key={s.status} className="ov-stage" style={{ cursor: 'default' }}>
                <span className="ov-stage-label">{s.label}</span>
                <span className="ov-stage-value" style={{ color: STAGE_TONE[s.status] }}>
                  {num(s.count)}
                </span>
                <span className="ov-stage-sub">
                  {totalLeads ? Math.round((s.count / totalLeads) * 1000) / 10 + '% of all leads' : ''}
                </span>
              </div>
            ))}
            <div className="ov-stage" style={{ cursor: 'default' }}>
              <span className="ov-stage-label">DNC</span>
              <span className="ov-stage-value" style={{ color: 'var(--text-secondary)' }}>
                {num(dncCount)}
              </span>
              <span className="ov-stage-sub">suppressed</span>
            </div>
          </div>
        )}
        <p className="ov-note">
          Executive observation view — read only. Real lead statuses for {orgName}.
        </p>
      </section>

      {/* ── leads needing action + activity ── */}
      <div className="ov-grid">
        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">Leads needing action</h2>
            {/* READ-ONLY: no "Open Leads →" button */}
          </div>
          {leads_needing_action.length === 0 ? (
            <div className="empty-state">No leads are in a state requiring action.</div>
          ) : (
            <div className="ov-tablewrap">
              <table className="ov-table">
                <thead>
                  <tr>
                    <th>Lead</th><th>Source</th><th>Status</th><th>Last touch</th>
                  </tr>
                </thead>
                <tbody>
                  {leads_needing_action.slice(0, 8).map(l => (
                    /* READ-ONLY: no onClick navigation */
                    <tr key={l.id}>
                      <td>
                        <strong>
                          {[l.first_name, l.last_name].filter(Boolean).join(' ')
                            || l.phone || l.email || 'Unnamed lead'}
                        </strong>
                      </td>
                      <td style={{ color: 'var(--text-secondary)' }}>
                        {l.import_list_name || l.source_file || '—'}
                      </td>
                      <td>
                        <span className={'ov-pill ov-pill--' + (STATUS_PILL[l.status] || 'off')}>
                          {String(l.status || '—').toUpperCase()}
                        </span>
                      </td>
                      <td style={{ color: 'var(--text-secondary)' }}>
                        {ago(l.last_messaged_at) ? ago(l.last_messaged_at) + ' ago' : 'never'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="ov-note">
            Executive read-only view — no lead assignment or status changes available.
          </p>
        </section>

        <section className="panel ov-panel">
          <div className="panel-header">
            <h2 className="panel-title">Recent activity</h2>
            {/* READ-ONLY: no "All activity →" button */}
          </div>
          {recent_activity.length === 0 ? (
            <div className="empty-state">Nothing has been sent in the last 7 days.</div>
          ) : recent_activity.slice(0, 8).map(a => (
            /* READ-ONLY: display div, not clickable button */
            <div key={a.channel + a.id} className="ov-activity" style={{ cursor: 'default' }}>
              <span className="ov-activity-dot" style={{
                background: a.delivery_status === 'failed' || a.delivery_status === 'undelivered'
                  ? 'var(--signal-red)'
                  : a.delivery_status === 'delivered' ? 'var(--signal-green)'
                  : a.channel === 'email' ? 'var(--signal-purple)' : 'var(--signal-blue)',
              }} />
              <span style={{ minWidth: 0 }}>
                <span className="ov-activity-title">
                  {a.channel === 'email' ? 'Email' : 'SMS'} to {a.lead_name}
                </span>
                <span className="ov-activity-sub">
                  {ago(a.sent_at) ? ago(a.sent_at) + ' ago' : '—'}
                  {' · '}{a.delivery_status || 'pending'}
                  {a.subject ? ` · ${a.subject}` : ''}
                </span>
              </span>
            </div>
          ))}
        </section>
      </div>

    </div>
  )
}
