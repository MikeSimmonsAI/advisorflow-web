/**
 * ORGANIZATION CONTROL CENTER — one page per organization.
 *
 * Tabs: Overview · Locations · People · Entitlements · Operations · Administration.
 *
 * THE STATUS WORDS ARE THE SERVER'S, NOT THIS FILE'S. Everything renders from
 * GET /god/customers/{id}/control-center, which evaluates the organization's
 * blueprint against stored rows (locations, users, activation links, Twilio /
 * A2P columns, email sender columns, calendar flags, AI deployments, audit
 * log). A figure the server cannot derive arrives as null and is shown as
 * "Not yet available" — never as a made-up number or trend.
 *
 * ENTERING THE CUSTOMER IS A DELIBERATE ACT. The button says so, the banner
 * that follows says whose records you are about to change, and the server
 * writes an audit row. Nothing here creates a membership.
 *
 * The existing working actions all remain: locations add/edit (plus make
 * primary / deactivate), add person with one-time setup link, reset password,
 * feature allow-list switches, the two delegation gates, activate.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { enterCustomer } from './enterCustomer'
import { errText } from './GodOpsShared'
import './GodOps.css'
import './customer/ControlCenter.css'
import { Pill, fmtDate } from './customer/ccShared'
import OverviewTab from './customer/OverviewTab'
import LocationsTab from './customer/LocationsTab'
import PeopleTab from './customer/PeopleTab'
import EntitlementsTab from './customer/EntitlementsTab'
import OperationsTab from './customer/OperationsTab'
import AdministrationTab from './customer/AdministrationTab'

const TABS = [
  ['overview', 'Overview'], ['locations', 'Locations'], ['people', 'People'],
  ['entitlements', 'Entitlements'], ['operations', 'Operations'],
  ['administration', 'Administration'],
]
const TAB_KEYS = TABS.map(t => t[0])
// Old deep links (?tab=features) keep working.
const ALIASES = { features: 'entitlements' }

function initials(name) {
  return (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase()
}

export default function CustomerDetail() {
  const { orgId } = useParams()
  const nav = useNavigate()
  const [cc, setCc] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [openWhat, setOpenWhat] = useState(null)
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState(() => {
    const t = ALIASES[params.get('tab')] || params.get('tab')
    return TAB_KEYS.includes(t) ? t : 'overview'
  })

  function chooseTab(t) {
    setTab(t)
    // Keep the URL honest so the tab survives a refresh and can be shared.
    const next = new URLSearchParams(params)
    if (t === 'overview') next.delete('tab'); else next.set('tab', t)
    setParams(next, { replace: true })
  }

  const load = useCallback(() => {
    api.get('/god/customers/' + orgId + '/control-center')
      .then(setCc).catch(e => setErr(errText(e)))
  }, [orgId])
  useEffect(load, [load])

  const clearOpen = useCallback(() => setOpenWhat(null), [])

  function onAction(action) {
    if (!action) return
    if (action.href) { nav(action.href); return }
    if (action.tab) {
      if (action.open) setOpenWhat(action.open)
      chooseTab(action.tab)
      window.scrollTo && window.scrollTo(0, 0)
    }
  }

  async function enterContext() {
    // The shared helper — one way into a tenant is one thing to audit.
    try {
      await enterCustomer(orgId, cc.header.name)
      nav('/god/customer-app')
    } catch (e) { setErr(errText(e)) }
  }

  async function activate() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/customers/' + orgId + '/activate', { acknowledge_warnings: true })
      load()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  if (err && !cc) return <div className="go-wrap"><div className="go-err">{err}</div></div>
  if (!cc) return <div className="go-wrap"><div className="go-muted">Loading…</div></div>

  const h = cc.header
  const statusTone = h.is_active ? 'good' : 'bad'

  return (
    <div className="go-wrap occ">
      <nav className="occ-crumbs" aria-label="Breadcrumb">
        <button onClick={() => nav('/god/platform')}>Platform</button><span>›</span>
        <button onClick={() => nav('/god/customers')}>Organizations</button><span>›</span>
        <span>{h.name}</span>
      </nav>

      <section className="occ-card">
        <div className="occ-header">
          <div className="occ-logo" aria-hidden="true">{initials(h.name)}</div>
          <div className="occ-title">
            <h1>{h.name}</h1>
            <div className="occ-meta">
              <span>{h.industry_label || h.industry || 'No industry'}</span>
              <span>·</span>
              <span>{h.brand || 'No brand'}</span>
              <Pill tone={statusTone} label={h.is_active ? 'Active' : 'Suspended'} />
              {h.lifecycle_status && h.lifecycle_status !== 'active' &&
                <Pill tone="neutral" label={h.lifecycle_status} />}
              {h.is_demo && <Pill tone="info" label="Demo" />}
            </div>
          </div>
          <div className="occ-head-actions">
            <button className="go-btn" onClick={() => nav('/god/customers/' + orgId + '/360')}>
              Customer 360
            </button>
            <button className="go-btn go-btn-primary" onClick={enterContext}>
              Enter organization
            </button>
          </div>
        </div>
        <div className="occ-facts">
          <div><div className="occ-fact-k">Organization ID</div><div className="occ-fact-v">{h.customer_id}</div></div>
          <div><div className="occ-fact-k">Brand</div><div className="occ-fact-v">{h.brand || '—'}</div></div>
          <div><div className="occ-fact-k">Plan</div><div className="occ-fact-v">{h.plan || '—'}</div></div>
          <div><div className="occ-fact-k">Created</div><div className="occ-fact-v">{fmtDate(h.created_at)}</div></div>
          <div>
            <div className="occ-fact-k">Timezone</div>
            <div className="occ-fact-v">{h.timezone || <span className="occ-muted">Not set</span>}</div>
          </div>
          <div>
            <div className="occ-fact-k">Primary contact</div>
            <div className="occ-fact-v">
              {h.primary_contact
                ? h.primary_contact.name
                : <span className="occ-muted" title={h.primary_contact_note}>Not tracked</span>}
            </div>
          </div>
          {h.implementation_owner && (
            <div>
              <div className="occ-fact-k">Implementation owner</div>
              <div className="occ-fact-v">{h.implementation_owner.name || h.implementation_owner.email}</div>
            </div>
          )}
        </div>
      </section>

      {err && <div className="go-err go-dismiss" onClick={() => setErr('')}>{err}</div>}

      <div className="occ-tabs" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k}
                  className={'occ-tab' + (tab === k ? ' on' : '')}
                  onClick={() => chooseTab(k)}>{label}</button>
        ))}
      </div>

      {tab === 'overview' &&
        <OverviewTab cc={cc} onAction={onAction} onActivate={activate} busy={busy} />}
      {tab === 'locations' &&
        <LocationsTab orgId={orgId} cc={cc} reload={load}
                      openAdd={openWhat === 'add-location'} onOpened={clearOpen} />}
      {tab === 'people' &&
        <PeopleTab orgId={orgId} cc={cc} reload={load}
                   openAdd={openWhat === 'add-person'} onOpened={clearOpen} />}
      {tab === 'entitlements' && <EntitlementsTab orgId={orgId} cc={cc} reload={load} />}
      {tab === 'operations' && <OperationsTab cc={cc} onEnter={enterContext} />}
      {tab === 'administration' && <AdministrationTab orgId={orgId} cc={cc} reload={load} />}
    </div>
  )
}
