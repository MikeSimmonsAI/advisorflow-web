/**
 * MAX LIFE COMMAND — write forms shared by the prospect record and the lists.
 *
 *   POST  /agency/appointments            {prospect_id, agent_id, type, medium, starts_at, notes}
 *   POST  /agency/applications            {prospect_id, agent_id, carrier?, product_category?, notes?}
 *   PATCH /agency/prospects/{id}/profile  STATED facts only (what the prospect said)
 *
 * An advisor creates work for themself (the server enforces it: 403 otherwise);
 * a manager may pick any agent with an agency profile. Nothing here contacts a
 * prospect, books an external calendar, or submits to a carrier.
 */
import { useEffect, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { api, fetchAndStoreBranding, getBranding, getCurrentUser } from '../../api/client'
import { readAuthority } from '../../auth/workspaceAuthority'
import { agencyVerticalFor } from '../../verticals/agencyVertical'
import { NEED_LABELS, agencyLanding, humanize, localToIso, pageInfo, profileForm, profilePatch } from './agencyFormat'
import { ErrorState, Notice, useAction, useAgency } from './agencyUi'

export const APPT_TYPES = ['discovery', 'needs_analysis', 'application', 'policy_review', 'follow_up']
export const APPT_MEDIUMS = ['phone', 'video', 'in_person']
export const PRODUCT_CATEGORIES = Object.keys(NEED_LABELS)

/** Agent <select>: managers choose; an advisor is fixed to themself. */
function AgentSelect({ value, onChange }) {
  const isManager = readAuthority().isManager
  const agents = useAgency(isManager ? '/agency/agents?per_page=200' : null)
  const me = getCurrentUser()
  if (!isManager) {
    return <label className="ag-field"><span>Agent</span><input className="ag-input" value={me?.full_name || 'You'} disabled aria-label="Agent" /></label>
  }
  return (
    <label className="ag-field"><span>Agent</span>
      <select className="ag-input" value={value || ''} onChange={e => onChange(e.target.value)} aria-label="Agent" data-testid="agent-select">
        <option value="">Choose an agent…</option>
        {(agents.data?.items || []).map(a => <option key={a.user_id} value={a.user_id}>{a.name}{a.available ? '' : ' (unavailable)'}</option>)}
      </select>
    </label>
  )
}

/** Search-as-you-type prospect picker for the list screens. */
function ProspectPicker({ value, onChange }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 250); return () => clearTimeout(t) }, [q])
  const list = useAgency(`/agency/prospects?per_page=8${term ? `&q=${encodeURIComponent(term)}` : ''}`)
  if (value) {
    return (
      <div className="ag-field"><span>Prospect</span>
        <div className="ag-row"><strong>{value.name}</strong><button type="button" className="ag-btn ag-btn--sm ag-btn--ghost" onClick={() => onChange(null)}>Change</button></div>
      </div>)
  }
  return (
    <div className="ag-field ag-picker"><span>Prospect</span>
      <input className="ag-input" value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, email, phone" aria-label="Find prospect" data-testid="prospect-search" />
      <ul className="ag-picker__list">
        {(list.data?.items || []).map(p => (
          <li key={p.id}><button type="button" className="ag-picker__opt" onClick={() => onChange({ id: p.id, name: p.name, agentId: p.assigned_agent?.id })}>
            {p.name || 'Unnamed'} <span className="ag-muted ag-small">{[p.state, p.assigned_agent?.name].filter(Boolean).join(' · ')}</span></button></li>))}
        {list.data && !list.data.items.length ? <li className="ag-muted ag-small">No match.</li> : null}
      </ul>
    </div>)
}

function useAgentDefault(initial) {
  const isManager = readAuthority().isManager
  const me = getCurrentUser()
  const [agentId, setAgentId] = useState(isManager ? (initial || '') : (me?.id || ''))
  return [isManager ? agentId : (me?.id || ''), setAgentId]
}

