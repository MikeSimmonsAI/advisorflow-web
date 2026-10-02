/**
 * LEAD DISTRIBUTION, APPOINTMENTS, AGENTS & PRODUCTION.
 *
 *   GET /agency/assignments?state=            board
 *   GET|PUT /agency/distribution/config       managers edit thresholds/factors
 *   POST /agency/assignments/sweep            time out expired offers → reassign / escalate
 *   GET /agency/appointments?range=&status=&needs_confirmation=   ·  PATCH /agency/appointments/{id}
 *   GET /agency/agents?with_capacity=         ·  PUT /agency/agents/{user_id}/profile (managers)
 */
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { readAuthority } from '../../auth/workspaceAuthority'
import { apiQuery, filterLabel, fmtDateTime, humanize, metric, pctWidth, relTime } from './agencyFormat'
import { AgencyPage, DemoBadge, ErrorState, FilterBar, ListState, Loading, Notice, Pill, Section, useAction, useAgency } from './agencyUi'
import { NewAppointmentForm } from './AgencyForms'

const STATES = ['offered', 'accepted', 'declined', 'timed_out', 'escalated']

function DistributionConfig({ isManager }) {
  const q = useAgency('/agency/distribution/config')
  const [form, setForm] = useState(null)
  const [run, busy, error] = useAction()
  const [saved, setSaved] = useState(false)
  useEffect(() => { if (q.data) setForm(q.data) }, [q.data])
  if (q.loading && !q.data) return <Loading />
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />
  if (!form) return null
  const num = (k, label, hint) => (
    <label className="ag-field"><span>{label}</span>
      <input className="ag-input" type="number" min="0" value={form[k] ?? ''} disabled={!isManager}
        onChange={e => { setSaved(false); setForm({ ...form, [k]: e.target.value === '' ? null : Number(e.target.value) }) }} />
      {hint ? <small>{hint}</small> : null}</label>)
  const save = async () => {
    const body = {}
    for (const k of ['acceptance_timeout_minutes', 'max_active_per_agent', 'stalled_days', 'response_target_minutes', 'review_window_days', 'workload_alert_pct']) if (form[k] != null) body[k] = form[k]
    body.factors_enabled = form.factors_enabled
    const r = await run(() => api.put('/agency/distribution/config', body))
    if (r) { setSaved(true); q.reload() }
  }
  const all = form.available_factors || form.factors_enabled || []
  return (
    <div data-testid="dist-config">
      <div className="ag-fields">
        {num('acceptance_timeout_minutes', 'Acceptance timeout (min)', 'Offer expires after this')}
        {num('max_active_per_agent', 'Max active per agent')}
        {num('response_target_minutes', 'Response target (min)')}
        {num('stalled_days', 'Application stalled after (days)')}
        {num('review_window_days', 'Annual review window (days)')}
        {num('workload_alert_pct', 'Workload alert (%)')}
      </div>
      <div className="ag-k">Factors considered</div>
      <div className="ag-chips">
        {all.map(f => {
          const on = (form.factors_enabled || []).includes(f)
          return <button key={f} type="button" disabled={!isManager} className={`ag-chip${on ? ' ag-chip--on' : ''}`}
            onClick={() => { setSaved(false); setForm({ ...form, factors_enabled: on ? form.factors_enabled.filter(x => x !== f) : [...form.factors_enabled, f] }) }}>{humanize(f)}</button>
        })}
      </div>
      {isManager ? <div className="ag-row"><button className="ag-btn" disabled={busy} onClick={save}>Save configuration</button>{saved ? <span className="ag-ok">Saved.</span> : null}</div>
        : <Notice>Only managers can change distribution settings.</Notice>}
      {error ? <ErrorState error={error} /> : null}
    </div>
  )
}

