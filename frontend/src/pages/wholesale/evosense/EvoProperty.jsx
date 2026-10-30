/* EVOSENSE — ONE-PROPERTY ACQUISITION COMMAND (Project OVERDRIVE, Design B,
 * owner approved Oct 29, 2026 — #439).
 *
 * Top of the screen (AcquisitionCommand.jsx): header with address, county,
 * type, parcel, distress score and its explanation, readiness and Launch Deal;
 * the overview (photo, map links, owner, mailing address, transfer history,
 * sources); six key numbers; the seven property actions.
 *
 * Below, the tabs hold every panel this screen already had - nothing removed:
 *   Overview         next action, distress evidence, deal numbers, comps,
 *                    work priority, road to a deal, buyers, contacts, what you can do
 *   Ownership        verification checks, owner of record, conflicts and
 *                    identity reviews, public record, property type
 *   Distress Signals why EvoSense found it - evidence, freshness, limitations
 *   Comps & Value    comparable sales (used and excluded), manual comp, estimates
 *   Underwriting     Wholesale / Investment / Both on the shared formulas
 *   Buyers           buyer matches - match, verification, readiness kept apart
 *   Contacts         contacts, contactability, permission, seller conversation
 *   AI Assistant     recommendation, scores and why, first contact, call
 *                    outcome, seller facts (each labelled AI / rules / person)
 *   Activity         follow-ups, field visits, timeline, nurture, judgment,
 *                    data intelligence (provenance, cost ledger)
 *
 * Every value keeps its truth state. Promotion into Wholesale Operations is a
 * person's decision, confirmed in a drawer; EvoSense never makes an offer, and
 * Launch Deal goes through the existing deal package approval gate.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, getBranding } from '../../../api/client'
import BookAppointment from '../appts/BookAppointment'
import FieldPropertyCard from '../appts/FieldPropertyCard'
import FieldVisitsPanel from '../appts/FieldVisitsPanel'
import { errText } from '../wsShared'
import { canRunHunts } from '../../../auth/workspaceAuthority'
import {
  Alert, Drawer, Empty, EvoApp, Feed, Num, PageSkeleton, Panel,
  Score, ScoreWhy, TabBar, Tag, ago, cents, humanize, money, shortDate, statusLabel,
} from '../ds/ds'
import '../ds/evo-pages.css'
import EvoActions from './EvoActions'
import OpportunityCard from './OpportunityCard'
import PropertyTypePanel from './PropertyTypePanel'
import { CallOutcomePanel, FirstContactPlan } from './Acquire'
import { ReadinessPanel } from './Readiness'
import EvoPermission from './EvoPermission'
import EvoFollowUp from './EvoFollowUp'
import { MatchResults } from '../wsBuyerIntelligence'
import PropertyPhoto from './PropertyPhoto'
import DealCalculator from '../calc/DealCalculator'
import { prefillFromProperty } from '../calc/prefill'
import { ev } from '../engine'
import {
  ACQ_TABS, AcqActions, AcqHeader, AcqKpis, AcqOverview, CompsPanel, HASH_TO_TAB, LaunchDealDrawer, OwnershipVerification,
  UnderwritingModes, tabFrom,
} from './AcquisitionCommand'

/** Free links: Google Maps, Street View, and the county's own record page. */
function PlaceLinks({ links }) {
  if (!links) return null
  const items = [[links.maps_url, 'Open in Google Maps'], [links.street_view_url, 'Street View'],
    [links.county_record_url, links.county_record_label || 'County record'],
    [links.zillow_url, 'Zillow'], [links.redfin_url, 'Redfin']].filter(([u]) => u)
  if (!items.length) return null
  return (
    <p className="evo-small" style={{ margin: '8px 0 0', display: 'flex', flexWrap: 'wrap', gap: '4px 14px' }}>
      {items.map(([u, l]) => <a key={u} className="evo-link" href={u} target="_blank" rel="noreferrer">{l} ↗</a>)}
    </p>
  )
}

/** Every public-record fact on file for this property, each with where it came
 *  from. Unknown stays "not on record" - nothing is filled in by guess. */
