/** Screens of the insurance retention workspace (Issue #499). Data: /agency/retention/*. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useIns } from './InsuranceShell'
import { Empty, Icon, Modal, StatusPill, agoText, errText, fmtDate, fmtDateTime, fmtTime, initials, productIcon, shortReason, useToast } from './insuranceUi'

function useData(pathFn, deps) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const load = useCallback(async () => {
    try { setData(await api.get(pathFn())); setError(null) } catch (e) { setError(e) }
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [load])
  return { data, error, reload: load }
}

function Err({ error, onRetry }) {
  if (!error) return null
  return <div className="ins-alert" role="alert">{errText(error)} {onRetry && <button className="ins-btn ins-btn--sm" onClick={onRetry}>Retry</button>}</div>
}

function localDay(iso, tz) {
  try { return new Date(iso).toLocaleDateString('en-CA', { timeZone: tz }) } catch { return String(iso).slice(0, 10) }
}
function sameDay(iso, tz) { return localDay(iso, tz) === localDay(new Date().toISOString(), tz) }

function greeting(tz) {
  let h = 9
  try { h = Number(new Date().toLocaleString('en-US', { timeZone: tz, hour: 'numeric', hour12: false })) } catch { /* default */ }
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

const KPI = {
  due: { icon: 'calendar', tone: 'blue', view: 'due' },
  attention: { icon: 'alert', tone: 'red', view: 'attention' },
  booked: { icon: 'calcheck', tone: 'green', view: 'booked' },
  confirmed: { icon: 'check', tone: 'navy', view: 'confirmed' },
}

// ───────────────────────────── TODAY ─────────────────────────────

export function TodayPage() {
  const c = useIns()
  const nav = useNavigate()
  const { data, error, reload } = useData(() => c.q('/agency/retention/today', { location_id: c.locationId, agent_id: c.agentId }),
    [c.locationId, c.agentId, c.tick, c.q])
  const [assign, setAssign] = useState(null)
  const [book, setBook] = useState(null)
  const [toast, say] = useToast()
  const s = c.settings
  const first = (c.viewer.actor_name || '').split(' ')[0]
  const after = (msg) => { say(msg); reload(); c.refresh() }
  return (
    <div className="ins-today">
      <div className="ins-hero">
        <div>
          <h1>{greeting(s.timezone)}{first ? `, ${first}` : ''}.</h1>
          <p className="ins-hero__tag">{s.tagline}</p>
          <p className="ins-muted">
            {s.display_name}
            {s.is_demo && <> · Demo scenario: {c.locations.length} location{c.locations.length === 1 ? '' : 's'}{c.agents.length ? ` · ${c.agents.length} staff` : ''}</>}
            {c.viewer.kind === 'agent' && <> · Your assigned work and your locations’ shared queue</>}
          </p>
        </div>
        {c.canManage && c.agents.length > 0 && (
          <label className="ins-select ins-select--light">
            <Icon name="user" /><span className="sr-only">Agent</span>
            <select value={c.agentId} onChange={e => c.setAgent(e.target.value)}>
              <option value="">All agents</option>
              <option value="unassigned">Unassigned</option>
              {c.agents.map(a => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}
            </select>
          </label>
        )}
      </div>
      <Err error={error} onRetry={reload} />
      {!data ? <div className="ins-loading">Loading today…</div> : (<>
        <section className="ins-kpis" aria-label="Key numbers">
          {data.metrics.map(m => (
            <button key={m.key} className={`ins-kpi ins-kpi--${KPI[m.key].tone}`} title={m.basis}
                    onClick={() => nav({ pathname: `${BASE}/renewals`, search: `?view=${KPI[m.key].view}${c.locationId ? `&loc=${c.locationId}` : ''}${c.agentId ? `&agent=${c.agentId}` : ''}` })}>
              <span className="ins-kpi__icon"><Icon name={KPI[m.key].icon} size={22} /></span>
              <span className="ins-kpi__body"><span className="ins-kpi__label">{m.label}</span>
                <span className="ins-kpi__value">{m.value}</span>
                <span className="ins-kpi__sub">{m.policies} polic{m.policies === 1 ? 'y' : 'ies'}</span></span>
            </button>
          ))}
        </section>
        <div className="ins-grid">
          <section className="ins-card ins-card--queue" aria-labelledby="prio-h">
            <div className="ins-card__head">
              <h2 id="prio-h">{c.viewer.kind === 'agent' ? 'My renewal priorities' : 'Your renewal priorities'}</h2>
              <Link to={`${BASE}/renewals?view=attention`} className="ins-link">View all <Icon name="arrow" size={14} /></Link>
            </div>
            <QueueTable rows={data.queue} onAssign={setAssign} onBook={setBook} />
          </section>
          <AssistantPanel a={data.assistant} onChange={after} />
        </div>
        <div className="ins-grid ins-grid--even">
          <section className="ins-card" aria-labelledby="wl-h">
            <div className="ins-card__head"><h2 id="wl-h"><Icon name="users" /> Team workload</h2>
              <Link to={`${BASE}/team`} className="ins-link">Team <Icon name="arrow" size={14} /></Link></div>
            {data.workload.length === 0 ? <Empty>No locations in your scope.</Empty> : (
              <table className="ins-table ins-table--compact">
                <thead><tr><th>Location</th><th>Open renewals (60 days)</th><th aria-label="Load" /></tr></thead>
                <tbody>{data.workload.map(w => (
                  <tr key={w.location_id}>
                    <td><b>{w.location}</b>{!w.verified && <span className="ins-tag">illustrative</span>}</td>
                    <td>{w.capacity ? `${w.open} of ${w.capacity} (${w.pct}%)` : `${w.open} open`}</td>
                    <td className="ins-bar-cell">{w.capacity ? <span className="ins-bar"><span style={{ width: `${Math.min(100, w.pct)}%` }} className={w.pct > 90 ? 'hot' : ''} /></span> : null}</td>
                  </tr>))}</tbody>
              </table>
            )}
            <p className="ins-fine">Capacity = configured reviews per 14 days × 4 for staff at the location.</p>
          </section>
          <section className="ins-card" aria-labelledby="ap-h">
            <div className="ins-card__head"><h2 id="ap-h"><Icon name="calendar" /> Today’s appointments</h2>
              <Link to={`${BASE}/appointments`} className="ins-link">Calendar <Icon name="arrow" size={14} /></Link></div>
            {data.appointments.length === 0 ? <Empty>No appointments today.</Empty> : (
              <ul className="ins-appts">{data.appointments.map(a => (
                <li key={a.id}>
                  <span className="ins-appts__time">{fmtTime(a.starts_at, s.timezone)}</span>
                  <button className="ins-appts__body" onClick={() => c.openClient(a.lead_id)}>
                    <b>{a.notes || a.title}</b>
                    <span>{a.household}{a.product_label ? ` · ${a.product_label}` : ''}{a.location ? ` · ${a.location}` : ''}{a.agent_name ? ` · ${a.agent_name}` : ''}</span>
                  </button>
                </li>))}</ul>
            )}
          </section>
        </div>
      </>)}
      {assign && <AssignDialog row={assign} onClose={() => setAssign(null)} onDone={m => { setAssign(null); after(m) }} />}
      {book && <BookDialog row={book} onClose={() => setBook(null)} onDone={m => { setBook(null); after(m) }} />}
      {toast}
    </div>
  )
}

function QueueTable({ rows, onAssign, onBook, selectable, selected, setSelected }) {
  const c = useIns()
  if (!rows.length) return <Empty>Nothing needs attention right now.</Empty>
  return (
    <div className="ins-table-wrap">
      <table className="ins-table">
        <thead><tr>
          {selectable && <th><span className="sr-only">Select</span></th>}
          <th>Client</th><th>Policy</th><th>Renewal</th><th>Reason / status</th><th>Assigned</th><th>Next action</th><th><span className="sr-only">Actions</span></th>
        </tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.id}>
            {selectable && <td><input type="checkbox" aria-label={`Select ${r.household}`} checked={selected.has(r.id)}
                                     onChange={e => { const n = new Set(selected); e.target.checked ? n.add(r.id) : n.delete(r.id); setSelected(n) }} /></td>}
            <td><b>{r.household}</b><small>{r.location_name || '—'}{r.preferred_language === 'es' ? ' · Español' : ''}</small></td>
            <td><span className="ins-prod"><Icon name={productIcon(r.product)} size={16} />{r.product_label}</span><small>{r.policy_masked}</small></td>
            <td>{fmtDate(r.renewal_date)}<small>{r.days_to_renewal >= 0 ? `in ${r.days_to_renewal} d` : `${-r.days_to_renewal} d ago`}</small></td>
            <td><StatusPill row={r} /></td>
            <td>{r.assigned_name ? <span className="ins-person"><span className="ins-avatar ins-avatar--sm">{initials(r.assigned_name)}</span>{r.assigned_name}</span> : <span className="ins-muted">Unassigned</span>}</td>
            <td className="ins-next">{r.next_action || '—'}</td>
            <td className="ins-actions"><div>
              <button className="ins-btn ins-btn--sm ins-btn--outline" onClick={() => c.openClient(r.lead_id)}>Open</button>
              {!c.readOnly && !r.outcome && (c.canManage || !r.assigned_user_id) && (
                <button className="ins-btn ins-btn--sm" onClick={() => onAssign(r)}>{r.assigned_user_id ? 'Assign' : (c.canManage ? 'Assign' : 'Claim')}</button>)}
              {!c.readOnly && !r.outcome && r.assigned_user_id && !(r.review && ['booked', 'confirmed'].includes(r.review.status)) && (
                <button className="ins-btn ins-btn--sm" onClick={() => onBook(r)}>Schedule</button>)}
            </div></td>
          </tr>))}</tbody>
      </table>
    </div>
  )
}

