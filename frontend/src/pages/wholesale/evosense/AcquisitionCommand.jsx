/* PROJECT OVERDRIVE — ONE-PROPERTY ACQUISITION COMMAND (Design B, owner
 * approved and locked Oct 29, 2026 — #439, docs/wholesale/OVERDRIVE_OWNER_LOCKED.md).
 *
 * The pieces of the approved screen that EvoProperty.jsx did not already have:
 *   header       address, city / county / state, type, parcel, distress score
 *                with its explanation, readiness, work priority, Launch Deal
 *   overview     photo, map links, owner, mailing address, ownership and
 *                transfer history, source and verification labels
 *   six KPIs     distress score, motivation signals, equity, ARV, repairs, MAO
 *   actions      Verify Ownership, Analyze Deal, Find Active Buyers, Enrich
 *                Contacts, Schedule Follow-Up, Add to Pipeline, Launch Deal
 *   tabs         Overview, Ownership, Distress Signals, Comps & Value,
 *                Underwriting, Buyers, Contacts, AI Assistant, Activity
 *
 * EVERY NUMBER COMES FROM GET /wholesale/evosense/properties/{id} (the same
 * payload the screen already loaded) or from a labelled calculation the server
 * made. Nothing from the approved mockup — no owner, contact, dollar figure,
 * image, sale or buyer statistic — exists in this file. Unknown stays unknown.
 *
 * EVERY ACTION IS A REAL ONE: it switches to the tab whose existing panels do
 * the work, opens an existing drawer, or calls an existing endpoint. When an
 * action cannot run, the button says why instead of pretending.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Alert, Drawer, Metric, Metrics, Panel, Score, ScoreWhy, Seg, Tag, humanize, money, shortDate } from '../ds/ds'
import { MeasureTags } from './Measures'
import { DealPackagePanel } from '../wsPackage'
import { UnderwritingTab } from '../wsUnderwriting'
import DealCalculator from '../calc/DealCalculator'
import { prefillFromProperty } from '../calc/prefill'
import '../wholesale.css'
import '../wsUnderwriting.css'
import './acquisition-command.css'
import { kpis, ownershipChecks, readiness } from './acqModel'

export { ACQ_TABS, TAB_KEYS, HASH_TO_TAB, tabFrom, kpis, ownershipChecks, readiness } from './acqModel'

export function AcqHeader({ d, onLaunch, canLaunchUi, explain, setExplain }) {
  const p = d.property
  const f = d.facts || {}
  const phys = f.physical || {}
  const rd = readiness(d)
  const where = [p.city, p.county ? `${p.county} County` : null, p.state || null].filter(Boolean).join(', ')
  const type = phys.property_type ? humanize(phys.property_type) : 'Type not on record'
  const parcel = phys.parcel_apn || p.parcel_apn
  const scoreDetail = d.scores && d.scores.property_opportunity
  return (
    <header className="acq-head" aria-labelledby="prop-title">
      <div className="acq-head__main">
        <p className="acq-head__eyebrow">Acquisition Command · Property</p>
        <h1 className="acq-head__title" id="prop-title">{p.address}{p.unit ? ` #${p.unit}` : ''}</h1>
        <p className="acq-head__sub">{where}{p.zip_code ? ` ${p.zip_code}` : ''}</p>
        <div className="acq-head__chips">
          <Tag>{type}</Tag>
          <Tag kind={parcel ? undefined : 'danger'}>{parcel ? `Parcel ${parcel}` : 'Parcel not on record'}</Tag>
          <Tag kind={rd.tone} title="How much verified evidence the assessment rests on">{rd.label}</Tag>
          {rd.stage ? <Tag>{rd.stage}</Tag> : null}
          {p.is_test ? <Tag kind="sandbox">Practice record</Tag> : null}
          {p.has_conflicts ? <Tag kind="danger">Sources conflict</Tag> : null}
        </div>
        {d.opportunity && d.opportunity.measures ? (
          <div className="acq-head__measures"><MeasureTags m={d.opportunity.measures} /></div>
        ) : null}
      </div>
      <div className="acq-head__side">
        <div className="acq-head__score">
          <Score kind="opportunity" value={p.opportunity_score} expanded={explain} onExplain={() => setExplain(!explain)} />
          <span className="acq-head__scorelabel">Distress score</span>
        </div>
        {canLaunchUi ? (
          <button type="button" className="evo-btn evo-btn--primary acq-launch" onClick={onLaunch} data-action="launch-deal">
            Launch Deal</button>
        ) : null}
      </div>
      {explain ? (
        <div className="acq-head__why">
          <p className="evo-scoreexp__t">Why the distress score is {p.opportunity_score ?? '—'}</p>
          <ScoreWhy score={scoreDetail} />
          <p className="evo-muted evo-small" style={{ margin: '6px 0 0' }}>Rules-based points from evidence on file. Not a probability of a deal.</p>
        </div>
      ) : null}
    </header>
  )
}

export function AcqOverview({ d, photo, links, recordFacts }) {
  const p = d.property
  const f = d.facts || {}
  const owners = d.owners || []
  const current = owners.filter((o) => o.current !== false)
  const former = owners.filter((o) => o.current === false)
  const rf = recordFacts
  const deed = f.deed_transfer || {}
  return (
    <section className="acq-overview" aria-label="Property overview">
      <div className="acq-overview__media">{photo}{links}</div>
      <div className="acq-overview__body">
        <h2 className="evo-panel__title">Ownership</h2>
        {current.length ? current.map((o) => (
          <div key={o.id} className="acq-owner">
            <div className="acq-owner__name">{o.name || 'name not on record'}{' '}
              {o.in_conflict ? <Tag kind="danger">Conflict</Tag> : null}
              <Tag kind={o.resolution && o.resolution !== 'unresolved' ? 'live' : 'estimate'}>{o.resolution_label || 'Unverified'}</Tag>
            </div>
            <dl className="acq-kv">
              <dt>Owner type</dt><dd>{o.owner_type_label || humanize(o.owner_type || 'unknown')}</dd>
              <dt>Mailing address</dt><dd>{o.mailing || <span className="evo-muted">not on record</span>}</dd>
              <dt>Lives at property</dt><dd>{!o.mailing ? <span className="evo-muted">unknown</span> : rf.away ? `No - mail goes${rf.awayCity ? ` to ${rf.awayCity}` : ' elsewhere'}` : 'Mail goes to the property'}</dd>
              <dt>Source</dt><dd>{(o.sources || []).join(', ') || <span className="evo-muted">not recorded</span>}</dd>
            </dl>
          </div>
        )) : <p className="evo-muted" style={{ margin: '0 0 10px' }}>No owner of record. Nothing is guessed.</p>}
        <h3 className="acq-subtitle">Ownership &amp; transfer history</h3>
        <ul className="acq-history">
          <li><b>Last deed transfer:</b> {deed.date ? `${shortDate(deed.date)} · sale price not public in Texas` : <span className="evo-muted">no transfer date on record</span>}</li>
          <li><b>Years owned:</b> {f.ownership_years && f.ownership_years.value != null ? `${f.ownership_years.value}` : <span className="evo-muted">unknown</span>}</li>
          {former.map((o) => <li key={o.id}><b>Former owner:</b> {o.name || 'name not on record'} <span className="evo-muted">· {(o.sources || []).join(', ') || 'source not recorded'}</span></li>)}
        </ul>
        <p className="evo-muted evo-small" style={{ margin: '8px 0 0' }}>
          {(d.provenance || []).length} source record{(d.provenance || []).length === 1 ? '' : 's'} kept
          {f.appraisal && f.appraisal.source ? ` · county record from ${f.appraisal.source}${f.appraisal.at ? `, read ${shortDate(f.appraisal.at)}` : ''}` : ''}
          {(d.conflicts || []).length ? ` · ${(d.conflicts || []).length} conflict${d.conflicts.length === 1 ? '' : 's'} shown, not overwritten` : ''}
        </p>
      </div>
    </section>
  )
}

export function AcqKpis({ d }) {
  const k = kpis(d)
  return (
    <Metrics label="Property key numbers">
      <Metric label="Distress score" value={k.distress.value} sub={k.distress.sub} />
      <Metric label="Motivation signals" value={k.signals.value} sub={k.signals.sub} />
      <Metric label="Estimated equity" value={k.equity.value} sub={k.equity.sub} />
      <Metric label="After repair value" value={k.arv.value} sub={k.arv.sub} />
      <Metric label="Estimated repairs" value={k.repairs.value} sub={k.repairs.sub} />
      <Metric label="Max wholesale offer" value={k.mao.value} sub={k.mao.sub} />
    </Metrics>
  )
}

/** The seven approved actions. `why` explains any action that cannot run. */
export function AcqActions({ items }) {
  const blocked = items.filter((a) => a.disabled && a.why)
  return (
    <div className="acq-actions" role="toolbar" aria-label="Property actions">
      <div className="acq-actions__row">
        {items.map((a) => (
          a.href ? (
            <Link key={a.key} className={`evo-btn ${a.primary ? 'evo-btn--primary' : 'evo-btn--secondary'}`} to={a.href} data-action={a.key}>{a.label}</Link>
          ) : (
            <button key={a.key} type="button" className={`evo-btn ${a.primary ? 'evo-btn--primary' : 'evo-btn--secondary'}`}
                    disabled={a.disabled} title={a.why || a.hint || undefined} onClick={a.onClick} data-action={a.key}
                    aria-describedby={a.disabled && a.why ? `acq-why-${a.key}` : undefined}>{a.label}</button>
          )
        ))}
      </div>
      {blocked.length ? (
        <ul className="acq-actions__why">
          {blocked.map((a) => <li key={a.key} id={`acq-why-${a.key}`}><b>{a.label}:</b> {a.why}</li>)}
        </ul>
      ) : null}
    </div>
  )
}

