/* MY DEAL ROOM — ACQUISITION COMMAND AS A SHORT WORK SCREEN (redesign, Nov 2026).
 *
 *   compact header   title, EvoSense status, last / next hunt, the existing
 *                    Pause / Resume and Run hunt now controls, and a link to
 *                    Activity & system health (every metric, budget and the
 *                    feed that used to sit on this page — nothing removed)
 *   ONE work queue   GET /wholesale/evosense/work-queue. Needs Decision,
 *                    Upcoming Auctions, Handoffs and All are views of it, so a
 *                    property is listed once with every task on it
 *   detail panel     evidence, source links, contact status, history and the
 *                    relevant action. Docked beside the queue on a laptop,
 *                    a slide-over on a tablet, a full-width view on a phone
 *
 * The view, search, sort, page and selected row live in the URL, so closing
 * the panel, opening the full property and coming Back all return to the same
 * place in the queue (scroll position is restored too).
 *
 * Four answers stay apart on every row: auction urgency, investment evidence,
 * contact confidence and seller intent. Tax value, market estimate and ARV are
 * always labelled. The CURRENT sale comes from the property's sale event; a
 * date written into an older hand-off is shown as history, or as "Needs
 * verification" with both sources (services/evosense/work_queue.py).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { canRunHunts, isWholesaleAdmin } from '../../../auth/workspaceAuthority'
import { errText } from '../wsShared'
import { ev } from '../engine'
import { Alert, EvoApp, PropertyThumb, SandboxTag, ago, humanize, money, when } from '../ds/ds'
import '../ds/evo-pages.css'
import './work-queue.css'
import PropertyPhoto from './PropertyPhoto'

const PAGE = 25
const SORTS = [['work', 'Work priority'], ['sale_date', 'Sale date'], ['evidence', 'Investment evidence'], ['newest', 'Newest found']]
const URGENCY_TONE = { CRITICAL: 'critical', URGENT_VERIFY: 'verify', UPCOMING: 'warn', VERIFY: 'verify',
  OUTCOME_UNVERIFIED: 'verify', POSTPONED: 'info', CANCELED: '', COMPLETED: '', NONE: '' }
const WORK_TONE = { CRITICAL: 'critical', REVIEW_URGENT: 'verify', HIGH: 'warn', NORMAL: '', REVIEW: 'verify', CLOSED: '' }
const EMPTY = {
  needs_decision: ['Nothing needs a decision', 'EvoSense hands a property to a person when a seller states a price, asks for an offer or a visit, crosses an intent threshold, or a verified sale is close. None is waiting.'],
  auctions: ['No upcoming sale on record', 'No foreclosure or tax sale on record for a property in this workspace has a date that has not passed.'],
  handoffs: ['No hand-off in progress', 'Hand-offs a person has taken and not yet closed are listed here.'],
  all: ['Nothing to work yet', 'No property passes an active strategy yet. Run a hunt, or wait for the next automatic one.'],
}

function fmtDay(iso) {
  if (!iso) return null
  return new Date(iso + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}
const L = ({ tone, children, title }) => <span className={`mdr-label${tone ? ' mdr-label--' + tone : ''}`} title={title}>{children}</span>

/** The sale cell: current sale first; "Needs verification" or "moved" beside it. */
function SaleCell({ sale, compact }) {
  const c = sale && sale.current
  if (!c && (!sale || sale.status === 'none')) return <span className="wq-muted">No sale on record</span>
  return (
    <span className="wq-sale">
      {c ? (
        <>
          <span className="wq-sale__date">{c.display || 'Date not read'}</span>
          <span className="wq-sale__sub">
            {c.kind || 'Sale'}{c.days != null && c.days >= 0 ? ` · ${c.days} day${c.days === 1 ? '' : 's'}` : ''}
            {c.verified ? ' · verified' : ''}
          </span>
        </>
      ) : null}
      {sale.status === 'needs_verification' ? <L tone="verify" title={sale.message || ''}>Needs verification</L>
        : sale.status === 'historical' && !compact ? <L tone="info" title={sale.message || ''}>Sale moved</L> : null}
    </span>
  )
}

