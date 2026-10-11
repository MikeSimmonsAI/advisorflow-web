/* Providers & Controls — shared pieces: status pills, the latest-result cell,
 * the filter bar, capability cards, the county matrix, the provider and
 * capability drawers, and the paid-enable confirmation.
 * Every action here calls a handler the page passes in (the same server
 * endpoints the page always used); nothing is simulated. */
import { useEffect, useId, useRef, useState } from 'react'
import { Drawer, Glyph, ago, cents } from '../../ds/ds'
import * as M from './model'

const TONE_CLASS = { good: 'is-good', danger: 'is-danger', attention: 'is-warn', info: 'is-info', quiet: 'is-quiet', none: 'is-none' }
const TONE_SYMBOL = { good: '●', danger: '▲', attention: '■', info: '◆', quiet: '○', none: '–' }

/** A status with a shape as well as a colour, so meaning never rests on colour alone. */
export function Pill({ tone = 'quiet', children, title }) {
  return (
    <span className={`pc-pill ${TONE_CLASS[tone] || 'is-quiet'}`} title={title}>
      <span className="pc-pill__dot" aria-hidden="true">{TONE_SYMBOL[tone] || '○'}</span>{children}
    </span>
  )
}

const KIND_SHORT = { 'Public-record connector': 'Public record' }
export function KindTag({ p, compact }) {
  const k = p.connector_kind === 'real' && M.isPaid(p) ? 'paid' : p.connector_kind
  const label = M.kindLabel(p)
  return (
    <span className="pc-kindwrap">
      <span className={`pc-kind pc-kind--${k}`} title={label}>{compact ? (KIND_SHORT[label] || label) : label}</span>
      {M.isEvaluationOnly(p) ? <span className="pc-kind pc-kind--eval" title="Callable by Provider Evaluation only; never used by the engine">Evaluation only</span> : null}
    </span>
  )
}

const LATEST_SHORT = { ok: 'Succeeded', failed: 'Failed', blocked: 'Blocked', rate_limited: 'Rate limited', untested: 'Not yet tested',
                       never: 'Never called', na: 'Never called', manual: 'Not called' }
export function Latest({ p, compact }) {
  const l = M.latestOf(p)
  return (
    <div className="pc-latest">
      <Pill tone={l.tone} title={l.label}>{compact ? (LATEST_SHORT[l.key] || l.label) : l.label}</Pill>
      {l.at ? <span className="pc-sub">{ago(new Date(l.at).toISOString())}</span> : null}
      {l.key === 'failed' || l.key === 'blocked' ? <span className={`pc-sub pc-sub--danger${compact ? ' pc-clamp' : ''}`} title={l.detail}>{l.detail}</span> : null}
      {!compact && l.key === 'ok' && l.records != null ? <span className="pc-sub">{l.records} record{l.records === 1 ? '' : 's'}</span> : null}
      {l.key === 'ok' && l.earlier ? (
        <span className={`pc-sub${l.earlier.recent ? ' pc-sub--warn' : ''}`}>
          Earlier failure ({l.earlier.detail}) {ago(new Date(l.earlier.at).toISOString())} · recovered
        </span>
      ) : null}
    </div>
  )
}

export function Stat({ label, value, sub, tone, onClick, ariaLabel }) {
  const body = (
    <>
      <span className="pc-stat__label">{label}</span>
      <span className="pc-stat__value">{value}</span>
      {sub ? <span className="pc-stat__sub">{sub}</span> : null}
    </>
  )
  return onClick
    ? <button type="button" className={`pc-stat is-button${tone ? ' is-' + tone : ''}`} onClick={onClick} aria-label={ariaLabel}>{body}</button>
    : <div className={`pc-stat${tone ? ' is-' + tone : ''}`}>{body}</div>
}

