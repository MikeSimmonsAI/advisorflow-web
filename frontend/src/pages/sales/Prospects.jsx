/**
 * Prospects — the list view of the same opportunities the board shows.
 *
 * THIS IS BRAND SALES, NOT THE TENANT CRM. A `Lead` belongs to a customer
 * organisation and lives in the tenant app; an `Opportunity` belongs to a brand
 * sales org and is a company we are trying to sell TO. They have no foreign key
 * between them and never will. This page reads `/sales/opportunities` — the
 * same endpoint, the same records and the same scoping as the pipeline board.
 * Nothing here touches leads.
 *
 * WHY A LIST WHEN A BOARD ALREADY EXISTS. A board is for moving one deal
 * through stages. A list is for the questions a board is bad at: who owns what,
 * what has nobody picked up, what has gone quiet, what is overdue — and for
 * acting on several of them in a row. Same data, different question, so it
 * reuses the endpoint rather than duplicating the pipeline.
 *
 * UNOWNED PROSPECTS ARE THE POINT. `_scoped_opportunities` deliberately shows a
 * rep the unowned rows as well as their own, so a new prospect is never
 * orphaned into invisibility. Sorting them to the top and letting a manager
 * assign one from here is the whole reason this screen earns its place.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import NewProspect from './NewProspect'
import ReassignControl from './ReassignControl'
import {
  Card, Chip, Empty, ErrorBar, Metric,
  money, shortDate, dueLabel,
} from './parts'

const SORTS = {
  recent: { label: 'Recently changed', fn: (a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')) },
  value: { label: 'Deal value', fn: (a, b) => (b.deal_value || 0) - (a.deal_value || 0) },
  stale: { label: 'Longest in stage', fn: (a, b) => (b.days_in_stage || 0) - (a.days_in_stage || 0) },
  company: { label: 'Company A–Z', fn: (a, b) => String(a.company_name || '').localeCompare(String(b.company_name || '')) },
}

export default function Prospects() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [team, setTeam] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)

  const [q, setQ] = useState('')
  const [stage, setStage] = useState('')
  const [owner, setOwner] = useState('')
  const [sort, setSort] = useState('recent')
  const [showLost, setShowLost] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await api.get('/sales/opportunities?include_lost=true'))
    } catch (e) {
      setError(e.message || 'Could not load prospects.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.get('/sales/team').then(r => setTeam(Array.isArray(r) ? r : []))
      .catch(() => setTeam([]))
  }, [])

  const isManager = !!data?.is_manager
  const stages = data?.stages || []

  const all = useMemo(() => {
    const rows = stages.flatMap(s => s.opportunities || [])
    return showLost ? rows.concat(data?.lost || []) : rows
  }, [stages, data, showLost])

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let out = all.filter(o => {
      if (stage && o.stage !== stage) return false
      if (owner === '__none__') { if (o.owner_user_id) return false }
      else if (owner && o.owner_user_id !== owner) return false
      if (!needle) return true
      return [o.company_name, o.contact_name, o.email, o.phone, o.industry]
        .filter(Boolean).some(v => String(v).toLowerCase().includes(needle))
    })
    out = [...out].sort(SORTS[sort].fn)
    // Unowned first, always. They are the ones nobody is looking at.
    return out.sort((a, b) => (a.owner_user_id ? 1 : 0) - (b.owner_user_id ? 1 : 0))
  }, [all, q, stage, owner, sort])

  const unowned = all.filter(o => !o.owner_user_id).length
  const overdue = all.filter(o => dueLabel(o.next_action_due_at).text === 'Overdue').length
  const noAction = all.filter(o => !o.next_action).length
  const value = rows.reduce((n, o) => n + (o.deal_value || 0), 0)

  const clearAll = () => { setQ(''); setStage(''); setOwner(''); setShowLost(false) }
  const filtered = q || stage || owner || showLost

  return (
    <SalesShell
      title="Prospects"
      subtitle={isManager
        ? 'Every company your team is selling to, as a list you can work down.'
        : 'The companies you are selling to, plus anything nobody has picked up yet.'}
      actions={
        <>
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
          <button className="sw-btn sw-primary" onClick={() => setCreating(true)}>
            + New Prospect
          </button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      {creating && (
        <NewProspect onClose={() => setCreating(false)}
                     onCreated={o => { setCreating(false); nav('/sales/opportunities/' + o.id) }} />
      )}

      <div className="sw-metrics">
        <Metric label="Prospects" value={all.length}
                sub={isManager ? 'across the team' : 'in your book'} />
        <Metric label="Unowned" value={unowned} attn={unowned > 0}
                sub={unowned ? 'nobody is working these' : 'all assigned'} />
        <Metric label="Overdue action" value={overdue} attn={overdue > 0}
                sub={overdue ? 'past their due date' : 'nothing overdue'} />
        <Metric label="No next action" value={noAction} attn={noAction > 0}
                sub={noAction ? 'no agreed next step' : 'every deal has one'} />
        <Metric label="Showing" value={rows.length}
                sub={money(value) || 'no value set'} />
      </div>

      <div className="sw-filters">
        <input className="sw-input" placeholder="Search company, contact, email…"
               style={{ minWidth: 240 }}
               value={q} onChange={e => setQ(e.target.value)} />
        <select className="sw-select" value={stage} onChange={e => setStage(e.target.value)}>
          <option value="">Every stage</option>
          {stages.map(s => (
            <option key={s.key} value={s.key}>{s.label} ({s.count})</option>
          ))}
        </select>
        <select className="sw-select" value={owner} onChange={e => setOwner(e.target.value)}>
          <option value="">Any owner</option>
          <option value="__none__">Unowned only ({unowned})</option>
          {team.map(t => <option key={t.id} value={t.id}>{t.full_name}</option>)}
        </select>
        <select className="sw-select" value={sort} onChange={e => setSort(e.target.value)}>
          {Object.entries(SORTS).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
        <label className="sw-subtle sw-flex" style={{ gap: 5 }}>
          <input type="checkbox" checked={showLost}
                 onChange={e => setShowLost(e.target.checked)} />
          Include lost
        </label>
        {filtered ? (
          <button className="sw-btn sw-ghost" onClick={clearAll}>Clear filters</button>
        ) : null}
      </div>

      {loading && !data ? <div className="sw-subtle">Loading…</div> : null}

      {data && rows.length === 0 ? (
        <Card>
          <Empty title={filtered ? 'Nothing matches those filters' : 'No prospects yet'}>
            {filtered
              ? 'Clear the filters to see everything again.'
              : 'Production stays clean until a real prospect is entered — nothing here is seeded. Use + New Prospect to create the first one.'}
          </Empty>
        </Card>
      ) : null}

      {rows.length > 0 && (
        <Card title="PROSPECTS"
              sub="Unowned first — those are the ones nobody is looking at"
              bodyless>
          <div className="sw-tablewrap">
            <table className="sw-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Stage</th>
                  <th>Owner</th>
                  <th>Value</th>
                  <th>Next action</th>
                  <th>Next meeting</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map(o => {
                  const due = dueLabel(o.next_action_due_at)
                  return (
                    <tr key={o.id}>
                      <td>
                        <b>{o.company_name}</b>
                        <div className="sw-subtle">
                          {[o.contact_name, o.industry].filter(Boolean).join(' · ') || '—'}
                        </div>
                      </td>
                      <td>
                        <Chip>{o.stage_label}</Chip>
                        <div className="sw-subtle">
                          {o.days_in_stage != null ? o.days_in_stage + 'd in stage' : '—'}
                        </div>
                        {o.attention
                          ? <Chip tone="amber">{o.attention}</Chip> : null}
                      </td>
                      <td>
                        {o.owner_name || <Chip tone="amber">Unowned</Chip>}
                        {isManager ? (
                          <div style={{ marginTop: 6 }}>
                            <ReassignControl
                              opportunityId={o.id}
                              canReassign
                              currentOwnerId={o.owner_user_id}
                              currentOwnerName={o.owner_name}
                              onReassigned={load}
                            />
                          </div>
                        ) : null}
                      </td>
                      <td>{o.deal_value != null ? money(o.deal_value) : <span className="sw-subtle">—</span>}</td>
                      <td>
                        {o.next_action || <span className="sw-subtle">Not set</span>}
                        {due.text
                          ? <div style={{ marginTop: 4 }}><Chip tone={due.tone}>{due.text}</Chip></div>
                          : (o.next_action_due_at
                             ? <div className="sw-subtle">{shortDate(o.next_action_due_at)}</div>
                             : null)}
                      </td>
                      <td>
                        {o.next_appointment
                          ? <>
                              {shortDate(o.next_appointment.starts_at_local)}
                              {o.confirmation_status === 'pending'
                                ? <div><Chip tone="amber">unconfirmed</Chip></div>
                                : null}
                            </>
                          : <span className="sw-subtle">None booked</span>}
                      </td>
                      <td>
                        <button className="sw-btn"
                                onClick={() => nav('/sales/opportunities/' + o.id)}>
                          Open
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <p className="sw-subtle" style={{ marginTop: 14 }}>
        These are companies your brand is selling to. Customer leads belong to
        the customers themselves and live inside their own tenant — a prospect
        here never becomes a lead there.
      </p>
    </SalesShell>
  )
}
