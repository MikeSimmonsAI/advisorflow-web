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
 * Nine screens, one shell (2026-10 redesign). Deep links are unchanged:
 * /program?tab=responses|review|locations|campaigns|assets|launch|health|settings
 * plus ?location=, ?queue= and ?q=. Every number is read from the server; every
 * send is decided by the server's gates, never by this page.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import { readAuthority } from '../../auth/workspaceAuthority'
import { Icon, errText, initials, Loading } from './sci/ui'
import Dashboard from './sci/Dashboard'
import Responses from './sci/Responses'
import Review from './sci/Review'
import Locations from './sci/Locations'
import Campaigns from './sci/Campaigns'
import Assets from './sci/Assets'
import Launch from './sci/Launch'
import Health from './sci/Health'
import Settings from './sci/Settings'
import './ProgramCenter.css'

const TABS = [
  { key: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
  { key: 'responses', label: 'Responses', icon: 'responses' },
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
  const [menuOpen, setMenuOpen] = useState(false)
  const [search, setSearch] = useState(params.get('q') || '')
  const isManager = readAuthority().isWorkspaceManager
  const navigate = useNavigate()
  const user = getCurrentUser() || {}
  const userName = user.full_name || [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email || 'Signed in'

  /** Change several query parameters at once; empty values are removed. */
  const setMany = useCallback(changes => {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      Object.entries(changes).forEach(([k, v]) => { if (v) next.set(k, v); else next.delete(k) })
      return next
    }, { replace: true })
  }, [setParams])
  const setParam = useCallback((key, value) => setMany({ [key]: value }), [setMany])
  const goTab = useCallback((key, extra = {}) => {
    setMany({ tab: key === 'dashboard' ? '' : key, ...extra })
    setMenuOpen(false)
    window.scrollTo?.({ top: 0 })
  }, [setMany])

  const load = useCallback(() => {
    let alive = true
    setErr('')
    api.get(`/program/dashboard${locationId ? `?location_id=${encodeURIComponent(locationId)}` : ''}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) { setErr(errText(e)); setData(null) } })
    return () => { alive = false }
  }, [locationId])
  useEffect(() => load(), [load])

  useEffect(() => {
    const label = TABS.find(t => t.key === tab)?.label || 'Dashboard'
    document.title = `${label} · Family Service Center`
  }, [tab])

  const submitSearch = e => {
    e.preventDefault()
    goTab('review', { q: search.trim(), queue: 'all' })
  }

  if (err && !data) {
    return (
      <div className="sci-app" style={{ display: 'block' }}>
        <div className="sci-work"><div className="sci-alert" role="alert">{err}</div></div>
      </div>
    )
  }
  if (!data) return <div className="sci-app" style={{ display: 'block' }}><Loading /></div>

  const prog = data.program
  const att = data.attention
  const realLocations = data.locations.filter(l => !l.is_review_bucket)
  const reviewCount = (att.data_review || 0) + (att.location_review || 0) + (att.duplicate_review || 0)
  const counts = { responses: att.hot_responses || 0, review: reviewCount }
  const progName = prog.name || 'Service Corporation International'

  return (
    <div className={`sci-app${menuOpen ? ' sci-menu-open' : ''}`}>
      <aside className="sci-side" aria-label="Family Service Center navigation">
        <div className="sci-brand">
          <span className="sci-brand-mark" aria-hidden="true">SCI</span>
          <div>
            <div className="sci-brand-name">{progName}</div>
            <div className="sci-brand-sub">Family Service Center</div>
          </div>
        </div>
        <div className="sci-navlabel">Workspace</div>
        <nav className="sci-nav">
          {TABS.map(t => (
            <button key={t.key} type="button" aria-current={tab === t.key ? 'page' : undefined} onClick={() => goTab(t.key)}>
              <Icon name={t.icon} />
              <span>{t.label}</span>
              {counts[t.key] > 0 && (
                <span className={`sci-count${t.key === 'review' ? ' warn' : ''}`}
                  aria-label={t.key === 'review' ? `${counts[t.key]} records to review` : `${counts[t.key]} HOT responses`}>
                  {counts[t.key]}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="sci-side-foot">
          <button type="button" onClick={() => navigate('/')}>← Back to main menu</button>
          <div className="sci-side-state" style={{ '--dot': data.automation?.active_campaign_families ? '#67c5ae' : '#d1a76c' }}>
            {data.automation?.active_campaign_families
              ? `${data.automation.active_campaign_families} campaign(s) on`
              : 'All campaigns off · nothing sends'}
          </div>
        </div>
      </aside>
      {menuOpen && <button type="button" className="sci-scrim" aria-label="Close menu" onClick={() => setMenuOpen(false)} />}

      <div className="sci-main">
        <header className="sci-top">
          <button type="button" className="sci-iconbtn sci-menubtn" aria-label="Open menu" aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}><Icon name="menu" /></button>
          <div className="sci-top-title">
            Family Service Center
            <small>{progName}{data.selected_location ? ` · ${data.selected_location.official_name}` : ''}</small>
          </div>
          <div className="sci-top-spacer" />
          <form className="sci-search" role="search" onSubmit={submitSearch}>
            <Icon name="search" />
            <label htmlFor="sci-search" className="sci-sr">Search contacts</label>
            <input id="sci-search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search contacts, Lead IDs…" />
          </form>
          <label htmlFor="sci-location" className="sci-sr">Location</label>
          <select id="sci-location" className="sci-topselect" value={locationId} onChange={e => setParam('location', e.target.value)}>
            <option value="">All locations ({realLocations.length})</option>
            {data.locations.map(l => (
              <option key={l.location_id} value={l.location_id}>{l.name}</option>
            ))}
          </select>
          <button type="button" className="sci-iconbtn" onClick={() => goTab('responses')}
            aria-label={`${att.hot_responses || 0} HOT responses. Open Responses`}>
            <Icon name="bell" />
            {att.hot_responses > 0 && <span className="sci-dot" aria-hidden="true">{att.hot_responses}</span>}
          </button>
          <div className="sci-profile">
            <span className="sci-avatar" aria-hidden="true">{initials(userName)}</span>
            <span className="sci-profile-text">{userName}<small>{isManager ? 'Workspace manager' : 'Team member'}</small></span>
          </div>
        </header>

        <main className="sci-work" id="sci-main">
          {err && <div className="sci-alert" role="alert">{err}</div>}
          {tab === 'dashboard' && <Dashboard data={data} goTab={goTab} setParam={setParam} />}
          {tab === 'responses' && <Responses locationId={locationId} onChange={load} navigate={navigate} />}
          {tab === 'review' && <Review locationId={locationId} locations={data.locations} isManager={isManager}
            attention={att} initialQueue={params.get('queue') || firstOpenQueue(att)} initialSearch={params.get('q') || ''} onChange={load} />}
          {tab === 'locations' && <Locations isManager={isManager} selected={locationId} onChange={load} />}
          {tab === 'campaigns' && <Campaigns isManager={isManager} locationId={locationId} locations={data.locations} />}
          {tab === 'assets' && <Assets isManager={isManager} locations={data.locations} />}
          {tab === 'launch' && <Launch isManager={isManager} onChange={load} goTab={goTab} />}
          {tab === 'health' && <Health />}
          {tab === 'settings' && <Settings isManager={isManager} program={prog} readiness={data.readiness}
            locations={realLocations} onChange={load} />}
        </main>
      </div>
    </div>
  )
}