/** New appointment. `prospect` = {id,name,agentId} when opened from a record; omitted on the list screen. */
export function NewAppointmentForm({ prospect, onCreated, onCancel }) {
  const [who, setWho] = useState(prospect || null)
  const [agentId, setAgentId] = useAgentDefault(prospect?.agentId)
  const [f, setF] = useState({ type: 'discovery', medium: 'phone', starts_at: '', notes: '' })
  const [run, busy, error, setError] = useAction()
  useEffect(() => { if (who?.agentId && !agentId) setAgentId(who.agentId) }, [who]) // eslint-disable-line react-hooks/exhaustive-deps
  const startsIso = localToIso(f.starts_at)
  const ok = who && agentId && startsIso
  const submit = async (e) => {
    e.preventDefault()
    if (!ok) { setError(new Error('Choose a prospect, an agent and a start time.')); return }
    const r = await run(() => api.post('/agency/appointments', {
      prospect_id: who.id, agent_id: agentId, type: f.type, medium: f.medium, starts_at: startsIso, notes: f.notes.trim() || undefined,
    }))
    if (r) onCreated?.(r)
  }
  return (
    <form className="ag-form" onSubmit={submit} data-testid="new-appointment">
      <div className="ag-fields">
        {prospect ? null : <ProspectPicker value={who} onChange={setWho} />}
        <AgentSelect value={agentId} onChange={setAgentId} />
        <label className="ag-field"><span>Type</span>
          <select className="ag-input" value={f.type} onChange={e => setF({ ...f, type: e.target.value })} aria-label="Appointment type">
            {APPT_TYPES.map(t => <option key={t} value={t}>{humanize(t)}</option>)}</select></label>
        <label className="ag-field"><span>Medium</span>
          <select className="ag-input" value={f.medium} onChange={e => setF({ ...f, medium: e.target.value })} aria-label="Medium">
            {APPT_MEDIUMS.map(t => <option key={t} value={t}>{humanize(t)}</option>)}</select></label>
        <label className="ag-field"><span>Starts</span>
          <input className="ag-input" type="datetime-local" value={f.starts_at} onChange={e => setF({ ...f, starts_at: e.target.value })} aria-label="Starts at" required /></label>
        <label className="ag-field ag-field--wide"><span>Notes</span>
          <input className="ag-input" value={f.notes} onChange={e => setF({ ...f, notes: e.target.value })} placeholder="Optional" aria-label="Appointment notes" /></label>
      </div>
      <div className="ag-row">
        <button className="ag-btn" type="submit" disabled={busy || !ok}>{busy ? 'Saving…' : 'Create appointment'}</button>
        {onCancel ? <button className="ag-btn ag-btn--ghost" type="button" onClick={onCancel}>Cancel</button> : null}
        <span className="ag-muted ag-small">Recorded as pending; nothing is sent to the prospect and no external calendar is booked.</span>
      </div>
      {error ? <ErrorState error={error} /> : null}
    </form>
  )
}

/** Start an application (status draft). */
export function NewApplicationForm({ prospect, onCreated, onCancel }) {
  const [who, setWho] = useState(prospect || null)
  const [agentId, setAgentId] = useAgentDefault(prospect?.agentId)
  const [f, setF] = useState({ carrier: '', product_category: '', notes: '' })
  const [run, busy, error, setError] = useAction()
  useEffect(() => { if (who?.agentId && !agentId) setAgentId(who.agentId) }, [who]) // eslint-disable-line react-hooks/exhaustive-deps
  const ok = who && agentId
  const submit = async (e) => {
    e.preventDefault()
    if (!ok) { setError(new Error('Choose a prospect and an agent.')); return }
    const r = await run(() => api.post('/agency/applications', {
      prospect_id: who.id, agent_id: agentId, carrier: f.carrier.trim() || undefined,
      product_category: f.product_category || undefined, notes: f.notes.trim() || undefined,
    }))
    if (r) onCreated?.(r)
  }
  return (
    <form className="ag-form" onSubmit={submit} data-testid="new-application">
      <div className="ag-fields">
        {prospect ? null : <ProspectPicker value={who} onChange={setWho} />}
        <AgentSelect value={agentId} onChange={setAgentId} />
        <label className="ag-field"><span>Product category</span>
          <select className="ag-input" value={f.product_category} onChange={e => setF({ ...f, product_category: e.target.value })} aria-label="Product category">
            <option value="">Not decided</option>{PRODUCT_CATEGORIES.map(k => <option key={k} value={k}>{humanize(k)}</option>)}</select></label>
        <label className="ag-field"><span>Carrier</span>
          <input className="ag-input" value={f.carrier} onChange={e => setF({ ...f, carrier: e.target.value })} placeholder="As recorded (optional)" aria-label="Carrier" /></label>
        <label className="ag-field ag-field--wide"><span>Notes</span>
          <input className="ag-input" value={f.notes} onChange={e => setF({ ...f, notes: e.target.value })} placeholder="Optional" aria-label="Application notes" /></label>
      </div>
      <div className="ag-row">
        <button className="ag-btn" type="submit" disabled={busy || !ok}>{busy ? 'Saving…' : 'Start application'}</button>
        {onCancel ? <button className="ag-btn ag-btn--ghost" type="button" onClick={onCancel}>Cancel</button> : null}
        <span className="ag-muted ag-small">Starts as a draft. Nothing is submitted to a carrier.</span>
      </div>
      {error ? <ErrorState error={error} /> : null}
    </form>
  )
}

const CONTACT = ['phone', 'sms', 'email', 'video', 'in_person']
const TRI = [['', 'Not stated'], ['yes', 'Yes'], ['no', 'No']]

