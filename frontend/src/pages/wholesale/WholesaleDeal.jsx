/* The deal room — one property, one page, every section.
 *
 * Built from a single GET /wholesale/deals/{id}, which returns the property, the
 * seller, the conversation, the qualification, the analysis, the comps, the
 * offers and approvals, the documents, the buyer matches and their reasons, the
 * buyer outreach, the assignment, title, closing and the audit history.
 *
 * The rule the screen enforces on itself: EVERY FIGURE CARRIES ITS PROVENANCE
 * and every refusal carries its reason. A number with no label and a button that
 * fails with no explanation are the two ways this kind of tool loses somebody's
 * trust.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './wholesale.css'
import {
  Band, Empty, ErrorBox, Factors, Score, Sourced, Steps, Warnings,
  errText, fmtBool, fmtDate, fmtLabel, fmtMoney, fmtNum, fmtWhen, Note, Reads, Why,
} from './wsShared'
import { AuthImage } from './wsFiles'
import { PropertyWorkspace } from './wsProperty'
import { CompsWorkspace } from './wsComps'
import { NegotiationLedger } from './wsOffers'
import { BuyerBoard } from './wsBuyerBoard'
import { ClosingWorkspace } from './wsClosing'
import { SellerTimeline } from './wsTimeline'
import { DocumentDrawer } from './wsDocuments'
import { SharingWorkspace } from './wsSharing'
import { EvoApp, Hero, PageSkeleton, PropertyThumb, Ring, Status, Tag, humanize, money, shortDate } from './ds/ds'
import './ds/evo-pages.css'

const TABS = [
  ['overview', 'Overview'],
  ['seller', 'Seller & conversation'],
  ['analysis', 'Analysis & comps'],
  ['offer', 'Offer & approvals'],
  ['documents', 'Contracts & documents'],
  ['buyers', 'Buyer matching'],
  ['closing', 'Assignment & closing'],
  ['sharing', 'Sharing'],
  ['audit', 'Audit history'],
]

function KeyNum({ k, v, note, pos }) {
  const has = v !== null && v !== undefined && v !== ''
  return <><dt>{k}{note ? <span className="evo-muted evo-small"> · {note}</span> : null}</dt>
    <dd className={!has ? 'is-quiet' : pos ? 'is-pos' : ''}>{has ? money(v) : 'not yet'}</dd></>
}

export default function WholesaleDeal() {
  const { dealId } = useParams()
  const [room, setRoom] = useState(null)
  const [tab, setTab] = useState('overview')
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      setRoom(await api.get(`/wholesale/deals/${dealId}`))
    } catch (e) {
      setError(errText(e))
    }
  }, [dealId])

  useEffect(() => { load() }, [load])

  async function act(fn, successMessage) {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      if (successMessage) setNotice(successMessage)
      await load()
      return true
    } catch (e) {
      setError(errText(e))
      // Reported, not thrown. The caller uses the answer to decide whether to
      // close its editor: a form that closes on a failed save discards what
      // the person typed and leaves an error about a form they cannot see.
      return false
    } finally {
      setBusy(false)
    }
  }

  if (error && !room) return <EvoApp world="operations"><ErrorBox error={error} /></EvoApp>
  if (!room) return <EvoApp world="operations"><PageSkeleton /></EvoApp>

  const { deal, property, seller, analysis, stages } = room
  const cover = (room.photos || []).find((p) => p.is_primary)
                || (room.photos || [])[0]
  const nextAction = room.next_action || {}

  return (
    <EvoApp world="operations">
      <Link className="evo-crumb" to="/wholesale">← Deal Operations</Link>
      {/* THE PROPERTY HERO (board screen 6). The background is the deal's own
          uploaded photo when there is one — that IS this property. Without one
          it is a parcel-map illustration, which can never be mistaken for it. */}
      <Hero
        art={cover ? <AuthImage path={cover.url} alt="" /> : null}
        scene="parcel"
        eyebrow={`Wholesale Operations · ${deal.stage_label || 'Deal'}`}
        title={property?.street_address || property?.address || '(no address)'}
        sub={[[property?.city, property?.state].filter(Boolean).join(', '), property?.zip_code].filter(Boolean).join(' ') || null}
        meta={[
          property?.property_type ? { label: humanize(property.property_type) } : null,
          property?.bedrooms ? { label: `${property.bedrooms} beds` } : null,
          property?.bathrooms ? { label: `${property.bathrooms} baths` } : null,
          property?.square_feet ? { label: `${Number(property.square_feet).toLocaleString()} sq ft` } : null,
          deal.is_test ? { label: 'Sandbox record', tone: 'paused' } : null,
          seller?.is_dnc ? { label: 'Seller: do not contact', tone: 'paused' } : null,
          { label: `Moved ${fmtWhen(deal.stage_changed_at)}` },
        ]}
        score={seller?.qualification_score != null
          ? <><Ring kind="contact" size="lg" value={Math.round(seller.qualification_score)} title="Seller qualification" /><span>Seller qualification</span></>
          : null}
        actions={
          <span className="evo-herostage">
            <label htmlFor="ws-stage-select">Stage</label>
            <select id="ws-stage-select" value={deal.stage} disabled={busy}
                    onChange={(e) => act(
                      () => api.post(`/wholesale/deals/${dealId}/stage`, { stage: e.target.value }),
                      'Stage updated.')}>
              {stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </span>
        }
      />

      <section className="evo-keynums" aria-label="Key numbers">
        <div className="evo-keynums__media">
          {cover
            ? <span className="evo-thumb evo-thumb--hero"><AuthImage path={cover.url} alt={property?.address || 'Property'} /></span>
            : <PropertyThumb address={property?.address} size="hero" label="No photo on file" />}
        </div>
        <div className="evo-keynums__body">
          <h2 className="evo-keynums__title">Key numbers</h2>
          <dl>
            <KeyNum k="Estimated value" v={property?.estimated_value} note={property?.estimated_value_source} />
            <KeyNum k="ARV" v={deal.arv} note={deal.arv_source} />
            <KeyNum k="Max offer (MAO)" v={deal.max_allowable_offer} />
            <KeyNum k="Contract price" v={deal.contract_price} />
            <KeyNum k="Buyer price" v={deal.buyer_price} />
            <KeyNum k="Assignment fee" v={deal.assignment_fee} pos />
            <dt>Target close</dt><dd className={deal.closing_date ? '' : 'is-quiet'}>{deal.closing_date ? shortDate(deal.closing_date) : 'not set'}</dd>
          </dl>
          <div className="evo-chips" style={{ marginTop: 'auto', paddingTop: 8 }}>
            <Status status={deal.stage} label={deal.stage_label} />
            {seller?.qualification_band ? <Band band={seller.qualification_band} /> : null}
          </div>
        </div>
      </section>

      <ErrorBox error={error} />
      {notice ? <div className="ws-good">{notice}</div> : null}

      {/* ── THE DEAL SUMMARY HEADER ───────────────────────────────────────
          Fifteen questions, no clicks: what is it worth, what can we pay,
          what did we offer, is it under contract, who are the buyers, what
          is title waiting on, when does it close, what do we make, and what
          happens next. Everything here is already in the deal room payload.

          A figure that does not exist yet shows an em dash. It is never
          filled with a plausible number, because a header that guesses is a
          header nobody can price a deal from. */}
      <DealSummary room={room} nextAction={nextAction} goTo={setTab} />

      <div className="ws-tabs">
        {TABS.map(([key, label]) => (
          <button key={key} className={`ws-tab ${tab === key ? 'is-active' : ''}`}
                  onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {tab === 'overview' ? <Overview room={room} goTo={setTab} reload={load} /> : null}
      {tab === 'seller' ? <SellerTab room={room} act={act} busy={busy} /> : null}
      {tab === 'analysis' ? <AnalysisTab room={room} act={act} busy={busy} /> : null}
      {tab === 'offer' ? <OfferTab room={room} act={act} busy={busy} /> : null}
      {tab === 'documents' ? <DocumentsTab room={room} act={act} busy={busy} /> : null}
      {tab === 'buyers' ? <BuyersTab room={room} act={act} busy={busy} /> : null}
      {tab === 'closing' ? <ClosingTab room={room} act={act} busy={busy} /> : null}
      {tab === 'sharing'
        ? <SharingWorkspace deal={deal} buyers={room.buyer_matches}
                            act={act} busy={busy} /> : null}
      {tab === 'audit' ? <AuditTab room={room} /> : null}
    </EvoApp>
  )
}


function KV({ label, children }) {
  return (
    <div>
      <div className="ws-k">{label}</div>
      <div className="ws-v">{children ?? <span className="ws-muted">—</span>}</div>
    </div>
  )
}


/* SMS CONSENT OF RECORD for this deal's seller - read from the server's
 * consent records, never inferred from the seller having a phone number. */
function SmsConsentKVs({ dealId }) {
  const [c, setC] = useState(null)
  useEffect(() => {
    if (!dealId) return undefined
    let live = true
    api.get(`/wholesale/sms/consents?deal_id=${encodeURIComponent(dealId)}`)
      .then((r) => { if (live) setC(r) }).catch(() => { if (live) setC({ error: true }) })
    return () => { live = false }
  }, [dealId])
  if (!c || c.error) return <KV label="SMS consent">{c?.error ? 'Unavailable' : null}</KV>
  const latest = (c.consents || [])[0]
  const label = c.status === 'opted_in' ? 'YES' : c.status === 'opted_out' ? 'Opted out' : 'NO'
  return (
    <>
      <KV label="SMS consent">
        <span className={`ws-pill ${c.status === 'opted_in' ? 'is-ok' : 'is-muted'}`}>{label}</span>
      </KV>
      <KV label="Consent given">{latest ? shortDate(latest.consented_at) : null}</KV>
      <KV label="Consent source">{latest ? (latest.source_url || latest.form_id) : null}</KV>
      {latest?.opted_out_at ? <KV label="Opted out">{shortDate(latest.opted_out_at)}{latest.opt_out_keyword ? ` (${latest.opt_out_keyword})` : ''}</KV> : null}
    </>
  )
}


/* WHAT IS WAITING ON A PERSON, read off this deal's own record.
 *
 * Every line below is a fact already on this screen somewhere — a pending
 * approval row, an opted-out flag, an empty ARV, a match with no outreach.
 * Nothing here predicts, scores or recommends: it does not know what the right
 * next move is, only what is unfinished, and it says so in the order a deal
 * actually stalls. When nothing is outstanding it says that rather than
 * inventing an errand. */
function openItems(room) {
  const { deal, seller, analysis, approvals, cadence, buyer_matches, buyer_outreach } = room
  const items = []
  const add = (text, tab, tone) => items.push({ text, tab, tone })

  if (deal.stage === 'closed') return { terminal: 'This deal is closed.', items }
  if (deal.stage === 'dead') return { terminal: 'This deal is marked dead.', items }

  const pending = (approvals || []).filter((a) => a.status === 'pending')
  if (pending.length) {
    add(`${pending.length} approval${pending.length === 1 ? '' : 's'} waiting on a person.`,
        'offer', 'warn')
  }

  if (!seller) {
    add('No owner on this property yet — nothing can be sent until there is one.',
        'seller', 'warn')
  } else {
    if (seller.is_dnc) {
      add('The owner has opted out. Nothing further will be sent to them.',
          'seller', 'warn')
    } else if (!seller.phone && !seller.email) {
      add('No phone or email on file for the owner.', 'seller', 'warn')
    } else if (cadence && cadence.state === 'not_started' && cadence.can_start) {
      add('Outreach has not started for this owner.', 'seller')
    }
    if (seller.needs_human) {
      add(seller.needs_human_reason
        ? `Flagged for a person: ${seller.needs_human_reason}`
        : 'The reply reader flagged this conversation for a person.', 'seller', 'warn')
    }
  }

  if (analysis && (analysis.arv === null || analysis.arv === undefined)) {
    add('No ARV on file, so there is no offer to work from.', 'analysis')
  }

  const matched = (buyer_matches || []).length
  const contacted = (buyer_outreach || []).filter((o) => o.status === 'sent').length
  if (matched && !contacted) {
    add(`${matched} matched buyer${matched === 1 ? '' : 's'}, none contacted yet.`, 'buyers')
  }

  return { terminal: null, items }
}

function NextUp({ room, goTo }) {
  const { terminal, items } = openItems(room)
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">What is waiting</div>
      {terminal ? <p className="ws-panel-note">{terminal}</p> : null}
      {!terminal && !items.length ? (
        <p className="ws-panel-note">Nothing on this deal is waiting on a person.</p>
      ) : null}
      {items.length ? (
        <ul className="ws-nextup">
          {items.map((it, i) => (
            <li key={i} className={it.tone === 'warn' ? 'is-warn' : ''}>
              <span>{it.text}</span>
              <button className="btn btn--secondary btn--sm"
                      onClick={() => goTo(it.tab)}>Open</button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}


function Overview({ room, goTo, reload }) {
  const { property: p, seller, analysis, deal } = room
  return (
    <>
      <NextUp room={room} goTo={goTo} />

      {/* The property is now EDITABLE here rather than read-only. A record
          that cannot be corrected in the product is one that goes stale the
          first time a skip trace returns a wrong year built. */}
      <PropertyWorkspace property={p} photos={room.photos || []}
                         capability={room.file_storage}
                         onSaved={reload} onPhotosChanged={reload} />

      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Numbers at a glance</div>
        <div className="ws-kv">
          <KV label="ARV"><Sourced value={analysis.arv} source={analysis.arv_source} /></KV>
          <KV label="Repairs">
            <Sourced value={analysis.repair_estimate}
                     source={analysis.repair_estimate_source} />
          </KV>
          <KV label="Max allowable offer">{fmtMoney(analysis.max_allowable_offer)}</KV>
          <KV label="Proposed offer">{fmtMoney(analysis.proposed_offer)}</KV>
          <KV label="Contract price">{fmtMoney(analysis.contract_price)}</KV>
          <KV label="Buyer price">{fmtMoney(analysis.buyer_price)}</KV>
          <KV label="Estimated spread">{fmtMoney(analysis.estimated_spread)}</KV>
          <KV label="Fee collected">{fmtMoney(analysis.wholesale_fee_collected)}</KV>
        </div>
        <Warnings items={analysis.warnings} />
      </div>

      {seller ? (
        <div className="panel ws-panel">
          <div className="panel-title ws-panel-title">Seller</div>
          <div className="ws-kv">
            <KV label="Name">{[seller.first_name, seller.last_name].filter(Boolean).join(' ')}</KV>
            <KV label="Phone">{seller.phone}</KV>
            <KV label="Email">{seller.email}</KV>
            <KV label="Qualification"><Band band={seller.qualification_band} /></KV>
            <KV label="Score">{seller.qualification_score}</KV>
            <KV label="Completeness">
              {seller.completeness === null ? null : seller.completeness + '%'}
            </KV>
            <KV label="Prefers">{seller.preferred_contact_method ? humanize(seller.preferred_contact_method) : null}</KV>
            <KV label="Came in via">
              {p?.acquisition_source === 'seller_inquiry' ? 'Seller inquiry form' : humanize(p?.acquisition_source || '')}
            </KV>
            <SmsConsentKVs dealId={deal?.id} />
          </div>
        </div>
      ) : (
        <div className="panel ws-panel">
          <div className="panel-title ws-panel-title">Seller</div>
          <Empty>
            No owner attached yet. Add one on the Seller tab, or run enrichment from
            the Properties screen.
          </Empty>
        </div>
      )}
    </>
  )
}


function SellerTab({ room, act, busy }) {
  const { seller, deal, property } = room
  const [message, setMessage] = useState('')
  const [compose, setCompose] = useState('send')
  const [form, setForm] = useState({ first_name: '', last_name: '', phone: '', email: '' })

  if (!seller) {
    return (
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Attach the owner</div>
        <Note>
          The owner is created as a contact in this workspace.
        </Note>
        <Why label="Why the owner is a workspace contact">
          <p className="ws-comp__sub">
            It is what gives this deal do-not-contact handling, a consent record
            and message history — without keeping a second copy of any of them.
          </p>
        </Why>
        <div className="ws-grid">
          {[['first_name', 'First name'], ['last_name', 'Last name'],
            ['phone', 'Phone'], ['email', 'Email']].map(([key, label]) => (
            <div className="ws-field" key={key}>
              <label htmlFor={`sel-${key}`}>{label}</label>
              <input id={`sel-${key}`} value={form[key]}
                     onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
            </div>
          ))}
        </div>
        <div className="ws-actions" style={{ marginTop: 12 }}>
          <button className="btn btn--primary" disabled={busy}
                  onClick={() => act(
                    () => api.post(`/wholesale/properties/${property.id}/seller`, form),
                    'Owner attached.')}>
            Attach owner
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="ws-seller">
      <div className="ws-seller__main">
        {/* 1. WHO THIS IS. The qualification and the answers it was scored
            from, in one panel: they were two, and reading the score without
            the answers beside it meant scrolling past the whole thing. */}
        <div className="panel ws-panel">
          <div className="panel-title ws-panel-title">
            <span>The owner, and how they qualified</span>
            <Band band={seller.qualification_band} />
          </div>
          <div className="ws-kv">
            <KV label="Score">{seller.qualification_score}</KV>
            <KV label="Completeness">
              {seller.completeness === null ? null : seller.completeness + '%'}
            </KV>
            <KV label="Reader intent">{fmtLabel(seller.ai_intent, null)}</KV>
            <KV label="Last read">{fmtWhen(seller.ai_last_run_at)}</KV>
          </div>
          {seller.needs_human ? (
            <div className="ws-warn" style={{ marginTop: 10 }}>
              Flagged for a person: {seller.needs_human_reason}
            </div>
          ) : null}
          {seller.ai_summary
            ? <Note>{seller.ai_summary}</Note> : null}
          {seller.qualification_reasons && seller.qualification_reasons.length ? (
            <Why label={`How the score of ${seller.qualification_score ?? '—'} was reached`}>
              <ul className="ws-factors">
                {seller.qualification_reasons.map((r, i) => (
                  <li key={i}>
                    <span className="ws-f-mark ws-f-na">·</span><span>{r}</span>
                  </li>
                ))}
              </ul>
            </Why>
          ) : null}

          <div className="ws-subhead">What the owner has told us</div>
          <div className="ws-kv">
            {/* These were `String(true)` — the literal word `true`, eight times
                across this tab, on a screen an acquisitions person reads all
                day. */}
            <KV label="Considering selling">
              {fmtBool(seller.considering_selling, 'not stated')}
            </KV>
            <KV label="Property available">
              {fmtBool(seller.is_available, 'not stated')}
            </KV>
            <KV label="Asking price">
              <Sourced value={seller.asking_price} source={seller.asking_price_source} />
            </KV>
            <KV label="Timeline">{fmtLabel(seller.timeline, null)}</KV>
            <KV label="Condition">{fmtLabel(seller.property_condition, null)}</KV>
            <KV label="Major repairs">{seller.major_repairs}</KV>
            <KV label="Occupancy">{fmtLabel(seller.occupancy, null)}</KV>
            <KV label="Motivation">{seller.motivation}</KV>
            <KV label="Reason for selling">{fmtLabel(seller.reason_for_selling, null)}</KV>
            <KV label="Mortgage / liens">{seller.mortgage_note}</KV>
            <KV label="Decision makers">{seller.decision_makers}</KV>
            <KV label="Best callback time">{seller.best_callback_time}</KV>
          </div>
        </div>

        {/* 2. THE COMPOSER. Two boxes that looked identical and did opposite
            things — one sends, one records something that already happened —
            are now one control that makes you choose which. */}
        <div className="panel ws-panel">
          <div className="panel-title ws-panel-title">
            <span>Message the owner</span>
            <div className="ws-seg" role="group"
                 aria-label="What this box does">
              <button type="button" aria-pressed={compose === 'send'}
                      className={`ws-seg__btn ${compose === 'send' ? 'is-on' : ''}`}
                      onClick={() => setCompose('send')}>Send a message</button>
              <button type="button" aria-pressed={compose === 'record'}
                      className={`ws-seg__btn ${compose === 'record' ? 'is-on' : ''}`}
                      onClick={() => setCompose('record')}>Record what they said</button>
            </div>
          </div>

          {compose === 'send' ? (
            <>
              <Note>
                Sent through this workspace's own messaging service, so the
                suppression list, the consent record and the delivery receipt
                all apply.
              </Note>
              <OutreachForm dealId={deal.id} seller={seller} act={act} busy={busy} />
              <Why label="What happens to a message that cannot be sent">
                <p className="ws-comp__sub">
                  A sandbox record and a contact who has opted out are both
                  refused before anything leaves, and the refusal names the
                  reason rather than failing quietly. Nothing on this screen can
                  send around that check.
                </p>
              </Why>
            </>
          ) : (
            <>
              <Note tone="warn">
                This sends nothing. Type what the owner actually said — on the
                phone, in a text, in an email — and it is read into the answers
                above and the deal re-qualified.
              </Note>
              <label className="ws-vis-hidden" htmlFor="sel-record">
                What the owner said
              </label>
              <textarea id="sel-record" rows={3} value={message}
                        onChange={(e) => setMessage(e.target.value)}
                        placeholder="What did they say?"
                        className="ws-input" />
              <div className="ws-actions" style={{ marginTop: 10 }}>
                <button className="btn btn--primary" disabled={busy || !message.trim()}
                        onClick={() => act(
                          async () => {
                            await api.post(`/wholesale/deals/${deal.id}/seller-reply`,
                                           { message, mode: 'manual' })
                            setMessage('')
                          }, 'Reply read and qualification updated.')}>
                  Read and qualify
                </button>
                <span className="ws-pill is-muted">Manual entry</span>
              </div>
              <Why label="What reads it, and what happens if AI is unavailable">
                <p className="ws-comp__sub">
                  This module has no inbox — nothing here receives a text on its
                  own. If AI is unavailable a pattern reader runs instead, and
                  the result is marked for review rather than scored
                  optimistically.
                </p>
              </Why>
            </>
          )}
        </div>

        {/* 3. THE THREAD. One conversation, in the order things happened. */}
        <SellerTimeline communications={room.communications} />
      </div>

      <div className="ws-seller__side">
        {/* 4. THE CADENCE and 5. THE PERMISSIONS. Both are settings for the
            conversation rather than part of it, so they sit beside it. */}
        <CadencePanel dealId={deal.id} cadence={room.cadence} act={act} busy={busy} />

        <div className="panel ws-panel">
          <div className="panel-title ws-panel-title">Contact permissions</div>
          <div className="ws-kv ws-kv--tight">
            <KV label="Status">
              {seller.is_dnc
                ? <span className="ws-pill is-dnc">do not contact</span>
                : <span className="ws-pill is-ok">{seller.lead_status || 'new'}</span>}
            </KV>
            <KV label="SMS allowed">{fmtBool(seller.allow_sms)}</KV>
            <KV label="Email allowed">{fmtBool(seller.allow_email)}</KV>
            <KV label="Voice allowed">{fmtBool(seller.allow_voice)}</KV>
            <KV label="SMS consent on file">{fmtBool(seller.sms_consent)}</KV>
          </div>
          <Note>
            "Not stated" is not permission. A blank is a source that never said,
            and every send path treats it that way.
          </Note>
        </div>
      </div>
    </div>
  )
}


const CADENCE_LABELS = {
  not_started: 'Not started',
  no_seller: 'No owner attached',
  active: 'Running',
  paused: 'Paused',
  completed: 'Finished all touches',
  stopped_manual: 'Stopped by you',
  stopped_dnc: 'Stopped — the owner opted out',
  stopped_deal_closed: 'Stopped — the deal closed',
  stopped_deal_dead: 'Stopped — the deal is dead',
  stopped_replied: 'Stopped — the owner replied',
  stopped_booked: 'Stopped — an appointment was booked',
}

/* The seller's multi-touch sequence.
 *
 * This is a control surface over the platform's own cadence engine, not a
 * second one. What it must never do is look like it is working when it is not,
 * so it shows three separate things: the state, why it cannot start if it
 * cannot, and whether this deployment can send at all. */
function CadencePanel({ dealId, cadence, act, busy }) {
  if (!cadence) return null
  const state = cadence.state || 'not_started'
  const running = state === 'active'
  const pill = running ? 'is-ok' : state === 'paused' ? 'is-warn'
    : state.startsWith('stopped') ? 'is-dnc' : ''

  function go(action) {
    return () => act(
      () => api.post(`/wholesale/deals/${dealId}/cadence`, { action }),
      `Cadence ${action === 'stop' ? 'stopped' : action + 'ed'}.`)
  }

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Follow-up sequence</span>
        <span className={`ws-pill ${pill}`}>
          {CADENCE_LABELS[state] || state}
        </span>
      </div>
      <Note>
        The same schedule, templates and sending rules as every other lead in
        this workspace.
      </Note>

      {cadence.state === 'no_seller' ? (
        <Empty>{cadence.detail}</Empty>
      ) : (
        <>
          <div className="ws-kv">
            <KV label="Touches sent">{cadence.current_touch ?? 0}</KV>
            <KV label="Next touch due">
              {cadence.next_touch_due_at ? fmtWhen(cadence.next_touch_due_at) : null}
            </KV>
            <KV label="Last sent">
              {cadence.last_touch_sent_at ? fmtWhen(cadence.last_touch_sent_at) : null}
            </KV>
            <KV label="Started">
              {cadence.started_at ? fmtWhen(cadence.started_at) : null}
            </KV>
          </div>

          {cadence.blockers && cadence.blockers.length ? (
            <div className="ws-warn" style={{ marginTop: 12 }}>
              {cadence.blockers.map((b, i) => <div key={i}>{b}</div>)}
            </div>
          ) : null}

          {cadence.sending_note ? (
            <div className="ws-notice" style={{ marginTop: 12 }}>
              {cadence.sending_note}
            </div>
          ) : null}

          <div className="ws-actions" style={{ marginTop: 12 }}>
            <button className="btn btn--primary" disabled={busy || !cadence.can_start}
                    onClick={go('start')}>
              {state === 'not_started' ? 'Start sequence' : 'Start again'}
            </button>
            <button className="btn btn--secondary" disabled={busy || !cadence.can_pause}
                    onClick={go('pause')}>Pause</button>
            <button className="btn btn--secondary" disabled={busy || !cadence.can_resume}
                    onClick={go('resume')}>Resume</button>
            <button className="btn btn--secondary" disabled={busy || !cadence.can_stop}
                    onClick={go('stop')}>Stop</button>
          </div>
          <Why label="When it stops by itself">
            <p className="ws-comp__sub">
              It stops on its own when the owner opts out, when the owner
              replies, when an appointment is booked, and when the deal closes
              or dies. You do not have to remember to stop it.
            </p>
          </Why>
        </>
      )}
    </div>
  )
}


function OutreachForm({ dealId, seller, act, busy }) {
  const [text, setText] = useState('')
  const blocked = seller?.is_dnc
  return (
    <>
      {blocked ? (
        <div className="ws-warn">
          This owner is on the do-not-contact list. Nothing can be sent, and that
          is enforced on the server, not by hiding this box.
        </div>
      ) : null}
      <label className="ws-vis-hidden" htmlFor="sel-send">
        Message to send to the owner
      </label>
      <textarea id="sel-send" rows={3} value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Hi {first_name}, I buy houses in your area…"
                className="ws-input" />
      <div className="ws-actions" style={{ marginTop: 10 }}>
        <button className="btn btn--primary"
                disabled={busy || blocked || !text.trim() || !seller?.phone}
                onClick={() => act(async () => {
                  await api.post(`/wholesale/deals/${dealId}/outreach`,
                                 { message: text, channel: 'sms' })
                  setText('')
                }, 'Message sent.')}>
          Send SMS
        </button>
        {!seller?.phone
          ? <span className="ws-muted">No phone number on this owner yet.</span>
          : null}
      </div>
    </>
  )
}


function AnalysisTab({ room, act, busy }) {
  const { deal, analysis, comps, arv_calculation: arvCalc } = room
  const [form, setForm] = useState({
    arv: deal.arv ?? '', repair_estimate: deal.repair_estimate ?? '',
    investor_percentage_used: deal.investor_percentage_used ?? '',
    desired_wholesale_fee: deal.desired_wholesale_fee ?? '',
    proposed_offer: deal.proposed_offer ?? '', analysis_notes: deal.analysis_notes ?? '',
  })
  function set(key, value) { setForm((f) => ({ ...f, [key]: value })) }

  return (
    <>
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">The offer, worked out</div>
        {analysis.blocked ? (
          <div className="ws-warn">
            {analysis.warnings[0] || 'Not enough information to calculate an offer.'}
          </div>
        ) : <Steps steps={analysis.steps} />}
        <Warnings items={analysis.warnings} />
        <div className="ws-kv" style={{ marginTop: 14 }}>
          <KV label="Investor %">
            {analysis.investor_percentage}
            <span className="ws-source">{analysis.investor_percentage_source}</span>
          </KV>
          <KV label="Wholesale fee">
            {fmtMoney(analysis.wholesale_fee)}
            <span className="ws-source">{analysis.wholesale_fee_source}</span>
          </KV>
          <KV label="Estimated buyer margin">
            {fmtMoney(analysis.estimated_buyer_margin)}
          </KV>
        </div>
      </div>

      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Assumptions — all editable</div>
        <p className="ws-panel-note">
          A figure you type here is labelled MANUAL. An ARV derived from the comps
          below is labelled ESTIMATED, and a manual ARV is never overwritten by a
          comp set.
        </p>
        <div className="ws-grid">
          {[['arv', 'ARV', 'money'],
            ['repair_estimate', 'Repair estimate', 'money'],
            ['investor_percentage_used', 'Investor % for this deal', 'percent'],
            ['desired_wholesale_fee', 'Wholesale fee', 'money'],
            ['proposed_offer', 'Proposed offer', 'money']].map(([key, label, as]) => (
            <div className="ws-field" key={key}>
              <label htmlFor={`as-${key}`}>{label}</label>
              <input id={`as-${key}`} inputMode="decimal" value={form[key]}
                     onChange={(e) => set(key, e.target.value)} />
              {/* The box keeps the raw figure; this reads it back. */}
              <Reads value={form[key]} as={as} />
            </div>
          ))}
        </div>
        <div className="ws-field" style={{ marginTop: 12 }}>
          <label htmlFor="an-notes">Notes</label>
          <textarea id="an-notes" rows={2} value={form.analysis_notes}
                    onChange={(e) => set('analysis_notes', e.target.value)} />
        </div>
        <div className="ws-actions" style={{ marginTop: 12 }}>
          <button className="btn btn--primary" disabled={busy}
                  onClick={() => act(() => {
                    const payload = {}
                    Object.entries(form).forEach(([k, v]) => {
                      if (v === '' || v === null) return
                      payload[k] = k === 'analysis_notes' ? v : Number(v)
                    })
                    return api.patch(`/wholesale/deals/${deal.id}/analysis`, payload)
                  }, 'Analysis updated.')}>
            Save assumptions
          </button>
          <button className="btn btn--secondary" disabled={busy}
                  onClick={() => act(
                    () => api.post(`/wholesale/deals/${deal.id}/analysis/recalculate`),
                    'Recalculated.')}>
            Recalculate from comps
          </button>
        </div>
      </div>

      {/* The comps workspace. SAVE / EDIT / DELETE on every row, with
          "exclude from the ARV" kept visibly distinct from "delete the
          record", and the subject set beside the comp set so the numbers can
          be argued with rather than taken on faith. */}
      <CompsWorkspace dealId={deal.id} deal={deal} comps={comps}
                      stats={room.comp_statistics} arvCalc={arvCalc}
                      capability={room.file_storage} act={act} busy={busy} />
    </>
  )
}


function OfferTab({ room, act, busy }) {
  const { deal, approvals, analysis } = room
  const [amount, setAmount] = useState(analysis.max_allowable_offer ?? '')
  const [reasoning, setReasoning] = useState('')
  // Which premature approval, if any, the operator has deliberately chosen to
  // send anyway. Reset by re-rendering, so it never persists across deals.
  const [acknowledged, setAcknowledged] = useState(null)
  const readiness = room.approval_readiness || { kinds: {}, next: null }

  return (
    <>
      {/* The negotiation first: what was offered, what came back, where the two
          sides stand. The approval machinery below it is a separate question
          and was being read as the whole of the offer story. */}
      <NegotiationLedger deal={deal} offers={room.offers || []}
                         analysis={analysis} act={act} busy={busy} />

      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Ask for approval</div>
        <Note>
          An offer, a contract and an assignment each need a person to approve
          them. Nothing here binds the company on its own.
        </Note>
        <div className="ws-grid">
          <div className="ws-field">
            <label htmlFor="ap-amount">Amount</label>
            <input id="ap-amount" value={amount}
                   onChange={(e) => setAmount(e.target.value)} />
          </div>
        </div>
        <div className="ws-field" style={{ marginTop: 10 }}>
          <label htmlFor="ap-reason">Reasoning</label>
          <textarea id="ap-reason" rows={2} value={reasoning}
                    onChange={(e) => setReasoning(e.target.value)} />
        </div>
        {/* Phase 5. Three identical buttons made every approval look equally
            valid, and the human review found an assignment approval pending
            for a fee on a deal with no buyer and no buyer price. The next
            valid one is primary; the others say what they are waiting for and
            can still be sent deliberately. Nothing is auto-approved. */}
        <div className="ws-approvals">
          {['offer', 'contract', 'assignment'].map((kind) => {
            const state = readiness.kinds?.[kind] || {}
            const gaps = state.missing || []
            const isNext = !!state.is_next
            return (
              <div className={`ws-approval ${isNext ? 'is-next' : ''}`} key={kind}>
                <button
                  className={`btn ${isNext ? 'btn--primary' : 'btn--secondary'}`}
                  disabled={busy}
                  onClick={() => act(
                    () => api.post(`/wholesale/deals/${deal.id}/approvals`, {
                      kind,
                      amount: amount === '' ? null : Number(amount),
                      reasoning: reasoning || null,
                      // Only ever true once the operator has read the gap
                      // below and pressed the button again.
                      acknowledge_missing: acknowledged === kind,
                    }), `${kind} approval requested.`)}>
                  Request {kind} approval
                </button>
                {state.approved ? (
                  <span className="ws-pill is-ok">Approved</span>
                ) : state.pending ? (
                  <span className="ws-pill is-warn">Already pending</span>
                ) : null}
                {gaps.length ? (
                  <span className="ws-approval__gap">
                    Needs {gaps.map((g) => g.toLowerCase()).join(', ')}.
                    {' '}
                    <label className="ws-checkbox">
                      <input type="checkbox"
                             checked={acknowledged === kind}
                             onChange={(e) => setAcknowledged(
                               e.target.checked ? kind : null)} />
                      Ask anyway
                    </label>
                  </span>
                ) : null}
              </div>
            )
          })}
        </div>
        <Why label="What a request keeps a copy of">
          <p className="ws-comp__sub">
            The numbers the request was built from are stored with it, so a
            later edit to the ARV, the repairs or the fee cannot rewrite what
            somebody actually approved.
          </p>
        </Why>
      </div>

      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Approval history</div>
        {!approvals.length ? <Empty>Nothing has been asked for yet.</Empty> : (
          <div className="ws-scroll">
          <table className="ws-table">
            <thead>
              <tr><th>Kind</th><th>Amount</th><th>Status</th><th>Requested</th>
                <th>Decided</th><th /></tr>
            </thead>
            <tbody>
              {approvals.map((a) => (
                <tr key={a.id}>
                  <td>{a.kind}</td>
                  <td className="ws-num">{fmtMoney(a.amount)}</td>
                  <td>
                    <span className={`ws-pill ${a.status === 'approved' ? 'is-ok'
                      : a.status === 'rejected' ? 'is-dnc' : 'is-warn'}`}>
                      {a.status}
                    </span>
                  </td>
                  <td>{fmtWhen(a.created_at)}</td>
                  <td>{a.decided_at ? fmtWhen(a.decided_at) : '—'}</td>
                  <td>
                    {a.status === 'pending' ? (
                      <span className="ws-actions">
                        <button className="btn btn--primary btn--sm" disabled={busy}
                                onClick={() => act(
                                  () => api.post(`/wholesale/approvals/${a.id}/decide`,
                                                 { approve: true }), 'Approved.')}>
                          Approve
                        </button>
                        <button className="btn btn--secondary btn--sm" disabled={busy}
                                onClick={() => act(
                                  () => api.post(`/wholesale/approvals/${a.id}/decide`,
                                                 { approve: false }), 'Rejected.')}>
                          Reject
                        </button>
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
    </>
  )
}


function DocumentsTab({ room, act, busy }) {
  const { deal } = room

  return (
    <>
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Contract</div>
        <ContractForm deal={deal} act={act} busy={busy} />
      </div>

      {/* The drawer IS the panel. It used to be wrapped in a second one whose
          only content was a paragraph explaining the design, which made every
          document sit inside two nested boxes under an essay. */}
      <DocumentDrawer room={room} act={act} busy={busy} />
    </>
  )
}


function ContractForm({ deal, act, busy }) {
  const [form, setForm] = useState({
    contract_price: deal.contract_price ?? '',
    contract_status: deal.contract_status ?? 'none',
    inspection_deadline: deal.inspection_deadline ?? '',
    close_of_escrow_target: deal.close_of_escrow_target ?? '',
  })
  return (
    <>
      <div className="ws-grid">
        <div className="ws-field">
          <label htmlFor="ct-price">Contract price</label>
          <input id="ct-price" value={form.contract_price}
                 onChange={(e) => setForm((f) => ({ ...f, contract_price: e.target.value }))} />
        </div>
        <div className="ws-field">
          <label htmlFor="ct-status">Status</label>
          <select id="ct-status" value={form.contract_status}
                  onChange={(e) => setForm((f) => ({ ...f, contract_status: e.target.value }))}>
            {['none', 'preparing', 'sent', 'signed', 'cancelled'].map(
              (s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="ws-field">
          <label htmlFor="ct-inspection">Inspection deadline</label>
          <input id="ct-inspection" type="date" value={form.inspection_deadline || ''}
                 onChange={(e) => setForm((f) => ({ ...f, inspection_deadline: e.target.value }))} />
        </div>
        <div className="ws-field">
          <label htmlFor="ct-close">Target close</label>
          <input id="ct-close" type="date" value={form.close_of_escrow_target || ''}
                 onChange={(e) => setForm((f) => ({ ...f, close_of_escrow_target: e.target.value }))} />
        </div>
      </div>
      <div className="ws-actions" style={{ marginTop: 12 }}>
        <button className="btn btn--primary" disabled={busy}
                onClick={() => act(() => api.patch(
                  `/wholesale/deals/${deal.id}/contract`, {
                    contract_price: form.contract_price === '' ? null
                      : Number(form.contract_price),
                    contract_status: form.contract_status,
                    inspection_deadline: form.inspection_deadline || null,
                    close_of_escrow_target: form.close_of_escrow_target || null,
                  }), 'Contract updated.')}>
          Save contract
        </button>
      </div>
    </>
  )
}


/* One matched buyer, as a decision rather than as a score.
 *
 * WHAT CHANGED IN PHASE 4. This row used to be: a name, a percentage, and every
 * single criterion the matcher evaluated, printed in full, for every buyer. The
 * reasoning is genuinely valuable — it is why this matcher can be argued with —
 * but it is what you read AFTER you have a shortlist, not while you are making
 * one. It now lives behind "View why", unchanged and complete.
 *
 * What is in front of you instead is what the decision actually turns on:
 * whether they buy where this house is, whether they can pay for it, how fast
 * they close, and what they have done here before. Nothing is ranked or
 * recommended beyond the score the matcher already computed.
 */
function MatchRow({ m, chosen, onToggle }) {
  const a = m.activity
  const excluded = m.disqualified || m.do_not_contact
  return (
    <>
      <tr className={`ws-match ${excluded ? 'is-excluded' : ''}`}>
        <td>
          <input type="checkbox" checked={chosen} disabled={excluded}
                 aria-label={`Send the deal sheet to ${m.buyer_name || 'this buyer'}`}
                 onChange={(e) => onToggle(e.target.checked)} />
        </td>
        <td>
          <div className="ws-comp__addr">{m.buyer_name || '(unnamed)'}</div>
          <div className="ws-comp__sub">
            {m.contact_name || '—'}
            {m.buy_box_count > 1 ? ` · ${m.buy_box_count} buy boxes` : ''}
          </div>
          {m.do_not_contact ? (
            <div className="ws-comp__sub ws-blocked">opted out of contact</div>
          ) : null}
          {m.disqualified_reason ? (
            <div className="ws-comp__sub ws-blocked">{m.disqualified_reason}</div>
          ) : null}
        </td>
        <td className="ws-num"><Score value={m.score} /></td>
        <td>
          {m.geography || <span className="ws-muted">anywhere — no geography set</span>}
          {m.max_price !== null && m.max_price !== undefined ? (
            <div className="ws-comp__sub">up to {fmtMoney(m.max_price)}</div>
          ) : null}
        </td>
        <td>
          {m.proof_of_funds_on_file
            ? <span className="ws-pill is-ok">Funds on file</span>
            : <span className="ws-pill is-muted">No funds on file</span>}
        </td>
        <td className="ws-num">
          {m.typical_close_days ? `${m.typical_close_days} days` : '—'}
          {m.reliability_rating
            ? <div className="ws-comp__sub">rated {m.reliability_rating}</div> : null}
        </td>
        <td>
          {a && a.sheets_sent ? (
            <>
              <div>{a.sheets_sent} sent · {a.responded} replied</div>
              <div className="ws-comp__sub">
                {a.selected_count
                  ? `chosen on ${a.selected_count} deal${a.selected_count === 1 ? '' : 's'}`
                  : 'never chosen'}
              </div>
            </>
          ) : <span className="ws-muted">nothing sent yet</span>}
        </td>
        <td>
          {/* The full criterion-by-criterion reasoning, unchanged — one click
              away instead of always on. */}
          <details className="ws-why ws-why--inline">
            <summary>View why</summary>
            <div className="ws-why__body">
              <p className="ws-comp__sub">
                The score is the share of the dimensions this buyer&apos;s buy box
                actually constrains that this deal satisfies. A dimension they
                never specified is not counted against them.
              </p>
              <Factors factors={m.factors} />
            </div>
          </details>
        </td>
      </tr>
    </>
  )
}


function BuyersTab({ room, act, busy }) {
  const { deal, buyer_matches: matches, analysis } = room
  const [chosen, setChosen] = useState({})
  const [askingPrice, setAskingPrice] = useState(analysis.buyer_price
                                                 ?? analysis.contract_price ?? '')
  const [preview, setPreview] = useState(null)
  const [outcome, setOutcome] = useState(null)
  const [channel, setChannel] = useState('email')
  const selected = Object.keys(chosen).filter((k) => chosen[k])

  return (
    <>
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">
          <span>Matched buyers ({matches.length})</span>
          <button className="btn btn--secondary btn--sm" disabled={busy}
                  onClick={() => act(
                    () => api.post(`/wholesale/deals/${deal.id}/match-buyers`),
                    'Buyers rescored.')}>
            Run matching
          </button>
        </div>
        <Note>
          Tick the buyers to send to. Nothing goes out until you press Send.
        </Note>
        {!matches.length ? (
          <Empty>No buyers scored yet. Add buyers with buy boxes, then run matching.</Empty>
        ) : (
          <div className="ws-scroll">
          <table className="ws-table ws-matches">
            <thead>
              <tr>
                <th style={{ width: 28 }} /><th>Buyer</th>
                <th className="ws-num">Match</th><th>Where they buy</th>
                <th>Can they take it</th><th className="ws-num">Closes in</th>
                <th>Track record</th><th />
              </tr>
            </thead>
            <tbody>
              {matches.map((m) => (
                <MatchRow key={m.id} m={m} chosen={!!chosen[m.buyer_id]}
                          onToggle={(v) => setChosen(
                            (c) => ({ ...c, [m.buyer_id]: v }))} />
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">Send the deal</div>
        <p className="ws-panel-note">
          Nothing goes out until you press Send, and only to the buyers you tick.
          This screen never mails a whole match list.
        </p>

        {(room.disposition_channels || []).filter((c) => !c.enabled).length ? (
          <div className="ws-notice">
            {(room.disposition_channels || []).filter((c) => !c.enabled)
              .map((c) => <div key={c.channel}>{c.detail}</div>)}
          </div>
        ) : null}

        <div className="ws-grid">
          <div className="ws-field">
            <label htmlFor="dp-asking">Asking price for buyers</label>
            <input id="dp-asking" value={askingPrice}
                   onChange={(e) => setAskingPrice(e.target.value)} />
          </div>
          <div className="ws-field">
            <label htmlFor="dp-channel">Channel</label>
            <select id="dp-channel" value={channel}
                    onChange={(e) => setChannel(e.target.value)}>
              {(room.disposition_channels || [{ channel: 'email' }]).map((c) => (
                <option key={c.channel} value={c.channel}>
                  {c.channel}{c.enabled === false ? ' (not enabled)' : ''}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="ws-actions" style={{ marginTop: 12 }}>
          <button className="btn btn--secondary" disabled={busy || !selected.length}
                  onClick={() => act(async () => {
                    setPreview(await api.post(
                      `/wholesale/deals/${deal.id}/disposition/preview`, {
                        buyer_ids: selected,
                        asking_price: askingPrice === '' ? null : Number(askingPrice),
                      }))
                  })}>
            Preview the deal sheet
          </button>
          <button className="btn btn--primary" disabled={busy || !selected.length}
                  onClick={() => act(async () => {
                    const result = await api.post(
                      `/wholesale/deals/${deal.id}/disposition`, {
                        buyer_ids: selected,
                        asking_price: askingPrice === '' ? null : Number(askingPrice),
                        channel,
                      })
                    setChosen({})
                    setOutcome(result)
                  }, null)}>
            Send to {selected.length || 0} buyer(s)
          </button>
        </div>

        {/* WHAT ACTUALLY HAPPENED, PER BUYER. Not a toast saying "done" —
            a send that was refused and a send that succeeded look identical in
            a toast, and the difference is the whole point. */}
        {outcome ? (
          <div style={{ marginTop: 14 }}>
            <div className={outcome.sent ? 'ws-good' : 'ws-warn'}>
              {outcome.sent} sent, {outcome.failed} not sent.
            </div>
            <div className="ws-scroll">
            <table className="ws-table">
              <thead>
                <tr><th>Buyer</th><th>Result</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {outcome.results.map((r) => (
                  <tr key={r.buyer_id}>
                    <td>{r.buyer_name}</td>
                    <td>
                      <span className={`ws-pill ${r.sent ? 'is-ok' : 'is-dnc'}`}>
                        {r.sent ? 'sent' : r.code}
                      </span>
                    </td>
                    <td className="ws-muted">
                      {r.sent
                        ? (r.provider_message_id
                          ? `Provider reference ${r.provider_message_id}` : 'Sent.')
                        : r.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </div>
        ) : null}

        {preview ? (
          <div style={{ marginTop: 14 }}>
            <div className="ws-notice">{preview.note}</div>
            <div className="mono ws-mono">{preview.subject}{'\n\n'}{preview.body}</div>
          </div>
        ) : null}
      </div>

      {/* Every buyer on this deal, side by side, with the response form, the
          proof-of-funds control and the selection decision on the same row. */}
      <BuyerBoard dealId={deal.id} capability={room.file_storage}
                  act={act} busy={busy} />
    </>
  )
}


function ClosingTab({ room, act, busy }) {
  // The ledger, the contract dates, title, the closing and the two ways a deal
  // ends. Lifted out whole: this tab was four disconnected forms and no
  // arithmetic, and the one subtraction a wholesaler's business turns on was
  // left for the reader to do in their head.
  return (
    <ClosingWorkspace deal={room.deal} matches={room.buyer_matches || []}
                      act={act} busy={busy} />
  )
}


function AuditTab({ room }) {
  const { events } = room
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">Audit history</div>
      <Note>
        Every material action on this deal, with who did it — a person, the AI,
        an automation or the system.
      </Note>
      <Why label="What else these actions are written to">
        <p className="ws-comp__sub">
          Actions taken by a signed-in person are also written to the workspace
          audit log, which is kept separately from this module.
        </p>
      </Why>
      {!events.length ? <Empty>Nothing recorded yet.</Empty> : (
        <ul className="ws-events">
          {events.map((e) => (
            <li key={e.id}>
              <div className="ws-ev-head">
                <span className={`ws-actor actor-${e.actor_type}`}>{e.actor_type}</span>
                <span className="ws-ev-action">{e.action}</span>
                {e.actor_label ? <span className="ws-muted">· {e.actor_label}</span> : null}
                <span className="ws-ev-when">{fmtWhen(e.created_at)}</span>
              </div>
              {e.summary ? <div className="ws-ev-summary">{e.summary}</div> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


/* ── The Deal Summary Header ─────────────────────────────────────────────────
 *
 * Persistent, above the tabs, on every tab. It exists because of one sentence
 * in the brief: "A user should not need to hunt through eight tabs just to
 * understand the deal."
 *
 * Grouped the way a wholesaler thinks: the money, then the calendar, then the
 * disposition, then title. NEXT ACTION sits first and largest because it is
 * the only part that tells you what to DO, and it is computed on the server
 * from real workflow state — the same function the Command Center reads, so
 * the two can never disagree.
 */
function DealSummary({ room, nextAction, goTo }) {
  const { deal, seller, analysis, property } = room
  const matches = room.buyer_matches || []
  const outreach = room.buyer_outreach || []
  const contacted = outreach.filter((o) => o.sent_at).length
  const interested = outreach.filter(
    (o) => ['interested', 'offer_submitted', 'selected'].includes(o.status)).length
  const offers = outreach.filter((o) => o.offer_amount !== null
                                     && o.offer_amount !== undefined)
  const bestOffer = offers.length
    ? Math.max(...offers.map((o) => Number(o.offer_amount))) : null

  const collected = analysis.wholesale_fee_collected

  return (
    <div className="ws-summary">
      {/* The top line is the deal in six numbers: what it is worth, the most
          we may pay, what we did pay, what a buyer pays, what is left for us,
          and the day it has to happen by. Everything else on this header is
          real and stays on the screen — it is simply not this. */}
      <div className="ws-summary__top">
        <button type="button"
                className={`ws-summary__next tone-${nextAction.tone || 'ok'}`}
                onClick={() => nextAction.tab && goTo(nextAction.tab)}>
          <span className="ws-summary__next-label">Next action</span>
          <span className="ws-summary__next-title">{nextAction.label || '—'}</span>
          {nextAction.detail
            ? <span className="ws-summary__next-detail">{nextAction.detail}</span>
            : null}
        </button>

        <div className="ws-keys">
          <Key label="ARV" value={fmtMoney(analysis.arv)}
               tag={analysis.arv_source} />
          <Key label="Max offer (MAO)"
               value={fmtMoney(analysis.max_allowable_offer)} />
          <Key label="Contract price" value={fmtMoney(analysis.contract_price)} />
          <Key label="Buyer price" value={fmtMoney(analysis.buyer_price)} />
          <Key label="Expected spread"
               value={fmtMoney(analysis.estimated_spread)} />
          {/* Money that actually moved is the only green figure on the page,
              and it only appears once there is some. */}
          {collected !== null && collected !== undefined
            ? <Key label="Fee collected" tone="collected"
                   value={fmtMoney(collected)} />
            : null}
          <Key label="Closing" value={fmtDate(deal.closing_date)} />
          {/* Phase 6. PAYMENT STATUS belongs on the top line, because
              "closed" and "paid" are different facts and the header was
              answering only the first. `payment_state` is the server's, from
              the record — a closing date passing moves nothing. */}
          <PaymentKey state={deal.payment_state} collected={collected} />
        </div>
      </div>

      <div className="ws-summary__groups">
        <SumGroup title="Other money">
          <Sum label="Repairs" value={fmtMoney(analysis.repair_estimate)}
               tag={analysis.repair_estimate_source} />
          <Sum label="Seller asking" value={fmtMoney(seller?.asking_price)} />
          <Sum label="Our offer" value={fmtMoney(analysis.proposed_offer)} />
          {collected === null || collected === undefined
            ? <Sum label="Fee collected" value="—" />
            : null}
        </SumGroup>

        <SumGroup title="Dates">
          <Sum label="Contract" value={fmtDate(deal.contract_date)} />
          <Sum label="Inspection" value={fmtDate(deal.inspection_deadline)} />
          <Sum label="Target close" value={fmtDate(deal.close_of_escrow_target)} />
          <Sum label="Closed" value={fmtDate(deal.closed_at)} />
        </SumGroup>

        <SumGroup title="Disposition">
          <Sum label="Matched" value={fmtNum(matches.length, '0')} />
          <Sum label="Contacted" value={fmtNum(contacted, '0')} />
          <Sum label="Interested" value={fmtNum(interested, '0')} />
          <Sum label="Offers" value={fmtNum(offers.length, '0')} />
          <Sum label="Best offer" value={fmtMoney(bestOffer)} />
        </SumGroup>

        <SumGroup title="Seller">
          <Sum label="Name" wrap value={seller
            ? [seller.first_name, seller.last_name].filter(Boolean).join(' ') || '—'
            : '—'} />
          <Sum label="Phone" value={seller?.phone || '—'} />
          <Sum label="Email" value={seller?.email || '—'} wrap />
          <Sum label="Qualification"
               value={seller?.qualification_band
                 ? `${fmtLabel(seller.qualification_band)}${seller.qualification_score !== null && seller.qualification_score !== undefined ? ` (${seller.qualification_score})` : ''}`
                 : '—'} />
        </SumGroup>

        <SumGroup title="Title">
          <Sum label="Company" value={deal.title_company || '—'} wrap />
          <Sum label="Officer" value={deal.title_escrow_officer || '—'} wrap />
          <Sum label="Status" value={fmtLabel(deal.title_status)} />
          <Sum label="Earnest" value={fmtMoney(deal.earnest_money)} />
        </SumGroup>
      </div>
    </div>
  )
}


/* A headline figure. Same data as a `Sum`, twice the size, because the header
 * had nine equally-weighted money rows and a person reading it had to find the
 * spread among them rather than see it. */
/* The four states money can be in, named so nobody has to infer one.
 * EXPECTED and COLLECTED are the two that get confused, so they are the two
 * drawn most differently. */
const PAYMENT_LABEL = {
  not_closed: 'Expected',
  payment_pending: 'Closed — payment pending',
  fee_collected: 'Collected',
}
const PAYMENT_TONE = {
  not_closed: null,
  payment_pending: 'attention',
  fee_collected: 'collected',
}

function PaymentKey({ state, collected }) {
  const key = state || (collected ? 'fee_collected' : 'not_closed')
  const tone = PAYMENT_TONE[key]
  // A WORD, NOT A FIGURE. The other keys are numbers at display size; setting
  // "Closed — payment pending" in the same type wrapped it onto three lines
  // and broke the row. It is a state, so it is drawn as one.
  return (
    <div className="ws-key ws-key--state">
      <div className="ws-key__label">Payment</div>
      <div className={`ws-key__state ${tone ? `tone-${tone}` : ''}`}>
        {PAYMENT_LABEL[key] || fmtLabel(key)}
      </div>
    </div>
  )
}


function Key({ label, value, tag, tone }) {
  const empty = value === null || value === undefined || value === '' || value === '—'
  return (
    <div className={`ws-key ${tone && !empty ? 'is-' + tone : ''}`}>
      <span className="ws-key__label">{label}</span>
      <span className="ws-key__value">{empty ? '—' : value}</span>
      {tag ? <span className="ws-source">{tag}</span> : null}
    </div>
  )
}


function SumGroup({ title, children }) {
  return (
    <div className="ws-sumgroup">
      <div className="ws-sumgroup__title">{title}</div>
      <div className="ws-sumgroup__items">{children}</div>
    </div>
  )
}


/* One figure. `tag` is the provenance label the module carries everywhere —
 * ESTIMATED / IMPORTED / MANUAL / VERIFIED — so a number never appears without
 * saying where it came from. */
function Sum({ label, value, tag, tone, wrap }) {
  const empty = value === null || value === undefined || value === '' || value === '—'
  // Green is for money that moved. An em dash is not money that moved.
  const applied = tone && !empty ? 'is-' + tone : ''
  return (
    <div className={`ws-sum ${applied} ${wrap ? 'ws-sum--wrap' : ''}`}>
      <span className="ws-sum__label">{label}</span>
      <span className="ws-sum__value">{empty ? '—' : value}</span>
      {tag ? <span className="ws-sum__tag"><span className="ws-source">{tag}</span></span> : null}
    </div>
  )
}


