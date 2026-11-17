import { useEffect, useMemo, useRef, useState } from 'react'
import './commandCenter.css'
import { cardPhoto, widePhoto } from './photos'
import {
  availableCategories, categoryOf, chooseFeatured, firstName, healthLabel, matchesQuery,
  photoKeyFor, roleLabel, summaryCounts,
} from './commandCenterModel'

/**
 * PROGRAM ACCESS V2 - the EvoSys Pro business command center (owner-approved
 * hybrid design, issue "Program Access V2 - Premium UI Redesign").
 *
 * Presentation only. The caller hands in cards that were built from the
 * server's /auth/my-contexts answer for THIS login (ProgramAccessPortal.jsx),
 * plus the person's server-kept pins and Needs Attention items. Nothing here
 * decides access: every card's destination re-checks on the server, and a card
 * the server did not list never reaches this component.
 */

const VIEW_KEY = 'ev.programAccess.view'
function readView() {
  try { return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid' } catch { return 'grid' }
}
function saveView(v) {
  try { localStorage.setItem(VIEW_KEY, v) } catch { /* storage unavailable: keep in memory only */ }
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

const Icon = {
  grid: <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" />,
  layers: <path d="M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5" />,
  eye: <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" />,
  bell: <path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0" />,
  team: <path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21v-1a6 6 0 0 1 12 0v1M16 3.5a4 4 0 0 1 0 7.5M22 21v-1a6 6 0 0 0-4-5.6" />,
  shield: <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />,
  key: <path d="M15 8a4 4 0 1 1-3.5 6L5 20.5 3.5 19M8 17l2 2" />,
  out: <path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" />,
  search: <path d="M11 4a7 7 0 1 0 4.4 12.4L20 21M11 4a7 7 0 0 1 0 14" />,
  building: <path d="M4 21V5l8-3v19M12 9h8v12M7 8h2M7 12h2M7 16h2M15 13h2M15 17h2M2 21h20" />,
  users: <path d="M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM1 21v-1a7 7 0 0 1 14 0v1M17 11a3 3 0 1 0 0-6M23 21v-1a5 5 0 0 0-4-4.9" />,
  pulse: <path d="M3 12h4l2-6 4 12 2-6h6" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  arrow: <path d="M5 12h13M13 6l6 6-6 6" />,
  list: <path d="M4 6h16M4 12h16M4 18h16" />,
}
function Svg({ name, size = 20 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false"
         fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {Icon[name]}
    </svg>
  )
}

/** A card's real status, only where the server or its presentation says one. */
function statusOf(item) {
  if (item.badge) return item.badge
  if (item.program === 'preview') return item.stage_label || null
  if (item.orgLabel === 'View only') return 'View only'
  return null
}

export default function CommandCenter({
  user, cards, others, previews, pins, pinNote, onTogglePin, keyOf, onChoose, hrefOf,
  attention, health, isOwner, canManageTeam, onSignOut,
}) {
  const [view, setView] = useState(readView)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [navOpen, setNavOpen] = useState(false)
  const [bellOpen, setBellOpen] = useState(false)
  const bellRef = useRef(null)
  const workspacesRef = useRef(null)
  const previewsRef = useRef(null)

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
  const jump = (ref) => {
    setNavOpen(false)
    const el = ref.current
    if (!el) return
    const reduce = typeof window !== 'undefined' && window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
    el.focus({ preventScroll: true })
  }

  const nothing = !all.length && !previews.length
  const statCount = 2 + (counts.previews > 0 ? 1 : 0) + (healthInfo ? 1 : 0)

  return (
    <div className={'pa2' + (navOpen ? ' pa2--nav-open' : '')}>
      <a className="pa2-skip" href="#pa2-main">Skip to workspaces</a>

      <aside className="pa2-side" id="pa2-nav" aria-label="Program Access navigation">
        <div className="pa2-logo">
          <img src="/brands/evosyspro-logo-on-dark-440.png" alt="EvoSys Pro" width="440" height="120" />
          <button type="button" className="pa2-side-close" aria-label="Close menu" onClick={() => setNavOpen(false)}>
            <Svg name="close" />
          </button>
        </div>
        <nav className="pa2-nav">
          <a className="pa2-nav-item is-active" href="/workspaces" aria-current="page">
            <Svg name="grid" /> Program Access
          </a>
          {all.length > 0 && (
            <button type="button" className="pa2-nav-item" onClick={() => jump(workspacesRef)}>
              <Svg name="layers" /> My Workspaces <span className="pa2-nav-count">{all.length}</span>
            </button>
          )}
          {previews.length > 0 && (
            <button type="button" className="pa2-nav-item" onClick={() => jump(previewsRef)}>
              <Svg name="eye" /> Prospect Previews <span className="pa2-nav-count">{previews.length}</span>
            </button>
          )}
          {attention.length > 0 && (
            <button type="button" className="pa2-nav-item" aria-haspopup="true" aria-expanded={bellOpen}
                    onClick={() => { setNavOpen(false); setBellOpen(true) }}>
              <Svg name="bell" /> Notifications <span className="pa2-nav-count pa2-nav-count--alert">{attention.length}</span>
            </button>
          )}
          {canManageTeam && (
            <a className="pa2-nav-item" href="/sales/my-team"><Svg name="team" /> People &amp; Access</a>
          )}
          {isOwner && (
            <a className="pa2-nav-item pa2-nav-item--owner" href="/god"><Svg name="shield" /> God Mode</a>
          )}
          <a className="pa2-nav-item" href="/change-password"><Svg name="key" /> Settings</a>
        </nav>
        <div className="pa2-me">
          <span className="pa2-me-avatar" aria-hidden="true">{initials(personName)}</span>
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
          <button type="button" className="pa2-menu" aria-controls="pa2-nav" aria-expanded={navOpen}
                  aria-label="Open menu" onClick={() => setNavOpen(true)}>
            <Svg name="menu" />
          </button>
          <img className="pa2-top-logo" src="/brands/evosyspro-emblem-96.png" alt="" width="32" height="32" />
          <div className="pa2-hello">
            <h1>Welcome back{first ? ', ' + first : ''}</h1>
            <p>Your business command center. Access, manage and grow your EvoSys workspaces.</p>
          </div>
          <div className="pa2-top-tools">
            {(all.length + previews.length) > 0 && (
              <label className="pa2-search">
                <Svg name="search" size={18} />
                <span className="pa2-sr">Search your workspaces</span>
                <input type="search" value={query} onChange={e => setQuery(e.target.value)}
                       placeholder="Search workspaces…" aria-label="Search your workspaces" />
              </label>
            )}
            {attention.length > 0 && (
              <div className="pa2-bell" ref={bellRef}>
                <button type="button" className="pa2-bell-btn" aria-haspopup="true" aria-expanded={bellOpen}
                        aria-label={'Notifications: ' + attention.length + ' need attention'}
                        onClick={() => setBellOpen(o => !o)}>
                  <Svg name="bell" />
                  <span className="pa2-bell-dot">{attention.length}</span>
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
            <span className="pa2-top-avatar" aria-hidden="true" title={personName}>{initials(personName)}</span>
          </div>
        </header>

        <main id="pa2-main" className="pa2-main" tabIndex={-1}>
          {pinNote && <p className="pa2-note" role="status">{pinNote}</p>}

          {!nothing && (
            <section className={'pa2-stats pa2-stats--' + statCount} aria-label="Summary">
              <Stat icon="layers" tone="blue" value={counts.workspaces} label="Authorized Workspaces"
                    sub="Destinations this login can open" />
              <Stat icon="building" tone="green" value={counts.clients} label="Active Clients"
                    sub="Client businesses you can open" />
              {counts.previews > 0 && (
                <Stat icon="eye" tone="violet" value={counts.previews} label="Prospect Previews"
                      sub="Demos for prospects, not customers" />
              )}
              {healthInfo && (
                <Stat icon="pulse" tone={healthInfo.tone === 'ok' ? 'teal' : 'amber'} value={null}
                      label="Platform Health" sub={healthInfo.label} health={healthInfo.tone} />
              )}
            </section>
          )}

          {featured && !q && (
            <section className="pa2-feature" aria-labelledby="pa2-feature-title"
                     data-program-key={keyOf(featured)}>
              <img className="pa2-feature-img" src={widePhoto(photoKeyFor(featured))} alt=""
                   width="1600" height="440" fetchpriority="high" />
              <div className="pa2-feature-shade" aria-hidden="true" />
              <div className="pa2-feature-body">
                <p className="pa2-eyebrow">
                  {pinnedSet.has(keyOf(featured)) ? 'Featured · Pinned' : 'Featured workspace'}
                </p>
                <h2 id="pa2-feature-title">{featured.display}</h2>
                {featured.orgLabel && <p className="pa2-feature-org">{featured.orgLabel}</p>}
                <p className="pa2-feature-sum">{featured.summary}</p>
                <div className="pa2-feature-actions">
                  <OpenAction item={featured} hrefOf={hrefOf} onChoose={onChoose}
                              className="pa2-btn pa2-btn--primary pa2-btn--lg" />
                  {statusOf(featured) && <span className="pa2-pill pa2-pill--glass">{statusOf(featured)}</span>}
                </div>
              </div>
            </section>
          )}

          {all.length > 0 && (
            <section className="pa2-section" aria-labelledby="pa2-ws-title" ref={workspacesRef} tabIndex={-1}>
              <div className="pa2-section-head">
                <h2 id="pa2-ws-title">{q ? 'Results for “' + query.trim() + '”' : 'My Workspaces'}</h2>
                <div className="pa2-section-tools">
                  {cats.length > 2 && (
                    <div className="pa2-filters" role="group" aria-label="Filter workspaces">
                      {cats.map(c => (
                        <button type="button" key={c.key} aria-pressed={activeCat === c.key}
                                className={'pa2-chip' + (activeCat === c.key ? ' is-on' : '')}
                                onClick={() => setCategory(c.key)}>
                          {c.label}
                        </button>
                      ))}
                    </div>
                  )}
                  {all.length > 1 && (
                    <div className="pa2-viewtoggle" role="group" aria-label="Layout">
                      <button type="button" aria-pressed={view === 'grid'} onClick={() => choose('grid')}
                              aria-label="Grid view" title="Grid view"><Svg name="grid" size={17} /></button>
                      <button type="button" aria-pressed={view === 'list'} onClick={() => choose('list')}
                              aria-label="List view" title="List view"><Svg name="list" size={17} /></button>
                    </div>
                  )}
                </div>
              </div>
              {shown.length > 0 ? (
                <div className={'pa2-grid pa2-grid--' + view} role="list" aria-label="Your workspaces">
                  {shown.map(item => (
                    <div role="listitem" key={keyOf(item)}>
                      <WorkspaceTile item={item} layout={view} hrefOf={hrefOf} onChoose={onChoose}
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

          {previews.length > 0 && (
            <section className="pa2-previews" aria-labelledby="pa2-pv-title" ref={previewsRef} tabIndex={-1}>
              <div className="pa2-section-head">
                <div>
                  <h2 id="pa2-pv-title">Prospect Previews</h2>
                  <p className="pa2-sub">Demos built for prospects who are not customers yet.</p>
                </div>
              </div>
              {shownPreviews.length > 0 ? (
                <div className="pa2-pv-grid" role="list" aria-label="Prospect previews">
                  {shownPreviews.map(item => (
                    <div role="listitem" key={item.organization_id}>
                      <PreviewTile item={item} onChoose={onChoose} />
                    </div>
                  ))}
                </div>
              ) : <p className="pa2-none">No preview matches that search.</p>}
            </section>
          )}

          {nothing && (
            <div className="pa2-empty">
              <b>No workspaces have been assigned to this account.</b>
              <span>Ask your administrator to grant access to a program.</span>
            </div>
          )}

          <footer className="pa2-foot">Each program keeps its own data, workflow and permissions.</footer>
        </main>
      </div>
    </div>
  )
}

function Stat({ icon, tone, value, label, sub, health }) {
  return (
    <div className={'pa2-stat pa2-stat--' + tone}>
      <span className="pa2-stat-icon" aria-hidden="true"><Svg name={icon} size={22} /></span>
      <div className="pa2-stat-text">
        <span className="pa2-stat-label">{label}</span>
        {value !== null
          ? <b className="pa2-stat-value">{value}</b>
          : <b className={'pa2-stat-health pa2-stat-health--' + health}><i aria-hidden="true" />{sub}</b>}
        {value !== null && <small>{sub}</small>}
      </div>
    </div>
  )
}

/** The single real action: a link where the destination is a plain URL (no
 *  pop-up blocking), otherwise a button that runs openProgramItem. */
function OpenAction({ item, hrefOf, onChoose, className, stretch }) {
  const label = (item.actionLabel || 'Open Workspace')
  const aria = label + ': ' + item.display + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')
  const href = hrefOf(item)
  const cls = className + (stretch ? ' pa2-stretch' : '')
  const inner = <>{label}<Svg name="arrow" size={16} /></>
  return href
    ? <a className={cls} href={href} aria-label={aria}>{inner}</a>
    : <button type="button" className={cls} aria-label={aria} onClick={() => onChoose(item)}>{inner}</button>
}

function WorkspaceTile({ item, layout, hrefOf, onChoose, pinned, onTogglePin, programKey }) {
  const status = statusOf(item)
  return (
    <article className={'pa2-card pa2-card--' + layout} data-program-key={programKey}
             data-category={categoryOf(item)}>
      <div className="pa2-card-media">
        <img src={cardPhoto(photoKeyFor(item))} alt="" width="720" height="450" loading="lazy" decoding="async" />
        <span className="pa2-card-mark" aria-hidden="true">{item.symbol}</span>
      </div>
      <div className="pa2-card-body">
        <p className="pa2-card-kind">{item.kind}</p>
        <h3 className="pa2-card-name">{item.display}</h3>
        {item.orgLabel && item.orgLabel !== 'View only' && <p className="pa2-card-org">{item.orgLabel}</p>}
        <p className="pa2-card-sum">{item.summary}</p>
        <div className="pa2-card-foot">
          <OpenAction item={item} hrefOf={hrefOf} onChoose={onChoose} className="pa2-btn pa2-btn--primary" stretch />
          {status && <span className="pa2-pill">{status}</span>}
        </div>
      </div>
      {onTogglePin && (
        <button type="button" className={'pa2-pin' + (pinned ? ' is-pinned' : '')} aria-pressed={!!pinned}
                aria-label={(pinned ? 'Unpin ' : 'Pin ') + item.display + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')}
                title={pinned ? 'Unpin' : 'Pin to top (pinned first becomes featured)'}
                onClick={() => onTogglePin(item)}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
            <path d="M9 3h6l-1 6 4 4v2h-5v6l-1 1-1-1v-6H6v-2l4-4z" fill={pinned ? 'currentColor' : 'none'}
                  stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
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
  const facts = (item.facts || []).filter(Boolean)
  const aria = 'Open preview: ' + item.display
  return (
    <article className="pa2-pv" data-program-key={'preview:' + item.organization_id}>
      <img className="pa2-pv-img" src={cardPhoto(photoKeyFor(item))} alt="" width="720" height="450"
           loading="lazy" decoding="async" />
      <div className="pa2-pv-body">
        <p className="pa2-card-kind">{item.kind}</p>
        <h3 className="pa2-pv-name">{item.display}</h3>
        {item.orgLabel && <p className="pa2-card-org">{item.orgLabel}</p>}
        {facts.length > 0 && <p className="pa2-pv-facts">{facts.join(' · ')}</p>}
        {href ? (
          internal
            ? <a className="pa2-btn pa2-btn--soft pa2-stretch" href={href} aria-label={aria}>Open Preview <Svg name="arrow" size={15} /></a>
            : <a className="pa2-btn pa2-btn--soft pa2-stretch" href={href} target="_blank" rel="noopener noreferrer" aria-label={aria + ' (opens in a new tab)'}>Open Preview <Svg name="arrow" size={15} /></a>
        ) : (
          <button type="button" className="pa2-btn pa2-btn--soft pa2-stretch" aria-label={aria} onClick={() => onChoose(item)}>
            Open Preview <Svg name="arrow" size={15} />
          </button>
        )}
      </div>
    </article>
  )
}
