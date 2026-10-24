/* EVOSENSE — AN IMPORTED LIST, FOLLOWED AS A GROUP (e.g. the Top 100).
 *
 * Every property the list brought in, on one screen: who owns it now and how
 * we know, tax and foreclosure status with their evidence, the ordinary score
 * with what earned it and what holds it back, a priority and the next action.
 * "Work queue" narrows to properties a person can act on today.
 *
 * Mortgage foreclosure (a scheduled trustee sale: CLAIMED by the list until a
 * clerk notice VERIFIES it) and property taxes are separate columns - current
 * taxes never cancel a foreclosure. A list's name is never read for dates.
 * Groups A-E: verified scheduled sale / claimed / conflicting or postponed /
 * cancelled-resolved-passed / no notice found (never proof of none).
 *
 * Nothing here imports, buys or sends anything. "Re-check public records"
 * runs the free county lookups, county tax files and county clerk foreclosure
 * notices again for this list, and the panel below says exactly which
 * counties and sources answered. A notice read by hand (Collin and other
 * counties without a clerk source) is recorded with its document number.
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
  'VERIFIED NOTICE': 'danger', CLAIMED: 'estimate', CONFLICT_FC: 'danger', POSTPONED: 'danger', CANCELED: 'info',
  RESOLVED: 'info', 'SALE PASSED': 'info', 'NO NOTICE FOUND': 'estimate', 'NO EVIDENCE': undefined,
  COMPLETED: 'live', PARTIAL: 'estimate', FAILED: 'danger', 'NOT CONNECTED': undefined,
}
const LABEL = { CLAIMED: 'Claimed — source supplied', 'VERIFIED NOTICE': 'Verified clerk notice', 'SALE PASSED': 'Sale date passed',
  'NO NOTICE FOUND': 'No clerk notice found', 'NO EVIDENCE': 'No evidence on file', CONFLICT: 'Conflict' }
const GROUPS = ['A', 'B', 'C', 'D', 'E']
const FILTERS = [
  ['priority', 'Priority', ['A', 'B', 'C', 'REVIEW', 'PARK']],
  ['owner', 'Owner', ['VERIFIED', 'UNVERIFIED', 'CONFLICT', 'NO OWNER']],
  ['tax', 'Taxes', ['TAX SALE', 'TAX LAWSUIT', 'DELINQUENT', 'CURRENT', 'NOT CHECKED']],
  ['foreclosure', 'Foreclosure', ['VERIFIED NOTICE', 'CLAIMED', 'CONFLICT', 'POSTPONED', 'CANCELED', 'RESOLVED',
    'SALE PASSED', 'NO NOTICE FOUND', 'NO EVIDENCE']],
]

function Word({ value, fc }) {
  if (!value) return <span className="evo-muted">—</span>
  return <Tag kind={fc && value === 'CONFLICT' ? TONE.CONFLICT_FC : TONE[value]}>{LABEL[value] || value}</Tag>
}

const COUNTY_WORDS = (k, v) => {
  if (v.status === 'NOT CONNECTED') return v.reason
  if (v.status === 'FAILED' || v.error) return `Failed — ${v.error}`
  return `${v.notices_in_window} notices in the sale window · ${(v.matched_index || 0) + (v.matched_full_text || 0)} of ${v.properties} houses matched`
    + `${v.matched_full_text ? ` (${v.matched_full_text} by the notice text)` : ''} · ${v.no_match} with no notice · ${v.requests} requests`
}

function CheckedPanel({ last, running }) {
  if (running) return <Panel title="Re-check in progress"><p className="evo-small">RUNNING — county owner records, tax files and clerk foreclosure notices are being read now. Results appear here when every source has answered.</p></Panel>
  if (!last) return <Panel title="What has been checked"><p className="evo-small evo-muted">This list has not been re-checked against public records yet.</p></Panel>
  const d = last.details || {}
  const fc = d.foreclosure || {}
  const tax = d.tax || {}
  const outcome = d.outcome || (Object.keys(fc).length ? 'COMPLETED' : 'COMPLETED (before clerk notices were connected)')
  return (
    <Panel title="What the last re-check actually checked">
      <p className="evo-small"><Word value={outcome.split(' ')[0]} /> {new Date(last.at).toLocaleString()} · $0 · nothing sent to anyone.</p>
      {Object.keys(fc).length ? (
        <table className="evo-table evo-small" style={{ marginTop: 8 }}>
          <caption className="evo-sr">Clerk foreclosure notices by county</caption>
          <thead><tr><th scope="col">County clerk notices</th><th scope="col">Result</th><th scope="col">Detail</th></tr></thead>
          <tbody>
            {Object.entries(fc).sort().map(([k, v]) => (
              <tr key={k}><td>{k === 'all' ? 'All counties' : `${k[0].toUpperCase()}${k.slice(1)} County`}</td>
                <td><Word value={v.status || (v.error ? 'FAILED' : 'COMPLETED')} /></td><td>{COUNTY_WORDS(k, v)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <p className="evo-small evo-muted">Clerk foreclosure notices were not part of this re-check (run it again to include them).</p>}
      {Object.keys(tax).length ? (
        <p className="evo-small evo-muted" style={{ marginTop: 8 }}>
          Taxes: {Object.entries(tax).map(([k, v]) => `${k.replace(/_/g, ' ')} — ${v.error ? `failed (${v.error})` : `${v.on_roll ?? v.matched ?? 0} found, ${v.delinquent ?? v.ingested ?? 0} with past-due tax`}`).join(' · ')}
        </p>
      ) : null}
    </Panel>
  )
}

function RecordNotice({ row, onDone, onCancel }) {
  const [f, setF] = useState({ document_number: '', sale_date: row.foreclosure.claimed_sale_date || row.foreclosure.sale_date || '', case_status: 'scheduled', filed_date: '', record_url: '', note: '' })
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  async function save(e) {
    e.preventDefault(); setSaving(true); setErr('')
    try {
      await api.post(`/wholesale/evosense/properties/${row.id}/signals`, { signal_type: 'PRE_FORECLOSURE', ...f })
      onDone()
    } catch (x) { setErr(errText(x)) } finally { setSaving(false) }
  }
  const up = (k) => (e) => setF({ ...f, [k]: e.target.value })
  return (
    <Panel title={`Record the notice you read for ${row.street_address}`}>
      <form onSubmit={save} className="evo-filterbar" style={{ flexWrap: 'wrap', gap: 8 }}>
        <label className="evo-small">Document number on the county record
          <input className="evo-select" value={f.document_number} onChange={up('document_number')} placeholder="e.g. 2026-0099123" /></label>
        <label className="evo-small">Sale date
          <input className="evo-select" type="date" value={f.sale_date} onChange={up('sale_date')} /></label>
        <label className="evo-small">Filed / posted date (optional)
          <input className="evo-select" type="date" value={f.filed_date} onChange={up('filed_date')} /></label>
        <label className="evo-small">Status on the record
          <select className="evo-select" value={f.case_status} onChange={up('case_status')}>
            {['scheduled', 'postponed', 'canceled', 'resolved'].map((v) => <option key={v} value={v}>{v}</option>)}
          </select></label>
        <label className="evo-small">Link to the record (optional)
          <input className="evo-select" value={f.record_url} onChange={up('record_url')} /></label>
        <label className="evo-small">Note (optional)
          <input className="evo-select" value={f.note} onChange={up('note')} /></label>
        <button type="submit" className="evo-btn evo-btn--primary" disabled={saving}>{saving ? 'Saving…' : 'Save notice'}</button>
        <button type="button" className="evo-btn evo-btn--secondary" onClick={onCancel}>Cancel</button>
      </form>
      <p className="evo-small evo-muted">With a document number and sale date this counts as a verified notice (read by a person). Without a document number it is kept as a claim.</p>
      <Alert>{err}</Alert>
    </Panel>
  )
}

const listTitle = (kind) => (/^top100/i.test(kind) ? 'Top 100' : `List: ${kind}`)

export default function EvoCohort() {
  const { kind } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [recordFor, setRecordFor] = useState(null)

  const load = useCallback(() => {
    api.get(`/wholesale/evosense/cohorts/${encodeURIComponent(kind)}`).then(setData).catch((e) => setError(errText(e)))
  }, [kind])
  useEffect(() => { load() }, [load])
  // While a re-check runs, ask only "is it done?" (a few hundred bytes) every
  // 20 s; re-read the full list once, when it has finished.
  const rechecking = !!data?.rechecking
  useEffect(() => {
    if (!rechecking) return undefined
    let stop = false
    const tick = async () => {
      try {
        const st = await api.get(`/wholesale/evosense/cohorts/${encodeURIComponent(kind)}?status_only=1`)
        if (stop) return
        if (!st.rechecking) { load(); return }
      } catch (e) { /* keep waiting; the next tick tries again */ }
      if (!stop) timer = setTimeout(tick, 20000)
    }
    let timer = setTimeout(tick, 20000)
    return () => { stop = true; clearTimeout(timer) }
  }, [rechecking, kind, load])

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
      (!get('group') || r.foreclosure.category === get('group')) &&
      (!county || r.county === county) &&
      (!queue || (r.owner.status === 'VERIFIED' && ['A', 'B', 'C'].includes(r.priority))))
    if (sort === 'score') rs = rs.slice().sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
    if (sort === 'sale') rs = rs.slice().sort((a, b) => (a.foreclosure.days ?? 9999) - (b.foreclosure.days ?? 9999))
    if (sort === 'address') rs = rs.slice().sort((a, b) => (a.street_address || '').localeCompare(b.street_address || ''))
    return rs
  }, [data, params])  // eslint-disable-line react-hooks/exhaustive-deps

  async function recheck() {
    setBusy(true); setNote(''); setError('')
    try {
      const r = await api.post(`/wholesale/evosense/cohorts/${encodeURIComponent(kind)}/recheck`)
      setNote(r.status === 'started' ? 'Re-check started — owner records, county tax files and county clerk foreclosure notices take a few minutes. This page refreshes itself and shows the result when every source has answered.'
        : 'A re-check of this list is already running.')
      if (r.status === 'started') setData((d) => (d ? { ...d, rechecking: true } : d))
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const s = data?.summary
  const counties = s ? Object.keys(s.counties).sort() : []
  return (
    <EvoApp world="acquisition">
      <Hero
        scene="street"
        eyebrow="Imported list"
        title={listTitle(kind)}
        sub="Every property this list brought in — who owns it now, the scheduled foreclosure sale (claimed by the list until a county clerk notice confirms it), taxes, score and the next step."
        meta={s ? [
          { label: <><b>{s.properties}</b> properties</> },
          { label: <><b>{s.owner.VERIFIED || 0}</b> verified owners</> },
          { label: <><b>{(s.sale_dates?.supplied || 0) + (s.sale_dates?.verified || 0)}</b> with a sale date ({s.sale_dates?.verified || 0} verified)</> },
          { label: <><b>{(s.priority.A || 0) + (s.priority.B || 0)}</b> priority A/B</>, tone: (s.priority.A || s.priority.B) ? 'paused' : undefined },
        ] : null}
        actions={<>
          <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !data || data?.rechecking} onClick={recheck}
                  title={!data ? 'Nothing to re-check: this list has no properties' : undefined}>
            {data?.rechecking ? 'Re-checking…' : 'Re-check public records'}</button>
          <Link className="evo-btn evo-btn--secondary" to="/wholesale/evosense/inbox">Discovery Inbox</Link>
        </>}
      />
      <Alert>{error}</Alert>
      {note ? <Alert kind="ok">{note}</Alert> : null}
      {data ? <CheckedPanel last={data.last_recheck} running={data.rechecking} /> : null}
      {recordFor ? <RecordNotice row={recordFor} onCancel={() => setRecordFor(null)}
                                 onDone={() => { setRecordFor(null); setNote('Notice recorded.'); load() }} /> : null}

      {s?.groups ? (
        <Panel title="Foreclosure evidence">
          <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 8 }}>
            {GROUPS.map((g) => (
              <button key={g} type="button" className="evo-bucketbtn" aria-pressed={get('group') === g}
                      onClick={() => set('group', get('group') === g ? '' : g)}>
                {g}. {s.groups[g].label} <span className="evo-seg__n">({s.groups[g].count})</span>
              </button>
            ))}
          </div>
        </Panel>
      ) : null}

      {s ? (
        <Panel title="Where the list stands">
          <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 8 }}>
            {FILTERS.map(([k, label]) => Object.entries(k === 'priority' ? s.priority : s[k] || {}).map(([v, n]) => (
              <button key={k + v} type="button" className="evo-bucketbtn" aria-pressed={get(k) === v}
                      onClick={() => set(k, get(k) === v ? '' : v)}>
                {label}: {k === 'priority' ? PRIORITY[v] : (LABEL[v] || v)} <span className="evo-seg__n">({n})</span>
              </button>
            )))}
          </div>
          <p className="evo-small evo-muted" style={{ marginTop: 10 }}>
            Scores: {s.score.scored} scored · highest {s.score.max ?? '—'} · median {s.score.median ?? '—'}.
            {' '}{s.with_contacts} with a phone or email on file for the current owner.
          </p>
        </Panel>
      ) : null}

      {!data && error ? null : <Panel flush>
        <div className="evo-filterbar">
          <label className="evo-small" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={queue} onChange={(e) => set('queue', e.target.checked ? '1' : '')} />
            Work queue — verified owner, priority A–C
          </label>
          {FILTERS.map(([k, label, vals]) => (
            <label key={k}><span className="evo-sr">{label}</span>
              <select className="evo-select" value={get(k)} onChange={(e) => set(k, e.target.value)}>
                <option value="">{label}: all</option>
                {vals.map((v) => <option key={v} value={v}>{k === 'priority' ? PRIORITY[v] : (LABEL[v] || v)}</option>)}
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
              <option value="sale">Soonest sale date</option>
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
                  <th scope="col">Foreclosure sale</th>
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
                    <td data-label="Foreclosure sale" title={r.foreclosure.detail || ''}>
                      {r.foreclosure.sale_date ? <b style={{ display: 'block' }}>{r.foreclosure.sale_date}{r.foreclosure.days != null && r.foreclosure.days >= 0 ? ` · ${r.foreclosure.days} days` : ''}</b> : null}
                      <span className="evo-small">{r.foreclosure.category}. </span><Word value={r.foreclosure.status} fc />
                      {r.foreclosure.foreclosure_type ? <span className="evo-prop__sub" style={{ display: 'block' }}>{r.foreclosure.foreclosure_type}</span> : null}
                      {r.foreclosure.document ? (
                        <span className="evo-prop__sub" style={{ display: 'block' }}>
                          {r.foreclosure.document_url
                            ? <a href={r.foreclosure.document_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Notice {r.foreclosure.document}</a>
                            : `Notice ${r.foreclosure.document}`}
                          {r.foreclosure.notices?.length > 1 ? ` · ${r.foreclosure.notices.length} notices on file` : ''}
                        </span>) : null}
                      {r.foreclosure.evidence ? (
                        <details className="evo-prop__sub" onClick={(e) => e.stopPropagation()}>
                          <summary>What the notice says</summary>
                          <span style={{ display: 'block', whiteSpace: 'normal', maxWidth: 360 }}>{r.foreclosure.evidence}</span>
                        </details>) : null}
                      {r.foreclosure.claimed_sale_date && r.foreclosure.status !== 'VERIFIED NOTICE' ? <span className="evo-prop__sub" style={{ display: 'block' }}>list claims {r.foreclosure.claimed_sale_date}</span> : null}
                      <span className="evo-prop__sub" style={{ display: 'block' }}>
                        {r.foreclosure.clerk_checked ? `clerk checked ${r.foreclosure.clerk_checked}` : 'clerk not checked'}
                        {r.foreclosure.confidence ? ` · confidence ${r.foreclosure.confidence}` : ''}
                      </span>
                      {['CLAIMED', 'NO NOTICE FOUND', 'CONFLICT', 'SALE PASSED'].includes(r.foreclosure.status) ? (
                        <button type="button" className="evo-btn evo-btn--secondary evo-small" style={{ marginTop: 4 }}
                                onClick={(e) => { e.stopPropagation(); setRecordFor(r); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>
                          Record a notice</button>) : null}
                    </td>
                    <td data-label="Other signals"><Chips items={r.signals} max={3} /></td>
                    <td data-label="Next action">
                      <span className="evo-small">{r.next_action}</span>
                      {r.earned?.length ? <span className="evo-prop__sub" style={{ display: 'block' }}>Why: {r.earned.map((f) => `+${f.points} ${f.label}`).join('; ')}</span> : null}
                      {r.list_claims?.list_owner_name ? <span className="evo-prop__sub" style={{ display: 'block' }}>List's owner name: {r.list_claims.list_owner_name}</span> : null}
                      {r.missing?.length ? <span className="evo-prop__sub" style={{ display: 'block' }}>Missing: {r.missing.join('; ')}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>}
    </EvoApp>
  )
}
