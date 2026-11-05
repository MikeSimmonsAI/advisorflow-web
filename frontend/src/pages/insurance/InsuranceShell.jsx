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
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, logout } from '../../api/client'
import ProgramSwitchLink from '../../components/ProgramSwitchLink'
import { ClientDrawer } from './InsurancePages'
import { Icon, errText, initials } from './insuranceUi'
import './insurance.css'

export const BASE = '/program/insurance'
const Ctx = createContext(null)
export const useIns = () => useContext(Ctx)

// Navigation comes from the SERVER (/agency/retention/me -> nav): only pages
// this person's effective access can use. These are just icons and order.
const ICONS = { today: 'home', renewals: 'calendar', clients: 'users', conversations: 'chat', appointments: 'clock',
  team: 'users', reports: 'chart', automation: 'spark', import: 'upload', settings: 'settings', me: 'user' }
const ROLE_LABEL = { admin: 'Agency administrator', manager: 'Location manager', producer: 'Producer',
  retention: 'Retention specialist', scheduler: 'Appointment setter', analyst: 'Read-only analyst' }

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

  const [me, setMe] = useState(null)
  const [menu, setMenu] = useState(false)
  const load = useCallback(async () => {
    try {
      const [w, m] = await Promise.all([api.get(withPreview('/agency/retention/workspace')),
        api.get(withPreview('/agency/retention/me'))])
      setWs(w); setMe(m); setError(null)
    } catch (e) { setError(e) }
  }, [withPreview])
  useEffect(() => { load() }, [load])
  useEffect(() => { setOpen(false) }, [loc.pathname])
  // A member's chosen landing page (My settings), once per visit to the shell root.
  const go = useNavigate()
  const [landed, setLanded] = useState(false)
  useEffect(() => {
    if (!me || landed) return
    setLanded(true)
    const key = me.prefs?.landing
    const hit = key && (me.nav || []).find(n => n.key === key)
    if (hit && loc.pathname === BASE) go(BASE + hit.path + loc.search, { replace: true })
  }, [me]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!ws || !me?.permissions?.conversations) return
    api.get(withPreview('/agency/retention/conversations?open_only=true'))
      .then(d => setReplies(d.items.reduce((n, t) => n + (t.unhandled ? 1 : 0), 0))).catch(() => {})
  }, [ws, me, tick, withPreview])

  const setParam = (k, v) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v); else next.delete(k)
    setParams(next, { replace: k !== 'client' })
  }
  const can = (mod, act) => !!(me?.permissions?.[mod] || []).includes(act)
  const value = useMemo(() => ws && me && ({
    ws, me, viewer: { ...ws.viewer, ...me }, settings: ws.settings, locations: ws.locations, agents: ws.agents,
    can: (mod, act) => can(mod, act) && (!ws.viewer.preview || act === 'view'),
    isDemo: ws.settings.is_demo, canManage: can('renewals', 'assign') && !ws.viewer.preview,
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
  }), [ws, me, locationId, agentId, previewAs, tick, withPreview, load]) // eslint-disable-line react-hooks/exhaustive-deps

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
  const nav = (me.nav || []).map(n => ({ ...n, to: BASE + n.path, icon: ICONS[n.key] || 'spark', end: n.path === '' }))
  const primary = nav.filter(n => n.primary)
  const more = nav.filter(n => !n.primary)
  const crumb = [...nav].reverse().find(n => (n.end ? loc.pathname === n.to : loc.pathname.startsWith(n.to)))?.label || 'Today'
  const ended = me.status && me.status !== 'active'
  const who = value.viewer
  const roleText = who.preview ? `Previewing ${who.name}` : (who.is_owner ? 'Agency owner' : (who.profile || ROLE_LABEL[who.dashboard] || 'Member'))
  const scopeText = who.record_scope === 'agency' ? 'All locations'
    : who.record_scope === 'locations' ? (me.location_names?.join(', ') || 'My locations')
      : who.record_scope === 'team' ? 'My team' : 'Assigned to me'
  return (
    <Ctx.Provider value={value}>
      <div className={`ins ins-app${me.prefs?.compact ? ' ins-compact' : ''}`}>
        <a href="#ins-main" className="ins-skip">Skip to content</a>
        {open && <div className="ins-scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
        <aside className={`ins-side ${open ? 'open' : ''}`} aria-label="Insurance workspace navigation">
          <div className="ins-brand">
            <span className="ins-brand__mark" aria-hidden="true">{initials(s.display_name)}</span>
            <div><b>{s.display_name}</b><small>{s.presentation?.subtitle || 'Insurance Workspace'}</small></div>
          </div>
          <nav className="ins-nav">
            {primary.map(it => (
              <NavLink key={it.key} to={{ pathname: it.to, search: keepSearch(params) }} end={it.end}>
                <Icon name={it.icon} /><span>{it.label}</span>
                {it.key === 'conversations' && replies > 0 && <span className="ins-nav__count" aria-label={`${replies} conversations need a reply`}>{replies}</span>}
              </NavLink>
            ))}
          </nav>
          <nav className="ins-nav ins-nav--secondary" aria-label="More">
            {more.map(it => (
              <NavLink key={it.key} to={{ pathname: it.to, search: keepSearch(params) }}><Icon name={it.icon} /><span>{it.label}</span></NavLink>
            ))}
          </nav>
          <div className="ins-powered">Powered by <b>EvoSys Pro</b></div>
        </aside>
        <div className="ins-main">
          <header className="ins-top">
            <button className="ins-btn ins-btn--icon ins-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
            <nav className="ins-crumbs" aria-label="Breadcrumb"><a href="/workspaces">{s.presentation?.subtitle || 'Insurance'}</a><span aria-hidden="true">/</span><b>{crumb}</b></nav>
            <div className="ins-top__right">
              <span className="ins-chip" title="Your access profile"><Icon name="user" size={15} /> {roleText}</span>
              {(who.record_scope === 'agency' || (ws.locations.length > 1 && who.record_scope === 'locations')) && !ended ? (
                <label className="ins-select">
                  <Icon name="pin" />
                  <span className="sr-only">Location</span>
                  <select value={locationId} onChange={e => value.setLocation(e.target.value)}>
                    <option value="">{who.record_scope === 'agency' ? 'All locations' : 'My locations'}</option>
                    {ws.locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select>
                </label>
              ) : <span className="ins-chip ins-chip--scope" title="What you can see"><Icon name="pin" size={15} /> {scopeText}</span>}
              {s.is_demo && <span className="ins-mode ins-mode--demo" title="Synthetic customer data. Simulated delivery — no messages are sent.">Sample data</span>}
              {!s.is_demo && <span className="ins-mode ins-mode--live">Live · {s.data_mode.replace('_', ' ')}</span>}
              <ProgramSwitchLink />
              <div className="ins-menuwrap">
                <button className="ins-me" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(m => !m)}>
                  <span className="ins-avatar" aria-hidden="true">{initials(who.actor_name)}</span>
                  <span className="ins-me__text"><b>{who.actor_name}</b><small>{roleText}</small></span>
                </button>
                {menu && (
                  <div className="ins-menu" role="menu" onMouseLeave={() => setMenu(false)}>
                    <div className="ins-menu__who"><b>{who.actor_name}</b><small>{me.email}</small><small>{roleText} · {scopeText}</small></div>
                    <NavLink role="menuitem" to={`${BASE}/me`} onClick={() => setMenu(false)}>My settings</NavLink>
                    <a role="menuitem" href="/workspaces">Switch workspace</a>
                    <button role="menuitem" onClick={async () => { try { await logout() } finally { window.location.href = '/login' } }}>Sign out</button>
                  </div>
                )}
              </div>
            </div>
          </header>
          {ended && (
            <div className="ins-preview ins-preview--ended" role="alert">
              Your access to this agency is {me.status}. Ask the agency owner to restore it. You can still open My settings and sign out.
            </div>
          )}
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
          {s.is_demo && <footer className="ins-foot">Sample data · Simulated delivery — no live messages are sent.</footer>}
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
