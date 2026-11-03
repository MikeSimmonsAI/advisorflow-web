/**
 * ACQUISITION CONTROL CENTER (/acquisition-ops/*) - Issue #452, owner-approved
 * purple theme. Open to God and to a login the owner granted operator access
 * for a client (e.g. wholesale@) - that grant is NOT God Mode.
 *
 * Every number is the server's. Workers that are not built say so; nothing
 * here can send a message or call a data provider.
 */
import { createContext, Suspense, useContext, useEffect, useMemo, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import { BusinessForm, Readiness } from './BusinessForm'
import { DateChip, Icon, Kpi, Loading, Logo, PageHeader, Pill, apptPill, clock, errText, initials, money, useFont, useLoad, useToast, when } from './ui'
import './acquisition.css'
import './hub.css'

import { OPS, OpsCtx as Ctx, useOps } from './opsContext'
import { MasterOverview } from './HubPages'
export { OPS, useOps }
const pct = v => (v == null ? '—' : Math.round(v * 100) + '%')

// THE ACQUISITION HUB NAVIGATION (#474). Program Access -> Acquisition.
// Owner-only groups are hidden from operators AND refused by the server
// (every /god/acquisition/* endpoint is God-checked); hiding is not security.
const MASTER_NAV = [
  { to: OPS, label: 'Control Center', icon: 'dashboard', end: true },
  { to: `${OPS}/import`, label: 'Import Center', icon: 'upload' },
  { to: `${OPS}/master`, label: 'Master Prospect Pool', icon: 'database' },
  { to: `${OPS}/research`, label: 'Research & Qualification', icon: 'flask' },
  { to: `${OPS}/assignments`, label: 'Assignments & Distribution', icon: 'share' },
  { to: `${OPS}/clients`, label: 'Cleaning Client Directory', icon: 'store' },
  { to: `${OPS}/reporting`, label: 'All-Clients Reporting', icon: 'chart' },
]
// Client-scoped operating tools: EvoSys staff work these for the client chosen
// in the selector. Nothing here is the customer's own portal.
// Owner-level operating pages that span every client (no client selection needed).
const OPS_NAV = [
  { to: `${OPS}/followups`, label: 'Follow-up Queue', icon: 'reply' },
]
const CLIENT_NAV = [
  { to: `${OPS}/client-dashboard`, label: 'Client Operations', icon: 'dashboard' },
  { to: `${OPS}/prospects`, label: 'Prospect Queue', icon: 'users' },
  { to: `${OPS}/outreach`, label: 'Outreach', icon: 'mail' },
  { to: `${OPS}/responses`, label: 'Responses', icon: 'reply' },
  { to: `${OPS}/appointments`, label: 'Appointments', icon: 'calendar' },
  { to: `${OPS}/pipeline`, label: 'Pipeline & Contracts', icon: 'pipeline' },
  { to: `${OPS}/discovery`, label: 'Lead Discovery', icon: 'search' },
  { to: `${OPS}/health`, label: 'Worker Health', icon: 'heart' },
]
const SUPPORT_NAV = [
  { to: `${OPS}/onboarding`, label: 'Client Configuration', icon: 'building' },
  { to: `${OPS}/calendar`, label: 'Booking & Calendar', icon: 'calendar' },
  { to: `${OPS}/settings`, label: 'Pause, Exclusions & Operators', icon: 'settings' },
  { to: `${OPS}/activity`, label: 'Audit History', icon: 'clock' },
]
const MASTER_PATHS = [OPS, ...MASTER_NAV.slice(1).map(x => x.to), ...OPS_NAV.map(x => x.to), `${OPS}/activity`]
// Pages that need a chosen client. The owner is never dropped into one by default.
const CLIENT_PATHS = [...CLIENT_NAV.map(x => x.to), `${OPS}/onboarding`, `${OPS}/settings`]
const TITLES = Object.fromEntries([...MASTER_NAV, ...OPS_NAV, ...CLIENT_NAV, ...SUPPORT_NAV].map(x => [x.to, x.label]))

function NavGroup({ title, items, children, pre = [], startOpen = true }) {
  const [open, setOpen] = useState(startOpen)
  return (
    <div className={`hb-group ${open ? '' : 'closed'}`}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>{title}<i>▾</i></button>
      {pre.length > 0 && <nav className="aq-nav">{pre.map(it => (
        <NavLink key={it.to} to={it.to} end={it.end}><Icon name={it.icon} />{it.label}</NavLink>))}</nav>}
      {children}
      <nav className="aq-nav">{items.map(it => (
        <NavLink key={it.to} to={it.to} end={it.end}><Icon name={it.icon} />{it.label}</NavLink>))}</nav>
    </div>
  )
}

function Enable({ onDone }) {
  const [orgId, setOrgId] = useState('')
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const go = async () => {
    setBusy(true); setFail(null)
    try { await api.post(`/god/acquisition/${orgId.trim()}/enable`, {}); onDone() } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="aq aq-gate"><div className="aq-card">
      <Logo />
      <h1 className="aq-h1">Enable a cleaning client workspace</h1>
      <p className="aq-lede">Use an existing cleaning company's organization ID. Every client receives its own private Acquisition portal and starts paused. Nothing is sent.</p>
      <div className="aq-field"><label htmlFor="aq-org">Organization ID</label><input id="aq-org" value={orgId} onChange={e => setOrgId(e.target.value)} /></div>
      {fail && <div className="aq-err">{fail}</div>}
      <button className="aq-btn aq-btn--primary" disabled={busy || !orgId.trim()} onClick={go}>{busy ? 'Setting up…' : 'Enable acquisition pilot'}</button>
    </div></div>
  )
}

// Parent only manages authorized tenant identities. The keyed child remounts
// every data-fetching hook when the selection changes, so rows from one
// client's Prospects list can NEVER flash on another client's dashboard.
export default function AcquisitionOps() {
  useFont()
  const [clients, cErr, reloadClients] = useLoad('/acquisition-ops/clients')
  const [selectedOrg, setSelectedOrgState] = useState(() => {
    try { return sessionStorage.getItem('aq_client') || '' } catch { return '' } })
  const setSelectedOrg = id => { setSelectedOrgState(id); try { sessionStorage.setItem('aq_client', id || '') } catch { /* private mode */ } }
  const valid = clients?.items?.some(c => c.organization_id === selectedOrg)
  // OWNER: no client until one is chosen (never Divine Shine, never a TEST company).
  // OPERATOR: their first authorized client - they have nothing else.
  const orgId = valid ? selectedOrg : (clients?.is_god ? null : clients?.items?.[0]?.organization_id)
  if (cErr) {
    return <div className="aq aq-gate"><div className="aq-card">
      <h1 className="aq-h1">{cErr.status === 403 ? 'No acquisition access' : 'This page couldn’t load'}</h1>
      <p className="aq-lede">{cErr.status === 403 ? 'This login has not been given acquisition operator access.' : errText(cErr)}</p>
      <a className="aq-btn" href="/workspaces">Back to programs</a>
    </div></div>
  }
  if (!clients) return <div className="aq"><Loading label="Opening the control center…" /></div>
  if (!clients.items.length && !clients.is_god) return <div className="aq"><Loading /></div>
  return <TenantAcquisitionOps key={orgId || 'master'} orgId={orgId} clients={clients} onSelectOrg={setSelectedOrg} reloadClients={reloadClients} />
}

function TenantAcquisitionOps({ orgId, clients, onSelectOrg, reloadClients }) {
  const [ovLoaded, oErr, reloadOv] = useLoad(orgId ? `/acquisition-ops/${orgId}/overview` : null)
  const [list, lErr, reloadList] = useLoad(orgId ? `/acquisition-ops/${orgId}/prospects` : null)
  // The owner's master shell runs with no client loaded at all.
  const ov = orgId ? ovLoaded : { is_god: true, organization_id: null, organization_name: null, me: { name: getCurrentUser()?.name || '' },
                                   recent_events: [], profile: {} }
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  useEffect(() => { setOpen(false) }, [loc.pathname])

  if (oErr) {
    return <div className="aq aq-gate"><div className="aq-card">
      <h1 className="aq-h1">{oErr.status === 403 ? 'No acquisition access' : 'This page couldn’t load'}</h1>
      <p className="aq-lede">{errText(oErr)}</p>
      <a className="aq-btn" href="/workspaces">Back to programs</a>
    </div></div>
  }
  if (orgId && (!ov || ov.organization_id !== orgId)) return <div className="aq"><Loading label="Opening selected client…" /></div>
  const reload = () => { reloadOv(); reloadList() }
  const p = ov.profile
  const me = ov.me?.name || ''
  const onMaster = ov.is_god && MASTER_PATHS.includes(loc.pathname)
  const needsClient = !orgId && !onMaster
  const page = TITLES[loc.pathname] || (ov.is_god ? 'Control Center' : 'Client Operations')
  return (
    <Ctx.Provider value={{ ov, list, listErr: lErr, reload, orgId, clients, onSelectOrg, reloadClients }}>
      <div className="aq aq-app aq-dark">
        {open && <div className="aq-scrim" onClick={() => setOpen(false)} />}
        <aside className={`aq-side ${open ? 'open' : ''}`} aria-label="Acquisition Control Center navigation">
          <div className="aq-logo"><span className="aq-logo__mark"><Icon name="spark" size={16} /></span>
            <span>EvoSys Pro<small>Acquisition Control Center</small></span></div>
          {ov.is_god && <NavGroup title="Master Control" items={MASTER_NAV} />}
          <NavGroup title="Acquisition Operations" pre={ov.is_god ? OPS_NAV : []} items={ov.is_god || clients.items.length > 1 ? CLIENT_NAV : CLIENT_NAV.map(x => x.to === `${OPS}/client-dashboard` ? { ...x, to: OPS, end: true } : x)}>
            {(clients.is_god || clients.items.length > 1) && <div className="hb-client">
              <label htmlFor="hb-client">Cleaning company</label>
              <select id="hb-client" aria-label="Acquisition client workspace" value={orgId || ''} onChange={e => onSelectOrg(e.target.value)}>
                {!orgId && <option value="">Choose a client…</option>}
                {clients.items.map(c => <option key={c.organization_id} value={c.organization_id}>{c.name}</option>)}
              </select></div>}
          </NavGroup>
          <NavGroup title="Administration" items={ov.is_god ? SUPPORT_NAV : SUPPORT_NAV.filter(x => x.to !== `${OPS}/activity`)} startOpen={false}>
          </NavGroup>
          <nav className="aq-nav" style={{ marginTop: 'auto' }}><a href="/workspaces"><Icon name="back" />Return to Program Access</a></nav>
        </aside>
        <div className="aq-main">
          <header className="aq-top">
            <button className="aq-btn aq-btn--sm aq-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
            <nav className="hb-crumbs" aria-label="Breadcrumb"><a href="/workspaces">Program Access</a><span>›</span>
              <span className="hb-crumb-mid">Acquisition Control Center</span><span className="hb-crumb-mid">›</span><b>{page}</b></nav>
            <div className="aq-top__right">
              <span className={`hb-role ${ov.is_god ? '' : 'hb-role--op'}`}><Icon name={ov.is_god ? 'shield' : 'key'} size={14} />{ov.is_god ? 'Owner' : 'Operator'}</span>
              <span className="aq-me"><span className="aq-avatar">{initials(me)}</span>
                <div><b>{me.split(' ')[0] || me}</b><span>EvoSys Pro</span></div></span>
            </div>
          </header>
          {!onMaster && orgId && <div className="aq-page" style={{ paddingBottom: 0 }}>
            <div className="hb-strip"><span className="hb-strip__mark">{initials(ov.organization_name)}</span>
              <div><b>{ov.organization_name} — Acquisition Operations</b>
                <span>{p.offer_label || 'Cleaning client'} · {(p.service_area || []).slice(0, 4).join(', ') || 'No service area yet'}</span></div>
              <div className="hb-strip__right">
                <span className={`aq-badge ${p.paused ? 'aq-badge--paused' : ''}`}><Icon name={p.paused ? 'pause' : 'calendar'} size={15} />
                  {p.paused ? 'Paused' : ov.progress.started ? `Pilot day ${ov.progress.day} of 30` : 'Pilot not started'}</span>
                <span className={`aq-badge ${p.readiness?.launch_ready ? '' : 'aq-badge--off'}`}>{p.readiness?.launch_ready ? 'Onboarding complete' : 'Onboarding draft'}</span>
              </div></div></div>}
          {needsClient ? <ChooseClient clients={clients} onSelectOrg={onSelectOrg} page={page} />
            : <Suspense fallback={<Loading />}><Outlet /></Suspense>}
        </div>
      </div>
    </Ctx.Provider>
  )
}

// The owner picks a client on purpose; nothing is pre-selected for them.
function ChooseClient({ clients, onSelectOrg, page }) {
  const items = clients.items || []
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Acquisition Operations" title={page} sub="Choose which cleaning client to work on. Nothing is opened until you pick one." />
      {items.length ? <div className="hb-cards">{items.map(c => (
        <button key={c.organization_id} className="hb-ccard" style={{ textAlign: 'left', cursor: 'pointer', font: 'inherit', color: 'inherit' }} onClick={() => onSelectOrg(c.organization_id)}>
          <div className="hb-ccard__top"><span className="hb-strip__mark" style={{ width: 36, height: 36 }}>{initials(c.name)}</span>
            <div><b>{c.name}</b>{/^test\s/i.test(c.name || '') && <div className="hb-muted">TEST workspace — not counted in results</div>}</div></div>
        </button>))}</div>
        : <div className="aq-card"><p>No cleaning clients yet.</p><a className="aq-btn aq-btn--primary" href={`${OPS}/clients?add=1`}>Add Cleaning Company</a></div>}
    </div>
  )
}

