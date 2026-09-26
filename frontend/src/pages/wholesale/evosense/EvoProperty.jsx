/* EVOSENSE — PROPERTY INTELLIGENCE (Phase 7.2 redesign).
 *
 * One discovered property as a story, top to bottom:
 *   HERO           imagery, address, value, equity, occupancy, status
 *   NEXT ACTION    the operator's next move, unmistakable, with its buttons
 *   INTELLIGENCE   the three EvoSense scores, each opening its deterministic WHY
 *   CONVERSATION   the real timeline: SELLER SAID vs SYSTEM EXTRACTED
 *   SELLER FACTS   every extracted fact with its provenance (the seller's words)
 *   DEAL INTEL     asking, appraisal district tax value (reference only), ARV
 *                  (verified comps or "Insufficient comparable sales"), repairs,
 *                  MAO (only from a verified ARV), assignment target, spread
 *   WHY FOUND      the signals, with evidence and freshness
 *   OWNER/CONTACT  identity, relationship, phone/email, validation, confidence
 *   DATA INTEL     sources, freshness, conflicts, provider, cost
 *   TIMELINE       discovered -> enriched -> contacted -> reply -> handoff -> promoted
 *
 * Every value keeps its truth state. Promotion into Wholesale Operations is a
 * person's decision, confirmed in a drawer; EvoSense never makes an offer.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import {
  Alert, Drawer, Empty, EvoApp, Feed, Hero, Num, PageSkeleton, Panel, PropertyThumb, Ring, SandboxTag,
  Score, ScoreWhy, Status, Tag, ago, cents, humanize, money, shortDate, statusLabel,
} from '../ds/ds'
import '../ds/evo-pages.css'

const FRESHNESS = { current: 'FRESH', aging: 'AGING', stale: 'STALE' }

const FEEDBACK = [['GOOD_FIND', 'Good find'], ['HIGH_PRIORITY', 'High priority'], ['BAD_FIT', 'Bad fit'],
  ['WRONG_OWNER', 'Wrong owner'], ['BAD_CONTACT', 'Bad contact'],
  ['NOT_ACTUALLY_DISTRESSED', 'Not distressed'], ['IGNORE', 'Ignore']]

const MILESTONES = {
  'property.discovered': 'Discovered', 'enrichment.found': 'Contact found', 'outreach.started': 'Contacted',
  'reply.read': 'Seller replied', 'nurture.set': 'Nurture', 'handoff.opened': 'Handed to you',
  'promoted': 'Promoted to Deal Operations', 'identity.review': 'Identity review',
}

function truthKind(t) {
  const s = String(t || '')
  if (s.startsWith('SELLER')) return 'live'
  if (s.startsWith('SYSTEM') || s.includes('ESTIMATE')) return 'estimate'
  if (s.startsWith('MISSING') || s.startsWith('INSUFF') || s.startsWith('NOT')) return 'danger'
  return null
}
function Truth({ children }) {
  if (!children) return null
  return <Tag kind={truthKind(children)}>{String(children).split(' — ')[0].split(' (')[0]}</Tag>
}

export default function EvoProperty() {
  const { propertyId } = useParams()
  const [d, setD] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [reply, setReply] = useState('')
  const [nurture, setNurture] = useState('60_days')
  const [nurtureDate, setNurtureDate] = useState('')
  const [explain, setExplain] = useState(null)
  const [promoteOpen, setPromoteOpen] = useState(false)
  const [raw, setRaw] = useState(null)

  async function showRaw(obsId) {
    try { setRaw(await api.get(`/wholesale/evosense/properties/${propertyId}/observations/${obsId}/raw`)) }
    catch (e) { setError(errText(e)) }
  }

  const load = useCallback(async () => {
    try { setD(await api.get(`/wholesale/evosense/properties/${propertyId}`)); setError(null) }
    catch (e) { setError(errText(e)) }
  }, [propertyId])
  useEffect(() => { load() }, [load])

  async function act(fn, done) {
    setBusy(true); setError(null); setNotice(null)
    try { const r = await fn(); if (done) setNotice(done(r)); await load(); return r }
    catch (e) { setError(errText(e)); return null }
    finally { setBusy(false) }
  }

  if (!d) {
    return <EvoApp world="acquisition">{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>
  }

  const p = d.property
  const f = d.facts
  const eng = d.engagements[d.engagements.length - 1]
  const canPromote = !p.promoted_deal_id && p.identity_status !== 'review'
  const canReply = eng && ['active', 'responded', 'handed_off', 'nurture'].includes(eng.status)
  const canEnrich = ['high_opportunity', 'needs_enrichment', 'budget_blocked', 'waiting_for_data'].includes(p.status)
  const topContact = d.contacts[0]
  const handoffOpen = d.handoff && d.handoff.status === 'open'
  const eco = d.economics || {}
  const line = (label) => (eco.lines || []).find((l) => l.label === label) || {}
  const place = [p.city, p.state].filter(Boolean).join(', ')

  const scoreDetail = {
    opportunity: d.scores.property_opportunity,
    contact: topContact ? topContact.confidence : null,
    intent: d.scores.seller_intent,
  }

  const enrich = () => act(() => api.post(`/wholesale/evosense/properties/${propertyId}/enrich`,
    { approved: p.status === 'needs_enrichment' }), (r) => `${humanize(r.decision)}: ${(r.reasons || [])[0] || ''}`)
  const outreach = () => act(() => api.post(`/wholesale/evosense/properties/${propertyId}/outreach`, {}),
    (r) => r.started ? (r.delivery === 'sandbox_simulated'
      ? 'Opening message written — sandbox, simulated, nothing sent.' : 'Enrolled in the SMS cadence.')
      : `Refused: ${r.reason}`)

  const actions = (
    <>
      {p.promoted_deal_id ? (
        <Link className="evo-btn evo-btn--primary" to={`/wholesale/deals/${p.promoted_deal_id}`}>Open in Deal Operations</Link>
      ) : null}
      {topContact && topContact.kind === 'phone' && handoffOpen ? (
        p.is_test || topContact.is_test ? (
          <span className="evo-btn evo-btn--secondary is-static" title="Sandbox record: the number is fictional, so it is shown rather than dialled.">
            Call seller · {topContact.value}</span>
        ) : <a className="evo-btn evo-btn--secondary" href={`tel:${topContact.value}`}>Call seller</a>
      ) : null}
      {canEnrich ? (
        <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={enrich}>
          {p.status === 'needs_enrichment' ? 'Approve lookup' : 'Look up contact'}</button>
      ) : null}
      {p.status === 'ready_for_outreach' ? (
        <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={outreach}>Start outreach</button>
      ) : null}
      {canPromote ? (
        <button type="button" className={`evo-btn ${handoffOpen ? 'evo-btn--primary' : 'evo-btn--ghost'}`} disabled={busy}
                onClick={() => setPromoteOpen(true)}>Promote to Deal Operations</button>
      ) : null}
    </>
  )

  return (
    <EvoApp world="acquisition">
      <Link className="evo-crumb" to="/wholesale/evosense/inbox">← Discovery Inbox</Link>
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      {/* 1 ── HERO (board: the property workspace banner) ───────────────── */}
      <Hero
        scene="parcel" sceneDrawn
        eyebrow="EvoSense · Property Intelligence"
        title={<span id="prop-title">{p.address}{p.unit ? ` #${p.unit}` : ''}</span>}
        sub={`${place} ${p.zip_code || ''}${p.county ? ` · ${p.county} County` : ''}`}
        meta={[
          { label: statusLabel(p.status) },
          f.physical.property_type ? { label: humanize(f.physical.property_type) } : null,
          f.physical.bedrooms ? { label: `${f.physical.bedrooms} beds` } : null,
          f.physical.bathrooms ? { label: `${f.physical.bathrooms} baths` } : null,
          f.physical.square_feet ? { label: `${Number(f.physical.square_feet).toLocaleString()} sq ft` } : null,
        ]}
        score={<><Ring kind="opportunity" size="lg" value={p.opportunity_score} /><span>Opportunity score</span></>}
      />

      <section className="evo-phero" aria-label="Property snapshot">
        <div className="evo-phero__media">
          <PropertyThumb address={p.address} size="hero" lazy={false}
                         label={p.is_test ? 'Sandbox · property image unavailable' : undefined} />
        </div>
        <div className="evo-phero__body">
          <div className="evo-chips">
            <Status status={p.status} />
            {p.is_test ? <SandboxTag /> : null}
            {p.strategy_name ? <Tag>{p.strategy_name}</Tag> : null}
            {p.has_conflicts ? <Tag kind="danger">Sources conflict</Tag> : null}
          </div>
          <h2 className="evo-panel__title">Key numbers</h2>
          <dl className="evo-phero__facts">
            {f.appraisal ? (
              <div><dt>Tax value</dt><dd>{money(f.appraisal.value)}<small title="What the county appraisal district certified for property tax. Not a market value and never an ARV.">{f.appraisal.label || 'Appraisal district tax value'}</small></dd></div>
            ) : (
              <div><dt>Market estimate</dt><dd>{money(f.estimated_value.value)}<small>{f.estimated_value.value ? 'modelled estimate' : 'none on file'}</small></dd></div>
            )}
            <div><dt>ARV</dt><dd className="is-quiet">—<small>{(f.arv && f.arv.label) || 'Insufficient comparable sales'}</small></dd></div>
            <div><dt>Equity</dt><dd>{f.equity_pct.value != null ? `${f.equity_pct.value}%` : '—'}<small>{f.equity_pct.value != null ? (f.equity_pct.truth || '').toLowerCase().split(' (')[0] : 'missing'}</small></dd></div>
            <div><dt>Occupancy</dt><dd>{f.occupancy.value ? humanize(f.occupancy.value) : '—'}<small>{f.occupancy.source || 'not reported'}</small></dd></div>
            <div><dt>Size</dt><dd>{f.physical.bedrooms || '—'}bd · {f.physical.bathrooms || '—'}ba<small>{f.physical.square_feet ? `${Number(f.physical.square_feet).toLocaleString()} sq ft` : 'sq ft unknown'}{f.physical.year_built ? ` · ${f.physical.year_built}` : ''}</small></dd></div>
            <div><dt>Owned</dt><dd>{f.ownership_years.value != null ? `${f.ownership_years.value} yrs` : '—'}<small>{f.ownership_years.last_sale_date ? `sold ${shortDate(f.ownership_years.last_sale_date)}` : 'no sale date'}</small></dd></div>
          </dl>
        </div>
      </section>

      {/* 10 ── NEXT ACTION (placed first: it is what the operator came for) ─ */}
      <section className="evo-nextcard" aria-label="Next action">
        <div>
          <span className="evo-hero__label">{handoffOpen ? 'Needs you · next action' : 'Next action'}</span>
          <div className="evo-nextcard__v">{p.next_action || '—'}</div>
          {p.next_action_detail ? <div className="evo-nextcard__d">{p.next_action_detail}</div> : null}
          {handoffOpen && !p.next_action_detail ? <div className="evo-nextcard__d">{d.handoff.reasons.map((r) => r.label).join(' · ')}</div> : null}
        </div>
        <div className="evo-nextcard__actions">{actions}</div>
      </section>

      <nav className="evo-sec-nav" aria-label="Sections">
        {[['intel', 'Intelligence'], ['conversation', 'Conversation'], ['facts', 'Seller facts'], ['deal', 'Deal intelligence'],
          ['signals', 'Why found'], ['owner', 'Owner & contact'], ['data', 'Data intelligence'], ['timeline', 'Timeline']]
          .map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
      </nav>

      <div className="evo-cols evo-cols--wide-rail">
        <div className="evo-stack">
          {/* 2 ── INTELLIGENCE ─────────────────────────────────────────── */}
          <Panel title="EvoSense intelligence" id="intel" hint="Select a score to see exactly why">
            <div className="evo-scores">
              {['opportunity', 'contact', 'intent'].map((k) => (
                <Score key={k} kind={k}
                       value={k === 'opportunity' ? p.opportunity_score : k === 'contact' ? p.contact_confidence : p.seller_intent}
                       expanded={explain === k} onExplain={() => setExplain(explain === k ? null : k)} />
              ))}
            </div>
            {explain ? (
              <div className="evo-scoreexp">
                <p className="evo-scoreexp__t">Why {k2name(explain)} is {explain === 'opportunity' ? p.opportunity_score ?? '—'
                  : explain === 'contact' ? p.contact_confidence ?? '—' : p.seller_intent ?? '—'}</p>
                <ScoreWhy score={scoreDetail[explain]} />
                {explain === 'intent' && !scoreDetail.intent ? <p className="evo-muted evo-small">Intent comes only from what the seller says.</p> : null}
              </div>
            ) : null}
          </Panel>

          {/* 5 ── CONVERSATION ─────────────────────────────────────────── */}
          <Panel title="Seller conversation" id="conversation"
                 hint={eng ? (eng.delivery_mode === 'sandbox_simulated' ? 'Sandbox — messages are simulated; nothing is sent' : 'Platform SMS') : null}>
            {!eng ? <Empty title="No conversation yet">{p.next_action}.</Empty> : (
              <>
                {eng.blocked_reason ? <Alert kind="warn">Outreach {humanize(eng.status).toLowerCase()}: {eng.blocked_reason}</Alert> : null}
                {eng.nurture_until ? <Alert kind="info">Nurture until {shortDate(eng.nurture_until)} — {eng.nurture_reason}</Alert> : null}
                <ol className="evo-convo" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  {eng.messages.map((m) => {
                    const extracted = d.seller_facts.filter((x) => x.message_id === m.id && !x.superseded)
                    return (
                      <li key={m.id} className={`evo-msg evo-msg--${m.direction === 'inbound' ? 'in' : 'out'}`}>
                        <div className="evo-msg__meta">
                          <span>{m.direction === 'inbound' ? 'Seller said' : 'EvoSense sent'}</span>
                          <span>· {ago(m.at)}</span>
                          {m.delivery === 'sandbox_simulated' ? <Tag kind="sandbox">Simulated</Tag> : null}
                        </div>
                        <div>{m.body}</div>
                        {m.direction === 'inbound' && (m.outcome || extracted.length) ? (
                          <div className="evo-msg__extracted">
                            <span className="evo-hero__label" style={{ margin: 0 }}>System extracted</span>
                            {m.outcome ? <Tag>{humanize(m.outcome)}</Tag> : null}
                            {extracted.map((x) => (
                              <span key={x.id} className="evo-chip">{humanize(x.fact_type)}: {x.fact_type === 'asking_price' ? money(x.value) : humanize(x.value)}</span>
                            ))}
                          </div>
                        ) : null}
                        {m.pending ? (
                          <div className="evo-msg__extracted">
                            <Tag kind="danger">{m.pending.startsWith('EvoSense is paused') ? 'Held while paused' : 'AI review pending'}</Tag>
                            <span>{m.pending}</span>
                            <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy}
                                    onClick={() => act(() => api.post(`/wholesale/evosense/properties/${propertyId}/retry-reading`, {}),
                                      (r) => `Retried ${r.retried.length}: ${r.retried.map((x) => x.outcome ? humanize(x.outcome) : 'still pending').join(', ')}`)}>
                              Retry reading</button>
                          </div>
                        ) : null}
                      </li>
                    )
                  })}
                </ol>
              </>
            )}
            {canReply ? (
              <form style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8 }} onSubmit={(e) => {
                e.preventDefault()
                act(() => api.post(`/wholesale/evosense/properties/${propertyId}/reply`,
                  { text: reply, delivery: p.is_test ? 'sandbox_simulated' : 'manual_entry' }),
                (r) => {
                  setReply('')
                  if (r.pending) return `Saved. ${r.why || 'Reading pending.'}`
                  const via = r.path ? ' Delivered through the platform inbound SMS path.' : ''
                  return `Read as ${humanize(r.outcome)}.${via} ${(r.actions || []).join(' · ')}`
                })
              }}>
                <label htmlFor="es-reply" className="evo-field__label">
                  {p.is_test ? 'Simulate a seller reply (sandbox)' : 'Record a reply the seller sent you elsewhere'}</label>
                <textarea id="es-reply" className="evo-textarea" rows={2} value={reply} onChange={(e) => setReply(e.target.value)} />
                <div><button type="submit" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy || !reply.trim()}>Record reply</button></div>
              </form>
            ) : null}
          </Panel>

          {/* 6 ── SELLER FACTS ─────────────────────────────────────────── */}
          <Panel title="Seller facts" id="facts" hint="Every fact keeps the seller's own words">
            {!d.seller_facts.filter((x) => !x.superseded).length ? (
              <p className="evo-muted" style={{ margin: 0 }}>The seller has not stated anything yet. Nothing is inferred.</p>
            ) : (
              <table className="evo-facts">
                <tbody>
                  {d.seller_facts.filter((x) => !x.superseded).map((x) => (
                    <tr key={x.id}>
                      <td className="evo-facts__k">{humanize(x.fact_type)}</td>
                      <td>
                        <span className="evo-facts__v">{x.fact_type === 'asking_price' ? money(x.value) : humanize(x.value)}</span>{' '}
                        <Truth>{x.truth}</Truth>
                        <span className="evo-facts__q">“{x.quote}” · extracted by {x.extracted_by || 'rules'} · {ago(x.at)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          {/* 7 ── DEAL INTELLIGENCE ────────────────────────────────────── */}
          <Panel title="Deal intelligence" id="deal" hint="Preliminary and internal · never shown to a seller or investor">
            <div className="evo-econ">
              {[['Seller asking', line('Seller asking')],
                ['Appraisal district tax value', line('Appraisal District Tax Value')],
                ['ARV', line('ARV')],
                ['Repairs', line('Repairs')], ['Preliminary MAO', line('Preliminary MAO')],
                ['Assignment target', line('Wholesale fee')]].map(([label, l]) => (
                <div key={label} className="evo-econ__cell">
                  <div className="evo-econ__k">{label}</div>
                  <div className="evo-econ__v">{l.value != null ? money(l.value) : (l.display || '—')}</div>
                  <div className="evo-econ__t"><Truth>{l.truth_label}</Truth></div>
                </div>
              ))}
              <div className="evo-econ__cell">
                <div className="evo-econ__k">Potential spread</div>
                <div className="evo-econ__v" style={{ color: eco.spread > 0 ? 'var(--evo-success-ink)' : eco.spread < 0 ? 'var(--evo-danger-ink)' : undefined }}>
                  {eco.spread != null ? (eco.spread >= 0 ? '+' : '−') + money(Math.abs(eco.spread)) : '—'}</div>
                <div className="evo-econ__t"><Tag kind="estimate">Estimate</Tag></div>
              </div>
            </div>
            {eco.verdict ? <p style={{ margin: '14px 0 0', color: 'var(--evo-text-primary)' }}>{eco.verdict}</p> : null}
            {(eco.warnings || []).map((w, i) => <p key={i} className="evo-muted evo-small" style={{ margin: '6px 0 0' }}>{w}</p>)}
            <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>{eco.notice}</p>
          </Panel>

          {/* 3 ── WHY FOUND ────────────────────────────────────────────── */}
          <Panel title="Why EvoSense found it" id="signals" hint="Evidence, not truth · aging counts half, stale counts nothing">
            {!d.signals.length ? <p className="evo-muted" style={{ margin: 0 }}>No signals.</p> : (
              <ul className="evo-signal-list">
                {d.signals.map((s) => (
                  <li key={s.signal_type} className={`evo-signal-item${s.freshness === 'stale' ? ' is-stale' : ''}`}>
                    <span className="evo-signal-item__name">{s.label}{s.value ? <span className="evo-muted" style={{ fontWeight: 400 }}> · {s.value}</span> : null}</span>
                    <span className="evo-chips">
                      <Tag kind={s.freshness === 'current' ? 'live' : s.freshness === 'stale' ? 'danger' : undefined}>{FRESHNESS[s.freshness] || 'UNKNOWN'}</Tag>
                      {s.family && s.family !== s.signal_type ? <Tag kind="info">{humanize(s.family.toLowerCase())}</Tag> : null}
                      {s.derived ? <Tag kind="estimate">derived</Tag> : <Tag>record</Tag>}
                    </span>
                    <span className="evo-signal-item__meta">
                      {s.evidence.map((e) => [
                        e.source === 'evosense_derived' ? 'computed from facts' : e.source,
                        e.connector ? humanize(e.connector) : null,
                        e.observed_at ? shortDate(e.observed_at) : null,
                        e.confidence ? `${e.confidence}% confidence` : null,
                        e.provenance && e.provenance.rule ? e.provenance.rule : null,
                        e.provenance && e.provenance.rule_version ? e.provenance.rule_version : null,
                      ].filter(Boolean).join(' · ')).join('  |  ')}
                    </span>
                    {(s.evidence.find((e) => e.provenance && e.provenance.limitations) || {}).provenance ? (
                      <span className="evo-signal-item__meta" style={{ fontStyle: 'italic' }}>
                        Limitation: {s.evidence.find((e) => e.provenance && e.provenance.limitations).provenance.limitations}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {/* 9 ── TIMELINE ─────────────────────────────────────────────── */}
          <Panel title="Timeline" id="timeline">
            <ol className="evo-timeline">
              {d.timeline.slice().reverse().map((t, i) => (
                <li key={i} className={MILESTONES[t.action] ? 'is-milestone' : ''}>
                  <div className="evo-timeline__t">{MILESTONES[t.action] ? <strong>{MILESTONES[t.action]} · </strong> : null}{t.summary}</div>
                  <div className="evo-timeline__m">{shortDate(t.at)} · {new Date(t.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} · {t.actor === 'user' ? 'a person' : t.actor}</div>
                </li>
              ))}
            </ol>
          </Panel>
        </div>

        <aside className="evo-rail" aria-label="Owner, contact and data">
          {/* 4 ── OWNER / CONTACT ──────────────────────────────────────── */}
          <Panel title="Owner & contact" id="owner">
            {!d.owners.length ? <p className="evo-muted" style={{ margin: 0 }}>No owner of record.</p> : d.owners.map((o) => (
              <div key={o.id} style={{ marginBottom: 14 }}>
                <div className="evo-strong" style={{ fontSize: 16 }}>{o.name}{' '}
                  {o.in_conflict ? <Tag kind="danger">Conflict</Tag> : null}{!o.current ? <Tag>Former</Tag> : null}</div>
                <div className="evo-muted evo-small">{humanize(o.owner_type)} · <span style={{ color: o.resolution === 'unresolved' ? 'var(--evo-warning-ink)' : undefined }}>{o.resolution_label}</span>
                  {o.properties_owned_here > 1 ? ` · owns ${o.properties_owned_here} properties here` : ''}</div>
                <dl className="evo-kv" style={{ marginTop: 10, fontSize: 13 }}>
                  <dt>Mailing</dt><dd style={{ textAlign: 'right', whiteSpace: 'normal' }}>{o.mailing || '—'}</dd>
                  <dt>Sources</dt><dd>{o.sources.join(', ') || '—'}</dd>
                  {o.persons.length ? <><dt>People</dt><dd style={{ whiteSpace: 'normal' }}>{o.persons.map((x) => `${x.name} (${humanize(x.role).toLowerCase()})`).join(', ')}</dd></> : null}
                </dl>
              </div>
            ))}
            {!d.contacts.length ? <p className="evo-muted evo-small" style={{ margin: 0 }}>No contact found. Nothing is invented.</p> : d.contacts.map((c) => (
              <div key={c.id} className="evo-contact">
                <span className="evo-contact__v">{c.value}</span>
                <Num kind="contact" value={c.confidence ? c.confidence.value : null} />
                <span className="evo-contact__meta">
                  <span>{c.person} · {humanize(c.role).toLowerCase()}</span>
                  <Tag>{humanize(c.kind)}</Tag>
                  {c.line_type ? <Tag>{humanize(c.line_type)}</Tag> : null}
                  <Tag kind={c.validation === 'valid' ? 'live' : undefined}>{humanize(c.validation || 'unvalidated')}</Tag>
                  <Tag kind={c.connector_kind === 'sandbox' ? 'sandbox' : undefined}>{c.connector_label || c.source}</Tag>
                  {c.agreeing_sources > 1 ? <span>{c.agreeing_sources} sources agree</span> : null}
                </span>
                {c.status !== 'active' ? <span className="evo-contact__meta" style={{ color: 'var(--evo-danger-ink)' }}>{humanize(c.status)} — {c.status_reason}</span> : null}
              </div>
            ))}
            {d.sms_eligibility ? (
              <div style={{ marginTop: 12 }} className="evo-small">
                <span>SMS: </span>
                <strong style={{ color: d.sms_eligibility.verdict.startsWith('ELIGIBLE') ? undefined : 'var(--evo-warning-ink)' }}>
                  {d.sms_eligibility.verdict}</strong>
                {d.sms_eligibility.phones.map((x) => (
                  <div key={x.contact_id} className="evo-muted">{x.phone}: {x.eligible ? 'consent of record' : x.reasons.map((r) => humanize(r.toLowerCase())).join(', ')}</div>
                ))}
                <div className="evo-muted" style={{ marginTop: 4 }}>{d.sms_eligibility.rule}</div>
              </div>
            ) : null}
            {d.eligibility ? (
              <details style={{ marginTop: 12 }} open={!d.eligibility.eligible}>
                <summary className="evo-small" style={{ cursor: 'pointer' }}>Outreach eligibility: <strong>{d.eligibility.eligible ? 'eligible' : 'not eligible'}</strong></summary>
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5 }}>{d.eligibility.checks.map((c) => (
                  <li key={c.code} style={{ color: c.ok ? 'var(--evo-text-secondary)' : 'var(--evo-danger-ink)' }}>{c.ok ? '✓' : '✕'} {c.label}{c.detail && !c.ok ? ` — ${c.detail}` : ''}</li>))}</ul>
              </details>
            ) : null}
          </Panel>

          {/* 8 ── DATA INTELLIGENCE ────────────────────────────────────── */}
          <Panel title="Data intelligence" id="data" action={<span className="evo-money">{cents(d.spent_cents)}</span>}>
            <dl className="evo-kv" style={{ fontSize: 13 }}>
              <dt>Found by</dt><dd>{d.attribution.first_strategy || '—'}</dd>
              <dt>Sources</dt><dd>{d.attribution.sources.length}</dd>
              <dt>Tax value year</dt><dd>{f.appraisal && f.appraisal.year ? f.appraisal.year : '—'}</dd>
              <dt>Conflicts</dt><dd style={{ color: d.conflicts.length ? 'var(--evo-warning-ink)' : undefined }}>{d.conflicts.length || 'none'}</dd>
            </dl>
            <div className="evo-divider" />
            <p className="evo-hero__label">Provenance — raw evidence kept</p>
            <Feed items={(d.provenance || []).map((o) => ({
              key: o.id, tone: o.connector_kind === 'sandbox' ? 'attention' : 'good',
              text: <>{o.provider_label} · {humanize(o.capability.toLowerCase())}{' '}
                {o.has_raw ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => showRaw(o.id)}>Raw record</button> : null}</>,
              meta: [o.connector_label, o.match ? `${o.match} match` : null,
                     `retrieved ${shortDate(o.retrieved_at)}`, o.source_updated_at ? `source as of ${shortDate(o.source_updated_at)}` : null,
                     o.adapter_version, o.content_hash ? `sha256 ${o.content_hash.slice(0, 10)}…` : null,
                     o.evidence && o.evidence.city_basis ? `city: ${o.evidence.city_basis}` : null].filter(Boolean).join(' · '),
            }))} empty="No provider observations." />
            {d.lookups && d.lookups.length ? (
              <details style={{ marginTop: 12 }}>
                <summary className="evo-small" style={{ cursor: 'pointer' }}>Free public-record lookups ({d.lookups.length})</summary>
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, color: 'var(--evo-text-secondary)' }}>
                  {d.lookups.map((x) => <li key={x.id}><strong>{x.governor}</strong> · {x.provider_label}{x.outcome && x.outcome !== 'skipped' ? ` → ${humanize(x.outcome)}` : ''} — {(x.reasons || [])[0]}</li>)}
                </ul>
              </details>
            ) : null}
            {d.ledger.length ? (
              <>
                <div className="evo-divider" />
                <p className="evo-hero__label">Cost ledger</p>
                <dl className="evo-kv" style={{ fontSize: 13 }}>
                  {d.ledger.map((l) => (
                    <FragmentKV key={l.id} k={`${l.provider} · ${humanize(l.operation).toLowerCase()}`}
                                v={<>{cents(l.cents)}{l.status !== 'charged' ? <span className="evo-muted"> ({l.status === 'failed_refunded' ? 'refunded' : l.status})</span> : null}</>} />
                  ))}
                </dl>
              </>
            ) : <p className="evo-muted evo-small" style={{ margin: '12px 0 0' }}>Nothing spent on this property.</p>}
            {d.enrichment.length ? (
              <details style={{ marginTop: 12 }}>
                <summary className="evo-small" style={{ cursor: 'pointer' }}>Enrichment decisions ({d.enrichment.length})</summary>
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, color: 'var(--evo-text-secondary)' }}>
                  {d.enrichment.map((x) => <li key={x.id}><strong>{x.governor || humanize(x.decision)}</strong>{x.outcome && x.outcome !== 'skipped' ? ` → ${humanize(x.outcome)}` : ''} — {(x.reasons || [])[0]}</li>)}
                </ul>
              </details>
            ) : null}
            {d.conflicts.length ? (
              <div style={{ marginTop: 12 }}>
                <p className="evo-hero__label">Conflicts — not overwritten</p>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: 'var(--evo-text-secondary)' }}>{d.conflicts.map((c, i) => (
                  <li key={i}>{humanize(c.field)}: {String(c.incoming.value)} from {c.incoming.source} vs {(c.existing || []).map((x) => `${x.value || x.owner} (${x.source})`).join(', ')}</li>))}</ul>
              </div>
            ) : null}
            {d.identity_reviews.length ? d.identity_reviews.map((r) => (
              <div key={r.id} style={{ marginTop: 12 }}>
                <Alert kind="warn">{r.reason}</Alert>
                <div className="evo-actionbar">
                  <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy}
                          onClick={() => act(() => api.post(`/wholesale/evosense/identity-reviews/${r.id}`, { action: 'merge', property_id: propertyId }), () => 'Merged into this property.')}>
                    Same property — merge</button>
                  <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                          onClick={() => act(() => api.post(`/wholesale/evosense/identity-reviews/${r.id}`, { action: 'new' }), () => 'Kept as a separate property.')}>
                    Different property</button>
                </div>
              </div>
            )) : null}
          </Panel>

          {eng && !['stopped', 'promoted'].includes(eng.status) ? (
            <Panel title="Nurture">
              <div className="evo-actionbar">
                <label className="evo-sr" htmlFor="es-nurt">Follow up in</label>
                <select id="es-nurt" className="evo-select" style={{ width: 'auto', flex: 1 }} value={nurture} onChange={(e) => setNurture(e.target.value)}>
                  <option value="30_days">30 days</option><option value="60_days">60 days</option>
                  <option value="90_days">90 days</option><option value="6_months">6 months</option>
                  <option value="date">A specific date</option>
                </select>
                {nurture === 'date' ? <input type="date" className="evo-input" style={{ width: 'auto' }} value={nurtureDate}
                                             aria-label="Follow-up date" onChange={(e) => setNurtureDate(e.target.value)} /> : null}
                <button type="button" className="evo-btn evo-btn--secondary" disabled={busy}
                        onClick={() => act(() => api.post(`/wholesale/evosense/properties/${propertyId}/nurture`, { choice: nurture, date: nurtureDate || null }),
                          (r) => `Nurture until ${shortDate(r.nurture_until)}.`)}>Set</button>
              </div>
            </Panel>
          ) : null}

          <Panel title="Your judgment" hint="Recorded for review · EvoSense does not retrain itself">
            <div className="evo-chips" style={{ gap: 8 }}>
              {FEEDBACK.map(([k, label]) => (
                <button key={k} type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                        onClick={() => act(() => api.post(`/wholesale/evosense/properties/${propertyId}/feedback`, { kind: k }), () => `Recorded: ${label}.`)}>{label}</button>
              ))}
            </div>
            {d.feedback.length ? <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>Last: {humanize(d.feedback[0].kind)} · {ago(d.feedback[0].at)}</p> : null}
          </Panel>
        </aside>
      </div>

      <Drawer open={promoteOpen} onClose={() => setPromoteOpen(false)} title="Promote to Deal Operations"
              sub={`${p.address}, ${place}`}
              footer={<>
                <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setPromoteOpen(false)}>Cancel</button>
                <button type="button" className="evo-btn evo-btn--primary" disabled={busy}
                        onClick={async () => {
                          const r = await act(() => api.post(`/wholesale/evosense/properties/${propertyId}/promote`, {}),
                            (x) => x.already ? 'Already in Deal Operations.' : 'Promoted into Deal Operations.')
                          if (r) setPromoteOpen(false)
                        }}>Promote</button>
              </>}>
        <p style={{ marginTop: 0 }}>This creates a Wholesale deal for this property and carries its EvoSense history with it:
          owner, contacts, signals, the seller's words and the preliminary numbers.</p>
        <ul style={{ color: 'var(--evo-text-secondary)', paddingLeft: 18, lineHeight: 1.7 }}>
          <li>Nothing is sent to the seller.</li>
          <li>No offer is made — offers still go through Deal Operations' approval gates.</li>
          <li>EvoSense stops working this owner; the deal team takes over.</li>
        </ul>
      </Drawer>

      <Drawer open={!!raw} onClose={() => setRaw(null)} title="Raw source record"
              sub={raw ? `${raw.provider} · ${raw.reference}` : ''}
              footer={<button type="button" className="evo-btn evo-btn--ghost" onClick={() => setRaw(null)}>Close</button>}>
        {raw ? (
          <>
            <dl className="evo-kv" style={{ fontSize: 12.5 }}>
              <dt>Retrieved</dt><dd>{raw.retrieved_at ? new Date(raw.retrieved_at).toLocaleString() : '—'}</dd>
              <dt>Source as of</dt><dd>{raw.source_updated_at ? shortDate(raw.source_updated_at) : 'not stated by the source'}</dd>
              <dt>Adapter</dt><dd>{raw.adapter_version || '—'}</dd>
              <dt>SHA-256</dt><dd style={{ wordBreak: 'break-all' }}>{raw.content_hash || '—'}</dd>
              {raw.source_url ? <><dt>Publisher</dt><dd><a href={raw.source_url} target="_blank" rel="noreferrer">open</a></dd></> : null}
            </dl>
            <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 11.5, maxHeight: 360, overflow: 'auto',
                          background: 'var(--evo-surface-2, #f6f7f9)', padding: 10, borderRadius: 8 }}>{raw.raw || '(no raw payload recorded)'}</pre>
          </>
        ) : null}
      </Drawer>
    </EvoApp>
  )
}

function k2name(k) {
  return { opportunity: 'Property Opportunity', contact: 'Contact Confidence', intent: 'Seller Intent' }[k]
}

function FragmentKV({ k, v }) {
  return <><dt>{k}</dt><dd>{v}</dd></>
}

