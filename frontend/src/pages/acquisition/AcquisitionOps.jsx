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
import { api } from '../../api/client'
import { DateChip, Icon, Kpi, Loading, Logo, Pill, apptPill, clock, errText, initials, money, useFont, useLoad, useToast, when } from './ui'
import './acquisition.css'

const Ctx = createContext(null)
const useOps = () => useContext(Ctx)
export const OPS = '/acquisition-ops'
const pct = v => (v == null ? '—' : Math.round(v * 100) + '%')

const NAV = [
  { to: OPS, label: 'Control Center', icon: 'dashboard', end: true },
  { to: `${OPS}/discovery`, label: 'Lead Discovery', icon: 'search', soon: 'P2' },
  { to: `${OPS}/prospects`, label: 'Prospects', icon: 'users' },
  { to: `${OPS}/outreach`, label: 'Outreach', icon: 'mail', soon: 'P3' },
  { to: `${OPS}/responses`, label: 'Responses', icon: 'reply', soon: 'P3' },
  { to: `${OPS}/appointments`, label: 'Appointments', icon: 'calendar' },
  { to: `${OPS}/pipeline`, label: 'Pipeline', icon: 'pipeline' },
  { to: `${OPS}/health`, label: 'Worker Health', icon: 'heart' },
  { to: `${OPS}/settings`, label: 'Settings', icon: 'settings' },
]

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
      <h1 className="aq-h1">Set up the pilot client</h1>
      <p className="aq-lede">Paste the organization ID of the client’s workspace. The pilot is limited to one cleaning company and starts paused. Nothing is sent.</p>
      <div className="aq-field"><label htmlFor="aq-org">Organization ID</label><input id="aq-org" value={orgId} onChange={e => setOrgId(e.target.value)} /></div>
      {fail && <div className="aq-err">{fail}</div>}
      <button className="aq-btn aq-btn--primary" disabled={busy || !orgId.trim()} onClick={go}>{busy ? 'Setting up…' : 'Enable acquisition pilot'}</button>
    </div></div>
  )
}

