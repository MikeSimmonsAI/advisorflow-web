/**
 * RECRUITING & LICENSING, AGENCY INTELLIGENCE.
 *
 *   GET  /agency/recruits?stage=&near_activation=   board (stages are per-org config)
 *   GET  /agency/recruits/{id}  ·  POST …/stage {to}  ·  POST …/milestones {label,status,due}
 *   GET  /agency/attention  ·  POST /agency/ask
 *
 * Licensing and exam status are AS ENTERED — there is no government
 * licensing integration and the screen says so.
 */
import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { apiQuery, filterLabel, fmtDate, fmtDateTime, groupByStage, humanize, sortAttention } from './agencyFormat'
import { AgencyPage, DemoBadge, ErrorState, FilterBar, Loading, Notice, Pill, Section, useAction, useAgency } from './agencyUi'
import { AskEvoAI, AttentionList } from './CommandCenter'

function RecruitPanel({ id, onChanged }) {
  const q = useAgency(`/agency/recruits/${id}`)
  const [run, busy, error] = useAction()
  const [m, setM] = useState({ label: '', due: '' })
  if (q.loading && !q.data) return <Loading />
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />
  const r = q.data
  const act = async (fn) => { const x = await run(fn); if (x) { q.reload(); onChanged() } return x }
  const idx = r.stages.indexOf(r.stage)
  return (
    <div data-testid="recruit-detail">
      <div className="ag-contact ag-small">
        <span><strong>{r.name}</strong></span><span>{r.jurisdiction || 'Jurisdiction not entered'}</span>
        <span>Recruiter: {r.recruiter?.name || '—'}</span><span>Exam: {r.exam_status ? humanize(r.exam_status) : 'Not entered'}</span>
        <span>Training: {r.training_progress_pct == null ? 'Not entered' : `${r.training_progress_pct}%`}</span><DemoBadge on={r.is_demo} />
      </div>
      <ol className="ag-stepper">
        {r.stages.map((s, i) => (
          <li key={s} className={i < idx ? 'is-done' : i === idx ? 'is-current' : ''}>
            <button type="button" disabled={busy || i === idx} onClick={() => act(() => api.post(`/agency/recruits/${r.id}/stage`, { to: s }))} title={i === idx ? 'Current stage' : `Move to ${humanize(s)}`}>{humanize(s)}</button>
          </li>))}
      </ol>
      <h3 className="ag-h3">Licensing milestones</h3>
      {r.milestones?.length ? (
        <ul className="ag-linklist">{r.milestones.map(x => <li key={x.id}>{x.label} <Pill value={x.status} /> <span className="ag-muted ag-small">{x.due ? `due ${fmtDate(x.due)}` : ''}{x.completed_at ? ` · done ${fmtDate(x.completed_at)}` : ''}</span></li>)}</ul>
      ) : <p className="ag-muted">No milestones entered.</p>}
      <div className="ag-row">
        <input className="ag-input" value={m.label} onChange={e => setM({ ...m, label: e.target.value })} placeholder="Milestone (e.g. Pre-licensing course)" aria-label="Milestone" />
        <input className="ag-input ag-input--sm" type="date" value={m.due} onChange={e => setM({ ...m, due: e.target.value })} aria-label="Due" />
        <button className="ag-btn ag-btn--sm" disabled={busy || !m.label.trim()} onClick={async () => { if (await act(() => api.post(`/agency/recruits/${r.id}/milestones`, { label: m.label.trim(), status: 'pending', due: m.due || undefined }))) setM({ label: '', due: '' }) }}>Add</button>
      </div>
      {r.stage_history?.length ? <>
        <h3 className="ag-h3">Stage history</h3>
        <ol className="ag-timeline">{r.stage_history.map((h, i) => <li key={i}>{h.from ? `${humanize(h.from)} → ` : ''}<strong>{humanize(h.to)}</strong> <span className="ag-muted ag-small">{fmtDateTime(h.at)}</span></li>)}</ol></> : null}
      {r.notes ? <p className="ag-small">{r.notes}</p> : null}
      {error ? <ErrorState error={error} /> : null}
      <Notice>{r.licensing_note}</Notice>
    </div>
  )
}

