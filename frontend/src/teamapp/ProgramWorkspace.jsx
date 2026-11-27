/**
 * EVOSYS PRO MOBILE - A PROGRAM ON THE PHONE (/app/p/<pid>/...).
 *
 * Tapping a program on Programs opens this light workspace instead of the full
 * web screens: Dashboard (default), Replies (programs with a reply queue) and
 * More. The full web workspace is one tap away under More ("Open Full
 * Workspace"), and its Back / Program Access link returns here.
 *
 *   program  SCI and Restland (program workspaces): today's numbers, Needs a
 *            Reply, upcoming appointments; Replies answers a family in place
 *            through the existing send path (/program/command/reply).
 *   sales    the Back Office: the follow-ups, appointments and decisions the
 *            launcher already reports (/launcher/attention).
 *   other    every other program: what it is, and its full workspace.
 *
 * NOTHING HERE DECIDES ACCESS. The entry is found again in this login's own
 * /auth/my-contexts answer (a link to anything else shows "not available"),
 * and every request is re-authorized on the server for the workspace it names.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, useNavigate, useParams } from 'react-router-dom'
import { api, clearWorkspaceContext, fetchMyContexts, setBrandContext } from '../api/client'
import { formatPhone } from '../utils/phone'
import AppointmentSheet from './AppointmentSheet'
import { BookitDashboard } from './BookitPhone'
import { programInventory } from '../pages/program/programAccessModel'
import { openProgramItem } from '../pages/program/programDestinations'
import { MOBILE_PROGRAMS, setLauncherOrigin } from '../pages/program/launcherOrigin'
import { ErrorNote, Icon, Loading, useTeam } from './TeamApp'
import { ago, contactLinks, countLabel, draftKey, findProgram, hasLeads, looksLikeEmail, mobileKind, upcomingBooking, mobileProgramPath, programRequest,
         programTabs, whenLabel } from './programMobile'
import { programStyle } from './teamHelpers'

const OPTS = { noOrgContext: true, skipRedirect: true }
const LOC_KEY = 'af_mobile_program_location'

/** The program this link names, looked up in this login's own inventory. */
export function useEntry() {
  const { pid } = useParams()
  const [state, setState] = useState({ phase: 'loading', item: null, error: null })
  const [n, setN] = useState(0)
  useEffect(() => {
    let live = true
    setState(s => ({ ...s, phase: 'loading', error: null }))
    fetchMyContexts({ force: n > 0 }).then(async c => {
      let deal = c && c.deal_builder
      if (c && !deal) { try { deal = await api.get('/deal-builder/access', OPTS) } catch { deal = null } }
      if (!live) return
      const item = findProgram(programInventory(c || {}, deal, MOBILE_PROGRAMS), pid)
      setState({ phase: item ? 'ready' : 'missing', item, error: null })
    }).catch(e => { if (live) setState({ phase: 'error', item: null, error: e }) })
    return () => { live = false }
  }, [pid, n])
  return { ...state, pid, retry: () => setN(x => x + 1) }
}

/** GET in one program's own workspace: {data, error, loading, reload}. */
export function useProgramGet(item, path) {
  const [state, setState] = useState({ data: null, error: null, loading: !!(item && path) })
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!item || !path) return undefined
    let live = true
    setState(s => ({ data: s.data, error: null, loading: true }))
    api.get(path, programRequest(item))
      .then(d => { if (live) setState({ data: d, error: null, loading: false }) })
      .catch(e => { if (live) setState({ data: null, error: e, loading: false }) })
    return () => { live = false }
  }, [item, path, n])
  const reload = useCallback(() => setN(x => x + 1), [])
  return { ...state, reload }
}

function useLocationChoice(item) {
  const key = LOC_KEY + ':' + ((item && item.organization_id) || '')
  const [loc, setLoc] = useState(() => { try { return sessionStorage.getItem(key) || '' } catch { return '' } })
  const choose = v => { setLoc(v); try { if (v) sessionStorage.setItem(key, v); else sessionStorage.removeItem(key) } catch { /* ignore */ } }
  return [loc, choose]
}

function openFull(item, navigate, origin) {
  openProgramItem(item, navigate, { origin })
}

/** A launcher "needs attention" link (Back Office pages), returning here afterwards. */
function openSalesLink(item, navigate, origin, href) {
  setLauncherOrigin(origin)
  clearWorkspaceContext()
  if (item.platform_id) setBrandContext(item.platform_id, item.platform_name || '')
  navigate(href)
}

