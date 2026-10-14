/**
 * FAMILY SERVICE CENTER - a location outreach program's workspace (SCI).
 *
 *   GET  /program/dashboard?location_id=   metrics, attention, pipeline, ...
 *   GET  /program/responses                the response queue (HOT first)
 *   POST /program/responses/{id}/mark      opened / responded / active / closed
 *   GET  /program/records?queue=           Data / Location / Duplicate review
 *   GET  /program/locations, PATCH         location profiles (what families see)
 *   GET  /program/campaigns, PATCH, /preview  one family rendered per location
 *   GET  /program/assets, POST, /active    logos, facility images, approved flyers
 *   PATCH /program/settings, POST /program/import (dry-run / stage only)
 *
 * Ten screens, one shell (2026-10 EvoSys Pro command-center redesign). Deep
 * links are unchanged: /program?tab=send|responses|review|locations|campaigns|
 * assets|launch|health|settings plus ?location=, ?queue= and ?q=. Every number
 * is read from the server; every send is decided by the server's gates, never
 * by this page.
 *
 * IDENTITY: the top-right chip names the SIGNED-IN PERSON (refreshed from
 * /settings/profile) and the SCI workspace from this program's own server
 * record - never a cached brand name from another workspace (the old chip
 * could show "EvoSys Wholesale" inside SCI).
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, fetchMyContexts, getCurrentUser, logout, refreshCurrentUser } from '../../api/client'
import { readAuthority } from '../../auth/workspaceAuthority'
import { Icon, LocationMark, SearchBox, errText, initials, Loading } from './sci/ui'
import Dashboard from './sci/Dashboard'
import Responses from './sci/Responses'
import Review from './sci/Review'
import Locations from './sci/Locations'
import Campaigns from './sci/Campaigns'
import Assets from './sci/Assets'
import Launch from './sci/Launch'
import Health from './sci/Health'
import Settings from './sci/Settings'
import Send from './sci/Send'
import Leads from './sci/Leads'
import Appointments from './sci/Appointments'
import Outbox from './sci/Outbox'
import Person from './sci/Person'
import './ProgramCenter.css'

const TABS = [
  { key: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
  { key: 'leads', label: 'Leads', icon: 'users' },
  { key: 'send', label: 'Choose & Send', icon: 'send' },
  { key: 'outbox', label: 'Outbox', icon: 'mail' },
  { key: 'responses', label: 'Responses', icon: 'responses' },
  { key: 'appointments', label: 'Appointments', icon: 'calendar' },
  { key: 'review', label: 'Review', icon: 'review' },
  { key: 'locations', label: 'Locations', icon: 'locations' },
  { key: 'campaigns', label: 'Campaigns', icon: 'campaigns' },
  { key: 'assets', label: 'Assets & Flyers', icon: 'assets' },
  { key: 'launch', label: 'Launch Readiness', icon: 'launch' },
  { key: 'health', label: 'Health', icon: 'health' },
  { key: 'settings', label: 'Settings', icon: 'settings' },
]
const COLLAPSE_KEY = 'sci_nav_collapsed'

/** Open Review on the first queue that has work in it, most urgent first. */
function firstOpenQueue(att) {
  const order = ['location_review', 'data_review', 'duplicate_review', 'on_hold']
  return order.find(k => (att?.[k] || 0) > 0) || 'all'
}

function readCollapsed() {
  try { return localStorage.getItem(COLLAPSE_KEY) === '1' } catch { return false }
}

