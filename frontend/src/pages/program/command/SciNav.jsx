/* SCI NAVIGATION - the ONE menu for the whole SCI experience (Oct 16 repair).

   Before this file there were two: SCI Live drew the new side menu, but five
   of its items were plain <a href="/program?tab=..."> links into the Program
   Center, which drew its own older sidebar (Dashboard, Leads, Choose & Send,
   Outbox, ...). Clicking AI Assistant, Outreach Cadence, New Intake, Calendar
   or Settings therefore reloaded the page into the other layout, and every
   login / workspace switch landed on /program in that older layout too.

   Now both pages (SCI Live at /program/live and the program screens at
   /program?tab=...) render this same menu inside the same shell, and every
   item is a router link: no full reload, Back/Forward work, every screen has
   its own URL, and the old deep links (/program?tab=review, ?person=,
   ?location=) still open the same screens. Nothing the older sidebar offered
   is lost - its screens are listed under "Program tools". */
import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { fetchMyContexts, logout } from '../../../api/client'
import { readAuthority } from '../../../auth/workspaceAuthority'

const P = {
  live: <><rect x="3" y="3" width="7" height="8" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="15" width="7" height="6" rx="1" /></>,
  fam: <path d="M12 3 21 12 12 21 3 12z" />,
  conv: <><rect x="3" y="5" width="18" height="14" rx="1.5" /><path d="M3 9h18M3 13h18" /></>,
  ai: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
  cad: <><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M7 4v16M11 4v16M15 4v16" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  appt: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5h4" /></>,
  cal: <><rect x="4" y="4" width="16" height="16" rx="1.5" /><rect x="8" y="8" width="8" height="8" /></>,
  ana: <><rect x="3" y="3" width="18" height="18" rx="1.5" /><path d="M3 15l6-6 4 4 8-8M3 21l18-18" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1" /></>,
  dash: <><path d="M4 13a8 8 0 0 1 16 0" /><path d="M12 13l4-4" /><path d="M3 17h18" /></>,
  users: <><circle cx="9" cy="8" r="3.2" /><path d="M3 20c.6-3.3 3-5 6-5s5.4 1.7 6 5" /><path d="M16 4.5a3 3 0 0 1 0 6M18 14.5c1.6.6 2.7 2.4 3 5.5" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="1.5" /><path d="M3 7l9 6 9-6" /></>,
  review: <><path d="M9 11l2 2 4-4" /><rect x="4" y="3" width="16" height="18" rx="1.5" /></>,
  pin: <><path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>,
  img: <><rect x="3" y="4" width="18" height="16" rx="1.5" /><circle cx="9" cy="10" r="1.8" /><path d="M21 16l-5-5-8 9" /></>,
  rocket: <><path d="M12 15l-3-3c1.5-5 4.5-8 10-9-1 5.5-4 8.5-9 10z" /><path d="M9 12H5l2-3h4M12 15v4l3-2v-4" /></>,
  pulse: <path d="M3 12h4l2-6 4 12 2-6h6" />,
  more: <><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></>,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  inbox: <><path d="M3 13l3-8h12l3 8v6H3z" /><path d="M3 13h5l1 3h6l1-3h5" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></>,
}
export const NavIco = ({ n: name, s = 18 }) => (
  <svg viewBox="0 0 24 24" width={s} height={s} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{P[name]}</svg>
)

/* key, icon, label, URL, the older menu's name for the same screen */
export const NAV = [
  ['live', 'live', 'SCI Live', '/program/live'],
  ['search', 'fam', 'Families', '/program/live?s=search'],
  ['inbox', 'conv', 'Conversations', '/program/live?s=inbox'],
  ['responses', 'ai', 'AI Assistant', '/program?tab=responses', 'Responses'],
  ['campaigns', 'cad', 'Outreach Cadence', '/program?tab=campaigns', 'Campaigns'],
  ['send', 'plus', 'New Intake', '/program?tab=send', 'Choose & Send'],
  ['appts', 'appt', 'Appointments', '/program/live?s=appts'],
  ['appointments', 'cal', 'Calendar', '/program?tab=appointments', 'Appointments'],
  ['reports', 'ana', 'Analytics', '/program/live?s=reports'],
  ['consent', 'review', 'Opt-ins & Opt-outs', '/program/live?s=consent'],
  ['settings', 'gear', 'Settings', '/program?tab=settings'],
]
export const TOOLS = [
  ['dashboard', 'dash', 'Program Overview', '/program?tab=dashboard', 'Dashboard'],
  ['leads', 'users', 'Leads', '/program?tab=leads'],
  ['outbox', 'mail', 'Outbox', '/program?tab=outbox'],
  ['review', 'review', 'Review', '/program?tab=review'],
  ['locations', 'pin', 'Locations', '/program?tab=locations'],
  ['assets', 'img', 'Assets & Flyers', '/program?tab=assets'],
  ['launch', 'rocket', 'Launch Readiness', '/program?tab=launch'],
  ['health', 'pulse', 'Health', '/program?tab=health'],
]
const PC_TABS = new Set([...NAV, ...TOOLS].filter(i => i[3].startsWith('/program?')).map(i => i[0]))
export const LIVE_SCREENS = new Set(['search', 'inbox', 'appts', 'reports', 'consent'])

