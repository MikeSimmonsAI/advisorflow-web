/**
 * Opportunity Detail — a guided sales workspace, not a database editor.
 *
 * WHAT THIS PAGE USED TO BE. Every panel the deal has, open, at full height,
 * in lifecycle order: fourteen empty discovery textareas, a five-field demo
 * work order, the pricing card, the proposal builder, closing, billing. A rep
 * opening a deal met a wall of empty boxes and had to work out for themselves
 * what the next move was.
 *
 * WHAT IT IS NOW. The same records, the same endpoints, the same authority —
 * arranged so that the five questions a salesperson actually opens a deal to
 * answer are answered before any scrolling:
 *
 *   who is this · where are we · what do I do next · what is missing ·
 *   what comes after that
 *
 * The deal's own lifecycle drives it. `SECTION_FOR_STAGE` maps the EXISTING
 * stages onto five sections; the one the deal is in is expanded, the ones
 * behind it collapse to a summary line, the ones ahead sit closed but
 * openable. Nothing here invents a second lifecycle, a second discovery
 * engine, a second proposal system or a second billing model — DealBillingPanel,
 * ProposalPanel, ClosingPanel, DemoSitesPanel and ApptSyncPanel are the same
 * components doing the same work, put where a seller looks for them.
 *
 * Every write still goes to the real API and the record reloads from the
 * server: what is on screen is what the database holds.
 */
import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import FindTeamTime from './FindTeamTime'
import ApptSyncPanel from './ApptSyncPanel'
import RescheduleDialog from './RescheduleDialog'
import ProposalPanel from './ProposalPanel'
import DemoSitesPanel from './DemoSitesPanel'
import ClosingPanel from './ClosingPanel'
// The money half of the same deal, beside the closing checklist rather than in
// a screen the rep has to leave this one to reach.
import DealBillingPanel from './DealBillingPanel'
import ReassignControl from './ReassignControl'
import DiscoveryPanel from './DiscoveryPanel'
import DemoPanel from './DemoPanel'
import DealCommand, { StageSection, SECTION_FOR_STAGE, SECTION_ORDER } from './DealCommand'
import {
  Card, Chip, Info, Empty, ErrorBar,
  money, dateTime, dueLabel, wallDateTime,
} from './parts'
import { BillingOptions } from './BillingOptions.jsx'
import CustomizeDealDialog from './CustomizeDealDialog'

const CONF_TONE = {
  confirmed: 'green', declined: 'red', no_show: 'red',
  cancelled: 'red', sent: 'amber', pending: 'amber',
}

/**
 * Meetings on this deal, booked through the shared-availability finder so every
 * required person was actually free.
 */
function Meetings({ opp, onFind, onConfirm, onCancel, onMove, saving }) {
  const appts = opp.appointments || []
  return (
    <Card title="MEETINGS"
          sub="Booked through Find Team Time — everyone required was free"
          right={<button className="sw-btn sw-primary" onClick={onFind}>Find Team Time</button>}
          bodyless>
      {appts.length === 0 ? (
        <Empty title="No meetings booked">
          <b>Find Team Time</b> picks the meeting type, resolves who must attend
          from their roles, and returns only the times when all of them are free.
        </Empty>
      ) : appts.map(a => (
        <div className="sw-row" key={a.id}>
          <div>
            <b>
              {wallDateTime(a.starts_at_local || a.starts_at)} ·{' '}
              {a.meeting_type || 'Meeting'}
            </b>
            <p>
              {a.duration_minutes} min · {a.timezone} ·{' '}
              {a.participants.map(p => p.full_name).join(', ')}
            </p>
          </div>
          <div className="sw-actions">
            {/* JOIN MEETING. Only ever the attendee link — the host link is a
                separate, participant-gated fetch and is never in this payload. */}
            {a.video?.join_url && a.status === 'scheduled' && (
              <a className="sw-tiny sw-primary" href={a.video.join_url}
                 target="_blank" rel="noopener noreferrer"
                 style={{ textDecoration: 'none' }}>Join</a>
            )}
            {a.video?.needs_attention && (
              <Chip tone="amber">{a.video.label}</Chip>
            )}
            <Chip tone={CONF_TONE[a.confirmation_status]}>
              {String(a.confirmation_status || '').replace('_', ' ')}
            </Chip>
            {a.confirmation_status !== 'confirmed' && a.status === 'scheduled' && (
              <button className="sw-tiny sw-primary" disabled={saving}
                      onClick={() => onConfirm(a.id)}>Confirm</button>
            )}
            {a.status === 'scheduled' && (
              <button className="sw-tiny" disabled={saving}
                      onClick={() => onMove(a)}>Move</button>
            )}
            {a.status === 'scheduled' && (
              <button className="sw-tiny" disabled={saving}
                      onClick={() => onCancel(a.id)}>Cancel</button>
            )}
          </div>
        </div>
      ))}
    </Card>
  )
}

/* The deal's identity — who this is and how to reach them. Editable in place,
   because these get typed in a hurry at intake and corrected later: a prospect
   created from a voicemail has no email until somebody calls back. */
const RECORD_FIELDS = [
  ['company_name', 'COMPANY NAME', 'text', 'Acme Facilities LLC'],
  ['contact_name', 'CONTACT NAME', 'text', 'First Last'],
  ['phone', 'PHONE', 'tel', '+1 555 555 0100'],
  ['email', 'EMAIL', 'email', 'name@company.com'],
  ['website', 'WEBSITE', 'url', 'https://example.com'],
  ['industry', 'INDUSTRY', 'text', 'Commercial cleaning'],
  ['timezone', 'TIMEZONE', 'text', 'America/Chicago'],
]

