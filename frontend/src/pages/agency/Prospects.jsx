/**
 * PROSPECTS & FAMILIES — GET /agency/prospects (filters pass through from the
 * URL, so a Command Center count lands on exactly the rows it counted).
 *
 * AI OPPORTUNITY CENTER — the prospects that need an action (unassigned, or
 * uncontacted), each with its rules-generated brief one click away.
 */
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiQuery, factText, filterLabel, humanize, relTime } from './agencyFormat'
import { AgencyPage, DemoBadge, ErrorState, FilterBar, ListState, Loading, Pill, Section, useAgency } from './agencyUi'
import { BriefView } from './ProspectDetail'
import { NewApplicationForm, NewAppointmentForm, Pager } from './AgencyForms'

const FILTERS = ['q', 'status', 'intent', 'agent_id', 'unassigned', 'need', 'assignment', 'uncontacted']
const PER_PAGE = 50

export default function Prospects() {
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const [q, setQ] = useState(params.get('q') || '')
  const list = useAgency(`/agency/prospects${apiQuery(params, [...FILTERS, 'page'], { per_page: PER_PAGE })}`)
  const [form, setForm] = useState(null)
  const [flash, setFlash] = useState('')
  const label = filterLabel(params, FILTERS.filter(f => f !== 'q'))
  const setFilter = (k, v) => { const p = new URLSearchParams(params); p.delete('page'); if (v) p.set(k, v); else p.delete(k); setParams(p) }
  const setPage = (n) => { const p = new URLSearchParams(params); if (n > 1) p.set('page', String(n)); else p.delete('page'); setParams(p) }
  const done = (msg) => { setForm(null); setFlash(msg); list.reload() }
  return (
    <AgencyPage title="Prospects & Families" eyebrow="Client Acquisition" testid="agency-prospects"
      actions={<>
        <button type="button" className="ag-btn ag-btn--ghost" onClick={() => { setFlash(''); setForm(f => (f === 'appointment' ? null : 'appointment')) }}>+ Appointment</button>
        <button type="button" className="ag-btn ag-btn--ghost" onClick={() => { setFlash(''); setForm(f => (f === 'application' ? null : 'application')) }}>+ Application</button>
      </>}>
      {form === 'appointment' ? <Section title="New appointment"><NewAppointmentForm onCancel={() => setForm(null)} onCreated={() => done('Appointment recorded as pending.')} /></Section> : null}
      {form === 'application' ? <Section title="Start an application"><NewApplicationForm onCancel={() => setForm(null)} onCreated={a => { setForm(null); nav(`/agency/applications/${a.id}`) }} /></Section> : null}
      {flash ? <p className="ag-ok" role="status">{flash}</p> : null}
      <div className="ag-toolbar">
        <form onSubmit={e => { e.preventDefault(); setFilter('q', q.trim()) }} className="ag-toolbar__search">
          <input className="ag-input" value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, email, phone" aria-label="Search prospects" />
        </form>
        <select className="ag-input ag-input--sm" value={params.get('intent') || ''} onChange={e => setFilter('intent', e.target.value)} aria-label="Intent">
          <option value="">Any intent</option><option value="high">High intent</option><option value="medium">Medium</option><option value="low">Low</option>
        </select>
        <select className="ag-input ag-input--sm" value={params.get('assignment') || (params.get('unassigned') ? 'unassigned' : '')}
          onChange={e => { const p = new URLSearchParams(params); p.delete('unassigned'); if (e.target.value) p.set('assignment', e.target.value); else p.delete('assignment'); setParams(p) }} aria-label="Assignment">
          <option value="">Any assignment</option>
          {['unassigned', 'offered', 'accepted', 'declined', 'timed_out', 'escalated'].map(s => <option key={s} value={s}>{humanize(s)}</option>)}
        </select>
      </div>
      <FilterBar label={label} clearTo="/agency/prospects" />
      <Section>
        <ListState q={list} empty={label ? 'No prospects match this filter.' : 'No prospects yet. Website and intake submissions arrive here.'}>
          <div className="ag-table-wrap">
            <table className="ag-table">
              <thead><tr><th>Prospect</th><th>Need</th><th>Intent</th><th>Agent</th><th>Assignment</th><th>Last activity</th><th>SMS consent</th></tr></thead>
              <tbody>
                {(list.data?.items || []).map(p => (
                  <tr key={p.id} onClick={() => nav(`/agency/prospects/${p.id}`)} className="ag-row-link">
                    <td><Link to={`/agency/prospects/${p.id}`} onClick={e => e.stopPropagation()}><strong>{p.name || 'Unnamed'}</strong></Link>
                      <div className="ag-muted ag-small">{[p.state, p.source].filter(Boolean).join(' · ')} <DemoBadge on={p.is_demo} /></div></td>
                    <td>{factText(p.need_categories)}</td>
                    <td><Pill value={p.intent_level} /> {p.intent_level ? null : <span className="ag-muted">Not recorded</span>}</td>
                    <td>{p.assigned_agent?.name || <span className="ag-muted">—</span>}</td>
                    <td><Pill value={p.assignment_state} /></td>
                    <td>{relTime(p.last_activity_at || p.created_at)}</td>
                    <td>{p.sms_consent === true ? <Pill value="granted" label="Recorded" tone="green" /> : <span className="ag-muted">{p.sms_consent === false ? 'Not given' : 'Unknown'}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager data={list.data} onPage={setPage} />
        </ListState>
      </Section>
    </AgencyPage>
  )
}

function OpportunityCard({ p }) {
  const [open, setOpen] = useState(false)
  const brief = useAgency(open ? `/agency/prospects/${p.id}/brief` : null)
  return (
    <article className="ag-opp" data-testid="opportunity">
      <div className="ag-opp__head">
        <div>
          <Link to={`/agency/prospects/${p.id}`} className="ag-opp__name">{p.name}</Link> <DemoBadge on={p.is_demo} />
          <div className="ag-muted ag-small">{[p.state, p.source, `received ${relTime(p.created_at)}`].filter(Boolean).join(' · ')}</div>
        </div>
        <div className="ag-opp__pills"><Pill value={p.intent_level} label={p.intent_level ? `${humanize(p.intent_level)} intent` : null} /><Pill value={p.assignment_state} /></div>
      </div>
      <div className="ag-small">{factText(p.need_categories)}</div>
      <div className="ag-opp__actions">
        <button type="button" className="ag-btn ag-btn--ghost" onClick={() => setOpen(o => !o)} aria-expanded={open}>{open ? 'Hide brief' : 'Show AI brief'}</button>
        <Link className="ag-btn" to={`/agency/prospects/${p.id}`}>Open record</Link>
      </div>
      {open ? (brief.loading ? <Loading label="Building brief" /> : brief.error ? <ErrorState error={brief.error} onRetry={brief.reload} /> : brief.data ? <BriefView brief={brief.data} compact /> : null) : null}
    </article>
  )
}

export function OpportunityCenter() {
  const unassigned = useAgency('/agency/prospects?unassigned=true&per_page=100')
  const uncontacted = useAgency('/agency/prospects?uncontacted=true&per_page=100')
  const loading = (unassigned.loading && !unassigned.data) || (uncontacted.loading && !uncontacted.data)
  const error = unassigned.error || uncontacted.error
  const seen = new Map()
  for (const p of [...(unassigned.data?.items || []), ...(uncontacted.data?.items || [])]) if (!seen.has(p.id)) seen.set(p.id, p)
  const rank = { high: 0, medium: 1, low: 2 }
  const rows = [...seen.values()].sort((a, b) => (rank[a.intent_level] ?? 3) - (rank[b.intent_level] ?? 3))
  return (
    <AgencyPage title="AI Opportunity Center" eyebrow="Command" testid="agency-opportunities">
      <p className="ag-lede">Prospects that are unassigned or have not yet been contacted, highest recorded intent first. Each brief separates what the record says from what the system infers, and lists what is still unknown.</p>
      {loading ? <Loading /> : error ? <ErrorState error={error} onRetry={() => { unassigned.reload(); uncontacted.reload() }} /> :
        rows.length ? <div className="ag-opps">{rows.map(p => <OpportunityCard key={p.id} p={p} />)}</div>
          : <Section><p className="ag-muted">No prospect needs an action right now. <Link to="/agency/prospects">All prospects →</Link></p></Section>}
    </AgencyPage>
  )
}
