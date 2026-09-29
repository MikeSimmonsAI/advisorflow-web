/**
 * LEAD INTELLIGENCE CONTROL CENTER — /god/lead-intelligence?tab=…
 *
 * One page, five tabs (spec §30): Overview, Qualification Engine, Lead
 * Scraper, Lead Browser, Rules & Routing. The tab is the ?tab= query param so
 * the God rail can link straight to each one.
 *
 * TRUTHFULNESS: every figure comes from /god/lead-intelligence/* — real row
 * counts. A null from the API renders as "Not yet available"; no trend is ever
 * shown because the API does not compute one.
 *
 * SAFETY: nothing on this page sends, enrolls or grants consent. "Route" is an
 * explicit action that stages the selected prospects in the destination
 * organization's Universal Intake for THAT organization to review.
 *
 * The standalone screens stay routed and linked from here:
 *   /god/lead-scraper                 classic scraper (direct import into an org)
 *   /god/lead-browser                 master database browser (also embedded)
 *   /god/diagnostics/qualification    read-only per-org qualification diagnostic
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import GodLeadBrowser from './GodLeadBrowser'
import './LeadIntelligence.css'

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'qualification', label: 'Qualification Engine' },
  { key: 'scraper', label: 'Lead Scraper' },
  { key: 'browser', label: 'Lead Browser' },
  { key: 'routing', label: 'Rules & Routing' },
]

const WINDOWS = [
  { value: '', label: 'All time' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
]

const READY = 'READY_TO_SEND'
const REVIEW = 'REVIEW_REQUIRED'
const EXCLUDED = 'EXCLUDED'
const BUCKET_LABEL = { [READY]: 'Ready', [REVIEW]: 'Review', [EXCLUDED]: 'Excluded' }
const BUCKET_TONE = { [READY]: 'teal', [REVIEW]: 'gold', [EXCLUDED]: 'red' }
const PRIORITY_TONE = { HIGH: 'teal', MEDIUM: 'gold', LOW: 'blue' }
const STAGE_TONE = { qualified: 'blue', duplicate: 'off', invalid: 'off', suppressed: 'red' }

const MILES = [1, 3, 5, 10, 15, 25, 50]
const US_STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']

const fmt = v => (v === null || v === undefined ? '—' : Number(v).toLocaleString())
const pct = (a, b) => (b ? Math.round((a / b) * 100) : null)
const when = iso => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}
const errText = e => e?.detail?.message || (typeof e?.detail === 'string' ? e.detail : null)
  || e?.message || 'Request failed'

function Pill({ tone = 'off', children }) {
  return <span className={'li-pill ' + tone}>{children}</span>
}

function NA() { return <span className="li-na">Not yet available</span> }

function useOrgs() {
  const [orgs, setOrgs] = useState([])
  useEffect(() => {
    api.get('/god/orgs', { params: { limit: 200 } })
      .then(r => setOrgs(Array.isArray(r) ? r : (r?.orgs || [])))
      .catch(() => setOrgs([]))
  }, [])
  return orgs
}

/* ───────────────────────────── OVERVIEW ───────────────────────────── */

function Kpi({ label, value, foot, tone }) {
  return (
    <div className={'li-kpi ' + (tone || '')}>
      <div className="k-label">{label}</div>
      <div className="k-value">{value === null || value === undefined ? <NA /> : fmt(value)}</div>
      {foot && <div className="k-foot">{foot}</div>}
    </div>
  )
}

function Bars({ rows, tone }) {
  const max = Math.max(1, ...rows.map(r => r.count))
  if (!rows.length) return <div className="li-empty">No records yet.</div>
  return (
    <div className="li-bars">
      {rows.map(r => (
        <div className="li-bar-row" key={r.name}>
          <span className="b-name" title={r.name}>{r.name}</span>
          <span className="li-bar-track"><span className={'li-bar-fill ' + (tone || '')}
            style={{ width: Math.max(2, (r.count / max) * 100) + '%', display: 'block' }} /></span>
          <span className="b-count">{fmt(r.count)}</span>
        </div>
      ))}
    </div>
  )
}