const COMMON_TIMEZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver',
  'America/Phoenix', 'America/Los_Angeles', 'America/Anchorage',
  'Pacific/Honolulu', 'UTC',
]

/**
 * RECORD, read-mode and edit-mode. Read-mode is deliberately unchanged from
 * what it always was — a record being read is not a form, and an always-live
 * form invites edits nobody meant to make. Pressing Edit swaps the same card
 * into inputs; nothing navigates.
 *
 * Saves go through the page's own PATCH, so the server is the one that decides
 * what is valid and it writes the change to this deal's timeline.
 */
function RecordIdentity({ opp, editing, saved, onSave, onSaved, onCancel }) {
  const [form, setForm] = useState({})
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  // Seed once per opening, so Cancel-then-Edit starts from the record again
  // rather than from whatever was half-typed last time — and so a background
  // Refresh landing mid-edit does not wipe out what is being typed.
  const seeded = useRef(false)
  useEffect(() => {
    if (!editing) { seeded.current = false; return }
    if (seeded.current) return
    seeded.current = true
    const f = {}
    RECORD_FIELDS.forEach(([k]) => { f[k] = opp[k] || '' })
    setForm(f); setErr(null)
  }, [editing, opp])

  async function save() {
    if (!(form.company_name || '').trim()) {
      // The company name titles this record everywhere else in the workspace.
      setErr('A company name is required — it is how this deal is named in the pipeline.')
      return
    }
    // Send only what actually changed. An empty string clears the field
    // server-side; an untouched field is not in the body at all.
    const body = {}
    RECORD_FIELDS.forEach(([k]) => {
      const next = (form[k] || '').trim()
      if (next !== (opp[k] || '')) body[k] = next
    })
    if (Object.keys(body).length === 0) { onCancel(); return }
    setErr(null); setBusy(true)
    try {
      await onSave(body)
      onSaved()
    } catch (e) {
      setErr(e.message || 'Could not save these changes. Nothing was changed.')
    } finally { setBusy(false) }
  }

  if (!editing) {
    return (
      <>
        <div className="sw-infogrid">
          <Info label="PHONE" value={opp.phone} />
          <Info label="EMAIL" value={opp.email} />
          <Info label="WEBSITE" value={opp.website} />
          <Info label="TIMEZONE" value={opp.timezone} />
          <Info label="SALES OWNER" value={opp.owner_name} />
          <Info label="BRAND" value={opp.brand_sales_org?.name} />
          {/* `deal_value` IS NOT WHAT THE CUSTOMER WILL BE CHARGED, and shown
              here under that name it read exactly as if it were. On a live deal
              it sat at $1,497 — the legacy catalogue figure — three lines above
              a Billing panel correctly stating a $1,500 setup fee. Two numbers,
              both authoritative-looking, differing by three dollars: the kind of
              contradiction a rep repeats to a customer.

              So it is shown ONLY where it is still the only signal there is — a
              legacy deal carrying neither a package nor a negotiated rate, which
              is the one case `deal_pricing` reads it for. Where commercial terms
              resolve, the Billing panel is the single authority and this would
              only compete with it.

              The column itself is untouched: pipeline reporting still reads it,
              the pricing card still edits it behind its own disclosure, and no
              historical data is migrated. This is a presentation fix. */}
          {!opp.selected_package_id && opp.custom_unit_price == null
            && opp.deal_value != null ? (
            <Info label="LEGACY DEAL VALUE (REPORTING ONLY)"
                  value={money(opp.deal_value)} />
          ) : null}
        </div>
        {!opp.selected_package_id && opp.custom_unit_price == null
          && opp.deal_value != null && (
          <p className="sw-subtle" style={{ margin: '8px 0 0', fontSize: 11 }}>
            This deal has no package and no negotiated rate, so the legacy value
            is all there is. It is a one-time figure used for pipeline reporting
            — not a quote, and not what the customer would be charged.
          </p>
        )}
        {saved && (
          <p className="sw-subtle" style={{ margin: '10px 0 0', fontSize: 12 }}>
            Record updated. The change is on this deal's timeline.
          </p>
        )}
      </>
    )
  }

  return (
    <>
      <datalist id="af-tz-list">
        {COMMON_TIMEZONES.map(tz => <option value={tz} key={tz} />)}
      </datalist>
      <div className="sw-grid-even">
        {RECORD_FIELDS.map(([k, label, type, placeholder]) => (
          <div className="sw-field" key={k}>
            <label>{label}</label>
            <input
              className="sw-input"
              type={type}
              value={form[k] || ''}
              placeholder={placeholder}
              list={k === 'timezone' ? 'af-tz-list' : undefined}
              disabled={busy}
              onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))}
            />
          </div>
        ))}
      </div>
      {err && <div className="sw-err" style={{ marginTop: 12 }}>{err}</div>}
      <p className="sw-subtle" style={{ margin: '10px 0 0', fontSize: 12 }}>
        Clearing a field empties it on the record. Every change is written to
        this deal's timeline with what it was before.
      </p>
      <div className="sw-flex" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
        <button className="sw-btn" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="sw-btn sw-primary" onClick={save} disabled={busy}>
          {busy ? 'Saving…' : 'Save record'}
        </button>
      </div>
    </>
  )
}