// ── Frame ───────────────────────────────────────────────────────────────────
export function Frame({ entry, children, tab }) {
  const navigate = useNavigate()
  const { me } = useTeam()
  const work = !!(me && me.previews && me.previews.phone_work)
  const item = entry.item
  const look = item ? programStyle(item) : null
  return (
    <div className="ta-screen ta-prog">
      <div className="ta-prog-head">
        <button type="button" className="ta-prog-back" onClick={() => navigate('/app/programs')}>
          <Icon name="back" size={20} /> Programs</button>
        {item && (
          <div className="ta-prog-title" style={{ '--acc': look.accent }}>
            <span className="ta-prog-icon" aria-hidden><Icon name={look.icon} size={22} /></span>
            <h1>{item.display}</h1>
          </div>
        )}
        {item && item.orgLabel && <p className="ta-muted ta-small ta-prog-sub">{item.orgLabel}</p>}
      </div>
      {item && (
        <nav className="ta-seg ta-prog-tabs" aria-label={item.display + ' sections'}>
          {programTabs(item, { work }).map(t => (
            <NavLink key={t.key || 'dash'} end={!t.key} to={mobileProgramPath(item, t.key)}
                     className={() => (tab === (t.key || 'dashboard') ? 'is-on' : '')}
                     aria-current={tab === (t.key || 'dashboard') ? 'page' : undefined}>{t.label}</NavLink>
          ))}
        </nav>
      )}
      {entry.phase === 'loading' && <Loading />}
      {entry.phase === 'error' && <ErrorNote error={entry.error} onRetry={entry.retry} />}
      {entry.phase === 'missing' && (
        <div className="ta-state" role="alert">
          <p>This program is not available on your account.</p>
          <button type="button" className="ta-btn ta-btn--ghost" onClick={() => navigate('/app/programs')}>Back to Programs</button>
        </div>
      )}
      {entry.phase === 'ready' && children}
    </div>
  )
}

function Stat({ value, label, note }) {
  return (
    <div className="ta-stat">
      <span className="ta-stat-n">{value === null || value === undefined ? '–' : value}</span>
      <span className="ta-stat-l">{label}</span>
      {note && <span className="ta-stat-note">{note}</span>}
    </div>
  )
}