export function OwnershipVerification({ d, countyUrl }) {
  const checks = ownershipChecks(d)
  const done = checks.filter((c) => c.ok).length
  return (
    <Panel title="Ownership verification" id="verify" hint={`${done} of ${checks.length} checks pass · from the records on file`}>
      <ul className="acq-checks">
        {checks.map((c) => (
          <li key={c.key} className={c.ok ? 'is-ok' : 'is-open'}>
            <span aria-hidden="true">{c.ok ? '✓' : '!'}</span>
            <span><b>{c.label}</b><span className="evo-muted evo-small" style={{ display: 'block' }}>{c.detail}</span></span>
          </li>
        ))}
      </ul>
      <p className="evo-small" style={{ margin: '10px 0 0' }}>
        {countyUrl ? <a className="evo-link" href={countyUrl} target="_blank" rel="noreferrer">Open the official county record ↗</a> : 'No county record link on file.'}
        <span className="evo-muted"> · Corrections from an official record and manual owner entry are under "What you can do".</span>
      </p>
    </Panel>
  )
}

export function CompsPanel({ eco }) {
  const eng = (eco && eco.arv && eco.arv.engine) || null
  const used = (eng && eng.comps_used) || []
  const excluded = (eng && eng.comps_excluded) || []
  return (
    <Panel title="Comparable sales" id="comps" hint="Closed sales only · an estimate or a list price is never a sale">
      {!used.length && !excluded.length ? (
        <p className="evo-muted" style={{ margin: 0 }}>No sold comps on file yet. Add one below with a source a second person can check.</p>
      ) : (
        <div className="evo-table-wrap"><table className="evo-table">
          <thead><tr><th>Address</th><th className="is-num">Closed price</th><th>Sale date</th><th className="is-num">Miles</th><th className="is-num">$/sq ft</th><th>Origin</th><th>Used?</th></tr></thead>
          <tbody>
            {used.map((c) => (
              <tr key={c.comp_id || c.address}><td>{c.address || '—'}</td><td className="is-num">{money(c.sale_price)}</td>
                <td>{c.sale_date ? shortDate(c.sale_date) : '—'}</td><td className="is-num">{c.distance_miles ?? '—'}</td>
                <td className="is-num">{c.price_per_sqft != null ? money(Math.round(c.price_per_sqft)) : '—'}</td>
                <td><Tag kind={c.manual ? 'estimate' : undefined}>{c.origin || '—'}</Tag></td><td><Tag kind="live">Counted</Tag></td></tr>
            ))}
            {excluded.map((c) => (
              <tr key={(c.comp_id || c.address) + '-x'} className="is-muted"><td>{c.address || '—'}</td><td className="is-num">{money(c.sale_price)}</td>
                <td>{c.sale_date ? shortDate(c.sale_date) : '—'}</td><td className="is-num">{c.distance_miles ?? '—'}</td>
                <td className="is-num">{c.price_per_sqft != null ? money(Math.round(c.price_per_sqft)) : '—'}</td>
                <td><Tag>{c.origin || '—'}</Tag></td>
                <td className="evo-small">Excluded: {(c.excluded || []).map((e) => e.label).join('; ') || 'rule'}</td></tr>
            ))}
          </tbody>
        </table></div>
      )}
      {eco && eco.arv && eco.arv.value != null ? (
        <p className="evo-small" style={{ margin: '10px 0 0' }}>ARV <b>{money(eco.arv.value)}</b> from {eco.arv.comp_count} eligible sale{eco.arv.comp_count === 1 ? '' : 's'} · {eco.arv.confidence} confidence</p>
      ) : <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>No ARV until enough eligible closed sales are on file.</p>}
    </Panel>
  )
}

