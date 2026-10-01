/**
 * TASKS & FOLLOW-UP / RENEWALS — the energy operating queues.
 *
 *   GET /energy-ops/queues          every queue's count + the rules behind it
 *   GET /energy-ops/queues/{key}    the records in one queue (paged)
 *
 * Each queue is a documented server query (app/routers/energy_ops_router.py).
 * A null count renders "Not yet available" — never a zero. Renewal dates come
 * only from a contract end date a person entered; leads without one are
 * counted as missing, not guessed. Nothing on this screen sends or promotes.
 *
 * `initialQueue` lets the Renewals nav entry open the same screen on the
 * renewal window; the URL ?queue= param wins so Overview cards can drill in.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { humanizeKey } from '../../utils/humanize'
import './energy.css'

const NA = 'Not yet available'
const PER_PAGE = 25

const COLUMNS = {
  task: ['Task', 'Record', 'Due', 'Owner'],
  lead: ['Name', 'Stage', 'Detail', 'Owner', 'Consent'],
  contact: ['Name', 'Company', 'Email / Phone', 'Source', 'Lead'],
}

function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString()
}

function leadDetail(row) {
  if (row.reason) return row.reason
  if (row.days_to_renewal != null) {
    const n = row.days_to_renewal
    return `Contract ends ${row.contract_end_date} (${n < 0 ? `${-n}d ago` : `in ${n}d`})`
  }
  if (row.days_since_message != null) return `Last message ${row.days_since_message}d ago, no reply`
  if (row.contract_end_date) return `Contract ends ${row.contract_end_date}`
  if ('contract_end_date' in row) return 'No contract end date on file'
  return '—'
}

export default function FollowUpQueues({ initialQueue = 'follow_up_due', title, subtitle }) {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const active = params.get('queue') || initialQueue
  const [summary, setSummary] = useState(null)
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/energy-ops/queues').then(setSummary).catch(e => setError(e.message || 'Could not load queues'))
  }, [])

  const load = useCallback(() => {
    setData(null)
    api.get(`/energy-ops/queues/${active}?page=${page}&per_page=${PER_PAGE}`)
      .then(setData).catch(e => setError(e.message || 'Could not load queue'))
  }, [active, page])
  useEffect(() => { load() }, [load])

  const pick = key => { setPage(1); setParams({ queue: key }) }
  const queues = summary?.queues || []
  const activeQ = queues.find(q => q.key === active)
  const items = data?.items || []
  const kind = items[0]?.type || (activeQ?.kind === 'contacts' ? 'contact' : activeQ?.kind === 'tasks' ? 'task' : 'lead')
  const cols = COLUMNS[kind] || COLUMNS.lead

  return (
    <div className="eo" data-testid="energy-followup">
      <div className="eo-head">
        <div>
          <h1>{title || 'Tasks & Follow-Up'}</h1>
          <p className="eo-sub">{subtitle || 'Follow-ups due, overdue and upcoming; customers to renew or reactivate; leads that went quiet.'}</p>
        </div>
        <Link className="eo-btn" to="/workqueue">Manage tasks</Link>
      </div>
      {error && <div className="eo-banner eo-banner--error">{error}</div>}

      <div className="eo-kpis" role="tablist">
        {queues.map(q => (
          <button key={q.key} type="button" role="tab" aria-pressed={q.key === active}
            className={`eo-kpi${q.available ? '' : ' eo-kpi--na'}`} onClick={() => pick(q.key)}
            data-queue={q.key}>
            <span className="eo-kpi-n">{q.available ? q.count.toLocaleString() : NA}</span>
            <span className="eo-kpi-l">{q.label}</span>
          </button>
        ))}
        {!summary && !error && <div className="eo-note">Loading queues…</div>}
      </div>
      {summary && (
        <p className="eo-note">
          Overdue = open task due before today · Escalation = overdue {summary.rules.escalate_after_days}+ days or a flagged lead ·
          No response = messaged {summary.rules.no_response_days}+ days ago with no reply ·
          Renewal window = contract end date between {-summary.rules.renewal_window_days[0]} days ago and {summary.rules.renewal_window_days[1]} days ahead.
          {summary.renewal_date_missing > 0 && ` ${summary.renewal_date_missing.toLocaleString()} enrolled customer(s) have no contract end date on file and are not in the renewal window.`}
        </p>
      )}

      <div className="eo-panel">
        {data && data.available === false ? (
          <div className="eo-empty">{data.detail || NA}</div>
        ) : !data ? (
          <div className="eo-empty">Loading…</div>
        ) : items.length === 0 ? (
          <div className="eo-empty">Nothing in “{activeQ?.label || active}” right now.</div>
        ) : (
          <table className="eo-table">
            <thead><tr>{cols.map((c, i) => <th key={c} className={i > 2 ? 'eo-hide-sm' : ''}>{c}</th>)}</tr></thead>
            <tbody>
              {items.map(row => row.type === 'task' ? (
                <tr key={`t-${row.id}`} className={row.link ? 'eo-click' : ''} onClick={() => row.link && navigate(row.link)}>
                  <td>{row.title}{row.days_overdue > 0 && <> <span className="eo-pill eo-pill--red">{row.days_overdue}d overdue</span></>}</td>
                  <td>{row.name || '—'}</td>
                  <td>{fmtDate(row.due_at)}</td>
                  <td className="eo-hide-sm">{row.owner || 'Unassigned'}</td>
                </tr>
              ) : row.type === 'contact' ? (
                <tr key={`c-${row.id}`} className="eo-click" onClick={() => navigate(row.link)}>
                  <td>{row.name}</td>
                  <td>{row.company || '—'}</td>
                  <td>{row.email || row.phone || '—'}</td>
                  <td className="eo-hide-sm">{row.source || '—'}</td>
                  <td className="eo-hide-sm">{row.lead_id ? <Link to={`/leads/${row.lead_id}`} onClick={e => e.stopPropagation()}>Open lead</Link> : <span className="eo-pill">Contact only</span>}</td>
                </tr>
              ) : (
                <tr key={`l-${row.id}`} className="eo-click" onClick={() => navigate(row.link)}>
                  <td>{row.name}</td>
                  <td>{humanizeKey(row.tier_label || row.tier)}</td>
                  <td>{leadDetail(row)}</td>
                  <td className="eo-hide-sm">{row.owner || 'Unassigned'}</td>
                  <td className="eo-hide-sm">
                    {row.dnc ? <span className="eo-pill eo-pill--red">DNC</span>
                      : row.sms_consent ? <span className="eo-pill eo-pill--green">SMS consent</span>
                        : <span className="eo-pill">No SMS consent</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && data.total > PER_PAGE && (
          <div className="eo-pager">
            <span>{(page - 1) * PER_PAGE + 1}–{Math.min(page * PER_PAGE, data.total)} of {data.total.toLocaleString()}</span>
            <span style={{ display: 'flex', gap: 6 }}>
              <button type="button" className="eo-btn" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
              <button type="button" className="eo-btn" disabled={page * PER_PAGE >= data.total} onClick={() => setPage(p => p + 1)}>Next</button>
            </span>
          </div>
        )}
      </div>
      {(active === 'reactivation' || active === 'previous_customers') && (
        <p className="eo-note">Promotion to a lead is a deliberate action on the contact record. Nothing on this list is promoted automatically.</p>
      )}
    </div>
  )
}

export function RenewalsQueues() {
  return <FollowUpQueues initialQueue="renewal_window" title="Renewals"
    subtitle="Customers whose recorded contract end date is approaching, previous customers and reactivation candidates." />
}
