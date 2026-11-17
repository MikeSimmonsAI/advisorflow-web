import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import './commandCenter.css'
import { accentPhoto, cardPhoto, widePhoto } from './photos'
import DemosPanel from './DemosPanel'
import {
  availableCategories, cardStatus, categoryOf, chooseFeatured, factLine, firstName, healthLabel,
  matchesQuery, photoKeyFor, roleLabel, shortLine, summaryCounts,
} from './commandCenterModel'

/**
 * PROGRAM ACCESS V2 - the EvoSys Pro business command center, built to the
 * owner-approved hybrid mockup (issue #664): dark header with the summary
 * tiles, featured banner, four columns of SHORT, WIDE photo cards, and a
 * single row of prospect previews.
 *
 * Presentation only. The caller hands in cards built from the server's
 * /auth/my-contexts answer for THIS login (ProgramAccessPortal.jsx), plus the
 * person's server-kept pins and Needs Attention items. Nothing here decides
 * access: every destination re-checks on the server, and a card the server
 * did not list never reaches this component.
 */

const VIEW_KEY = 'ev.programAccess.view'
function readView() {
  try { return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid' } catch { return 'grid' }
}
function saveView(v) {
  try { localStorage.setItem(VIEW_KEY, v) } catch { /* storage unavailable: keep in memory only */ }
}

/* How many card columns the grid shows right now (the same breakpoints as
 * commandCenter.css). The first row of cards is the tall "lead" row; later
 * rows are compact, as in the approved mockup. */
const COLUMN_QUERIES = [['(min-width: 1341px)', 4], ['(min-width: 1024px)', 3], ['(min-width: 641px)', 2]]
function currentColumns() {
  if (typeof window === 'undefined' || !window.matchMedia) return 4
  for (const [q, n] of COLUMN_QUERIES) if (window.matchMedia(q).matches) return n
  return 1
}
function useColumns() {
  const [cols, setCols] = useState(currentColumns)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined
    const lists = COLUMN_QUERIES.map(([q]) => window.matchMedia(q))
    const update = () => setCols(currentColumns())
    lists.forEach(l => (l.addEventListener ? l.addEventListener('change', update) : l.addListener(update)))
    return () => lists.forEach(l => (l.removeEventListener ? l.removeEventListener('change', update) : l.removeListener(update)))
  }, [])
  return cols
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

const Icon = {
  home: <path d="M3 11l9-7 9 7M5 10v10h5v-6h4v6h5V10" />,
  layers: <path d="M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5" />,
  day: <path d="M4 5h16v15H4zM4 9h16M8 3v4M16 3v4M8 14l2.5 2.5L16 12" />,
  bell: <path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0" />,
  team: <path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21v-1a6 6 0 0 1 12 0v1M16 3.5a4 4 0 0 1 0 7.5M22 21v-1a6 6 0 0 0-4-5.6" />,
  book: <path d="M4 5h16v15H4zM4 9h16M9 3v4M15 3v4M8 13h3M8 16h6" />,
  shield: <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />,
  gear: <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />,
  out: <path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" />,
  search: <path d="M11 4a7 7 0 1 0 4.4 12.4L20 21M11 4a7 7 0 0 1 0 14" />,
  building: <path d="M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6M9 11h.01M15 11h.01" />,
  people: <path d="M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM1 21v-1a7 7 0 0 1 14 0v1M17 11a3 3 0 1 0 0-6M23 21v-1a5 5 0 0 0-4-4.9" />,
  bars: <path d="M5 20V12M10 20V6M15 20v-9M20 20V4" />,
  check: <path d="M20 6L9 17l-5-5" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  arrow: <path d="M5 12h13M13 6l6 6-6 6" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" />,
  list: <path d="M4 6h16M4 12h16M4 18h16" />,
  grid: <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" />,
  binoculars: <path d="M6 18a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 18a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM9 15h6M4 13l2-8h3l1 7M20 13l-2-8h-3l-1 7" />,
  star: <path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" />,
  key: <path d="M15 8a4 4 0 1 1-3.5 6L5 20.5 3.5 19M8 17l2 2" />,
  play: <path d="M3 5h18v12H3zM8 21h8M12 17v4M10 8.5v5l4.5-2.5z" />,
}
function Svg({ name, size = 20, fill = 'none' }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false"
         fill={fill} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {Icon[name]}
    </svg>
  )
}

