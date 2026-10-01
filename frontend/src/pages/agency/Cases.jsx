/**
 * CASE MANAGEMENT — Applications & Underwriting, Policies & Clients.
 *
 *   GET  /agency/applications?status=&stalled=&open=     list
 *   GET  /agency/applications/{id}                       detail (+ allowed_transitions)
 *   POST /agency/applications/{id}/transition {to,note}  server enforces the state machine
 *   POST /agency/applications/{id}/requirements|documents (documents = metadata only)
 *   POST /agency/applications/{id}/issue                 creates the Policy
 *   GET  /agency/policies?review_due=  ·  GET /agency/policies/{id}  ·  POST …/review-task
 *
 * Statuses are what the agency RECORDED. Nothing here underwrites, binds or
 * calls a carrier.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { APP_STATUS_LABELS, apiQuery, filterLabel, fmtDate, fmtDateTime, humanize } from './agencyFormat'
import { AgencyPage, DemoBadge, ErrorState, FilterBar, ListState, Loading, Notice, Pill, Section, useAction, useAgency } from './agencyUi'
import { NewApplicationForm } from './AgencyForms'

const APP_FILTERS = ['status', 'agent_id', 'stalled', 'open']

export function Applications() {
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const list = useAgency(`/agency/applications${apiQuery(params, APP_FILTERS, { per_page: 100 })}`)
  const label = filterLabel(params, APP_FILTERS)
  const set = (k, v) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p) }
  const [creating, setCreating] = useState(false)
  return (
    <AgencyPage title="Applications & Underwriting" eyebrow="Case Management" testid="agency-applications"
      actions={<button type="button" className="ag-btn" onClick={() => setCreating(c => !c)} data-testid="open-new-application">{creating ? 'Close' : '+ New application'}</button>}>
      {creating ? <Section title="Start an application"><NewApplicationForm onCancel={() => setCreating(false)} onCreated={a => nav(`/agency/applications/${a.id}`)} /></Section> : null}
      <div className="ag-toolbar">
        <select className="ag-input ag-input--sm" value={params.get('status') || ''} onChange={e => set('status', e.target.value)} aria-label="Status">
          <option value="">Any status</option>{Object.entries(APP_STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label className="ag-check"><input type="checkbox" checked={params.get('stalled') === 'true'} onChange={e => set('stalled', e.target.checked ? 'true' : '')} /> Stalled only</label>
        <label className="ag-check"><input type="checkbox" checked={params.get('open') === 'true'} onChange={e => set('open', e.target.checked ? 'true' : '')} /> Open only</label>
      </div>
      <FilterBar label={label} clearTo="/agency/applications" />
      <Section>
        <ListState q={list} empty={label ? 'No applications match this filter.' : 'No applications yet. Start one from a prospect record.'}>
          <div className="ag-table-wrap">
            <table className="ag-table">
              <thead><tr><th>Client</th><th>Status</th><th>Days in status</th><th>Agent</th><th>Carrier / product</th><th>Submitted</th><th>Next action</th></tr></thead>
              <tbody>{(list.data?.items || []).map(a => (
                <tr key={a.id} className={`ag-row-link${a.stalled ? ' ag-row--stalled' : ''}`} onClick={() => nav(`/agency/applications/${a.id}`)}>
                  <td><Link to={`/agency/applications/${a.id}`} onClick={e => e.stopPropagation()}><strong>{a.prospect?.name}</strong></Link> <DemoBadge on={a.is_demo} /></td>
                  <td><Pill value={a.status} />{a.stalled ? <> <Pill value="stalled" label="Stalled" /></> : null}</td>
                  <td>{a.days_in_status}</td>
                  <td>{a.agent?.name || '—'}</td>
                  <td>{a.carrier || <span className="ag-muted">Not recorded</span>}<div className="ag-small ag-muted">{humanize(a.product_category) || 'Product not recorded'}</div></td>
                  <td>{fmtDate(a.submitted_at)}</td>
                  <td className="ag-small">{a.next_action || '—'}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </ListState>
      </Section>
    </AgencyPage>
  )
}

export function ApplicationDetail() {
  const { applicationId } = useParams()
  const q = useAgency(`/agency/applications/${applicationId}`)
  const [run, busy, error] = useAction()
  const [to, setTo] = useState('')
  const [note, setNote] = useState('')
  const [req, setReq] = useState({ label: '', due: '' })
  const [doc, setDoc] = useState({ name: '', kind: '' })
  const [issue, setIssue] = useState({ policy_number: '', effective_date: '' })
  if (q.loading && !q.data) return <AgencyPage title="Application"><Loading /></AgencyPage>
  if (q.error) return <AgencyPage title="Application"><ErrorState error={q.error} onRetry={q.reload} /><Link className="ag-link" to="/agency/applications">← Applications</Link></AgencyPage>
  const a = q.data
  const act = async (fn) => { const r = await run(fn); if (r !== undefined) q.reload(); return r }
  const transition = async () => { if (!to) return; if (await act(() => api.post(`/agency/applications/${a.id}/transition`, { to, note: note || undefined }))) { setTo(''); setNote('') } }
  const canIssue = (a.allowed_transitions || []).includes('issued')
  return (
    <AgencyPage title={`${a.prospect?.name || 'Client'} — ${humanize(a.product_category) || 'Application'}`} eyebrow={<Link to="/agency/applications">Applications & Underwriting</Link>} testid="agency-application"
      actions={<><DemoBadge on={a.is_demo} /><Pill value={a.status} />{a.stalled ? <Pill value="stalled" label={`Stalled · ${a.days_in_status}d`} /> : null}</>}>
      <div className="ag-contact ag-small">
        <span>Prospect: <Link to={`/agency/prospects/${a.prospect?.id}`}>{a.prospect?.name}</Link></span>
        <span>Agent: {a.agent?.name || '—'}</span><span>Carrier: {a.carrier || 'Not recorded'}</span>
        <span>Submitted: {fmtDate(a.submitted_at)}</span>{a.policy_id ? <span><Link to={`/agency/policies/${a.policy_id}`}>View issued policy →</Link></span> : null}
      </div>
      <div className="ag-grid ag-grid--detail">
        <div className="ag-col">
          <Section title="Move this case">
            <p className="ag-small"><span className="ag-k">Next action</span> {a.next_action || '—'}</p>
            {(a.allowed_transitions || []).length ? (
              <div className="ag-transitions" data-testid="transitions">
                {a.allowed_transitions.filter(t => t !== 'issued').map(t => (
                  <button key={t} type="button" className={`ag-chip${to === t ? ' ag-chip--on' : ''}`} onClick={() => setTo(t)}>{humanize(t)}</button>))}
              </div>) : <p className="ag-muted">Terminal status — no further transitions.</p>}
            {to ? (
              <div className="ag-row">
                <input className="ag-input" value={note} onChange={e => setNote(e.target.value)} placeholder={`Note for “${humanize(to)}”`} aria-label="Transition note" />
                <button className="ag-btn" disabled={busy} onClick={transition}>Record: {humanize(to)}</button>
              </div>) : null}
            {canIssue ? (
              <div className="ag-issue">
                <h3 className="ag-h3">Record issue → creates the policy</h3>
                <div className="ag-row">
                  <input className="ag-input" value={issue.policy_number} onChange={e => setIssue({ ...issue, policy_number: e.target.value })} placeholder="Policy number (as issued)" aria-label="Policy number" />
                  <input className="ag-input ag-input--sm" type="date" value={issue.effective_date} onChange={e => setIssue({ ...issue, effective_date: e.target.value })} aria-label="Effective date" />
                  <button className="ag-btn" disabled={busy} onClick={() => act(() => api.post(`/agency/applications/${a.id}/issue`, { policy_number: issue.policy_number || undefined, effective_date: issue.effective_date || undefined }))}>Record issued</button>
                </div>
              </div>) : null}
            {error ? <ErrorState error={error} /> : null}
            {a.disclaimer ? <Notice>{a.disclaimer}</Notice> : null}
          </Section>
          <Section title="Status history">
            {a.history?.length ? (
              <ol className="ag-timeline">{a.history.map((h, i) => (
                <li key={i}><div>{h.from ? <>{humanize(h.from)} → </> : null}<strong>{humanize(h.to)}</strong> <span className="ag-muted ag-small">{fmtDateTime(h.at)}{h.by?.name ? ` · ${h.by.name}` : ''}</span></div>{h.note ? <div className="ag-small">{h.note}</div> : null}</li>))}
              </ol>) : <p className="ag-muted">No history recorded.</p>}
          </Section>
        </div>
        <div className="ag-col">
          <Section title="Requirements">
            {a.requirements?.length ? <ul className="ag-linklist">{a.requirements.map(r => <li key={r.id}>{r.label} <Pill value={r.status} /> <span className="ag-muted ag-small">{r.due ? `due ${fmtDate(r.due)}` : ''}</span></li>)}</ul> : <p className="ag-muted">None recorded.</p>}
            <div className="ag-row">
              <input className="ag-input" value={req.label} onChange={e => setReq({ ...req, label: e.target.value })} placeholder="Requirement (e.g. signed illustration)" aria-label="Requirement" />
              <input className="ag-input ag-input--sm" type="date" value={req.due} onChange={e => setReq({ ...req, due: e.target.value })} aria-label="Due" />
              <button className="ag-btn ag-btn--sm" disabled={busy || !req.label.trim()} onClick={async () => { if (await act(() => api.post(`/agency/applications/${a.id}/requirements`, { label: req.label.trim(), due: req.due || undefined }))) setReq({ label: '', due: '' }) }}>Add</button>
            </div>
          </Section>
          <Section title="Documents" aside={<span className="ag-muted ag-small">Metadata only — no files stored here</span>}>
            {a.documents?.length ? <ul className="ag-linklist">{a.documents.map(d => <li key={d.id}>{d.name} <span className="ag-muted ag-small">{d.kind ? humanize(d.kind) : ''} · {fmtDate(d.added_at)}</span></li>)}</ul> : <p className="ag-muted">None recorded.</p>}
            <div className="ag-row">
              <input className="ag-input" value={doc.name} onChange={e => setDoc({ ...doc, name: e.target.value })} placeholder="Document name" aria-label="Document name" />
              <input className="ag-input ag-input--sm" value={doc.kind} onChange={e => setDoc({ ...doc, kind: e.target.value })} placeholder="Kind" aria-label="Document kind" />
              <button className="ag-btn ag-btn--sm" disabled={busy || !doc.name.trim()} onClick={async () => { if (await act(() => api.post(`/agency/applications/${a.id}/documents`, { name: doc.name.trim(), kind: doc.kind || undefined }))) setDoc({ name: '', kind: '' }) }}>Add</button>
            </div>
          </Section>
          <Section title="Notes & tasks">
            {a.notes ? <p>{a.notes}</p> : <p className="ag-muted">No notes.</p>}
            {a.tasks?.length ? <ul className="ag-linklist">{a.tasks.map(t => <li key={t.id}>{t.title} <Pill value={t.status} /></li>)}</ul> : null}
          </Section>
        </div>
      </div>
    </AgencyPage>
  )
}

export function Policies() {
  const { policyId } = useParams()
  const [params, setParams] = useSearchParams()
  const list = useAgency(`/agency/policies${apiQuery(params, ['review_due', 'status'], { per_page: 100 })}`)
  const detail = useAgency(policyId ? `/agency/policies/${policyId}` : null)
  const [run, busy, error] = useAction()
  const label = filterLabel(params, ['review_due', 'status'])
  return (
    <AgencyPage title="Policies & Clients" eyebrow="Case Management" testid="agency-policies">
      <div className="ag-toolbar">
        <label className="ag-check"><input type="checkbox" checked={params.get('review_due') === 'true'} onChange={e => { const p = new URLSearchParams(params); if (e.target.checked) p.set('review_due', 'true'); else p.delete('review_due'); setParams(p) }} /> Annual review due</label>
      </div>
      <FilterBar label={label} clearTo="/agency/policies" />
      {policyId ? (
        <Section title="Policy" className="ag-card--focus" aside={<Link to="/agency/policies" className="ag-link">Close</Link>}>
          {detail.loading && !detail.data ? <Loading /> : detail.error ? <ErrorState error={detail.error} /> : detail.data ? (
            <div>
              <div className="ag-contact ag-small">
                <span>Client: <Link to={`/agency/prospects/${detail.data.client?.id}`}>{detail.data.client?.name}</Link></span>
                <span>{detail.data.policy_number || 'No policy number'}</span><span>{detail.data.carrier || 'Carrier not recorded'}</span>
                <span>Effective {fmtDate(detail.data.effective_date)}</span><span>Review {fmtDate(detail.data.annual_review_date)}</span>
                <Pill value={detail.data.status} /><DemoBadge on={detail.data.is_demo} />
              </div>
              {detail.data.tasks?.length ? <ul className="ag-linklist">{detail.data.tasks.map(t => <li key={t.id}>{t.title} <Pill value={t.status} /> <span className="ag-muted ag-small">{t.due_at ? `due ${fmtDate(t.due_at)}` : ''}</span></li>)}</ul> : <p className="ag-muted">No service tasks.</p>}
              <div className="ag-row">
                {['annual_review', 'beneficiary_review', 'service'].map(k => (
                  <button key={k} className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={async () => { const r = await run(() => api.post(`/agency/policies/${policyId}/review-task`, { kind: k })); if (r) { detail.reload(); list.reload() } }}>+ {humanize(k)} task</button>))}
              </div>
              {error ? <ErrorState error={error} /> : null}
            </div>) : null}
        </Section>) : null}
      <Section>
        <ListState q={list} empty={label ? 'No policies match this filter.' : 'No policies yet. Issued applications become policies here.'}>
          <div className="ag-table-wrap">
            <table className="ag-table">
              <thead><tr><th>Client</th><th>Product</th><th>Status</th><th>Carrier</th><th>Effective</th><th>Annual review</th><th>Open service tasks</th></tr></thead>
              <tbody>{(list.data?.items || []).map(x => (
                <tr key={x.id} className={x.id === policyId ? 'ag-row--sel' : ''}>
                  <td><Link to={`/agency/policies/${x.id}`}><strong>{x.client?.name}</strong></Link> <DemoBadge on={x.is_demo} /><div className="ag-small ag-muted">{x.policy_number}</div></td>
                  <td>{humanize(x.product_category) || '—'}</td><td><Pill value={x.status} /></td><td>{x.carrier || '—'}</td>
                  <td>{fmtDate(x.effective_date)}</td><td>{fmtDate(x.annual_review_date)}</td><td>{x.open_service_tasks}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </ListState>
      </Section>
    </AgencyPage>
  )
}