/** Search + county + capability + status. Values live in the URL (the page owns them). */
export function FilterBar({ value, onChange, counties, capabilities, showCap = true, showStatus = true, placeholder, count,
                           statusOptions = M.GROUPS }) {
  const id = useId()
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value })
  const active = ['q', 'county', 'cap', 'status'].filter((k) => value[k]).length
  return (
    <div className="pc-filters" role="search" aria-label="Filter providers">
      <label className="pc-filters__search" htmlFor={`${id}-q`}>
        <span className="evo-sr">Search</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></svg>
        <input id={`${id}-q`} type="search" value={value.q || ''} placeholder={placeholder || 'Search name, county, capability…'} onChange={set('q')} />
      </label>
      <label className="pc-filters__sel"><span className="evo-sr">County</span>
        <select value={value.county || ''} onChange={set('county')}>
          <option value="">All counties</option>
          {counties.map((c) => <option key={c} value={c}>{c} County</option>)}
        </select>
      </label>
      {showCap ? (
        <label className="pc-filters__sel"><span className="evo-sr">Capability</span>
          <select value={value.cap || ''} onChange={set('cap')}>
            <option value="">All capabilities</option>
            {capabilities.map((c) => <option key={c} value={c}>{M.capName(c)}</option>)}
          </select>
        </label>
      ) : null}
      {showStatus ? (
        <label className="pc-filters__sel"><span className="evo-sr">Status</span>
          <select value={value.status || ''} onChange={set('status')}>
            <option value="">All statuses</option>
            {statusOptions.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
      ) : null}
      {active ? <button type="button" className="pc-linkbtn" onClick={() => onChange({})}>Clear filters</button> : null}
      {count != null ? <span className="pc-filters__count" aria-live="polite">{count}</span> : null}
    </div>
  )
}

/** A compact capability card: icon, name, purpose, coverage, best source, status. */
export function CapabilityCard({ cap, providers, counties, onOpen, selected }) {
  const st = M.capabilityStatus(cap, providers)
  const cov = M.coverageFor(cap.capability, providers, counties)
  const best = M.bestProvider(cap, providers)
  return (
    <button type="button" className={`pc-card${selected ? ' is-selected' : ''}`} onClick={() => onOpen(cap.capability)}
            aria-label={`${M.capName(cap.capability)}: ${st.label}. ${cov.live} of ${cov.total} counties live. Open details.`}>
      <span className="pc-card__top">
        <span className="pc-card__icon" aria-hidden="true"><Glyph name={M.CAP_ICON[cap.capability] || 'layers'} /></span>
        <Pill tone={st.tone}>{st.label}</Pill>
      </span>
      <span className="pc-card__name">{M.capName(cap.capability)}</span>
      <span className="pc-card__desc">{M.CAP_PURPOSE[cap.capability] || ''}</span>
      <span className="pc-card__foot">
        <span className="pc-cov" title="Counties with a live, county-wide source">
          <b>{cov.live}</b>/{cov.total} counties live{cov.cityOnly ? <> · {cov.cityOnly} city-only</> : null}
        </span>
        <span className="pc-card__best">{best ? M.shortLabel(best.label) : 'No source'}</span>
        {st.key === 'live' && st.issues.length ? <span className="pc-card__issue">▲ {st.issues.length} issue{st.issues.length === 1 ? '' : 's'}</span> : null}
      </span>
    </button>
  )
}

export function CountyMatrix({ rows, onOpenProvider }) {
  return (
    <div className="pc-matrix" role="table" aria-label="County coverage">
      <div className="pc-matrix__row is-head" role="row">
        <span role="columnheader">County</span><span role="columnheader">Coverage</span><span role="columnheader">Best source</span>
      </div>
      {rows.map((r) => (
        <div key={r.county} className="pc-matrix__row" role="row">
          <span role="cell" className="pc-strong">{r.county}</span>
          <span role="cell"><Pill tone={M.CELL_TONE[r.cell]}>{M.CELL_LABEL[r.cell]}</Pill></span>
          <span role="cell">
            {r.best ? (
              <button type="button" className="pc-linkbtn" onClick={() => onOpenProvider(r.best.key)}>{M.shortLabel(r.best.label)}</button>
            ) : <span className="pc-muted">—</span>}
            {r.sources.length > 1 ? <span className="pc-sub">+{r.sources.length - 1} more</span> : null}
          </span>
        </div>
      ))}
    </div>
  )
}

