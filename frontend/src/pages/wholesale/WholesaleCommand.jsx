/* WHOLESALE — DEAL OPERATIONS (Phase 7.2; formerly the Wholesale "Command Center").
 *
 * There used to be two command centres. Now there is one per operating world:
 *   EvoSense · Acquisition Command   finds and works the NEXT opportunities
 *   Wholesale · Deal Operations      moves the deals ALREADY in the transaction
 * Acquisition metrics live in EvoSense, not here.
 *
 * This screen answers: WHAT DEALS ARE IN MOTION? WHAT NEEDS ATTENTION? WHAT IS
 * CLOSING? WHAT MONEY IS EXPECTED? Every number is counted from this
 * workspace's own records (GET /wholesale/operating-board + /dashboard).
 *
 * `focus` gives the two focused views the navigation names:
 *   closing       CONTRACTS & CLOSING - deals from under contract to title
 *   dispositions  DISPOSITIONS - property -> matched buyers -> outreach ->
 *                 response -> selected buyer -> assignment
 * They are views of the same real deals, not separate data.
 *
 * SANDBOX. The board excludes sandbox records by default so a real workspace
 * reports its real pipeline. In the LOCAL REVIEW environment (a local SQLite
 * database, never production) sandbox records are included by default -
 * reviewing sandbox data is the point there - and the toggle says so.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { errText } from './wsShared'
import { AuthImage } from './wsFiles'
import {
  Alert, Empty, EvoApp, Feed, Hero, Metric, Metrics, PageSkeleton, Panel, PropertyThumb, Status, TabBar,
  ago, humanize, money, moneyK, shortDate, useEnvironment,
} from './ds/ds'
import './ds/evo-pages.css'

const CLOSING_STAGES = ['under_contract', 'disposition', 'buyer_identified', 'assignment_pending', 'title_closing']
const DISPO_STAGES = ['under_contract', 'disposition', 'buyer_identified', 'assignment_pending']
const CONTRACT_STEPS = [['under_contract', 'Under contract'], ['disposition', 'Marketing'], ['buyer_identified', 'Buyer found'],
  ['assignment_pending', 'Assignment'], ['title_closing', 'Title'], ['closed', 'Closed']]

const GROUPS = [
  { key: 'sourcing', label: 'Finding the property', keys: ['new_property', 'owner_identified', 'enrichment_needed', 'ready_for_outreach'] },
  { key: 'seller', label: 'Talking to the seller', keys: ['outreach_active', 'seller_engaged', 'qualifying', 'qualified'] },
  { key: 'offer', label: 'Analysis & offer', keys: ['analysis', 'offer_review', 'offer_sent', 'negotiating'] },
  { key: 'contract', label: 'Contract to close', keys: ['under_contract', 'disposition', 'buyer_identified', 'assignment_pending', 'title_closing'] },
]

export function DealThumb({ photo, address }) {
  if (photo) return <span className="evo-thumb"><AuthImage path={photo} alt={address ? `Photo of ${address}` : ''} /></span>
  return <PropertyThumb address={address} />
}

function DateBox({ iso }) {
  if (!iso) return <span className="evo-date"><b>—</b><span>no date</span></span>
  const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso)
  const days = Math.round((d - new Date()) / 86400000)
  return (
    <span className={`evo-date${days < 0 ? ' is-past' : days <= 10 ? ' is-soon' : ''}`}
          title={days < 0 ? `${-days} days ago` : `in ${days} days`}>
      <b>{d.getDate()}</b><span>{d.toLocaleDateString([], { month: 'short' })}</span>
    </span>
  )
}

function Progress({ stage }) {
  const i = CONTRACT_STEPS.findIndex(([k]) => k === stage)
  return (
    <span className="evo-progress" role="img" aria-label={`Step ${i + 1} of ${CONTRACT_STEPS.length}: ${CONTRACT_STEPS[i] ? CONTRACT_STEPS[i][1] : stage}`}>
      {CONTRACT_STEPS.map(([k], j) => <span key={k} className={j < i ? 'is-done' : j === i ? 'is-now' : ''} />)}
    </span>
  )
}

export default function WholesaleCommand({ focus }) {
  const navigate = useNavigate()
  const env = useEnvironment()
  const [includeTest, setIncludeTest] = useState(null)
  const [board, setBoard] = useState(null)
  const [ops, setOps] = useState(null)
  const [rooms, setRooms] = useState({})
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('pipeline')

  // Local review includes sandbox records by default; production never does.
  useEffect(() => { if (env && includeTest === null) setIncludeTest(!!env.local_review) }, [env, includeTest])

  const load = useCallback(async () => {
    if (includeTest === null) return
    setLoading(true); setError(null)
    try {
      const q = includeTest ? '?include_test=true' : ''
      const [counts, work] = await Promise.all([
        api.get('/wholesale/dashboard' + q), api.get('/wholesale/operating-board' + q)])
      setBoard(counts); setOps(work)
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }, [includeTest])
  useEffect(() => { load() }, [load])

  const dispoDeals = useMemo(() => (ops ? ops.active_deals.filter((d) => DISPO_STAGES.includes(d.stage)) : []), [ops])
  // Dispositions reads each deal's room (matches + outreach). At most ten.
  useEffect(() => {
    if (focus !== 'dispositions' || !dispoDeals.length) return
    let alive = true
    Promise.all(dispoDeals.slice(0, 10).map((d) => api.get(`/wholesale/deals/${d.deal_id}`).then((r) => [d.deal_id, r]).catch(() => [d.deal_id, null])))
      .then((pairs) => { if (alive) setRooms(Object.fromEntries(pairs)) })
    return () => { alive = false }
  }, [focus, dispoDeals])

  const world = 'operations'
  const title = focus === 'closing' ? 'Contracts & Closing' : focus === 'dispositions' ? 'Dispositions' : 'Deal Operations'
  const sub = focus === 'closing' ? 'Every deal from signed contract to closing table: approvals, buyer, title, date and fee.'
    : focus === 'dispositions' ? 'Matching each contracted property to the right cash buyer, through to assignment.'
      : 'What is in motion, what needs you, what is closing, and the money it is expected to bring.'
  const sandboxToggle = (
    <label className="evo-btn evo-btn--ghost" style={{ cursor: 'pointer' }} title="Sandbox records are excluded from a real workspace's numbers unless you include them.">
      <input type="checkbox" checked={!!includeTest} onChange={(e) => setIncludeTest(e.target.checked)} style={{ margin: 0 }} />
      Include sandbox records
    </label>
  )

  if (!ops) {
    return <EvoApp world={world}>{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>
  }

  const h = ops.headline
  const HERO = {
    closing: { scene: 'contract', eyebrow: 'Transaction Control', title: 'Contracts & Closing',
      sub: 'Stay organized. Keep it moving — from signed contract to the closing table.', quote: 'Execution builds trust.' },
    dispositions: { scene: 'sold', eyebrow: 'Buyer Matching', title: 'Dispositions',
      sub: 'Turn contracts into real outcomes: the right cash buyer for every property.', quote: "Today's closing creates tomorrow's opportunity." },
    ops: { scene: 'estate', eyebrow: 'Wholesale Operations', title: 'Deal Operations',
      sub: 'From opportunity to close: what is in motion, what needs you, and the money it is expected to bring.', quote: 'Good deals create financial freedom.' },
  }[focus || 'ops']
  const head = (
    <>
      <Hero scene={HERO.scene} eyebrow={HERO.eyebrow} title={HERO.title} sub={HERO.sub} quote={HERO.quote}
            meta={[{ label: <>As of <b>{new Date(ops.as_of).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</b></> },
                   ops.include_test ? { label: 'Including sandbox records', tone: 'paused' } : null]}
            actions={<Link className="evo-btn evo-btn--primary" to="/wholesale/properties?add=1">+ New Deal</Link>} />
      <div className="evo-toolbar evo-toolbar--end">{sandboxToggle}</div>
    </>
  )

  if (focus === 'closing') return <EvoApp world={world}>{head}<Alert>{error}</Alert><ClosingView ops={ops} navigate={navigate} /></EvoApp>
  if (focus === 'dispositions') {
    return <EvoApp world={world}>{head}<Alert>{error}</Alert><DispositionsView deals={dispoDeals} rooms={rooms} navigate={navigate} /></EvoApp>
  }

  const stages = ops.pipeline || []
  const byKey = Object.fromEntries(stages.map((s) => [s.key, s]))

  return (
    <EvoApp world={world}>
      {head}
      <Alert>{error}</Alert>
      <Metrics label="Deal operations">
        <Metric label="Active deals" value={h.active_deals} tone="primary" />
        <Metric label="Under contract" value={h.under_contract} tone="success" />
        <Metric label="Awaiting approval" value={h.awaiting_approval} tone="warning" attention={h.awaiting_approval > 0} />
        <Metric label="Closing in 30 days" value={h.closing_30_days} tone="info" />
        <Metric label="Expected pipeline value" value={moneyK(h.pipeline_value)} tone="primary"
                sub={`Fee expected on ${h.active_deals} open deal${h.active_deals === 1 ? '' : 's'} · not revenue`} />
        <Metric label="Fees collected" value={moneyK(h.fees_collected)} tone="success" sub="Recorded by a person at close" />
      </Metrics>

      <div className="evo-cols">
        <div className="evo-stack">
          <Panel flush>
            <div style={{ padding: '4px 16px 0' }}>
              <TabBar label="Deal operations views" value={tab} onChange={setTab}
                      items={[
                        { key: 'pipeline', label: 'Pipeline', count: ops.active_total },
                        { key: 'attention', label: 'Needs attention', count: ops.attention_total },
                        { key: 'closings', label: 'Closings', count: ops.closings_total },
                        { key: 'risk', label: 'At risk', count: ops.at_risk_total },
                      ]} />
            </div>

            <div role="tabpanel" id="panel-pipeline" aria-labelledby="tab-pipeline" hidden={tab !== 'pipeline'}>
              {!ops.active_deals.length ? (
                <Empty title="No active deals" action={<Link className="evo-btn evo-btn--primary" to="/wholesale/evosense">Open Acquisition Command</Link>}>
                  EvoSense is hunting for your next opportunity. Promote one — or add a property — to start a deal.
                </Empty>
              ) : (
                <div className="evo-table-wrap">
                  <table className="evo-table evo-table--cards">
                    <caption className="evo-sr">Active deals</caption>
                    <thead><tr><th scope="col">Property</th><th scope="col">Stage</th><th scope="col">Target close</th>
                      <th scope="col" className="is-right">Deal value</th><th scope="col">Next action</th></tr></thead>
                    <tbody>
                      {ops.active_deals.map((d) => (
                        <tr key={d.deal_id} className={`is-link${d.next_action?.tone === 'urgent' ? ' is-attention' : ''}`}
                            onClick={() => navigate('/wholesale/deals/' + d.deal_id)}>
                          <td className="is-lead" data-label="">
                            <div className="evo-prop">
                              <DealThumb photo={d.photo_url} address={d.address} />
                              <div className="evo-prop__text">
                                <Link className="evo-prop__addr" to={'/wholesale/deals/' + d.deal_id} onClick={(e) => e.stopPropagation()}>{d.address || '(no address)'}</Link>
                                <span className="evo-prop__sub">{[d.city, d.state].filter(Boolean).join(', ')}</span>
                              </div>
                            </div>
                          </td>
                          <td data-label="Stage"><Status status={d.stage} label={d.stage_label} /></td>
                          <td data-label="Target close" className="evo-nowrap">{d.closing_date ? shortDate(d.closing_date) : '—'}</td>
                          <td data-label="Deal value" className="is-right"><span className="evo-money">{d.expected_fee ? money(d.expected_fee) : '—'}</span></td>
                          <td data-label="Next action"><span className="evo-next">{d.next_action?.label || '—'}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="evo-pipewrap">
                <h3 className="evo-panel__title" style={{ marginBottom: 10 }}>Pipeline by stage <span className="evo-panel__hint">count and expected fee</span></h3>
                <div className="evo-stack" style={{ gap: 14 }}>
                  {GROUPS.map((g) => (
                    <div key={g.key}>
                      <p className="evo-hero__label">{g.label}</p>
                      <div className="evo-pipeline">
                        {g.keys.filter((k) => byKey[k]).map((k) => {
                          const st = byKey[k]
                          return (
                            <Link key={k} className={`evo-stage${st.count ? '' : ' is-empty'}`} to={`/wholesale/properties?stage=${k}`}>
                              <span className="evo-stage__n">{st.count}</span>
                              <span className="evo-stage__l">{st.label}</span>
                              <span className="evo-stage__v">{st.value ? moneyK(st.value) : '—'}</span>
                            </Link>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div role="tabpanel" id="panel-attention" aria-labelledby="tab-attention" hidden={tab !== 'attention'} className="evo-tabpad">
              {!ops.needs_attention.length ? (
                <Empty title="Nothing is waiting on you" icon="✓">Everything open is with somebody else.</Empty>
              ) : (
                <ul className="evo-needlist">
                  {ops.needs_attention.map((d) => (
                    <li key={d.deal_id}>
                      <button type="button" className="evo-needrow" onClick={() => navigate('/wholesale/deals/' + d.deal_id)}>
                        <DealThumb photo={d.photo_url} address={d.address} />
                        <span className="evo-needrow__main">
                          <span className="evo-prop__addr">{d.address || '(no address)'}</span>
                          <span className="evo-prop__sub">{d.next_action?.label}{d.next_action?.detail ? ` · ${d.next_action.detail}` : ''}</span>
                        </span>
                        <Status status={d.stage} label={d.stage_label} />
                        <span className="evo-money">{d.expected_fee ? money(d.expected_fee) : '—'}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div role="tabpanel" id="panel-closings" aria-labelledby="tab-closings" hidden={tab !== 'closings'} className="evo-tabpad">
              {!ops.upcoming_closings.length ? <Empty title="No closings scheduled">Nothing is scheduled to close in the next 30 days.</Empty> : (
                <ul className="evo-closing">
                  {ops.upcoming_closings.map((d) => (
                    <li key={d.deal_id}><Link to={'/wholesale/deals/' + d.deal_id}>
                      <DateBox iso={d.closing_date} />
                      <span style={{ minWidth: 0 }}><span className="evo-prop__addr">{d.address}</span>
                        <span className="evo-prop__sub">{d.stage_label}</span></span>
                      <span className="evo-money">{d.expected_fee ? moneyK(d.expected_fee) : '—'}</span>
                    </Link></li>
                  ))}
                </ul>
              )}
            </div>

            <div role="tabpanel" id="panel-risk" aria-labelledby="tab-risk" hidden={tab !== 'risk'} className="evo-tabpad">
              {!(ops.at_risk || []).length ? <Empty title="Nothing at risk" icon="✓">No open deal is past its closing date or quiet {ops.quiet_days}+ days.</Empty> : (
                <Feed items={ops.at_risk.map((d) => ({
                  key: d.deal_id, tone: d.risk.level === 'overdue' ? 'bad' : 'attention',
                  text: <Link className="evo-feedlink" to={'/wholesale/deals/' + d.deal_id}>{d.address || '(no address)'}</Link>,
                  meta: `${d.risk.label} · ${d.stage_label}`,
                }))} />
              )}
            </div>
          </Panel>
        </div>

        <aside className="evo-rail" aria-label="Closings, risk and activity">
          <Panel title="Upcoming closings" count={ops.closings_total}
                 action={<Link className="evo-link" to="/wholesale/closing">All →</Link>}>
            {!ops.upcoming_closings.length ? <p className="evo-muted" style={{ margin: 0 }}>Nothing scheduled in the next 30 days.</p> : (
              <ul className="evo-closing">
                {ops.upcoming_closings.map((d) => (
                  <li key={d.deal_id}><Link to={'/wholesale/deals/' + d.deal_id}>
                    <DateBox iso={d.closing_date} />
                    <span style={{ minWidth: 0 }}><span className="evo-prop__addr">{d.address}</span>
                      <span className="evo-prop__sub">{d.stage_label}</span></span>
                    <span className="evo-money">{d.expected_fee ? moneyK(d.expected_fee) : '—'}</span>
                  </Link></li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Recent activity">
            <Feed items={(ops.recent_activity || []).slice(0, 8).map((e) => ({
              key: e.id, tone: e.actor_type === 'user' ? 'good' : e.actor_type === 'ai' ? 'info' : 'quiet',
              text: e.summary || humanize(e.action), meta: `${e.created_at ? ago(e.created_at) : ''} · ${e.actor_type}`,
            }))} empty="Nothing has happened yet in this workspace." />
          </Panel>
          {board ? (
            <Panel title="Funnel totals">
              <dl className="evo-kv" style={{ fontSize: 13 }}>
                {[['properties_imported', 'Properties'], ['sellers_identified', 'Sellers'], ['offers_made', 'Offers made'],
                  ['closed_deals', 'Closed deals'], ['cash_buyers', 'Cash buyers'], ['buyer_responses', 'Buyer responses']]
                  .filter(([k]) => board[k] !== undefined).map(([k, l]) => <KV key={k} k={l} v={board[k] ?? '—'} />)}
              </dl>
            </Panel>
          ) : null}
        </aside>
      </div>
      {loading ? <span className="evo-sr" role="status">Refreshing</span> : null}
    </EvoApp>
  )
}

function KV({ k, v }) { return <><dt>{k}</dt><dd>{v}</dd></> }

const CLOSING_TABS = [
  ['all', 'All in contract', CLOSING_STAGES],
  ['contract', 'Under contract', ['under_contract']],
  ['buyer', 'Buyer & assignment', ['disposition', 'buyer_identified', 'assignment_pending']],
  ['title', 'Title & closing', ['title_closing']],
]

function ClosingView({ ops, navigate }) {
  const [tab, setTab] = useState('all')
  const inContract = ops.active_deals.filter((d) => CLOSING_STAGES.includes(d.stage))
    .sort((a, b) => (a.closing_date || '9').localeCompare(b.closing_date || '9'))
  const deals = inContract.filter((d) => CLOSING_TABS.find(([k]) => k === tab)[2].includes(d.stage))
  const fees = inContract.reduce((n, d) => n + (d.expected_fee || 0), 0)
  return (
    <>
      <Metrics label="Contracts and closing">
        <Metric label="In contract" value={inContract.length} tone="success" />
        <Metric label="Closing in 30 days" value={ops.headline.closing_30_days} tone="info" />
        <Metric label="Awaiting approval" value={ops.headline.awaiting_approval} tone="warning" attention={ops.headline.awaiting_approval > 0} />
        <Metric label="Assignment fees expected" value={money(fees)} tone="primary" sub="expected, not collected" />
        <Metric label="Fees collected" value={money(ops.headline.fees_collected)} tone="success" />
      </Metrics>
      <Panel flush>
        <div style={{ padding: '4px 16px 0' }}>
          <TabBar label="Contract stage" value={tab} onChange={setTab} controls={false}
                  items={CLOSING_TABS.map(([k, l, st]) => ({ key: k, label: l, count: inContract.filter((d) => st.includes(d.stage)).length }))} />
        </div>
        {!deals.length ? (
          <Empty title={inContract.length ? 'Nothing at this step' : 'No deals under contract'} action={<Link className="evo-btn evo-btn--secondary" to="/wholesale">Deal Operations</Link>}>
            When an offer is approved and a contract is signed, the deal appears here with its closing timeline.
          </Empty>
        ) : (
          <div className="evo-table-wrap">
            <table className="evo-table evo-table--cards">
              <caption className="evo-sr">Deals under contract</caption>
              <thead><tr><th scope="col">Closing</th><th scope="col">Property</th><th scope="col">Where it stands</th>
                <th scope="col" className="is-right">Contract</th><th scope="col" className="is-right">Buyer price</th>
                <th scope="col" className="is-right">Assignment fee</th><th scope="col">Outstanding</th></tr></thead>
              <tbody>
                {deals.map((d) => (
                  <tr key={d.deal_id} className={`is-link${d.risk ? ' is-attention' : ''}`} onClick={() => navigate('/wholesale/deals/' + d.deal_id)}>
                    <td data-label="Closing"><DateBox iso={d.closing_date} /></td>
                    <td className="is-lead" data-label="">
                      <div className="evo-prop"><DealThumb photo={d.photo_url} address={d.address} />
                        <div className="evo-prop__text"><Link className="evo-prop__addr" to={'/wholesale/deals/' + d.deal_id} onClick={(e) => e.stopPropagation()}>{d.address}</Link>
                          <span className="evo-prop__sub">{[d.city, d.state].filter(Boolean).join(', ')}</span></div></div>
                    </td>
                    <td data-label="Where it stands"><Status status={d.stage} label={d.stage_label} /><Progress stage={d.stage} /></td>
                    <td data-label="Contract" className="is-right"><span className="evo-money">{money(d.contract_price)}</span></td>
                    <td data-label="Buyer price" className="is-right"><span className="evo-money">{money(d.buyer_price)}</span></td>
                    <td data-label="Assignment fee" className="is-right"><span className="evo-money">{money(d.expected_fee)}</span></td>
                    <td data-label="Outstanding"><span className="evo-next">{d.next_action?.label || '—'}</span>
                      {d.risk ? <span className="evo-prop__sub" style={{ color: 'var(--evo-warning-ink)' }}>{d.risk.label}</span> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <p className="evo-muted evo-small" style={{ marginTop: 12 }}>Contract, approval, document and title details live in each deal's workspace. Nothing here is a legal state
        the workflow does not itself record.</p>
    </>
  )
}

function DispositionsView({ deals, rooms, navigate }) {
  return (
    <>
      <Metrics label="Dispositions">
        <Metric label="In disposition" value={deals.length} tone="violet" />
        <Metric label="Buyers matched" value={Object.values(rooms).reduce((n, r) => n + ((r && r.buyer_matches) || []).filter((m) => !m.disqualified).length, 0)} tone="info" />
        <Metric label="Buyers contacted" value={Object.values(rooms).reduce((n, r) => n + ((r && r.buyer_outreach) || []).length, 0)} tone="primary" />
        <Metric label="Buyer offers" value={Object.values(rooms).reduce((n, r) => n + ((r && r.buyer_outreach) || []).filter((o) => o.offer_amount).length, 0)} tone="success" />
      </Metrics>
      {!deals.length ? (
        <Panel><Empty title="Nothing in disposition" action={<Link className="evo-btn evo-btn--secondary" to="/wholesale/buyers">Cash buyers</Link>}>
          A deal appears here once it is under contract and ready to be matched to a cash buyer.</Empty></Panel>
      ) : (
        <div className="evo-stack">
          {deals.map((d) => {
            const r = rooms[d.deal_id]
            const matches = r ? (r.buyer_matches || []).filter((m) => !m.disqualified) : null
            const out = r ? r.buyer_outreach || [] : null
            const offers = out ? out.filter((o) => o.offer_amount) : null
            const selected = r && r.deal && r.deal.assigned_buyer_id
            return (
              <Panel key={d.deal_id}>
                <div className="evo-strat__top" style={{ marginBottom: 14 }}>
                  <div className="evo-prop"><DealThumb photo={d.photo_url} address={d.address} />
                    <div className="evo-prop__text"><Link className="evo-prop__addr" to={'/wholesale/deals/' + d.deal_id}>{d.address}</Link>
                      <span className="evo-prop__sub">{[d.city, d.state].filter(Boolean).join(', ')} · contract {money(d.contract_price)}</span></div></div>
                  <Status status={d.stage} label={d.stage_label} />
                </div>
                <div className="evo-strat__results" role="group" aria-label="Disposition flow">
                  <div><b>{matches ? matches.length : '…'}</b><span>Matched buyers</span></div>
                  <div><b>{out ? out.length : '…'}</b><span>Contacted</span></div>
                  <div className={offers && offers.length ? 'is-attention' : ''}><b>{offers ? offers.length : '…'}</b><span>Offers</span></div>
                  <div><b>{selected ? 'Yes' : d.buyer_price ? 'Priced' : '—'}</b><span>Buyer selected</span></div>
                </div>
                {matches && matches.length ? (
                  <div className="evo-table-wrap" style={{ marginTop: 12 }}>
                    <table className="evo-table">
                      <thead><tr><th scope="col">Matched buyer</th><th scope="col" className="is-num">Buy-box fit</th><th scope="col">Outreach</th><th scope="col" className="is-right">Offer</th></tr></thead>
                      <tbody>
                        {matches.slice(0, 4).map((m) => {
                          const o = (out || []).find((x) => x.buyer_id === m.buyer_id)
                          return (
                            <tr key={m.buyer_id}>
                              <td><span className="evo-strong">{m.buyer_name || m.company_name || 'Buyer'}</span></td>
                              <td className="is-num"><span className="evo-num evo-num--contact">{m.score}</span></td>
                              <td>{o ? <Status status={o.status} tone={o.status === 'offer_submitted' ? 'good' : 'quiet'} /> : <span className="evo-muted">not contacted</span>}</td>
                              <td className="is-right"><span className="evo-money">{o && o.offer_amount ? money(o.offer_amount) : '—'}</span></td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <div className="evo-actionbar" style={{ marginTop: 14 }}>
                  <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => navigate('/wholesale/deals/' + d.deal_id)}>Open buyer board</button>
                  <span className="evo-muted evo-small">Seller details are never shared with buyers.</span>
                </div>
              </Panel>
            )
          })}
        </div>
      )}
    </>
  )
}

export function DealOperationsClosing() { return <WholesaleCommand focus="closing" /> }
export function DealOperationsDispositions() { return <WholesaleCommand focus="dispositions" /> }