function LocationPicker({ locations, value, onChange }) {
  if (!locations || locations.length < 2) return null
  return (
    <label className="ta-prog-loc">
      <span className="ta-sr">Location</span>
      <select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">All locations</option>
        {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>
    </label>
  )
}

function QueueRow({ i, onOpen }) {
  const flag = i.human_handoff ? 'Needs a person' : (i.sla_breached ? 'Overdue' : (i.badge || null))
  return (
    <li>
      <button type="button" className="ta-row ta-qrow" onClick={onOpen}>
        <span className="ta-row-main">
          <span className="ta-row-name">{i.name}</span>
          <span className="ta-qrow-text">{i.summary || (i.channel === 'email' ? (i.subject || 'Email') : 'Message')}</span>
          <span className="ta-row-sub">
            {[i.channel_label, i.location, ago(i.received_at)].filter(Boolean).join(' · ')}
          </span>
        </span>
        {flag && <span className={'ta-flag' + (i.human_handoff || i.sla_breached ? ' is-hot' : '')}>{flag}</span>}
        <Icon name="chev" size={18} />
      </button>
    </li>
  )
}

// ── Dashboard ───────────────────────────────────────────────────────────────
function ProgramDashboardBody({ item }) {
  const navigate = useNavigate()
  const [loc, setLoc] = useLocationChoice(item)
  const path = '/program/command/mobile/summary' + (loc ? '?location_id=' + encodeURIComponent(loc) : '')
  const s = useProgramGet(item, path)
  const d = s.data
  const tz = (d && d.time_zone) || 'America/Chicago'
  // A saved location that is no longer this workspace's: back to all locations.
  useEffect(() => { if (s.error && s.error.status === 404 && loc) setLoc('') }, [s.error, loc]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      {d && <LocationPicker locations={d.locations} value={loc} onChange={setLoc} />}
      {s.loading && !d && <Loading />}
      {s.error && <ErrorNote error={s.error} onRetry={s.reload} />}
      {d && (
        <>
          <p className="ta-stats-h" id="ta-today-h">TODAY</p>
          <div className="ta-stats" aria-labelledby="ta-today-h">
            <Stat value={d.stats.sent_today.total} label="Messages sent"
                  note={d.stats.sent_today.emails ? d.stats.sent_today.texts + ' texts · ' + d.stats.sent_today.emails + ' emails' : null} />
            <Stat value={d.stats.replies_today} label="Replies received" />
            <Stat value={d.stats.booked_today} label="Appointments booked" />
          </div>
          <p className="ta-muted ta-small ta-prog-note">
            {d.taking_place_today === 1 ? '1 appointment is' : d.taking_place_today + ' appointments are'} taking place today · Central time</p>

          <section className="ta-section" aria-labelledby="ta-nr-h">
            <div className="ta-section-head">
              <h2 id="ta-nr-h" className="ta-h2">Needs a Reply{d.needs_reply.count ? ' (' + d.needs_reply.count + ')' : ''}</h2>
              {d.needs_reply.count > 0 && <button type="button" className="ta-linkbtn" onClick={() => navigate(mobileProgramPath(item, 'replies'))}>See all</button>}
            </div>
            {d.needs_reply.count === 0
              ? <div className="ta-card ta-muted">Nobody is waiting for a reply right now.</div>
              : <ul className="ta-list">
                  {d.needs_reply.items.map(i => (
                    <QueueRow key={i.lead_id} i={i} onOpen={() => navigate(mobileProgramPath(item, 'replies') + '/' + encodeURIComponent(i.lead_id))} />
                  ))}
                </ul>}
          </section>

          <section className="ta-section" aria-labelledby="ta-up-h">
            <h2 id="ta-up-h" className="ta-h2">Next appointments</h2>
            {d.upcoming.length === 0
              ? <div className="ta-card ta-muted">No upcoming appointments.</div>
              : <ul className="ta-list">
                  {d.upcoming.map(a => (
                    <li key={a.booking_id}>
                      <button type="button" className="ta-row" onClick={() => navigate(mobileProgramPath(item, 'replies') + '/' + encodeURIComponent(a.lead_id))}>
                        <span className="ta-row-icon"><Icon name="calendar" /></span>
                        <span className="ta-row-main">
                          <span className="ta-row-name">{a.name}</span>
                          <span className="ta-row-sub">{[whenLabel(a.when, tz), a.label, a.location].filter(Boolean).join(' · ')}</span>
                        </span>
                        <Icon name="chev" size={18} />
                      </button>
                    </li>
                  ))}
                </ul>}
          </section>
        </>
      )}
    </>
  )
}

function SalesDashboardBody({ item, origin }) {
  const navigate = useNavigate()
  const s = useProgramGet(item, '/launcher/attention')
  const items = (s.data && s.data.items) || []
  return (
    <>
      {s.loading && !s.data && <Loading />}
      {s.error && <ErrorNote error={s.error} onRetry={s.reload} />}
      {s.data && (
        <section className="ta-section" aria-labelledby="ta-sales-h">
          <h2 id="ta-sales-h" className="ta-h2">Next up</h2>
          {items.length === 0
            ? <div className="ta-card ta-muted">No follow-ups due, appointments this week or decisions waiting.</div>
            : <ul className="ta-list">
                {items.map(a => (
                  <li key={a.kind + (a.href || '')}>
                    <button type="button" className="ta-row" onClick={() => openSalesLink(item, navigate, origin, a.href)}>
                      <span className="ta-row-icon"><Icon name={a.kind === 'next_appointment' ? 'calendar' : (a.kind === 'follow_ups' ? 'reply' : 'check')} /></span>
                      <span className="ta-row-main">
                        <span className="ta-row-name">{a.title}</span>
                        <span className="ta-row-sub">{a.kind === 'next_appointment' && a.at ? whenLabel(a.at) : a.detail}</span>
                      </span>
                      <Icon name="chev" size={18} />
                    </button>
                  </li>
                ))}
              </ul>}
          <p className="ta-muted ta-small">Lead conversations open in the full workspace.</p>
        </section>
      )}
    </>
  )
}

function OtherDashboardBody({ item, origin }) {
  const navigate = useNavigate()
  return (
    <section className="ta-section">
      <div className="ta-card">
        {item.kind && <p className="ta-prog-kind">{item.kind}</p>}
        {item.summary && <p>{item.summary}</p>}
        {item.facts && item.facts.length > 0 && <p className="ta-muted ta-small">{item.facts.join(' · ')}</p>}
      </div>
      <button type="button" className="ta-btn ta-btn--primary ta-btn--block" onClick={() => openFull(item, navigate, origin)}>
        Open Full Workspace</button>
      <p className="ta-muted ta-small ta-prog-note">This program's numbers and work queue are in its full workspace.</p>
    </section>
  )
}

export function ProgramDashboard() {
  const entry = useEntry()
  const item = entry.item
  const origin = item ? mobileProgramPath(item) : MOBILE_PROGRAMS
  const kind = mobileKind(item)
  const { me } = useTeam()
  const work = !!(me && me.previews && me.previews.phone_work)
  return (
    <Frame entry={entry} tab="dashboard">
      {kind === 'program' && <ProgramDashboardBody item={item} />}
      {kind === 'sales' && <SalesDashboardBody item={item} origin={origin} />}
      {kind === 'other' && (item.type === 'restland_bookit' && work
        ? <BookitDashboard item={item} />
        : <OtherDashboardBody item={item} origin={origin} />)}
    </Frame>
  )
}

// ── Replies ─────────────────────────────────────────────────────────────────
export function ProgramReplies() {
  const entry = useEntry()
  const navigate = useNavigate()
  const item = entry.item
  const isProgram = mobileKind(item) === 'program'
  const [loc] = useLocationChoice(item)
  const path = isProgram ? '/program/command/mobile/replies' + (loc ? '?location_id=' + encodeURIComponent(loc) : '') : null
  const r = useProgramGet(item, path)
  const items = (r.data && r.data.items) || []
  const waiting = items.filter(i => i.needs_reply)
  const rest = items.filter(i => !i.needs_reply)
  const open = i => navigate(mobileProgramPath(item, 'replies') + '/' + encodeURIComponent(i.lead_id))
  return (
    <Frame entry={entry} tab="replies">
      {item && !isProgram && <div className="ta-state">This program has no reply queue on mobile.</div>}
      {isProgram && r.loading && !r.data && <Loading />}
      {isProgram && r.error && <ErrorNote error={r.error} onRetry={r.reload} />}
      {isProgram && r.data && (
        <>
          <section className="ta-section" aria-labelledby="ta-rw-h">
            <h2 id="ta-rw-h" className="ta-h2">Needs a Reply ({waiting.length})</h2>
            {waiting.length === 0
              ? <div className="ta-card ta-muted">Nobody is waiting for a reply right now.</div>
              : <ul className="ta-list">{waiting.map(i => <QueueRow key={i.lead_id} i={i} onOpen={() => open(i)} />)}</ul>}
          </section>
          {rest.length > 0 && (
            <section className="ta-section" aria-labelledby="ta-ro-h">
              <h2 id="ta-ro-h" className="ta-h2">Other open conversations</h2>
              <ul className="ta-list">{rest.map(i => <QueueRow key={i.lead_id} i={i} onOpen={() => open(i)} />)}</ul>
            </section>
          )}
          {r.data.total > items.length && <p className="ta-muted ta-small">Showing {items.length} of {r.data.total}. The rest are in the full workspace.</p>}
        </>
      )}
    </Frame>
  )
}

// ── One conversation ────────────────────────────────────────────────────────
function Bubble({ t, tz }) {
  const mine = t.who !== 'family'
  const who = t.who === 'family' ? null : (t.who === 'ai' ? 'AI draft (not sent)' : (t.source === 'manual' ? 'Staff' : 'Sent'))
  return (
    <div className={'ta-msg' + (mine ? ' is-mine' : '')}>
      <div className="ta-msg-col">
        <div className={'ta-bubble ta-bubble--static' + (t.who === 'ai' ? ' is-draft' : '')}>
          {t.subject && <strong className="ta-small">{t.subject}</strong>}
          <span className="ta-body">{t.text || (t.channel === 'voicemail' ? 'Voicemail' : '')}</span>
        </div>
        <span className="ta-msg-time">
          {[who, t.channel === 'email' ? 'Email' : (t.channel === 'voicemail' ? 'Voicemail' : 'Text'),
            t.at ? whenLabel(t.at, tz) : null, t.delivery && t.who === 'us' ? t.delivery : null].filter(Boolean).join(' · ')}
        </span>
      </div>
    </div>
  )
}

function ActionLink({ href, icon, label }) {
  if (!href) return <span className="ta-cact is-off" aria-disabled="true"><Icon name={icon} size={20} /><span>{label}</span></span>
  return <a className="ta-cact" href={href}><Icon name={icon} size={20} /><span>{label}</span></a>
}

export function ProgramThread() {
  const entry = useEntry()
  const navigate = useNavigate()
  const { leadId } = useParams()
  const item = entry.item
  const isProgram = mobileKind(item) === 'program'
  const f = useProgramGet(isProgram ? item : null, '/program/command/family/' + encodeURIComponent(leadId) + '?history=60')
  const [text, setText] = useState('')
  const [keepMine, setKeepMine] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [sent, setSent] = useState(null)
  const keyRef = useRef(draftKey())
  const endRef = useRef(null)
  const d = f.data
  const target = (d && d.reply_to) || {}
  const { me } = useTeam()
  const links = contactLinks(d || {})
  const work = !!(me && me.previews && me.previews.phone_work)
  const leadPage = d && item && work && hasLeads(item)
    ? mobileProgramPath(item, 'leads') + '/' + encodeURIComponent(leadId) : null
  const [apptOpen, setApptOpen] = useState(false)
  const nextAppt = d ? upcomingBooking(d.bookings) : null
  const opted = !!(d && d.handling && d.handling.opted_out)
  const blocked = opted ? 'This family asked not to be contacted.'
    : (target.channel === 'email' && target.threadable === false) ? target.why_not
      : (!target.channel ? 'There is no phone number or email to answer on.' : null)
  useEffect(() => { if (endRef.current) endRef.current.scrollIntoView({ block: 'end' }) }, [d])
  const back = () => navigate(item ? mobileProgramPath(item, 'replies') : '/app/programs')

  async function send(e) {
    e.preventDefault()
    if (busy || !text.trim() || blocked) return
    setBusy(true); setErr(null); setSent(null)
    try {
      const out = await api.post('/program/command/reply', {
        lead_id: leadId, channel: target.channel, text: text.trim(), response_id: target.response_id || null,
        idempotency_key: keyRef.current, then: keepMine ? 'human' : 'ai',
      }, programRequest(item))
      setText('')
      keyRef.current = draftKey()
      setSent(out && out.handoff && out.handoff.kept_human ? 'Sent. You are keeping this conversation.' : 'Sent.')
      f.reload()
    } catch (ex) {
      // The draft stays. A refusal from the server is final for this key, so a
      // retry gets a fresh one; a lost connection keeps the same key, so a retry
      // can never send twice.
      if (ex && ex.status) keyRef.current = draftKey()
      setErr((ex && ex.message) || 'Not sent. Check your connection and try again.')
    } finally { setBusy(false) }
  }

  return (
    <div className="ta-convo ta-prog-convo">
      <header className="ta-convo-top">
        <button type="button" className="ta-icon-btn ta-icon-btn--light" onClick={back} aria-label="Back to Replies"><Icon name="back" /></button>
        <div className="ta-convo-title">
          <h1>{(d && d.name) || 'Conversation'}</h1>
          {d && <span className="ta-convo-sub">{[d.location, d.handling && d.handling.owner === 'human' ? 'With a person' : 'AI assistant on'].filter(Boolean).join(' · ')}</span>}
        </div>
      </header>
      {d && (
        <nav className="ta-convo-actions" aria-label="Contact and details">
          <ActionLink href={links.call} icon="phone" label="Call" />
          <ActionLink href={links.text} icon="sms" label="Text" />
          <ActionLink href={links.email} icon="mail" label="Email" />
          {work && <button type="button" className={'ta-cact' + (nextAppt ? ' has-appt' : '')} onClick={() => setApptOpen(true)}>
            <Icon name="calendar" size={20} /><span>{nextAppt ? 'Appt' : 'Book'}</span></button>}
          {leadPage && <button type="button" className="ta-cact" onClick={() => navigate(leadPage)}><Icon name="note" size={20} /><span>Details</span></button>}
        </nav>
      )}
      {apptOpen && d && <AppointmentSheet item={item} leadId={leadId} name={d.name || 'The family'} bookings={d.bookings}
                                          onClose={() => setApptOpen(false)} onDone={() => f.reload()} />}
      <div className="ta-msgs" aria-live="polite">
        {entry.phase === 'missing' && <div className="ta-state">This program is not available on your account.</div>}
        {entry.phase === 'error' && <ErrorNote error={entry.error} onRetry={entry.retry} />}
        {(entry.phase === 'loading' || (f.loading && !d)) && entry.phase !== 'missing' && <Loading />}
        {f.error && <ErrorNote error={f.error} onRetry={f.reload} />}
        {d && (
          <>
            {d.booking && d.booking.when_text && <p className="ta-muted ta-small ta-prog-meta">Booked {d.booking.when_text}</p>}
            {d.human_handoff && d.handoff_reason && <div className="ta-note">Handed to a person: {d.handoff_reason}</div>}
            {d.thread.map((t, k) => <Bubble key={k} t={t} tz="America/Chicago" />)}
            <div ref={endRef} />
          </>
        )}
      </div>
      {d && (
        <form className="ta-composer" onSubmit={send}>
          {blocked && <div className="ta-note" role="status">{blocked}</div>}
          {!blocked && d.suggested_reply && !text && (
            <button type="button" className="ta-suggest-btn" onClick={() => setText(d.suggested_reply)}>
              Use suggested reply</button>
          )}
          {!blocked && (
            <p className={'ta-sendas' + (target.channel === 'sms' ? ' is-text' : '')} data-channel={target.channel}>
              {target.channel === 'email'
                ? <>Goes out as an <strong>email</strong> to {target.to_name || target.to}{target.subject ? ' · ' + target.subject : ''}</>
                : <>Goes out as a <strong>text message</strong> to {formatPhone(d.phone) || 'their phone'}. Your sign-off and “Reply STOP to opt out.” are added.</>}
            </p>
          )}
          {!blocked && target.channel === 'sms' && looksLikeEmail(text) && (
            <div className="ta-note ta-note--warn" role="alert">This reads like an email, but it will be sent as a text message.{links.email ? ' To email them, tap Email at the top.' : ''}</div>
          )}
          {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
          {sent && !err && <div className="ta-note" role="status">{sent}</div>}
          {!blocked && (
            <>
              <div className="ta-composer-row">
                <textarea className="ta-input" rows={2} value={text} onChange={e => setText(e.target.value)} disabled={busy}
                          placeholder={target.channel === 'email' ? 'Reply by email…' : 'Reply by text…'}
                          aria-label={target.channel === 'email' ? 'Email reply' : 'Text reply'} />
                <button type="submit" className="ta-send" disabled={busy || !text.trim()} aria-label={busy ? 'Sending' : 'Send'}>
                  <Icon name="send" /></button>
              </div>
              <label className="ta-keep"><input type="checkbox" checked={keepMine} onChange={e => setKeepMine(e.target.checked)} disabled={busy} />
                <span>Keep this conversation with me (the AI won’t answer the next reply)</span></label>
            </>
          )}
        </form>
      )}
    </div>
  )
}

// ── More ────────────────────────────────────────────────────────────────────
export function ProgramMore() {
  const entry = useEntry()
  const navigate = useNavigate()
  const item = entry.item
  const origin = item ? mobileProgramPath(item, 'more') : MOBILE_PROGRAMS
  return (
    <Frame entry={entry} tab="more">
      {item && (
        <section className="ta-section">
          <button type="button" className="ta-btn ta-btn--primary ta-btn--block" onClick={() => openFull(item, navigate, origin)}>
            Open Full Workspace</button>
          <p className="ta-muted ta-small ta-prog-note">Every screen and setting for {item.display}. Its Back link returns here.</p>
          <ul className="ta-list">
            <li><button type="button" className="ta-row" onClick={() => navigate('/app/programs')}>
              <span className="ta-row-icon"><Icon name="grid" /></span>
              <span className="ta-row-main"><span className="ta-row-name">All Programs</span></span>
              <Icon name="chev" size={18} /></button></li>
          </ul>
        </section>
      )}
    </Frame>
  )
}

/** The unanswered count for a program card: a separate tap target that opens Replies. */
export function useReplyCount(item) {
  const isProgram = mobileKind(item) === 'program'
  const [n, setN] = useState(null)
  useEffect(() => {
    if (!isProgram) return undefined
    let live = true
    api.get('/program/command/mobile/count', programRequest(item))
      .then(d => { if (live) setN(d && typeof d.needs_reply === 'number' ? d.needs_reply : null) })
      .catch(() => { if (live) setN(null) })
    return () => { live = false }
  }, [item, isProgram])
  return useMemo(() => countLabel(n), [n])
}