/* The white icon tile on each card (mockup): one glyph and colour per kind of
 * workspace, chosen from the same photo key as the card - never by a client's
 * name. Initials ("VG", "DB") come from the card's own symbol. */
const TILE = {
  restland: { color: '#16a34a', path: 'M5 19c9 0 14-5 14-14-9 0-14 5-14 14zM5 19l7-7' },
  sci: { color: '#2563eb', path: 'M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM1 21v-1a7 7 0 0 1 14 0v1M17 11a3 3 0 1 0 0-6M23 21v-1a5 5 0 0 0-4-4.9' },
  energy: { color: '#f59e0b', path: 'M13 2L4 14h7l-1 8 9-12h-7z', fill: true },
  roofing: { color: '#15803d', path: 'M3 11l9-7 9 7M5 10v10h14V10M10 20v-5h4v5' },
  wholesale: { color: '#7c3aed', path: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5' },
  insurance: { color: '#0d9488', path: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4' },
  gold: { color: '#f59e0b', path: 'M6 3h12l3 6-9 12L3 9zM3 9h18M9 3l3 6 3-6' },
  cleaning: { color: '#0ea5e9', path: 'M12 2v6M8 8h8l1 13H7zM10 12h4' },
  backoffice: { color: '#1e3a8a', path: 'M6 3h12v18H6zM9 8h6M9 12h6M9 16h4' },
  volt: { color: '#dc2626', path: 'M13 2L4 14h7l-1 8 9-12h-7z', fill: true },
  consumer: { color: '#dc2626', path: 'M12 5v14M5 12h14' },
  acquisition: { color: '#7c3aed', path: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z' },
  executive: { color: '#1e3a8a', path: 'M12 2l3 7h7l-5.5 4.5 2 7.5-6.5-4.5L5.5 21l2-7.5L2 9h7z' },
}
function CardTile({ item, photo }) {
  const t = TILE[photo]
  if (!t) {
    const sym = String(item.symbol || '')
    const label = /^[A-Z]{2,3}$/.test(sym) ? sym : initials(item.display)
    return <span className="pa2-tile pa2-tile--text" aria-hidden="true">{label}</span>
  }
  return (
    <span className="pa2-tile" aria-hidden="true" style={{ color: t.color }}>
      <svg viewBox="0 0 24 24" width="24" height="24" fill={t.fill ? 'currentColor' : 'none'} stroke="currentColor"
           strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round"><path d={t.path} /></svg>
    </span>
  )
}

export default function CommandCenter({
  user, cards, others, previews, pins, pinNote, onTogglePin, keyOf, onChoose, hrefOf,
  attention, health, isOwner, canManageTeam, myDayHref, demos = [], onSignOut,
}) {
  // THE DEMOS PAGE lives in the address (/workspaces?view=demos) so Back and
  // a bookmark both work; it is offered only when the server listed demos.
  const [params, setParams] = useSearchParams()
  const onDemos = params.get('view') === 'demos' && demos.length > 0
  const showView = (v) => {
    setNavOpen(false)
    const next = new URLSearchParams(params)
    if (v) next.set('view', v); else next.delete('view')
    setParams(next)
    if (typeof window !== 'undefined') window.scrollTo(0, 0)
  }
  const [view, setView] = useState(readView)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [navOpen, setNavOpen] = useState(false)
  const [bellOpen, setBellOpen] = useState(false)
  const bellRef = useRef(null)
  const workspacesRef = useRef(null)
  const columns = useColumns()

  const personName = user?.full_name || user?.name || user?.email || ''
  const first = firstName(user)
  const role = roleLabel(user?.role)

  // "Other authorized workspaces" (a membership with no program yet) are
  // full cards too, in the client category.
  const otherCards = useMemo(() => (others || []).map(w => ({
    ...w, display: w.organization_name, summary: 'Your authorized business workspace',
    kind: 'BUSINESS WORKSPACE', program: 'business', variant: 'general', symbol: initials(w.organization_name),
  })), [others])
  const all = useMemo(() => [...cards, ...otherCards], [cards, otherCards])

  const pinnedSet = useMemo(() => new Set(pins), [pins])
  const ordered = useMemo(() => {
    const pinned = pins.map(k => all.find(c => keyOf(c) === k)).filter(Boolean)
    return [...pinned, ...all.filter(c => !pinnedSet.has(keyOf(c)))]
  }, [all, pins, pinnedSet, keyOf])

  const featured = chooseFeatured(all, pins, keyOf)
  const counts = summaryCounts(cards, others, previews)
  const healthInfo = healthLabel(health)
  const cats = availableCategories(all)
  const activeCat = cats.some(c => c.key === category) ? category : 'all'

  const q = query.trim().toLowerCase()
  const shown = ordered.filter(c => (activeCat === 'all' || categoryOf(c) === activeCat) && matchesQuery(c, q))
  const shownPreviews = previews.filter(p => matchesQuery(p, q))

  // Close the notifications list on outside click / Escape.
  useEffect(() => {
    if (!bellOpen) return undefined
    const onDoc = (e) => { if (bellRef.current && !bellRef.current.contains(e.target)) setBellOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setBellOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [bellOpen])
  useEffect(() => {
    if (!navOpen) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setNavOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [navOpen])

  const choose = (v) => { setView(v); saveView(v) }

  const nothing = !all.length && !previews.length
  const featuredPhoto = featured ? photoKeyFor(featured) : null
  // Right-hand picture: the program's dedicated equipment shot where one
  // exists (Commercial Cleaning), otherwise its own card photo.
  const featuredAccent = featuredPhoto ? (accentPhoto(featuredPhoto) || cardPhoto(featuredPhoto)) : null
  const featuredStatus = featured ? cardStatus(featured) : null
  const allCount = all.length

  return (
    <div className={'pa2' + (navOpen ? ' pa2--nav-open' : '')}>
      <a className="pa2-skip" href="#pa2-main">Skip to workspaces</a>

      <aside className="pa2-side" id="pa2-nav" aria-label="Program Access navigation">
        <div className="pa2-brand">
          <img src="/brands/evosyspro-emblem-96.png" alt="" width="64" height="64" />
          <div className="pa2-brand-text"><b>EvoSys Pro</b><span>Business Command Platform</span></div>
          <button type="button" className="pa2-side-close" aria-label="Close menu" onClick={() => setNavOpen(false)}>
            <Svg name="close" />
          </button>
        </div>
        <nav className="pa2-nav">
          <a className={'pa2-nav-item' + (onDemos ? '' : ' is-active')} href="/workspaces"
             aria-current={onDemos ? undefined : 'page'}
             onClick={(e) => { e.preventDefault(); showView(null) }}>
            <Svg name="home" size={22} /> Program Access
          </a>
          {demos.length > 0 && (
            <a className={'pa2-nav-item' + (onDemos ? ' is-active' : '')} href="/workspaces?view=demos"
               aria-current={onDemos ? 'page' : undefined}
               onClick={(e) => { e.preventDefault(); showView('demos') }}>
              <Svg name="play" size={22} /> Demos <span className="pa2-nav-count">{demos.length}</span>
            </a>
          )}
          {myDayHref && (
            <a className="pa2-nav-item" href={myDayHref}><Svg name="day" size={22} /> My Day</a>
          )}
          {attention.length > 0 && (
            <button type="button" className="pa2-nav-item" aria-haspopup="true" aria-expanded={bellOpen}
                    onClick={() => { setNavOpen(false); setBellOpen(true) }}>
              <Svg name="bell" size={22} /> Notifications <span className="pa2-nav-count">{attention.length}</span>
            </button>
          )}
          {(isOwner || canManageTeam) && (
            <a className="pa2-nav-item" href={isOwner ? '/god/access' : '/sales/my-team'}>
              <Svg name="team" size={22} /> People &amp; Access
            </a>
          )}
          <a className="pa2-nav-item" href="/training"><Svg name="book" size={22} /> Resources</a>
          {isOwner && (
            <a className="pa2-nav-item pa2-nav-item--owner" href="/god"><Svg name="shield" size={22} /> God Mode</a>
          )}
          <a className="pa2-nav-item" href="/change-password"><Svg name="gear" size={22} /> Settings</a>
        </nav>
        <div className="pa2-me">
          <span className="pa2-me-avatar" aria-hidden="true">{initials(personName)}<i /></span>
          <span className="pa2-me-text">
            <b title={personName}>{personName || 'Signed in'}</b>
            {role && <small>{role}</small>}
          </span>
          <button type="button" className="pa2-me-out" onClick={onSignOut} aria-label="Sign out" title="Sign out">
            <Svg name="out" size={18} />
          </button>
        </div>
      </aside>
      <div className="pa2-scrim" aria-hidden="true" onClick={() => setNavOpen(false)} />

      <div className="pa2-body">
        <header className="pa2-top">
          <div className="pa2-top-row">
            <button type="button" className="pa2-menu" aria-controls="pa2-nav" aria-expanded={navOpen}
                    aria-label="Open menu" onClick={() => setNavOpen(true)}>
              <Svg name="menu" />
            </button>
            <div className="pa2-hello">
              <h1>Welcome back{first ? ', ' + first : ''}</h1>
              <p>Your business command center. Access, manage and grow all of your EvoSys workspaces.</p>
            </div>
            <div className="pa2-top-tools">
              {(allCount + previews.length + demos.length) > 0 && (
                <label className="pa2-search">
                  <Svg name="search" size={20} />
                  <span className="pa2-sr">Search your workspaces</span>
                  <input type="search" value={query} onChange={e => setQuery(e.target.value)}
                         placeholder={onDemos ? 'Search demos…' : 'Search workspaces…'}
                         aria-label={onDemos ? 'Search your demos' : 'Search your workspaces'} />
                </label>
              )}
              {attention.length > 0 && (
                <div className="pa2-bell" ref={bellRef}>
                  <button type="button" className="pa2-bell-btn" aria-haspopup="true" aria-expanded={bellOpen}
                          aria-label={'Notifications: ' + attention.length + ' need attention'}
                          onClick={() => setBellOpen(o => !o)}>
                    <Svg name="bell" size={28} />
                    <span className="pa2-bell-dot" aria-hidden="true" />
                  </button>
                  {bellOpen && (
                    <div className="pa2-bell-pop" role="dialog" aria-label="Needs attention">
                      <p className="pa2-bell-title">Needs attention</p>
                      <ul>
                        {attention.map(a => (
                          <li key={a.kind}>
                            <a href={a.href}><b>{a.title}</b>{a.detail && <span>{a.detail}</span>}</a>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {!nothing && (
            <section className="pa2-stats" aria-label="Summary"
                     style={{ '--pa2-stat-cols': 2 + (counts.previews > 0 ? 1 : 0) + (healthInfo ? 1 : 0) }}>
              <Stat icon="layers" value={counts.workspaces} label="Workspaces" title="Authorized workspaces this login can open" />
              <Stat icon="people" value={counts.clients} label="Active Clients" short="Clients" title="Client businesses this login can open" />
              {counts.previews > 0 && (
                <Stat icon="bars" value={counts.previews} label="Previews" title="Prospect demos (not customers)" />
              )}
              {healthInfo && (
                <Stat icon="check" value={healthInfo.label} label="Platform Health" short="Health" title={healthInfo.detail}
                      health={healthInfo.tone} />
              )}
            </section>
          )}
        </header>

        <main id="pa2-main" className="pa2-main" tabIndex={-1}>
          {pinNote && <p className="pa2-note" role="status">{pinNote}</p>}

          {onDemos && <DemosPanel demos={demos} query={q} rawQuery={query.trim()} onBack={() => showView(null)} />}

          {!onDemos && featured && !q && (
            <section className="pa2-feature" aria-labelledby="pa2-feature-title" data-program-key={keyOf(featured)}>
              <img className="pa2-feature-img" src={widePhoto(featuredPhoto)} alt="" width="900" height="440"
                   fetchpriority="high" />
              {featuredAccent && (
                <img className="pa2-feature-accent" src={featuredAccent} alt="" width="520" height="440" />
              )}
              <div className="pa2-feature-shade" aria-hidden="true" />
              <div className="pa2-feature-body">
                <p className="pa2-eyebrow"><Svg name="star" size={13} fill="#facc15" /> Featured Workspace</p>
                <h2 id="pa2-feature-title">{featured.display}</h2>
                <p className="pa2-feature-sum">{factLine(featured)}</p>
                <div className="pa2-feature-actions">
                  {featuredStatus && (
                    <span className={'pa2-status pa2-status--' + featuredStatus.tone + ' pa2-status--lg'}>
                      {featuredStatus.label === 'Active' ? 'Active Workspace' : featuredStatus.label}
                    </span>
                  )}
                  <OpenAction item={featured} hrefOf={hrefOf} onChoose={onChoose}
                              className="pa2-btn pa2-btn--primary pa2-btn--lg" />
                  {isOwner && (
                    <a className="pa2-btn pa2-btn--ghost pa2-btn--lg" href="/god/access">
                      <Svg name="key" size={17} /> Manage Access
                    </a>
                  )}
                </div>
              </div>
            </section>
          )}

          {!onDemos && allCount > 0 && (
            <section className="pa2-section" aria-labelledby="pa2-ws-title" ref={workspacesRef} tabIndex={-1}>
              <div className="pa2-section-head">
                <h2 id="pa2-ws-title"><Svg name="building" size={22} />
                  {q ? 'Results for “' + query.trim() + '”' : 'My Workspaces'}</h2>
                {cats.length > 2 && (
                  <div className="pa2-filters" role="group" aria-label="Filter workspaces">
                    {cats.map(c => (
                      <button type="button" key={c.key} aria-pressed={activeCat === c.key}
                              className={'pa2-chip' + (activeCat === c.key ? ' is-on' : '')}
                              onClick={() => setCategory(c.key)}>
                        {c.key === 'all' ? c.label + ' (' + allCount + ')' : c.label}
                      </button>
                    ))}
                  </div>
                )}
                {allCount > 1 && (
                  <div className="pa2-viewtoggle" role="group" aria-label="Layout">
                    <button type="button" aria-pressed={view === 'grid'} onClick={() => choose('grid')}
                            aria-label="Grid view" title="Grid view"><Svg name="grid" size={18} /></button>
                    <button type="button" aria-pressed={view === 'list'} onClick={() => choose('list')}
                            aria-label="List view" title="List view"><Svg name="list" size={18} /></button>
                  </div>
                )}
              </div>
              {shown.length > 0 ? (
                <div className={'pa2-grid pa2-grid--' + view} role="list" aria-label="Your workspaces">
                  {shown.map((item, i) => (
                    <div role="listitem" key={keyOf(item)}>
                      <WorkspaceTile item={item} layout={view} hrefOf={hrefOf} onChoose={onChoose}
                                     size={i < columns ? 'lead' : 'compact'}
                                     pinned={pinnedSet.has(keyOf(item))} onTogglePin={onTogglePin}
                                     programKey={keyOf(item)} />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="pa2-none">
                  {q ? 'No workspace you can open matches that search.' : 'Nothing in this category for your login.'}
                </p>
              )}
            </section>
          )}

          {!onDemos && previews.length > 0 && (
            <section className="pa2-previews" aria-labelledby="pa2-pv-title">
              <div className="pa2-pv-intro">
                <h2 id="pa2-pv-title"><Svg name="binoculars" size={24} /> Prospect Previews</h2>
                <p>Demos built for prospects who are not customers yet. Open one to see exactly what that prospect was shown.</p>
              </div>
              {shownPreviews.length > 0
                ? shownPreviews.map(item => <PreviewTile key={item.organization_id} item={item} onChoose={onChoose} />)
                : <p className="pa2-none pa2-none--pv">No preview matches that search.</p>}
            </section>
          )}

          {!onDemos && nothing && (
            <div className="pa2-empty">
              <b>No workspaces have been assigned to this account.</b>
              <span>Ask your administrator to grant access to a program.</span>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

function Stat({ icon, value, label, short, title, health }) {
  return (
    <div className={'pa2-stat' + (health ? ' pa2-stat--health pa2-stat--' + health : '')} title={title}
         aria-label={label + ': ' + value}>
      <span className="pa2-stat-icon" aria-hidden="true"><Svg name={icon} size={health ? 20 : 30} /></span>
      <span className="pa2-stat-text">
        <b className="pa2-stat-value">{value}</b>
        <span className="pa2-stat-label">
          {short ? <><span className="pa2-stat-long">{label}</span><span className="pa2-stat-short" aria-hidden="true">{short}</span></> : label}
        </span>
      </span>
    </div>
  )
}

/** The single real action: a link where the destination is a plain URL (no
 *  pop-up blocking), otherwise a button that runs openProgramItem. */
function OpenAction({ item, hrefOf, onChoose, className, stretch }) {
  const label = item.actionLabel || 'Open Workspace'
  const aria = label + ': ' + item.display + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')
  const href = hrefOf(item)
  const cls = className + (stretch ? ' pa2-stretch' : '')
  const inner = stretch ? <>Open<Svg name="arrow" size={17} /></> : <><Svg name="arrow" size={19} />{label}</>
  return href
    ? <a className={cls} href={href} aria-label={aria}>{inner}</a>
    : <button type="button" className={cls} aria-label={aria} onClick={() => onChoose(item)}>{inner}</button>
}

function WorkspaceTile({ item, layout, hrefOf, onChoose, pinned, onTogglePin, programKey, size = 'lead' }) {
  const status = cardStatus(item)
  const photo = photoKeyFor(item)
  const line = shortLine(item)
  return (
    <article className={'pa2-card pa2-card--' + layout + (layout === 'grid' ? ' pa2-card--' + size : '')}
             data-program-key={programKey}
             data-category={categoryOf(item)}>
      <img className="pa2-card-img" src={cardPhoto(photo)} alt="" width="720" height="330" loading="lazy" decoding="async" />
      <div className="pa2-card-head">
        <CardTile item={item} photo={photo} />
        <div className="pa2-card-titles">
          <h3 className="pa2-card-name">{item.display}</h3>
          {line && <p className="pa2-card-line">{line}</p>}
        </div>
      </div>
      <div className="pa2-card-foot">
        {status && <span className={'pa2-status pa2-status--' + status.tone}>{status.label}</span>}
        <OpenAction item={item} hrefOf={hrefOf} onChoose={onChoose} className="pa2-btn pa2-btn--primary pa2-btn--card" stretch />
      </div>
      {onTogglePin && (
        <button type="button" className={'pa2-pin' + (pinned ? ' is-pinned' : '')} aria-pressed={!!pinned}
                aria-label={(pinned ? 'Unpin ' : 'Pin ') + item.display + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')}
                title={pinned ? 'Unpin' : 'Pin (the first pinned workspace is featured)'}
                onClick={() => onTogglePin(item)}>
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false">
            <path d="M9 3h6l-1 6 4 4v2h-5v6l-1 1-1-1v-6H6v-2l4-4z" fill={pinned ? 'currentColor' : 'none'}
                  stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" />
          </svg>
        </button>
      )}
    </article>
  )
}

function PreviewTile({ item, onChoose }) {
  // A preview opens the deal's existing demo link: a REAL link (new tab), never
  // a script-opened pop-up. A God-granted preview opens inside this app.
  const internal = !!(item.grant_id && item.path)
  const href = item.href || item.url
  const aria = 'Open preview: ' + item.display
  const inner = <>Open Preview <Svg name="external" size={16} /></>
  return (
    <article className="pa2-pv" data-program-key={'preview:' + item.organization_id}
             title={(item.facts || []).filter(Boolean).join(' · ')}>
      <img className="pa2-pv-img" src={cardPhoto(photoKeyFor(item))} alt="" width="720" height="330"
           loading="lazy" decoding="async" />
      <div className="pa2-pv-body">
        <h3 className="pa2-pv-name">{item.display}</h3>
        {href ? (
          internal
            ? <a className="pa2-btn pa2-btn--outline pa2-stretch" href={href} aria-label={aria}>{inner}</a>
            : <a className="pa2-btn pa2-btn--outline pa2-stretch" href={href} target="_blank" rel="noopener noreferrer"
                 aria-label={aria + ' (opens in a new tab)'}>{inner}</a>
        ) : (
          <button type="button" className="pa2-btn pa2-btn--outline pa2-stretch" aria-label={aria} onClick={() => onChoose(item)}>
            {inner}
          </button>
        )}
      </div>
    </article>
  )
}