export function Recruiting() {
  const { recruitId } = useParams()
  const [params, setParams] = useSearchParams()
  const list = useAgency(`/agency/recruits${apiQuery(params, ['stage', 'near_activation'], { per_page: 200 })}`)
  const cfg = useAgency('/agency/distribution/config')
  const [adding, setAdding] = useState(false)
  const [nr, setNr] = useState({ name: '', jurisdiction: '' })
  const [run, busy, error] = useAction()
  const label = filterLabel(params, ['stage', 'near_activation'])
  const stages = cfg.data?.recruit_stages || []
  const groups = groupByStage(list.data?.items || [], stages)
  const create = async () => {
    const r = await run(() => api.post('/agency/recruits', { name: nr.name.trim(), jurisdiction: nr.jurisdiction.trim().toUpperCase() || undefined }))
    if (r) { setNr({ name: '', jurisdiction: '' }); setAdding(false); list.reload() }
  }
  return (
    <AgencyPage title="Recruiting & Licensing" eyebrow="Agency Growth" testid="agency-recruiting"
      actions={<button className="ag-btn" onClick={() => setAdding(a => !a)}>{adding ? 'Cancel' : 'Add recruit'}</button>}>
      {adding ? (
        <Section title="New recruit">
          <div className="ag-row">
            <input className="ag-input" value={nr.name} onChange={e => setNr({ ...nr, name: e.target.value })} placeholder="Full name" aria-label="Name" />
            <input className="ag-input ag-input--sm" value={nr.jurisdiction} onChange={e => setNr({ ...nr, jurisdiction: e.target.value })} placeholder="State" aria-label="Jurisdiction" />
            <button className="ag-btn" disabled={busy || !nr.name.trim()} onClick={create}>Create</button>
          </div>
          {error ? <ErrorState error={error} /> : null}
        </Section>) : null}
      <div className="ag-toolbar">
        <label className="ag-check"><input type="checkbox" checked={params.get('near_activation') === 'true'} onChange={e => setParams(e.target.checked ? { near_activation: 'true' } : {})} /> Close to activation</label>
      </div>
      <FilterBar label={label} clearTo="/agency/recruits" />
      {recruitId ? <Section title="Recruit" className="ag-card--focus" aside={<Link className="ag-link" to={`/agency/recruits${params.toString() ? `?${params}` : ''}`}>Close</Link>}><RecruitPanel id={recruitId} onChanged={list.reload} /></Section> : null}
      {(list.loading && !list.data) || (cfg.loading && !cfg.data) ? <Loading /> : list.error || cfg.error ? <ErrorState error={list.error || cfg.error} onRetry={() => { list.reload(); cfg.reload() }} /> : (
        <div className="ag-board ag-board--stages" data-testid="recruit-board">
          {groups.map(g => (
            <div key={g.stage} className="ag-board__col">
              <div className="ag-board__head"><span>{humanize(g.stage)}</span> <span className="ag-muted">{g.items.length}</span></div>
              {g.items.length ? g.items.map(r => (
                <Link key={r.id} to={`/agency/recruits/${r.id}${params.toString() ? `?${params}` : ''}`} className={`ag-board__card${r.id === recruitId ? ' is-sel' : ''}`}>
                  <strong>{r.name}</strong>
                  <span className="ag-small">{r.jurisdiction || '—'} · {r.recruiter?.name || 'No recruiter'}</span>
                  <span className="ag-small ag-muted">Milestones {r.milestones_done}/{r.milestones_total}{r.milestones_overdue ? ` · ${r.milestones_overdue} overdue` : ''}</span>
                  <span>{r.near_activation ? <Pill value="near" label="Near activation" tone="gold" /> : null}{r.milestones_overdue ? <Pill value="overdue" label="Overdue" /> : null}<DemoBadge on={r.is_demo} /></span>
                </Link>)) : <p className="ag-muted ag-small">—</p>}
            </div>))}
        </div>)}
    </AgencyPage>
  )
}

export function Intelligence() {
  const attention = useAgency('/agency/attention')
  const items = sortAttention(attention.data?.items)
  const kinds = [...new Set(items.map(i => i.kind))]
  return (
    <AgencyPage title="Agency Intelligence" eyebrow="Intelligence" testid="agency-intelligence">
      <p className="ag-lede">What needs your attention today, from this workspace's records. Every item opens the record behind it.</p>
      {kinds.length ? <div className="ag-chips ag-chips--summary">{kinds.map(k => <span key={k} className="ag-chip ag-chip--static">{humanize(k)} · {items.filter(i => i.kind === k).length}</span>)}</div> : null}
      <div className="ag-grid ag-grid--main">
        <Section title="Needs attention"><AttentionList q={attention} /></Section>
        <AskEvoAI />
      </div>
    </AgencyPage>
  )
}
