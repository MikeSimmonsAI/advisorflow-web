/* SCI Command - the phone command center, built to Mike's two-phone mockup
   (Oct 14). Families needing attention first, statistics second, every
   control working:
     Home: location filter, search, four tiles, compact owner alert (owner
           only), Priority inbox with per-family actions, today's actions.
     Family: Call / Reply / Schedule Visit / Add Note, the conversation,
           family context with Assign, suggested next step, quick schedule
           with two REAL calendar times, Note / Text / Email composer.
     Tabs: Home, Inbox, Search, Appointments.
   Reads /program/command (+ /family/{id}, /slots) and writes through the
   existing endpoints: /sms/send, /email/send/{id}, /work/leads/{id}/notes,
   /program/responses/{id}/mark, /program/command/schedule, /assign.
   Route: /program/command */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import EngagementGauge, { useEngagement } from '../EngagementGauge'
import SelfCheck from '../SelfCheck'
import VoicemailPlayer from '../../../components/telephony/VoicemailPlayer'
import { Journey, MetricDrill, PerformanceHome, ResendBar } from './Performance'
import { Home, ScreenTabs, TopBar, useHomeData } from './Dashboard'
import { LIVE_SCREENS, MobileNav, SideNav } from './SciNav'
import Consent from './Consent'
import './Dashboard.css'
import './Command.css'
import './Performance.css'

const REFRESH_MS = 45000
const time = iso => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '')
const when = iso => {
  if (!iso) return ''
  const d = new Date(iso)
  return new Date().toDateString() === d.toDateString() ? time(iso)
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + time(iso)
}
const ago = iso => {
  if (!iso) return ''
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  return m < 2 ? 'just now' : m < 60 ? `${m} min ago` : `at ${time(iso)}`
}
const dayLabel = iso => {
  const d = new Date(iso), t = new Date()
  const tm = new Date(t.getFullYear(), t.getMonth(), t.getDate() + 1)
  if (d.toDateString() === t.toDateString()) return 'Today'
  if (d.toDateString() === tm.toDateString()) return 'Tomorrow'
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}
const fmtPhone = p => {
  const d = (p || '').replace(/\D/g, '').replace(/^1(?=\d{10}$)/, '')
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : (p || '')
}
const BADGE = { New: 'new', Hot: 'new', Review: 'review', Active: 'active', Low: 'low', Booked: 'active' }

const I = {
  reply: <path d="M4 5h16v11H8l-4 4z" />,
  call: <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z" />,
  cal: <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M4 10h16M9 3v4M15 3v4" /></>,
  note: <><path d="M6 3h9l4 4v14H6z" /><path d="M9 12h7M9 16h5" /></>,
  check: <path d="M5 12l4 4 10-10" />,
  clock: <><circle cx="12" cy="12" r="8" /><path d="M12 8v5l3 2" /></>,
  x: <><circle cx="12" cy="12" r="8" /><path d="M9 9l6 6M15 9l-6 6" /></>,
  home: <path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z" />,
  inbox: <path d="M4 5h16v11H8l-4 4z" />,
  search: <><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></>,
  back: <path d="M15 5l-7 7 7 7" />,
  warn: <><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 18v.5" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>,
  person: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  pin: <><path d="M12 21s7-6 7-12a7 7 0 0 0-14 0c0 6 7 12 7 12z" /><circle cx="12" cy="9" r="2.5" /></>,
  flag: <path d="M5 21V4h11l-2 4 2 4H5" />,
  down: <path d="M6 9l6 6 6-6" />,
  more: <><circle cx="5" cy="12" r="1.3" fill="currentColor" /><circle cx="12" cy="12" r="1.3" fill="currentColor" /><circle cx="19" cy="12" r="1.3" fill="currentColor" /></>,
  bulb: <><path d="M9 18h6M10 21h4" /><path d="M12 3a6 6 0 0 0-3 11v2h6v-2a6 6 0 0 0-3-11z" /></>,
}
// Solid icons for the four tiles and the active tab, as in the mockup.
const F = {
  alert: <><circle cx="12" cy="12" r="10" /><path d="M12 7v6M12 16.5v.5" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" /></>,
  chat: <><path d="M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H9l-5 4V5a1 1 0 0 1 1-1z" /><path d="M8 10.5h.5M12 10.5h.5M16 10.5h.5" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" /></>,
  cal: <><rect x="3" y="5" width="18" height="16" rx="2.5" /><path d="M8 3v4M16 3v4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><rect x="5.5" y="10" width="13" height="8.5" rx="1" fill="#fff" /><path d="M8 13h2M11 13h2M14 13h2M8 16h2M11 16h2" stroke="currentColor" strokeWidth="1.6" /></>,
  people: <><circle cx="9" cy="8" r="3.6" /><path d="M2 20c0-4 3.2-6.5 7-6.5s7 2.5 7 6.5z" /><circle cx="17" cy="9" r="2.8" /><path d="M14.5 14.2c.8-.4 1.7-.6 2.5-.6 3 0 5 2 5 5.4h-5" /></>,
  home: <path d="M3 11.2 12 3.5l9 7.7V21h-6.5v-6.5h-5V21H3z" />,
}
const Solid = ({ n, s = 22 }) => <svg className="ti" viewBox="0 0 24 24" width={s} height={s} fill="currentColor" aria-hidden="true">{F[n]}</svg>
const Icon = ({ n, s = 18 }) => (
  <svg viewBox="0 0 24 24" width={s} height={s} fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{I[n]}</svg>
)