function Scores({ it }) {
  return (
    <span className="wq-scores">
      <span title="Investment evidence score — not auction urgency or guaranteed profit"><b>{it.investment_evidence ?? '—'}</b><i>Evidence</i></span>
      <span title="Contact confidence"><b>{it.contact_confidence ?? '—'}</b><i>Contact</i></span>
      <span title="Seller intent"><b>{it.seller_intent ?? '—'}</b><i>Intent</i></span>
    </span>
  )
}

function TaskLabels({ it }) {
  const out = []
  for (const t of it.tasks || []) {
    if (t.kind === 'handoff') out.push(<L key={t.id} tone={t.status === 'open' ? 'critical' : 'info'}>{t.status === 'open' ? 'Needs decision' : 'Hand-off in progress'}</L>)
  }
  if (it.work && it.work.work_priority && it.work.work_priority !== 'NORMAL') {
    out.push(<L key="wp" tone={WORK_TONE[it.work.work_priority]}>{it.work.work_priority_label || humanize(it.work.work_priority)}</L>)
  }
  return out.length ? <span className="wq-labels">{out}</span> : null
}

export default function WorkQueueCommand() {
  const isAdmin = isWholesaleAdmin()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const view = params.get('view') || 'needs_decision'
  const sort = params.get('sort') || 'work'
  const q = params.get('q') || ''
  const page = Math.max(0, parseInt(params.get('page') || '0', 10) || 0)
  const sel = params.get('sel')

  const [cc, setCc] = useState(null)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [qDraft, setQDraft] = useState(q)
  useEffect(() => { setQDraft(q) }, [q])
  const listRef = useRef(null)
  const scrollKey = 'wq-scroll:' + [view, sort, q, page].join('|')

  const set = useCallback((patch, { replace = false } = {}) => {
    const n = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) { if (v === null || v === undefined || v === '') n.delete(k); else n.set(k, String(v)) }
    if (n.get('view') === 'needs_decision') n.delete('view')
    if (n.get('sort') === 'work') n.delete('sort')
    if (n.get('page') === '0') n.delete('page')
    setParams(n, { replace })
  }, [params, setParams])

  const loadHeader = useCallback(async () => {
    try { setCc(await api.get('/wholesale/evosense/command-center')) } catch (e) { setError(errText(e)) }
  }, [])
  const loadQueue = useCallback(async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ view, sort, offset: String(page * PAGE), limit: String(PAGE) })
      if (q) qs.set('q', q)
      setData(await api.get('/wholesale/evosense/work-queue?' + qs.toString()))
      setError(null)
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }, [view, sort, q, page])
  useEffect(() => { loadHeader() }, [loadHeader])
  useEffect(() => { loadQueue() }, [loadQueue])

  // keep the place in the list across Back / panel close
  useEffect(() => {
    if (loading || !listRef.current) return
    const y = parseInt(sessionStorage.getItem(scrollKey) || '0', 10)
    if (y) listRef.current.scrollTop = y
  }, [loading, scrollKey])
  const onScroll = (e) => { try { sessionStorage.setItem(scrollKey, String(e.currentTarget.scrollTop)) } catch { /* storage off */ } }

  async function huntAll() {
    if (!cc) return
    setBusy(true); setNotice(null); setError(null)
    const lines = []
    try {
      for (const s of cc.strategies || []) {
        const r = await api.post(`/wholesale/evosense/strategies/${s.id}/hunt${s.pilot ? '?background=true' : ''}`, {})
        const c = r.counts || {}
        lines.push(r.background ? `${s.name}: pilot hunt started in the background` : r.status === 'skipped' ? `${s.name}: skipped — ${r.error}` :
          `${s.name}: ${c.observed || 0} seen, ${c.created || 0} new, ${c.contacts_found || 0} contacts`)
      }
      setNotice(lines.join(' · ') || 'No active strategy to hunt.')
      await Promise.all([loadHeader(), loadQueue()])
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  async function togglePause() {
    setBusy(true); setError(null)
    try { await api.patch('/wholesale/evosense/controls', { paused_all: !cc.controls.paused_all }); await loadHeader() }
    catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const items = (data && data.items) || []
  const selected = useMemo(() => items.find((i) => i.id === sel) || null, [items, sel])
  const counts = (data && data.counts) || {}
  const total = (data && data.total) || 0
  const pages = Math.max(1, Math.ceil(total / PAGE))

  // header facts (all from /command-center, as before)
  const a = (cc && cc.automation) || {}
  const paused = !!(cc && cc.controls && cc.controls.paused_all)
  const nextHunt = (a.strategies || []).filter((s) => s.state === 'scheduled' && s.next_due_at)
    .sort((x, y) => x.next_due_at.localeCompare(y.next_due_at))[0]
  const lastHunt = ((cc && cc.strategies) || []).map((s) => s.last_hunt_at).filter(Boolean).sort().pop()
  const hunting = !cc ? null : paused ? ['warn', ev('EvoSense paused')] : a.hunting === 'active' ? ['good', ev('EvoSense hunting')]
    : a.hunting === 'manual' ? ['', 'Manual hunts only'] : ['', 'No active strategy']
  const attention = cc ? ((cc.routing_reviews || []).length + ((a.inbound || {}).pending_ai_review || 0)) : 0

  return (
    <EvoApp world="acquisition">
      <header className="wq-head">
        <div className="wq-head__title">
          <h1>Acquisition Command</h1>
          <div className="wq-head__meta">
            {hunting ? <L tone={hunting[0]}>{hunting[1]}</L> : <span className="wq-muted">Loading status…</span>}
            {cc ? <span className="wq-muted">Last hunt <b>{lastHunt ? ago(lastHunt) : 'never'}</b></span> : null}
            {cc ? <span className="wq-muted">Next <b>{paused ? 'paused' : nextHunt ? when(nextHunt.next_due_at) : (a.hunting === 'manual' ? 'manual only' : '—')}</b></span> : null}
            {cc && cc.sandbox && cc.sandbox.banner ? <SandboxTag /> : null}
          </div>
        </div>
        <div className="wq-head__actions">
          <Link className="evo-btn evo-btn--ghost evo-btn--sm" to="/wholesale/evosense/activity">
            Activity &amp; system health{attention ? <span className="wq-dot" aria-label={`${attention} need attention`}>{attention}</span> : null}
          </Link>
          {cc ? (
            <button type="button" className={`evo-btn evo-btn--sm ${paused ? 'evo-btn--success' : 'evo-btn--secondary'}`}
                    onClick={togglePause} disabled={busy || (paused && !isAdmin)}
                    title={paused && !isAdmin ? ev('Only an administrator of this workspace can resume EvoSense.') : undefined}>
              {paused ? ev('Resume EvoSense') : ev('Pause EvoSense')}
            </button>
          ) : null}
          {cc ? (
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={huntAll}
                    disabled={busy || paused || !(cc.strategies || []).length || !canRunHunts()}
                    title={!canRunHunts() ? 'Only an admin or a team manager can do this.' : paused ? ev('EvoSense is paused') : 'Runs every active strategy now — the same hunt the scheduler runs'}>
              {busy ? 'Hunting…' : 'Run hunt now'}
            </button>
          ) : null}
        </div>
      </header>

      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      {paused ? <Alert kind="warn"><span><strong>{ev('EvoSense is paused.')}</strong> Nothing is discovered, bought or sent. Seller replies are still saved and opt-outs are still honoured.</span></Alert> : null}
      {cc && (cc.routing_reviews || []).length ? (
        <Alert kind="warn"><span>{cc.routing_reviews.length} inbound message{cc.routing_reviews.length === 1 ? '' : 's'} could belong to more than one conversation. <Link className="evo-link" to="/wholesale/evosense/activity#routing">Choose where it belongs →</Link></span></Alert>
      ) : null}

      <div className={`wq${selected ? ' has-sel' : ''}`}>
        <section className="wq-list" aria-label="Work queue">
          <div className="wq-toolbar">
            <div className="wq-tabs" role="tablist" aria-label="Queue view">
              {((data && data.views) || [{ key: 'needs_decision', label: 'Needs Decision' }, { key: 'auctions', label: 'Upcoming Auctions' }, { key: 'handoffs', label: 'Handoffs' }, { key: 'all', label: 'All' }]).map((v) => (
                <button key={v.key} type="button" role="tab" aria-selected={view === v.key} className={`wq-tab${view === v.key ? ' is-on' : ''}`}
                        onClick={() => set({ view: v.key, page: 0, sel: null })}>
                  {v.label}{counts[v.key] != null ? <span className="wq-tab__n">{counts[v.key]}</span> : null}
                </button>
              ))}
            </div>
            <form className="wq-filter" role="search" onSubmit={(e) => { e.preventDefault(); set({ q: qDraft.trim(), page: 0, sel: null }) }}>
              <label className="evo-sr" htmlFor="wq-q">Filter the queue</label>
              <input id="wq-q" type="search" value={qDraft} onChange={(e) => setQDraft(e.target.value)} placeholder="Filter by address, city, ZIP, county" />
              <label className="evo-sr" htmlFor="wq-sort">Sort</label>
              <select id="wq-sort" value={sort} onChange={(e) => set({ sort: e.target.value, page: 0 })}>
                {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </form>
          </div>

          <div className="wq-rows" ref={listRef} onScroll={onScroll} aria-busy={loading}>
            {loading && !data ? (
              <div className="wq-skel" role="status" aria-live="polite">{[0, 1, 2, 3, 4].map((i) => <span key={i} />)}<span className="evo-sr">Loading the queue…</span></div>
            ) : !items.length ? (
              <div className="wq-empty">
                <b>{q ? 'Nothing matches that filter' : EMPTY[view][0]}</b>
                <p>{q ? `No property in this view matches “${q}”.` : ev(EMPTY[view][1])}</p>
                {q ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => set({ q: null, page: 0 })}>Clear filter</button> : null}
              </div>
            ) : (
              <ol className="wq-ol">
                {items.map((it) => (
                  <li key={it.id}>
                    <button type="button" className={`wq-row${it.id === sel ? ' is-sel' : ''}`} aria-current={it.id === sel ? 'true' : undefined}
                            onClick={() => set({ sel: it.id })}>
                      <PropertyThumb address={it.address} size="sm" />
                      <span className="wq-row__main">
                        <span className="wq-row__addr">{it.address}{it.unit ? ` #${it.unit}` : ''}</span>
                        <span className="wq-row__place">{[it.city || (it.county ? `${it.county} County` : null), it.state].filter(Boolean).join(', ')} {it.zip_code}{it.is_test ? <> · <SandboxTag /></> : null}</span>
                        <TaskLabels it={it} />
                      </span>
                      <span className="wq-row__sale"><SaleCell sale={it.sale} compact /></span>
                      <span className="wq-row__scores"><Scores it={it} /></span>
                      <span className="wq-row__next"><i>Next</i>{it.next_action || '—'}</span>
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </div>
          {total > PAGE ? (
            <nav className="wq-pager" aria-label="Queue pages">
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={page <= 0} onClick={() => set({ page: page - 1, sel: null })}>‹ Previous</button>
              <span>{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={page + 1 >= pages} onClick={() => set({ page: page + 1, sel: null })}>Next ›</button>
            </nav>
          ) : null}
        </section>

        {selected ? (
          <QueueDetail it={selected} onClose={() => set({ sel: null })} onChanged={() => { loadQueue(); loadHeader() }}
                       onOpen={() => navigate(`/wholesale/evosense/property/${selected.id}`)} />
        ) : (
          <aside className="wq-detail wq-detail--empty" aria-label="Selected property">
            <p>Select a property to see its evidence, sale, contacts, history and next action.</p>
          </aside>
        )}
      </div>
    </EvoApp>
  )
}

/** The side panel: evidence, sources, contact status, history, the action. */
function QueueDetail({ it, onClose, onChanged, onOpen }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const panel = useRef(null)
  useEffect(() => {
    let alive = true
    setD(null); setErr(null)
    api.get(`/wholesale/evosense/properties/${it.id}`).then((x) => { if (alive) setD(x) }).catch((e) => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [it.id])
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    if (panel.current) panel.current.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [it.id, onClose])

  const handoff = (it.tasks || []).find((t) => t.kind === 'handoff')
  async function resolve(status) {
    setBusy(true); setErr(null)
    try { await api.post(`/wholesale/evosense/handoffs/${handoff.id}`, { status }); onChanged() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const v = it.values || {}
  const sale = it.sale || {}
  const contacts = (d && d.contacts) || []
  const live = contacts.filter((c) => !['wrong_party', 'invalid', 'opted_out'].includes(c.status))
  const links = (d && d.links) || {}
  const sources = [
    links.county_record_url ? [links.county_record_label || 'County record', links.county_record_url] : null,
    links.maps_url ? ['Map', links.maps_url] : null,
    links.street_view_url ? ['Street View', links.street_view_url] : null,
    links.zillow_url ? ['Zillow', links.zillow_url] : null,
  ].filter(Boolean)
  const signals = (d && d.signals) || []
  const timeline = ((d && d.timeline) || []).slice(0, 8)
  const contactPhone = live.find((c) => c.kind === 'phone')

  return (
    <aside className="wq-detail" aria-label={`Details for ${it.address}`} tabIndex={-1} ref={panel}>
      <div className="wq-detail__bar">
        <button type="button" className="wq-back" onClick={onClose} aria-label="Close details and return to the queue">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg>
          <span>Queue</span>
        </button>
        <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/evosense/property/${it.id}`}>Open full property</Link>
      </div>
      <div className="wq-detail__scroll">
        <div className="wq-detail__photo">
          <PropertyPhoto propertyId={it.id} address={it.address} isTest={it.is_test} showWhy={false} />
        </div>
        <h2 className="wq-detail__addr">{it.address}</h2>
        <p className="wq-detail__place">{[it.city, it.state].filter(Boolean).join(', ')} {it.zip_code}{it.county ? ` · ${it.county} County` : ''}{it.property_type ? ` · ${humanize(it.property_type)}` : ''}</p>
        <TaskLabels it={it} />

        <section className="wq-next" aria-label="Next action">
          <span className="wq-k">Next action</span>
          <b>{it.next_action || '—'}</b>
          {it.work && it.work.why ? <span className="wq-muted">{it.work.why}</span> : null}
          <div className="wq-next__actions">
            {handoff && handoff.status === 'open' ? (
              <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy} onClick={() => resolve('acknowledged')}
                      title="Take this hand-off: it moves to Handoffs until you close it">Take it</button>
            ) : null}
            {contactPhone && !contactPhone.is_test && !it.is_test ? <a className="evo-btn evo-btn--secondary evo-btn--sm" href={`tel:${contactPhone.value}`}>Call seller</a> : null}
            <button type="button" className={`evo-btn ${handoff && handoff.status === 'open' ? 'evo-btn--secondary' : 'evo-btn--primary'} evo-btn--sm`} onClick={onOpen}>Work this property</button>
            {it.promoted_deal_id ? <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/deals/${it.promoted_deal_id}`}>Open the deal</Link> : null}
            {handoff ? (
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => resolve('dismissed')}
                      title="Close the hand-off without a deal. It stays in the property history.">Dismiss hand-off</button>
            ) : null}
          </div>
          {err ? <p className="wq-err" role="alert">{err}</p> : null}
        </section>

        <section className="wq-sec" aria-label="Sale">
          <h3>Sale</h3>
          {sale.current ? (
            <dl className="wq-kv">
              <dt>Current sale</dt><dd><b>{sale.current.display || 'Date not read'}</b> · {sale.current.kind || 'Sale'}{' '}
                {sale.current.verified ? <L tone="good">Verified</L> : <L tone="verify">Not verified</L>}</dd>
              <dt>Urgency</dt><dd>{sale.current.label || '—'}</dd>
              <dt>Source</dt><dd className="wq-muted">{sale.current.source}</dd>
            </dl>
          ) : <p className="wq-muted">No current sale on the property record.</p>}
          {sale.status === 'needs_verification' ? <p className="wq-callout wq-callout--verify"><b>Needs verification.</b> {sale.message}</p> : null}
          {sale.status === 'historical' ? <p className="wq-callout"><b>Sale moved.</b> {sale.message}</p> : null}
          {(sale.history || []).length ? (
            <ul className="wq-hist">
              {sale.history.map((h) => <li key={h.date + h.source}><span>{h.display}</span><span className="wq-muted">{h.historical ? 'Earlier sale (passed) · ' : ''}{h.source}</span></li>)}
            </ul>
          ) : null}
        </section>

        <section className="wq-sec" aria-label="Investment evidence">
          <h3>Evidence</h3>
          <div className="wq-measures">
            <div><i>Investment evidence</i><b>{it.investment_evidence ?? '—'}</b><span>not auction urgency or profit</span></div>
            <div><i>Contact confidence</i><b>{it.contact_confidence ?? '—'}</b></div>
            <div><i>Seller intent</i><b>{it.seller_intent ?? '—'}</b></div>
          </div>
          <dl className="wq-kv">
            <dt>Tax value</dt><dd>{v.tax_value ? <>{money(v.tax_value.value)} <span className="wq-muted">{v.tax_value.label || 'Appraisal district'}{v.tax_value.year ? ` ${v.tax_value.year}` : ''}</span></> : <span className="wq-muted">none on file</span>}</dd>
            <dt>Market estimate</dt><dd>{v.market_estimate != null ? <>{money(v.market_estimate)} <span className="wq-muted">modelled estimate</span></> : <span className="wq-muted">none on file</span>}</dd>
            <dt>ARV</dt><dd>{v.arv && v.arv.value != null ? money(v.arv.value) : <span className="wq-muted">{(v.arv && v.arv.label) || 'Insufficient comparable sales'}</span>}</dd>
          </dl>
          {signals.length ? (
            <ul className="wq-signals">
              {signals.slice(0, 6).map((s, i) => (
                <li key={(s.id || s.label) + i}>{s.label}
                  {s.source ? <span className="wq-muted"> · {s.source}</span> : null}
                  {/^https?:\/\//.test(s.source_reference || '') ? <> · <a href={s.source_reference} target="_blank" rel="noopener noreferrer">source ↗</a></> : null}
                  {s.freshness === 'stale' ? <span className="wq-muted"> · stale</span> : null}</li>
              ))}
            </ul>
          ) : d ? <p className="wq-muted">No current signals.</p> : null}
          {sources.length ? (
            <p className="wq-links">{sources.map(([l, u]) => <a key={u} href={u} target="_blank" rel="noopener noreferrer">{l} ↗</a>)}</p>
          ) : null}
        </section>

        <section className="wq-sec" aria-label="Contact status">
          <h3>Contact</h3>
          {!d ? <p className="wq-muted">Loading…</p> : !contacts.length ? <p className="wq-muted">No contact on file. Find contact from the property.</p> : (
            <ul className="wq-contacts">
              {contacts.slice(0, 4).map((c) => (
                <li key={c.id}><span>{c.kind === 'phone' ? 'Phone' : c.kind === 'email' ? 'Email' : humanize(c.kind)} · {c.person || 'unknown person'}</span>
                  <span className="wq-muted">{humanize(c.status || 'unverified')}{c.connector_label ? ` · ${c.connector_label}` : ''}</span></li>
              ))}
            </ul>
          )}
        </section>

        <section className="wq-sec" aria-label="History">
          <h3>History</h3>
          {(it.tasks || []).filter((t) => t.kind === 'handoff').map((t) => (
            <div key={t.id} className="wq-handoff">
              <span className="wq-k">Hand-off {t.status === 'open' ? 'opened' : 'taken'} · {t.since ? ago(t.since) : ''}</span>
              <ul>{(t.reasons || []).map((r) => <li key={r.code}>{r.label}</li>)}</ul>
              {t.note ? <p className="wq-muted">Original note: “{t.note}”</p> : null}
            </div>
          ))}
          {timeline.length ? (
            <ul className="wq-timeline">
              {timeline.map((e, i) => <li key={i}><span>{e.summary}</span><span className="wq-muted">{ago(e.at)} · {e.actor === 'user' ? 'a person' : e.actor}</span></li>)}
            </ul>
          ) : d ? <p className="wq-muted">Nothing recorded yet.</p> : null}
        </section>
        {err && !d ? <p className="wq-err" role="alert">{err}</p> : null}
      </div>
    </aside>
  )
}