/** Detail for one engine capability: county matrix, every attached source. */
export function CapabilityDrawer({ cap, providers, counties, onClose, onOpenProvider }) {
  if (!cap) return null
  const st = M.capabilityStatus(cap, providers)
  const cov = M.coverageFor(cap.capability, providers, counties)
  const attached = M.sortProviders(providers.filter((p) => (p.capabilities || []).includes(cap.capability)))
  return (
    <Drawer open title={M.capName(cap.capability)} sub={M.CAP_PURPOSE[cap.capability]} onClose={onClose} wide>
      <div className="pc-drawer">
        <div className="pc-drawer__status">
          <Pill tone={st.tone}>{st.label}</Pill>
          <span className="pc-muted">{cov.live}/{cov.total} counties with a live county-wide source{cov.cityOnly ? ` · ${cov.cityOnly} with a city-only source` : ''}</span>
        </div>
        {cap.note ? <p className="pc-note">{cap.note}</p> : null}
        {st.issues.length ? (
          <div className="pc-note is-danger" role="alert">
            {st.issues.map((p) => <div key={p.key}><b>{M.shortLabel(p.label)}</b>: {M.latestOf(p).detail || p.why}</div>)}
          </div>
        ) : null}
        <h3 className="pc-h3">County coverage</h3>
        {counties.length ? <CountyMatrix rows={cov.rows} onOpenProvider={onOpenProvider} /> : <p className="pc-muted">No county is covered by any source yet.</p>}
        {cov.anyManual.length ? (
          <p className="pc-sub" style={{ marginTop: 8 }}>Manual entry and file import work for any county: {cov.anyManual.map((p) => p.label).join(', ')}.</p>
        ) : null}
        <h3 className="pc-h3">Attached sources ({attached.length})</h3>
        <ul className="pc-srclist">
          {attached.map((p) => (
            <li key={p.key}>
              <button type="button" className="pc-srclist__btn" onClick={() => onOpenProvider(p.key)}>
                <span className="pc-srclist__name">{p.label}</span>
                <span className="pc-srclist__meta"><KindTag p={p} /><span className="pc-sub">{M.scopeOf(p).label}</span><span className="pc-sub">{p.cost}</span></span>
                <Latest p={p} compact />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </Drawer>
  )
}

function Row({ k, children }) {
  return <><dt>{k}</dt><dd>{children}</dd></>
}

/** Detail for one provider: every dimension, evidence and the safe actions. */
export function ProviderDrawer({ p, isAdmin, busy, onClose, onToggle, onVerify, extra, controls }) {
  const [approving, setApproving] = useState(false)
  useEffect(() => { setApproving(false) }, [p && p.key, p && p.enabled])
  if (!p) return null
  const l = M.latestOf(p)
  const conf = M.configOf(p)
  const en = M.enabledOf(p)
  const scope = M.scopeOf(p)
  const real = p.connector_kind === 'real'
  const canToggle = ['real', 'sandbox'].includes(p.connector_kind) && p.state !== 'NOT CONFIGURED'
  const footer = (
    <div className="pc-actions">
      {canToggle ? (
        <button type="button" className={`evo-btn ${p.enabled ? 'evo-btn--ghost' : 'evo-btn--primary'}`} disabled={busy || !isAdmin}
                onClick={() => (!p.enabled && M.isPaid(p) ? setApproving(true) : onToggle(p, !p.enabled))}>
          {p.enabled ? 'Switch off' : (M.isPaid(p) ? 'Switch on (paid)…' : 'Switch on')}</button>
      ) : null}
      {real && p.enabled && p.state !== 'BLOCKED' && p.state !== 'NOT CONFIGURED' ? (
        <button type="button" className="evo-btn evo-btn--ghost" disabled={busy || !isAdmin || M.isPaid(p)}
                title={M.isPaid(p) ? 'A paid source is never test-called from here' : 'One cheap request to the live source'}
                onClick={() => onVerify(p.key)}>{busy ? 'Working…' : 'Test connection'}</button>
      ) : null}
      {real && p.enabled && p.state === 'BLOCKED' ? (
        <button type="button" className="evo-btn evo-btn--ghost" disabled={busy || !isAdmin}
                title="Blocked platform-wide. Only a platform admin's re-test (one request) is sent; anyone else is refused without calling the source."
                onClick={() => onVerify(p.key)}>Platform re-test</button>
      ) : null}
      {!isAdmin ? <span className="pc-sub">Switching and testing need a workspace admin.</span> : null}
    </div>
  )
  return (
    <Drawer open title={p.label} sub={p.coverage || p.jurisdiction} onClose={onClose} footer={footer} wide>
      <div className="pc-drawer">
        <div className="pc-drawer__status">
          <KindTag p={p} />
          <Pill tone={l.tone}>{l.label}</Pill>
          <span className="pc-muted">{p.why}</span>
        </div>
        {(l.key === 'failed' || l.key === 'blocked') ? (
          <div className="pc-note is-danger" role="alert">
            <b>{l.key === 'blocked' ? 'Blocked' : 'Latest attempt failed'}</b>{l.at ? ` ${ago(new Date(l.at).toISOString())}` : ''}: {l.detail}
            {l.streak ? ` · ${l.streak} failure${l.streak === 1 ? '' : 's'} in a row` : ''}
          </div>
        ) : null}
        {l.key === 'ok' && l.earlier ? (
          <div className={`pc-note${l.earlier.recent ? ' is-warn' : ''}`}>
            Recovered: an earlier attempt failed ({l.earlier.detail}) {ago(new Date(l.earlier.at).toISOString())}; a newer one succeeded {ago(new Date(l.at).toISOString())}.
          </div>
        ) : null}
        <div className="pc-dims">
          <div><span className="pc-dims__k">Kind</span><span>{M.kindLabel(p)}</span></div>
          <div><span className="pc-dims__k">Configuration</span><Pill tone={conf.tone}>{conf.label}</Pill></div>
          <div><span className="pc-dims__k">Switched on</span><Pill tone={en.tone}>{en.label}</Pill></div>
          <div><span className="pc-dims__k">Runtime state</span><span>{p.state}</span></div>
          <div><span className="pc-dims__k">Scope</span><span>{scope.label}</span></div>
          <div><span className="pc-dims__k">Cost</span><span>{p.cost}</span></div>
        </div>
        <h3 className="pc-h3">Evidence</h3>
        <dl className="pc-kv">
          <Row k="Last success">{p.last_success_at ? `${ago(p.last_success_at)} · ${new Date(p.last_success_at).toLocaleString()}` : 'Never'}</Row>
          <Row k="Last failure">{p.last_failure_at ? `${ago(p.last_failure_at)} · ${p.last_failure_reason || 'error'}` : 'None recorded'}</Row>
          <Row k="Calls">{p.calls_total ? `${p.successes_total}/${p.calls_total} succeeded` : 'No calls yet'}</Row>
          {p.last_verified_at ? <Row k="Last verified">{ago(p.last_verified_at)}</Row> : null}
          {p.last_record_count != null ? <Row k="Records last run">{p.last_record_count}</Row> : null}
          {p.consecutive_failures ? <Row k="Failures in a row">{p.consecutive_failures}</Row> : null}
          {p.degraded_until ? <Row k="Backing off until">{new Date(p.degraded_until).toLocaleString()}</Row> : null}
          {p.freshness_days ? <Row k="Data counts as fresh for">{p.freshness_days} days</Row> : null}
          {Object.keys(p.costs || {}).length ? (
            <Row k={p.connector_kind === 'sandbox' ? 'Simulated cost' : 'Cost per call'}>
              {Object.entries(p.costs).map(([k, v]) => `${M.capName(k)} ${cents(v)}`).join(' · ')}</Row>
          ) : null}
        </dl>
        <h3 className="pc-h3">What it provides</h3>
        <div className="pc-chips">{(p.capabilities || []).map((c) => <span key={c} className="pc-chip">{M.capName(c)}</span>)}</div>
        {scope.counties.length ? <p className="pc-sub" style={{ marginTop: 8 }}>Counties: {scope.counties.join(', ')}{scope.key === 'city' ? ` (${scope.label.replace(/ only$/, '')} only — not county-wide)` : ''}</p> : null}
        {p.jurisdiction || p.source_type || p.access_method ? (
          <>
            <h3 className="pc-h3">Source</h3>
            <dl className="pc-kv">
              {p.jurisdiction ? <Row k="Jurisdiction">{p.jurisdiction}</Row> : null}
              {p.source_type ? <Row k="Type">{p.source_type}{p.role ? ` · ${p.role}` : ''}</Row> : null}
              {p.access_method ? <Row k="Access">{p.access_method}</Row> : null}
              {p.refresh ? <Row k="Refresh">{p.refresh}</Row> : null}
              {p.adapter_version ? <Row k="Adapter">{p.adapter_version}</Row> : null}
              {p.public_url ? <Row k="Publisher"><a className="evo-link" href={p.public_url} target="_blank" rel="noreferrer">Open publisher page ↗</a></Row> : null}
            </dl>
            {p.terms_note ? <p className="pc-sub">{p.terms_note}</p> : null}
          </>
        ) : null}
        {M.isPaid(p) ? (
          <p className="pc-note">Paid source. It is only called inside the workspace and strategy budgets, never while the Paid data switch is paused, and a failed paid lookup is refunded. It is never test-called from this screen.</p>
        ) : null}
        {p.connector_kind === 'sandbox' ? <p className="pc-note">Sandbox data is synthetic. It serves sandbox properties only — never a real one.</p> : null}
        {approving ? <PaidApproval p={p} controls={controls} busy={busy} onCancel={() => setApproving(false)}
                                   onConfirm={() => onToggle(p, true)} /> : null}
        {extra}
      </div>
    </Drawer>
  )
}

/** Inline approval before a paid provider is switched on. Requires an explicit tick. */
export function PaidApproval({ p, controls, onCancel, onConfirm, busy }) {
  const [ok, setOk] = useState(false)
  const ref = useRef(null)
  const tid = useId()
  useEffect(() => { if (ref.current) { ref.current.scrollIntoView({ block: 'nearest' }); ref.current.focus() } }, [])
  if (!p) return null
  const day = controls && controls.org_daily_budget_cents
  const month = controls && controls.org_monthly_budget_cents
  return (
    <section className="pc-approve" role="group" aria-labelledby={tid}>
        <h2 id={tid} className="pc-modal__title">Switch on a paid provider?</h2>
        <p><b>{p.label}</b> charges per lookup{Object.keys(p.costs || {}).length ? `: ${Object.entries(p.costs).map(([k, v]) => `${M.capName(k)} ${cents(v)}`).join(', ')}` : ''}.</p>
        {M.isEvaluationOnly(p) ? <p className="pc-note is-warn">Evaluation only: switching it on does not let the engine use it. Only a Provider Evaluation run (with its own spend limit and typed confirmation) can call it.</p> : null}
        <ul className="pc-modal__list">
          <li>Switching it on makes no call now. Lookups happen only when a strategy or a person asks for one.</li>
          <li>Workspace budget: {day == null ? 'no daily ceiling' : `${cents(day)}/day`}, {month == null ? 'no monthly ceiling' : `${cents(month)}/month`} — on top of each strategy&apos;s own budget.</li>
          <li>The Paid data switch is {controls && controls.paused_paid_data ? 'PAUSED — nothing paid will run until it is resumed' : 'running'}.</li>
        </ul>
        <label className="pc-modal__ack">
          <input ref={ref} type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} />
          <span>I approve paid lookups from this provider within these budgets.</span>
        </label>
        <div className="pc-actions">
          <button type="button" className="evo-btn evo-btn--ghost" onClick={onCancel}>Cancel</button>
          <button type="button" className="evo-btn evo-btn--primary" disabled={!ok || busy} onClick={onConfirm}>{busy ? 'Switching on…' : 'Approve and switch on'}</button>
        </div>
    </section>
  )
}