export default function Command() {
  // EVERY SCREEN HAS A URL (Oct 16 navigation repair): ?s= screen, ?f= family,
  // ?v= family view, ?m= metric drill. Refresh, Back/Forward, a shared link and
  // the side menu all land on the same screen; nothing lives only in memory.
  const [sp] = useSearchParams()
  const navigate = useNavigate()
  const where = useLocation()
  const tab = LIVE_SCREENS.has(sp.get('s')) ? sp.get('s') : 'home'
  const family = sp.get('f') || null
  const familyView = sp.get('v') === 'journey' ? 'journey' : 'conversation'
  const mRaw = sp.get('m')
  const drill = useMemo(() => { try { return mRaw ? JSON.parse(mRaw) : null } catch { return null } }, [mRaw])
  /** Move to another SCI Live screen as a real history entry. */
  const nav = useCallback((patch, replace = false) => {
    const next = new URLSearchParams(where.search)
    Object.entries(patch).forEach(([k, v]) => {
      if (v === null || v === undefined || v === '') next.delete(k)
      else next.set(k, typeof v === 'object' ? JSON.stringify(v) : String(v))
    })
    const qs = next.toString()
    navigate({ pathname: '/program/live', search: qs ? `?${qs}` : '' }, { replace })
  }, [navigate, where.search])
  const setTab = k => nav({ s: k === 'home' ? null : k, f: null, v: null, m: null })
  const setDrill = spec => nav({ m: spec || null, f: null, v: null })
  /** Back inside the app when there is history, otherwise to the given screen. */
  const back = fallback => (window.history.state?.idx > 0 ? navigate(-1) : nav(fallback, true))
  const [loc, setLoc] = useState('')
  const [q, setQ] = useState('')
  const [d, setD] = useState(null)
  const [live, setLive] = useState(null)          // former SCI Live numbers
  const [at, setAt] = useState(null)
  const engagement = useEngagement()
  const [err, setErr] = useState('')
  const [range, setRange] = useState({ preset: 'today' })
  const [lastFamily, setLastFamily] = useState(null)
  const [inboxSel, setInboxSel] = useState(null)
  const wide = useWide()
  const home = useHomeData()
  const openFamily = (id, view) => { setLastFamily(id); nav({ f: id, v: view === 'journey' ? 'journey' : null }) }
  useEffect(() => { if (family) setLastFamily(family) }, [family])
  const [startReply, setStartReply] = useState(false) // opened with Reply: composer open and focused
  const [toast, setToast] = useState('')
  const busy = useRef(false)

  const load = useCallback(() => {
    if (busy.current) return
    busy.current = true
    const p = new URLSearchParams()
    if (loc) p.set('location_id', loc)
    const safe = path => api.get(path).catch(() => null)
    // The former SCI Live numbers (sends, deliveries, booking-page opens, next
    // up, just sent) load beside the command data, so nothing the old screen
    // showed is lost.
    Promise.all([safe('/program/outbox'), safe('/program/booking-activity?days=1'), safe('/program/setter/stats?days=30'),
      safe('/program/command/pipeline')])
      .then(([ob, ba, st, pl]) => setLive({ ob, ba, st, pl }))
    api.get('/program/command' + (p.toString() ? `?${p}` : '')).then(r => { setD(r); setErr(''); setAt(new Date())
      try { sessionStorage.removeItem('sci_live_entry') } catch { /* private mode */ } })
      .catch(e => {
        const msg = (e && e.message) || 'Could not load.'
        // Opened in a fresh tab (Home Screen shortcut, a link): no workspace is
        // chosen yet. Go through the SCI front door, which picks SCI and comes
        // straight back to SCI Live.
        if (/no workspace|no outreach program|customer workspace route|brand sales/i.test(msg) && !sessionStorage.getItem('sci_live_entry')) {
          try { sessionStorage.setItem('sci_live_entry', '1') } catch { /* private mode */ }
          window.location.replace('/sci?to=live')
          return
        }
        setErr(msg)
      })
      .finally(() => { busy.current = false })
  }, [loc])

  useEffect(() => {
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, REFRESH_MS)
    const onShow = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onShow)
    window.addEventListener('focus', onShow)
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', onShow); window.removeEventListener('focus', onShow) }
  }, [load])
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(''), 3500); return () => clearTimeout(t) }, [toast])

  const act = async (item, a) => {
    if (a === 'call') { window.location.href = `tel:${item.phone || ''}`; if (!item.phone) openFamily(item.lead_id); return }
    if (['reply', 'schedule', 'review', 'note'].includes(a)) {
      setStartReply(a === 'reply')
      if (wide && tab === 'inbox') { setInboxSel(item.lead_id); setLastFamily(item.lead_id) } else openFamily(item.lead_id)
      return
    }
    const state = a === 'close' || a === 'dismiss' ? 'closed' : 'opened'
    try {
      await api.post(`/program/responses/${item.response_id}/mark`, { state })
      setToast(a === 'snooze' ? 'Snoozed - it stays in the list as handled.' : 'Closed.')
      load()
    } catch (e) { setToast(e.message || 'Could not update.') }
  }

  const screen = family ? (familyView === 'journey' ? 'journey' : 'inbox') : drill ? 'drill'
    : tab === 'home' ? 'home' : tab
  const go = k => {
    if (k === 'drill') { setDrill({ metric: 'attempts', range: { preset: 'today' } }); return }
    if (k === 'journey') {
      const id = lastFamily || inboxSel || d?.inbox?.[0]?.lead_id
      if (id) openFamily(id, 'journey'); else setToast('Open a family first.')
      return
    }
    setTab(k)
  }
  // A new screen starts at the top (Back keeps the browser's own restore).
  const screenKey = `${tab}|${family || ''}|${familyView}|${mRaw || ''}`
  useEffect(() => { window.scrollTo(0, 0) }, [screenKey])
  // The browser tab names the screen that is open (the program screens set
  // their own), so coming back here never leaves "Settings" in the title.
  useEffect(() => {
    const name = family ? (familyView === 'journey' ? 'Family Journey' : 'Conversation') : drill ? 'Metric Drill-down'
      : { search: 'Families', inbox: 'Conversations', appts: 'Appointments', reports: 'Analytics', consent: 'Opt-ins & Opt-outs' }[tab] || 'SCI Live'
    document.title = `${name} · SCI · EvoSys Pro`
  }, [tab, family, familyView, drill])
  // Clicking the menu item of the screen already open: back to its top.
  const onSame = () => window.scrollTo({ top: 0, behavior: 'smooth' })
  const frame = (body, cls = '') => (
    <div className="sl-shell">
      <SideNav badges={{ inbox: d?.counts?.needs_attention }} onSame={onSame} />
      <div className="sl-main">
        <TopBar batch={home.a?.daily_batch} />
        <ScreenTabs screen={screen} go={go} />
        <div className={`sl-body ${cls}`}>{body}</div>
        {toast && <div className="cmd-toast" role="status">{toast}</div>}
        <MobileNav badges={{ inbox: d?.counts?.needs_attention }} onSame={onSame} />
      </div>
    </div>
  )
  if (family) {
    return frame(<Family key={family} id={family} view={familyView} startReply={startReply}
      onBack={() => { setStartReply(false); back({ f: null, v: null }); load() }} toast={setToast} toastText={null} />)
  }
  if (drill) {
    return frame(<MetricDrill key={JSON.stringify(drill)} spec={drill} range={drill.range || range} loc={loc} onBack={() => back({ m: null })} openFamily={openFamily} />, 'wide')
  }
  return frame(<div className={`cmd ${tab === 'home' || tab === 'reports' || (tab === 'inbox' && wide) ? 'cmd-wide' : ''}`}>
      {tab === 'home' && <>
        {err && <p className="cmd-err">{err}</p>}
        <div className="sl-search"><SearchBox q={q} setQ={v => { setQ(v); if (v.trim()) setTab('search') }} /></div>
        {d?.owner_alert && (
          <Link className="cmd-owner" to="/program?tab=health">
            <Icon n="warn" />
            <span><b>Owner alert</b><br />{d.owner_alert.text}</span>
            <span className="cmd-link">View ›</span>
          </Link>
        )}
        {(d?.counts?.needs_attention > 0 || (d?.unknown || []).length > 0) && (
          <button className="sl-alert" onClick={() => go('inbox')}><b>{d.counts.needs_attention} famil{d.counts.needs_attention === 1 ? 'y needs' : 'ies need'} a reply</b>
            {(d.unknown || []).length ? <span> · {d.unknown.length} unknown caller{d.unknown.length === 1 ? '' : 's'}</span> : null}<em>Open inbox ›</em></button>)}
        <Home a={home.a} sched={home.sched} consent={home.consent} openMetric={setDrill} openFamily={openFamily} />
        {d && <div className="cmd-cols">
          <div>
            <Unknown items={d.unknown} toast={setToast} reload={load} />
            <SectionHead title="Today's upcoming actions" onAll={() => setTab('inbox')} />
            <div className="cmd-card cmd-actions">
              <button onClick={() => setTab('inbox')}><Icon n="reply" />
                <span><b>Follow up with {d.upcoming.follow_up} families</b><br />
                  <small>Includes {d.upcoming.new} new and {d.upcoming.review} needing review
                    {d.upcoming.callbacks ? ` · ${d.upcoming.callbacks} callbacks` : ''}</small></span>›</button>
              <button onClick={() => setTab('appts')}><Icon n="cal" />
                <span><b>{d.upcoming.visits_booked ? `${d.upcoming.visits_booked} appointment${d.upcoming.visits_booked === 1 ? '' : 's'} booked` : 'No appointments scheduled'}</b><br />
                  <small>{d.upcoming.visits_booked ? 'See who is coming in' : 'Get in touch to book a visit today'}</small></span>›</button>
              {(d.tasks || []).map(t => (
                <button key={`${t.kind}-${t.lead_id}`} onClick={() => openFamily(t.lead_id)}><Icon n="mail" />
                  <span><b>Mail a printed guide · {t.name}</b><br />
                    <small>{t.note || 'Confirm the address, mail it, then mark it done'}</small></span>›</button>))}
            </div>
          </div>
          <div>
            <TodayActivity live={live} engagement={engagement} open={openFamily} />
          </div>
        </div>}
        {d && <p className="cmd-foot">Updates every {Math.round(REFRESH_MS / 1000)} seconds and whenever you come back to this screen{at ? ` · last ${time(at.toISOString())}` : ''}.
          {' '}<Link to="/program?tab=dashboard">Open the Program Overview</Link></p>}
      </>}
      {tab === 'inbox' && (wide
        ? <div className="sl-inbox">
          <div className="sl-inbox-list"><InboxTab loc={loc} act={act} open={id => { setStartReply(false); setInboxSel(id); setLastFamily(id) }} selected={inboxSel} /></div>
          <div className="sl-inbox-pane">{inboxSel
            ? <Family key={inboxSel} id={inboxSel} embedded startReply={startReply} onBack={() => setInboxSel(null)} toast={setToast} toastText={null} />
            : <p className="sl-pick">Choose a conversation on the left. The family's messages, context, handling and scheduling open here.</p>}</div>
        </div>
        : <InboxTab loc={loc} act={act} open={openFamily} />)}
      {tab === 'search' && <>
        <header className="cmd-top"><h1>Search</h1></header>
        <SearchBox q={q} setQ={setQ} autoFocus />
        <SearchResults q={q} loc={loc} open={openFamily} />
      </>}
      {tab === 'appts' && <Appointments open={openFamily} />}
      {tab === 'consent' && <Consent />}
      {tab === 'reports' && <section id="sl-reports" className="sl-reports">
        <SectionHead title="Analytics · performance & reports" />
        <PerformanceHome range={range} setRange={setRange} loc={loc} setLoc={setLoc} openMetric={setDrill} />
      </section>}
    </div>, tab === 'home' || tab === 'inbox' || tab === 'reports' || tab === 'consent' ? 'wide' : '')
}