export default function AcquisitionOps() {
  useFont()
  const [clients, cErr, reloadClients] = useLoad('/acquisition-ops/clients')
  const orgId = clients?.items?.[0]?.organization_id
  const [ov, oErr, reloadOv] = useLoad(orgId ? `/acquisition-ops/${orgId}/overview` : null)
  const [list, lErr, reloadList] = useLoad(orgId ? `/acquisition-ops/${orgId}/prospects` : null)
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  useEffect(() => { setOpen(false) }, [loc.pathname])

  const err = cErr || oErr
  if (err) {
    return (
      <div className="aq aq-gate"><div className="aq-card">
        <h1 className="aq-h1">{err.status === 403 ? 'No acquisition access' : 'This page couldn’t load'}</h1>
        <p className="aq-lede">{err.status === 403 ? 'This login has not been given acquisition operator access. The platform owner grants it.' : errText(err)}</p>
        <a className="aq-btn" href="/workspaces">Back to programs</a>
      </div></div>
    )
  }
  if (!clients) return <div className="aq"><Loading label="Opening the control center…" /></div>
  if (!clients.items.length) return clients.is_god ? <Enable onDone={reloadClients} /> : <div className="aq"><Loading /></div>
  if (!ov) return <div className="aq"><Loading label="Opening the control center…" /></div>

  const reload = () => { reloadOv(); reloadList() }
  const p = ov.profile
  const me = ov.me?.name || ''
  return (
    <Ctx.Provider value={{ ov, list, listErr: lErr, reload, orgId }}>
      <div className="aq aq-app">
        {open && <div className="aq-scrim" onClick={() => setOpen(false)} />}
        <aside className={`aq-side ${open ? 'open' : ''}`} aria-label="Operations navigation">
          <Logo />
          <div className="aq-side__label">Operations</div>
          <nav className="aq-nav">
            {NAV.map(it => (
              <NavLink key={it.to} to={it.to} end={it.end}>
                <Icon name={it.icon} />{it.label}{it.soon && <span className="aq-nav__soon">{it.soon}</span>}
              </NavLink>
            ))}
          </nav>
          <div className="aq-help"><b>Development stage</b>No discovery, outreach or booking worker runs. Nothing can be sent.</div>
        </aside>
        <div className="aq-main">
          <header className="aq-top">
            <button className="aq-btn aq-btn--sm aq-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
            <span className="aq-top__title">Acquisition Control Center</span>
            <div className="aq-top__right">
              {!ov.is_god && <a className="aq-btn aq-btn--sm" href="/workspaces">Switch program</a>}
              <span className="aq-me"><span className="aq-avatar">{initials(me)}</span>
                <div><b>{me.split(' ')[0] || me}</b><span>{ov.is_god ? 'Owner' : 'Operator'}</span></div></span>
            </div>
          </header>
          <div className="aq-band">
            <div><h1>Acquisition Control Center</h1><p>{ov.organization_name} Pilot • Lead Generation &amp; Appointment Setting</p></div>
            <div className="aq-band__right">
              <span className={`aq-badge ${p.paused ? 'aq-badge--paused' : ''}`}><Icon name={p.paused ? 'pause' : 'calendar'} size={15} />
                {p.paused ? 'Paused' : ov.progress.started ? `Pilot day ${ov.progress.day} of 30` : 'Pilot not started'}</span>
            </div>
          </div>
          <Suspense fallback={<Loading />}><Outlet /></Suspense>
        </div>
      </div>
    </Ctx.Provider>
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

export function Soon({ title, phase, text }) {
  return (
    <div className="aq-page"><section className="aq-card aq-soon">
      <b>{title}</b><p className="aq-lede" style={{ margin: '0 auto' }}>{text}</p>
      <p style={{ marginTop: 12 }}><span className="aq-pill">Planned in {phase}</span></p>
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
      <div className="aq-tabs">{[['overview', 'Overview'], ['signals', `Signals (${x.evidence.length})`], ['contacts', 'Contacts'], ['actions', 'Actions']].map(([k, l]) => (
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
        </dl>
      )}
      {tab === 'actions' && (
        <div className="aq-actions">
          <select value={stage} onChange={e => setStage(e.target.value)}><option value="">Move to stage…</option>
            {ov.stages.filter(s => !['contacted', 'replied'].includes(s.key)).map(s => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
          <input placeholder="Note / reason (required for Interested and Not a fit)" style={{ flex: 1, minWidth: 220 }} value={note} onChange={e => setNote(e.target.value)} />
          <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!stage} onClick={move}>Move</button>
          <small style={{ color: 'var(--aq-sub)', width: '100%' }}>Contacted and Replied are recorded from real messages only.</small>
        </div>
      )}
      {fail && <div className="aq-err">{fail}</div>}
    </div>
  )
}

function AddProspect() {
  const { ov, orgId, reload } = useOps()
  const blank = { business_name: '', segment: '', website: '', source_url: '', city: '', phone: '', contact_first_name: '',
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
          {input('website', 'Website')}{input('source_url', 'Where it was found (URL)')}{input('city', 'City')}</div>
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
                <span>{[x.segment_label, x.city, x.domain].filter(Boolean).join(' · ')} · {x.evidence.length} evidence</span></div>
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
            <div className="aq-row" key={w.key}><div className="aq-row__main"><b>{w.label}</b><span>Planned in {w.phase} · {w.runs} runs · {w.errors} errors</span></div>
              <span className="aq-pill">Not built</span></div>))}
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
          <p className="aq-lede" style={{ marginBottom: 10 }}>{p.paused ? `Paused ${when(p.paused_at)}: ${p.paused_reason || ''}.` : 'Not paused. No worker exists yet, so nothing runs.'} Pilot clock: {p.pilot_started_at ? `started ${when(p.pilot_started_at)}` : 'not started — it starts only when live operations are verified.'}</p>
          {p.paused ? <button className="aq-btn" onClick={() => run(() => api.post(`/acquisition-ops/${orgId}/resume`, {}), 'Unpaused')}>Unpause</button> : (
            <div className="aq-actions"><input placeholder="Why pause? (required)" value={reason} onChange={e => setReason(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
              <button className="aq-btn aq-btn--danger" onClick={() => run(() => api.post(`/acquisition-ops/${orgId}/pause`, { reason }), 'Paused')}>Emergency pause</button></div>)}
        </section>
        <section className="aq-card"><h2>Never contact</h2>
          <div className="aq-field"><textarea value={excl} onChange={e => setExcl(e.target.value)} placeholder="existing-client.com, name@company.com" />
            <small>The client’s existing accounts and anyone who asked not to be contacted. Domains cover their sub-domains.</small></div>
          <button className="aq-btn aq-btn--primary aq-btn--sm" onClick={() => run(() => api.put(`/acquisition-ops/${orgId}/profile`, { exclusions: excl }), 'Saved')}>Save list</button>
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