function Distribution({ prospects }) {
  const pr = prospects?.by_priority || {}
  const ex = prospects?.by_bucket?.[EXCLUDED] || 0
  const parts = [
    { key: 'HIGH', label: 'High (45+)', v: pr.HIGH || 0, color: 'var(--gm-teal)' },
    { key: 'MEDIUM', label: 'Medium (22–44)', v: pr.MEDIUM || 0, color: 'var(--gm-amber)' },
    { key: 'LOW', label: 'Low (<22)', v: pr.LOW || 0, color: 'var(--gm-blue)' },
    { key: 'EXC', label: 'Excluded', v: ex, color: 'var(--gm-red)' },
  ]
  const total = parts.reduce((a, p) => a + p.v, 0)
  if (!total) return <div className="li-empty">No qualified prospects yet.</div>
  let acc = 0
  const stops = parts.map(p => {
    const from = (acc / total) * 360
    acc += p.v
    return `${p.color} ${from}deg ${(acc / total) * 360}deg`
  }).join(', ')
  return (
    <div className="li-donut-wrap">
      <div className="li-donut" style={{ background: `conic-gradient(${stops})` }}>
        <div className="li-donut-center"><b>{fmt(total)}</b><span>scored</span></div>
      </div>
      <div className="li-legend">
        {parts.map(p => (
          <div key={p.key}><i style={{ background: p.color }} />{p.label} — <b>{fmt(p.v)}</b>
            {' '}<span className="muted">({pct(p.v, total)}%)</span></div>
        ))}
      </div>
    </div>
  )
}

