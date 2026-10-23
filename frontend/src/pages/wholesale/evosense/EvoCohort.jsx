/* EVOSENSE — AN IMPORTED LIST, FOLLOWED AS A GROUP (e.g. the 2010 Top 100).
 *
 * Every property the list brought in, on one screen: who owns it now and how
 * we know, tax and foreclosure status with their evidence, the ordinary score
 * with what earned it and what holds it back, a priority and the next action.
 * "Work queue" narrows to properties a person can act on today.
 *
 * Nothing here imports, buys or sends anything. "Re-check public records"
 * runs the free county lookups and county tax files again for this list.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Chips, Empty, EvoApp, Hero, Panel, Ring, Skeleton, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import './evo-actions.css'

const PRIORITY = { A: 'A — act now', B: 'B — strong', C: 'C — watch', REVIEW: 'Review', PARK: 'Park' }
const TONE = {
  VERIFIED: 'live', CONFLICT: 'danger', UNVERIFIED: 'estimate', 'NO OWNER': 'danger',
  'TAX SALE': 'danger', 'TAX LAWSUIT': 'danger', DELINQUENT: 'danger', CURRENT: 'live', 'NOT CHECKED': 'estimate',
  'ACTIVE NOTICE': 'danger', POSTPONED: 'danger', CANCELED: 'info', RESOLVED: 'info', UNVERIFIED_FC: 'estimate',
}
const FILTERS = [
  ['priority', 'Priority', ['A', 'B', 'C', 'REVIEW', 'PARK']],
  ['owner', 'Owner', ['VERIFIED', 'UNVERIFIED', 'CONFLICT', 'NO OWNER']],
  ['tax', 'Taxes', ['TAX SALE', 'TAX LAWSUIT', 'DELINQUENT', 'CURRENT', 'NOT CHECKED']],
  ['foreclosure', 'Foreclosure', ['ACTIVE NOTICE', 'POSTPONED', 'CANCELED', 'RESOLVED', 'NO CURRENT EVIDENCE', 'UNVERIFIED']],
]

function Word({ value, fc }) {
  if (!value) return <span className="evo-muted">—</span>
  return <Tag kind={TONE[fc && value === 'UNVERIFIED' ? 'UNVERIFIED_FC' : value]}>{value}</Tag>
}

export default function EvoCohort() {
  const { kind } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    api.get(`/wholesale/evosense/cohorts/${encodeURIComponent(kind)}`).then(setData).catch((e) => setError(errText(e)))
  }, [kind])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!data?.rechecking) return undefined
    const t = setTimeout(load, 15000)
    return () => clearTimeout(t)
  }, [data, load])

  const get = (k) => params.get(k) || ''
  const set = (k, v) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p, { replace: true }) }
  const queue = get('queue') === '1'
  const sort = get('sort') || 'priority'
  const county = get('county')

  const rows = useMemo(() => {
    if (!data) return []
    let rs = data.rows.filter((r) =>
      (!get('priority') || r.priority === get('priority')) &&
      (!get('owner') || r.owner.status === get('owner')) &&
      (!get('tax') || r.tax.status === get('tax')) &&
      (!get('foreclosure') || r.foreclosure.status === get('foreclosure')) &&
      (!county || r.county === county) &&
      (!queue || (r.owner.status === 'VERIFIED' && ['A', 'B', 'C'].includes(r.priority))))
    if (sort === 'score') rs = rs.slice().sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
    if (sort === 'address') rs = rs.slice().sort((a, b) => (a.street_address || '').localeCompare(b.street_address || ''))
    return rs
  }, [data, params])  // eslint-disable-line react-hooks/exhaustive-deps

  async function recheck() {
    setBusy(true); setNote(''); setError('')
    try {
      const r = await api.post(`/wholesale/evosense/cohorts/${encodeURIComponent(kind)}/recheck`)
      setNote(r.status === 'started' ? 'Re-checking public records — county tax files take a few minutes. This page refreshes itself.'
        : 'A re-check of this list is already running.')
      setTimeout(load, 2000)
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const s = data?.summary
  const counties = s ? Object.keys(s.counties).sort() : []
  return (
    <EvoApp world="acquisition">
      <Hero
        scene="street"
        eyebrow="Imported list"
        title={kind === 'top100-2010' ? 'Top 100 (2010 list)' : `List: ${kind}`}
        sub="Every property this list brought in — who owns it now, what the public record shows today, and what to do next. The list's age is never treated as current evidence."
        meta={s ? [
          { label: <><b>{s.properties}</b> properties</> },
          { label: <><b>{s.owner.VERIFIED || 0}</b> verified owners</> },
          { label: <><b>{(s.priority.A || 0) + (s.priority.B || 0)}</b> priority A/B</>, tone: (s.priority.A || s.priority.B) ? 'paused' : undefined },
        ] : null}
        actions={<>
          <button type="button" className="evo-btn evo-btn--primary" disabled={busy || data?.rechecking} onClick={recheck}>
            {data?.rechecking ? 'Re-checking…' : 'Re-check public records'}</button>
          <Link className="evo-btn evo-btn--secondary" to="/wholesale/evosense/inbox">Discovery Inbox</Link>
        </>}
      />
      <Alert>{error}</Alert>
      {note ? <Alert kind="ok">{note}</Alert> : null}
      {data?.last_recheck ? (
        <p className="evo-small evo-muted" style={{ margin: '0 0 10px' }}>
          Last re-checked {new Date(data.last_recheck.at).toLocaleString()} · $0 · nothing sent to anyone.
        </p>
      ) : null}

      {s ? (
        <Panel title="Where the list stands">
          <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 8 }}>
            {FILTERS.map(([k, label]) => Object.entries(k === 'priority' ? s.priority : s[k] || {}).map(([v, n]) => (
              <button key={k + v} type="button" className="evo-bucketbtn" aria-pressed={get(k) === v}
                      onClick={() => set(k, get(k) === v ? '' : v)}>
                {label}: {k === 'priority' ? PRIORITY[v] : v} <span className="evo-seg__n">({n})</span>
              </button>
            )))}
          </div>
          <p className="evo-small evo-muted" style={{ marginTop: 10 }}>
            Scores: {s.score.scored} scored · highest {s.score.max ?? '—'} · median {s.score.median ?? '—'}.
            {' '}{s.with_contacts} with a phone or email on file for the current owner.
          </p>
        </Panel>
      ) : null}

      <Panel flush>
        <div className="evo-filterbar">
          <label className="evo-small" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={queue} onChange={(e) => set('queue', e.target.checked ? '1' : '')} />
            Work queue — verified owner, priority A–C
          </label>
          {FILTERS.map(([k, label, vals]) => (
            <label key={k}><span className="evo-sr">{label}</span>
              <select className="evo-select" value={get(k)} onChange={(e) => set(k, e.target.value)}>
                <option value="">{label}: all</option>
                {vals.map((v) => <option key={v} value={v}>{k === 'priority' ? PRIORITY[v] : v}</option>)}
              </select>
            </label>
          ))}
          <label><span className="evo-sr">County</span>
            <select className="evo-select" value={county} onChange={(e) => set('county', e.target.value)}>
              <option value="">County: all</option>
              {counties.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label><span className="evo-sr">Sort</span>
            <select className="evo-select" value={sort} onChange={(e) => set('sort', e.target.value)}>
              <option value="priority">Priority, then score</option>
              <option value="score">Highest score</option>
              <option value="address">Address A–Z</option>
            </select>
          </label>
        </div>
        {!data && !error ? <div style={{ padding: 20 }}><Skeleton rows={8} height={28} /></div> : null}
        {data && !rows.length ? <Empty title="Nothing matches">Clear a filter to see more of the list.</Empty> : null}
        {rows.length ? (
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards">
              <caption className="evo-sr">Properties from this list</caption>
              <thead>
                <tr>
                  <th scope="col">Property</th>
                  <th scope="col">Priority</th>
                  <th scope="col" className="is-num">Score</th>
                  <th scope="col">Owner now</th>
                  <th scope="col">Taxes</th>
                  <th scope="col">Foreclosure</th>
                  <th scope="col">Other signals</th>
                  <th scope="col">Next action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="is-link" onClick={() => navigate(`/wholesale/evosense/property/${r.id}`)}>
                    <td className="is-lead" data-label="">
                      <Link className="evo-prop__addr" to={`/wholesale/evosense/property/${r.id}`} onClick={(e) => e.stopPropagation()}>
                        {r.street_address}</Link>
                      <span className="evo-prop__sub" style={{ display: 'block' }}>
                        {[r.city, r.county ? `${r.county} County` : null, r.zip_code].filter(Boolean).join(' · ')}
                        {r.ownership_years != null ? ` · owned ${r.ownership_years} yrs` : ''}
                      </span>
                    </td>
                    <td data-label="Priority"><b>{PRIORITY[r.priority]}</b></td>
                    <td className="is-num" data-label="Score" title={[...r.earned.map((f) => `+${f.points} ${f.label}`), ...r.held_back].join('\n')}>
                      <Ring kind="opportunity" value={r.score} />
                    </td>
                    <td data-label="Owner now">
                      <Word value={r.owner.status} /> <span className="evo-small">{r.owner.name || ''}</span>
                      {r.owner.sources?.length ? <span className="evo-prop__sub" style={{ display: 'block' }}>per {r.owner.sources.join(', ')}</span> : null}
                      {(r.owner.flags || []).map((f) => <Tag key={f.key} kind="estimate" title={f.label}>{f.key === 'ESTATE_INDICATED' ? 'Estate indicated' : f.key === 'LIFE_ESTATE' ? 'Life estate' : f.key === 'MULTIPLE_OWNERS_ETAL' ? 'ET AL' : f.key.toLowerCase()}</Tag>)}
                    </td>
                    <td data-label="Taxes" title={r.tax.detail || ''}>
                      <Word value={r.tax.status} />
                      {r.tax.date ? <span className="evo-prop__sub" style={{ display: 'block' }}>{r.tax.date}</span> : null}
                    </td>
                    <td data-label="Foreclosure" title={r.foreclosure.detail || ''}><Word value={r.foreclosure.status} fc /></td>
                    <td data-label="Other signals"><Chips items={r.signals} max={3} /></td>
                    <td data-label="Next action">
                      <span className="evo-small">{r.next_action}</span>
                      {r.missing?.length ? <span className="evo-prop__sub" style={{ display: 'block' }}>Missing: {r.missing.join('; ')}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>
    </EvoApp>
  )
}
