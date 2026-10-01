/* THE MORNING COMMAND CENTER (GET /wholesale/command-center).
 *
 * NEEDS YOU first - only work a person must do, each item saying WHY YOU ARE
 * SEEING THIS and showing the evidence (the seller's own words). Then the
 * counts a wholesaler acts on: new qualified sellers, replies, appointments,
 * properties waiting for contact data, nurture coming due, provider problems,
 * spend and pipeline movement. Every count links to the list behind it; a
 * number that leads nowhere is not shown.
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { errText } from './wsShared'
import { Alert, Empty, Metric, Metrics, Panel, ago, humanize, money } from './ds/ds'

function Count({ label, value, to, sub, tone, attention }) {
  const metric = <Metric label={label} value={value} sub={sub} tone={tone} attention={attention} />
  return to && value ? <Link to={to} style={{ textDecoration: 'none', color: 'inherit' }}>{metric}</Link> : metric
}

/* EVIDENCE DEPTH: where valuation evidence is thin, and which capabilities
 * are blocked because no REAL provider is operational (manual entry and the
 * sandbox never count as operational here). */
function EvidenceDepth({ dd, cents }) {
  const first = (x) => (x && x.items && x.items[0] ? x.items[0].link : null)
  return (
    <>
      <Metrics label="Evidence depth">
        <Count label="ARV on enough comps" value={dd.comps_enough_evidence.count} sub="deals in analysis / offer"
               tone="success" to={dd.comps_enough_evidence.link} />
        <Count label="Insufficient comps" value={dd.comps_insufficient.count} sub="no ARV, or low evidence"
               tone="warning" attention={dd.comps_insufficient.count > 0} to={first(dd.comps_insufficient)} />
        <Count label="MAO not calculated" value={dd.mao_not_calculated.count} sub="missing ARV evidence or repairs"
               tone="warning" to={first(dd.mao_not_calculated)} />
        <Count label="Qualified, awaiting analysis" value={dd.qualified_awaiting_analysis.count}
               to={first(dd.qualified_awaiting_analysis)} />
        {dd.cost && dd.cost.avg_total_cents != null ? (
          <Count label="Avg cost per promoted deal" value={cents(dd.cost.avg_total_cents)}
                 sub={`find ${cents(dd.cost.avg_cost_to_find_cents) || '—'} · qualify ${cents(dd.cost.avg_cost_to_qualification_cents) || 'not reached'}`} />
        ) : null}
      </Metrics>
      {(dd.blocked_by_provider_config || []).length ? (
        <p className="evo-prop__sub" style={{ marginTop: 8 }}>
          Provider-blocked (no real provider operational - manual entry only):{' '}
          {dd.blocked_by_provider_config.map((b, k) => (
            <span key={b.capability}>{k ? ' · ' : ''}<Link to={b.link}>{b.label}</Link>{b.waiting ? ` (${b.waiting} waiting)` : ''}</span>
          ))}
        </p>
      ) : null}
    </>
  )
}