/** Edit the family profile — ONLY what the prospect said. */
export function ProfileEditor({ prospectId, profile, onSaved, onCancel }) {
  const [f, setF] = useState(() => profileForm(profile))
  const [run, busy, error] = useAction()
  const patch = profilePatch(f, profile)
  const changed = Object.keys(patch).length > 0
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const toggleNeed = (k) => setF({ ...f, need_categories: f.need_categories.includes(k) ? f.need_categories.filter(x => x !== k) : [...f.need_categories, k] })
  const save = async (e) => {
    e.preventDefault()
    if (!changed) return
    const r = await run(() => api.patch(`/agency/prospects/${prospectId}/profile`, patch))
    if (r) onSaved?.(r)
  }
  return (
    <form className="ag-form" onSubmit={save} data-testid="profile-editor">
      <p className="ag-stated" role="note"><strong>As stated by the prospect.</strong> Record only what the prospect told you. Leave a field blank (“Not stated”) rather than guess — no assumptions about health, income, eligibility or suitability.</p>
      <div className="ag-fields">
        <label className="ag-field"><span>Adults in household</span><input className="ag-input" type="number" min="0" value={f.household_adults} onChange={set('household_adults')} aria-label="Adults in household" /></label>
        <label className="ag-field"><span>Children</span><input className="ag-input" type="number" min="0" value={f.household_children} onChange={set('household_children')} aria-label="Children" /></label>
        <label className="ag-field"><span>Preferred contact</span>
          <select className="ag-input" value={f.preferred_contact} onChange={set('preferred_contact')} aria-label="Preferred contact">
            <option value="">Not stated</option>{CONTACT.map(c => <option key={c} value={c}>{humanize(c)}</option>)}</select></label>
        <label className="ag-field ag-field--wide"><span>Household notes (their words)</span><input className="ag-input" value={f.household_notes} onChange={set('household_notes')} aria-label="Household notes" /></label>
      </div>
      <div className="ag-k">Needs they raised</div>
      <div className="ag-chips">
        {PRODUCT_CATEGORIES.map(k => <button key={k} type="button" className={`ag-chip${f.need_categories.includes(k) ? ' ag-chip--on' : ''}`} onClick={() => toggleNeed(k)} aria-pressed={f.need_categories.includes(k)}>{humanize(k)}</button>)}
      </div>
      <div className="ag-fields">
        <label className="ag-field ag-field--wide"><span>Financial goals (one per line)</span><textarea className="ag-input ag-textarea" rows={3} value={f.financial_goals} onChange={set('financial_goals')} aria-label="Financial goals" /></label>
        <label className="ag-field ag-field--wide"><span>Concerns they stated (one per line)</span><textarea className="ag-input ag-textarea" rows={3} value={f.stated_concerns} onChange={set('stated_concerns')} aria-label="Stated concerns" /></label>
        {[['retirement_interest', 'Retirement interest'], ['business_owner_interest', 'Business-owner interest'], ['living_benefits_interest', 'Living-benefits interest']].map(([k, l]) => (
          <label key={k} className="ag-field"><span>{l}</span>
            <select className="ag-input" value={f[k]} onChange={set(k)} aria-label={l}>{TRI.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>))}
      </div>
      <div className="ag-row">
        <button className="ag-btn" type="submit" disabled={busy || !changed}>{busy ? 'Saving…' : 'Save stated facts'}</button>
        {onCancel ? <button className="ag-btn ag-btn--ghost" type="button" onClick={onCancel}>Cancel</button> : null}
        {!changed ? <span className="ag-muted ag-small">No changes.</span> : null}
      </div>
      {error ? <ErrorState error={error} /> : null}
    </form>
  )
}

/** Prev / next for a paginated {items,total,page,per_page} answer. */
export function Pager({ data, onPage }) {
  const p = pageInfo(data)
  if (!data || p.total === 0) return null
  return (
    <div className="ag-pager" data-testid="pager">
      <span className="ag-muted ag-small">{p.from}–{p.to} of {p.total}</span>
      <button type="button" className="ag-btn ag-btn--sm ag-btn--ghost" disabled={!p.hasPrev} onClick={() => onPage(p.page - 1)}>← Prev</button>
      <span className="ag-small">Page {p.page} of {p.pages}</span>
      <button type="button" className="ag-btn ag-btn--sm ag-btn--ghost" disabled={!p.hasNext} onClick={() => onPage(p.page + 1)}>Next →</button>
    </div>
  )
}

/**
 * LANDING. Wraps the generic Overview at "/" and "/workspace/:id": an agency
 * workspace (explicit `insurance_agency` entitlement) is sent to the Command
 * Center instead. The cached branding decides instantly when it is for this
 * workspace; otherwise the fresh answer decides, and until then the wrapped
 * page renders as before (nothing regresses for any other workspace).
 */
export function AgencyHomeGate({ children }) {
  const navigate = useNavigate()
  const here = typeof window !== 'undefined' ? window.location.pathname : '/'
  const cached = getBranding()
  const pathOrg = (here.match(/^\/workspace\/([^/]+)/) || [])[1]
  const cachedFits = cached && (!pathOrg || cached.organization_id === pathOrg)
  const instant = cachedFits ? agencyLanding(!!agencyVerticalFor(cached), here) : null
  useEffect(() => {
    if (instant) return undefined
    let live = true
    fetchAndStoreBranding({ applyTheme: false }).then(b => {
      const to = live ? agencyLanding(!!agencyVerticalFor(b), window.location.pathname) : null
      if (to) navigate(to, { replace: true })
    }).catch(() => {})
    return () => { live = false }
  }, [instant, navigate])
  if (instant) return <Navigate to={instant} replace />
  return children
}

export function StatedNote() {
  return <Notice>Profile fields are what the prospect stated. They are not verified and are not an assessment of need, health or eligibility.</Notice>
}