function PublicRecord({ p, f, owners, links, propertyId, busy, act }) {
  const phys = f.physical || {}
  const mine = f.entered_by_you || {}
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({})
  const yours = (k, shown) => (mine[k] ? (
    <>{shown} <span className="evo-tag evo-tag--info" title={`Entered by ${mine[k].by_name || 'you'}${mine[k].at ? ` on ${shortDate(mine[k].at)}` : ''}`}>Entered by you</span>
      <span className="evo-muted evo-small" style={{ display: 'block', fontWeight: 400 }}>County record: {mine[k].county_value ?? 'none'}</span></>
  ) : shown)
  function startEdit() {
    setForm({ bedrooms: phys.bedrooms ?? '', bathrooms: phys.bathrooms ?? '', square_feet: phys.square_feet ?? '', year_built: phys.year_built ?? '' })
    setEditing(true)
  }
  async function save() {
    const body = {}
    for (const k of ['bedrooms', 'bathrooms', 'square_feet', 'year_built']) {
      const was = phys[k] ?? ''
      if (String(form[k]) !== String(was)) body[k] = form[k] === '' ? null : Number(form[k])
    }
    if (!Object.keys(body).length) { setEditing(false); return }
    const r = await act(() => api.patch(`/wholesale/evosense/properties/${propertyId}/facts`, body), () => 'Saved. The county record is kept as it was.')
    if (r) setEditing(false)
  }
  async function revert(k) {
    await act(() => api.patch(`/wholesale/evosense/properties/${propertyId}/facts`, { [k]: null }), () => 'Back to the county record.')
  }
  const ap = f.appraisal || null
  const none = <span className="evo-muted">not on record</span>
  const num = (v, suffix = '') => (v === null || v === undefined || v === '' ? none : `${Number(v).toLocaleString()}${suffix}`)
  const age = phys.year_built ? ` (${new Date().getFullYear() - phys.year_built} yrs old)` : ''
  const current = owners.filter((o) => o.current !== false)
  const rows = [
    ['Parcel / account', phys.parcel_apn || p.parcel_apn ? (links && links.county_record_url
      ? <a className="evo-link" href={links.county_record_url} target="_blank" rel="noreferrer">{phys.parcel_apn || p.parcel_apn} ↗</a>
      : (phys.parcel_apn || p.parcel_apn)) : none],
    ['County', p.county ? `${p.county} County, ${p.state || 'TX'}` : none],
    ['City / ZIP', p.city || p.zip_code ? [p.city, p.zip_code].filter(Boolean).join(' ') : none],
    ['Property type', phys.property_type ? humanize(phys.property_type)
      : (recordFacts(p, f, owners).landOnly ? 'Vacant land (building valued at $0)' : none)],
    ['Bedrooms', yours('bedrooms', num(phys.bedrooms))],
    ['Full baths', yours('bathrooms', num(phys.bathrooms))],
    ['Half baths', phys.half_bathrooms != null ? num(phys.half_bathrooms) : none],
    ['Living area', yours('square_feet', num(phys.square_feet, ' sq ft'))],
    ['Year built', yours('year_built', phys.year_built ? `${phys.year_built}${age}` : none)],
    ['Tax value (total)', ap ? `${money(ap.value)} · ${ap.year || ''}` : none],
    ['Land value', ap && ap.land != null ? money(ap.land) : none],
    ['Improvement value', ap && ap.improvements != null ? money(ap.improvements) : none],
    ['Value per sq ft', ap && ap.value && phys.square_feet ? `${money(Math.round(ap.value / phys.square_feet))} (tax value)` : none],
    ['Last deed transfer', f.deed_transfer && f.deed_transfer.date ? `${shortDate(f.deed_transfer.date)} · price not public in Texas` : none],
    ['Years owned', f.ownership_years && f.ownership_years.value != null ? `${f.ownership_years.value} years` : none],
    ['Occupancy', f.occupancy && f.occupancy.value ? humanize(f.occupancy.value) : none],
    ['Owner of record', current.length ? current.map((o) => `${o.name || 'name not on record'}${o.owner_type_label ? ` (${o.owner_type_label.toLowerCase()})` : ''}`).join('; ') : none],
    ['Owner mailing address', current.length && current.some((o) => o.mailing) ? current.map((o) => o.mailing).filter(Boolean).join('; ') : none],
  ]
  return (
    <Panel title="Public record" id="record"
           hint={ap && ap.source ? `From ${ap.source}${ap.at ? ` · read ${shortDate(ap.at)}` : ''}` : 'County appraisal and deed records on file'}>
      <dl className="evo-kv" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: '10px 20px', margin: 0 }}>
        {rows.map(([k, v]) => (
          <div key={k}><dt className="evo-muted evo-small">{k}</dt><dd style={{ margin: 0, fontWeight: 600 }}>{v}</dd></div>
        ))}
      </dl>
      {propertyId && act ? (
        editing ? (
          <div style={{ marginTop: 14, display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end' }}>
            {[['bedrooms', 'Bedrooms', '1'], ['bathrooms', 'Full baths', '0.5'], ['square_feet', 'Living area (sq ft)', '1'], ['year_built', 'Year built', '1']].map(([k, label, step]) => (
              <label key={k} className="evo-field" style={{ maxWidth: 150 }}><span className="evo-field__label">{label}</span>
                <input className="evo-input" type="number" step={step} value={form[k]} disabled={busy}
                       onChange={(e) => setForm((x) => ({ ...x, [k]: e.target.value }))} /></label>
            ))}
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy} onClick={save}>Save</button>
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
            <span className="evo-muted evo-small" style={{ flexBasis: '100%' }}>Your numbers are kept separate. The county record is never changed, and a county refresh will not overwrite what you enter.</span>
          </div>
        ) : (
          <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy} onClick={startEdit}>Edit beds, baths, size, year</button>
            {Object.keys(mine).map((k) => (
              <button key={k} type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => revert(k)}>
                Use county {k === 'square_feet' ? 'living area' : k === 'year_built' ? 'year built' : k}</button>
            ))}
          </div>
        )
      ) : null}
    </Panel>
  )
}

/* VALUE ESTIMATES - each labelled for what it is. Neither is a sale price or an ARV:
 *  - RentCast: a modelled estimate from nearby LISTINGS (asking prices), only
 *    when a person asks or the property scores 65+, inside a hard free budget;
 *  - the county figure: this ZIP's median tax value per sq ft x living area. */
