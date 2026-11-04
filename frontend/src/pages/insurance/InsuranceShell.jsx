/**
 * EvoSys Pro — INSURANCE RETENTION WORKSPACE (/program/insurance/*), Issue #499.
 *
 * The daily operating screen for an established agency's EXISTING clients:
 * renewals, reviews, replies, assignments. Built from the Vincent Greene Agency
 * mockup, but every name, count and setting comes from the server
 * (/agency/retention/*) — nothing on these screens is hard-coded to one agency.
 *
 * The server is the authority on scope: an owner sees the agency, a location
 * manager their locations, an agent their own work plus the shared queue.
 * "Preview as" (demo workspaces, owners only) asks the server for that
 * person's real scope and is read-only.
 */
import { createContext, Suspense, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { NavLink, Outlet, useLocation, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import ProgramSwitchLink from '../../components/ProgramSwitchLink'
import { ClientDrawer } from './InsurancePages'
import { Icon, errText, initials } from './insuranceUi'
import './insurance.css'

export const BASE = '/program/insurance'
const Ctx = createContext(null)
export const useIns = () => useContext(Ctx)

const NAV = [
  { to: BASE, label: 'Today', icon: 'home', end: true },
  { to: `${BASE}/renewals`, label: 'Renewals', icon: 'calendar' },
  { to: `${BASE}/clients`, label: 'Clients & Policies', icon: 'users' },
  { to: `${BASE}/conversations`, label: 'Conversations', icon: 'chat', count: 'replies' },
  { to: `${BASE}/appointments`, label: 'Appointments', icon: 'clock' },
  { to: `${BASE}/team`, label: 'Team & Locations', icon: 'pin' },
]
const MORE = [
  { to: `${BASE}/reports`, label: 'Reports', icon: 'chart' },
  { to: `${BASE}/automation`, label: 'Automation', icon: 'spark', manager: true },
  { to: `${BASE}/import`, label: 'Imports', icon: 'upload', manager: true },
  { to: `${BASE}/settings`, label: 'Settings', icon: 'settings' },
]

export default function InsuranceShell() {
  const [ws, setWs] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)
  const [replies, setReplies] = useState(0)
  const [params, setParams] = useSearchParams()
  const loc = useLocation()
  const previewAs = params.get('as') || ''
  const locationId = params.get('loc') || ''
  const agentId = params.get('agent') || ''
  const clientId = params.get('client') || ''
  const [tick, setTick] = useState(0)

  const withPreview = useCallback((path) => {
    if (!previewAs) return path
    return path + (path.includes('?') ? '&' : '?') + 'preview_as=' + encodeURIComponent(previewAs)
  }, [previewAs])

  const load = useCallback(async () => {
    try { setWs(await api.get(withPreview('/agency/retention/workspace'))); setError(null) } catch (e) { setError(e) }
  }, [withPreview])
  useEffect(() => { load() }, [load])
  useEffect(() => { setOpen(false) }, [loc.pathname])
  useEffect(() => {
    if (!ws) return
    api.get(withPreview('/agency/retention/conversations?open_only=true'))
      .then(d => setReplies(d.items.reduce((n, t) => n + (t.unhandled ? 1 : 0), 0))).catch(() => {})
  }, [ws, tick, withPreview])

  const setParam = (k, v) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v); else next.delete(k)
    setParams(next, { replace: k !== 'client' })
  }
  const value = useMemo(() => ws && ({
    ws, viewer: ws.viewer, settings: ws.settings, locations: ws.locations, agents: ws.agents,
    isDemo: ws.settings.is_demo, canManage: ['owner', 'manager'].includes(ws.viewer.kind) && !ws.viewer.preview,
    readOnly: !!ws.viewer.preview,
    locationId, agentId, setLocation: v => setParam('loc', v), setAgent: v => setParam('agent', v),
    openClient: id => setParam('client', id), closeClient: () => setParam('client', ''),
    q: (path, extra = {}) => {
      const sp = new URLSearchParams()
      Object.entries(extra).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') sp.set(k, v) })
      const s = sp.toString()
      return withPreview(path + (s ? (path.includes('?') ? '&' : '?') + s : ''))
    },
    tick, refresh: () => setTick(t => t + 1), reloadWorkspace: load,
  }), [ws, locationId, agentId, previewAs, tick, withPreview, load]) // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    const denied = [402, 403, 404, 409].includes(error.status)
    return (
      <div className="ins ins-gate">
        <div className="ins-card ins-gate__card">
          <h1>{denied ? 'This workspace doesn’t have the insurance retention workspace' : 'This page couldn’t load'}</h1>
          <p className="ins-muted">{denied ? (error.message || 'Switch to an insurance agency workspace to continue.') : errText(error)}</p>
          <div className="ins-row">
            <a className="ins-btn" href="/workspaces">Program Access</a>
            {!denied && <button className="ins-btn ins-btn--primary" onClick={load}>Try again</button>}
          </div>
        </div>
      </div>
    )
  }
  if (!value) return <div className="ins ins-loading" role="status">Opening the agency workspace…</div>

  const s = ws.settings
  const crumb = [...NAV, ...MORE].find(n => (n.end ? loc.pathname === n.to : loc.pathname.startsWith(n.to)))?.label || 'Today'
  const more = MORE.filter(m => !m.manager || value.canManage)
  return (
    <Ctx.Provider value={value}>
      <div className="ins ins-app">
        <a href="#ins-main" className="ins-skip">Skip to content</a>
        {open && <div className="ins-scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
        <aside className={`ins-side ${open ? 'open' : ''}`} aria-label="Insurance workspace navigation">
          <div className="ins-brand">
            <span className="ins-brand__mark" aria-hidden="true">{initials(s.display_name)}</span>
            <div><b>{s.display_name}</b><small>Insurance Workspace</small></div>
          </div>
          <nav className="ins-nav">
            {NAV.map(it => (
              <NavLink key={it.to} to={{ pathname: it.to, search: keepSearch(params) }} end={it.end}>
                <Icon name={it.icon} /><span>{it.label}</span>
                {it.count === 'replies' && replies > 0 && <span className="ins-nav__count" aria-label={`${replies} conversations need a reply`}>{replies}</span>}
              </NavLink>
            ))}
          </nav>
          <nav className="ins-nav ins-nav--secondary" aria-label="More">
            {more.map(it => (
              <NavLink key={it.to} to={{ pathname: it.to, search: keepSearch(params) }}><Icon name={it.icon} /><span>{it.label}</span></NavLink>
            ))}
          </nav>
          <div className="ins-powered">Powered by <b>EvoSys Pro</b></div>
        </aside>
        <div className="ins-main">
          <header className="ins-top">
            <button className="ins-btn ins-btn--icon ins-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
            <nav className="ins-crumbs" aria-label="Breadcrumb"><a href="/workspaces">Insurance</a><span aria-hidden="true">/</span><b>{crumb}</b></nav>
            <div className="ins-top__right">
              {value.viewer.kind !== 'agent' && (
                <label className="ins-select">
                  <Icon name="pin" />
                  <span className="sr-only">Location</span>
                  <select value={locationId} onChange={e => value.setLocation(e.target.value)}>
                    <option value="">{value.viewer.kind === 'owner' ? 'All locations' : 'My locations'}</option>
                    {ws.locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select>
                </label>
              )}
              <span className={`ins-mode ${s.is_demo ? 'ins-mode--demo' : 'ins-mode--live'}`} title={s.is_demo ? 'Synthetic customer data. Simulated delivery — no messages are sent.' : `Data source: ${s.data_mode}`}>
                {s.is_demo ? 'Demo scenario' : `Live · ${s.data_mode.replace('_', ' ')}`}
              </span>
              <ProgramSwitchLink />
              <span className="ins-me" title={value.viewer.preview ? `Previewing as ${value.viewer.name}` : value.viewer.name}>
                <span className="ins-avatar" aria-hidden="true">{initials(value.viewer.actor_name)}</span>
                <span className="ins-me__text"><b>{value.viewer.actor_name}</b><small>{roleLabel(value.viewer)}</small></span>
              </span>
            </div>
          </header>
          {value.viewer.preview && (
            <div className="ins-preview" role="status">
              Previewing as <b>{value.viewer.name}</b> ({value.viewer.kind}) — read-only. The server is showing exactly what they can see.
              <button className="ins-btn ins-btn--sm" onClick={() => setParam('as', '')}>Return to my view</button>
            </div>
          )}
          {!value.viewer.preview && ws.preview_options?.length > 0 && (
            <PreviewPicker options={ws.preview_options} onPick={id => setParam('as', id)} />
          )}
          <main id="ins-main" className="ins-content" tabIndex={-1}>
            <Suspense fallback={<div className="ins-loading">Loading…</div>}><Outlet /></Suspense>
          </main>
          {s.is_demo && <footer className="ins-foot">Synthetic customer data · Simulated delivery — no live messages sent. Locations other than Mesquite, staff and customers are illustrative.</footer>}
        </div>
        {clientId && <ClientDrawer leadId={clientId} onClose={value.closeClient} />}
      </div>
    </Ctx.Provider>
  )
}

function keepSearch(params) {
  const sp = new URLSearchParams()
  ;['loc', 'as'].forEach(k => { if (params.get(k)) sp.set(k, params.get(k)) })
  const s = sp.toString()
  return s ? '?' + s : ''
}

function roleLabel(v) {
  const k = { owner: 'Agency owner / admin', manager: 'Location manager', agent: 'Agent' }[v.kind] || v.kind
  return v.preview ? `Previewing ${v.kind}` : k
}

function PreviewPicker({ options, onPick }) {
  const [val, setVal] = useState('')
  return (
    <div className="ins-previewbar">
      <label>
        <span>Demo: see the workspace as</span>
        <select value={val} onChange={e => setVal(e.target.value)}>
          <option value="">Choose a staff member…</option>
          {options.map(o => <option key={o.user_id} value={o.user_id}>{o.name} — {o.title}</option>)}
        </select>
      </label>
      <button className="ins-btn ins-btn--sm" disabled={!val} onClick={() => onPick(val)}>Preview</button>
    </div>
  )
}