export default function MorningCommand({ includeTest, dealSteps = 0 }) {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(null)

  useEffect(() => {
    let alive = true
    api.get('/wholesale/command-center' + (includeTest ? '?include_test=true' : ''))
      .then((d) => { if (alive) setData(d) })
      .catch((e) => { if (alive) setError(errText(e)) })
    return () => { alive = false }
  }, [includeTest])

  if (error) return <Alert>{error}</Alert>
  if (!data) return null
  const ny = data.needs_you
  const cents = (c) => (c === null || c === undefined ? null : money(c / 100))
  const moved = data.pipeline_movement

  return (
    <Panel title="This morning" count={ny.count} hint="Only what needs a person. Everything else is running.">
      {!ny.items.length ? (
        dealSteps > 0 ? (
          // Not "nothing needs you" while the deal board below lists work for a
          // person (e.g. "Add comparable sales") - the two panels must agree.
          <Empty title="No decisions waiting" icon="✓">
            {dealSteps} deal{dealSteps === 1 ? ' has' : 's have'} a next step for a person - see Needs attention below.
          </Empty>
        ) : (
        <Empty title="Nothing needs you right now" icon="✓">
          Routine work - lookups, scoring, nurture timers - is handled. Anything that needs a decision will appear here.
        </Empty>
        )
      ) : (
        <ul className="evo-needlist" aria-label="Needs you">
          {ny.items.map((i, n) => (
            <li key={`${i.kind}-${i.deal_id || i.evosense_property_id || n}`}>
              <div className="evo-needrow" style={{ display: 'block' }}>
                <button type="button" className="evo-linkbtn" style={{ textAlign: 'left', width: '100%' }}
                        onClick={() => (i.link ? navigate(i.link) : null)}>
                  <span className="evo-prop__addr">{i.title}</span>
                  <span className="evo-prop__sub"><b>Why you are seeing this:</b> {i.why}</span>
                </button>
                {i.evidence && i.evidence.length ? (
                  <>
                    <button type="button" className="evo-btn evo-btn--ghost" style={{ marginTop: 4 }}
                            aria-expanded={open === n} onClick={() => setOpen(open === n ? null : n)}>
                      {open === n ? 'Hide evidence' : `Evidence (${i.evidence.length})`}
                    </button>
                    {open === n ? (
                      <ul className="evo-prop__sub" style={{ margin: '6px 0 0 18px' }}>
                        {i.evidence.map((e, k) => <li key={k}>{e}</li>)}
                      </ul>
                    ) : null}
                  </>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      <Metrics label="This morning">
        <Count label="New qualified sellers" value={data.new_qualified.count} sub={`last ${data.new_qualified.days} days`}
               tone="success" to={data.new_qualified.items[0]?.link} />
        <Count label="Seller replies" value={data.seller_replies.count} sub="last 48 hours" tone="info"
               to={data.seller_replies.items[0]?.link} />
        <Count label="Appointments" value={data.appointments.count} tone="primary" to={data.appointments.items[0]?.link}
               attention={data.appointments.items.some((a) => a.status === 'requested')} />
        <Count label="Awaiting contact data"
               value={(data.awaiting_contact_data.wholesale_deals || 0) + (data.awaiting_contact_data.evosense_properties || 0)}
               sub={`${data.awaiting_contact_data.evosense_properties || 0} EvoSense · ${data.awaiting_contact_data.wholesale_deals || 0} deals`}
               to={data.awaiting_contact_data.evosense_properties ? data.awaiting_contact_data.evosense_link : data.awaiting_contact_data.link} />
        <Count label="Nurture due" value={data.nurture.due} sub={`${data.nurture.upcoming_14_days} more in 14 days`}
               to={data.nurture.items[0]?.link} />
        <Count label="Provider / budget problems" value={data.provider_problems.count} tone="warning"
               attention={data.provider_problems.count > 0} to={data.provider_problems.items[0]?.link} />
        {data.spend ? (
          <Count label="Spend today" value={cents(data.spend.today_cents)}
                 sub={`${cents(data.spend.month_cents)} this month${data.spend.note ? ' · sandbox prices' : ''}`} />
        ) : null}
        <Count label="Pipeline moves" value={moved.total} sub={`last ${moved.days} days`} />
      </Metrics>

      {data.seller_replies.items.length ? (
        <ul className="evo-feed" aria-label="Latest seller replies" style={{ marginTop: 12 }}>
          {data.seller_replies.items.slice(0, 5).map((r, k) => (
            <li key={k}>
              <Link to={r.link}>{r.seller || 'Seller'}</Link>: “{r.body}” <span className="evo-prop__sub">{r.at ? ago(r.at) : ''}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {moved.total ? (
        <p className="evo-prop__sub" style={{ marginTop: 8 }}>
          Moved to: {Object.entries(moved.by_stage).map(([s, n]) => `${humanize(s)} ${n}`).join(' · ')}
        </p>
      ) : null}

      {data.data_depth ? <EvidenceDepth dd={data.data_depth} cents={cents} /> : null}
    </Panel>
  )
}