// ── Control Center ──────────────────────────────────────────────────────
function ProgressChart({ progress }) {
  if (!progress.started || !progress.series.length) {
    return <div className="aq-empty" style={{ padding: '60px 0' }}>The 30-day pilot clock hasn’t started.<br />This chart fills in from real activity once it does.</div>
  }
  const s = progress.series
  const W = 520, H = 200, P = 28
  const max = Math.max(5, ...s.map(x => x.researched))
  const x = i => P + (i / Math.max(1, 29)) * (W - P * 2)
  const y = v => H - P - (v / max) * (H - P * 2)
  const line = k => s.map((r, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Pilot progress">
      {[0, .5, 1].map(f => <line key={f} x1={P} x2={W - P} y1={y(max * f)} y2={y(max * f)} stroke="#ece6fb" />)}
      <path d={`${line('researched')} L${x(s.length - 1)},${y(0)} L${x(0)},${y(0)} Z`} fill="rgba(139,92,246,.14)" />
      <path d={line('researched')} fill="none" stroke="#8b5cf6" strokeWidth="2.5" />
      <path d={line('interested')} fill="none" stroke="#2f6fdb" strokeWidth="2.5" />
      <path d={line('booked')} fill="none" stroke="#15924a" strokeWidth="2.5" />
      {[1, 7, 14, 21, 30].map(d => <text key={d} x={x(d - 1)} y={H - 8} fontSize="11" textAnchor="middle" fill="#5f5a7a">Day {d}</text>)}
    </svg>
  )
}

function TargetRing({ booked, high }) {
  const r = 56, C = 2 * Math.PI * r, f = Math.min(1, booked / high)
  return (
    <div className="aq-ring">
      <svg width="140" height="140" viewBox="0 0 140 140" aria-hidden="true">
        <circle cx="70" cy="70" r={r} fill="none" stroke="#ece6fb" strokeWidth="12" />
        <circle cx="70" cy="70" r={r} fill="none" stroke="#7c4dea" strokeWidth="12" strokeLinecap="round"
                strokeDasharray={`${C * f} ${C}`} transform="rotate(-90 70 70)" />
      </svg>
      <div className="aq-ring__txt"><b>{booked} of {high}</b><span>Appointments</span></div>
    </div>
  )
}

export function ControlCenter() {
  const { ov } = useOps()
  const loc = useLocation()
  return ov.is_god && loc.pathname === OPS ? <MasterOverview /> : <ClientControlCenter />
}

function ClientControlCenter() {
  const { ov } = useOps()
  const f = ov.client_funnel, all = ov.funnel, t = ov.targets
  const checks = [
    { on: ov.progress.started, text: `Pilot active (${t.pilot_days} days)` },
    { on: f.booked >= t.appointments_low, text: `Target: ${t.appointments_low}–${t.appointments_high} appointments` },
    { on: f.held > 0, text: 'Track outcomes & ROI' },
    { on: false, text: 'Prepare pilot results report' },
  ]
  return (
    <div className="aq-page">
      {ov.profile.paused && <div className="aq-note aq-note--warn"><b>Paused</b> {ov.profile.paused_at ? `since ${when(ov.profile.paused_at)}` : ''} — {ov.profile.paused_reason}</div>}
      {!ov.profile.readiness?.launch_ready && <div className="aq-note aq-note--warn"><b>Onboarding is a draft.</b> Real businesses can't be emailed until it has: {(ov.profile.readiness?.missing || []).join(', ')}. <a href={`${OPS}/onboarding`}>Finish onboarding</a></div>}
      <div className="aq-note">{ov.profile.offer_label} · {(ov.profile.service_states || []).join(', ') || 'no states set'} · {(ov.profile.service_area || []).slice(0, 6).join(', ')}{(ov.profile.service_area || []).length > 6 ? '…' : ''} · {ov.profile.timezone}</div>
      <div className="aq-grid aq-kpis aq-kpis--dark">
        <Kpi icon="search" value={f.real} label="Businesses Found" sub={`(DFW target) · ${all.practice} practice`} />
        <Kpi icon="users" value={f.contacts_verified} label="Contacts Verified" sub="(Decision-makers)" />
        <Kpi icon="mail" value={pct(f.reply_rate)} label="Reply Rate" sub={f.contacted ? `(${f.replied} of ${f.contacted} contacted)` : '(Nothing sent yet)'} />
        <Kpi icon="calendar" value={f.booked} label="Appointments" sub="(Booked)" />
      </div>
      <div className="aq-grid aq-split">
        <section className="aq-card">
          <h2>Pilot Progress (30 Days)</h2>
          <div className="aq-legend"><span><i style={{ background: '#8b5cf6' }} />Leads Researched</span><span><i style={{ background: '#2f6fdb' }} />Interested</span><span><i style={{ background: '#15924a' }} />Appointments</span></div>
          <ProgressChart progress={ov.progress} />
        </section>
        <section className="aq-card">
          <h2>Pilot Targets</h2>
          <TargetRing booked={f.booked} high={t.appointments_high} />
          <ul className="aq-checklist">{checks.map(c => <li key={c.text}><span className={`aq-tick ${c.on ? 'on' : ''}`} />{c.text}</li>)}</ul>
        </section>
      </div>
      <section className="aq-card" style={{ marginTop: 14 }}>
        <h2>Recent activity</h2>
        {ov.recent_events.length ? ov.recent_events.slice(0, 8).map((e, i) => (
          <div className="aq-row" key={i}><div className="aq-row__main"><b>{e.kind.replace('_', ' ')}{e.to ? ` → ${e.to.replace('_', ' ')}` : ''}</b><span>{when(e.at)}{e.note ? ` · ${e.note}` : ''}</span></div></div>
        )) : <div className="aq-empty">No activity yet.</div>}
      </section>
    </div>
  )
}

export function OpsOnboarding() {
  const { ov, orgId, reload } = useOps()
  const [toast, say] = useToast()
  return (
    <div className="aq-page">{toast}
      <h1 className="aq-h1">Onboarding</h1>
      <p className="aq-lede">The client's business facts. Fill in only what the client has confirmed; nothing here is guessed.</p>
      <Readiness r={ov.profile.readiness} />
      <div style={{ marginTop: 14 }}>
        <BusinessForm profile={ov.profile} segments={ov.segments} canEdit ownerControls={ov.is_god}
          onSave={async body => { await api.put(`/acquisition-ops/${orgId}/profile`, body); say('Saved'); reload() }} />
      </div>
    </div>
  )
}

export function Soon({ title, phase, text }) {
  return (
    <div className="aq-page"><section className="aq-card aq-soon">
      <b>{title}</b><p className="aq-lede" style={{ margin: '0 auto' }}>{text}</p>
      {phase && <p style={{ marginTop: 12 }}><span className="aq-pill">Planned in {phase}</span></p>}
    </section></div>
  )
}

// ── Prospects ───────────────────────────────────────────────────────────
function qualification(x, profile) {
  const cities = (profile.service_area || []).map(c => c.toLowerCase())
  const ev = x.evidence || []
  return [
    { on: !!x.segment && (profile.segments || []).includes(x.segment), text: 'In target industry' },
    { on: !!x.city && cities.includes(x.city.toLowerCase()), text: 'In service area (DFW)' },
    { on: ev.some(e => e.kind === 'size' || e.kind === 'buying_signal'), text: 'Likely recurring need (evidence)' },
    { on: !!x.contact_title || ev.some(e => e.kind === 'decision_maker'), text: 'Decision-maker found' },
    { on: ev.some(e => e.kind === 'contact_email' && e.verified), text: 'Contact email verified' },
  ]
}

function ProspectDetail({ x }) {
  const { ov, orgId, reload } = useOps()
  const [tab, setTab] = useState('overview')
  const [stage, setStage] = useState('')
  const [note, setNote] = useState('')
  const [ev, setEv] = useState({ kind: ov.evidence_kinds[0], value: '', source_url: '', confidence: '', verified: false })
  const [fail, setFail] = useState(null)
  const move = async () => {
    setFail(null)
    try { await api.post(`/acquisition-ops/${orgId}/prospects/${x.id}/stage`, { stage, note, reason: note }); setStage(''); setNote(''); reload() } catch (e) { setFail(errText(e)) }
  }
  const addEv = async () => {
    setFail(null)
    try { await api.post(`/acquisition-ops/${orgId}/prospects/${x.id}/evidence`, { ...ev, confidence: ev.confidence === '' ? null : ev.confidence }); setEv({ ...ev, value: '', source_url: '', confidence: '' }); reload() } catch (e) { setFail(errText(e)) }
  }
  const q = qualification(x, ov.profile)
  return (
    <div className="aq-detail">
      <div className="aq-card__head"><span className="aq-kpi__icon" style={{ margin: 0 }}><Icon name="building" /></span>
        <div><h2 style={{ margin: 0 }}>{x.business_name}</h2><span style={{ color: 'var(--aq-sub)', fontSize: 13 }}>{[x.segment_label, x.city].filter(Boolean).join(' • ')}</span></div>
        <span style={{ marginLeft: 'auto' }}>{x.score != null ? <span className="aq-score">Score: {x.score}</span> : <span className="aq-pill">Not scored</span>}</span>
      </div>
      <div className="aq-tabs">{[['overview', 'Overview'], ['signals', `Signals (${x.evidence.length})`], ['contacts', 'Contacts'], ['email', 'Email'], ['schedule', 'Schedule'], ['actions', 'Actions']].map(([k, l]) => (
        <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>))}</div>
      {tab === 'overview' && (
        <div className="aq-grid aq-two">
          <dl className="aq-dl">
            <dt>Business type</dt><dd>{x.segment_label || '—'}</dd>
            <dt>Website</dt><dd>{x.website ? <a href={x.website} target="_blank" rel="noreferrer">{x.domain || x.website}</a> : '—'}</dd>
            <dt>Found at</dt><dd>{x.source_url ? <a href={x.source_url} target="_blank" rel="noreferrer">{x.source}</a> : x.source}</dd>
            <dt>Stage</dt><dd><Pill stage={x.stage} label={x.stage_label} />{x.is_practice && <> <span className="aq-pill">practice</span></>}</dd>
            {x.disqualified_reason && <><dt>Not a fit</dt><dd>{x.disqualified_reason}</dd></>}
            {x.interest_note && <><dt>Interest</dt><dd>{x.interest_note}</dd></>}
          </dl>
          <div><h2 style={{ fontSize: 14 }}>Qualification</h2>
            <ul className="aq-checklist">{q.map(c => <li key={c.text}><span className={`aq-tick ${c.on ? 'on' : ''}`} />{c.text}</li>)}</ul>
            {x.stage === 'contact_verified' && <span className="aq-pill aq-pill--ok">Ready for Outreach</span>}
          </div>
        </div>
      )}
      {tab === 'signals' && (
        <>
          {x.evidence.length ? x.evidence.map(e => (
            <div key={e.id} className="aq-row"><div className="aq-row__main"><b>{e.kind}: {e.value || '—'}</b>
              <span>{e.source_url ? <a href={e.source_url} target="_blank" rel="noreferrer">{e.source_url}</a> : 'no source'} · confidence {e.confidence ?? '—'}</span></div>
              <span className={`aq-pill ${e.verified ? 'aq-pill--ok' : ''}`}>{e.verified ? 'verified' : 'unverified'}</span></div>
          )) : <div className="aq-empty">No evidence recorded.</div>}
          <div className="aq-actions">
            <select value={ev.kind} onChange={e => setEv({ ...ev, kind: e.target.value })}>{ov.evidence_kinds.map(k => <option key={k}>{k}</option>)}</select>
            <input placeholder="Fact" value={ev.value} onChange={e => setEv({ ...ev, value: e.target.value })} />
            <input placeholder="Source URL" value={ev.source_url} onChange={e => setEv({ ...ev, source_url: e.target.value })} />
            <input placeholder="Confidence 0–1" style={{ width: 130 }} value={ev.confidence} onChange={e => setEv({ ...ev, confidence: e.target.value })} />
            <label className="aq-checks"><label><input type="checkbox" checked={ev.verified} onChange={e => setEv({ ...ev, verified: e.target.checked })} />Verified at source</label></label>
            <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={addEv}>Add evidence</button>
          </div>
        </>
      )}
      {tab === 'contacts' && (
        <dl className="aq-dl">
          <dt>Name</dt><dd>{x.contact_name || '—'}</dd><dt>Title</dt><dd>{x.contact_title || '—'}</dd>
          <dt>Email</dt><dd>{x.email || '—'}</dd><dt>Phone</dt><dd>{x.phone || '—'}</dd><dt>Address</dt><dd>{x.address || '—'}</dd>
          <dt>Territory</dt><dd>{x.geo ? <><span className={`aq-pill ${x.geo.status === 'eligible' ? 'aq-pill--ok' : ''}`}>{x.geo.status}</span> {x.geo.note}</> : '—'}</dd>
        </dl>
      )}
      {tab === 'contacts' && <LocationForm x={x} />}
      {tab === 'email' && <EmailTab x={x} />}
      {tab === 'schedule' && <ScheduleTab x={x} />}
      {tab === 'actions' && (
        <div className="aq-actions">
          <select value={stage} onChange={e => setStage(e.target.value)}><option value="">Move to stage…</option>
            {ov.stages.filter(s => !['contacted', 'replied', 'appointment_booked'].includes(s.key)).map(s => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
          <input placeholder="Note / reason (required for Interested and Not a fit)" style={{ flex: 1, minWidth: 220 }} value={note} onChange={e => setNote(e.target.value)} />
          <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!stage} onClick={move}>Move</button>
          <small style={{ color: 'var(--aq-sub)', width: '100%' }}>Contacted and Replied are recorded from real messages only. Appointments are booked on the Schedule tab, onto the client’s real calendar.</small>
        </div>
      )}
      {fail && <div className="aq-err">{fail}</div>}
    </div>
  )
}

// Book (or move) a real appointment on the CLIENT's connected calendar.
function ScheduleTab({ x }) {
  const { orgId, reload } = useOps()
  const [cal] = useLoad(`/acquisition-ops/${orgId}/calendar`)
  const today = new Date()
  const iso = d => d.toISOString().slice(0, 10)
  const [day, setDay] = useState(iso(new Date(today.getTime() + 864e5)))
  const [slots, setSlots] = useState(null)
  const [slot, setSlot] = useState('')
  const [agreed, setAgreed] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [done, setDone] = useState(null)
  const booked = x.stage === 'appointment_booked'
  const canBook = x.stage === 'interested'
  const look = async () => {
    setFail(null); setSlots(null); setSlot('')
    try { setSlots(await api.get(`/acquisition-ops/${orgId}/prospects/${x.id}/slots?day=${day}`)) } catch (e) { setFail(errText(e)) }
  }
  const go = async () => {
    setBusy(true); setFail(null)
    try {
      const r = booked ? await api.post(`/acquisition-ops/${orgId}/prospects/${x.id}/reschedule`, { slot, note })
        : await api.post(`/acquisition-ops/${orgId}/prospects/${x.id}/book`, { slot, note, confirmed_with_prospect: agreed })
      setDone(r); setSlots(null); reload()
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  if (!canBook && !booked) return <div className="aq-empty">Appointments are booked once the prospect is <b>Interested</b>. Current stage: {x.stage_label}.</div>
  return (
    <div>
      {cal && !cal.ready && <div className="aq-note aq-note--warn">{cal.status_label}. <a href={`${OPS}/calendar`}>Open booking setup →</a></div>}
      {booked && x.appointment && <div className="aq-note">Booked for <b>{when(x.appointment.time)}</b> · {x.appointment.calendar_synced ? 'on the client’s calendar' : 'calendar not confirmed'}. Pick a new time to reschedule the same appointment.</div>}
      {done && <div className="aq-ok">{done.status === 'booked' ? 'Booked and on the client’s calendar' : 'Rescheduled'} — {when(done.booked_time)}. Nothing was emailed.</div>}
      <div className="aq-actions">
        <input type="date" aria-label="Day" value={day} min={iso(today)} onChange={e => setDay(e.target.value)} />
        <button className="aq-btn aq-btn--sm" disabled={!cal?.ready} onClick={look}>Show real free times</button>
      </div>
      {slots && (slots.slots.length ? <div className="aq-actions">{slots.slots.map(t => (
        <button key={t} className={`aq-btn aq-btn--sm ${slot === t ? 'aq-btn--primary' : ''}`} onClick={() => setSlot(t)}>{clock(t)}</button>))}</div>
        : <p className="hb-muted">No free times that day{slots.reason ? ` — ${slots.reason}` : ''}.</p>)}
      {slots && <p className="hb-muted">From {slots.calendar_of}’s connected calendar ({slots.timezone}), within the client’s booking windows.</p>}
      {slot && <div style={{ marginTop: 10 }}>
        {!booked && <label className="aq-checks"><label><input type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} />The prospect agreed to {when(slot)}</label></label>}
        <div className="aq-field"><input placeholder="Note for the record (optional)" value={note} onChange={e => setNote(e.target.value)} /></div>
        <button className="aq-btn aq-btn--primary" disabled={busy || (!booked && !agreed)} onClick={go}>{busy ? 'Writing to the calendar…' : booked ? 'Move appointment' : 'Book on the client’s calendar'}</button>
      </div>}
      {fail && <div className="aq-err">{fail}</div>}
    </div>
  )
}

// Owner: where EvoSys books this client's appointments.
export function CalendarSetup() {
  const { ov, orgId } = useOps()
  const [cfg, err, reload] = useLoad(`/acquisition-ops/${orgId}/calendar`)
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  useEffect(() => { if (cfg && !f) setF({ ...cfg, booking_user_id: cfg.booking_user?.id || '', windows: Object.fromEntries(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(d => [d, (cfg.booking_windows[d] || [])[0] || ['', '']])) }) }, [cfg])
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!cfg || !f) return <div className="aq-page"><Loading /></div>
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const save = async () => {
    setBusy(true); setFail(null)
    const windows = Object.fromEntries(Object.entries(f.windows).filter(([, r]) => r[0] && r[1]).map(([d, r]) => [d, [r]]))
    try {
      await api.put(`/acquisition-ops/${orgId}/calendar`, { booking_user_id: f.booking_user_id || null, timezone: f.timezone, appointment_minutes: f.appointment_minutes,
        min_notice_hours: f.min_notice_hours ?? '', buffer_minutes: f.buffer_minutes ?? '', booking_windows: windows, meeting_location: f.meeting_location || '',
        booking_contact: f.booking_contact || '', reschedule_rules: f.reschedule_rules || '', confirmation_pref: f.confirmation_pref })
      say('Saved'); reload()
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const DAYS = [['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'], ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun']]
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Administration" title="Booking & Calendar" sub={`Where EvoSys books ${ov.organization_name}’s appointments. Only real free times on the client’s connected calendar are ever offered.`} />
      <div className={`aq-note ${cfg.ready ? '' : 'aq-note--warn'}`}><b>{cfg.ready ? 'Ready.' : 'Not ready.'}</b> {cfg.status_label}</div>
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Appointment destination</h2>
          <div className="aq-field"><label htmlFor="c-u">Client login that receives appointments</label>
            <select id="c-u" value={f.booking_user_id} onChange={set('booking_user_id')} disabled={!ov.is_god}><option value="">Choose…</option>
              {cfg.candidates.map(u => <option key={u.id} value={u.id}>{u.name} — {u.calendar_connected ? 'calendar connected' : 'no calendar yet'}</option>)}</select></div>
          {!cfg.candidates.length && <p className="hb-muted">This client has no login yet. Invite one from the Cleaning Client Directory; they connect their Google or Microsoft calendar after signing in.</p>}
          <div className="aq-grid aq-two">
            <div className="aq-field"><label htmlFor="c-tz">Time zone</label><input id="c-tz" value={f.timezone || ''} onChange={set('timezone')} /></div>
            <div className="aq-field"><label htmlFor="c-m">Meeting length (min)</label><input id="c-m" inputMode="numeric" value={f.appointment_minutes || ''} onChange={set('appointment_minutes')} /></div>
            <div className="aq-field"><label htmlFor="c-n">Minimum notice (hours)</label><input id="c-n" inputMode="numeric" value={f.min_notice_hours ?? ''} onChange={set('min_notice_hours')} /></div>
            <div className="aq-field"><label htmlFor="c-b">Buffer between visits (min)</label><input id="c-b" inputMode="numeric" value={f.buffer_minutes ?? ''} onChange={set('buffer_minutes')} /></div>
          </div>
          <p className="hb-muted">Bookings are placed in {cfg.slot_engine_timezone} (the platform scheduler’s working zone).</p>
        </section>
        <section className="aq-card"><h2>Booking windows</h2>
          <p className="hb-muted">Leave a day blank to allow any free time the calendar shows.</p>
          {DAYS.map(([d, l]) => <div className="aq-actions" key={d} style={{ marginTop: 6 }}><b style={{ width: 40 }}>{l}</b>
            <input type="time" aria-label={`${l} from`} value={f.windows[d][0]} onChange={e => setF({ ...f, windows: { ...f.windows, [d]: [e.target.value, f.windows[d][1]] } })} />
            <span>to</span><input type="time" aria-label={`${l} to`} value={f.windows[d][1]} onChange={e => setF({ ...f, windows: { ...f.windows, [d]: [f.windows[d][0], e.target.value] } })} /></div>)}
        </section>
      </div>
      <section className="aq-card" style={{ marginTop: 14 }}><h2>Meeting details</h2>
        <div className="aq-grid aq-two">
          <div className="aq-field"><label htmlFor="c-l">Meeting location</label><input id="c-l" placeholder="e.g. At the prospect's property" value={f.meeting_location || ''} onChange={set('meeting_location')} /></div>
          <div className="aq-field"><label htmlFor="c-c">Booking contact at the client</label><input id="c-c" value={f.booking_contact || ''} onChange={set('booking_contact')} /></div>
          <div className="aq-field"><label htmlFor="c-r">Rescheduling rules</label><textarea id="c-r" value={f.reschedule_rules || ''} onChange={set('reschedule_rules')} /></div>
          <div className="aq-field"><label htmlFor="c-p">Confirmation</label><select id="c-p" value={f.confirmation_pref} onChange={set('confirmation_pref')}>
            <option value="calendar_only">Calendar event only</option><option value="calendar_and_email">Calendar event + EvoSys email</option></select></div>
        </div>
        {fail && <div className="aq-err">{fail}</div>}
        {ov.is_god ? <button className="aq-btn aq-btn--primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save booking setup'}</button> : <p className="hb-muted">Only the EvoSys owner changes booking setup.</p>}
      </section>
    </div>
  )
}

function LocationForm({ x }) {
  const { orgId, reload } = useOps()
  const [f, setF] = useState({ city: x.city || '', state: '', zip_code: '', note: '' })
  const [fail, setFail] = useState(null)
  const save = async () => {
    setFail(null)
    try { await api.put(`/acquisition-ops/${orgId}/prospects/${x.id}/location`, f); reload() } catch (e) { setFail(errText(e)) }
  }
  return (
    <div className="aq-actions" style={{ marginTop: 8 }}>
      <input placeholder="City" value={f.city} onChange={e => setF({ ...f, city: e.target.value })} />
      <input placeholder="State (TX)" style={{ width: 90 }} value={f.state} onChange={e => setF({ ...f, state: e.target.value })} />
      <input placeholder="ZIP" style={{ width: 90 }} value={f.zip_code} onChange={e => setF({ ...f, zip_code: e.target.value })} />
      <input placeholder="How you confirmed it" style={{ flex: 1, minWidth: 180 }} value={f.note} onChange={e => setF({ ...f, note: e.target.value })} />
      <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!f.state.trim()} onClick={save}>Confirm location</button>
      <small style={{ color: 'var(--aq-sub)', width: '100%' }}>Contiguous US except California. A phone area code isn't proof of location.</small>
      {fail && <div className="aq-err">{fail}</div>}
    </div>
  )
}

function EmailTab({ x }) {
  const { orgId, reload } = useOps()
  const [pv, setPv] = useState(null)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [sure, setSure] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [fail, setFail] = useState(null)
  const base = `/acquisition-ops/${orgId}/prospects/${x.id}/email`
  useEffect(() => {
    let live = true
    api.post(`${base}/preview`, {}).then(r => { if (live) { setPv(r); setSubject(r.subject); setBody(r.body) } }).catch(e => live && setFail(errText(e)))
    return () => { live = false }
  }, [base, x.stage])
  const send = async () => {
    setBusy(true); setFail(null); setMsg(null)
    try {
      const r = await api.post(`${base}/send`, { subject, body })
      setMsg(r.pilot_started ? 'Sent. The 30-day pilot clock has started.' : 'Sent.'); setSure(false); reload()
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  if (!pv) return fail ? <div className="aq-err">{fail}</div> : <div className="aq-empty">Loading…</div>
  const blocked = pv.refused_because || []
  return (
    <div>
      <dl className="aq-dl"><dt>From</dt><dd>{pv.from}</dd><dt>To</dt><dd>{pv.to || '—'}</dd>
        <dt>Sent today</dt><dd>{pv.sent_last_24h} of {pv.daily_cap}</dd></dl>
      <label className="aq-field">Subject<input value={subject} onChange={e => setSubject(e.target.value)} /></label>
      <label className="aq-field">Message<textarea rows={9} value={body} onChange={e => setBody(e.target.value)} /></label>
      {pv.kind === 'booking' && <p style={{ color: 'var(--aq-sub)', fontSize: 12 }}>The walkthrough link replaces [walkthrough link] when it's sent. It only offers times the calendar shows as free.</p>}
      <p style={{ color: 'var(--aq-sub)', fontSize: 12, whiteSpace: 'pre-line' }}>Added at the bottom: {pv.footer}</p>
      {blocked.length > 0 && <div className="aq-err">Can't send yet:<ul>{blocked.map(b => <li key={b}>{b}</li>)}</ul></div>}
      <div className="aq-actions">
        <label className="aq-checks"><label><input type="checkbox" checked={sure} disabled={blocked.length > 0} onChange={e => setSure(e.target.checked)} />I've read this email and want it sent to {pv.to}</label></label>
        <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!sure || busy || blocked.length > 0} onClick={send}>{busy ? 'Sending…' : pv.kind === 'booking' ? 'Send walkthrough link' : 'Send email'}</button>
      </div>
      {msg && <div className="aq-ok">{msg}</div>}
      {fail && <div className="aq-err">{fail}</div>}
    </div>
  )
}

function AddProspect() {
  const { ov, orgId, reload } = useOps()
  const blank = { business_name: '', segment: '', website: '', source_url: '', city: '', state: '', zip_code: '', phone: '', contact_first_name: '',
                  contact_last_name: '', contact_title: '', email: '', is_practice: true }
  const [f, setF] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  const set = k => e => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const go = async () => {
    setBusy(true); setFail(null)
    try { await api.post(`/acquisition-ops/${orgId}/prospects`, { ...f, segment: f.segment || null }); setF(blank); say('Business added'); reload() }
    catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const input = (k, label) => <div className="aq-field"><label>{label}</label><input value={f[k]} onChange={set(k)} /></div>
  return (
    <section className="aq-card">{toast}
      <h2>Add a business by hand</h2>
      <div className="aq-grid aq-two">
        <div>{input('business_name', 'Business name *')}
          <div className="aq-field"><label>Business type</label><select value={f.segment} onChange={set('segment')}><option value="">—</option>
            {ov.segments.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}</select></div>
          {input('website', 'Website')}{input('source_url', 'Where it was found (URL)')}{input('city', 'City')}{input('state', 'State (from the address)')}{input('zip_code', 'ZIP')}</div>
        <div>{input('contact_first_name', 'Contact first name')}{input('contact_last_name', 'Contact last name')}
          {input('contact_title', 'Contact title')}{input('email', 'Contact email')}{input('phone', 'Phone')}</div>
      </div>
      <div className="aq-checks"><label><input type="checkbox" checked={f.is_practice} onChange={set('is_practice')} />Practice cohort (never shown to the client, never contacted)</label></div>
      {fail && <div className="aq-err">{fail}</div>}
      <div className="aq-actions"><button className="aq-btn aq-btn--primary" disabled={busy || !f.business_name.trim()} onClick={go}>{busy ? 'Adding…' : 'Add business'}</button></div>
    </section>
  )
}

export function Prospects() {
  const { ov, list, listErr } = useOps()
  const [stage, setStage] = useState('')
  const [practice, setPractice] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const items = useMemo(() => (list?.items || []).filter(x =>
    (!stage || x.stage === stage) && (practice === '' || String(x.is_practice) === practice) &&
    (!q || (x.business_name + ' ' + (x.city || '') + ' ' + (x.domain || '')).toLowerCase().includes(q.toLowerCase()))), [list, stage, practice, q])
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Prospects</h1>
      <p className="aq-lede">Every business in the pipeline with where it came from and the evidence behind each fact.</p>
      <section className="aq-card">
        <div className="aq-actions" style={{ marginTop: 0, marginBottom: 8 }}>
          <input placeholder="Search name, city or website" value={q} onChange={e => setQ(e.target.value)} style={{ flex: '1 1 220px' }} />
          <select value={stage} onChange={e => setStage(e.target.value)}><option value="">All stages</option>
            {ov.stages.map(s => <option key={s.key} value={s.key}>{s.label} ({ov.funnel.by_stage[s.key] || 0})</option>)}</select>
          <select value={practice} onChange={e => setPractice(e.target.value)}><option value="">Real + practice</option><option value="false">Real only</option><option value="true">Practice only</option></select>
        </div>
        {listErr && <div className="aq-err">{errText(listErr)}</div>}
        {!list ? <Loading /> : items.length ? items.map(x => (
          <div key={x.id}>
            <div className="aq-row">
              <span className="aq-kpi__icon" style={{ margin: 0, width: 32, height: 32 }}><Icon name="building" size={16} /></span>
              <div className="aq-row__main"><b>{x.business_name} {x.is_practice && <span className="aq-pill">practice</span>}</b>
                <span>{[x.segment_label, x.city, x.domain].filter(Boolean).join(' · ')} · {x.evidence.length} evidence{x.geo && x.geo.status !== 'eligible' ? <> · <span className="aq-pill">{x.geo.status === 'review' ? 'Location needs review' : 'Outside territory'}</span></> : null}</span></div>
              {x.score != null && <span className="aq-score">{x.score}</span>}
              <Pill stage={x.stage} label={x.stage_label} />
              <button className="aq-btn aq-btn--sm" onClick={() => setOpen(open === x.id ? null : x.id)}>{open === x.id ? 'Close' : 'Open'}</button>
            </div>
            {open === x.id && <ProspectDetail x={x} />}
          </div>
        )) : <div className="aq-empty">No businesses match. Discovery (P2) is not built; add businesses by hand below.</div>}
      </section>
      <div style={{ marginTop: 14 }}><AddProspect /></div>
    </div>
  )
}

// ── Appointments ────────────────────────────────────────────────────────
export function OpsAppointments() {
  const { list } = useOps()
  const [tab, setTab] = useState('upcoming')
  const now = new Date().toISOString()
  const withAppt = (list?.items || []).filter(x => x.appointment && !x.is_practice)
  const lists = {
    upcoming: withAppt.filter(x => x.stage === 'appointment_booked' && (x.appointment.time || '') >= now),
    completed: withAppt.filter(x => ['appointment_held', 'quote_issued', 'won', 'lost'].includes(x.stage)),
    no_show: withAppt.filter(x => x.stage === 'no_show'),
  }
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Appointments</h1>
      <p className="aq-lede">Schedule, confirm and track consultations. Booking against the client’s live calendar arrives in P4.</p>
      <div className="aq-tabs">{[['upcoming', `Upcoming (${lists.upcoming.length})`], ['completed', 'Completed'], ['no_show', 'No-Shows']].map(([k, l]) => (
        <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>))}</div>
      <section className="aq-card">
        {!list ? <Loading /> : lists[tab].length ? lists[tab].map(x => (
          <div className="aq-row" key={x.id}><DateChip iso={x.appointment.time} />
            <div className="aq-row__main"><b>{x.business_name}</b><span>{x.appointment.label || 'Walkthrough'} · {x.contact_name || '—'}</span></div>
            <span className="aq-time">🕒 {clock(x.appointment.time)}</span>{apptPill(x.appointment)}</div>
        )) : <div className="aq-empty">Nothing here yet.</div>}
      </section>
    </div>
  )
}

// ── Pipeline ────────────────────────────────────────────────────────────
export function Pipeline() {
  const { ov, list } = useOps()
  const f = ov.client_funnel
  const chev = [['Researched', f.real], ['Contacted', f.contacted], ['Interested', f.interested], ['Appointment', f.booked], ['Won', f.won]]
  const rows = (list?.items || []).filter(x => !x.is_practice && ['interested', 'appointment_booked', 'appointment_held', 'no_show', 'quote_issued', 'won', 'lost'].includes(x.stage))
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Opportunity Pipeline</h1>
      <p className="aq-lede">Track leads from first contact to closed contract. Real prospects only.</p>
      <div className="aq-chevrons">{chev.map(([l, v]) => <div className="aq-chev" key={l}>{l}<b>{v}</b></div>)}</div>
      <section className="aq-card aq-scroll">
        <table className="aq-table"><thead><tr><th>Opportunity</th><th>Type</th><th>Value</th><th>Stage</th></tr></thead>
          <tbody>{rows.length ? rows.map(x => (
            <tr key={x.id}><td><b>{x.business_name}</b></td><td>{x.segment_label || '—'}</td>
              <td>{x.monthly_value != null ? money(x.monthly_value, '/mo') : x.quote_amount != null ? money(x.quote_amount) : '—'}</td>
              <td><Pill stage={x.stage} label={x.stage_label} /></td></tr>
          )) : <tr><td colSpan="4" className="aq-empty">No opportunities have reached Interested yet.</td></tr>}</tbody></table>
      </section>
    </div>
  )
}

// ── Worker Health ───────────────────────────────────────────────────────
export function Health() {
  const { ov, orgId } = useOps()
  const [fb] = useLoad(`/acquisition-ops/${orgId}/feedback`)
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Worker Health</h1>
      <p className="aq-lede">What runs, what it costs and what failed — stated as it is.</p>
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Workers</h2>
          {ov.workers.map(w => (
            <div className="aq-row" key={w.key}><div className="aq-row__main"><b>{w.label}</b>
              <span>{w.state === 'on_demand' ? `Runs on demand · ${w.runs} runs · ${w.errors} failed${w.last_run ? ' · last ' + when(w.last_run) : ''}` : w.state === 'off' ? 'Built; switched off on the server' : `Planned in ${w.phase}`}</span></div>
              <span className={`aq-pill ${w.state === 'on_demand' ? 'aq-pill--ok' : ''}`}>{w.state === 'on_demand' ? 'Working' : w.state === 'off' ? 'Off' : 'Not built'}</span></div>))}
        </section>
        <section className="aq-card"><h2>Sending &amp; spend</h2>
          <dl className="aq-dl">
            <dt>Outbound</dt><dd>{ov.sending.note}</dd>
            <dt>Workspace texts</dt><dd>{ov.sending.workspace_texts_on ? 'On' : 'Off'}</dd>
            <dt>Provider calls</dt><dd>{ov.spend.provider_calls}</dd>
            <dt>AI calls</dt><dd>{ov.spend.ai_calls}</dd>
            <dt>Spend</dt><dd>${ov.spend.usd.toFixed(2)}</dd>
          </dl>
        </section>
      </div>
      <section className="aq-card" style={{ marginTop: 14 }}><h2>Client feedback</h2>
        {!fb ? <Loading /> : fb.items.length ? fb.items.map(i => (
          <div className="aq-row" key={i.id}><div className="aq-row__main"><b>{i.body}</b><span>{i.by} · {i.rating.replace('_', ' ')} · {when(i.at)}</span></div></div>
        )) : <div className="aq-empty">No feedback yet.</div>}
      </section>
    </div>
  )
}

// ── Settings ────────────────────────────────────────────────────────────
export function OpsSettings() {
  const { ov, orgId, reload } = useOps()
  const p = ov.profile
  const [reason, setReason] = useState('')
  const [excl, setExcl] = useState((p.exclusions || []).join(', '))
  const [email, setEmail] = useState('')
  const [addr, setAddr] = useState(p.mailing_address || '')
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  const run = async (fn, ok) => { setFail(null); try { await fn(); say(ok); reload() } catch (e) { setFail(errText(e)) } }
  return (
    <div className="aq-page">{toast}
      <h1 className="aq-h1">Settings</h1>
      <p className="aq-lede">Pilot controls, the never-contact list and who can operate this pilot.</p>
      {fail && <div className="aq-err">{fail}</div>}
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Emergency pause</h2>
          <p className="aq-lede" style={{ marginBottom: 10 }}>{p.paused ? `Paused ${when(p.paused_at)}: ${p.paused_reason || ''}.` : 'Not paused. Real prospects can be emailed (one at a time, by a person pressing Send).'} Pilot clock: {p.pilot_started_at ? `started ${when(p.pilot_started_at)}` : 'not started — it starts only when live operations are verified.'}</p>
          {p.paused ? <button className="aq-btn" onClick={() => run(() => api.post(`/acquisition-ops/${orgId}/resume`, {}), 'Unpaused')}>Unpause</button> : (
            <div className="aq-actions"><input placeholder="Why pause? (required)" value={reason} onChange={e => setReason(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
              <button className="aq-btn aq-btn--danger" onClick={() => run(() => api.post(`/acquisition-ops/${orgId}/pause`, { reason }), 'Paused')}>Emergency pause</button></div>)}
        </section>
        <section className="aq-card"><h2>Never contact</h2>
          <div className="aq-field"><textarea value={excl} onChange={e => setExcl(e.target.value)} placeholder="existing-client.com, name@company.com" />
            <small>The client’s existing accounts and anyone who asked not to be contacted. Domains cover their sub-domains.</small></div>
          <button className="aq-btn aq-btn--primary aq-btn--sm" onClick={() => run(() => api.put(`/acquisition-ops/${orgId}/profile`, { exclusions: excl }), 'Saved')}>Save list</button>
        </section>
        <section className="aq-card"><h2>Business mailing address</h2>
          <div className="aq-field"><input value={addr} onChange={e => setAddr(e.target.value)} placeholder="Street, city, state, ZIP" />
            <small>Printed at the bottom of every outreach email (the law requires it). Real prospects can't be emailed without it.</small></div>
          <button className="aq-btn aq-btn--primary aq-btn--sm" onClick={() => run(() => api.put(`/acquisition-ops/${orgId}/profile`, { mailing_address: addr }), 'Saved')}>Save address</button>
        </section>
        <section className="aq-card"><h2>Email sending</h2>
          <p className="aq-lede">{ov.sending.email_enabled ? `On. From ${ov.sending.from}, up to ${ov.sending.daily_cap} a day (${ov.sending.sent_last_24h} in the last 24 hours).` : 'Off on the server. Nothing can be sent.'}</p>
          <small style={{ color: 'var(--aq-sub)' }}>Practice prospects can only be emailed at: {(ov.sending.test_recipients || []).join(', ') || 'none set'}</small>
        </section>
      </div>
      {ov.is_god && (
        <section className="aq-card" style={{ marginTop: 14 }}><h2>Operators (owner only)</h2>
          <p className="aq-lede" style={{ marginBottom: 8 }}>Operators run this pilot’s screens. It is not God Mode and opens nothing else.</p>
          {ov.operators.length ? ov.operators.map(o => (
            <div className="aq-row" key={o.user_id}><div className="aq-row__main"><b>{o.name}</b><span>{o.email}</span></div>
              <button className="aq-btn aq-btn--sm" onClick={() => run(() => api.delete(`/god/acquisition/${orgId}/operators/${o.user_id}`), 'Access removed')}>Remove</button></div>
          )) : <div className="aq-empty">No operators yet.</div>}
          <div className="aq-actions"><input placeholder="Login email" value={email} onChange={e => setEmail(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
            <button className="aq-btn aq-btn--primary aq-btn--sm" disabled={!email.trim()} onClick={() => run(() => api.post(`/god/acquisition/${orgId}/operators`, { email }), 'Operator access granted').then(() => setEmail(''))}>Grant operator access</button></div>
        </section>
      )}
    </div>
  )
}

// ── Lead Discovery (P2) ─────────────────────────────────────────────────
export function Discovery() {
  const { ov, orgId, reload } = useOps()
  const [runs, rErr, reloadRuns] = useLoad(`/acquisition-ops/${orgId}/discovery/runs`)
  const areas = ov.profile.service_area || []
  const wanted = ov.segments.filter(s => (ov.profile.segments || []).includes(s.key))
  const [f, setF] = useState({ segment: wanted[0]?.key || '', city: areas[0] || '', max_results: 10, is_practice: false })
  const [plan, setPlan] = useState(null)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  const set = k => e => { setPlan(null); setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }) }
  const check = async () => {
    setFail(null)
    try { setPlan(await api.post(`/acquisition-ops/${orgId}/discovery/plan`, { ...f, max_results: Number(f.max_results) })) } catch (e) { setFail(errText(e)) }
  }
  const go = async () => {
    setBusy(true); setFail(null)
    try {
      const r = await api.post(`/god/acquisition/${orgId}/discovery/run`, { ...f, max_results: Number(f.max_results) })
      say(`Found ${r.found}, added ${r.added}`); setPlan(null); reloadRuns(); reload()
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="aq-page">{toast}
      <h1 className="aq-h1">Lead Discovery</h1>
      <p className="aq-lede">Find DFW businesses of the types the client wants, in small metered batches. Every fact keeps its source; nobody is contacted.</p>
      <div className="aq-grid aq-split">
        <section className="aq-card"><h2>Plan a search</h2>
          <div className="aq-grid aq-two">
            <div className="aq-field"><label>Business type</label><select value={f.segment} onChange={set('segment')}>
              {wanted.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}</select></div>
            <div className="aq-field"><label>City</label><select value={f.city} onChange={set('city')}>
              {areas.map(a => <option key={a}>{a}</option>)}</select></div>
            <div className="aq-field"><label>How many (max {runs?.limits?.max_results_per_run || 20})</label>
              <input inputMode="numeric" value={f.max_results} onChange={set('max_results')} /></div>
            <div className="aq-field"><label>&nbsp;</label><div className="aq-checks"><label><input type="checkbox" checked={f.is_practice} onChange={set('is_practice')} />Practice cohort</label></div></div>
          </div>
          <div className="aq-actions"><button className="aq-btn aq-btn--primary" onClick={check}>Check plan &amp; cost</button>
            {plan?.can_run && ov.is_god && <button className="aq-btn aq-btn--danger" disabled={busy} onClick={go}>{busy ? 'Searching…' : `Run search (up to $${plan.max_cost_usd})`}</button>}</div>
          {fail && <div className="aq-err">{fail}</div>}
          {plan && (
            <div className="aq-detail" style={{ marginTop: 14 }}>
              <dl className="aq-dl">
                <dt>Search</dt><dd>{plan.query}</dd>
                <dt>Requests</dt><dd>{plan.requests.search} search + up to {plan.requests.details_max} detail lookups</dd>
                <dt>Most it can cost</dt><dd>{plan.max_cost_usd == null ? 'Unknown — prices not configured' : `$${plan.max_cost_usd}`}</dd>
                <dt>Runs (24h)</dt><dd>{plan.runs_last_24h} of {plan.limits.max_runs_per_day}</dd>
              </dl>
              {plan.can_run ? <p className="aq-pill aq-pill--ok" style={{ marginTop: 10 }}>Ready{ov.is_god ? '' : ' — the platform owner runs paid searches'}</p>
                : <ul className="aq-checklist" style={{ marginTop: 8 }}>{plan.refused_because.map(r => <li key={r}><span className="aq-tick" />{r}</li>)}</ul>}
            </div>
          )}
        </section>
        <section className="aq-card"><h2>How it works</h2>
          <ul className="aq-checklist">
            <li><span className="aq-tick on" />Reuses the existing Google Places search</li>
            <li><span className="aq-tick on" />Duplicates, directories and never-contact entries are dropped</li>
            <li><span className="aq-tick on" />Listing facts are verified at source; business type is marked as an inference</li>
            <li><span className="aq-tick on" />Each business gets a score with its reasons</li>
            <li><span className="aq-tick on" />Every request is counted and priced</li>
          </ul>
        </section>
      </div>
      <section className="aq-card" style={{ marginTop: 14 }}><h2>Runs</h2>
        {rErr && <div className="aq-err">{errText(rErr)}</div>}
        {!runs ? <Loading /> : runs.items.length ? runs.items.map(r => (
          <div className="aq-row" key={r.id}><div className="aq-row__main"><b>{r.segment_label} · {r.city}{r.is_practice ? ' (practice)' : ''}</b>
            <span>{when(r.at)} · found {r.found}, added {r.added}, duplicates {r.duplicates}, excluded {r.excluded}, junk {r.junk} · {r.search_requests + r.detail_requests} requests · {r.cost_usd == null ? 'cost unknown' : `$${r.cost_usd}`}{r.error ? ` · ${r.error}` : ''}</span></div>
            <span className={`aq-pill ${r.status === 'done' ? 'aq-pill--ok' : ''}`}>{r.status}</span></div>
        )) : <div className="aq-empty">No discovery runs yet.</div>}
      </section>
    </div>
  )
}