function AssistantPanel({ a, onChange }) {
  const c = useIns()
  const [busy, setBusy] = useState(false)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState(null)
  const wf = a.workflows[0]
  const toggle = async () => {
    if (!wf) return
    setBusy(true)
    try { await api.post(c.q(`/agency/retention/workflows/${wf.id}/${a.state === 'running' ? 'pause' : 'resume'}`), {}); onChange(a.state === 'running' ? 'Renewal outreach paused.' : 'Renewal outreach resumed.') }
    catch (e) { onChange(errText(e)) } finally { setBusy(false) }
  }
  const ask = async (e) => {
    e.preventDefault()
    if (!question.trim()) return
    try { setAnswer(await api.post(c.q('/agency/retention/ask'), { question })) } catch (err) { setAnswer({ answer: errText(err), facts: [], inferred: [], records: [] }) }
  }
  const stateLabel = { running: c.settings.is_demo ? 'Running in demo' : 'Running', paused: 'Paused', not_set_up: 'Not set up' }[a.state]
  return (
    <section className="ins-card ins-ai" aria-labelledby="ai-h">
      <div className="ins-card__head">
        <h2 id="ai-h"><Icon name="spark" /> AI renewal assistant</h2>
        <span className={`ins-dot ins-dot--${a.state}`}>{stateLabel}</span>
      </div>
      {a.delivery_note && <span className="ins-tag ins-tag--blue">Simulated activity</span>}
      {a.blocked_reason && <p className="ins-warn">{a.blocked_reason}</p>}
      <ul className="ins-feed">
        {a.activity.length === 0 && <li className="ins-muted">No activity yet.</li>}
        {a.activity.slice(0, 4).map(ev => (
          <li key={ev.id} className={`ins-feed__item ins-feed__item--${ev.kind}`}>
            <span className="ins-feed__icon"><Icon name={ev.kind === 'reply' ? 'msg' : ev.kind === 'review_booked' ? 'calcheck' : ev.kind === 'outreach' ? 'bell' : 'check'} size={16} /></span>
            <span className="ins-feed__time">{sameDay(ev.at, c.settings.timezone) ? fmtTime(ev.at, c.settings.timezone) : fmtDate(localDay(ev.at, c.settings.timezone))}</span>
            <span className="ins-feed__text"><b>{ev.summary}</b>
              {ev.delivery_status && <small>{ev.direction === 'in' ? (ev.delivery_status === 'simulated' ? 'Simulated reply' : 'Received') : ev.delivery_status === 'simulated' ? 'Simulated — not sent' : ev.delivery_status === 'failed' ? 'Delivery failed' : ev.delivery_status}</small>}</span>
          </li>))}
      </ul>
      <dl className="ins-ai__facts">
        <div><dt>Replies needing a person</dt><dd><Link to={`${BASE}/conversations`}>{a.replies_needing_person}</Link></dd></div>
        <div><dt>Delivery failures</dt><dd>{a.delivery_failures}</dd></div>
        <div><dt>Last activity</dt><dd>{a.last_activity ? agoText(a.last_activity.at) : '—'}</dd></div>
      </dl>
      <div className="ins-ai__next">
        <Icon name="calendar" />
        <div><b>Next action:</b> {!a.next_scheduled_at ? 'Nothing scheduled' : new Date(a.next_scheduled_at) <= new Date() ? 'Due now — waiting for the next run' : fmtDateTime(a.next_scheduled_at, c.settings.timezone)}
          <small>{a.next_scheduled_at ? `Next renewal reminder${a.delivery_note ? ' (simulated)' : ''}, inside the ${c.settings.send_window_start}–${c.settings.send_window_end} send window.` : ''}</small></div>
        {c.canManage && wf && <button className="ins-btn ins-btn--sm ins-btn--outline" disabled={busy} onClick={toggle}>
          <Icon name={a.state === 'running' ? 'pause' : 'play'} size={14} /> {a.state === 'running' ? 'Pause' : 'Resume'}</button>}
      </div>
      <form className="ins-ask" onSubmit={ask}>
        <label htmlFor="ins-ask-q" className="sr-only">Ask about your renewals</label>
        <input id="ins-ask-q" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask: Which renewals need attention this week?" list="ins-ask-examples" />
        <datalist id="ins-ask-examples">
          <option value="Which renewals need attention this week?" /><option value="Who replied about a price increase?" />
          <option value="Which location has the most overdue follow-ups?" /><option value="Which agents can take more reviews?" />
        </datalist>
        <button className="ins-btn ins-btn--primary ins-btn--sm">Ask</button>
      </form>
      {answer && (
        <div className="ins-answer" aria-live="polite">
          <p><b>{answer.answer}</b></p>
          {answer.facts?.length > 0 && <><h3>Facts</h3><ul>{answer.facts.map((f, i) => <li key={i}>{f}</li>)}</ul></>}
          {answer.inferred?.length > 0 && <><h3>Inferred</h3><ul>{answer.inferred.map((f, i) => <li key={i}>{f}</li>)}</ul></>}
          {answer.records?.length > 0 && <ul className="ins-answer__recs">{answer.records.map(r => (
            <li key={r.renewal_id}><button className="ins-link" onClick={() => c.openClient(r.lead_id)}>{r.household}</button> — {r.reason || ''} · {fmtDate(r.renewal_date)}{r.assigned ? ` · ${r.assigned}` : ''}</li>))}</ul>}
        </div>
      )}
    </section>
  )
}

// ───────────────────────────── DIALOGS ─────────────────────────────