/** The continuous lifecycle, rendered from real timestamps on the record. */
function Lifecycle({ opp }) {
  const L = opp.lifecycle || {}
  const steps = [
    ['Prospect created', L.created_at],
    ['Contacted', L.contacted_at],
    ['Discovery completed', L.discovery_completed_at],
    ['Demo requested', L.demo_requested_at],
    ['Demo ready', L.demo_ready_at],
    ['Proposal sent', L.proposal_sent_at],
    ['Won', L.won_at],
    ['Handoff / onboarding', opp.customer_organization_id ? L.won_at : null],
  ]
  return (
    <div className="sw-life">
      {steps.map(([label, at]) => (
        <div key={label} className={'sw-life-step ' + (at ? 'sw-done' : 'sw-pending')}>
          <b>{label.toUpperCase()}</b>
          <small>{at ? dateTime(at) : 'Not yet'}</small>
        </div>
      ))}
    </div>
  )
}

function PackageDeal({ opp, packages, onPatch, saving }) {
  const [pkgId, setPkgId] = useState(opp.selected_package_id || '')
  const [interestId, setInterestId] = useState(opp.package_interest_id || '')
  const [value, setValue] = useState(opp.deal_value != null ? String(opp.deal_value) : '')
  const [fee, setFee] = useState(
    opp.implementation_fee != null ? String(opp.implementation_fee) : '')
  const [reason, setReason] = useState('')

  useEffect(() => {
    setPkgId(opp.selected_package_id || '')
    setInterestId(opp.package_interest_id || '')
    setValue(opp.deal_value != null ? String(opp.deal_value) : '')
    setFee(opp.implementation_fee != null ? String(opp.implementation_fee) : '')
    setReason('')
  }, [opp.id, opp.selected_package_id, opp.package_interest_id, opp.deal_value,
      opp.implementation_fee])

  const [customizing, setCustomizing] = useState(false)
  const selected = packages.find(p => p.id === pkgId)
  // Prefer the deal's own pricing block: it carries the per-deal implementation
  // fee, which the catalogue's copy cannot know about.
  const pricing = (opp.billing && opp.billing.package_id === pkgId && selected)
    ? { ...selected.pricing, ...{ options: opp.billing.options,
                                  implementation_fee: opp.billing.implementation_fee } }
    : (selected ? selected.pricing : null)
  const billingOption = opp.billing_option || 'month_to_month'
  // UNCHANGED ON PURPOSE. `deal_value` still derives from the package's `price`
  // - the one-time implementation figure it has always meant. The billing
  // option drives the recurring numbers, which are shown separately.
  const derived = selected && selected.price != null ? Number(selected.price) : null
  const isOverride = value !== '' && derived != null && Math.abs(Number(value) - derived) > 0.005
  const needsReason = (isOverride || (value !== '' && derived == null)) && !opp.deal_value_override

  const isCustom = opp.custom_unit_price != null
  const pending = opp.pending_pricing_approval

  return (
    <Card title={isCustom ? 'CUSTOM DEAL' : 'PACKAGE & TERMS'}
          sub={isCustom
            ? 'Negotiated pricing for this deal only — the catalogue is unchanged'
            : 'What they are being sold, and on what terms'}>

      {/* WHICH MODE THIS DEAL IS IN, said once and plainly. A salesperson
          should never have to infer from a populated field whether they are
          selling the package or something they negotiated. */}
      <div className="sw-flex" style={{ justifyContent: 'space-between',
                                        alignItems: 'center', marginBottom: 12 }}>
        <span className={'sw-pill' + (isCustom ? ' is-custom' : '')}>
          {isCustom ? 'CUSTOM DEAL' : 'STANDARD PACKAGE'}
        </span>
        <button className="sw-btn" disabled={saving || !selected}
                onClick={() => setCustomizing(true)}
                title={selected ? 'Negotiate pricing for this deal'
                                : 'Choose a package first'}>
          CUSTOMIZE DEAL
        </button>
      </div>

      {/* An unapproved deal is NOT the deal. Until a manager agrees, the deal
          still says what it actually is, and this says what was asked for. */}
      {pending && (
        <div className="sw-notbuilt" style={{ borderColor: 'rgba(255,170,60,.45)' }}>
          <b>PRICING AWAITING APPROVAL</b>
          <p>
            {pending.requested_by_name || 'Someone'} asked for pricing below the
            approved floor{pending.reason ? ' — ' + pending.reason : ''}. The deal
            below still shows the current agreed terms; nothing changes until a
            manager decides.
          </p>
        </div>
      )}

      <div className="sw-grid-even">
        <div className="sw-field">
          <label>PACKAGE INTEREST</label>
          <select className="sw-select" value={interestId}
                  onChange={e => { setInterestId(e.target.value); onPatch({ package_interest_id: e.target.value || null }) }}>
            <option value="">Not yet known</option>
            {packages.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div className="sw-field">
          <label>SELECTED PACKAGE</label>
          <select className="sw-select" value={pkgId}
                  onChange={e => { setPkgId(e.target.value); onPatch({ selected_package_id: e.target.value || null }) }}>
            <option value="">None selected</option>
            {/* Every number here is LABELLED. A bare "$1,497" is exactly how a
                one-time implementation fee starts being read as a monthly rate,
                and "$500" as the normal price. */}
            {packages.map(p => {
              const pr = p.pricing || {}
              const bits = []
              if (pr.implementation_fee != null)
                bits.push('$' + Number(pr.implementation_fee).toLocaleString() + ' setup')
              if (pr.monthly_price != null)
                bits.push('$' + Number(pr.monthly_price).toLocaleString() + '/mo')
              if (pr.contract_monthly_price != null)
                bits.push('or $' + Number(pr.contract_monthly_price).toLocaleString()
                          + '/mo on ' + pr.contract_term_months + 'mo')
              return (
                <option key={p.id} value={p.id}>
                  {p.name}{bits.length ? ' · ' + bits.join(' + ') : ' · custom'}
                </option>
              )
            })}
          </select>
        </div>
      </div>

      {pricing && (
        <BillingOptions pricing={pricing} selected={billingOption} disabled={saving}
                        onChoose={opt => onPatch({ billing_option: opt })} />
      )}

      {/* Quoted for THIS customer. Deliberately not a catalogue edit: changing
          the package would move the setup fee for every deal referencing it. */}
      {selected && (
        <div className="sw-field">
          <label>IMPLEMENTATION FEE FOR THIS DEAL{' '}
            <span style={{ fontWeight: 400 }}>
              (blank = the package&rsquo;s {selected.pricing && selected.pricing.implementation_fee != null
                ? money(selected.pricing.implementation_fee) : 'none'})
            </span>
          </label>
          <div className="sw-flex">
            <input className="sw-input" type="number" step="0.01" value={fee}
                   placeholder="Use package default"
                   onChange={e => setFee(e.target.value)} />
            <button className="sw-btn" disabled={saving}
                    onClick={() => onPatch({
                      implementation_fee: fee === '' ? null : Number(fee) })}>
              Save fee
            </button>
          </div>
        </div>
      )}

      {/* DEMOTED, DELIBERATELY, AND RELABELLED FOR WHAT IT ACTUALLY IS.
          `deal_value` has always been the ONE-TIME implementation figure and
          nothing else. Leading a pricing card with it — under a heading that
          reads like the whole deal — is how a $1,497 setup fee gets reported as
          the value of a $7,997 contract. The commercial summary above is the
          headline now; this stays because pipeline reporting still reads the
          column, and it says what it means. */}
      <details className="sw-disclose">
        <summary>Legacy deal value (one-time figure used by pipeline reporting)</summary>
        <div className="sw-field" style={{ marginTop: 10 }}>
          <label>ONE-TIME DEAL VALUE {derived != null && (
            <span style={{ fontWeight: 400 }}>(derived {money(derived)})</span>
          )}</label>
          <input className="sw-input" type="number" step="0.01" value={value}
                 onChange={e => setValue(e.target.value)} />
          <div className="sw-subtle" style={{ marginTop: 5 }}>
            This is the implementation fee, not the contract value. Total
            contract value is in the summary above.
          </div>
        </div>
        {needsReason && (
          <div className="sw-field">
            <label>OVERRIDE REASON (REQUIRED)</label>
            <input className="sw-input" value={reason} onChange={e => setReason(e.target.value)}
                   placeholder="Why is this different from the package price?" />
            {!opp.can_override_value && (
              <div className="sw-subtle" style={{ marginTop: 6 }}>
                Only a sales manager can override the derived value.
              </div>
            )}
          </div>
        )}
        <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
          <button className="sw-btn" disabled={saving || (needsReason && !reason.trim())}
                  onClick={() => onPatch({
                    deal_value: value === '' ? null : Number(value),
                    deal_value_override_reason: reason.trim() || undefined,
                  })}>
            {saving ? 'Saving…' : 'Save value'}
          </button>
        </div>
      </details>

      {opp.deal_value_override && (
        <div className="sw-notbuilt sw-mt">
          <b>VALUE OVERRIDDEN</b>
          <p>
            {opp.deal_value_override_by_name || 'A manager'} set this value
            {opp.deal_value_override_at ? ' on ' + dateTime(opp.deal_value_override_at) : ''}.
            {opp.deal_value_override_reason ? ' Reason: ' + opp.deal_value_override_reason : ''}
          </p>
        </div>
      )}

      {/* Projected compensation on this deal. Absent — not zero — for anyone
          not permitted to see it; the server sends null and this renders
          nothing rather than implying the deal pays nobody. */}
      {opp.compensation && <DealCompensation comp={opp.compensation} />}

      {customizing && (
        <CustomizeDealDialog
          opp={opp} pkg={selected} saving={saving}
          onCancel={() => setCustomizing(false)}
          onSave={patch => { setCustomizing(false); onPatch(patch) }}
        />
      )}
    </Card>
  )
}


/* PROJECTED COMPENSATION — on an open deal this is a forecast, and it says so.
 * Never rendered as money owed: `earned` is a different state that only exists
 * once funds are collected, and this component cannot display that state at
 * all because the projection endpoint cannot produce it. */
function DealCompensation({ comp }) {
  if (comp.status === 'unconfigured') {
    return (
      <div className="sw-subtle sw-mt">
        No compensation plan is configured for this sales organization, so no
        commission can be projected.
      </div>
    )
  }
  const pending = comp.status === 'pending_approval'
  return (
    <div className="sw-billing-summary sw-mt">
      <div className="sw-field" style={{ marginBottom: 6 }}>
        <label style={{ margin: 0 }}>
          PROJECTED COMPENSATION
          {pending && <span className="sw-pill" style={{ marginLeft: 8 }}>PENDING APPROVAL</span>}
        </label>
      </div>
      <Row label="Salesperson" value={money(comp.seller_total)} />
      {Number(comp.override_total) > 0 && (
        <Row label="Manager / upline overrides" value={money(comp.override_total)} />
      )}
      <Row label="Total projected" value={money(comp.total)} primary />
      {comp.capped && (
        <div className="sw-subtle" style={{ marginTop: 6 }}>
          Reduced to the {money(comp.cap_amount)} payout cap on this package.
        </div>
      )}
      <div className="sw-subtle" style={{ marginTop: 6 }}>
        {pending
          ? 'This deal is priced below the approved floor. Nothing is owed, and '
            + 'these figures change if a manager alters the terms.'
          : 'Projected on current terms. Not earned and not payable — commission '
            + 'is earned when the deal is won and the first payment is collected.'}
      </div>
    </div>
  )
}

function Row({ label, value, primary }) {
  return (
    <div className={'sw-billing-row' + (primary ? ' is-primary' : '')}>
      <span>{label}</span><b>{value ?? '—'}</b>
    </div>
  )
}

export default function OpportunityDetail() {
  const { oppId } = useParams()
  const nav = useNavigate()
  const [opp, setOpp] = useState(null)
  const [packages, setPackages] = useState([])
  const [team, setTeam] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  const [nextAction, setNextAction] = useState('')
  const [nextDue, setNextDue] = useState('')
  const [finding, setFinding] = useState(false)
  // The appointment being moved, or null. Holds the whole object rather than an
  // id because the reschedule dialog needs its participants and duration to run
  // the same shared-availability search the original booking used.
  const [moving, setMoving] = useState(null)
  // The closing projection, fetched ONCE here and handed to ClosingPanel rather
  // than fetched twice: the command area needs the proposal state to decide
  // what the next action is, and that is the same call.
  const [closing, setClosing] = useState(null)
  // Which stage sections are expanded. Seeded from the deal's own stage.
  const [open, setOpen] = useState([])
  // The deal whose stage last decided which section is open.
  const seededOpen = useRef(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const o = await api.get('/sales/opportunities/' + oppId)
      setOpp(o)
      setNextAction(o.next_action || '')
      setNextDue(o.next_action_due_at ? String(o.next_action_due_at).slice(0, 10) : '')
    } catch (e) {
      setError(e.message || 'Could not load this opportunity.')
    } finally { setLoading(false) }
  }, [oppId])

  const loadClosing = useCallback(async () => {
    try { setClosing(await api.get('/sales/opportunities/' + oppId + '/closing')) }
    catch { setClosing(null) }
  }, [oppId])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadClosing() }, [loadClosing])

  /* CAN THIS DEAL BE PRESENTED FROM, AND WITH WHAT? The server answers — it
     knows the brand, the entitlement and the prospect's name, and a browser
     working any of those out would be a browser deciding its own access. A
     failure here leaves the button absent rather than breaking the page. */
  const [demoLaunch, setDemoLaunch] = useState(null)
  useEffect(() => {
    let alive = true
    api.get('/sales/opportunities/' + oppId + '/demo-launch')
      .then(d => { if (alive) setDemoLaunch(d) })
      .catch(() => { if (alive) setDemoLaunch(null) })
    return () => { alive = false }
  }, [oppId])
  // Checkpoint 6 §15 — what happened after Won. A coarse, read-only projection
  // assembled server-side; this component never sees tenant data and could not
  // display it if it wanted to. Failing quietly is right: an opportunity that
  // was never won has nothing to show, and that is not an error worth a banner.
  const [postWon, setPostWon] = useState(null)
  // RECORD card edit mode. Lives here so the Edit control can sit in the card
  // header next to Reassign, where both actions on this card are together.
  const [editingRecord, setEditingRecord] = useState(false)
  const [recordSaved, setRecordSaved] = useState(false)
  useEffect(() => {
    api.get('/sales/packages').then(setPackages).catch(() => setPackages([]))
    api.get('/sales/team').then(setTeam).catch(() => setTeam([]))
  }, [])
  useEffect(() => {
    api.get('/sales/opportunities/' + oppId + '/implementation')
      .then(setPostWon).catch(() => setPostWon(null))
  }, [oppId])

  // Open the section the deal is actually in — once per DEAL. Re-opening it on
  // every refresh would slam a section shut under someone's cursor; keying the
  // seed on the id rather than on "have I run yet" is what makes navigating
  // from one deal to the next open the right section for the NEW one, instead
  // of inheriting whatever was open on the last one.
  const currentSection = opp ? (SECTION_FOR_STAGE[opp.stage] || 'discovery') : null
  useEffect(() => {
    if (!opp || seededOpen.current === opp.id) return
    seededOpen.current = opp.id
    setOpen([SECTION_FOR_STAGE[opp.stage] || 'discovery'])
  }, [opp])

  function toggle(id) {
    setOpen(o => (o.indexOf(id) === -1 ? o.concat([id]) : o.filter(x => x !== id)))
  }

  function openSection(id) {
    setOpen(o => (o.indexOf(id) === -1 ? o.concat([id]) : o))
    // Let the section render before scrolling to it.
    setTimeout(() => {
      const el = document.getElementById('sec-' + id)
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 30)
  }

  async function patch(body) {
    setSaving(true); setError(null)
    try {
      setOpp(await api.patch('/sales/opportunities/' + oppId, body))
      loadClosing()
    } catch (e) { setError(e.message || 'Save failed.') }
    finally { setSaving(false) }
  }

  /* The same write, but it lets the failure through instead of swallowing it
     into the page banner — the RECORD card shows the server's refusal next to
     the fields that caused it, where the person can act on it. */
  async function patchOrThrow(body) {
    setSaving(true); setError(null)
    try { setOpp(await api.patch('/sales/opportunities/' + oppId, body)) }
    finally { setSaving(false) }
  }

  async function saveDiscovery(structured, complete) {
    setSaving(true); setError(null)
    try {
      setOpp(await api.put('/sales/opportunities/' + oppId + '/discovery',
                           { structured, mark_complete: !!complete }))
    }
    catch (e) { setError(e.message || 'Save failed.') }
    finally { setSaving(false) }
  }

  async function confirmAppt(id) {
    setSaving(true); setError(null)
    try {
      await api.post('/sales/appointments/' + id + '/confirmation',
                     { confirmation_status: 'confirmed', source: 'staff_manual' })
      await load()
    } catch (e) { setError(e.message || 'Could not confirm.') }
    finally { setSaving(false) }
  }

  async function cancelAppt(id) {
    setSaving(true); setError(null)
    try {
      await api.post('/sales/appointments/' + id + '/cancel', {})
      await load()
    } catch (e) { setError(e.message || 'Could not cancel.') }
    finally { setSaving(false) }
  }

  async function addNote() {
    if (!note.trim()) return
    setSaving(true)
    try { await api.post('/sales/opportunities/' + oppId + '/notes', { summary: note.trim() }); setNote(''); await load() }
    catch (e) { setError(e.message || 'Could not add the note.') }
    finally { setSaving(false) }
  }

  /* One dispatcher for the command area, so "what the next action is" is
     decided in one place (DealCommand.primaryAction) and DONE in one place. */
  function onAction(a) {
    if (!a) return
    if (a.type === 'stage') { patch({ stage: a.stage }); openSection(SECTION_FOR_STAGE[a.stage] || 'discovery'); return }
    if (a.type === 'open') { openSection(a.section); return }
    // Some next actions are a different SCREEN, not a section of this one —
    // the demo build is the first. Routing is handled here for the same reason
    // every other action is: one dispatcher, so no component has to know how
    // navigation works on this page.
    if (a.type === 'route') { nav(a.to); return }
    if (a.type === 'meeting') { setFinding(true) }
  }

  const alerts = useMemo(() => {
    if (!opp) return []
    const out = []
    if (opp.attention) out.push({ tone: 'amber', text: opp.attention })
    const warn = (closing && closing.warnings || []).filter(w => w.level)
    const red = warn.filter(w => w.level === 'red').length
    if (warn.length) {
      out.push({ tone: red ? 'red' : 'amber',
                 text: warn.length + ' to deal with before this closes' })
    }
    const p = opp.discovery && opp.discovery.progress
    if (p && !p.complete && SECTION_FOR_STAGE[opp.stage] === 'discovery') {
      out.push({ tone: 'amber',
                 text: 'Discovery ' + p.answered + '/' + p.required + ' — '
                       + (p.missing || []).slice(0, 3).map(m => m.label).join(', ') })
    }
    return out.slice(0, 3)
  }, [opp, closing])

  if (loading && !opp) {
    return <SalesShell title="Opportunity"><div className="sw-subtle">Loading…</div></SalesShell>
  }
  if (!opp) {
    return (
      <SalesShell title="Opportunity">
        <ErrorBar error={error} onRetry={load} />
        <div className="sw-card">
          <Empty title="Not available">
            This opportunity does not exist, or it belongs to another representative.
          </Empty>
        </div>
      </SalesShell>
    )
  }

  const due = dueLabel(opp.next_action_due_at)
  const d = opp.demo || {}
  const disc = opp.discovery || {}
  const prog = disc.progress || { answered: 0, required: 0, complete: false }
  const prop = closing && closing.proposal
  const curIdx = SECTION_ORDER.indexOf(currentSection)
  const stateOf = id => {
    const i = SECTION_ORDER.indexOf(id)
    if (i < curIdx) return 'done'
    if (i === curIdx) return 'current'
    return 'upcoming'
  }
  const isOpen = id => open.indexOf(id) !== -1

  const nextMeetingLabel = (opp.appointments || []).length
    ? wallDateTime(opp.appointments[0].starts_at_local || opp.appointments[0].starts_at)
      + ' · ' + (opp.appointments[0].meeting_type || 'Meeting')
    : 'Nothing booked'

  const demoSummary = d.status
    ? [d.status.replace('_', ' '), d.owner_name, d.due_at ? 'due ' + dateTime(d.due_at) : null]
        .filter(Boolean).join(' · ')
    : 'Not requested'

  const proposalSummary = prop
    ? [prop.proposal_number, prop.status_label,
       prop.amount != null ? money(prop.amount) : null].filter(Boolean).join(' · ')
    : (opp.selected_package ? opp.selected_package.name + ' · no proposal yet'
                            : 'No package selected')

  const closingSummary = closing
    ? ((closing.warnings || []).filter(w => w.level).length
        ? (closing.warnings || []).filter(w => w.level).length + ' to deal with'
        : 'Nothing outstanding')
    : 'Loading…'

  const billingSummary = opp.customer_organization_id
    ? 'Customer provisioned · setup and subscription'
    : (opp.status === 'won' ? 'Won — awaiting provisioning'
                            : 'Setup fee and subscription are billed separately')

  return (
    <SalesShell
      title={opp.company_name}
      subtitle={[opp.contact_name, opp.industry].filter(Boolean).join(' · ') || 'Opportunity record'}
      actions={
        <>
          <button className="sw-btn" onClick={() => nav('/sales/pipeline')}>← Pipeline</button>
          {/* RUN DEMO — the entry point that was missing. A salesperson should
              never have to copy a demo URL, open God Mode or hunt through the
              Suite to present the deal they are already looking at. The
              opportunity already knows the brand, the prospect and the
              presenter; the button carries the deal's id so the Suite can put
              the prospect's name in the header and EXIT DEMO can come back
              here. Absent, not disabled, when the server says this person
              cannot present this brand. */}
          {demoLaunch?.eligible ? (
            <button className="sw-btn sw-primary"
                    title={'Present ' + (demoLaunch.brand_name || 'the product')
                           + ' to ' + (opp.company_name || 'this prospect')}
                    onClick={() => nav('/demo-suite/' + demoLaunch.platform_id
                                       + '?opportunity=' + encodeURIComponent(opp.id)
                                       + '&mode=present')}>
              Run demo
            </button>
          ) : null}
          <button className="sw-btn" onClick={() => { load(); loadClosing() }} disabled={loading}>
            Refresh
          </button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      <DealCommand
        opp={opp}
        closing={closing}
        nextMeeting={nextMeetingLabel}
        alerts={alerts}
        saving={saving}
        onAction={onAction}
        stageControl={<StageMover opp={opp} onMove={s => patch({ stage: s })} saving={saving} />}
      />

      {finding && (
        <FindTeamTime
          opportunity={opp}
          onClose={() => setFinding(false)}
          onBooked={() => { setFinding(false); load() }}
        />
      )}

      <div className="sw-mt sw-grid2">
        <div>
          {/* ── THE DEAL, IN THE ORDER IT IS ACTUALLY SOLD ────────────────── */}

          <StageSection
            id="discovery" step={1} title="DISCOVERY" state={stateOf('discovery')}
            open={isOpen('discovery')} onToggle={toggle}
            summary={disc.completed_at
              ? 'Completed ' + dateTime(disc.completed_at)
              : prog.answered + '/' + prog.required + ' captured'}
            badge={<Chip tone={prog.complete ? 'green' : (prog.answered ? 'amber' : null)}>
              {prog.answered}/{prog.required}
            </Chip>}
          >
            <DiscoveryPanel opp={opp} onSave={saveDiscovery} saving={saving} />
          </StageSection>

          <StageSection
            id="demo" step={2} title="DEMO" state={stateOf('demo')}
            open={isOpen('demo')} onToggle={toggle}
            summary={demoSummary}
            badge={d.status === 'ready' || d.status === 'delivered'
              ? <Chip tone="green">Ready</Chip>
              : (d.status ? <Chip tone="amber">{d.status.replace('_', ' ')}</Chip> : null)}
          >
            <DemoPanel opp={opp} team={team} onPatch={patch} saving={saving}
                       onRequest={() => patch({ stage: 'demo_build' })} />
            {/* Directly under the demo panel, because publishing the demo is
                the step that panel has been tracking. Publishing a platform
                walkthrough fills in DEMO URL and flips the status to ready on
                the server, so `load` refreshes the panel above. */}
            <div className="sw-mt"><DemoSitesPanel opp={opp} onChanged={load} /></div>
          </StageSection>

          <StageSection
            id="proposal" step={3} title="PROPOSAL" state={stateOf('proposal')}
            open={isOpen('proposal')} onToggle={toggle}
            summary={proposalSummary}
            badge={prop ? <Chip tone={prop.status === 'accepted' ? 'green' : 'amber'}>
              {prop.status_label}</Chip> : null}
          >
            <PackageDeal opp={opp} packages={packages} onPatch={patch} saving={saving} />
            <div className="sw-mt">
              <ProposalPanel opp={opp} packages={packages}
                             onChanged={() => { load(); loadClosing() }} />
            </div>
          </StageSection>

          <StageSection
            id="closing" step={4} title="CLOSING" state={stateOf('closing')}
            open={isOpen('closing')} onToggle={toggle}
            summary={closingSummary}
            badge={closing && (closing.warnings || []).filter(w => w.level).length
              ? <Chip tone="amber">
                  {(closing.warnings || []).filter(w => w.level).length}
                </Chip>
              : <Chip tone="green">Clear</Chip>}
          >
            <ClosingPanel opp={opp} data={closing} />
          </StageSection>

          <StageSection
            id="billing" step={5} title="BILLING & HANDOFF" state={stateOf('billing')}
            open={isOpen('billing')} onToggle={toggle}
            summary={billingSummary}
          >
            {/* THE BILLING ENGINE IS NOT TOUCHED HERE. Setup / implementation
                and the monthly subscription remain two separate obligations,
                resolved and rendered by DealBillingPanel exactly as it does
                everywhere else. This section only decides WHERE it appears. */}
            <DealBillingPanel opp={opp} />

            {postWon && postWon.provisioned ? (
              <Card title="AFTER THE SALE" sub="What happened once this was won">
                <div className="sw-infogrid">
                  <Info label="CUSTOMER ORGANISATION"
                        value={postWon.customer_organization_name} />
                  <Info label="STATUS" value={postWon.status_label} />
                  <Info label="IMPLEMENTATION OWNER"
                        value={postWon.implementation_owner || 'unassigned'} />
                  <Info label="ONBOARDING" value={postWon.percent_complete + '%'} />
                  <Info label={postWon.is_live ? 'LAUNCHED' : 'TARGET LAUNCH'}
                        value={postWon.is_live
                          ? 'Live'
                          : (postWon.target_launch_date
                             ? new Date(postWon.target_launch_date).toLocaleDateString()
                             : 'not set')} />
                </div>
                {postWon.is_blocked ? (
                  <p className="sw-subtle" style={{ margin: '10px 0 0' }}>
                    This implementation is currently blocked. The implementation
                    owner has the detail.
                  </p>
                ) : null}
                <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
                  <button className="sw-btn sw-primary" onClick={() => nav('/sales/onboarding')}>
                    View all implementations →
                  </button>
                </div>
              </Card>
            ) : postWon && postWon.is_won ? (
              <Card title="AFTER THE SALE">
                <p className="sw-subtle" style={{ margin: 0 }}>
                  Won — awaiting provisioning. A customer organisation is created
                  deliberately, not automatically.
                </p>
              </Card>
            ) : null}
          </StageSection>
        </div>

        {/* ── THE RAIL ──────────────────────────────────────────────────────
            Who they are, when you are seeing them, and what you owe them next.
            Everything historical is one click away rather than on the page. */}
        <div>
          <Card
            title="CUSTOMER"
            /* The reassign control sits on the header of the card that already
               shows SALES OWNER, so the fact and the action are in one place.
               `can_reassign` is the server's own per-record answer; the button
               does not exist for anyone else, and the endpoint refuses them
               regardless. */
            right={
              <>
                {!editingRecord && (
                  <button className="sw-btn" title="Edit company, contact and contact details"
                          onClick={() => { setRecordSaved(false); setEditingRecord(true) }}>
                    ✎ Edit
                  </button>
                )}
                <ReassignControl
                  opportunityId={opp.id}
                  canReassign={opp.can_reassign}
                  currentOwnerId={opp.owner_user_id}
                  currentOwnerName={opp.owner_name}
                  onReassigned={load}
                />
              </>
            }
          >
            <RecordIdentity
              opp={opp}
              editing={editingRecord}
              saved={recordSaved}
              onSave={patchOrThrow}
              onSaved={() => { setEditingRecord(false); setRecordSaved(true) }}
              onCancel={() => setEditingRecord(false)}
            />

            <div className="sw-grid-even sw-mt">
              <div className="sw-field">
                <label>NEXT ACTION</label>
                <input className="sw-input" value={nextAction}
                       onChange={e => setNextAction(e.target.value)} />
              </div>
              <div className="sw-field">
                <label>DUE {due.text && <Chip tone={due.tone}>{due.text}</Chip>}</label>
                <input className="sw-input" type="date" value={nextDue}
                       onChange={e => setNextDue(e.target.value)} />
              </div>
            </div>
            <div className="sw-flex" style={{ justifyContent: 'flex-end', marginTop: 10 }}>
              <button className="sw-btn" disabled={saving}
                      onClick={() => patch({
                        next_action: nextAction,
                        next_action_due_at: nextDue ? new Date(nextDue).toISOString() : null,
                      })}>Save next action</button>
            </div>
          </Card>

          <div className="sw-mt">
            <Meetings opp={opp} saving={saving}
                      onFind={() => setFinding(true)}
                      onConfirm={confirmAppt} onCancel={cancelAppt}
                      onMove={setMoving} />
          </div>

          {moving && (
            <RescheduleDialog appt={moving}
                              onClose={() => setMoving(null)}
                              onDone={load} />
          )}

          <div className="sw-mt">
            <Card title="ADD TO TIMELINE" sub="Logged against this record, permanently">
              <div className="sw-field">
                <input className="sw-input" value={note} placeholder="What happened?"
                       onChange={e => setNote(e.target.value)}
                       onKeyDown={e => { if (e.key === 'Enter') addNote() }} />
              </div>
              <div className="sw-flex" style={{ justifyContent: 'flex-end', marginTop: 10 }}>
                <button className="sw-btn sw-primary" onClick={addNote}
                        disabled={saving || !note.trim()}>Add note</button>
              </div>
            </Card>
          </div>

          {/* History and calendar plumbing: real, kept, and not in the way. */}
          <div className="sw-card sw-mt">
            <details className="sw-disclose is-flush">
              <summary>Activity ({(opp.timeline || []).length})</summary>
              {opp.timeline?.length
                ? <div className="sw-timeline" style={{ marginTop: 12 }}>
                    {opp.timeline.map(e => (
                      <div className="sw-event" key={e.id}>
                        <b>{e.summary}</b>
                        <p>
                          {dateTime(e.occurred_at)}
                          {e.actor_name ? ' · ' + e.actor_name : ''}
                          {e.detail ? ' · ' + e.detail : ''}
                        </p>
                      </div>
                    ))}
                  </div>
                : <p className="sw-subtle" style={{ marginTop: 10 }}>No activity yet.</p>}
            </details>
            <details className="sw-disclose is-flush">
              <summary>Lifecycle</summary>
              <div style={{ marginTop: 12 }}><Lifecycle opp={opp} /></div>
              {opp.customer_organization_id
                ? <div className="sw-subtle sw-mt" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span>Customer organization provisioned.</span>
                    <button className="sw-btn" style={{ padding: '2px 10px', fontSize: 12 }}
                            onClick={() => nav('/sales/onboarding')}>
                      View onboarding →
                    </button>
                  </div>
                : <div className="sw-subtle sw-mt">
                    No customer organization yet — provisioning happens when the deal is Won.
                  </div>}
            </details>
            <details className="sw-disclose is-flush">
              <summary>Calendar sync</summary>
              <div style={{ marginTop: 12 }}>
                <ApptSyncPanel opp={opp} onChanged={load} />
              </div>
            </details>
          </div>
        </div>
      </div>
    </SalesShell>
  )
}

/** Stage control. Options come from /sales/me so the client never hardcodes the lifecycle. */
function StageMover({ opp, onMove, saving }) {
  const [stages, setStages] = useState([])
  useEffect(() => {
    api.get('/sales/me').then(me => setStages(me.stages || [])).catch(() => setStages([]))
  }, [])
  return (
    <select className="sw-select" style={{ width: 180 }} value={opp.stage}
            disabled={saving || !stages.length}
            onChange={e => onMove(e.target.value)}>
      {stages.length
        ? stages.map(s => <option key={s.key} value={s.key}>{s.label}</option>)
        : <option value={opp.stage}>{opp.stage_label}</option>}
      <option value="lost">Lost</option>
    </select>
  )
}