export function Distribution() {
  const isManager = readAuthority().isManager
  const [params, setParams] = useSearchParams()
  const state = params.get('state') || ''
  const board = useAgency(`/agency/assignments${apiQuery(params, ['state', 'agent_id'], { per_page: 200 })}`)
  const unassigned = useAgency('/agency/prospects?unassigned=true&per_page=100')
  const [run, busy, error] = useAction()
  const [sweep, setSweep] = useState(null)
  const doSweep = async () => {
    const r = await run(() => api.post('/agency/assignments/sweep', {}))
    if (r) {
      const res = Array.isArray(r.results) ? r.results : []
      setSweep({ ...r, reassigned: res.filter(x => x.next && x.next.state === 'offered').length, escalated: res.filter(x => x.next && x.next.state === 'escalated').length })
      board.reload(); unassigned.reload()
    }
  }
  const items = board.data?.items || []
  const cols = state ? [state] : STATES
  return (
    <AgencyPage title="Lead Distribution" eyebrow="Client Acquisition" testid="agency-distribution"
      actions={isManager ? <button className="ag-btn" disabled={busy} onClick={doSweep} data-testid="sweep">{busy ? 'Sweeping…' : 'Run timeout sweep'}</button> : null}>
      {sweep ? <p className="ag-ok" role="status">Sweep complete: {sweep.timed_out ?? sweep.count ?? 0} offer(s) timed out{sweep.reassigned != null ? `, ${sweep.reassigned} reassigned` : ''}{sweep.escalated != null ? `, ${sweep.escalated} escalated` : ''}.</p> : null}
      {error ? <ErrorState error={error} /> : null}
      <Section title="Waiting for an agent" aside={<Link className="ag-link" to="/agency/prospects?unassigned=true">All unassigned →</Link>}>
        <ListState q={unassigned} empty="Every prospect has an agent.">
          <ul className="ag-linklist ag-linklist--inline">{(unassigned.data?.items || []).map(p => (
            <li key={p.id}><Link to={`/agency/prospects/${p.id}`}>{p.name}</Link> <Pill value={p.intent_level} /> <span className="ag-muted ag-small">{p.state} · {relTime(p.created_at)}</span></li>))}
          </ul>
        </ListState>
      </Section>
      <div className="ag-toolbar">
        <div className="ag-chips">
          <button type="button" className={`ag-chip${!state ? ' ag-chip--on' : ''}`} onClick={() => setParams({})}>All states</button>
          {STATES.map(s => <button key={s} type="button" className={`ag-chip${state === s ? ' ag-chip--on' : ''}`} onClick={() => setParams({ state: s })}>{humanize(s)}</button>)}
        </div>
      </div>
      {board.loading && !board.data ? <Loading /> : board.error ? <ErrorState error={board.error} onRetry={board.reload} /> : (
        <div className="ag-board" data-testid="assignment-board" tabIndex={0} role="region" aria-label="Assignments by status">
          {cols.map(s => {
            const rows = items.filter(a => a.state === s)
            return (
              <div key={s} className="ag-board__col">
                <div className="ag-board__head"><Pill value={s} /> <span className="ag-muted">{rows.length}</span></div>
                {rows.length ? rows.map(a => (
                  <Link key={a.id} to={`/agency/prospects/${a.prospect?.id}`} className="ag-board__card">
                    <strong>{a.prospect?.name || 'Prospect'}</strong>
                    <span className="ag-small">{a.agent?.name || 'No agent'} · attempt {a.attempt}</span>
                    <span className="ag-small ag-muted">{s === 'offered' && a.expires_at ? `expires ${relTime(a.expires_at)}` : fmtDateTime(a.responded_at || a.offered_at)}</span>
                    <DemoBadge on={a.is_demo} />
                  </Link>)) : <p className="ag-muted ag-small">None</p>}
              </div>)
          })}
        </div>)}
      <Section title="Distribution configuration"><DistributionConfig isManager={isManager} /></Section>
    </AgencyPage>
  )
}

const APPT_FILTERS = ['range', 'status', 'agent_id', 'needs_confirmation']