export default function ProgramCenter() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.some(t => t.key === params.get('tab')) ? params.get('tab') : 'dashboard'
  const locationId = params.get('location') || ''
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [search, setSearch] = useState(params.get('q') || '')
  const [me, setMe] = useState(() => getCurrentUser() || {})
  // The server's answer wins: a workspace admin by membership (Kerry) is a manager
  // even when the browser's stored role says otherwise.
  const isManager = data?.viewer_is_manager ?? readAuthority().isWorkspaceManager
  const navigate = useNavigate()
  const personId = params.get('person') || ''
  // A login that only has program workspaces has no "main menu" to go back
  // to - the Family Service Center IS its whole app.
  const [programOnly, setProgramOnly] = useState(false)
  useEffect(() => {
    let alive = true
    fetchMyContexts().then(c => { if (alive) setProgramOnly(!!c?.program_only) }).catch(() => {})
    return () => { alive = false }
  }, [])

  // The stored user can be stale (another workspace, an older name). Ask the
  // server who is signed in; the stored copy is only the first paint.
  useEffect(() => {
    let alive = true
    refreshCurrentUser().then(p => { if (alive && p) setMe(m => ({ ...m, ...p })) })
    return () => { alive = false }
  }, [])
  const userName = me.full_name || [me.first_name, me.last_name].filter(Boolean).join(' ') || me.email || 'Signed in'

  /** Change several query parameters at once; empty values are removed. */
  const setMany = useCallback((changes, { push = false } = {}) => {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      Object.entries(changes).forEach(([k, v]) => { if (v) next.set(k, v); else next.delete(k) })
      return next
    }, { replace: !push })
  }, [setParams])
  const setParam = useCallback((key, value) => setMany({ [key]: value }), [setMany])
  const goTab = useCallback((key, extra = {}) => {
    // Tab changes are real history entries so Back/Forward move between screens.
    setMany({ tab: key === 'dashboard' ? '' : key, queue: '', q: '', person: '', ...extra }, { push: true })
    setMenuOpen(false)
    window.scrollTo?.({ top: 0 })
  }, [setMany])
  const openPerson = useCallback(id => {
    if (id) { setMany({ person: id }, { push: true }); setMenuOpen(false) }
  }, [setMany])

  const load = useCallback(() => {
    let alive = true
    setErr('')
    api.get(`/program/dashboard${locationId ? `?location_id=${encodeURIComponent(locationId)}` : ''}`)
      .then(d => { if (alive) { setData(d); try { sessionStorage.removeItem('sci_pc_entry') } catch { /* private mode */ } } })
      .catch(e => {
        if (!alive) return
        // Standing in a workspace with no program (e.g. EvoSys Wholesale) while
        // holding SCI: go in through the SCI front door instead of an error.
        if (/no outreach program/i.test(errText(e)) && !sessionStorage.getItem('sci_pc_entry')) {
          try { sessionStorage.setItem('sci_pc_entry', '1') } catch { /* private mode */ }
          window.location.replace('/sci')
          return
        }
        setErr(errText(e)); setData(null)
      })
    return () => { alive = false }
  }, [locationId])
  useEffect(() => load(), [load])

  // Keep the Dashboard current on its own: quietly reload every 60 seconds
  // while it is open and the browser tab is visible. A failed background
  // reload keeps the last good numbers instead of blanking the screen.
  useEffect(() => {
    if (tab !== 'dashboard' || personId) return undefined
    const t = setInterval(() => {
      if (document.visibilityState !== 'visible') return
      api.get(`/program/dashboard${locationId ? `?location_id=${encodeURIComponent(locationId)}` : ''}`)
        .then(d => setData(d))
        .catch(() => {})
    }, 60000)
    return () => clearInterval(t)
  }, [tab, personId, locationId])

  useEffect(() => {
    const label = TABS.find(t => t.key === tab)?.label || 'Dashboard'
    document.title = `${label} · Family Service Center · EvoSys Pro`
  }, [tab])
  useEffect(() => { setSearch(params.get('q') || '') }, [params])

  const toggleCollapsed = () => setCollapsed(v => {
    const n = !v
    try { localStorage.setItem(COLLAPSE_KEY, n ? '1' : '0') } catch { /* storage blocked */ }
    return n
  })

  const submitSearch = e => {
    e.preventDefault()
    goTab('leads', { q: search.trim() })
  }

  if (err && !data) {
    return (
      <div className="sci-app" style={{ display: 'block' }}>
        <div className="sci-work">
          <div className="sci-alert" role="alert">{err}</div>
          <button type="button" className="sci-btn" onClick={load}><Icon name="refresh" />Try again</button>{' '}
          {!programOnly && <button type="button" className="sci-btn" onClick={() => navigate('/')}><Icon name="back" />Back to main menu</button>}
        </div>
      </div>
    )
  }
  if (!data) return <div className="sci-app" style={{ display: 'block' }}><Loading label="Loading the Family Service Center…" /></div>

  const prog = data.program
  const att = data.attention
  const realLocations = data.locations.filter(l => !l.is_review_bucket)
  const bucket = data.locations.find(l => l.is_review_bucket)
  const reviewCount = (att.data_review || 0) + (att.location_review || 0) + (att.duplicate_review || 0)
  const counts = { responses: att.hot_responses || 0, review: reviewCount }
  const sendingOn = (data.automation?.active_campaign_families || 0) > 0
  const selLoc = realLocations.find(l => l.location_id === locationId)
  const workspaceLabel = 'Family Service Center'

  return (
    <div className={`sci-app${menuOpen ? ' sci-menu-open' : ''}${collapsed ? ' sci-collapsed' : ''}`}>
      <a className="sci-skip" href="#sci-main">Skip to content</a>
      <aside className="sci-side" aria-label="Family Service Center navigation">
        <div className="sci-brand">
          <div className="sci-brand-word">EvoSys<span>Pro</span></div>
          <div className="sci-brand-tag">People · Outreach · Appointments · Results</div>
          <div className="sci-brand-mini" aria-hidden="true">E<span>P</span></div>
        </div>
        <nav className="sci-nav" aria-label="Workspace pages">
          {TABS.map(t => (
            <button key={t.key} type="button" aria-current={tab === t.key ? 'page' : undefined} onClick={() => goTab(t.key)}
              title={collapsed ? t.label : undefined}>
              <Icon name={t.icon} />
              <span>{t.label}</span>
              {counts[t.key] > 0 && (
                <em className={`sci-count${t.key === 'responses' ? ' hot' : ''}`} style={{ fontStyle: 'normal' }}
                  aria-label={t.key === 'review' ? `${counts[t.key]} records to review` : `${counts[t.key]} new HOT responses`}>
                  {counts[t.key]}
                </em>
              )}
            </button>
          ))}
        </nav>
        <div className="sci-side-foot">
          <button type="button" className="sci-collapse-btn" onClick={toggleCollapsed} aria-pressed={collapsed}
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}>
            <Icon name={collapsed ? 'expand' : 'collapse'} /><span>Collapse menu</span>
          </button>
          {programOnly ? (
            <button type="button" className="sci-side-link" onClick={async () => { await logout(); window.location.href = '/login' }} title="Sign out">
              <Icon name="lock" /><span>Sign out</span>
            </button>
          ) : (
            <button type="button" className="sci-side-link" onClick={() => navigate('/')} title="Back to main menu">
              <Icon name="back" /><span>Back to main menu</span>
            </button>
          )}
          <div className="sci-scicard" style={{ '--dot': sendingOn ? '#3fc28f' : '#e9a54a' }}
            title={sendingOn ? `${data.automation.active_campaign_families} campaign(s) switched on` : 'All campaigns off — nothing sends automatically'}>
            {selLoc ? <LocationMark name={selLoc.name} size={34} /> : <b aria-hidden="true"><Icon name="pin" size={20} /></b>}
            <span>
              {selLoc ? selLoc.name : 'Family Service Center'}
              <small><Icon name="pin" size={11} />{selLoc ? 'Selected location' : `${realLocations.length} locations`}</small>
              <small><i />{sendingOn ? `${data.automation.active_campaign_families} campaign(s) on` : 'All campaigns off'}</small>
            </span>
          </div>
        </div>
      </aside>
      {menuOpen && <button type="button" className="sci-scrim" aria-label="Close menu" onClick={() => setMenuOpen(false)} />}

      <div className="sci-main">
        <header className="sci-top">
          <button type="button" className="sci-iconbtn sci-menubtn" aria-label="Open menu" aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}><Icon name="menu" /></button>
          <div className="sci-top-logo">EvoSys <b>PRO</b></div>
          <div className="sci-top-spacer" />
          <form role="search" onSubmit={submitSearch}>
            <SearchBox id="sci-search" value={search} onChange={setSearch} label="Search contacts or Lead IDs"
              placeholder="Search contacts, Lead IDs, phone or email…" />
          </form>
          <div className="sci-topselect-wrap">
            <Icon name="pin" />
            <label htmlFor="sci-location" className="sci-sr">Location filter</label>
            <select id="sci-location" className="sci-topselect" value={locationId} onChange={e => setParam('location', e.target.value)}>
              <option value="">All locations ({realLocations.length})</option>
              {realLocations.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
              {bucket && <option value={bucket.location_id}>{bucket.name} — not a facility</option>}
            </select>
          </div>
          <button type="button" className="sci-iconbtn" onClick={() => goTab('responses')}
            aria-label={`${att.hot_responses || 0} new HOT responses. Open Responses`} title="Responses">
            <Icon name="bell" />
            {att.hot_responses > 0 && <span className="sci-dot" aria-hidden="true">{att.hot_responses}</span>}
          </button>
          <div className="sci-profile" title={me.email ? `Signed in as ${me.email}` : undefined}>
            <span className="sci-avatar" aria-hidden="true">{initials(userName)}</span>
            <span className="sci-profile-text">
              {userName}
              <small>{workspaceLabel} · {isManager ? 'Workspace manager' : 'Team member'}</small>
            </span>
          </div>
        </header>

        <main className="sci-work" id="sci-main" tabIndex={-1}>
          <form className="sci-mobile-search" role="search" onSubmit={submitSearch}>
            <SearchBox id="sci-search-m" value={search} onChange={setSearch} label="Search contacts or Lead IDs" placeholder="Search contacts or Lead IDs…" />
          </form>
          {err && <div className="sci-alert" role="alert">{err}</div>}
          {personId && <Person leadId={personId} isManager={isManager} onBack={() => (window.history.length > 1 ? navigate(-1) : setMany({ person: '' }))} />}
          {!personId && tab === 'leads' && <Leads locations={data.locations} locationId={locationId} openPerson={openPerson} initialQuery={params.get('q') || ''} />}
          {!personId && tab === 'appointments' && <Appointments locationId={locationId} openPerson={openPerson} />}
          {!personId && tab === 'outbox' && <Outbox openPerson={openPerson} />}
          {!personId && tab === 'dashboard' && <Dashboard data={data} goTab={goTab} setParam={setParam} isManager={isManager} />}
          {!personId && tab === 'responses' && <Responses locationId={locationId} onChange={load} navigate={navigate} isManager={isManager} />}
          {!personId && tab === 'review' && <Review locationId={locationId} locations={data.locations} isManager={isManager}
            attention={att} initialQueue={params.get('queue') || firstOpenQueue(att)} initialSearch={params.get('q') || ''}
            onChange={load} goTab={goTab} navigate={navigate} />}
          {!personId && tab === 'locations' && <Locations isManager={isManager} selected={locationId} onChange={load} setParam={setParam}
            performance={data.location_performance} goTab={goTab} />}
          {!personId && tab === 'campaigns' && <Campaigns isManager={isManager} locationId={locationId} locations={data.locations}
            performance={data.campaign_performance} goTab={goTab} />}
          {!personId && tab === 'assets' && <Assets isManager={isManager} locations={data.locations} />}
          {!personId && tab === 'launch' && <Launch isManager={isManager} onChange={load} goTab={goTab} />}
          {!personId && tab === 'send' && <Send locations={data.locations} isManager={isManager} locationId={locationId} goTab={goTab} automation={data.automation} openPerson={openPerson} />}
          {!personId && tab === 'health' && <Health goTab={goTab} />}
          {!personId && tab === 'settings' && <Settings isManager={isManager} program={prog} readiness={data.readiness}
            locations={realLocations} onChange={load} goTab={goTab} automation={data.automation} />}
        </main>
      </div>
    </div>
  )
}