const MODES = [{ key: 'wholesale', label: 'Wholesale' }, { key: 'investment', label: 'Investment' }, { key: 'both', label: 'Both' }]

/** Wholesale / Investment / Both. Wholesale is the property's own preliminary
 *  numbers (EvoSense economics). Investment is the server underwriting engine
 *  (fix-and-flip and buy-and-hold) - it runs on a deal, so before the property
 *  is in the pipeline this says so and offers the calculator, which uses the
 *  same offer formula. No second set of formulas. */
export function UnderwritingModes({ dealId, d, wholesale }) {
  const [mode, setMode] = useState('wholesale')
  const navigate = useNavigate()
  // The underwriting panel's own shortcuts open the matching tab of the deal.
  const goTo = (tab) => navigate(`/wholesale/deals/${dealId}?tab=${encodeURIComponent(tab)}`)
  const showW = mode === 'wholesale' || mode === 'both'
  const showI = mode === 'investment' || mode === 'both'
  return (
    <div className="acq-uw">
      <div className="acq-uw__bar">
        <Seg items={MODES} value={mode} onChange={setMode} label="Underwriting strategy" />
        <span className="evo-muted evo-small">Same shared formulas as Deal Operations · assumptions are labelled and editable</span>
      </div>
      {showW ? wholesale : null}
      {showI ? (
        dealId ? (
          <div className="acq-uw__inv"><UnderwritingTab dealId={dealId} goTo={goTo} /></div>
        ) : (
          <Panel title="Investment scenarios" hint="Fix and flip · buy and hold">
            <Alert kind="info">The investment scenarios (fix-and-flip, rental cash flow, cap rate, DSCR) run on a deal so every run is
              kept with its inputs. Add this property to the pipeline to run them. Until then, the calculator below shows the
              investor's side using the same offer formula.</Alert>
            <DealCalculator compact prefill={prefillFromProperty(d)} />
          </Panel>
        )
      ) : null}
    </div>
  )
}