export function Appointments() {
  const { appointmentId } = useParams()
  const [params, setParams] = useSearchParams()
  const list = useAgency(`/agency/appointments${apiQuery(params, APPT_FILTERS, { per_page: 100 })}`)
  const [run, busy, error] = useAction()
  const label = filterLabel(params, APPT_FILTERS)
  const range = params.get('range') || ''
  const setStatus = async (id, status) => { const r = await run(() => api.patch(`/agency/appointments/${id}`, { status })); if (r) list.reload() }
  const [creating, setCreating] = useState(false)
  const [flash, setFlash] = useState('')
  return (
    <AgencyPage title="Appointments" eyebrow="Client Acquisition" testid="agency-appointments"
      actions={<button type="button" className="ag-btn" onClick={() => { setFlash(''); setCreating(c => !c) }} data-testid="open-new-appointment">{creating ? 'Close' : '+ New appointment'}</button>}>
      {creating ? <Section title="New appointment"><NewAppointmentForm onCancel={() => setCreating(false)} onCreated={() => { setCreating(false); setFlash('Appointment recorded as pending.'); list.reload() }} /></Section> : null}
      {flash ? <p className="ag-ok" role="status">{flash}</p> : null}
      <div className="ag-toolbar ag-chips">
        {[['', 'All'], ['today', 'Today'], ['upcoming', 'Upcoming'], ['past', 'Past']].map(([k, l]) => (
          <button key={k} type="button" className={`ag-chip${range === k && !params.get('needs_confirmation') ? ' ag-chip--on' : ''}`} onClick={() => setParams(k ? { range: k } : {})}>{l}</button>))}
        <button type="button" className={`ag-chip${params.get('needs_confirmation') ? ' ag-chip--on' : ''}`} onClick={() => setParams({ needs_confirmation: 'true' })}>Needs confirmation</button>
      </div>
      <FilterBar label={label} clearTo="/agency/appointments" />
      {error ? <ErrorState error={error} /> : null}
      <Section>
        <ListState q={list} empty={label ? 'No appointments match this filter.' : 'No appointments scheduled.'}>
          <div className="ag-table-wrap">
            <table className="ag-table">
              <thead><tr><th>When</th><th>Prospect</th><th>Agent</th><th>Type</th><th>Status</th><th>Notes</th><th /></tr></thead>
              <tbody>{(list.data?.items || []).map(a => (
                <tr key={a.id} className={a.id === appointmentId ? 'ag-row--sel' : ''}>
                  <td>{fmtDateTime(a.starts_at)}<div className="ag-small ag-muted">{relTime(a.starts_at)}</div></td>
                  <td><Link to={`/agency/prospects/${a.prospect?.id}`}>{a.prospect?.name}</Link> <DemoBadge on={a.is_demo} /></td>
                  <td>{a.agent?.name || '—'}</td>
                  <td>{humanize(a.type)}<div className="ag-small ag-muted">{humanize(a.medium)}</div></td>
                  <td><Pill value={a.status} /></td>
                  <td className="ag-small">{a.notes || '—'}</td>
                  <td className="ag-nowrap">
                    {a.status === 'pending' ? <button className="ag-btn ag-btn--sm" disabled={busy} onClick={() => setStatus(a.id, 'confirmed')}>Confirm</button> : null}
                    {['pending', 'confirmed'].includes(a.status) ? <>
                      <button className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={() => setStatus(a.id, 'completed')}>Completed</button>
                      <button className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={() => setStatus(a.id, 'missed')}>Missed</button></> : null}
                  </td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </ListState>
      </Section>
      <Notice>Appointments are recorded here; no external calendar is connected.</Notice>
    </AgencyPage>
  )
}

function AgentEditor({ agent, onSaved }) {
  const [f, setF] = useState({ jurisdictions: agent.jurisdictions.join(', '), specializations: agent.specializations.join(', '), available: agent.available, max_active: agent.max_active })
  const [run, busy, error] = useAction()
  const split = s => s.split(',').map(x => x.trim()).filter(Boolean)
  const save = async () => {
    const r = await run(() => api.put(`/agency/agents/${agent.user_id}/profile`, {
      jurisdictions: split(f.jurisdictions).map(x => x.toUpperCase()), specializations: split(f.specializations).map(x => x.toLowerCase().replace(/\s+/g, '_')),
      available: f.available, max_active: Number(f.max_active) || 1,
    }))
    if (r) onSaved()
  }
  return (
    <div className="ag-editor">
      <label className="ag-field"><span>Jurisdictions</span><input className="ag-input" value={f.jurisdictions} onChange={e => setF({ ...f, jurisdictions: e.target.value })} placeholder="TX, OK" /></label>
      <label className="ag-field"><span>Specializations</span><input className="ag-input" value={f.specializations} onChange={e => setF({ ...f, specializations: e.target.value })} placeholder="family_protection, retirement" /></label>
      <label className="ag-field"><span>Max active</span><input className="ag-input" type="number" min="1" value={f.max_active} onChange={e => setF({ ...f, max_active: e.target.value })} /></label>
      <label className="ag-check"><input type="checkbox" checked={f.available} onChange={e => setF({ ...f, available: e.target.checked })} /> Available for new prospects</label>
      <button className="ag-btn ag-btn--sm" disabled={busy} onClick={save}>Save</button>
      {error ? <ErrorState error={error} /> : null}
    </div>
  )
}

export function Agents() {
  const { agentId } = useParams()
  const isManager = readAuthority().isManager
  const [params, setParams] = useSearchParams()
  const list = useAgency(`/agency/agents${apiQuery(params, ['with_capacity'], { per_page: 200 })}`)
  const [editing, setEditing] = useState(null)
  return (
    <AgencyPage title="Agents & Production" eyebrow="Agency Growth" testid="agency-agents">
      <div className="ag-toolbar">
        <label className="ag-check"><input type="checkbox" checked={params.get('with_capacity') === 'true'} onChange={e => setParams(e.target.checked ? { with_capacity: 'true' } : {})} /> With capacity only</label>
      </div>
      <ListState q={list} empty="No agents have an agency profile yet.">
        <div className="ag-agents">
          {(list.data?.items || []).map(a => (
            <article key={a.user_id} className={`ag-agent${a.user_id === agentId ? ' ag-agent--sel' : ''}`} data-testid="agent-card">
              <div className="ag-agent__head">
                <strong>{a.name}</strong>
                <span>{a.available ? <Pill value="available" label="Available" tone="green" /> : <Pill value="unavailable" label="Unavailable" tone="muted" />}<DemoBadge on={a.is_demo} /></span>
              </div>
              <div className="ag-meter" title={`${a.active_count} of ${a.max_active} active`}><span style={{ width: `${pctWidth(a.workload_pct)}%` }} className={a.workload_pct >= 90 ? 'ag-meter--hot' : ''} /></div>
              <div className="ag-small ag-muted">{a.workload_pct}% workload · {a.active_count} of {a.max_active} active</div>
              <dl className="ag-facts ag-facts--plain ag-facts--grid">
                <div><dt>Jurisdictions</dt><dd>{a.jurisdictions.join(', ') || 'None configured'}</dd></div>
                <div><dt>Specializations</dt><dd>{a.specializations.map(humanize).join(', ') || 'None configured'}</dd></div>
                <div><dt>Offers in queue</dt><dd><Link to={`/agency/distribution?state=offered&agent_id=${a.user_id}`}>{a.queue_count}</Link></dd></div>
                <div><dt>Upcoming appts</dt><dd><Link to={`/agency/appointments?agent_id=${a.user_id}&range=upcoming`}>{a.appointments_upcoming}</Link></dd></div>
                <div><dt>Open applications</dt><dd><Link to={`/agency/applications?agent_id=${a.user_id}&open=true`}>{a.applications_open}</Link></dd></div>
                <div><dt>Avg response</dt><dd className={a.avg_response_minutes == null ? 'ag-muted' : ''}>{metric(a.avg_response_minutes, ' min')}</dd></div>
              </dl>
              {isManager ? (editing === a.user_id ? <AgentEditor agent={a} onSaved={() => { setEditing(null); list.reload() }} />
                : <button className="ag-btn ag-btn--sm ag-btn--ghost" onClick={() => setEditing(a.user_id)}>Edit profile</button>) : null}
            </article>))}
        </div>
      </ListState>
      <Notice>Production and conversion metrics appear only when recorded; nothing here is estimated.</Notice>
    </AgencyPage>
  )
}