/** Which menu item the current URL belongs to. */
export function activeKey(loc) {
  const sp = new URLSearchParams(loc.search)
  if (loc.pathname.startsWith('/program/live') || loc.pathname.startsWith('/program/command')) {
    if (sp.get('f')) return sp.get('v') === 'journey' ? 'live' : 'inbox'
    if (sp.get('m')) return 'live'
    return LIVE_SCREENS.has(sp.get('s')) ? sp.get('s') : 'live'
  }
  if (loc.pathname === '/program' || loc.pathname === '/program/') {
    const t = sp.get('tab')
    return PC_TABS.has(t) ? t : 'dashboard'
  }
  return ''
}

/** Sign out (a login that only has SCI) or back to the main menu - the
    older sidebar's footer, kept. */
function ExitLink() {
  const navigate = useNavigate()
  const [view, setView] = useState(null)
  useEffect(() => {
    let alive = true
    fetchMyContexts().then(c => {
      if (alive) setView({
        only: !!c?.program_only,
        multi: (c?.workspace_contexts?.length || 0) + (c?.product_contexts?.length || 0) > 1,
      })
    }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (!view) return null
  if (view.multi) return <button type="button" className="sl-exit" onClick={() => navigate('/workspaces')}>← Switch program</button>
  return view.only
    ? <button type="button" className="sl-exit" onClick={async () => { await logout(); window.location.href = '/login' }}>Sign out</button>
    : <button type="button" className="sl-exit" onClick={() => navigate('/')}>← Back to main menu</button>
}

/** Badge numbers per menu key, e.g. { inbox: 3, responses: 2, review: 5 }. */
function Item({ item, on, badge, onClick }) {
  const [key, ic, label, to, was] = item
  return (
    <Link key={key} to={to} className={on ? 'on' : ''} aria-current={on ? 'page' : undefined}
      title={was ? `${label} (formerly ${was})` : label} onClick={onClick}>
      <NavIco n={ic} />{label}{badge ? <i>{badge}</i> : null}
    </Link>
  )
}

export function SideNav({ badges = {}, onSame }) {
  const loc = useLocation()
  const cur = activeKey(loc)
  const click = key => () => { if (key === cur && onSame) onSame(key) }
  return (
    <aside className="sl-side" aria-label="SCI menu">
      <div className="sl-brand"><span className="sl-mark">E</span>EvoSys <em>Pro</em></div>
      <nav aria-label="SCI screens">{NAV.map(i => <Item key={i[0]} item={i} on={cur === i[0]} badge={badges[i[0]]} onClick={click(i[0])} />)}</nav>
      <div className="sl-navgroup">Program tools</div>
      <nav aria-label="Program tools" className="sl-tools">{TOOLS.map(i => <Item key={i[0]} item={i} on={cur === i[0]} badge={badges[i[0]]} onClick={click(i[0])} />)}</nav>
      <div className="sl-sidebot">
        {readAuthority().role === 'spectator' && <span className="sl-pilot sl-viewonly">● VIEW ONLY · nothing can be changed or sent</span>}
        <span className="sl-pilot">● PILOT WORKSPACE</span>
        <p>SCI outreach proof of concept · EvoSys Pro-operated · Not corporate implementation</p>
        <ExitLink />
      </div>
    </aside>
  )
}

/** Phone / tablet: the bottom tabs plus a "More" sheet holding the full menu. */
export function MobileNav({ badges = {}, onSame }) {
  const loc = useLocation()
  const cur = activeKey(loc)
  const [open, setOpen] = useState(false)
  useEffect(() => { setOpen(false) }, [loc.pathname, loc.search])
  useEffect(() => {
    if (!open) return undefined
    const k = e => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open])
  const tabs = [['live', 'live', 'Performance', '/program/live'], ['inbox', 'inbox', 'Inbox', '/program/live?s=inbox'],
    ['search', 'search', 'Search', '/program/live?s=search'], ['appts', 'cal', 'Appointments', '/program/live?s=appts']]
  const inTabs = tabs.some(t => t[0] === cur)
  const click = key => () => { if (key === cur && onSame) onSame(key) }
  return <>
    {open && <div className="sl-sheet" role="dialog" aria-modal="true" aria-label="SCI menu">
      <button type="button" className="sl-sheet-scrim" aria-label="Close menu" onClick={() => setOpen(false)} />
      <div className="sl-sheet-body">
        <div className="sl-sheet-head"><b>SCI menu{readAuthority().role === 'spectator' ? ' · view only' : ''}</b>
          <button type="button" aria-label="Close menu" onClick={() => setOpen(false)}><NavIco n="x" /></button></div>
        <nav aria-label="SCI screens">{NAV.map(i => <Item key={i[0]} item={i} on={cur === i[0]} badge={badges[i[0]]} onClick={click(i[0])} />)}</nav>
        <div className="sl-navgroup">Program tools</div>
        <nav aria-label="Program tools">{TOOLS.map(i => <Item key={i[0]} item={i} on={cur === i[0]} badge={badges[i[0]]} onClick={click(i[0])} />)}</nav>
        <ExitLink />
      </div>
    </div>}
    <nav className="cmd-tabs" aria-label="Sections">
      {tabs.map(([k, ic, lbl, to]) => (
        <Link key={k} to={to} className={cur === k ? 'on' : ''} aria-current={cur === k ? 'page' : undefined} onClick={click(k)}>
          <span className="cmd-tabicon"><NavIco n={ic} s={22} />{badges[k] ? <i>{badges[k]}</i> : null}</span>{lbl}
        </Link>))}
      <button type="button" className={!inTabs && cur ? 'on' : ''} aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <span className="cmd-tabicon"><NavIco n="more" s={22} /></span>More
      </button>
    </nav>
  </>
}