function useWide() {
  const q = '(min-width: 1100px)'
  const [w, setW] = useState(() => typeof window !== 'undefined' && window.matchMedia?.(q).matches)
  useEffect(() => {
    const m = window.matchMedia?.(q)
    if (!m) return
    const f = () => setW(m.matches)
    m.addEventListener?.('change', f)
    return () => m.removeEventListener?.('change', f)
  }, [])
  return !!w
}

// Every SCI family, not only those who replied. The newest query always wins:
// a slower answer to an older query is ignored rather than shown.
function SearchResults({ q, loc, open }) {
  const [res, setRes] = useState(null)
  const seq = useRef(0)
  useEffect(() => {
    const term = q.trim()
    if (term.length < 2) { setRes(null); return }
    const n = ++seq.current
    const t = setTimeout(() => {
      const p = new URLSearchParams({ q: term })
      if (loc) p.set('location_id', loc)
      api.get(`/program/command/search?${p}`)
        .then(r => { if (n === seq.current) setRes(r.items || []) })
        .catch(() => { if (n === seq.current) setRes([]) })
    }, 250)
    return () => clearTimeout(t)
  }, [q, loc])
  if (q.trim().length < 2) return <p className="cmd-muted">Search by family name, phone, email or location.</p>
  if (res === null) return <p className="cmd-muted">Searching…</p>
  if (!res.length) return <p className="cmd-muted">No family matches.</p>
  return (
    <div className="cmd-card cmd-actions">
      {res.map(f => (
        <button key={f.lead_id} onClick={() => open(f.lead_id)}><Icon n="people" />
          <span><b>{f.name}</b><br /><small>{[f.location, f.phone, f.replied ? 'Has replied' : null].filter(Boolean).join(' · ')}</small></span>›</button>))}
    </div>
  )
}

