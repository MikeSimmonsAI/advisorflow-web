/* EVOSENSE — ACQUISITION COMMAND (Phase 7.3 light rebuild; board screen 1).
 *
 * The flagship screen. It answers, in this order:
 *   1. Is the engine running, and when does it hunt next?        (head + meta)
 *   2. What has it done?                                          (metric strip)
 *   3. What needs MY judgment?                                    (Needs You - dominates)
 *   4. What did it find?                                          (Recent Opportunities)
 *   5. Is everything healthy, what has it spent, what just happened?  (rail)
 *
 * NO FAKE VALUES. Every number is counted by the backend
 * (GET /wholesale/evosense/command-center). A timestamp is shown only when the
 * scheduler actually has one. An estimate always carries its ESTIMATE label.
 * EvoSense never sends an offer; this page never offers to.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import {
  Alert, Chips, Empty, EvoApp, Feed, Hero, Metric, Metrics, Num, PageSkeleton, Panel, PropCard,
  PropertyThumb, Ring, SandboxTag, Scores, Status, Tag, ago, cents, humanize, money, when,
} from '../ds/ds'
import '../ds/evo-pages.css'

const ACT_TONE = {
  'handoff.opened': 'attention', 'reply.read': 'info', 'reply.received': 'info', 'promoted': 'good',
  'enrichment.found': 'good', 'provider.failed': 'bad', 'outreach.blocked': 'attention',
  'hunt.succeeded': 'good', 'hunt.failed': 'bad', 'nurture.set': 'info', 'outreach.started': 'info',
}

export default function EvoCommand() {
  const navigate = useNavigate()
  const [cc, setCc] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  const load = useCallback(async () => {
    try { setCc(await api.get('/wholesale/evosense/command-center')); setError(null) }
    catch (e) { setError(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  async function huntAll() {
    if (!cc) return
    setBusy(true); setNotice(null); setError(null)
    const lines = []
    try {
      for (const s of cc.strategies || []) {
        // The SAME service the scheduler runs, one strategy at a time; the
        // strategy lock means a hunt already running is skipped, not doubled.
        // A PILOT reads public-record files; it runs on the server in the
        // background and reports through its run history.
        const r = await api.post(`/wholesale/evosense/strategies/${s.id}/hunt${s.pilot ? '?background=true' : ''}`, {})
        const c = r.counts || {}
        lines.push(r.background ? `${s.name}: pilot hunt started in the background` : r.status === 'skipped' ? `${s.name}: skipped — ${r.error}` :
          `${s.name}: ${c.observed || 0} seen, ${c.created || 0} new, ${c.contacts_found || 0} contacts, ${cents(c.spent_cents || 0)} spent`)
      }
      setNotice(lines.join(' · ') || 'No active strategy to hunt.')
      await load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  async function togglePause() {
    setBusy(true); setError(null)
    try {
      await api.patch('/wholesale/evosense/controls', { paused_all: !cc.controls.paused_all })
      await load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  async function resolveRouting(reviewId, engagementId) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post(`/wholesale/evosense/routing-reviews/${reviewId}`, { engagement_id: engagementId })
      setNotice(engagementId ? `Message attached and read${r.outcome ? ` as ${humanize(r.outcome).toLowerCase()}` : ''}.`
        : 'Marked as not belonging to any EvoSense conversation.')
      await load()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  if (!cc) {
    return (
      <EvoApp world="acquisition">
        {error ? <Alert>{error}</Alert> : <PageSkeleton />}
      </EvoApp>
    )
  }

  const a = cc.automation || {}
  const paused = cc.controls.paused_all
  const t = cc.totals || {}
  const nextHunt = (a.strategies || []).filter((s) => s.state === 'scheduled' && s.next_due_at)
    .sort((x, y) => x.next_due_at.localeCompare(y.next_due_at))[0]
  const lastHunt = (cc.strategies || []).map((s) => s.last_hunt_at).filter(Boolean).sort().pop()
  const huntLabel = { active: 'Active', paused: 'Paused', manual: 'Manual only', none: 'No active strategy' }[a.hunting] || humanize(a.hunting)
  const [hero, ...moreNeeds] = cc.needs_you || []

  const found = cc.found || []
  const top = [...found].sort((x, y) => (y.opportunity_score ?? -1) - (x.opportunity_score ?? -1))
  const best = top[0]
  const spotlight = top.slice(0, 4)
  const hunting = paused ? { label: 'EvoSense paused', tone: 'paused' }
    : a.hunting === 'active' ? { label: 'Hunting automatically', tone: 'live' }
    : { label: huntLabel, tone: undefined }

  return (
    <EvoApp world="acquisition">
      <Hero
        scene="skyline"
        eyebrow="Acquisition Intelligence"
        title="EvoSense Acquisition Command"
        sub="Set the strategy. Let EvoSense find the opportunity."
        quote="Opportunity doesn't wait to be listed."
        meta={[
          hunting,
          { label: <>{(cc.strategies || []).length} active {(cc.strategies || []).length === 1 ? 'strategy' : 'strategies'}</> },
          { label: <>Last hunt <b>{lastHunt ? ago(lastHunt) : 'never'}</b></> },
          { label: <>Next hunt <b>{paused ? 'paused' : nextHunt ? when(nextHunt.next_due_at) : (a.hunting === 'manual' ? 'manual only' : '—')}</b></> },
        ]}
        actions={<>
          <button type="button" className={`evo-btn ${paused ? 'evo-btn--success' : 'evo-btn--secondary'}`}
                  onClick={togglePause} disabled={busy}>
            {paused ? 'Resume EvoSense' : 'Pause EvoSense'}
          </button>
          <button type="button" className="evo-btn evo-btn--primary" onClick={huntAll}
                  disabled={busy || paused || !(cc.strategies || []).length}
                  title={paused ? 'EvoSense is paused' : 'Runs every active strategy now — the same hunt the scheduler runs'}>
            {busy ? 'Hunting…' : 'Run hunt now'}
          </button>
        </>}
      />

      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      {paused ? (
        <Alert kind="warn"><span><strong>EvoSense is paused.</strong> Nothing is discovered, bought or sent. Seller
          replies are still saved and opt-outs are still honoured.</span></Alert>
      ) : null}
      {cc.sandbox && cc.sandbox.banner ? (
        <p className="evo-sandbox-line"><SandboxTag /> {cc.sandbox.banner}</p>
      ) : null}

      <Metrics label="EvoSense totals">
        <Metric label="Top opportunity" value={best ? best.opportunity_score : null} tone="success"
                sub={best ? best.address : 'nothing scored yet'} />
        <Metric label="Properties evaluated" value={t.properties_evaluated}
                sub={`${t.hunts || 0} hunt${t.hunts === 1 ? '' : 's'} run`} />
        <Metric label="Contacts found" value={t.contacts_found} tone="primary"
                sub={cc.spent.cost_per_contact_found_cents != null ? `${cents(cc.spent.cost_per_contact_found_cents)} per contact` : null} />
        <Metric label="Action required" value={t.needs_you} tone="warning" attention={t.needs_you > 0}
                sub={t.needs_you ? 'needs your judgment' : 'nothing waiting'} />
        <Metric label="Active outreach" value={t.in_outreach} tone="info" sub={`${t.nurture || 0} in nurture`} />
        <Metric label="Spent this month" value={cents(cc.spent.month_cents)} tone="violet"
                sub={`${cents(cc.spent.today_cents)} today`} />
      </Metrics>

      {/* ── NEEDS YOU ─────────────────────────────────────────────────────── */}
      {hero ? <NeedsYouHero n={hero} onOpen={() => navigate(`/wholesale/evosense/property/${hero.property_id}`)} /> : null}
      {moreNeeds.length ? (
        <Panel title="Also waiting on you" count={moreNeeds.length} className="evo-gapb">
          <ul className="evo-needlist">
            {moreNeeds.map((n) => (
              <li key={n.id}>
                <button type="button" className="evo-needrow" onClick={() => navigate(`/wholesale/evosense/property/${n.property_id}`)}>
                  <PropertyThumb address={n.address} />
                  <span className="evo-needrow__main">
                    <span className="evo-prop__addr">{n.address}</span>
                    <span className="evo-prop__sub">{(n.reasons || []).map((r) => r.label).join(' · ')}</span>
                  </span>
                  <Num kind="intent" value={n.seller_intent} />
                  <span className="evo-next">{n.next_action} →</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
      {(cc.routing_reviews || []).length ? (
        <RoutingReviews items={cc.routing_reviews} busy={busy} onResolve={resolveRouting} />
      ) : null}

      {/* ── TOP OPPORTUNITIES (the board's image-led cards) ─────────────────── */}
      <h2 className="evo-section-title">
        Top Opportunities
        <Link className="evo-link" to="/wholesale/evosense/inbox">View all in Discovery Inbox →</Link>
      </h2>
      {!hero && !spotlight.length ? (
        <Panel>
          <Empty title="Nothing found yet" icon="✓">
            No property passes an active strategy yet. Run a hunt, or wait for the next automatic one. EvoSense hands
            a conversation to you when a seller states a price, asks for an offer or a visit, or crosses a strategy's
            intent threshold.
          </Empty>
        </Panel>
      ) : (
        <div className="evo-pcards evo-gapb">
          {spotlight.map((p) => (
            <PropCard key={p.id} href={`/wholesale/evosense/property/${p.id}`}
                      address={p.address} place={[[p.city, p.state].filter(Boolean).join(', '), p.zip_code].filter(Boolean).join(' ')}
                      score={p.opportunity_score} status={<Status status={p.status} />}
                      facts={[['Contact', p.contact_confidence ?? '—'], ['Intent', p.seller_intent ?? '—'],
                              ['Type', p.property_type ? humanize(p.property_type) : '—']]}
                      money={[p.appraisal
                                ? [p.appraisal.label || 'Appraisal district tax value', money(p.appraisal.value), '']
                                : ['Market estimate', money(p.estimated_value, 'none on file'), p.estimated_value == null ? 'quiet' : ''],
                              ['ARV', (p.arv && p.arv.label) || 'Insufficient comparable sales', 'quiet'],
                              ['Equity', p.equity_pct != null ? `${Math.round(p.equity_pct)}%` : 'unknown', p.equity_pct == null ? 'quiet' : 'pos']]}
                      foot={<>{p.is_test ? <SandboxTag /> : null}<span className="evo-next">{p.next_action || '—'}</span></>} />
          ))}
        </div>
      )}

      <div className="evo-cols">
        <div className="evo-stack">
          <Panel title="Recent opportunities" flush
                 action={<Link className="evo-link" to="/wholesale/evosense/inbox">Discovery Inbox →</Link>}>
            {!found.length ? (
              <Empty title="Nothing found yet">No property passes an active strategy yet.</Empty>
            ) : (
              <div className="evo-table-wrap">
                <table className="evo-table evo-table--cards">
                  <thead>
                    <tr>
                      <th scope="col">Property</th>
                      <th scope="col" className="is-num">Opportunity</th>
                      <th scope="col" className="is-num">Contact</th>
                      <th scope="col" className="is-num">Intent</th>
                      <th scope="col">Signals</th>
                      <th scope="col">Status · next action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {found.map((p) => (
                      <tr key={p.id} className={`is-link${p.status === 'needs_you' ? ' is-attention' : ''}`}
                          onClick={() => navigate(`/wholesale/evosense/property/${p.id}`)}>
                        <td className="is-lead" data-label="">
                          <div className="evo-prop">
                            <PropertyThumb address={p.address} />
                            <div className="evo-prop__text">
                              <Link className="evo-prop__addr" to={`/wholesale/evosense/property/${p.id}`}
                                    onClick={(e) => e.stopPropagation()}>{p.address}</Link>
                              <span className="evo-prop__sub">{[p.city, p.state].filter(Boolean).join(', ')} {p.zip_code}</span>
                            </div>
                          </div>
                        </td>
                        <td className="is-num" data-label="Opportunity"><Ring kind="opportunity" value={p.opportunity_score} /></td>
                        <td className="is-num" data-label="Contact"><Num kind="contact" value={p.contact_confidence} /></td>
                        <td className="is-num" data-label="Intent"><Num kind="intent" value={p.seller_intent} /></td>
                        <td data-label="Signals"><Chips items={p.signals} max={2} /></td>
                        <td data-label="Status">
                          <span className="evo-statusnext">
                            <Status status={p.status} />
                            <span className="evo-next">{p.next_action || '—'}</span>
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel title="Pipeline at a glance" hint="every property EvoSense has evaluated, by state">
            <div className="evo-buckets">
              {cc.buckets.filter((b) => b.count).map((b) => (
                <Link key={b.key} className="evo-bucket" to={`/wholesale/evosense/inbox?bucket=${b.key}`}>
                  <span className="evo-bucket__n">{b.count}</span>
                  <Status status={b.key} />
                </Link>
              ))}
              {!cc.buckets.some((b) => b.count) ? <span className="evo-muted">No properties discovered yet.</span> : null}
            </div>
          </Panel>
        </div>

        {/* ── INTELLIGENCE RAIL ─────────────────────────────────────────── */}
        <aside className="evo-rail" aria-label="System status, budget and activity">
          <SystemStatus cc={cc} />
          <BudgetUsage spent={cc.spent} />
          <Panel title="Recent activity">
            <Feed items={(cc.recent_activity || []).map((e, i) => ({
              key: i, tone: ACT_TONE[e.action] || 'quiet',
              text: <>{e.summary}{e.address ? <> — <Link className="evo-feedlink" to={`/wholesale/evosense/property/${e.property_id}`}>{e.address}</Link></> : null}</>,
              meta: `${ago(e.at)} · ${e.actor === 'user' ? 'you' : e.actor}`,
            }))} empty="EvoSense has not done anything yet." />
          </Panel>
        </aside>
      </div>
    </EvoApp>
  )
}

function NeedsYouHero({ n, onOpen }) {
  const eco = n.economics || {}
  const contact = n.contact
  const place = [n.city, n.state].filter(Boolean).join(', ')
  return (
    <section className="evo-hero" aria-labelledby="needs-you-title">
      <div className="evo-hero__media">
        <PropertyThumb address={n.address} size="hero" label={n.is_test ? 'Sandbox · property image unavailable' : undefined} />
      </div>
      <div className="evo-hero__body">
        <div className="evo-hero__top">
          <span className="evo-hero__kicker">Needs you</span>
          <Status status="needs_you" />
          {n.is_test ? <SandboxTag /> : null}
          <span className="evo-muted evo-hero__since">handed to you {ago(n.since)}</span>
        </div>
        <h2 className="evo-hero__addr" id="needs-you-title">{n.address}</h2>
        <p className="evo-hero__place">{place}{n.zip_code ? ` ${n.zip_code}` : ''}{n.property_type ? ` · ${humanize(n.property_type)}` : ''}</p>
        {n.signals && n.signals.length ? <div className="evo-hero__chips"><Chips items={n.signals} max={6} /></div> : null}

        <Scores opportunity={n.opportunity_score} contact={n.contact_confidence} intent={n.seller_intent} />

        {n.seller_quote ? (
          <blockquote className="evo-quote evo-hero__quote">
            <span className="evo-quote__who">Seller said {n.seller_quote_at ? `· ${ago(n.seller_quote_at)}` : ''}</span>
            “{n.seller_quote}”
          </blockquote>
        ) : null}

        {(eco.asking != null || eco.mao != null) ? (
          <dl className="evo-hero__eco">
            <div><dt>Seller asking</dt><dd className="evo-money">{money(eco.asking, 'not stated')}</dd>
              <dd className="evo-hero__truth">{eco.asking != null ? 'seller stated' : ''}</dd></div>
            <div><dt>Preliminary MAO</dt><dd className="evo-money">{money(eco.mao, 'Not calculated')}</dd>
              <dd className="evo-hero__truth">{eco.mao != null ? <Tag kind="estimate">Estimate</Tag> : 'needs a verified ARV'}</dd></div>
            <div><dt>Spread</dt><dd className={`evo-money ${eco.spread > 0 ? 'is-pos' : eco.spread < 0 ? 'is-neg' : ''}`}>
              {eco.spread != null ? (eco.spread >= 0 ? '+' : '−') + money(Math.abs(eco.spread)) : '—'}</dd>
              <dd className="evo-hero__truth">preliminary</dd></div>
          </dl>
        ) : null}

        <div className="evo-hero__why">
          <p className="evo-hero__label">Why this is here</p>
          <ul>{(n.reasons || []).map((r) => <li key={r.code}>{r.label}</li>)}</ul>
        </div>

        <div className="evo-hero__foot">
          <div className="evo-hero__next">
            <span className="evo-hero__label">Next recommended action</span>
            <span className="evo-hero__nextv">{n.next_action}</span>
          </div>
          <div className="evo-hero__actions">
            {contact && contact.kind === 'phone' && !contact.is_test && !n.is_test ? (
              <a className="evo-btn evo-btn--secondary evo-btn--lg" href={`tel:${contact.value}`}>Call seller</a>
            ) : contact && contact.kind === 'phone' ? (
              <span className="evo-btn evo-btn--secondary evo-btn--lg is-static"
                    title="Sandbox record: the number is fictional, so it is shown rather than dialled.">
                Call seller · {contact.value} <Tag kind="sandbox">Sandbox</Tag>
              </span>
            ) : null}
            <button type="button" className="evo-btn evo-btn--primary evo-btn--lg" onClick={onOpen}>Open opportunity</button>
          </div>
        </div>
        <p className="evo-hero__stop">EvoSense stops here. It never makes an offer, signs anything or moves money.</p>
      </div>
    </section>
  )
}

function SystemStatus({ cc }) {
  const a = cc.automation || {}
  const i = a.inbound || {}
  const ctl = cc.controls || {}
  const rows = [
    ['Automatic hunting', a.hunting === 'active' ? ['good', 'Running'] : a.hunting === 'paused' ? ['attention', 'Paused']
      : a.hunting === 'manual' ? ['quiet', 'Manual only'] : ['quiet', 'Off']],
    ['Inbound seller replies', ['good', 'Receiving']],
    ['Paid data lookups', ctl.paused_paid_data ? ['attention', 'Paused'] : ['good', 'Allowed']],
    ['SMS outreach', ctl.paused_sms ? ['attention', 'Paused'] : ['good', 'Allowed']],
    ['AI reply reading', ctl.paused_ai_replies ? ['attention', 'Paused'] : ['good', 'On']],
  ]
  return (
    <Panel title="System status" action={<Link className="evo-link" to="/wholesale/evosense/controls">Controls</Link>}>
      <ul className="evo-sys">
        {rows.map(([k, [tone, v]]) => (
          <li key={k}><span>{k}</span><span className={`evo-status is-${tone}`}>{v}</span></li>
        ))}
      </ul>
      {(i.pending_ai_review || i.routing_review || i.held_while_paused) ? (
        <div className="evo-sys__flags">
          {i.pending_ai_review ? <span className="evo-status is-attention">{i.pending_ai_review} AI review pending</span> : null}
          {i.routing_review ? <span className="evo-status is-attention">{i.routing_review} routing review</span> : null}
          {i.held_while_paused ? <span className="evo-status is-attention">{i.held_while_paused} held while paused</span> : null}
        </div>
      ) : null}
      <dl className="evo-kv evo-sys__kv">
        <dt>Last seller reply</dt><dd>{i.last_seller_reply_at ? ago(i.last_seller_reply_at) : '—'}</dd>
        {(a.strategies || []).map((s) => (
          <FragmentRow key={s.strategy_id} k={s.name}
                       v={s.state === 'scheduled' && s.next_due_at ? when(s.next_due_at)
                         : s.state === 'running' ? 'hunting now' : humanize(s.state)} />
        ))}
      </dl>
    </Panel>
  )
}

function FragmentRow({ k, v }) {
  return <><dt title="Next automatic hunt">{k}</dt><dd>{v}</dd></>
}

function BudgetUsage({ spent }) {
  return (
    <Panel title="Budget usage" action={<Link className="evo-link" to="/wholesale/evosense/controls#budget">Budgets</Link>}>
      <div className="evo-budget__totals">
        <div><span className="evo-money">{cents(spent.today_cents)}</span><span className="evo-muted">today</span></div>
        <div><span className="evo-money">{cents(spent.month_cents)}</span><span className="evo-muted">this month</span></div>
      </div>
      <ul className="evo-budget__list">
        {(spent.strategies || []).map((s) => {
          const pct = s.daily_budget_cents ? Math.min(100, Math.round(100 * s.today_cents / s.daily_budget_cents)) : 0
          return (
            <li key={s.id}>
              <div className="evo-budget__row"><span>{s.name}</span>
                <span className="evo-mono">{cents(s.today_cents)} / {cents(s.daily_budget_cents)}</span></div>
              <div className={`evo-bar-meter${pct >= 95 ? ' is-danger' : pct >= 75 ? ' is-warning' : ''}`} role="progressbar"
                   aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${s.name}: ${pct}% of today's budget used`}>
                <span style={{ width: `${pct}%` }} />
              </div>
            </li>
          )
        })}
      </ul>
      {spent.note ? <p className="evo-muted evo-small">{spent.note}</p> : null}
    </Panel>
  )
}

function RoutingReviews({ items, busy, onResolve }) {
  return (
    <Panel title="Routing review required" count={items.length}
           hint="An inbound SMS could belong to more than one conversation. It is saved and attached to none until you choose.">
      {items.map((r) => (
        <div key={r.id} className="evo-routing">
          <blockquote className="evo-quote">“{r.body}”</blockquote>
          <div className="evo-routing__actions">
            {r.candidates.map((c) => (
              <button key={c.engagement} type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy}
                      onClick={() => onResolve(r.id, c.engagement)}>Belongs to {c.address || 'this property'}</button>
            ))}
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                    onClick={() => onResolve(r.id, null)}>None of these</button>
          </div>
        </div>
      ))}
    </Panel>
  )
}