/** LAUNCH DEAL — the guided path to investors through the EXISTING deal
 *  package / approval / publication workflow. Nothing here can publish
 *  without the approval gate; nothing is sent from this drawer. */
export function LaunchDealDrawer({ open, onClose, p, onPromote, canPromote, promoteWhy, onChanged }) {
  const dealId = p.promoted_deal_id
  return (
    <Drawer open={open} onClose={onClose} wide title="Launch Deal" sub={p.address}
            footer={<button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Close</button>}>
      <ol className="acq-launch-steps">
        <li className={dealId ? 'is-done' : 'is-now'}><b>1. In the pipeline</b>
          {dealId ? <span> · done. <Link className="evo-link" to={`/wholesale/deals/${dealId}`}>Open the deal</Link></span>
            : <span className="evo-muted"> · a deal must exist before anything can be published.</span>}</li>
        <li className={dealId ? 'is-now' : ''}><b>2. Deal package</b><span className="evo-muted"> · create it, fix what it says is missing, send it for approval.</span></li>
        <li><b>3. Approval</b><span className="evo-muted"> · an approver signs off; nothing goes out before that.</span></li>
        <li><b>4. Publish &amp; share</b><span className="evo-muted"> · publish to your private investor portal, then pick buyer audiences in the Deal Room.</span></li>
        <li><b>5. Offers</b><span className="evo-muted"> · interest and offers arrive in the Deal Room and continue into Contracts &amp; Closing.</span></li>
      </ol>
      {dealId ? (
        <>
          <DealPackagePanel dealId={dealId} onChanged={onChanged} />
          <p className="evo-small" style={{ marginTop: 12, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <Link className="evo-link" to={`/wholesale/deals/${dealId}?tab=sharing`}>Investor page &amp; sharing settings</Link>
            <Link className="evo-link" to={`/wholesale/deals/${dealId}?tab=buyers`}>Buyer matching for this deal</Link>
            <Link className="evo-link" to="/wholesale/deal-room">Deal Room</Link>
          </p>
        </>
      ) : (
        <div style={{ marginTop: 12 }}>
          <button type="button" className="evo-btn evo-btn--primary" disabled={!canPromote} onClick={onPromote}
                  title={canPromote ? undefined : promoteWhy}>Add to pipeline first</button>
          {!canPromote && promoteWhy ? <p className="evo-muted evo-small" style={{ marginTop: 6 }}>{promoteWhy}</p> : null}
        </div>
      )}
    </Drawer>
  )
}