function AssignDialog({ row, onClose, onDone }) {
  const c = useIns()
  const [agent, setAgent] = useState(row.assigned_user_id || '')
  const [reason, setReason] = useState(row.assigned_user_id ? 'Manager reassignment' : 'Assigned by manager')
  const [moveReview, setMoveReview] = useState(false)
  const [err, setErr] = useState(null)
  const claimOnly = !c.canManage
  const submit = async (e) => {
    e.preventDefault()
    try {
      if (claimOnly) await api.post(c.q(`/agency/retention/renewals/${row.id}/claim`), { expected_version: row.version })
      else await api.post(c.q(`/agency/retention/renewals/${row.id}/assign`), { agent_id: agent || null, reason, expected_version: row.version, move_review: moveReview })
      onDone(claimOnly ? 'Claimed — it’s yours now.' : 'Assignment saved. History kept.')
    } catch (er) { setErr(er) }
  }
  return (
    <Modal title={claimOnly ? `Claim ${row.household}` : `Assign ${row.household}`} onClose={onClose}>
      <form onSubmit={submit} className="ins-form">
        {claimOnly ? <p>Take this renewal from the shared queue. If someone else claims it first, you’ll be told.</p> : (<>
          <label>Assign to<select value={agent} onChange={e => setAgent(e.target.value)} required>
            <option value="">Choose…</option>{c.agents.map(a => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}</select></label>
          <label>Reason<input value={reason} onChange={e => setReason(e.target.value)} maxLength={300} required /></label>
          {row.review && <label className="ins-check"><input type="checkbox" checked={moveReview} onChange={e => setMoveReview(e.target.checked)} /> Move the booked review to the new person too</label>}
          <p className="ins-fine">Current owner: {row.assigned_name || 'nobody'}{row.assigned_at ? ` since ${fmtDate(row.assigned_at)}` : ''}. Messages in progress continue without resending.</p>
        </>)}
        {err && <div className="ins-alert" role="alert">{errText(err)}</div>}
        <div className="ins-row ins-row--end"><button type="button" className="ins-btn" onClick={onClose}>Cancel</button>
          <button className="ins-btn ins-btn--primary">{claimOnly ? 'Claim' : 'Save'}</button></div>
      </form>
    </Modal>
  )
}

function BookDialog({ row, onClose, onDone }) {
  const c = useIns()
  // Tomorrow in the AGENCY's time zone, not UTC (after 6 PM Central, UTC is already tomorrow).
  const [day, setDay] = useState(localDay(new Date(Date.now() + 86400000).toISOString(), c.settings.timezone))
  const [time, setTime] = useState('10:00')
  const [medium, setMedium] = useState('in_person')
  const [agent, setAgent] = useState(row.assigned_user_id || '')
  const [err, setErr] = useState(null)
  const submit = async (e) => {
    e.preventDefault()
    // Interpret the chosen wall-clock time in the agency's time zone.
    const startsAt = zonedToUtcIso(day, time, c.settings.timezone)
    try {
      await api.post(c.q(`/agency/retention/renewals/${row.id}/book-review`), { starts_at: startsAt, medium, agent_id: c.canManage ? agent : undefined })
      onDone('Review booked. The renewal is not marked confirmed until someone records the outcome.')
    } catch (er) { setErr(er) }
  }
  return (
    <Modal title={`Schedule review — ${row.household}`} onClose={onClose}>
      <form onSubmit={submit} className="ins-form">
        <div className="ins-row"><label>Date<input type="date" value={day} onChange={e => setDay(e.target.value)} required /></label>
          <label>Time ({tzShort(c.settings.timezone)})<input type="time" value={time} step="900" onChange={e => setTime(e.target.value)} required /></label></div>
        <label>How<select value={medium} onChange={e => setMedium(e.target.value)}><option value="in_person">In office</option><option value="phone">Phone</option><option value="video">Video</option></select></label>
        {c.canManage && <label>With<select value={agent} onChange={e => setAgent(e.target.value)}>
          {c.agents.map(a => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}</select></label>}
        {err && <div className="ins-alert" role="alert">{errText(err)}</div>}
        <div className="ins-row ins-row--end"><button type="button" className="ins-btn" onClick={onClose}>Cancel</button><button className="ins-btn ins-btn--primary">Book review</button></div>
      </form>
    </Modal>
  )
}

function tzShort(tz) {
  try { return new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' }).formatToParts(new Date()).find(p => p.type === 'timeZoneName').value } catch { return tz }
}
export function zonedToUtcIso(day, time, tz) {
  const [y, m, d] = day.split('-').map(Number)
  const [hh, mm] = time.split(':').map(Number)
  const guess = Date.UTC(y, m - 1, d, hh, mm)
  const asTz = new Date(new Date(guess).toLocaleString('en-US', { timeZone: tz }))
  const asUtc = new Date(new Date(guess).toLocaleString('en-US', { timeZone: 'UTC' }))
  return new Date(guess + (asUtc - asTz)).toISOString()
}

// ───────────────────────────── RENEWALS ─────────────────────────────

const VIEWS = [['attention', 'Needs attention'], ['due', 'Due in 30 days'], ['booked', 'Reviews booked'], ['confirmed', 'Confirmed'],
  ['lost', 'Lost'], ['unassigned', 'Unassigned'], ['mine', 'Mine'], ['all', 'All']]

export function RenewalsPage() {
  const c = useIns()
  const [sp, setSp] = useSearchParams()
  const view = sp.get('view') || 'attention'
  const [product, setProduct] = useState('')
  const { data, error, reload } = useData(() => c.q('/agency/retention/renewals', { view, location_id: c.locationId, agent_id: c.agentId, product }),
    [view, c.locationId, c.agentId, product, c.tick, c.q])
  const [assign, setAssign] = useState(null)
  const [book, setBook] = useState(null)
  const [selected, setSelected] = useState(new Set())
  const [bulkTo, setBulkTo] = useState('')
  const [toast, say] = useToast()
  const after = m => { say(m); setSelected(new Set()); reload(); c.refresh() }
  const bulk = async () => {
    try { const r = await api.post(c.q('/agency/retention/renewals/bulk-assign'), { renewal_ids: [...selected], agent_id: bulkTo, reason: 'Bulk assignment' })
      after(`${r.assigned.length} assigned${r.conflicts.length ? `, ${r.conflicts.length} changed by someone else first` : ''}.`) } catch (e) { say(errText(e), 'err') }
  }
  const route = async () => {
    try { const r = await api.post(c.q('/agency/retention/renewals/auto-route'), {}); after(`${r.assigned.length} routed${r.unrouted.length ? `, ${r.unrouted.length} with no eligible person (${r.unrouted[0].why})` : ''}.`) }
    catch (e) { say(errText(e), 'err') }
  }
  return (
    <div>
      <div className="ins-pagehead"><h1>Renewals</h1>
        <div className="ins-row">
          <label className="ins-select ins-select--light"><span className="sr-only">Product</span>
            <select value={product} onChange={e => setProduct(e.target.value)}><option value="">All products</option>
              {c.ws.products.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}</select></label>
          {c.canManage && <button className="ins-btn" onClick={route} title="Assign unassigned renewals: current servicing agent first, then least-loaded eligible staff at the location (language-aware).">Route unassigned</button>}
        </div>
      </div>
      <div className="ins-tabs" role="tablist" aria-label="Renewal views">
        {VIEWS.filter(([k]) => k !== 'unassigned' || c.viewer.kind !== 'agent' || c.settings.shared_queue).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={view === k} className={view === k ? 'on' : ''} onClick={() => { const n = new URLSearchParams(sp); n.set('view', k); setSp(n) }}>{l}</button>))}
      </div>
      <Err error={error} onRetry={reload} />
      {c.canManage && selected.size > 0 && (
        <div className="ins-bulk"><b>{selected.size} selected</b>
          <select value={bulkTo} onChange={e => setBulkTo(e.target.value)} aria-label="Assign selected to"><option value="">Assign to…</option>
            {c.agents.map(a => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}</select>
          <button className="ins-btn ins-btn--sm ins-btn--primary" disabled={!bulkTo} onClick={bulk}>Assign</button></div>)}
      <section className="ins-card">
        {!data ? <div className="ins-loading">Loading…</div> : (<>
          <p className="ins-fine">{data.total} renewal{data.total === 1 ? '' : 's'} · {data.households} household{data.households === 1 ? '' : 's'}</p>
          <QueueTable rows={data.items} onAssign={setAssign} onBook={setBook} selectable={c.canManage} selected={selected} setSelected={setSelected} />
        </>)}
      </section>
      {assign && <AssignDialog row={assign} onClose={() => setAssign(null)} onDone={m => { setAssign(null); after(m) }} />}
      {book && <BookDialog row={book} onClose={() => setBook(null)} onDone={m => { setBook(null); after(m) }} />}
      {toast}
    </div>
  )
}

// ───────────────────────────── CLIENTS ─────────────────────────────

export function ClientsPage() {
  const c = useIns()
  const [search, setSearch] = useState('')
  const [term, setTerm] = useState('')
  const { data, error, reload } = useData(() => c.q('/agency/retention/clients', { search: term, location_id: c.locationId }), [term, c.locationId, c.tick, c.q])
  return (
    <div>
      <div className="ins-pagehead"><h1>Clients & Policies</h1>
        <form className="ins-search" onSubmit={e => { e.preventDefault(); setTerm(search) }} role="search">
          <Icon name="search" /><label htmlFor="ins-cs" className="sr-only">Search clients</label>
          <input id="ins-cs" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search households" />
        </form></div>
      <Err error={error} onRetry={reload} />
      <section className="ins-card">
        {!data ? <div className="ins-loading">Loading…</div> : data.items.length === 0 ? <Empty>No clients in your scope.</Empty> : (
          <div className="ins-table-wrap"><table className="ins-table">
            <thead><tr><th>Household</th><th>Policies</th><th>Next renewal</th><th>Location</th><th>Servicing agent</th><th>Source</th><th /></tr></thead>
            <tbody>{data.items.map(x => (
              <tr key={x.lead_id}>
                <td><b>{x.household}</b><small>{x.name}{x.preferred_language === 'es' ? ' · Español' : ''}</small></td>
                <td>{x.policies}<small>{x.products.join(', ')}</small></td>
                <td>{x.next_renewal ? <>{fmtDate(x.next_renewal)}<small>in {x.days_to_next} d</small></> : '—'}</td>
                <td>{x.location || '—'}</td><td>{x.servicing_agent || '—'}</td>
                <td><span className="ins-tag">{x.source === 'csv_import' ? 'CSV import' : x.source}</span></td>
                <td><button className="ins-btn ins-btn--sm ins-btn--outline" onClick={() => c.openClient(x.lead_id)}>Open</button></td>
              </tr>))}</tbody></table></div>
        )}
      </section>
    </div>
  )
}

// ───────────────────────────── CLIENT RECORD (drawer) ─────────────────────────────

export function ClientDrawer({ leadId, onClose }) {
  const c = useIns()
  const { data, error, reload } = useData(() => c.q(`/agency/retention/clients/${leadId}`), [leadId, c.tick, c.q])
  const [summary, setSummary] = useState(null)
  const [assign, setAssign] = useState(null)
  const [book, setBook] = useState(null)
  const [toast, say] = useToast()
  const [reply, setReply] = useState('')
  const [note, setNote] = useState('')
  const [draft, setDraft] = useState(null)
  useEffect(() => {
    const k = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', k)
    return () => document.removeEventListener('keydown', k)
  }, [onClose])
  const after = m => { say(m); reload(); c.refresh() }
  const act = async (fn, msg) => { try { await fn(); after(msg) } catch (e) { say(errText(e), 'err') } }
  const open = data?.renewals.filter(r => !r.outcome && r.in_scope) || []
  const focus = open[0]
  const tz = c.settings.timezone
  return (
    <div className="ins-drawer" role="dialog" aria-modal="true" aria-label="Client record" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ins-drawer__panel">
        <div className="ins-drawer__head">
          <div><h2>{data?.household || 'Client'}</h2>
            {data && <p className="ins-muted">{data.name} · {data.location?.name || 'No location'} · Servicing: {data.servicing_agent || '—'}
              {data.preferred_language === 'es' ? ' · Prefers Spanish' : ''}</p>}</div>
          <button className="ins-btn ins-btn--icon" onClick={onClose} aria-label="Close client record"><Icon name="x" /></button>
        </div>
        <Err error={error} onRetry={reload} />
        {!data ? <div className="ins-loading">Loading record…</div> : (
          <div className="ins-drawer__body">
            {focus && <div className="ins-nextbox"><b>Next action:</b> {focus.next_action || 'No action scheduled'}{focus.next_action_at ? ` · ${fmtDateTime(focus.next_action_at, tz)}` : ''}</div>}
            <div className="ins-meta">
              <span><Icon name="msg" size={14} /> {data.preferred_channel || 'sms'}</span>
              {data.contact.phone && <span>{data.contact.phone}</span>}
              {data.contact.email && <span>{data.contact.email}</span>}
              {!data.contact_ok && <span className="ins-pill ins-pill--grey">Opted out</span>}
              <span className="ins-tag">Source: {data.source === 'csv_import' ? 'CSV import' : data.source}{data.source_updated_at ? ` · ${fmtDate(data.source_updated_at)}` : ''}</span>
            </div>

            <h3>Policies & renewals</h3>
            <div className="ins-renewals">
              {data.renewals.map(r => (
                <div key={r.id} className={`ins-renewal ${r.in_scope ? '' : 'ins-renewal--locked'}`}>
                  <div className="ins-renewal__top">
                    <span className="ins-prod"><Icon name={productIcon(r.product)} size={16} /><b>{r.product_label}</b> <small>{r.policy_masked} · policy {r.policy_status}</small></span>
                    <StatusPill row={r} />
                  </div>
                  <div className="ins-renewal__line">Renews <b>{fmtDate(r.renewal_date)}</b> ({r.days_to_renewal} d) · {r.assigned_name || 'Unassigned'}
                    {r.review && <> · Review {r.review.status} {fmtDateTime(r.review.starts_at, tz)}</>}</div>
                  {r.attention_detail && <p className="ins-quote">“{r.attention_detail}”</p>}
                  {r.in_scope && !r.outcome && !c.readOnly && (
                    <div className="ins-row ins-row--wrap">
                      {(c.canManage || !r.assigned_user_id) && <button className="ins-btn ins-btn--sm" onClick={() => setAssign(r)}>{r.assigned_user_id ? 'Reassign' : c.canManage ? 'Assign' : 'Claim'}</button>}
                      {r.assigned_user_id && !(r.review && ['booked', 'confirmed'].includes(r.review.status)) && <button className="ins-btn ins-btn--sm" onClick={() => setBook(r)}>Schedule review</button>}
                      {r.review && ['booked', 'confirmed'].includes(r.review.status) && <button className="ins-btn ins-btn--sm" onClick={() => act(() => api.post(c.q(`/agency/retention/appointments/${r.review.id}/status`), { status: 'completed' }), 'Review marked completed.')}>Review held</button>}
                      <button className="ins-btn ins-btn--sm ins-btn--green" onClick={() => act(() => api.post(c.q(`/agency/retention/renewals/${r.id}/outcome`), { outcome: 'confirmed' }), 'Renewal outcome recorded: confirmed.')}>Record: renewed</button>
                      <button className="ins-btn ins-btn--sm" onClick={() => { if (window.confirm('Record this renewal as lost?')) act(() => api.post(c.q(`/agency/retention/renewals/${r.id}/outcome`), { outcome: 'lost' }), 'Renewal outcome recorded: lost.') }}>Record: lost</button>
                      <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={async () => setDraft(await api.get(c.q(`/agency/retention/renewals/${r.id}/draft`, { language: data.preferred_language })))}>Draft message</button>
                      {c.isDemo && c.canManage && <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={() => act(async () => {
                        const res = await api.post(c.q(`/agency/retention/renewals/${r.id}/run-next-step`), {})
                        if (!res.stats.processed) throw new Error(res.stats.skipped_duplicate ? 'That step already ran.' : 'No active workflow step for this renewal. Start a workflow in Automation.')
                      }, 'Next reminder recorded (simulated — nothing was sent).')}>Run next step (sim)</button>}
                    </div>)}
                  {!r.in_scope && <p className="ins-fine">Outside your assignments — visible for context only.</p>}
                </div>))}
            </div>

            {draft && <div className="ins-draft"><h3>Draft (not sent)</h3><p>{draft.draft}</p><p className="ins-fine">{draft.note}</p>
              <button className="ins-btn ins-btn--sm" onClick={() => setDraft(null)}>Close draft</button></div>}

            <div className="ins-row ins-row--between"><h3>Conversation & history</h3>
              <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={async () => setSummary(await api.get(c.q(`/agency/retention/clients/${leadId}/summary`)))}>Summarize</button></div>
            {summary && <div className="ins-answer"><h3>Facts</h3><ul>{summary.facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
              {summary.inferred.length > 0 && <><h3>Inferred priorities</h3><ul>{summary.inferred.map((f, i) => <li key={i}>{f}</li>)}</ul></>}
              <p className="ins-fine">{summary.note}</p></div>}
            <ol className="ins-thread">
              {data.timeline.length === 0 && <li className="ins-muted">No history yet.</li>}
              {data.timeline.map(e => <TimelineItem key={e.id} e={e} tz={tz} />)}
            </ol>
            {focus && !c.readOnly && (<>
              {c.isDemo && (
                <form className="ins-compose" onSubmit={e => { e.preventDefault(); if (!reply.trim()) return; act(() => api.post(c.q(`/agency/retention/renewals/${focus.id}/simulate-reply`), { text: reply }), 'Simulated client reply added; automation paused for a person.'); setReply('') }}>
                  <label htmlFor="ins-sim">Simulate a client reply <span className="ins-tag ins-tag--blue">demo</span></label>
                  <div className="ins-row"><input id="ins-sim" value={reply} onChange={e => setReply(e.target.value)} placeholder="e.g. My premium went up — can someone call me?" />
                    <button className="ins-btn ins-btn--sm">Add reply</button></div>
                </form>)}
              <form className="ins-compose" onSubmit={e => { e.preventDefault(); if (!note.trim()) return; act(() => api.post(c.q(`/agency/retention/renewals/${focus.id}/note`), { body: note, resolve: true }), 'Note saved and item marked handled.'); setNote('') }}>
                <label htmlFor="ins-note">Internal note (marks the reply handled)</label>
                <div className="ins-row"><input id="ins-note" value={note} onChange={e => setNote(e.target.value)} placeholder="Called client, explained the premium change…" />
                  <button className="ins-btn ins-btn--sm">Save note</button></div>
              </form>
            </>)}

            {data.enrollments.length > 0 && (<><h3>Automation</h3><ul className="ins-list">{data.enrollments.map(en => (
              <li key={en.id}><b>{en.workflow}</b> — {en.state.replace('_', ' ')}{en.paused_reason ? ` (${en.paused_reason.replace('_', ' ')})` : ''}{en.next_run_at ? ` · next ${fmtDateTime(en.next_run_at, tz)} (${en.next_step_days_before} days before)` : ''}</li>))}</ul></>)}
            {data.appointments.length > 0 && (<><h3>Appointments</h3><ul className="ins-list">{data.appointments.map(a => (
              <li key={a.id}>{fmtDateTime(a.starts_at, tz)} · {a.type} · {a.medium?.replace('_', ' ')} · {a.agent} · <b>{a.status}</b></li>))}</ul></>)}
          </div>
        )}
      </div>
      {assign && <AssignDialog row={assign} onClose={() => setAssign(null)} onDone={m => { setAssign(null); after(m) }} />}
      {book && <BookDialog row={book} onClose={() => setBook(null)} onDone={m => { setBook(null); after(m) }} />}
      {toast}
    </div>
  )
}

function TimelineItem({ e, tz }) {
  const d = e.detail || {}
  let label, body = e.body
  switch (e.kind) {
    case 'outreach': label = `Renewal reminder · ${e.channel || 'message'} · ${e.delivery_status === 'simulated' ? 'simulated, not sent' : e.delivery_status}`; break
    case 'reply': label = `Client reply${d.simulated ? ' (simulated)' : ''} · ${shortReason(d.classification?.code) || ''}${d.classification ? ' (inferred)' : ''}`; break
    case 'note': label = `Note${e.actor ? ` · ${e.actor}` : ''}`; break
    case 'review_booked': label = 'Review booked'; body = `${fmtDateTime(d.starts_at, tz)} with ${d.agent || 'agent'}`; break
    case 'assigned': case 'reassigned': case 'claimed':
      label = e.kind === 'claimed' ? 'Claimed from shared queue' : e.kind === 'assigned' ? 'Assigned' : 'Reassigned'
      body = `${d.from_name ? `${d.from_name} → ` : ''}${d.to_name || 'unassigned'}${d.reason ? ` · ${d.reason}` : ''}`; break
    case 'outcome': label = 'Outcome recorded'; body = `${d.outcome}${d.note ? ` — ${d.note}` : ''}`; break
    case 'enrolled': label = 'Enrolled in workflow'; body = d.workflow; break
    default: label = e.kind.replace(/_/g, ' ')
  }
  return (
    <li className={`ins-thread__item ins-thread__item--${e.direction || 'internal'} ${e.delivery_status === 'failed' ? 'is-failed' : ''}`}>
      <div className="ins-thread__meta"><span>{label}</span><time>{fmtDateTime(e.at, tz)}</time></div>
      {body && <div className="ins-thread__body">{body}</div>}
      {d.failure && <div className="ins-thread__fail">{d.failure}</div>}
    </li>
  )
}

// ───────────────────────────── CONVERSATIONS ─────────────────────────────

export function ConversationsPage() {
  const c = useIns()
  const [openOnly, setOpenOnly] = useState(true)
  const { data, error, reload } = useData(() => c.q('/agency/retention/conversations', { open_only: openOnly ? 'true' : '' }), [openOnly, c.tick, c.q])
  return (
    <div>
      <div className="ins-pagehead"><h1>Conversations</h1>
        <label className="ins-check"><input type="checkbox" checked={openOnly} onChange={e => setOpenOnly(e.target.checked)} /> Only replies waiting for a person</label></div>
      <Err error={error} onRetry={reload} />
      <section className="ins-card">
        {!data ? <div className="ins-loading">Loading…</div> : data.items.length === 0 ? <Empty>{openOnly ? 'No replies are waiting for a person.' : 'No conversations yet.'}</Empty> : (
          <ul className="ins-threads">{data.items.map(t => (
            <li key={t.lead_id}><button onClick={() => c.openClient(t.lead_id)}>
              <span className="ins-avatar">{initials(t.household)}</span>
              <span className="ins-threads__main"><b>{t.household}</b>
                <span className="ins-threads__last">{t.last_direction === 'in' ? '' : 'You: '}{t.last_body}</span></span>
              <span className="ins-threads__side"><time>{agoText(t.last_at)}</time>
                {t.unhandled > 0 && <span className="ins-pill ins-pill--red">{t.reason ? shortReason(t.classification) : 'Needs a person'}</span>}
                {t.last_status === 'simulated' && <small>simulated</small>}</span>
            </button></li>))}</ul>
        )}
      </section>
      <p className="ins-fine">Replies are untrusted text from clients: they are stored and routed, and can never change settings or approve anything. Reply categories are keyword inferences.</p>
    </div>
  )
}

// ───────────────────────────── APPOINTMENTS ─────────────────────────────

export function AppointmentsPage() {
  const c = useIns()
  const [days, setDays] = useState(7)
  const { data, error, reload } = useData(() => c.q('/agency/retention/appointments', { days, location_id: c.locationId, agent_id: c.agentId }), [days, c.locationId, c.agentId, c.tick, c.q])
  const [toast, say] = useToast()
  const tz = c.settings.timezone
  const groups = useMemo(() => {
    const g = {}
    for (const a of data?.items || []) {
      const k = new Date(a.starts_at).toLocaleDateString('en-US', { timeZone: tz, weekday: 'long', month: 'short', day: 'numeric' })
      ;(g[k] = g[k] || []).push(a)
    }
    return Object.entries(g)
  }, [data, tz])
  const set = async (a, status) => { try { await api.post(c.q(`/agency/retention/appointments/${a.id}/status`), { status }); say(`Marked ${status.replace('_', ' ')}.`); reload(); c.refresh() } catch (e) { say(errText(e), 'err') } }
  return (
    <div>
      <div className="ins-pagehead"><h1>Appointments</h1>
        <label className="ins-select ins-select--light"><span className="sr-only">Range</span>
          <select value={days} onChange={e => setDays(Number(e.target.value))}><option value={1}>Today</option><option value={7}>Next 7 days</option><option value={14}>Next 14 days</option><option value={30}>Next 30 days</option></select></label></div>
      <Err error={error} onRetry={reload} />
      {!data ? <div className="ins-loading">Loading…</div> : groups.length === 0 ? <section className="ins-card"><Empty>No appointments in this range.</Empty></section> : groups.map(([day, items]) => (
        <section className="ins-card" key={day}><h2 className="ins-day">{day}</h2>
          <ul className="ins-appts ins-appts--full">{items.map(a => (
            <li key={a.id}>
              <span className="ins-appts__time">{fmtTime(a.starts_at, tz)}</span>
              <button className="ins-appts__body" onClick={() => c.openClient(a.lead_id)}><b>{a.notes || a.title}</b>
                <span>{a.household}{a.product_label ? ` · ${a.product_label}` : ''}{a.location ? ` · ${a.location}` : ''} · {a.agent_name}</span></button>
              <span className="ins-pill ins-pill--blue">{a.status}</span>
              {!c.readOnly && ['booked', 'confirmed'].includes(a.status) && <span className="ins-row">
                <button className="ins-btn ins-btn--sm" onClick={() => set(a, 'completed')}>Held</button>
                <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={() => set(a, 'no_show')}>No-show</button></span>}
            </li>))}</ul></section>))}
      {toast}
    </div>
  )
}

// ───────────────────────────── TEAM & LOCATIONS ─────────────────────────────

export function TeamPage() {
  const c = useIns()
  const { data, error, reload } = useData(() => c.q('/agency/retention/team'), [c.tick, c.q])
  const [toast, say] = useToast()
  const toggle = async p => { try { await api.patch(c.q(`/agency/retention/team/${p.user_id}`), { available: !p.available }); say(`${p.name} marked ${p.available ? 'unavailable' : 'available'} for new work.`); reload() } catch (e) { say(errText(e), 'err') } }
  return (
    <div>
      <div className="ins-pagehead"><h1>Team & Locations</h1></div>
      <Err error={error} onRetry={reload} />
      {!data ? <div className="ins-loading">Loading…</div> : (<>
        <section className="ins-locs">{data.locations.map(l => (
          <div className="ins-card ins-loc" key={l.id}>
            <h2><Icon name="pin" /> {l.name} {l.is_primary && <span className="ins-tag ins-tag--blue">primary</span>}</h2>
            {l.verified && l.address ? <p>{l.address}, {l.city}, {l.state}<br />{l.phone}</p> : <p className="ins-muted">Illustrative location — no real address.</p>}
            <p className="ins-fine">{l.staff} staff · {l.timezone || c.settings.timezone}</p>
          </div>))}</section>
        <section className="ins-card"><h2>People</h2>
          <div className="ins-table-wrap"><table className="ins-table">
            <thead><tr><th>Name</th><th>Role</th><th>Languages</th><th>Open renewals</th><th>Reviews (14 d)</th><th>Overdue</th><th>Available</th></tr></thead>
            <tbody>{data.people.map(p => (
              <tr key={p.user_id}>
                <td><span className="ins-person"><span className="ins-avatar ins-avatar--sm">{initials(p.name)}</span>{p.name}</span><small>{data.locations.filter(l => p.location_ids.includes(l.id)).map(l => l.name).join(', ')}</small></td>
                <td>{p.title || '—'}</td><td>{p.languages.map(x => x.toUpperCase()).join(' · ')}</td>
                <td>{p.open_renewals}</td>
                <td>{p.review_capacity != null ? `${p.reviews_next_14_days} of ${p.review_capacity}` : p.reviews_next_14_days}</td>
                <td>{p.overdue > 0 ? <span className="ins-pill ins-pill--purple">{p.overdue}</span> : 0}</td>
                <td>{c.canManage ? <button className="ins-btn ins-btn--sm" onClick={() => toggle(p)} aria-pressed={p.available}>{p.available ? 'Yes' : 'No'}</button> : (p.available ? 'Yes' : 'No')}</td>
              </tr>))}</tbody></table></div>
          {c.isDemo && <p className="ins-fine">All staff shown are synthetic demo accounts. None can sign in.</p>}
        </section>
      </>)}
      {toast}
    </div>
  )
}

// ───────────────────────────── REPORTS ─────────────────────────────

export function ReportsPage() {
  const c = useIns()
  const today = new Date()
  const iso = d => d.toISOString().slice(0, 10)
  const [start, setStart] = useState(iso(new Date(today.getTime() - 30 * 86400000)))
  const [end, setEnd] = useState(iso(new Date(today.getTime() + 60 * 86400000)))
  const { data, error, reload } = useData(() => c.q('/agency/retention/reports', { start, end, location_id: c.locationId, agent_id: c.agentId }), [start, end, c.locationId, c.agentId, c.tick, c.q])
  return (
    <div>
      <div className="ins-pagehead"><h1>Reports</h1>
        <div className="ins-row"><label className="ins-inline">From <input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
          <label className="ins-inline">To <input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label></div></div>
      <Err error={error} onRetry={reload} />
      {!data ? <div className="ins-loading">Loading…</div> : (<>
        <p className="ins-muted">Cohort: {data.cohort.definition} {data.data === 'demo' && <span className="ins-tag ins-tag--blue">demo data only</span>}</p>
        <section className="ins-card"><h2>Renewal funnel</h2>
          <table className="ins-table ins-table--compact"><thead><tr><th>Stage</th><th>Count</th><th>Of</th><th>%</th><th /></tr></thead>
            <tbody>{data.funnel.map(f => (<tr key={f.key}><td>{f.label}{f.note && <small>{f.note}</small>}</td><td><b>{f.count}</b></td><td>{f.of ?? '—'}</td><td>{f.pct != null ? `${f.pct}%` : '—'}</td>
              <td className="ins-bar-cell">{f.of ? <span className="ins-bar"><span style={{ width: `${f.pct || 0}%` }} /></span> : null}</td></tr>))}</tbody></table>
          <p className="ins-fine"><b>Retention rate:</b> {data.retention.rate != null ? `${data.retention.rate}% of ${data.retention.of} decided renewals` : 'no recorded outcomes yet'}. {data.retention.definition}</p>
          <p className="ins-fine">{data.revenue_note}</p>
        </section>
        <div className="ins-grid ins-grid--even">
          <section className="ins-card"><h2>By location</h2><table className="ins-table ins-table--compact">
            <thead><tr><th>Location</th><th>Due</th><th>Confirmed</th><th>Lost</th><th>Unresolved</th></tr></thead>
            <tbody>{data.by_location.map(l => <tr key={l.location}><td>{l.location}</td><td>{l.due}</td><td>{l.confirmed}</td><td>{l.lost}</td><td>{l.unresolved}</td></tr>)}</tbody></table></section>
          <section className="ins-card"><h2>Outreach delivery</h2>
            {Object.keys(data.delivery).length === 0 ? <Empty>No outreach in this cohort.</Empty> : (
              <ul className="ins-list">{Object.entries(data.delivery).map(([k, v]) => <li key={k}><b>{v}</b> {k === 'simulated' ? 'simulated (not sent)' : k}</li>)}</ul>)}
          </section>
        </div>
      </>)}
    </div>
  )
}

// ───────────────────────────── AUTOMATION ─────────────────────────────

export function AutomationPage() {
  const c = useIns()
  const { data, error, reload } = useData(() => c.q('/agency/retention/workflows'), [c.tick, c.q])
  const [preview, setPreview] = useState(null)
  const [editing, setEditing] = useState(null)
  const [toast, say] = useToast()
  const call = async (path, msg) => { try { const r = await api.post(c.q(path), {}); say(typeof msg === 'function' ? msg(r) : msg); reload(); c.refresh() } catch (e) { say(errText(e), 'err') } }
  return (
    <div>
      <div className="ins-pagehead"><h1>Automation</h1>
        {c.canManage && <button className="ins-btn ins-btn--primary" onClick={() => setEditing({ name: 'Annual review invitations', offsets: c.settings.default_offsets, channel: 'sms', window_days: 60 })}>New workflow</button>}</div>
      <p className="ins-muted">Timing is an agency setting, not a carrier rule. Messages are only recorded inside the {c.settings.send_window_start}–{c.settings.send_window_end} send window in each location’s time zone. A reply pauses automation for that client until a person handles it.</p>
      <Err error={error} onRetry={reload} />
      {!data ? <div className="ins-loading">Loading…</div> : data.items.length === 0 ? <section className="ins-card"><Empty>No workflows yet.</Empty></section> : data.items.map(w => (
        <section className="ins-card ins-wf" key={w.id}>
          <div className="ins-card__head"><h2>{w.name}</h2><span className={`ins-dot ins-dot--${w.state === 'running' ? 'running' : 'paused'}`}>{w.state}</span></div>
          <p>{w.channel.toUpperCase()} · {w.offsets.join(' / ')} days before renewal · renewals within {w.window_days} days
            {' '}· <span className="ins-tag ins-tag--blue">{w.delivery_mode === 'simulated' ? 'Simulated delivery' : w.delivery_mode}</span></p>
          <p className="ins-fine">Enrollments — active {w.enrollments.active}, waiting on a person {w.enrollments.needs_human}, paused {w.enrollments.paused}, completed {w.enrollments.completed}, stopped {w.enrollments.stopped}. Last run {w.last_run_at ? fmtDateTime(w.last_run_at, c.settings.timezone) : 'never'}.</p>
          <blockquote className="ins-quote">{w.template}</blockquote>
          {c.canManage && <div className="ins-row ins-row--wrap">
            <button className="ins-btn ins-btn--sm" onClick={async () => { try { setPreview({ w, d: await api.get(c.q(`/agency/retention/workflows/${w.id}/preview`)) }) } catch (e) { say(errText(e), 'err') } }}>Preview eligible</button>
            {w.state === 'draft' && <button className="ins-btn ins-btn--sm ins-btn--primary" onClick={() => call(`/agency/retention/workflows/${w.id}/start`, r => `Started: ${r.enrolled} enrolled, ${r.excluded} excluded.`)}>Start</button>}
            {w.state === 'running' && <button className="ins-btn ins-btn--sm" onClick={() => call(`/agency/retention/workflows/${w.id}/pause`, 'Paused.')}>Pause</button>}
            {w.state === 'paused' && <button className="ins-btn ins-btn--sm" onClick={() => call(`/agency/retention/workflows/${w.id}/resume`, 'Resumed.')}>Resume</button>}
            {w.state === 'running' && <button className="ins-btn ins-btn--sm" onClick={() => call(`/agency/retention/workflows/${w.id}/start`, r => `${r.enrolled} newly eligible renewals enrolled.`)}>Enroll new eligible</button>}
            {w.state === 'running' && <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={() => call(`/agency/retention/workflows/${w.id}/run-due`, r => `${r.processed} due step(s) recorded, ${r.deferred_quiet_hours} held for the send window, ${r.skipped_duplicate} duplicates skipped.`)}>Run due steps now</button>}
            <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={() => setEditing(w)}>Edit</button>
          </div>}
        </section>))}
      {preview && (
        <Modal title={`Preview — ${preview.w.name}`} onClose={() => setPreview(null)} width={760}>
          <p><b>{preview.d.eligible_count}</b> eligible · <b>{preview.d.excluded_count}</b> excluded</p>
          {Object.keys(preview.d.exclusion_summary).length > 0 && <ul className="ins-list">{Object.entries(preview.d.exclusion_summary).map(([k, v]) => <li key={k}>{v} × {k}</li>)}</ul>}
          {preview.d.sample_message && <blockquote className="ins-quote">{preview.d.sample_message}</blockquote>}
          <div className="ins-table-wrap ins-scroll"><table className="ins-table ins-table--compact"><thead><tr><th>Household</th><th>Policy</th><th>Renews</th><th>Status</th></tr></thead>
            <tbody>{[...preview.d.eligible.map(x => ({ ...x, ok: true })), ...preview.d.excluded].slice(0, 80).map(x => (
              <tr key={x.id}><td>{x.household}</td><td>{x.product_label}</td><td>{fmtDate(x.renewal_date)}</td><td>{x.ok ? <span className="ins-pill ins-pill--green">Eligible</span> : <small>{x.reasons.join('; ')}</small>}</td></tr>))}</tbody></table></div>
        </Modal>)}
      {editing && <WorkflowEditor wf={editing} onClose={() => setEditing(null)} onSaved={m => { setEditing(null); say(m); reload() }} />}
      {toast}
    </div>
  )
}

function WorkflowEditor({ wf, onClose, onSaved }) {
  const c = useIns()
  const [name, setName] = useState(wf.name)
  const [offsets, setOffsets] = useState((wf.offsets || []).join(', '))
  const [channel, setChannel] = useState(wf.channel || 'sms')
  const [windowDays, setWindowDays] = useState(wf.window_days || 60)
  const [template, setTemplate] = useState(wf.template || '')
  const [err, setErr] = useState(null)
  const save = async e => {
    e.preventDefault()
    const body = { name, offsets: offsets.split(/[ ,/]+/).filter(Boolean).map(Number), channel, window_days: Number(windowDays), template: template || null }
    try {
      if (wf.id) await api.patch(c.q(`/agency/retention/workflows/${wf.id}`), body)
      else await api.post(c.q('/agency/retention/workflows'), body)
      onSaved(wf.id ? 'Workflow updated (change audited).' : 'Workflow created as a draft. Preview it, then Start.')
    } catch (er) { setErr(er) }
  }
  return (
    <Modal title={wf.id ? 'Edit workflow' : 'New workflow'} onClose={onClose} width={560}>
      <form className="ins-form" onSubmit={save}>
        <label>Name<input value={name} onChange={e => setName(e.target.value)} required maxLength={120} /></label>
        <div className="ins-row"><label>Days before renewal<input value={offsets} onChange={e => setOffsets(e.target.value)} required /></label>
          <label>Channel<select value={channel} onChange={e => setChannel(e.target.value)}><option value="sms">Text</option><option value="email">Email</option></select></label>
          <label>Window (days)<input type="number" min={1} max={180} value={windowDays} onChange={e => setWindowDays(e.target.value)} /></label></div>
        <label>Message template<textarea rows={4} value={template} onChange={e => setTemplate(e.target.value)} placeholder="Leave blank for the default. Fields: {first_name} {product} {renewal_date} {agency} {agent}" /></label>
        <p className="ins-fine">Templates use stored record fields only — never prices, quotes or coverage statements.</p>
        {err && <div className="ins-alert" role="alert">{errText(err)}</div>}
        <div className="ins-row ins-row--end"><button type="button" className="ins-btn" onClick={onClose}>Cancel</button><button className="ins-btn ins-btn--primary">Save</button></div>
      </form>
    </Modal>
  )
}

// ───────────────────────────── IMPORT ─────────────────────────────

const SAMPLE = 'household_name,first_name,last_name,email,phone,preferred_language,location,policy_number,product,renewal_date,policy_status,servicing_agent_email\n'
export function ImportPage() {
  const c = useIns()
  const [text, setText] = useState('')
  const [name, setName] = useState('import.csv')
  const [pv, setPv] = useState(null)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  const file = e => { const f = e.target.files?.[0]; if (!f) return; setName(f.name); const r = new FileReader(); r.onload = () => { setText(String(r.result || '')); setPv(null); setRes(null) }; r.readAsText(f) }
  const preview = async () => { setErr(null); try { setPv(await api.post(c.q('/agency/retention/import/preview'), { csv_text: text, filename: name })) } catch (e) { setErr(e) } }
  const commit = async () => { setErr(null); try { setRes(await api.post(c.q('/agency/retention/import/commit'), { csv_text: text, filename: name })); c.refresh() } catch (e) { setErr(e) } }
  return (
    <div>
      <div className="ins-pagehead"><h1>Import clients & policies</h1></div>
      <p className="ins-muted">CSV only. Nothing is deleted or replaced: matches update blanks and add new renewal terms. There is no carrier connection — imported rows are labeled “CSV import” with the time they were loaded.</p>
      <section className="ins-card ins-form">
        <label>CSV file<input type="file" accept=".csv,text/csv" onChange={file} /></label>
        <label>…or paste<textarea rows={6} value={text} onChange={e => { setText(e.target.value); setPv(null) }} placeholder={SAMPLE} /></label>
        <div className="ins-row"><button className="ins-btn" onClick={preview} disabled={!text.trim()}>Preview</button>
          {pv && pv.summary.create + pv.summary.update > 0 && <button className="ins-btn ins-btn--primary" onClick={commit}>Import {pv.summary.create + pv.summary.update} row(s)</button>}</div>
        {err && <div className="ins-alert" role="alert">{errText(err)}</div>}
      </section>
      {pv && <section className="ins-card"><h2>Preview</h2>
        <p>{pv.summary.total} rows · {pv.summary.create} new · {pv.summary.update} matched · <b>{pv.summary.errors} with errors</b> (skipped)</p>
        {pv.missing_required_mapping.length > 0 && <p className="ins-warn">Missing required columns: {pv.missing_required_mapping.join(', ')}</p>}
        <p className="ins-fine">{pv.timezone_note}</p>
        <div className="ins-table-wrap ins-scroll"><table className="ins-table ins-table--compact"><thead><tr><th>Row</th><th>Action</th><th>Household</th><th>Policy</th><th>Renews</th><th>Problems</th></tr></thead>
          <tbody>{pv.rows.map(r => <tr key={r.row}><td>{r.row}</td><td><span className={`ins-pill ins-pill--${r.action === 'error' ? 'red' : r.action === 'update' ? 'blue' : 'green'}`}>{r.action}</span></td>
            <td>{r.values.household_name || r.values.last_name}</td><td>{r.values.product} {r.values.policy_number}</td><td>{r.values.renewal_date || '—'}</td>
            <td><small>{[...r.errors, ...r.warnings].join('; ')}</small></td></tr>)}</tbody></table></div></section>}
      {res && <div className="ins-alert ins-alert--ok" role="status">Imported: {res.created} new, {res.updated} updated, {res.skipped_rows_with_errors} skipped.</div>}
    </div>
  )
}

// ───────────────────────────── SETTINGS ─────────────────────────────

export function SettingsPage() {
  const c = useIns()
  const s = c.settings
  const [form, setForm] = useState({ display_name: s.display_name, tagline: s.tagline, send_window_start: s.send_window_start,
    send_window_end: s.send_window_end, routing_mode: s.routing_mode, shared_queue: s.shared_queue, review_minutes: s.review_minutes,
    default_offsets: s.default_offsets.join(', ') })
  const [toast, say] = useToast()
  const owner = c.viewer.kind === 'owner' && !c.readOnly
  const save = async e => {
    e.preventDefault()
    try { await api.patch(c.q('/agency/retention/settings'), { ...form, review_minutes: Number(form.review_minutes), default_offsets: form.default_offsets.split(/[ ,/]+/).filter(Boolean).map(Number) })
      say('Settings saved (audited).'); c.reloadWorkspace() } catch (er) { say(errText(er), 'err') }
  }
  const reset = async () => {
    if (!window.confirm('Reset the demo to its starting state? Synthetic records only.')) return
    try { await api.post(c.q('/agency/retention/demo/reset'), {}); say('Demo reset.'); c.refresh() } catch (er) { say(errText(er), 'err') }
  }
  const f = k => ({ value: form[k], onChange: e => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }), disabled: !owner })
  return (
    <div>
      <div className="ins-pagehead"><h1>Settings</h1></div>
      <form className="ins-card ins-form" onSubmit={save}>
        <h2>Workspace</h2>
        <label>Display name<input {...f('display_name')} maxLength={120} /></label>
        <label>Tagline<input {...f('tagline')} maxLength={160} /></label>
        <h2>Renewal defaults</h2>
        <div className="ins-row"><label>Default days before renewal<input {...f('default_offsets')} /></label>
          <label>Send window start<input type="time" {...f('send_window_start')} /></label><label>Send window end<input type="time" {...f('send_window_end')} /></label></div>
        <div className="ins-row"><label>Routing<select {...f('routing_mode')}><option value="least_loaded">Least loaded</option><option value="round_robin">Round robin</option></select></label>
          <label>Review length (minutes)<input type="number" min={10} max={180} {...f('review_minutes')} /></label></div>
        <label className="ins-check"><input type="checkbox" checked={form.shared_queue} onChange={f('shared_queue').onChange} disabled={!owner} /> Agents may claim unassigned work at their locations</label>
        {owner ? <div className="ins-row ins-row--end"><button className="ins-btn ins-btn--primary">Save settings</button></div> : <p className="ins-fine">Only the agency owner or admin can change these.</p>}
      </form>
      <section className="ins-card"><h2>Data & delivery</h2>
        <p>Data source: <b>{s.is_demo ? 'Synthetic demo data' : s.data_mode.replace('_', ' ')}</b>. No carrier or Allstate system is connected.</p>
        <p>Message delivery: <b>simulated</b>. Workflows record what would be sent; nothing reaches a client until a sender is approved and connected through EvoSys Pro.</p>
        {s.reference && <><h3>Public reference</h3><ul className="ins-list"><li>{s.reference.public_identity}</li><li>{s.reference.office}</li><li>{s.reference.phone}</li>
          <li>Service in {s.reference.languages.join(' and ')}</li></ul><p className="ins-fine">{s.reference.note}</p></>}
        {s.is_demo && owner && <button className="ins-btn" onClick={reset}>Reset demo data</button>}
      </section>
      {toast}
    </div>
  )
}