// ONE inbox for texts and emails, with its own filters. Reads the same
// endpoint as Home; "Handled" brings back what was answered or closed.
const FILTERS = [['all', 'All'], ['needs_reply', 'Needs reply'], ['unread', 'Unread'], ['ai', 'AI handling'], ['human', 'Human attention'],
  ['appointments', 'Appointments'], ['text', 'Text'], ['email', 'Email'], ['handled', 'Handled']]

function InboxTab({ loc, act, open, selected }) {
  const [flt, setFlt] = useState('all')
  const [r, setR] = useState(null)
  const seq = useRef(0)
  const load = useCallback(() => {
    const n = ++seq.current
    const p = new URLSearchParams({ filter: flt, limit: '200' })
    if (loc) p.set('location_id', loc)
    api.get(`/program/command?${p}`).then(x => { if (n === seq.current) setR(x) }).catch(() => { if (n === seq.current) setR({ inbox: [] }) })
  }, [flt, loc])
  useEffect(() => {
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, REFRESH_MS)
    return () => clearInterval(t)
  }, [load])
  const c = r?.filter_counts || {}
  return <>
    <header className="cmd-top"><h1>Inbox</h1>{c.unread ? <p className="cmd-sub">{c.unread} new</p> : null}</header>
    <div className="cmd-chips" role="tablist">
      {FILTERS.map(([k, l]) => (
        <button key={k} role="tab" aria-selected={flt === k} className={flt === k ? 'on' : ''} onClick={() => setFlt(k)}>
          {l}{k !== 'handled' && c[k] !== undefined ? <i>{c[k]}</i> : null}</button>))}
    </div>
    {r && flt !== 'handled' && <Unknown items={(r.unknown || []).filter(u => ['all', 'needs_reply', 'unread', 'human'].includes(flt) || (flt === 'email' && u.channel_label === 'EMAIL') || (flt === 'text' && u.channel_label !== 'EMAIL'))} reload={load} />}
    {r === null ? <p className="cmd-muted">Loading…</p>
      : <Inbox items={r.inbox} act={act} open={open} selected={selected} empty={flt === 'handled' ? 'Nothing handled in the last two weeks.' : 'Nobody is waiting. Nice work.'} />}
  </>
}

// Replies from a number or address that matches no family - texts,
// voicemails (with the recording) and emails. Never hidden.
function Unknown({ items, toast, reload }) {
  const [gone, setGone] = useState({})
  const list = (items || []).filter(u => !gone[u.id])
  if (!list.length) return null
  const handled = async u => {
    try { await api.post(`/program/unmatched-replies/${u.id}/handled`, {}); setGone(g => ({ ...g, [u.id]: true })); toast?.('Marked handled.'); reload?.() }
    catch (e) { toast?.(e.message || 'Could not update.') }
  }
  return <>
    <SectionHead title={`Unknown callers & senders (${list.length})`} />
    {list.map(u => (
      <article key={u.id} className="cmd-card cmd-item">
        <div className="cmd-item-main">
          <span className="dot active" />
          <span className="cmd-item-text">
            <b>{u.channel_label === 'EMAIL' ? u.from : (fmtPhone(u.from) || u.from || 'Unknown')}
              <em className={`chan ${u.channel_label === 'EMAIL' ? 'email' : 'text'}`}>{u.channel_label}</em></b>
            <small>Not matched to a family{u.location ? ` · ${u.location}` : ''}</small>
            {u.subject && <small className="cmd-subjline">{u.subject}</small>}
            <span className="cmd-summary">{u.summary}</span>
            {u.audio_path && <VoicemailPlayer path={u.audio_path} label={`Play voicemail${u.duration ? ` (${u.duration}s)` : ''}`} />}
          </span>
          <span className="cmd-item-meta"><small>{time(u.received_at)}</small></span>
        </div>
        <div className="cmd-btns">
          {u.channel_label !== 'EMAIL' && u.from && <a className="primary" href={`tel:${u.from}`}><Icon n="call" />Call back</a>}
          <button onClick={() => handled(u)}><Icon n="check" />Handled</button>
        </div>
      </article>))}
  </>
}

function SearchBox({ q, setQ, autoFocus }) {
  return (
    <label className="cmd-search"><Icon n="search" />
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search family, phone, location..." autoFocus={autoFocus} />
    </label>
  )
}