function Overview({ days, go }) {
  const [s, setS] = useState(null)
  const [pl, setPl] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    setErr('')
    const params = days ? { days } : {}
    Promise.all([
      api.get('/god/lead-intelligence/summary', { params }),
      api.get('/god/lead-intelligence/pipeline', { params }),
    ]).then(([a, b]) => { setS(a); setPl(b) }).catch(e => setErr(errText(e)))
  }, [days])

  if (err) return <div className="li-error">{err}</div>
  if (!s || !pl) return <div className="li-empty">Loading…</div>
  const p = s.prospects
  const mp = s.master_pool
  const discovered = pl.stages[0]?.count || 0
  const sources = [
    ...(p.sources || []).map(x => ({ name: 'Scraper · ' + x.source, count: x.count })),
    ...(mp.sources || []).map(x => ({ name: 'Master · ' + x.source, count: x.count })),
  ].sort((a, b) => b.count - a.count).slice(0, 8)

  return (
    <>
      <div className="li-kpis">
        <Kpi label="Prospects (pool)" value={p.total} foot="Discovered by the Lead Scraper" />
        <Kpi label="Qualified — High" tone="high" value={p.by_priority?.HIGH} foot="Score 45+" />
        <Kpi label="Qualified — Medium" tone="med" value={p.by_priority?.MEDIUM} foot="Score 22–44" />
        <Kpi label="Qualified — Low" tone="low" value={p.by_priority?.LOW} foot="Score below 22" />
        <Kpi label="In review" value={p.in_review} foot="REVIEW, not yet routed" />
        <Kpi label="Excluded" tone="exc" value={p.by_bucket?.[EXCLUDED]} foot="Includes suppressed" />
        <Kpi label="Routed" value={p.routed} foot={`${fmt(p.routed_organizations)} organization(s)`} />
        <Kpi label="Master pool" value={mp.total_contacts}
             foot={`${fmt(mp.total_occurrences)} appearances · ${fmt(mp.organizations_represented)} orgs`} />
      </div>

      <div className="li-card" style={{ marginBottom: 16 }}>
        <div className="li-card-head">
          <h3>Lead Flow</h3>
          <span className="li-sub">Discover → Normalize → Dedupe → Suppress → Qualify → Route</span>
        </div>
        <div className="li-flow">
          {pl.stages.map((st, i) => (
            <div key={st.key} style={{ display: 'contents' }}>
              {i > 0 && <span className="li-arrow" aria-hidden>→</span>}
              <div className={'li-stage ' + st.key}>
                <div className="s-label">{st.label}</div>
                <div className="s-count">{fmt(st.count)}</div>
                <div className="s-pct">{discovered && st.key !== 'discovered'
                  ? pct(st.count, discovered) + '%' : ' '}</div>
              </div>
            </div>
          ))}
        </div>
        <p className="li-sub" style={{ marginTop: 10, marginBottom: 0 }}>
          Removed along the way: {fmt(pl.removed.invalid)} invalid · {fmt(pl.removed.duplicate)} duplicate ·
          {' '}{fmt(pl.removed.suppressed)} suppressed · {fmt(pl.removed.excluded)} excluded by qualification.
          {' '}Searches returned {fmt(pl.provider_results)} result(s) in total.
        </p>
      </div>

      <div className="li-grid-3">
        <div className="li-card">
          <h3>Lead Sources</h3>
          <p className="li-sub">Scraper pool and master database, by source</p>
          <Bars rows={sources} />
        </div>
        <div className="li-card">
          <h3>Qualification Distribution</h3>
          <p className="li-sub">Prospect pool, current thresholds</p>
          <Distribution prospects={p} />
          <p className="li-sub" style={{ marginTop: 10, marginBottom: 0 }}>
            Master pool qualification: {mp.qualification ? '' : <NA />} — qualified inside each
            organization by its own engine.
          </p>
        </div>
        <div className="li-card">
          <h3>Recent Activity</h3>
          <p className="li-sub">Searches and routing, newest first</p>
          {s.recent_activity.length === 0 ? <div className="li-empty">No activity yet.</div> : (
            <ul className="li-activity">
              {s.recent_activity.slice(0, 7).map((a, i) => (
                <li key={i}>
                  <span className={'a-dot ' + a.type} />
                  <div style={{ minWidth: 0 }}>
                    <div>{a.label}</div>
                    <div className="a-meta">{a.detail} · {when(a.at)}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="li-grid-2">
        <div className="li-card">
          <div className="li-card-head">
            <h3>Recent Leads</h3>
            <button className="li-btn sm" onClick={() => go('browser')}>View all</button>
          </div>
          {s.recent_prospects.length === 0
            ? <div className="li-empty">Nothing staged yet — run the Lead Scraper to begin.</div>
            : <ProspectTable rows={s.recent_prospects} compact />}
        </div>
        <div className="li-card">
          <h3>Quick Actions</h3>
          <p className="li-sub">Nothing here sends or enrolls anything.</p>
          <div className="li-actions">
            <button className="li-btn primary" onClick={() => go('scraper')}>Run Lead Scraper</button>
            <button className="li-btn" onClick={() => go('browser')}>Browse Leads</button>
            <button className="li-btn" onClick={() => go('qualification')}>Qualification Settings</button>
            <button className="li-btn" onClick={() => go('routing')}>Routing Rules</button>
            <Link className="li-btn" to="/god/lead-scraper" style={{ textAlign: 'center' }}>Classic Scraper</Link>
            <Link className="li-btn" to="/god/diagnostics/qualification" style={{ textAlign: 'center' }}>Qualification Diagnostic</Link>
          </div>
          {s.notes?.length > 0 && <div className="li-note li-mt">{s.notes.join(' ')}</div>}
        </div>
      </div>
    </>
  )
}

/* ───────────────────────────── SHARED TABLE ───────────────────────────── */

function ProspectTable({ rows, compact, selected, onToggle, onToggleAll }) {
  const selectable = !!onToggle
  return (
    <div className="li-table-wrap">
      <table className="li-table">
        <thead>
          <tr>
            {selectable && (
              <th style={{ width: 32 }}>
                <input type="checkbox" aria-label="Select all routable"
                  checked={rows.length > 0 && rows.filter(r => r.routable).every(r => selected.has(r.id))
                    && rows.some(r => r.routable)}
                  onChange={e => onToggleAll(e.target.checked)} />
              </th>
            )}
            <th>Name / Business</th>
            <th>Location</th>
            <th>Score</th>
            <th>Status</th>
            {!compact && <th>Industry</th>}
            {!compact && <th>Reason</th>}
            <th>Routed to</th>
            {!compact && <th>Added</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.id}>
              {selectable && (
                <td>
                  <input type="checkbox" disabled={!r.routable} checked={selected.has(r.id)}
                    title={r.routable ? 'Select' : 'Not routable: ' + r.not_routable_reason}
                    onChange={() => onToggle(r.id)} />
                </td>
              )}
              <td>
                <div style={{ fontWeight: 600 }}>{r.name || '—'}</div>
                <div className="muted">{r.phone || r.email || 'no contact'}</div>
              </td>
              <td className="muted">{[r.city, r.state].filter(Boolean).join(', ') || '—'}</td>
              <td className="num">
                {r.score !== null && r.score !== undefined
                  ? <><b>{r.score}</b> {r.priority && <Pill tone={PRIORITY_TONE[r.priority]}>{r.priority}</Pill>}</>
                  : '—'}
              </td>
              <td>
                {r.stage === 'qualified' || r.stage === 'suppressed'
                  ? <Pill tone={BUCKET_TONE[r.bucket] || 'off'}>{BUCKET_LABEL[r.bucket] || r.bucket}</Pill>
                  : <Pill tone={STAGE_TONE[r.stage] || 'off'}>{r.stage}</Pill>}
                {r.stage === 'suppressed' && <div className="muted">{r.suppressed_reason}</div>}
              </td>
              {!compact && <td className="muted">{r.industry || '—'}</td>}
              {!compact && (
                <td className="muted" style={{ maxWidth: 220 }}>
                  {(r.reasons || []).map(x => x.label || x.code).join('; ') || (r.best_channel ? 'Ready on ' + r.best_channel : '—')}
                </td>
              )}
              <td className="muted">
                {r.routed_org_name || (r.routed_org_id ? r.routed_org_id.slice(0, 8) : '—')}
                {!r.routed_org_id && r.destination_org_name && <div>intended: {r.destination_org_name}</div>}
              </td>
              {!compact && <td className="muted">{when(r.created_at)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ───────────────────────────── QUALIFICATION ───────────────────────────── */

function Qualification() {
  const [cfg, setCfg] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get('/god/lead-intelligence/qualification-config').then(setCfg).catch(e => setErr(errText(e)))
  }, [])
  if (err) return <div className="li-error">{err}</div>
  if (!cfg) return <div className="li-empty">Loading…</div>
  return (
    <>
      <div className="li-card" style={{ marginBottom: 16 }}>
        <h3>Priority bands</h3>
        <p className="li-sub">Scores come from explainable factors in the qualification engine.
          Excluded leads carry no priority.</p>
        <div className="li-bands">
          {cfg.bands.map(b => (
            <div className="li-band" key={b.priority}>
              <Pill tone={PRIORITY_TONE[b.priority]}>{b.priority}</Pill>
              <b>{b.label.split(': ')[1]}</b>
              <p>{b.priority === 'HIGH' ? 'Requires engagement evidence — reachability alone tops out at '
                + (cfg.thresholds.max_score_without_evidence ?? '—') + '.'
                : b.priority === 'MEDIUM' ? 'Reachable with some supporting signal.'
                : 'Reachable, little else known.'}</p>
            </div>
          ))}
        </div>
      </div>
      <div className="li-card" style={{ marginBottom: 16 }}>
        <h3>Statuses</h3>
        <p className="li-sub">Every answer is per channel; the headline status is the best channel.</p>
        <div className="li-bands">
          {cfg.buckets.map(b => (
            <div className="li-band" key={b.bucket}>
              <Pill tone={BUCKET_TONE[b.bucket]}>{b.label}</Pill>
              <p style={{ marginTop: 8 }}>{b.meaning}</p>
            </div>
          ))}
        </div>
        <div className="li-note li-mt">{cfg.channel_policy}</div>
      </div>
      <div className="li-card">
        <div className="li-card-head">
          <h3>Configuration</h3>
          <Pill tone="off">{cfg.editable ? 'Editable' : 'Read-only'}</Pill>
        </div>
        <div className="li-note" style={{ marginTop: 8 }}>{cfg.note}</div>
        <p className="li-sub li-mt" style={{ marginBottom: 0 }}>
          Per-organization view of who may be contacted on each channel, and why not:{' '}
          <Link className="li-link" to="/god/diagnostics/qualification">Open the Qualification Diagnostic →</Link>
        </p>
      </div>
    </>
  )
}

/* ───────────────────────────── SCRAPER ───────────────────────────── */

function Scraper({ go }) {
  const orgs = useOrgs()
  const [industries, setIndustries] = useState([])
  const [f, setF] = useState({ industry: '', query: '', city: '', state: '', radius: 5, max: 20, dest: '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState(null)
  const [staged, setStaged] = useState(null)
  useEffect(() => {
    api.get('/god/lead-intelligence/industries').then(r => setIndustries(r?.industries || [])).catch(() => {})
  }, [])
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))
  const canSearch = (f.industry || f.query.trim().length >= 2) && !busy

  async function search(e) {
    e.preventDefault()
    if (!canSearch) return
    setBusy(true); setErr(''); setRes(null); setStaged(null)
    try {
      const location = [f.city.trim(), f.state].filter(Boolean).join(', ')
      const r = await api.post('/scraper/search', {
        industry: f.industry || undefined,
        query: f.query.trim() || undefined,
        location: location || undefined,
        radius_meters: Math.round(f.radius * 1609.34),
        max_results: f.max,
        destination_org_id: f.dest || undefined,
      })
      setRes(r)
    } catch (ex) { setErr(errText(ex)) } finally { setBusy(false) }
  }

  async function stage() {
    if (!res?.results?.length) return
    setBusy(true); setErr('')
    try {
      const r = await api.post('/scraper/stage', {
        results: res.results, job_id: res.job_id || undefined,
        industry: f.industry || undefined, destination_org_id: f.dest || undefined,
      })
      setStaged(r)
    } catch (ex) { setErr(errText(ex)) } finally { setBusy(false) }
  }

  return (
    <>
      <form className="li-card" onSubmit={search} style={{ marginBottom: 16 }}>
        <h3>Find businesses</h3>
        <p className="li-sub">Results are staged into the Lead Intelligence pool, then normalized, deduplicated,
          checked against suppression and qualified. Nothing is sent to an organization until you route it.</p>
        <div className="li-form">
          <div className="li-field">
            <label htmlFor="li-ind">Industry</label>
            <select id="li-ind" value={f.industry} onChange={e => set('industry', e.target.value)}>
              <option value="">— Any —</option>
              {industries.map(i => <option key={i.key} value={i.key}>{i.label}</option>)}
            </select>
          </div>
          <div className="li-field">
            <label htmlFor="li-q">Search query</label>
            <input id="li-q" value={f.query} onChange={e => set('query', e.target.value)}
              placeholder="e.g. commercial roofing" />
          </div>
          <div className="li-field">
            <label htmlFor="li-city">City</label>
            <input id="li-city" value={f.city} onChange={e => set('city', e.target.value)} placeholder="Dallas" />
          </div>
          <div className="li-field">
            <label htmlFor="li-state">State</label>
            <select id="li-state" value={f.state} onChange={e => set('state', e.target.value)}>
              <option value="">—</option>
              {US_STATES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="li-field">
            <label htmlFor="li-rad">Radius</label>
            <select id="li-rad" value={f.radius} onChange={e => set('radius', +e.target.value)}>
              {MILES.map(m => <option key={m} value={m}>{m} mi</option>)}
            </select>
          </div>
          <div className="li-field">
            <label htmlFor="li-max">Max results</label>
            <select id="li-max" value={f.max} onChange={e => set('max', +e.target.value)}>
              {[10, 20, 30, 40, 60].map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="li-field">
            <label htmlFor="li-dest">Intended destination (optional)</label>
            <select id="li-dest" value={f.dest} onChange={e => set('dest', e.target.value)}>
              <option value="">— None yet —</option>
              {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </div>
          <div className="li-field">
            <button type="submit" className="li-btn primary" disabled={!canSearch} style={{ width: '100%' }}>
              {busy && !res ? 'Searching…' : 'Search'}
            </button>
          </div>
        </div>
        <p className="li-sub li-mt" style={{ marginBottom: 0 }}>
          The destination is recorded on the job as intent only — it is never routed automatically.
          Need to import straight into one organization? <Link className="li-link" to="/god/lead-scraper">Classic scraper →</Link>
        </p>
      </form>

      {err && <div className="li-error">{err}</div>}

      {res && (
        <div className="li-card">
          <div className="li-card-head">
            <h3>{fmt(res.total)} result(s) for “{res.query}”</h3>
            <button className="li-btn primary" disabled={busy || !res.results?.length || !!staged}
              onClick={stage}>{staged ? 'Staged' : 'Stage to Lead Intelligence pool'}</button>
          </div>
          {staged && (
            <div className="li-ok" style={{ marginTop: 10 }}>
              Staged {fmt(staged.counts.discovered)}: {fmt(staged.counts.qualified)} qualified
              ({fmt(staged.counts[READY])} ready, {fmt(staged.counts[REVIEW])} review),
              {' '}{fmt(staged.counts.duplicate)} duplicate, {fmt(staged.counts.suppressed)} suppressed,
              {' '}{fmt(staged.counts.invalid)} without a usable phone or email.{' '}
              <button type="button" className="li-btn sm" onClick={() => go('browser')}>Review & route →</button>
            </div>
          )}
          <div className="li-table-wrap li-mt">
            <table className="li-table">
              <thead><tr><th>Business</th><th>Phone</th><th>Address</th><th>Website</th></tr></thead>
              <tbody>
                {(res.results || []).map((r, i) => (
                  <tr key={r.place_id || i}>
                    <td style={{ fontWeight: 600 }}>{r.name}</td>
                    <td className="muted">{r.phone || '—'}</td>
                    <td className="muted">{r.address || '—'}</td>
                    <td className="muted">{r.website ? r.website.replace(/^https?:\/\//, '') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}

/* ───────────────────────────── BROWSER ───────────────────────────── */

const PAGE = 50

function RoutePanel({ ids, orgs, onDone }) {
  const [dest, setDest] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [preview, setPreview] = useState(null)
  async function doPreview() {
    setErr('')
    try { setPreview(await api.post('/god/lead-intelligence/route/preview', { prospect_ids: ids })) }
    catch (e) { setErr(errText(e)) }
  }
  async function doRoute() {
    if (!dest) { setErr('Choose a destination organization.'); return }
    const org = orgs.find(o => String(o.id) === String(dest))
    if (!window.confirm(`Stage ${ids.length} prospect(s) in ${org?.name || 'this organization'}'s Universal Intake for review?\n\nNo leads are created and nothing is sent.`)) return
    setBusy(true); setErr('')
    try { onDone(await api.post('/god/lead-intelligence/route', { prospect_ids: ids, destination_org_id: dest })) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const suggested = useMemo(() => {
    const c = {}
    for (const s of preview?.suggestions || []) if (s.destination_org_id) {
      c[s.destination_org_id] = c[s.destination_org_id] || { name: s.destination_org_name, n: 0 }
      c[s.destination_org_id].n += 1
    }
    return Object.entries(c)
  }, [preview])
  return (
    <div className="li-card" style={{ marginBottom: 12, background: 'var(--gm-panel-2)' }}>
      <div className="li-toolbar" style={{ marginBottom: 0 }}>
        <b>{ids.length} selected</b>
        <div className="li-field" style={{ minWidth: 220 }}>
          <select aria-label="Destination organization" value={dest} onChange={e => setDest(e.target.value)}>
            <option value="">— Destination organization —</option>
            {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </div>
        <button className="li-btn" onClick={doPreview}>Suggest from rules</button>
        <button className="li-btn primary" disabled={busy || !dest} onClick={doRoute}>
          {busy ? 'Routing…' : 'Route to Universal Intake'}
        </button>
      </div>
      {suggested.length > 0 && (
        <div className="li-sub li-mt">Rules suggest: {suggested.map(([id, v]) => (
          <button key={id} className="li-btn sm" style={{ marginRight: 6 }} onClick={() => setDest(id)}>
            {v.name || id.slice(0, 8)} ({v.n})</button>))}
        </div>
      )}
      {preview && suggested.length === 0 && <div className="li-sub li-mt">No active rule matches the selection.</div>}
      {err && <div className="li-error li-mt">{err}</div>}
    </div>
  )
}

function ProspectBrowser() {
  const orgs = useOrgs()
  const [q, setQ] = useState({ search: '', bucket: '', priority: '', stage: '', industry: '', state: '',
    city: '', score_min: '', score_max: '', routed: '', destination_org_id: '' })
  const [skip, setSkip] = useState(0)
  const [data, setData] = useState({ total: 0, rows: [] })
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState(new Set())
  const [done, setDone] = useState(null)

  const load = useCallback(async (s = 0) => {
    setLoading(true); setErr('')
    try {
      const r = await api.get('/god/lead-intelligence/prospects', { params: { ...q, skip: s, limit: PAGE } })
      setData(r); setSkip(s)
    } catch (e) { setErr(errText(e)) } finally { setLoading(false) }
  }, [q])
  useEffect(() => { const t = setTimeout(() => load(0), 250); return () => clearTimeout(t) }, [load])

  const set = (k, v) => setQ(p => ({ ...p, [k]: v }))
  const toggle = id => setSel(p => { const s = new Set(p); s.has(id) ? s.delete(id) : s.add(id); return s })
  const toggleAll = on => setSel(p => {
    const s = new Set(p)
    data.rows.filter(r => r.routable).forEach(r => (on ? s.add(r.id) : s.delete(r.id)))
    return s
  })

  return (
    <>
      <div className="li-card" style={{ marginBottom: 12 }}>
        <div className="li-form">
          <div className="li-field"><label>Search</label>
            <input value={q.search} onChange={e => set('search', e.target.value)} placeholder="Name, phone, email, website" /></div>
          <div className="li-field"><label>Status</label>
            <select value={q.bucket} onChange={e => set('bucket', e.target.value)}>
              <option value="">All</option><option value={READY}>Ready</option>
              <option value={REVIEW}>Review</option><option value={EXCLUDED}>Excluded</option></select></div>
          <div className="li-field"><label>Priority</label>
            <select value={q.priority} onChange={e => set('priority', e.target.value)}>
              <option value="">All</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select></div>
          <div className="li-field"><label>Stage</label>
            <select value={q.stage} onChange={e => set('stage', e.target.value)}>
              <option value="">All</option><option value="qualified">Qualified</option>
              <option value="suppressed">Suppressed</option><option value="duplicate">Duplicate</option>
              <option value="invalid">Invalid</option></select></div>
          <div className="li-field"><label>Industry</label>
            <input value={q.industry} onChange={e => set('industry', e.target.value)} placeholder="e.g. roofing" /></div>
          <div className="li-field"><label>State</label>
            <select value={q.state} onChange={e => set('state', e.target.value)}>
              <option value="">All</option>{US_STATES.map(s => <option key={s}>{s}</option>)}</select></div>
          <div className="li-field"><label>City</label>
            <input value={q.city} onChange={e => set('city', e.target.value)} /></div>
          <div className="li-field"><label>Score min / max</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input type="number" value={q.score_min} onChange={e => set('score_min', e.target.value)} placeholder="min" />
              <input type="number" value={q.score_max} onChange={e => set('score_max', e.target.value)} placeholder="max" />
            </div></div>
          <div className="li-field"><label>Routing</label>
            <select value={q.routed} onChange={e => set('routed', e.target.value)}>
              <option value="">All</option><option value="false">Not routed</option><option value="true">Routed</option></select></div>
          <div className="li-field"><label>Organization</label>
            <select value={q.destination_org_id} onChange={e => set('destination_org_id', e.target.value)}>
              <option value="">All</option>{orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></div>
        </div>
      </div>

      {done && (
        <div className="li-ok">Routed {fmt(done.routed)} to {done.destination_org_name} — batch {done.batch_code},
          staged for that organization's review. {done.refused?.length ? `${done.refused.length} refused (${[...new Set(done.refused.map(r => r.reason))].join(', ')}).` : ''} {done.note}</div>
      )}
      {sel.size > 0 && <RoutePanel ids={[...sel]} orgs={orgs}
        onDone={r => { setDone(r); setSel(new Set()); load(skip) }} />}
      {err && <div className="li-error">{err}</div>}

      <div className="li-card">
        <div className="li-card-head">
          <h3>{fmt(data.total)} prospect(s)</h3>
          {loading && <span className="li-sub">Loading…</span>}
        </div>
        {data.rows.length === 0 && !loading
          ? <div className="li-empty">No prospects match these filters.</div>
          : <ProspectTable rows={data.rows} selected={sel} onToggle={toggle} onToggleAll={toggleAll} />}
        <div className="li-pager">
          <span>{data.total ? `${skip + 1}–${Math.min(skip + PAGE, data.total)} of ${fmt(data.total)}` : ''}</span>
          <span style={{ display: 'flex', gap: 6 }}>
            <button className="li-btn sm" disabled={skip === 0 || loading} onClick={() => load(Math.max(0, skip - PAGE))}>← Prev</button>
            <button className="li-btn sm" disabled={skip + PAGE >= data.total || loading} onClick={() => load(skip + PAGE)}>Next →</button>
          </span>
        </div>
      </div>
    </>
  )
}

function Browser() {
  const [view, setView] = useState('pool')
  return (
    <>
      <div className="li-toolbar">
        <button className={'li-tab ' + (view === 'pool' ? 'on' : '')} onClick={() => setView('pool')}>Prospect pool</button>
        <button className={'li-tab ' + (view === 'master' ? 'on' : '')} onClick={() => setView('master')}>Master database</button>
        <Link className="li-link" to="/god/lead-browser" style={{ marginLeft: 'auto' }}>Open full-page Lead Browser →</Link>
      </div>
      {view === 'pool' ? <ProspectBrowser /> : <div className="li-card" style={{ padding: 0 }}><GodLeadBrowser /></div>}
    </>
  )
}

/* ───────────────────────────── ROUTING ───────────────────────────── */

const EMPTY_RULE = { name: '', destination_org_id: '', match_industry: '', match_state: '', match_city: '',
  min_score: '', allowed_buckets: [READY], sort_order: 100, is_active: true }

function Routing({ go }) {
  const orgs = useOrgs()
  const [rules, setRules] = useState(null)
  const [form, setForm] = useState(EMPTY_RULE)
  const [editing, setEditing] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api.get('/god/lead-intelligence/routing-rules')
    .then(r => setRules(r.rules || [])).catch(e => setErr(errText(e)))
  useEffect(() => { load() }, [])
  const set = (k, v) => setForm(p => ({ ...p, [k]: v }))
  const toggleBucket = b => setForm(p => ({ ...p, allowed_buckets: p.allowed_buckets.includes(b)
    ? p.allowed_buckets.filter(x => x !== b) : [...p.allowed_buckets, b] }))

  async function save(e) {
    e.preventDefault(); setErr('')
    const body = { ...form, min_score: form.min_score === '' ? null : Number(form.min_score),
      sort_order: Number(form.sort_order) || 100 }
    try {
      if (editing) await api.put('/god/lead-intelligence/routing-rules/' + editing, body)
      else await api.post('/god/lead-intelligence/routing-rules', body)
      setForm(EMPTY_RULE); setEditing(null); load()
    } catch (ex) { setErr(errText(ex)) }
  }
  async function remove(id) {
    if (!window.confirm('Delete this routing rule?')) return
    try { await api.delete('/god/lead-intelligence/routing-rules/' + id); load() } catch (ex) { setErr(errText(ex)) }
  }
  function edit(r) {
    setEditing(r.id)
    setForm({ name: r.name, destination_org_id: r.destination_org_id, match_industry: r.match_industry || '',
      match_state: r.match_state || '', match_city: r.match_city || '', min_score: r.min_score ?? '',
      allowed_buckets: r.allowed_buckets, sort_order: r.sort_order, is_active: r.is_active })
  }

  return (
    <>
      <div className="li-note" style={{ marginBottom: 16 }}>
        Rules only <b>suggest</b> a destination. Routing is always an explicit action from the Lead Browser, it
        re-checks the destination's suppression and DNC records, refuses excluded prospects, and stages the
        records in that organization's Universal Intake for review. No leads are created, no consent is granted,
        nothing is sent. <button className="li-btn sm" onClick={() => go('browser')}>Go to Lead Browser →</button>
      </div>
      {err && <div className="li-error">{err}</div>}
      <div className="li-grid-2">
        <div className="li-card">
          <h3>Routing rules</h3>
          <p className="li-sub">Evaluated in order; the first matching active rule is the suggestion.</p>
          {rules === null ? <div className="li-empty">Loading…</div> : rules.length === 0
            ? <div className="li-empty">No rules yet.</div> : (
              <div className="li-table-wrap">
                <table className="li-table">
                  <thead><tr><th>#</th><th>Rule</th><th>Matches</th><th>Destination</th><th>State</th><th /></tr></thead>
                  <tbody>
                    {rules.map(r => (
                      <tr key={r.id}>
                        <td className="num">{r.sort_order}</td>
                        <td style={{ fontWeight: 600 }}>{r.name}</td>
                        <td className="muted">
                          {[r.match_industry, r.match_city, r.match_state].filter(Boolean).join(' · ') || 'Any'}
                          {r.min_score !== null && r.min_score !== undefined && <> · score ≥ {r.min_score}</>}
                          <div>{r.allowed_buckets.map(b => BUCKET_LABEL[b]).join(', ')}</div>
                        </td>
                        <td>{r.destination_org_name || r.destination_org_id}</td>
                        <td>{r.is_active ? <Pill tone="teal">Active</Pill> : <Pill>Off</Pill>}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <button className="li-btn sm" onClick={() => edit(r)}>Edit</button>{' '}
                          <button className="li-btn sm" onClick={() => remove(r.id)}>Delete</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </div>
        <form className="li-card" onSubmit={save}>
          <h3>{editing ? 'Edit rule' : 'New rule'}</h3>
          <div className="li-form" style={{ gridTemplateColumns: '1fr' }}>
            <div className="li-field"><label>Name</label>
              <input required value={form.name} onChange={e => set('name', e.target.value)} /></div>
            <div className="li-field"><label>Destination organization</label>
              <select required value={form.destination_org_id} onChange={e => set('destination_org_id', e.target.value)}>
                <option value="">— Choose —</option>{orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select></div>
            <div className="li-field"><label>Industry (key)</label>
              <input value={form.match_industry} onChange={e => set('match_industry', e.target.value)} placeholder="e.g. roofing" /></div>
            <div className="li-field"><label>City / State</label>
              <div style={{ display: 'flex', gap: 6 }}>
                <input value={form.match_city} onChange={e => set('match_city', e.target.value)} placeholder="City" />
                <select value={form.match_state} onChange={e => set('match_state', e.target.value)}>
                  <option value="">Any</option>{US_STATES.map(s => <option key={s}>{s}</option>)}</select>
              </div></div>
            <div className="li-field"><label>Minimum score</label>
              <input type="number" value={form.min_score} onChange={e => set('min_score', e.target.value)} /></div>
            <div className="li-field"><label>Applies to</label>
              <div style={{ display: 'flex', gap: 12, fontSize: 13 }}>
                {[READY, REVIEW].map(b => (
                  <label key={b} style={{ display: 'flex', gap: 4, alignItems: 'center', fontWeight: 500 }}>
                    <input type="checkbox" checked={form.allowed_buckets.includes(b)} onChange={() => toggleBucket(b)} />
                    {BUCKET_LABEL[b]}</label>))}
              </div></div>
            <div className="li-field"><label>Order</label>
              <input type="number" value={form.sort_order} onChange={e => set('sort_order', e.target.value)} /></div>
            <label style={{ display: 'flex', gap: 6, fontSize: 13 }}>
              <input type="checkbox" checked={form.is_active} onChange={e => set('is_active', e.target.checked)} /> Active</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="li-btn primary" type="submit">{editing ? 'Save rule' : 'Add rule'}</button>
              {editing && <button className="li-btn" type="button" onClick={() => { setEditing(null); setForm(EMPTY_RULE) }}>Cancel</button>}
            </div>
          </div>
        </form>
      </div>
    </>
  )
}

/* ───────────────────────────── PAGE ───────────────────────────── */

export default function LeadIntelligence() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab') || 'overview'
  const tab = TABS.some(t => t.key === raw) ? raw : 'overview'
  const [days, setDays] = useState('')
  const go = key => setParams(key === 'overview' ? {} : { tab: key })

  return (
    <div className="li-page">
      <div className="li-crumb">Lead Intelligence › {TABS.find(t => t.key === tab).label}</div>
      <div className="li-head">
        <div>
          <h1>Lead Intelligence Control Center</h1>
          <p>Find, qualify, and route leads to the right organizations.</p>
        </div>
        <div className="li-head-actions">
          {tab === 'overview' && (
            <select aria-label="Time window" className="li-btn" value={days} onChange={e => setDays(e.target.value)}>
              {WINDOWS.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
            </select>
          )}
          <button className="li-btn primary" onClick={() => go('scraper')}>Run Lead Scraper</button>
        </div>
      </div>
      <div className="li-tabs" role="tablist">
        {TABS.map(t => (
          <button key={t.key} role="tab" aria-selected={tab === t.key}
            className={'li-tab ' + (tab === t.key ? 'on' : '')} onClick={() => go(t.key)}>{t.label}</button>
        ))}
      </div>
      {tab === 'overview' && <Overview days={days} go={go} />}
      {tab === 'qualification' && <Qualification />}
      {tab === 'scraper' && <Scraper go={go} />}
      {tab === 'browser' && <Browser />}
      {tab === 'routing' && <Routing go={go} />}
    </div>
  )
}
