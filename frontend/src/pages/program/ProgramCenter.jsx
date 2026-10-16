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
import { api, fetchMyContexts, getCurrentUser, refreshCurrentUser } from '../../api/client'
import { readAuthority } from '../../auth/workspaceAuthority'
import { Icon, SearchBox, errText, Loading } from './sci/ui'
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
import { TopBar } from './command/Dashboard'
import { MobileNav, NAV, SideNav, TOOLS } from './command/SciNav'
import './ProgramCenter.css'
import './command/Dashboard.css'
import './command/Command.css'

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

/** Open Review on the first queue that has work in it, most urgent first. */
function firstOpenQueue(att) {
  const order = ['location_review', 'data_review', 'duplicate_review', 'on_hold']
  return order.find(k => (att?.[k] || 0) > 0) || 'all'
}


export default function ProgramCenter() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.some(t => t.key === params.get('tab')) ? params.get('tab') : 'dashboard'
  const locationId = params.get('location') || ''
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [batch, setBatch] = useState(null)
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
    setMany({ tab: key, queue: '', q: '', person: '', ...extra }, { push: true })
    window.scrollTo?.({ top: 0 })
  }, [setMany])
  const openPerson = useCallback(id => {
    if (id) setMany({ person: id }, { push: true })
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
  // The shared top bar's "Automated intake ON/OFF" - one light read.
  useEffect(() => { api.get('/program/daily-batch').then(setBatch).catch(() => {}) }, [])
  // Each screen starts at the top; Back keeps the browser's own restore.
  useEffect(() => { window.scrollTo?.(0, 0) }, [tab, personId])

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
    const label = [...NAV, ...TOOLS].find(t => t[0] === tab)?.[2] || 'Program Overview'
    document.title = `${label} · SCI · EvoSys Pro`
  }, [tab])
  useEffect(() => { setSearch(params.get('q') || '') }, [params])

  const submitSearch = e => {
    e.preventDefault()
    goTab('leads', { q: search.trim() })
  }

  // ONE SHELL: the same side menu, top bar and phone tabs as SCI Live. The
  // older sidebar and navy header are gone; their search, location filter,
  // response bell and campaign status live in the bar above each screen.
  const badges = data ? {
    responses: data.attention?.hot_responses || 0,
    review: (data.attention?.data_review || 0) + (data.attention?.location_review || 0) + (data.attention?.duplicate_review || 0),
  } : {}
  const shell = body => (
    <div className="sl-shell sl-pc">
      <SideNav badges={badges} onSame={() => window.scrollTo?.({ top: 0, behavior: 'smooth' })} />
      <div className="sl-main">
        <TopBar batch={batch} />
        <div className="sci-app sci-embed">{body}</div>
        <MobileNav badges={badges} />
      </div>
    </div>
  )

  if (err && !data) {
    return shell(
      <div className="sci-work">
        <div className="sci-alert" role="alert">{err}</div>
        <button type="button" className="sci-btn" onClick={load}><Icon name="refresh" />Try again</button>{' '}
        {!programOnly && <button type="button" className="sci-btn" onClick={() => navigate('/')}><Icon name="back" />Back to main menu</button>}
      </div>
    )
  }
  if (!data) return shell(<Loading label="Loading…" />)

  const prog = data.program
  const att = data.attention
  const realLocations = data.locations.filter(l => !l.is_review_bucket)
  const bucket = data.locations.find(l => l.is_review_bucket)
  const sendingOn = (data.automation?.active_campaign_families || 0) > 0

  return shell(<>
    <a className="sci-skip" href="#sci-main">Skip to content</a>
    <div className="sci-embed-bar">
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
      <span className={`sci-chip ${sendingOn ? 'ok' : 'warn'}`}
        title={sendingOn ? `${data.automation.active_campaign_families} campaign(s) switched on` : 'All campaigns off — nothing sends automatically'}>
        {sendingOn ? `${data.automation.active_campaign_families} campaign(s) on` : 'All campaigns off'}</span>
      <button type="button" className="sci-iconbtn" onClick={() => goTab('responses')}
        aria-label={`${att.hot_responses || 0} new HOT responses. Open AI Assistant`} title="AI Assistant (responses)">
        <Icon name="bell" />
        {att.hot_responses > 0 && <span className="sci-dot" aria-hidden="true">{att.hot_responses}</span>}
      </button>
    </div>
    <main className="sci-work" id="sci-main" tabIndex={-1}>
          {err && <div className="sci-alert" role="alert">{err}</div>}
          {personId && <Person leadId={personId} isManager={isManager} onBack={() => (window.history.state?.idx > 0 ? navigate(-1) : setMany({ person: '', tab }))} />}
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
  </>)
}