function Tile({ icon, tone, label, v, onClick }) {
  return (
    <button className={`cmd-tile ${tone}`} onClick={onClick} disabled={!onClick}>
      <Solid n={icon} /><span>{label}</span><b>{v}</b>
    </button>
  )
}

function SectionHead({ title, onAll }) {
  return <div className="cmd-sec"><h2>{title}</h2>{onAll && <button onClick={onAll}>View all ›</button>}</div>
}

const ACT = {
  reply: ['reply', 'Reply', true], call: ['call', 'Call'], schedule: ['cal', 'Schedule'], note: ['note', 'Note'],
  close: ['check', 'Close'], snooze: ['clock', 'Snooze'], review: ['note', 'Review', true], dismiss: ['x', 'Dismiss'],
}

function Inbox({ items, act, open, empty, selected }) {
  if (!items.length) return <p className="cmd-card cmd-muted pad">{empty || 'Nobody is waiting right now.'}</p>
  return items.map(i => {
    const email = i.channel === 'email'
    const title = email && (i.sender_name || i.sender_email) ? (i.sender_name || i.sender_email) : i.name
    return (
      <article key={i.lead_id} className={`cmd-card cmd-item ${selected === i.lead_id ? 'sel' : ''}`}>
        <button className="cmd-item-main" onClick={() => open(i.lead_id)}>
          <span className={`dot ${i.class}`} />
          <span className="cmd-item-text">
            <span className="cmd-row1"><b className="cmd-name">{title}</b><small className="cmd-time">{when(i.received_at)}</small></span>
            <span className="cmd-row2">
              <em className={`chan ${email ? 'email' : 'text'}`}>{i.channel_label || (email ? 'EMAIL' : 'TEXT')}</em>
              <em className={`chan ${i.owner === 'human' ? 'human' : 'ai'}`}>{i.owner === 'human' ? 'PERSON' : 'AI'}</em>
              {i.is_test && <em className="chan human">TEST</em>}
              <em className={`badge ${BADGE[i.badge] || 'low'}`}>{i.badge}</em>
            </span>
            {email && i.sender_email && <small className="cmd-from" title={i.sender_email}>{i.sender_email}</small>}
            <small className="cmd-locline">{email ? `Family: ${i.name}${i.location ? ` · ${i.location}` : ''}` : (i.location || 'Location not set')}</small>
            {email && i.subject && <small className="cmd-subjline">{i.subject}</small>}
            <span className="cmd-summary">{i.summary}</span>
          </span>
          <span className="cmd-chev" aria-hidden="true">›</span>
        </button>
        <div className="cmd-btns">
          {i.actions.map(a => {
            const [ic, lbl, primary] = ACT[a]
            return <button key={a} className={primary ? 'primary' : ''} onClick={() => act(i, a)}><Icon n={ic} />{lbl}</button>
          })}
        </div>
      </article>
    )
  })
}

