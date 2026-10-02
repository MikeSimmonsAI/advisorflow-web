/**
 * MOBILE SCREENS. Every read and write is an EXISTING endpoint:
 *
 *   Home            /communications/summary, /communications/replies,
 *                   /work/tasks, /pipeline/appointments
 *   Conversations   /communications/replies
 *   Thread          /communications/thread/{lead}  (includes the compose gate)
 *   Quick reply     POST /communications/send — the SAME gated path the desktop
 *                   composer uses. The server re-runs consent / DNC / quiet
 *                   hours / sender checks and answers 409 with reasons; the
 *                   phone shows them. Nothing here can bypass that.
 *   Tasks           /work/tasks (GET, PATCH status)
 *   Appointments    /pipeline/appointments
 *   Contact         /communications/thread/{lead} (lead block), /work/tasks?lead_id,
 *                   /work/leads/{lead}/notes
 *   Notifications   /notifications/ , POST /notifications/{id}/read
 *   Workspaces      /auth/my-contexts + setWorkspaceContext (desktop mechanism)
 */
import { useState, useEffect } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { api, login, fetchMyContexts, setWorkspaceContext, clearWorkspaceContext, logout } from '../api/client'
import { useMobile, useApi, ScreenHead, Loading, ErrorState, Empty, Icon, isAuthenticated } from './MobileShell'
import { needsAttention, refusalReasons, canCompose, relTime, humanize, groupByDay,
         workspaceChoices, parseTs, NOT_AVAILABLE, greetingName, mobilePathFor } from './mobileHelpers'
import { BrainStrip } from './MobileAgency'
import { hasAgencyFeature } from '../verticals/agencyVertical'
import { readableTimestamps, replaceKeys } from '../utils/humanize'
import { APP_STATUS_LABELS } from '../pages/agency/agencyFormat'
import { readPushStatus, subscribePush, unsubscribePush, pushApi, pushRowState, PUSH_STATUS } from './push'

// ── Login ────────────────────────────────────────────────────────────────────
export function MobileLogin() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  async function submit(e) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      const data = await login(email, password)
      if (data && data.must_change_password) { navigate('/change-password'); return }
      try {
        const ctx = await fetchMyContexts({ force: true })
        const def = ctx && ctx.default_context
        if (def && def.type === 'workspace' && def.organization_id) setWorkspaceContext(def.organization_id)
        else clearWorkspaceContext()
      } catch { /* the shell re-asks */ }
      navigate('/m', { replace: true })
    } catch (ex) { setErr(ex.message || 'Sign-in failed') } finally { setBusy(false) }
  }
  if (isAuthenticated() && !busy) return <Navigate to="/m" replace />
  return (
    <div className="mshell mlogin" data-mskin="platform">
      <form className="mlogin-card" onSubmit={submit}>
        <h1 className="mscreen-title">Sign in</h1>
        <label className="mfield"><span>Email</span>
          <input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required /></label>
        <label className="mfield"><span>Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
        {err && <div className="mrefusal" role="alert">{err}</div>}
        <button className="mbtn mbtn--primary" disabled={busy} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  )
}

// ── Home: Needs Attention / My Work ──────────────────────────────────────────
export function MobileHome() {
  const { branding } = useMobile()
  // An insurance-agency workspace's "needs attention" is the agency attention
  // list (the same one the desktop Command Center shows), not just replies.
  return hasAgencyFeature(branding) ? <AgencyMobileHome /> : <CrmMobileHome />
}