function ValueEstimate({ f, propertyId, busy, act }) {
  const [usage, setUsage] = useState(null)
  useEffect(() => { api.get('/wholesale/evosense/valuation/usage').then(setUsage).catch(() => setUsage(null)) }, [])
  const v = f.value_estimate
  const n = f.neighborhood_tax_estimate
  const money = (x) => (x === null || x === undefined ? '-' : `$${Number(x).toLocaleString()}`)
  return (
    <Panel title="Value estimates" id="value" hint="Estimates only. Texas does not publish sale prices.">
      {n ? (
        <p style={{ margin: '0 0 10px' }}><strong>{money(n.value)}</strong> - county tax-value figure
          <span className="evo-muted evo-small" style={{ display: 'block' }}>{n.truth}</span></p>
      ) : <p className="evo-muted evo-small" style={{ margin: '0 0 10px' }}>County tax-value figure: needs living area and at least 5 nearby houses on file.</p>}
      {v ? (
        <div>
          <p style={{ margin: '0 0 6px' }}><strong>{money(v.estimate)}</strong> ({money(v.low)} - {money(v.high)}) - RentCast estimate
            <span className="evo-muted evo-small" style={{ display: 'block' }}>{v.truth}</span></p>
          {(v.comparables || []).length ? (
            <div className="evo-table-wrap"><table className="evo-table">
              <thead><tr><th>Nearby home</th><th>What it is</th><th className="is-num">Price</th><th className="is-num">Miles</th></tr></thead>
              <tbody>{v.comparables.map((c, i) => (
                <tr key={i}><td className="evo-small">{c.address}</td><td className="evo-small">{c.label}</td>
                  <td className="is-num">{money(c.price)}</td><td className="is-num">{c.distance_miles ?? '-'}</td></tr>))}</tbody>
            </table></div>
          ) : null}
        </div>
      ) : null}
      <div style={{ marginTop: 10, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm"
                disabled={busy || !usage || !usage.configured || usage.left <= 0}
                onClick={() => act(() => api.post(`/wholesale/evosense/properties/${propertyId}/valuation`, {}), () => 'Value estimate added.')}>
          {v ? 'Refresh RentCast estimate' : 'Get RentCast estimate'}</button>
        <span className="evo-muted evo-small">
          {!usage ? '' : !usage.configured ? 'RentCast is not connected (free plan key not added).'
            : `${usage.left} of ${usage.limit} free estimates left this month - it stops before the free limit, so it never bills.`}</span>
      </div>
    </Panel>
  )
}

/** Plain facts that follow directly from the county record (never a guess):
 *  - a land-only parcel: the county values the building at $0 and has no
 *    building on file, so there are no beds, baths or square feet to show;
 *  - an owner who lives somewhere else: the tax bill goes to a different
 *    address than the property. */
function recordFacts(p, f, owners) {
  const phys = (f && f.physical) || {}
  const ap = (f && f.appraisal) || null
  const landOnly = !!(ap && Number(ap.improvements) === 0 && Number(ap.land) > 0
    && !phys.bedrooms && !phys.square_feet && !phys.year_built)
  const current = (owners || []).filter((o) => o.current !== false)
  const owner = current[0] || null
  const mailing = owner && owner.mailing ? String(owner.mailing) : ''
  const norm = (s) => String(s || '').toUpperCase().replace(/[^A-Z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()
  const street = norm(p && p.address).split(' ').slice(0, 2).join(' ')   // house number + first word
  const away = !!(mailing && street && !norm(mailing).includes(street))
  let awayCity = null
  if (away) {
    const parts = mailing.split(',').map((s) => s.trim()).filter(Boolean)
    const st = parts.findIndex((s) => /^[A-Z]{2}$/.test(s) || /^[A-Z]{2}\s+\d{5}/.test(s))
    awayCity = st > 0 ? `${parts[st - 1]}, ${parts[st].slice(0, 2)}` : null
  }
  return { landOnly, land: ap ? ap.land : null, owner, away, awayCity }
}

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

function EnrichmentStatus({ propertyId, refreshKey }) {
  const [st, setSt] = useState(null)
  useEffect(() => { api.get(`/wholesale/evosense/properties/${propertyId}/enrichment-status`).then(setSt).catch(() => setSt(null)) }, [propertyId, refreshKey])
  if (!st) return null
  // `toneKey` lets a themed workspace (OLP) give the same status its own
  // shade on a dark ground; the inline colour is unchanged everywhere else.
  const toneKey = st.status === 'MATCHED' ? 'ok' : st.status === 'PARTIAL' ? 'warn' : st.status === 'NO_VERIFIED_MATCH' || st.status === 'FAILED' ? 'bad' : 'none'
  const tone = st.status === 'MATCHED' ? '#0f7a3d' : st.status === 'PARTIAL' ? '#9a6400' : st.status === 'NO_VERIFIED_MATCH' || st.status === 'FAILED' ? '#b42318' : '#555'
  return (
    <p className="evo-small" style={{ margin: '6px 0 10px' }}>
      Contact lookup: <b className={'evo-enrich-status is-' + toneKey} style={{ color: tone }}>{st.label}</b>
      {st.contacts.length ? <> · {st.contacts.map((c) => `${c.kind} ${c.quality === 'MATCHED' ? '(name matches owner)' : '(probable - confirm)'}`).join(', ')}</> : null}
      {st.last_decision && st.last_decision.reasons && st.last_decision.reasons.length && !st.contacts.length ? <span className="evo-muted"> · {st.last_decision.reasons[0]}</span> : null}
    </p>
  )
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
  // DESIGN B tabs. ?tab= is the deep link; an old in-page #anchor opens the
  // tab that now holds that section.
  const location = useLocation()
  const navigate = useNavigate()
  const [tab, setTab] = useState(() => tabFrom(location.search, location.hash))
  const [explainHead, setExplainHead] = useState(false)
  const [launchOpen, setLaunchOpen] = useState(false)
  useEffect(() => {
    const h = String(location.hash || '').replace(/^#/, '')
    if (HASH_TO_TAB[h]) setTab(HASH_TO_TAB[h])
  }, [location.hash])
  useEffect(() => { setTab(tabFrom(location.search, location.hash)) }, [propertyId])  // eslint-disable-line react-hooks/exhaustive-deps
  function selectTab(key) {
    setTab(key)
    const q = new URLSearchParams(location.search)
    if (key === 'overview') q.delete('tab'); else q.set('tab', key)
    const qs = q.toString()
    navigate({ pathname: location.pathname, search: qs ? `?${qs}` : '' }, { replace: true })
  }
  const [bookOpen, setBookOpen] = useState(false)
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
  // A field specialist or canvasser gets the one-screen field card instead.
  if (getBranding()?.wholesale_team?.field) return <FieldPropertyCard d={d} propertyId={propertyId} />

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
  const rf = recordFacts(p, f, d.owners || [])
  const aiCounts = (d.seller_facts || []).filter((x) => !x.superseded).reduce((acc, x) => {
    const k = x.extracted_by === 'ai' ? 'ai' : x.extracted_by === 'operator' ? 'operator' : 'rules'
    acc[k] += 1
    return acc
  }, { ai: 0, rules: 0, operator: 0 })

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
        <button type="button" className="evo-btn evo-btn--secondary" disabled={busy || !canRunHunts()} onClick={enrich}
                title={canRunHunts() ? undefined : 'Only an admin or a team manager can do this.'}>
          {p.status === 'needs_enrichment' ? 'Approve lookup' : 'Look up contact'}</button>
      ) : null}
      {p.status === 'ready_for_outreach' ? (
        <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={outreach}>Start outreach</button>
      ) : null}
      {canPromote ? (
        <button type="button" className={`evo-btn ${handoffOpen ? 'evo-btn--primary' : 'evo-btn--ghost'}`} disabled={busy}
                onClick={() => setPromoteOpen(true)}>Promote to Deal Operations</button>
      ) : null}
      {!getBranding()?.wholesale_team?.field ? (
        <button type="button" className="evo-btn evo-btn--secondary" onClick={() => setBookOpen(true)}>Book appointment</button>
      ) : null}
      {!getBranding()?.wholesale_team?.field ? (
        <button type="button" className="evo-btn evo-btn--secondary" onClick={() => go('buyers')}
                title="Score every buyer in your network against this property - nothing is sent">Find matching buyers</button>
      ) : null}
    </>
  )
  const bookDrawer = (
    <BookAppointment open={bookOpen} onClose={() => setBookOpen(false)}
                     link={{ evosense_property_id: propertyId, source: 'property' }}
                     address={[`${p.address || ''}${p.unit ? ` #${p.unit}` : ''}`, place, p.zip_code].filter(Boolean).join(', ')}
                     title={`Visit - ${p.address || 'property'}`}
                     onBooked={(a) => setNotice(`Appointment booked: ${a.local_date} at ${a.local_time}${a.assigned_to_name ? ` with ${a.assigned_to_name}` : ''}. It is on the Appointments page.`)} />
  )

  const go = (key, focusId) => {
    selectTab(key)
    if (typeof window !== 'undefined') {
      window.requestAnimationFrame(() => {
        const el = document.getElementById(focusId || 'acq-tabs')
        if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    }
  }
  const enrichWhy = !canEnrich
    ? `A contact lookup is not offered while this property is "${statusLabel(p.status)}"${d.contacts.length ? ' - contacts are already on file (Contacts tab)' : ''}.`
    : !canRunHunts() ? 'Only an admin or a team manager can run a contact lookup.' : null
  const promoteWhy = p.identity_status === 'review' ? 'A possible duplicate record must be resolved first (Ownership tab).' : null
  const actionItems = [
    { key: 'verify-ownership', label: 'Verify Ownership', onClick: () => go('ownership', 'verify') },
    { key: 'analyze-deal', label: 'Analyze Deal', onClick: () => go('underwriting') },
    { key: 'find-buyers', label: 'Find Active Buyers', onClick: () => go('buyers') },
    { key: 'enrich-contacts', label: p.status === 'needs_enrichment' ? 'Approve contact lookup' : 'Enrich Contacts',
      onClick: enrich, disabled: busy || !!enrichWhy, why: enrichWhy, hint: 'Uses the configured lookup budget; nothing is sent to the owner' },
    { key: 'schedule-follow-up', label: 'Schedule Follow-Up', onClick: () => go('activity', 'followup') },
    p.promoted_deal_id
      ? { key: 'add-to-pipeline', label: 'Open in Deal Operations', href: `/wholesale/deals/${p.promoted_deal_id}` }
      : { key: 'add-to-pipeline', label: 'Add to Pipeline', onClick: () => setPromoteOpen(true), disabled: busy || !!promoteWhy, why: promoteWhy },
    { key: 'launch-deal', label: 'Launch Deal', primary: true, onClick: () => setLaunchOpen(true) },
  ]
  const topSignals = d.signals.filter((s) => s.scored !== false).slice(0, 5)
  const tabItems = ACQ_TABS.map((t) => ({ ...t, count: t.key === 'signals' ? d.signals.filter((s) => s.scored !== false).length
    : t.key === 'contacts' ? d.contacts.length : undefined }))

  return (
    <EvoApp world="acquisition">
      <Link className="evo-crumb" to="/wholesale/evosense/inbox">← Discovery Inbox</Link>
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      {/* DESIGN B (owner approved, #439): header, overview, six numbers, actions */}
      <AcqHeader d={d} onLaunch={() => setLaunchOpen(true)} canLaunchUi explain={explainHead} setExplain={setExplainHead} />
      <AcqOverview d={d} recordFacts={rf}
                   photo={<PropertyPhoto propertyId={propertyId} address={p.address} isTest={p.is_test} />}
                   links={<PlaceLinks links={d.links} />} />
      <AcqKpis d={d} />
      <AcqActions items={actionItems} />

      {(d.review_flags || []).length ? (
        <Alert kind="warn">
          <strong>Review before anything else:</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{d.review_flags.map((r) => <li key={r.code}>{r.label}</li>)}</ul>
        </Alert>
      ) : null}

      <div id="acq-tabs" className="acq-tabs">
        <TabBar items={tabItems} value={tab} onChange={selectTab} label="Property sections" />
      </div>
      <div className="acq-tabpanel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0}>
        {tab === 'overview' ? (
          <div className="evo-stack">
            <section className="evo-nextcard" aria-label="Next action">
              <div>
                <span className="evo-hero__label">{handoffOpen ? 'Needs you · next action' : 'Next action'}</span>
                <div className="evo-nextcard__v">{p.next_action || '—'}</div>
                {p.next_action_detail ? <div className="evo-nextcard__d">{p.next_action_detail}</div> : null}
                {handoffOpen && !p.next_action_detail ? <div className="evo-nextcard__d">{d.handoff.reasons.map((r) => r.label).join(' · ')}</div> : null}
              </div>
              <div className="evo-nextcard__actions">{actions}</div>
            </section>
            <div className="acq-fold">
              <Panel title="Verified distress evidence" hint="Source, date and freshness on every signal"
                     action={<button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => go('signals')}>All signals</button>}>
                {!topSignals.length ? <p className="evo-muted" style={{ margin: 0 }}>No distress signals on file.</p> : (
                  <ul className="evo-signal-list">
                    {topSignals.map((s) => (
                      <li key={s.signal_type} className={`evo-signal-item${s.freshness === 'stale' ? ' is-stale' : ''}`}>
                        <span className="evo-signal-item__name">{s.label}</span>
                        <span className="evo-chips">
                          <Tag kind={s.freshness === 'current' ? 'live' : s.freshness === 'stale' ? 'danger' : undefined}>{FRESHNESS[s.freshness] || 'UNKNOWN'}</Tag>
                          {s.derived ? <Tag kind="estimate">derived</Tag> : <Tag>record</Tag>}
                        </span>
                        <span className="evo-signal-item__meta">{(s.evidence || []).slice(0, 1).map((e) => evidenceLine(e)).join('')}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
              <Panel title="Deal numbers" hint="Preliminary · internal only"
                     action={<button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => go('underwriting')}>Wholesale / Investment / Both</button>}>
                <dl className="acq-kv">
                  {['Seller asking', 'ARV', 'Repairs', 'Preliminary MAO', 'Wholesale fee'].map((label) => {
                    const l = line(label)
                    return <FragmentKV key={label} k={label === 'Wholesale fee' ? 'Assignment target' : label}
                                       v={<>{l.value != null ? money(l.value) : (l.display || '—')} <Truth>{l.truth_label}</Truth></>} />
                  })}
                </dl>
                {eco.verdict ? <p className="evo-small" style={{ margin: '8px 0 0' }}>{eco.verdict}</p> : null}
              </Panel>
              <Panel title="Comparable sales" hint="Closed sales only"
                     action={<button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => go('comps')}>Comps & value</button>}>
                <p style={{ margin: 0 }}><b>{((eco.arv && eco.arv.engine && eco.arv.engine.comps_used) || []).length}</b> eligible ·{' '}
                  {((eco.arv && eco.arv.engine && eco.arv.engine.comps_excluded) || []).length} excluded</p>
                <p className="evo-muted evo-small" style={{ margin: '6px 0 0' }}>
                  {eco.arv && eco.arv.value != null ? `ARV ${money(eco.arv.value)} · ${eco.arv.confidence} confidence` : ((f.arv && f.arv.label) || 'Insufficient comparable sales')}</p>
              </Panel>
            </div>
            <OpportunityCard o={d.opportunity} q={d.qualification} />
            <ReadinessPanel propertyId={propertyId} refreshKey={JSON.stringify([p.status, p.contact_confidence, p.seller_intent])} />
            <div className="acq-fold acq-fold--two">
              <Panel title="Buyer matches" hint="Match, verification and readiness kept separate">
                <p className="evo-small" style={{ margin: '0 0 10px' }}>Score every buyer in your network against this property. Nothing is sent.</p>
                <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => go('buyers')}>Find Active Buyers</button>
              </Panel>
              <Panel title="Contact enrichment" hint={`${d.contacts.length} contact${d.contacts.length === 1 ? '' : 's'} on file`}>
                <EnrichmentStatus propertyId={propertyId} refreshKey={busy} />
                <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => go('contacts')}>Open contacts</button>
              </Panel>
            </div>
            <EvoActions d={d} propertyId={propertyId} busy={busy} act={act} onPromote={() => setPromoteOpen(true)} />
          </div>
        ) : null}

        {tab === 'ownership' ? (
          <div className="evo-stack">
            <OwnershipVerification d={d} countyUrl={d.links && d.links.county_record_url} />
            <Panel title="Owner of record" id="owner-record">
                {!d.owners.length ? <p className="evo-muted" style={{ margin: 0 }}>No owner of record.</p> : d.owners.map((o) => (
                  <div key={o.id} style={{ marginBottom: 14 }}>
                    <div className="evo-strong" style={{ fontSize: 16 }}>{o.name}{' '}
                      {o.in_conflict ? <Tag kind="danger">Conflict</Tag> : null}{!o.current ? <Tag>Former</Tag> : null}</div>
                    <div className="evo-muted evo-small">{o.owner_type_label || humanize(o.owner_type)} · <span style={{ color: o.resolution === 'unresolved' ? 'var(--evo-warning-ink)' : undefined }}>{o.resolution_label}</span>
                      {o.properties_owned_here > 1 ? ` · owns ${o.properties_owned_here} properties here` : ''}</div>
                    {(o.review_flags || []).length ? (
                      <div className="evo-chips" style={{ marginTop: 6 }}>
                        {o.review_flags.map((r) => <Tag key={r.code} kind="danger" title={r.label}>{humanize(r.code.toLowerCase())}</Tag>)}
                      </div>
                    ) : null}
                    <dl className="evo-kv" style={{ marginTop: 10, fontSize: 13 }}>
                      <dt>Mailing</dt><dd style={{ textAlign: 'right', whiteSpace: 'normal' }}>{o.mailing || '—'}</dd>
                      <dt>Sources</dt><dd>{o.sources.join(', ') || '—'}</dd>
                      {o.persons.length ? <><dt>People</dt><dd style={{ whiteSpace: 'normal' }}>{o.persons.map((x) => `${x.name} (${humanize(x.role).toLowerCase()})`).join(', ')}</dd></> : null}
                    </dl>
                  </div>
                ))}
            </Panel>
            {(d.conflicts.length || d.identity_reviews.length) ? (
              <Panel title="Conflicts & identity reviews" hint="Kept side by side - a weaker source never overwrites an official one">
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
            ) : null}
            <PublicRecord p={p} f={f} owners={d.owners || []} links={d.links} propertyId={propertyId} busy={busy} act={act} />
            <PropertyTypePanel propertyId={propertyId} canEdit />
          </div>
        ) : null}

        {tab === 'signals' ? (
          <div className="evo-stack">
            <Panel title={ev('Why EvoSense found it')} id="signals" hint="Evidence, not truth · aging counts half, stale counts nothing · dates are the source's own">
              {!d.signals.length ? <p className="evo-muted" style={{ margin: 0 }}>No signals.</p> : (
                <>
                <ul className="evo-signal-list">
                  {d.signals.filter((s) => s.scored !== false).map((s) => (
                    <li key={s.signal_type} className={`evo-signal-item${s.freshness === 'stale' ? ' is-stale' : ''}`}>
                      <span className="evo-signal-item__name">{s.label}{s.value ? <span className="evo-muted" style={{ fontWeight: 400 }}> · {s.value}</span> : null}</span>
                      <span className="evo-chips">
                        <Tag kind={s.freshness === 'current' ? 'live' : s.freshness === 'stale' ? 'danger' : undefined}>{FRESHNESS[s.freshness] || 'UNKNOWN'}</Tag>
                        {s.family && s.family !== s.signal_type ? <Tag kind="info">{humanize(s.family.toLowerCase())}</Tag> : null}
                        {s.derived ? <Tag kind="estimate">derived</Tag> : <Tag>record</Tag>}
                      </span>
                      <span className="evo-signal-item__meta">
                        {s.evidence.map((e) => evidenceLine(e)).join('  |  ')}
                      </span>
                      {(s.evidence.find((e) => e.provenance && e.provenance.limitations) || {}).provenance ? (
                        <span className="evo-signal-item__meta" style={{ fontStyle: 'italic' }}>
                          Limitation: {s.evidence.find((e) => e.provenance && e.provenance.limitations).provenance.limitations}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
                {d.signals.some((s) => s.scored === false) ? (
                  <>
                    <p className="evo-hero__label" style={{ marginTop: 14 }}>Record history &amp; unverified — shown, not scored</p>
                    <ul className="evo-signal-list">
                      {d.signals.filter((s) => s.scored === false).map((s) => (
                        <li key={s.signal_type} className="evo-signal-item is-stale">
                          <span className="evo-signal-item__name">{s.label}</span>
                          <span className="evo-signal-item__meta">
                            {s.evidence.map((e) => `${e.value || ''} · ${evidenceLine(e)}`).join('  |  ')}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
                </>
              )}
            </Panel>
          </div>
        ) : null}

        {tab === 'comps' ? (
          <div className="evo-stack">
            <CompsPanel eco={eco} />
            <Panel title="Add a sold comp">
              <ManualComp propertyId={p.id} arv={eco.arv} busy={busy} act={act} />
            </Panel>
            <ValueEstimate f={f} propertyId={propertyId} busy={busy} act={act} />
          </div>
        ) : null}

        {tab === 'underwriting' ? (
          <UnderwritingModes dealId={p.promoted_deal_id} d={d} wholesale={
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
                <AcquisitionCost c={d.acquisition_cost} />
                <details className="evo-calc-open" style={{ marginTop: 16 }}>
                  <summary className="evo-btn evo-btn--ghost evo-btn--sm" style={{ display: 'inline-flex', cursor: 'pointer' }}>Run the numbers (deal calculator)</summary>
                  <DealCalculator compact prefill={prefillFromProperty(d)} />
                  <p className="evo-muted evo-small" style={{ margin: '8px 0 0' }}>
                    <Link className="evo-link" to={`/wholesale/calculator?property=${p.id}`}>Open full-page calculator</Link></p>
                </details>
              </Panel>
          } />
        ) : null}

        {tab === 'buyers' ? (
          <div className="evo-stack">
            <Panel title="Active buyers for this property" hint="Match quality, verification and purchase readiness are separate answers. Nothing is sent from here."
                   action={<Link className="evo-btn evo-btn--ghost evo-btn--sm" to="/wholesale/buyers">Buyer Intelligence</Link>}>
              <MatchResults subjectType="evosense" subjectId={propertyId} compact
                            onOpen={(id) => window.location.assign(`/wholesale/buyers?buyer=${id}`)} />
              <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>
                A matched investor is not automatically a verified cash buyer, and a deed is not proof of a cash purchase.</p>
            </Panel>
          </div>
        ) : null}

        {tab === 'contacts' ? (
          <div className="evo-stack">
            <Panel title="Contacts" id="owner">
              <EnrichmentStatus propertyId={propertyId} refreshKey={busy} />
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
              {d.contactability ? (
                <div style={{ marginTop: 12 }} className="evo-small">
                  <span>Contactability: </span>
                  <strong style={{ color: String(d.contactability.state).startsWith('CONTACTABLE') ? undefined : 'var(--evo-warning-ink)' }}>
                    {d.contactability.label}</strong>
                  {(d.contactability.reasons || []).map((r, i) => <div key={i} className="evo-muted">{r}</div>)}
                  <div className="evo-muted" style={{ marginTop: 4 }}>Having a number is not permission to use it; this says which channel, if any, is permitted and working.</div>
                </div>
              ) : null}
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
            <EvoPermission propertyId={propertyId} permission={d.permission}
                           ownerName={(d.owners.find((o) => o.current) || d.owners[0] || {}).name}
                           defaultPhone={(d.contacts.find((c) => c.kind === 'phone') || {}).value}
                           onChange={load} />
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
                            <span>{m.direction === 'inbound' ? 'Seller said' : ev('EvoSense sent')}</span>
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
                              <Tag kind="danger">{(m.pending.startsWith('EvoSense is paused') || m.pending.startsWith('Acquisition is paused')) ? 'Held while paused' : 'AI review pending'}</Tag>
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
          </div>
        ) : null}

        {tab === 'ai' ? (
          <div className="evo-stack">
            <Panel title="AI Assistant" hint="What the assistant read and recommends - every answer shows whether AI or the rules reader produced it">
              <p style={{ margin: 0 }}><b>Recommended next action:</b> {p.next_action || '—'}</p>
              {p.next_action_detail ? <p className="evo-muted evo-small" style={{ margin: '4px 0 0' }}>{p.next_action_detail}</p> : null}
              <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>
                Seller replies are read by the rules reader first; the platform AI reader (through the AI gateway and its spending
                limits) is consulted only when needed, and a reply it cannot read is held for a person. Facts on file:{' '}
                {aiCounts.ai} read by AI, {aiCounts.rules} by rules, {aiCounts.operator} entered by a person.
                The assistant never sends a message or makes an offer.</p>
            </Panel>
            <Panel title={ev('EvoSense intelligence')} id="intel" hint="Select a score to see exactly why">
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
            <FirstContactPlan propertyId={propertyId} />
            <CallOutcomePanel propertyId={propertyId} />
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
          </div>
        ) : null}

        {tab === 'activity' ? (
          <div className="evo-stack">
            <div id="followup"><EvoFollowUp propertyId={propertyId} items={d.follow_ups} onChange={load} /></div>
            <FieldVisitsPanel propertyId={propertyId} refreshKey={notice} />
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
            <Panel title="Your judgment" hint={ev('Recorded for review · EvoSense does not retrain itself')}>
              <div className="evo-chips" style={{ gap: 8 }}>
                {FEEDBACK.map(([k, label]) => (
                  <button key={k} type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy}
                          onClick={() => act(() => api.post(`/wholesale/evosense/properties/${propertyId}/feedback`, { kind: k }), () => `Recorded: ${label}.`)}>{label}</button>
                ))}
              </div>
              {d.feedback.length ? <p className="evo-muted evo-small" style={{ margin: '10px 0 0' }}>Last: {humanize(d.feedback[0].kind)} · {ago(d.feedback[0].at)}</p> : null}
            </Panel>
            <Panel title="Data intelligence" id="data" action={<span className="evo-money">{cents(d.spent_cents)}</span>}>
              <dl className="evo-kv" style={{ fontSize: 13 }}>
                <dt>Found by</dt><dd>{d.attribution.first_strategy || '—'}</dd>
                <dt>Sources</dt><dd>{d.attribution.sources.length}</dd>
                <dt>Tax value year</dt><dd>{f.appraisal && f.appraisal.year ? f.appraisal.year : '—'}</dd>
                <dt>City known from</dt><dd style={{ whiteSpace: 'normal', textAlign: 'right' }}>{f.situs_city_basis || '—'}</dd>
                <dt>Rules</dt><dd>{d.derivation_version || 'before derive/v3'}</dd>
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
            </Panel>
          </div>
        ) : null}
      </div>

      {bookDrawer}
      <LaunchDealDrawer open={launchOpen} onClose={() => setLaunchOpen(false)} p={p}
                        canPromote={canPromote} promoteWhy={promoteWhy || (p.promoted_deal_id ? null : 'This property cannot be added right now.')}
                        onPromote={() => { setLaunchOpen(false); setPromoteOpen(true) }} onChanged={load} />
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
        <p style={{ marginTop: 0 }}>This creates a Wholesale deal for this property and carries its {ev('EvoSense')} history with it:
          owner, contacts, signals, the seller's words and the preliminary numbers.</p>
        <ul style={{ color: 'var(--evo-text-secondary)', paddingLeft: 18, lineHeight: 1.7 }}>
          <li>Nothing is sent to the seller.</li>
          <li>No offer is made — offers still go through Deal Operations' approval gates.</li>
          <li>{ev('EvoSense stops working this owner; the deal team takes over.')}</li>
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

/** One piece of evidence, with the SOURCE's date (a plain calendar date, never
 * shifted by the viewer's time zone) and what that date is. */
/** What finding and qualifying this opportunity has ACTUALLY cost - charged
 *  cost-ledger entries only. Refunds, reservations and projections are never
 *  counted; a milestone not reached shows no figure. */
function AcquisitionCost({ c }) {
  if (!c) return null
  const m = (v, none) => (v === null || v === undefined ? none : cents(v))
  const b = c.buckets || {}
  return (
    <div style={{ marginTop: 14 }}>
      <h3 className="evo-panel__title" style={{ fontSize: 13, margin: '0 0 6px' }}>Acquisition cost (ledger)</h3>
      <div className="evo-econ">
        {[['Total spent', m(c.total_cents, cents(0))],
          ['Cost to find', m(c.cost_to_find_cents, cents(0))],
          ['Cost to contactable', m(c.cost_to_contactability_cents, 'not reached')],
          ['Cost to qualified', m(c.cost_to_qualification_cents, 'not reached')]].map(([k, v]) => (
          <div key={k} className="evo-econ__cell">
            <div className="evo-econ__k">{k}</div>
            <div className="evo-econ__v">{v}</div>
          </div>
        ))}
      </div>
      <p className="evo-muted evo-small" style={{ margin: '8px 0 0' }}>
        Public data {cents(b.public_data || 0)} · contact enrichment {cents(b.contact_enrichment || 0)} · valuation / comps {cents(b.valuation_comps || 0)}
        {b.other ? ` · other ${cents(b.other)}` : ''} · {c.charged_lookups || 0} charged, {c.refunded_lookups || 0} refunded, {c.free_public_lookups || 0} free public lookups
      </p>
      <p className="evo-muted evo-small" style={{ margin: '4px 0 0' }}>{c.note}</p>
    </div>
  )
}

/** A MANUAL sold comp: address, closed price, sale date and a source
 *  reference a second person could check. Labelled MANUAL for good. */
const COMP_FIELDS = [['street_address', 'Address'], ['sale_price', 'Closed price'], ['sale_date', 'Sale date (YYYY-MM-DD)'],
  ['source_reference', 'Source (MLS #, deed, URL)'], ['square_feet', 'Sq ft'], ['bedrooms', 'Beds'], ['bathrooms', 'Baths'],
  ['distance_miles', 'Distance (mi)'], ['sale_type', 'Sale type']]
const COMP_NUM = new Set(['sale_price', 'square_feet', 'bedrooms', 'bathrooms', 'distance_miles'])

function ManualComp({ propertyId, arv, busy, act }) {
  const [open, setOpen] = useState(false)
  const [c, setC] = useState({})
  const eng = (arv && arv.engine) || null
  const counted = eng ? eng.comps_used.length : 0
  const excluded = eng ? eng.comps_excluded : []
  return (
    <div style={{ marginTop: 14 }}>
      <p className="evo-muted evo-small" style={{ margin: 0 }}>
        Sold comps on file: {counted} eligible{excluded.length ? `, ${excluded.length} excluded` : ''}.
        {excluded.map((x, i) => <span key={i} style={{ display: 'block' }}>{x.address}: {x.excluded.map((e) => e.label).join('; ')}</span>)}
      </p>
      {!open ? (
        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" style={{ marginTop: 8 }}
                onClick={() => setOpen(true)}>Add a sold comp (manual)</button>
      ) : (
        <div className="evo-form-grid" style={{ marginTop: 8, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 8 }}>
          {COMP_FIELDS.map(([k, label]) => (
            <label key={k} className="evo-small">{label}
              <input className="evo-input" value={c[k] || ''} onChange={(e) => setC({ ...c, [k]: e.target.value })} />
            </label>
          ))}
          <div style={{ gridColumn: '1 / -1' }} className="evo-chips">
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm"
                    disabled={busy || !c.street_address || !c.sale_price || !c.sale_date || !(c.source_reference || '').trim()}
                    onClick={async () => {
                      const body = {}
                      Object.entries(c).forEach(([k, v]) => { if (v !== '' && v != null) body[k] = COMP_NUM.has(k) ? Number(v) : v })
                      const r = await act(() => api.post(`/wholesale/evosense/properties/${propertyId}/comps`, body),
                        () => 'Manual comp saved - labelled MANUAL.')
                      if (r) { setC({}); setOpen(false) }
                    }}>Save comp</button>
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}

function evidenceLine(e) {
  const date = e.evidence_date ? shortDate(e.evidence_date) : (e.observed_at ? `read ${shortDate(e.observed_at)}` : null)
  return [
    e.source === 'evosense_derived' ? 'computed from facts' : e.source,
    e.connector ? humanize(e.connector) : null,
    date, e.evidence_basis || null,
    e.case_status ? `status: ${e.case_status}` : null,
    e.confidence ? `${e.confidence}% confidence` : null,
    e.provenance && e.provenance.rule ? e.provenance.rule : null,
    e.rule_version || (e.provenance && e.provenance.rule_version) || null,
  ].filter(Boolean).join(' · ')
}

function k2name(k) {
  return { opportunity: 'Investment evidence score', contact: 'Contact Confidence', intent: 'Seller Intent' }[k]
}

function FragmentKV({ k, v }) {
  return <><dt>{k}</dt><dd>{v}</dd></>
}