function Appointments({ open }) {
  const [rows, setRows] = useState(null)
  useEffect(() => { api.get('/program/appointments?when=upcoming').then(r => setRows(r.items || [])).catch(() => setRows([])) }, [])
  return <>
    <header className="cmd-top"><h1>Appointments</h1></header>
    {rows === null ? <p className="cmd-muted">Loading…</p> : !rows.length
      ? <p className="cmd-card cmd-muted pad">No appointments scheduled yet.</p>
      : rows.map(a => (
        <button key={a.id} className="cmd-card cmd-appt" onClick={() => open(a.lead_id)}>
          <Icon n="cal" /><span><b>{a.name}</b><small>{a.location || ''}</small></span>
          <span className="cmd-item-meta"><b>{new Date(a.when.replace('Z', '')).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</b>
            <small>{a.label || 'Visit'}</small></span>
        </button>))}
  </>
}

function Family({ id, view: view0, startReply, onBack, toast, toastText, embedded }) {
  const [view, setView] = useState(view0 || 'conversation')
  const [f, setF] = useState(null)
  const [err, setErr] = useState('')
  const [mode, setMode] = useState('note')
  const [text, setText] = useState('')
  const sendKey = useRef(null)
  const newKey = () => (window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`)
  const [subject, setSubject] = useState('')
  const [slots, setSlots] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState({})
  const [menu, setMenu] = useState(false)
  const [picking, setPicking] = useState(false)
  const composer = useRef(null)
  const schedRef = useRef(null)

  const load = useCallback(() => {
    api.get(`/program/command/family/${id}`).then(r => { setF(r); setErr('') }).catch(e => setErr(e.message || 'Could not load.'))
  }, [id])
  useEffect(() => { load() }, [load])
  // Reply on an inbox card: straight to the right composer - Email (in the
  // thread) for an email, Text for a text - focused, no extra tap.
  const replied = useRef(false)
  useEffect(() => {
    if (!startReply || !f || replied.current) return
    replied.current = true
    const m = f.reply_to?.channel === 'email' ? 'email' : 'text'
    setMode(m)
    if (f.suggested_reply) setText(t => t || f.suggested_reply)
    setTimeout(() => { composer.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); composer.current?.focus() }, 80)
  }, [startReply, f])
  useEffect(() => {
    api.get(`/program/command/slots?lead_id=${id}`).then(setSlots).catch(() => setSlots({ slots: [], reason: 'Calendar unavailable' }))
  }, [id])
  useEffect(() => { if (f?.status === 'new' && f.response_id) api.post(`/program/responses/${f.response_id}/viewed`, {}).catch(() => {}) }, [f?.response_id, f?.status])

  const replyMode = () => (f?.reply_to?.channel === 'email' ? 'email' : 'text')
  const focusComposer = m => { setView('conversation'); setMode(m); if (m !== 'note' && f?.suggested_reply && !text) setText(f.suggested_reply); setTimeout(() => composer.current?.focus(), 50) }
  // then: 'human' (Send) or 'ai' (Send & let AI continue). One send key per
  // composed message, so a double tap, a retry or a refresh never sends twice.
  const send = async then => {
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      if (mode === 'note') {
        await api.post(`/work/leads/${id}/notes`, { body: text.trim() })
        toast('Note saved.')
      } else if (f.is_demo) {
        await api.post('/program/command/demo-send', { lead_id: id, channel: mode === 'email' ? 'email' : 'sms', text: text.trim(), subject: subject.trim() || null })
        toast('Demo family: saved in the conversation as simulated. Nothing was sent.')
      } else {
        if (!sendKey.current) sendKey.current = newKey()
        const r = await api.post('/program/command/reply', {
          lead_id: id, channel: mode === 'email' ? 'email' : 'sms', text: text.trim(),
          idempotency_key: sendKey.current, then: then || 'human',
          response_id: mode === 'email' ? f.reply_to?.response_id : (f.reply_to?.channel === 'sms' ? f.reply_to?.response_id : null),
        })
        const where = mode === 'email' ? `Email sent to ${r.email?.to || 'the sender'} in the same thread` : 'Text sent'
        toast(r.duplicate ? 'Already sent - not sent again.' : `${where}.${then === 'ai' ? ' AI will answer their next reply.' : ''}`)
      }
      sendKey.current = null
      setText(''); setSubject(''); load()
    } catch (e) { toast(e.message || 'Not sent.') } finally { setBusy(false) }
  }
  const setOwner = async owner => {
    setBusy(true)
    try {
      await api.post('/program/command/handling', { lead_id: id, owner })
      toast(owner === 'ai' ? 'AI Handling on - the AI answers their next reply.' : 'Human Handling on - the AI will not reply.')
      load()
    } catch (e) { toast(e.message || 'Could not change.') } finally { setBusy(false) }
  }
  const book = async s => {
    if (!window.confirm(`Book ${f.name} for ${s.label}? The family gets a confirmation text.`)) return
    setBusy(true)
    try {
      const r = await api.post('/program/command/schedule', { lead_id: id, slot: s.value, kind: 'visit' })
      toast(r.ok ? (r.simulated ? `Simulated: ${r.label} is open, but nothing was booked for real (test/demo family).` : `Booked - ${r.label}. Confirmed on the calendar.`)
        : (r.reason || 'That time is no longer open.'))
      load()
      api.get(`/program/command/slots?lead_id=${id}`).then(setSlots).catch(() => {})
    } catch (e) { toast(e.message || 'Could not book.') } finally { setBusy(false) }
  }
  const mailTask = async done => {
    try {
      await api.post('/program/command/mail-task', { lead_id: id, done })
      toast(done ? 'Marked as mailed.' : 'Task added: mail a printed guide.')
      load()
    } catch (e) { toast(e.message || 'Could not update the task.') }
  }
  const markHandled = async () => {
    try { await api.post(`/program/responses/${f.response_id}/mark`, { state: 'closed' }); toast('Marked handled.'); load() }
    catch (e) { toast(e.message || 'Could not update.') }
  }
  const assign = async uid => {
    setPicking(false)
    try { await api.post('/program/command/assign', { lead_id: id, user_id: uid || null }); toast(uid ? 'Assigned.' : 'Unassigned.'); load() }
    catch (e) { toast(e.message || 'Could not assign.') }
  }

  const canSend = mode === 'text' ? !!f?.phone
    : mode === 'email' ? !!(f?.reply_to?.channel === 'email' && f.reply_to.threadable) : true
  if (!f) return <div className="cmd"><button className="cmd-back" onClick={onBack}><Icon n="back" /> Back</button>{err ? <p className="cmd-err">{err}</p> : <p className="cmd-muted">Loading…</p>}</div>
  return (
    <div className={`cmd cmd-family ${embedded ? 'embedded' : ''}`}>
      <header className="cmd-fhead">
        <button className="cmd-back" onClick={onBack} aria-label="Back"><Icon n="back" s={24} /></button>
        <div><h1>{f.name}{f.is_test && <em className="badge low"> Test</em>}{f.is_demo && <em className="badge low"> Demo</em>}</h1><p className="cmd-sub">{f.location || 'Location not set'}</p></div>
        {f.badge && <em className={`badge ${BADGE[f.badge] || 'low'}`}>{f.badge}</em>}
        <span className="cmd-more">
          <button onClick={() => setMenu(!menu)} aria-label="More" aria-expanded={menu}><Icon n="more" s={24} /></button>
          {menu && <div className="cmd-menu" onClick={() => setMenu(false)}>
            <Link to={`/program?tab=leads&person=${id}`}>Open full family record</Link>
            {f.response_id && <button onClick={markHandled}>Mark handled</button>}
            {!f.mail_task && <button onClick={() => mailTask(false)}>Add task: mail a printed guide</button>}
            {f.phone && <button onClick={() => { navigator.clipboard?.writeText(f.phone).then(() => toast('Phone copied.')).catch(() => {}) }}>Copy phone number</button>}
          </div>}
        </span>
      </header>
      <div className="cmd-quick">
        <a href={f.phone ? `tel:${f.phone}` : undefined} className={f.phone ? '' : 'off'}><Icon n="call" s={22} />Call</a>
        <button className="primary" onClick={() => focusComposer(replyMode())}><Icon n="reply" s={22} />Reply</button>
        <button onClick={() => schedRef.current?.scrollIntoView({ behavior: 'smooth' })}><Icon n="cal" s={22} />Schedule Visit</button>
        <button onClick={() => focusComposer('note')}><Icon n="note" s={22} />Add Note</button>
      </div>
      {!f.is_demo && <ResendBar key={`rs-${id}`} id={id} onDone={load} />}

      <div className="cmd-seg two cmd-fview" role="tablist" aria-label="View">
        <button role="tab" aria-selected={view === 'conversation'} className={view === 'conversation' ? 'on' : ''} onClick={() => setView('conversation')}>Conversation</button>
        <button role="tab" aria-selected={view === 'journey'} className={view === 'journey' ? 'on' : ''} onClick={() => setView('journey')}>Journey</button>
      </div>
      {view === 'journey' && <Journey id={id} />}
      {view === 'conversation' && !f.is_demo && <section className="cmd-card cmd-handling" aria-label="Conversation handling">
        <h3><Icon n="person" /> Conversation handling</h3>
        <div className="cmd-seg two">
          <button className={f.handling?.owner === 'ai' ? 'on' : ''} disabled={busy || f.handling?.opted_out} onClick={() => f.handling?.owner !== 'ai' && setOwner('ai')}>
            AI Handling{f.handling?.owner === 'ai' ? ' — ON' : ''}</button>
          <button className={f.handling?.owner === 'human' ? 'on' : ''} disabled={busy} onClick={() => f.handling?.owner !== 'human' && setOwner('human')}>
            Human Handling{f.handling?.owner === 'human' ? ' — ON' : ''}</button>
        </div>
        <p className="cmd-muted small">
          {f.handling?.opted_out ? 'This family opted out. Nobody texts or emails them.'
            : f.handling?.owner === 'ai' ? 'The AI answers this family\'s next text or email, knowing everything sent so far.'
              : `A person has this conversation. The AI reads it but does not reply.${f.handling?.reason ? ` (${f.handling.reason})` : ''}`}
          {f.handling?.changed_at ? ` Last change: ${dayLabel(f.handling.changed_at)} ${time(f.handling.changed_at)}${f.handling.changed_by ? ` by ${f.handling.changed_by}` : ''}.` : ''}
        </p>
      </section>}

      {view === 'conversation' && <section className="cmd-thread" aria-label="Conversation">
        {f.thread.length ? f.thread.map((t, k) => (<Fragment key={k}>
          {(k === 0 || dayLabel(f.thread[k - 1].at || '') !== dayLabel(t.at || '')) && t.at && <div className="cmd-day">{dayLabel(t.at)}</div>}
          <div className={`bubble ${t.who}`}>
            {t.channel === 'email' && t.who === 'family' && <small className="cmd-bhead">From {t.sender_name ? `${t.sender_name} <${t.sender_email}>` : t.sender_email}{t.subject ? ` · ${t.subject}` : ''}</small>}
            {t.channel === 'email' && t.who === 'us' && (t.to || t.subject) && <small className="cmd-bhead">{t.to ? `To ${t.to}` : ''}{t.to && t.subject ? ' · ' : ''}{t.subject || ''}</small>}
            <p>{t.text}</p>
            {t.audio_path && <VoicemailPlayer path={t.audio_path} label={`Play voicemail${t.duration ? ` (${t.duration}s)` : ''}`} />}
            <small>{time(t.at)}{t.channel === 'email' ? ' · EMAIL' : t.channel === 'voicemail' ? ' · VOICEMAIL' : ' · TEXT'}{t.source === 'sci_appointment_setter' ? ' · AI' : ''}{t.delivery === 'simulated' && t.who === 'us' ? ' · simulated' : ''}{t.who === 'us' && ['failed', 'undelivered', 'bounced'].includes(t.delivery) ? ` · NOT SENT${t.error ? `: ${t.error}` : ''}` : ''}{t.who === 'ai' ? ` · AI ${t.delivery === 'draft' ? 'draft' : t.delivery}` : ''}</small>
          </div></Fragment>)) : <p className="cmd-muted">No messages yet.</p>}
      </section>}

      <section className="cmd-card cmd-ctx">
        <h3><Icon n="person" /> Family context <Link className="cmd-edit" to={`/program?tab=leads&person=${id}`}>Edit</Link></h3>
        <dl>
          <dt><Icon n="call" />Phone</dt><dd>{fmtPhone(f.phone) || '—'}{f.phone && <a href={`tel:${f.phone}`}>Call</a>}</dd>
          {f.email && <><dt><Icon n="mail" />Email</dt><dd>{f.email}</dd></>}
          <dt><Icon n="pin" />Location</dt><dd>{f.location || '—'}</dd>
          <dt><Icon n="flag" />Campaign</dt><dd>{f.campaign || '—'}</dd>
          <dt><Icon n="person" />Assigned to</dt>
          <dd>{f.assigned_to?.name || 'Unassigned'}<button className="link" onClick={() => setPicking(!picking)}>{f.assigned_to ? 'Change' : 'Assign'}</button></dd>
          {picking && <div className="cmd-assign">
            {f.team.map(u => <button key={u.id} className={f.assigned_to?.id === u.id ? 'on' : ''} onClick={() => assign(u.id)}>{u.name}</button>)}
            {f.assigned_to && <button onClick={() => assign('')}>Unassign</button>}
          </div>}
          <dt><Icon n="clock" />Last touch</dt><dd>{f.last_touch ? `${dayLabel(f.last_touch.at)} at ${time(f.last_touch.at)} (${f.last_touch.channel === 'email' ? 'Email' : 'Text'})` : '—'}</dd>
        </dl>
        {f.setter && <p className="cmd-setter">Appointment setter: <b>{f.setter.state.replace('_', ' ')}</b>
          {f.setter.offered.length ? ` · offered ${f.setter.offered.join(' or ')}` : ''}
          {f.setter.callback_window ? ` · callback ${f.setter.callback_window}` : ''}</p>}
        {f.booking && <p className="cmd-booked"><Icon n="check" /> Booked {new Date(f.booking.when).toLocaleString([], { weekday: 'long', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · {f.booking.label || 'Visit'}</p>}
      </section>

      {f.mail_task && <section className="cmd-card cmd-next">
        <h3><Icon n="mail" /> Printed guide by mail</h3>
        {f.mail_task.done
          ? <p>Mailed{f.mail_task.done_by ? ` by ${f.mail_task.done_by}` : ''}{f.mail_task.done_at ? ` · ${new Date(f.mail_task.done_at).toLocaleDateString()}` : ''}.</p>
          : <><p>The family asked for a printed guide. Confirm the address, mail it, then mark it done.{f.mail_task.note ? ` (${f.mail_task.note})` : ''}</p>
            <div className="cmd-send"><button className="primary" onClick={() => mailTask(true)}>Mark as mailed</button></div></>}
      </section>}
      <section className="cmd-card cmd-next">
        <h3><Icon n="bulb" /> Suggested next step</h3>
        <p>{f.suggestion.title}.</p>
        {f.suggestion.steps.map(s => (
          <label key={s}><input type="checkbox" checked={!!done[s]} onChange={e => setDone({ ...done, [s]: e.target.checked })} /> {s}</label>))}
      </section>

      <section className="cmd-card cmd-sched" ref={schedRef}>
        <h3><Icon n="cal" /> Quick schedule a visit</h3>
        {slots === null ? <p className="cmd-muted">Checking the calendar…</p>
          : slots.slots.length ? <div className="cmd-slots">{slots.slots.slice(0, 2).map(s => (
            <button key={s.value} disabled={busy} onClick={() => book(s)}><small>{dayLabel(s.value)}</small>{s.time}</button>))}</div>
            : <p className="cmd-muted">{slots.reason || 'No open times right now.'}</p>}
        {slots?.slots?.length > 2 && <details><summary>Choose custom time ›</summary>
          <div className="cmd-slots">{slots.slots.slice(2).map(s => (
            <button key={s.value} disabled={busy} onClick={() => book(s)}><small>{dayLabel(s.value)}</small>{s.time}</button>))}</div></details>}
      </section>

      <section className="cmd-card cmd-compose">
        <h3>Add a note or reply...</h3>
        <div className="cmd-seg">
          {[['note', 'note', 'Note'], ['text', 'reply', 'Text'], ['email', 'mail', 'Email']].map(([k, ic, l]) => (
            <button key={k} className={mode === k ? 'on' : ''} onClick={() => focusComposer(k)}><Icon n={ic} />{l}</button>))}
        </div>
        {mode === 'email' && !f.is_demo && (f.reply_to?.channel === 'email'
          ? <p className={`cmd-replyto ${f.reply_to.threadable ? '' : 'bad'}`}>Replying to <b>{f.reply_to.to_name || f.reply_to.to}</b>{f.reply_to.to_name ? ` <${f.reply_to.to}>` : ''}{f.reply_to.subject ? ` · ${/^re:/i.test(f.reply_to.subject) ? f.reply_to.subject : `Re: ${f.reply_to.subject}`}` : ''}
            {!f.reply_to.threadable && <><br />{f.reply_to.why_not}</>}</p>
          : <p className="cmd-replyto bad">There is no email from this family to answer. Reply by text, or add a note.</p>)}
        {mode === 'email' && f.is_demo && <input className="cmd-subj" value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject" />}
        <textarea ref={composer} value={text} onChange={e => setText(e.target.value)} rows={4}
                  placeholder={mode === 'note' ? 'Write a note about this family...' : mode === 'text' ? 'Write a text...' : 'Write an email...'} />
        <div className="cmd-send">
          {mode !== 'note' && f.suggested_reply && text !== f.suggested_reply && <button className="link" onClick={() => setText(f.suggested_reply)}>Use suggested reply</button>}
          {mode === 'email' && !f.is_demo && !text.includes('{GUIDE}') && <button className="link" onClick={() => setText(t => `${t.trim()}${t.trim() ? ' ' : ''}{GUIDE}`)}>Add guide link</button>}
          {mode !== 'note' && !f.is_demo && <button disabled={busy || !text.trim() || !canSend} onClick={() => send('ai')}>Send &amp; let AI continue</button>}
          <button className="primary" disabled={busy || !text.trim() || (mode !== 'note' && !f.is_demo && !canSend)} onClick={() => send('human')}>
            {mode === 'note' ? 'Save note' : mode === 'text' ? 'Send text' : 'Send email'}</button>
        </div>
      </section>
      {toastText && <div className="cmd-toast" role="status">{toastText}</div>}
    </div>
  )
}


/* Everything the former SCI Live screen showed, kept on the same screen:
   system status, engagement, today's sending numbers (with all-time
   underneath), who is next and what just went out. */
const BAD = { failed: 'Failed', bounced: 'Bounced', undelivered: 'Not delivered' }
const titled = s => (s || '').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())

function TodayActivity({ live, engagement, open }) {
  const sent = live?.ob?.sent || []
  const upcoming = (live?.ob?.upcoming || []).slice(0, 5)
  return <>
    <SectionHead title="System & sending" />
    <SelfCheck compact />
    <div className="cmd-card cmd-eng"><EngagementGauge data={engagement} compact /></div>
    <SectionHead title="Next up" />
    {upcoming.length ? <div className="cmd-card cmd-list">{upcoming.map((u, i) => (
      <button key={i} onClick={() => u.lead_id && open(u.lead_id)}>
        <span><b>{titled(u.name)}</b><small>{[u.location, u.what, u.channels].filter(Boolean).join(' · ')}</small></span>
        <em>{time(u.at)}</em></button>))}</div>
      : <p className="cmd-card cmd-muted pad">{live?.ob?.next_day ? `Done for today. ${live.ob.next_day} follow-up texts go out tomorrow morning when sending opens.` : 'Nothing else scheduled today.'}</p>}
    <SectionHead title="Just sent" />
    {sent.length ? <div className="cmd-card cmd-list">{sent.slice(0, 8).map((s, i) => (
      <button key={i} onClick={() => s.lead_id && open(s.lead_id)}>
        <span><b>{titled(s.name)}</b><small>{s.location || ''}</small></span>
        <em className={BAD[s.status] ? 'bad' : ''}>{s.channel === 'text' ? 'Text' : 'Email'} · {BAD[s.status] || (s.status === 'delivered' ? 'Delivered' : s.status === 'opened' ? 'Opened' : 'Sent')} · {time(s.at)}</em>
      </button>))}</div>
      : <p className="cmd-card cmd-muted pad">Nothing sent yet today.</p>}
  </>
}