function AgencyMobileHome() {
  const { identity } = useMobile()
  const att = useApi('/agency/attention')
  const sum = useApi('/agency/summary')
  const items = att.data?.items || []
  const card = (k) => (sum.data && sum.data.counts && sum.data.counts[k]) || {}
  const first = greetingName(identity?.user_full_name)
  return (
    <div className="mscreen">
      <ScreenHead title={first ? 'Hi, ' + first : 'My Work'} sub={identity?.workspace_role ? humanize(identity.workspace_role) : null} />
      <div className="mstats">
        <Stat label="Needs attention" value={att.data ? (att.data.total ?? items.length) : undefined} to="/agency" />
        <Stat label="Unassigned" value={card('unassigned').count} to={card('unassigned').link || '/agency/prospects'} />
        <Stat label="Stalled apps" value={card('applications_stalled').count} to={card('applications_stalled').link || '/agency/applications'} />
      </div>
      <div className="msection-title">Needs attention</div>
      {att.loading && <Loading />}
      {!att.loading && att.error && <ErrorState error={att.error} onRetry={att.reload} />}
      {!att.loading && !att.error && items.length === 0 && <Empty>Nothing needs you right now.</Empty>}
      <ul className="mlist">
        {items.map((it, i) => (
          <li key={(it.link && it.link.id) || i}>
            <Link to={mobilePathFor((it.link && it.link.path) || '/agency')} className="mrow">
              <span className={'mpill mpill--' + (it.severity === 'high' ? 'reply mpill--hot' : 'task')}>{humanize((it.link && it.link.type) || 'item')}</span>
              <span className="mrow-main"><span className="mrow-title">{it.title}</span>
                <span className="mrow-detail">{readableTimestamps(replaceKeys(it.detail, APP_STATUS_LABELS))}</span></span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

function CrmMobileHome() {
  const { summary, identity } = useMobile()
  const att = useApi('/communications/replies?needs_attention=true&page_size=25&sort=oldest')
  const recent = useApi('/communications/replies?reviewed=false&page_size=25')
  const tasks = useApi('/work/tasks?status=open&page_size=100')
  const appts = useApi('/pipeline/appointments?days=1')
  const loading = att.loading || tasks.loading || appts.loading
  const replies = mergeById([...(att.data?.items || []), ...((recent.data?.items || []).filter(r => r.is_hot))])
  const items = needsAttention({ replies, tasks: tasks.data?.items || [], appointments: appts.data?.items || [] })
  const hot = replies.filter(r => r.is_hot)
  const first = greetingName(identity?.user_full_name)
  const greeting = first ? 'Hi, ' + first : 'My Work'
  return (
    <div className="mscreen">
      <ScreenHead title={greeting} sub={identity?.workspace_role ? humanize(identity.workspace_role) : null} />
      <div className="mstats">
        <Stat label="Needs attention" value={summary?.needs_attention} to="/m/conversations?f=attention" />
        <Stat label="Callbacks" value={summary?.callbacks} to="/m/conversations?f=callbacks" />
        <Stat label="Open tasks" value={summary?.open_tasks} to="/m/tasks" />
      </div>
      {hot.length > 0 && (
        <section className="mhot" aria-label="Hot alerts">
          <div className="msection-title"><Icon name="flame" size={16} /> Hot alerts</div>
          {hot.slice(0, 3).map(r => (
            <Link key={r.id} to={'/m/conversations/' + r.lead_id} className="mhot-item">
              <strong>{r.contact_name}</strong><span>{r.body}</span><em>{relTime(r.received_at)}</em>
            </Link>
          ))}
        </section>
      )}
      <div className="msection-title">Needs attention</div>
      {loading && <Loading />}
      {!loading && att.error && <ErrorState error={att.error} onRetry={att.reload} />}
      {!loading && !att.error && items.length === 0 && <Empty>Nothing needs you right now.</Empty>}
      <ul className="mlist">
        {items.map(it => (
          <li key={it.id}>
            <Link to={it.href || '#'} className="mrow">
              <span className={'mpill mpill--' + it.kind + (it.hot ? ' mpill--hot' : '')}>{it.badge}</span>
              <span className="mrow-main"><span className="mrow-title">{it.title}</span>
                <span className="mrow-detail">{it.detail}</span></span>
              <span className="mrow-meta">{relTime(it.at)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Stat({ label, value, to }) {
  return (
    <Link to={to} className="mstat">
      <span className="mstat-value">{typeof value === 'number' ? value : '—'}</span>
      <span className="mstat-label">{label}</span>
    </Link>
  )
}

function mergeById(list) {
  const seen = new Set(); const out = []
  for (const r of list) { if (r && !seen.has(r.id)) { seen.add(r.id); out.push(r) } }
  return out
}

// ── Conversations ────────────────────────────────────────────────────────────
const FILTERS = [
  { key: 'all', label: 'All', q: '' },
  { key: 'attention', label: 'Attention', q: '&needs_attention=true' },
  { key: 'callbacks', label: 'Callbacks', q: '&callbacks=true' },
  { key: 'mine', label: 'Mine', q: '&assigned_to=me' },
]
export function MobileConversations() {
  const initial = new URLSearchParams(window.location.search).get('f') || 'all'
  const [f, setF] = useState(FILTERS.some(x => x.key === initial) ? initial : 'all')
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const flt = FILTERS.find(x => x.key === f)
  const { data, error, loading, reload } = useApi('/communications/replies?page_size=50' + flt.q + (term ? '&q=' + encodeURIComponent(term) : ''))
  // One row per conversation (latest reply per lead) — the list is replies.
  const rows = []
  const seen = new Set()
  for (const r of data?.items || []) { if (!seen.has(r.lead_id)) { seen.add(r.lead_id); rows.push(r) } }
  return (
    <div className="mscreen">
      <ScreenHead title="Conversations" sub={data ? data.total + ' replies' : null} />
      <form className="msearch" onSubmit={e => { e.preventDefault(); setTerm(q.trim()) }}>
        <input type="search" placeholder="Search name, phone, text" value={q} onChange={e => setQ(e.target.value)} aria-label="Search conversations" />
      </form>
      <div className="mchips" role="tablist">
        {FILTERS.map(x => <button key={x.key} type="button" role="tab" aria-selected={f === x.key}
                                  className={'mchip' + (f === x.key ? ' is-active' : '')} onClick={() => setF(x.key)}>{x.label}</button>)}
      </div>
      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {!loading && !error && rows.length === 0 && <Empty>No conversations here.</Empty>}
      <ul className="mlist">
        {rows.map(r => (
          <li key={r.id}>
            <Link to={'/m/conversations/' + r.lead_id} className="mrow">
              <span className="mavatar" aria-hidden>{(r.contact_name || '?').slice(0, 1)}</span>
              <span className="mrow-main">
                <span className="mrow-title">{r.contact_name}{r.is_hot && <span className="mdot-hot" title="Hot" />}</span>
                <span className="mrow-detail">{r.body}</span>
              </span>
              <span className="mrow-meta">{relTime(r.received_at)}
                {r.is_dnc && <span className="mtag mtag--warn">DNC</span>}
                {!r.is_dnc && r.needs_attention && <span className="mtag">!</span>}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── Thread + quick reply ─────────────────────────────────────────────────────
export function MobileThread() {
  const { leadId } = useParams()
  const { observing, refreshSummary } = useMobile()
  const { data, error, loading, reload } = useApi('/communications/thread/' + encodeURIComponent(leadId))
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState(null)
  const [sent, setSent] = useState(null)
  if (loading && !data) return <div className="mscreen"><ScreenHead title="Conversation" back="/m/conversations" /><Loading /></div>
  if (error) return <div className="mscreen"><ScreenHead title="Conversation" back="/m/conversations" /><ErrorState error={error} onRetry={reload} /></div>
  const lead = data.lead
  const gate = data.compose
  const allowed = canCompose(gate, { observing })
  const gateReasons = observing ? [{ code: 'OBSERVATION', label: 'Read-only observation session.' }] : refusalReasons(gate)
  const lastInbound = [...data.events].reverse().find(e => e.type === 'message' && e.direction === 'inbound')
  async function send(e) {
    e.preventDefault()
    if (!body.trim() || !allowed) return
    setSending(true); setRefusal(null); setSent(null)
    try {
      const res = await api.post('/communications/send', { lead_id: lead.id, body: body.trim(),
                                                            reply_id: lastInbound ? lastInbound.id : null })
      setBody(''); setSent(res); reload(); refreshSummary && refreshSummary()
    } catch (ex) {
      setRefusal(refusalReasons(ex))
    } finally { setSending(false) }
  }
  return (
    <div className="mscreen mthread">
      <ScreenHead title={lead.name} sub={<Link to={'/m/contacts/' + lead.id}>View contact</Link>} back="/m/conversations" />
      <BrainStrip leadId={lead.id} onDraft={(text) => { if (allowed) setBody(text) }} />
      <div className="mevents">
        {data.events.length === 0 && <Empty>No messages yet.</Empty>}
        {data.events.map(ev => <EventBubble key={ev.type + ev.id} ev={ev} />)}
      </div>
      <form className="mcompose" onSubmit={send}>
        {!allowed && (
          <div className="mrefusal" role="status" data-testid="gate-refusal">
            <strong>Texting is blocked</strong>
            <ul>{gateReasons.map((r, i) => <li key={i}>{r.label}</li>)}</ul>
          </div>
        )}
        {refusal && (
          <div className="mrefusal" role="alert" data-testid="send-refusal">
            <strong>Not sent</strong>
            <ul>{refusal.map((r, i) => <li key={i}>{r.label}</li>)}</ul>
          </div>
        )}
        {sent && <div className="mok" role="status">Sent{sent.state ? ' · ' + humanize(sent.state) : ''}</div>}
        <div className="mcompose-row">
          <textarea rows={2} maxLength={1600} placeholder={allowed ? 'Quick reply by SMS' : 'Sending unavailable'}
                    value={body} onChange={e => setBody(e.target.value)} disabled={!allowed || sending} aria-label="Reply" />
          <button type="submit" className="mbtn mbtn--primary mbtn--icon" disabled={!allowed || sending || !body.trim()} aria-label="Send">
            <Icon name="send" size={20} /></button>
        </div>
      </form>
    </div>
  )
}

function EventBubble({ ev }) {
  if (ev.type === 'note') return <div className="mnote"><span>Note · {ev.sender || NOT_AVAILABLE}</span>{ev.body}</div>
  if (ev.type === 'call' || ev.type === 'voicemail') {
    return <div className="mnote"><span>{ev.type === 'call' ? 'Call' : 'Voicemail'} · {humanize(ev.status || ev.outcome || '')} · {relTime(ev.at)}</span>{ev.summary || ev.body || ''}</div>
  }
  const out = ev.direction === 'outbound'
  return (
    <div className={'mbubble ' + (out ? 'mbubble--out' : 'mbubble--in')}>
      {ev.channel === 'email' && ev.subject && <div className="mbubble-subj">✉ {ev.subject}</div>}
      {ev.body && <div>{ev.body}</div>}
      <div className="mbubble-meta">{out ? (ev.sender || (ev.actor === 'ai' ? 'AI' : '')) : ''} {relTime(ev.at)}
        {ev.state ? ' · ' + humanize(ev.state) : ''}</div>
    </div>
  )
}

// ── Tasks ────────────────────────────────────────────────────────────────────
const DUE = [{ k: '', l: 'All open' }, { k: 'overdue', l: 'Overdue' }, { k: 'today', l: 'Today' }, { k: 'upcoming', l: 'Upcoming' }]
export function MobileTasks() {
  const { observing, refreshSummary } = useMobile()
  const [due, setDue] = useState('')
  const { data, error, loading, reload } = useApi('/work/tasks?status=open&page_size=100' + (due ? '&due=' + due : ''))
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)
  async function complete(t) {
    setBusy(t.id); setErr(null)
    try { await api.patch('/work/tasks/' + t.id, { status: 'done' }); reload(); refreshSummary && refreshSummary() }
    catch (ex) { setErr(ex.message) } finally { setBusy(null) }
  }
  return (
    <div className="mscreen">
      <ScreenHead title="Tasks" sub={data ? data.total + ' open' : null} />
      <div className="mchips">{DUE.map(d => <button key={d.k} type="button" className={'mchip' + (due === d.k ? ' is-active' : '')} onClick={() => setDue(d.k)}>{d.l}</button>)}</div>
      {err && <div className="mrefusal" role="alert">{err}</div>}
      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {!loading && !error && (data?.items || []).length === 0 && <Empty>No open tasks.</Empty>}
      <ul className="mlist">
        {(data?.items || []).map(t => (
          <li key={t.id} className="mrow mrow--task">
            <button type="button" className="mcheck" aria-label={'Complete ' + t.title} disabled={observing || busy === t.id} onClick={() => complete(t)} />
            <span className="mrow-main">
              <span className="mrow-title">{t.title}</span>
              <span className="mrow-detail">{t.lead_id ? <Link to={'/m/contacts/' + t.lead_id}>{t.lead_name || 'Contact'}</Link> : 'No contact'}
                {t.assigned_to_name ? ' · ' + t.assigned_to_name : ''}</span>
            </span>
            <span className={'mrow-meta' + (t.overdue ? ' is-overdue' : '')}>{t.due_at ? (t.overdue ? 'Overdue ' : '') + relTime(t.due_at) : 'No due date'}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── Appointments ─────────────────────────────────────────────────────────────
export function MobileAppointments() {
  const { data, error, loading, reload } = useApi('/pipeline/appointments?days=7')
  const upcoming = (data?.items || []).filter(a => a.upcoming)
  const groups = groupByDay(upcoming)
  return (
    <div className="mscreen">
      <ScreenHead title="Appointments" sub="Upcoming booked appointments" />
      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {!loading && !error && groups.length === 0 && <Empty>No upcoming appointments.</Empty>}
      {groups.map(g => (
        <section key={g.key}>
          <div className="msection-title">{g.label}</div>
          <ul className="mlist">
            {g.items.map(a => (
              <li key={a.id}>
                <Link to={'/m/contacts/' + a.lead_id} className="mrow">
                  <span className="mtime">{new Date(parseTs(a.booked_time)).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</span>
                  <span className="mrow-main"><span className="mrow-title">{a.lead_name || NOT_AVAILABLE}</span>
                    <span className="mrow-detail">{a.appointment_type || 'Appointment'}{a.advisor_name ? ' · ' + a.advisor_name : ''}</span></span>
                  <span className={'mtag' + (a.status === 'cancelled' ? ' mtag--warn' : '')}>{humanize(a.status)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

// ── Contact / Lead ───────────────────────────────────────────────────────────
export function MobileContact() {
  const { leadId } = useParams()
  const th = useApi('/communications/thread/' + encodeURIComponent(leadId) + '?limit=20')
  const tasks = useApi('/work/tasks?status=open&lead_id=' + encodeURIComponent(leadId))
  const notes = useApi('/work/leads/' + encodeURIComponent(leadId) + '/notes')
  if (th.loading && !th.data) return <div className="mscreen"><ScreenHead title="Contact" back="/m" /><Loading /></div>
  if (th.error) return <div className="mscreen"><ScreenHead title="Contact" back="/m" /><ErrorState error={th.error} onRetry={th.reload} /></div>
  const l = th.data.lead
  const place = [l.city, l.state].filter(Boolean).join(', ')
  return (
    <div className="mscreen">
      <ScreenHead title={l.name} sub={humanize(l.status) || null} back="/m" />
      <div className="mcard">
        <Field label="Phone" value={l.phone ? <a href={'tel:' + l.phone}>{l.phone}</a> : null} />
        <Field label="Email" value={l.email ? <a href={'mailto:' + l.email}>{l.email}</a> : null} />
        <Field label="Location" value={place || null} />
        <Field label="Owner" value={l.assigned_to_name} />
        <Field label="Source" value={l.source ? humanize(l.source) : null} />
        <Field label="SMS consent" value={l.sms_consent ? 'Recorded' + (l.sms_consent_source ? ' (' + humanize(l.sms_consent_source) + ')' : '') : 'Not recorded'} />
        {(l.is_dnc || l.suppressed) && <div className="mtag mtag--warn">Do not contact</div>}
      </div>
      <div className="mactions">
        <Link className="mbtn mbtn--primary" to={'/m/conversations/' + l.id}>Conversation</Link>
        {l.phone && <a className="mbtn" href={'tel:' + l.phone}><Icon name="phone" size={18} /> Call</a>}
      </div>
      <div className="msection-title">Open tasks</div>
      {(tasks.data?.items || []).length === 0 ? <Empty>No open tasks.</Empty> : (
        <ul className="mlist">{tasks.data.items.map(t => <li key={t.id} className="mrow"><span className="mrow-main"><span className="mrow-title">{t.title}</span></span><span className={'mrow-meta' + (t.overdue ? ' is-overdue' : '')}>{t.due_at ? (t.overdue ? 'Overdue ' : 'Due ') + relTime(t.due_at) : ''}</span></li>)}</ul>
      )}
      <div className="msection-title">Notes</div>
      {notes.error ? <ErrorState error={notes.error} /> : ((notes.data?.items || notes.data || []).length === 0 ? <Empty>No notes.</Empty> : (
        <ul className="mlist">{(notes.data.items || notes.data).slice(0, 10).map(n => <li key={n.id} className="mnote"><span>{n.author_name || NOT_AVAILABLE} · {relTime(n.created_at)}</span>{n.body}</li>)}</ul>
      ))}
    </div>
  )
}

function Field({ label, value }) {
  return <div className="mfieldrow"><span>{label}</span><span>{value || <em className="mna">{NOT_AVAILABLE}</em>}</span></div>
}

// ── Notifications ────────────────────────────────────────────────────────────
export function MobileNotifications() {
  const { data, error, loading, reload } = useApi('/notifications/')
  const items = Array.isArray(data) ? data : (data?.items || [])
  const [clearing, setClearing] = useState(false)
  async function markRead(n) { try { await api.post('/notifications/' + n.id + '/read', {}); reload() } catch { /* stays unread */ } }
  async function markAll() {
    setClearing(true)
    try { await api.post('/notifications/read-all', items.length ? { through: items[0].created_at } : {}); reload() }
    catch { /* stays unread */ } finally { setClearing(false) }
  }
  // The server's link wins (a Wholesale inquiry opens its deal), mapped to the
  // phone screen when there is one; only in-app paths are followed.
  const target = n => (n.link && n.link.startsWith('/') && !n.link.startsWith('//')) ? mobilePathFor(n.link)
    : n.lead_id ? '/m/contacts/' + n.lead_id : null
  return (
    <div className="mscreen">
      <ScreenHead title="Notifications" sub={data && typeof data.unread_count === 'number' ? data.unread_count + ' unread' : null} back="/m" />
      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {!loading && !error && items.length === 0 && <Empty>You're all caught up.</Empty>}
      {items.length > 0 && (
        <div className="mactions"><button type="button" className="mbtn" disabled={clearing} onClick={markAll}>{clearing ? 'Clearing…' : 'Mark all read'}</button></div>
      )}
      <ul className="mlist">
        {items.map(n => (
          <li key={n.id} className="mrow">
            <span className="mrow-main"><span className="mrow-title">{n.title || humanize(n.type) || 'Notification'}</span>
              <span className="mrow-detail">{n.message || n.body || ''}</span>
              {n.created_at && <span className="mrow-detail">{relTime(n.created_at)}</span>}
              {target(n) && <Link to={target(n)} className="mrow-link" onClick={() => markRead(n)}>Open</Link>}</span>
            <button type="button" className="mbtn mbtn--ghost" onClick={() => markRead(n)}>Mark read</button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── More: workspace switcher, install, push status, sign out ────────────────
export function MobileMore() {
  const { contexts, workspaceId, switchWorkspace, canInstall, promptInstall, identity } = useMobile()
  const choices = workspaceChoices(contexts, workspaceId)
  const [push, setPush] = useState(null)
  const [pushBusy, setPushBusy] = useState(false)
  const [pushMsg, setPushMsg] = useState(null)
  const papi = pushApi(api)
  const refreshPush = () => readPushStatus(papi.getConfig).then(setPush)
  useEffect(() => { refreshPush() }, [])
  async function runPush(fn, okMsg) {
    setPushBusy(true); setPushMsg(null)
    try { await fn(); if (okMsg) setPushMsg(okMsg) }
    catch (e) { setPushMsg(e?.message || 'Could not complete that.') }
    finally { setPushBusy(false); refreshPush() }
  }
  const pushRow = pushRowState(push)
  return (
    <div className="mscreen">
      <ScreenHead title="More" sub={identity?.display_name || null} />
      <WorkspaceList choices={choices} onPick={switchWorkspace} />
      <div className="msection-title">App</div>
      <div className="mcard">
        <div className="mfieldrow"><span>Install</span><span>{canInstall
          ? <button type="button" className="mbtn mbtn--primary" onClick={promptInstall}>Install app</button>
          : <em className="mna">Use your browser's "Add to Home Screen"</em>}</span></div>
        <div className="mfieldrow"><span>Push alerts</span><span data-testid="push-status">{pushRow.label}</span></div>
        {(pushRow.action || push === PUSH_STATUS.SUBSCRIBED) && (
          <div className="mfieldrow" data-testid="push-actions"><span />
            <span>
              {pushRow.action === 'enable' && <button type="button" className="mbtn mbtn--primary" disabled={pushBusy}
                onClick={() => runPush(async () => { const r = await subscribePush({ getConfig: papi.getConfig, postSubscription: papi.postSubscription }); if (!r.ok) throw new Error(pushRowState(r.status).label) }, 'Push alerts enabled on this device.')}>Enable</button>}
              {push === PUSH_STATUS.SUBSCRIBED && <>
                <button type="button" className="mbtn" disabled={pushBusy}
                  onClick={() => runPush(() => papi.test(), 'Test alert sent to your devices.')}>Send test</button>{' '}
                <button type="button" className="mbtn mbtn--ghost" disabled={pushBusy}
                  onClick={() => runPush(() => unsubscribePush({ deleteByHash: papi.deleteByHash }), 'Push alerts turned off on this device.')}>Turn off</button>
              </>}
            </span>
          </div>)}
        {pushMsg && <div className="mfieldrow" role="status" data-testid="push-msg"><span /><span>{pushMsg}</span></div>}
        <div className="mfieldrow"><span>Full site</span><span><a href="/">Open desktop view</a></span></div>
      </div>
      <button type="button" className="mbtn mbtn--ghost mbtn--block" onClick={() => logout()}>Sign out</button>
    </div>
  )
}

function WorkspaceList({ choices, onPick }) {
  if (!choices.length) return null
  return (
    <>
      <div className="msection-title">Workspace</div>
      <ul className="mlist" data-testid="workspace-list">
        {choices.map(c => (
          <li key={c.id}>
            <button type="button" className={'mrow mrow--btn' + (c.active ? ' is-active' : '')} onClick={() => onPick(c.id)} aria-pressed={c.active}>
              <span className="mrow-main"><span className="mrow-title">{c.name}</span>
                <span className="mrow-detail">{c.role ? humanize(c.role) : ''}</span></span>
              {c.active && <span className="mtag">Current</span>}
            </button>
          </li>
        ))}
      </ul>
    </>
  )
}

export function MobileWorkspaces() {
  const { contexts, workspaceId, switchWorkspace } = useMobile()
  if (!contexts) return <div className="mscreen"><ScreenHead title="Choose workspace" /><Loading /></div>
  return (
    <div className="mscreen">
      <ScreenHead title="Choose workspace" />
      <WorkspaceList choices={workspaceChoices(contexts, workspaceId)} onPick={switchWorkspace} />
    </div>
  )
}
