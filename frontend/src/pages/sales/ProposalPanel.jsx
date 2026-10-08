/**
 * The Proposal panel on an Opportunity — create, edit, price, publish, send,
 * and watch what the buyer does with it.
 *
 * BUILT FOR A SALESPERSON, NOT A DEVELOPER. Everything AdvisorFlow already
 * knows is prefilled by the server, so this is a page for editing prose and
 * pressing Send — not a form for re-entering the company name.
 *
 * PRICING AUTHORITY IS SERVER-SIDE. The discount field is hidden from a rep
 * because `can_override_price` says so, but hiding it is a courtesy, not the
 * control — the API refuses a rep's discount regardless of what this renders.
 */
import { useEffect, useState, useCallback } from 'react'
import { api, API_BASE } from '../../api/client'
import { Card, Chip, Empty, ErrorBar, dateTime } from './parts'
import {
  formatCents, validateAdjustment, validateCustomRate, totalAfter,
  refusalMessage, refusalKind,
} from '../../utils/proposalMoney.js'

const STATUS_TONE = {
  draft: null, internal_review: null, ready: 'blue', sent: 'blue',
  viewed: 'amber', accepted: 'green', declined: 'red',
  change_requested: 'amber', expired: 'red', superseded: null,
}

const SECTIONS = [
  ['executive_summary', 'OVERVIEW', 'A short framing the customer reads first'],
  ['business_need', 'THE SITUATION TODAY', 'What you heard in discovery'],
  ['objectives', 'OBJECTIVES', 'What they want to be true afterwards'],
  ['recommended_solution', 'RECOMMENDED SOLUTION', 'What you are proposing'],
  ['scope', 'SCOPE', "What's included"],
  ['deliverables', 'DELIVERABLES', 'What they actually receive'],
  ['implementation_plan', 'IMPLEMENTATION', 'How it gets done'],
  ['terms', 'TERMS', 'Commercial terms'],
]

// Money renders from integer cents, always with cents, never via Intl/float
// totals. `cents` (the server's *_cents field) wins; a legacy dollar value is
// converted once, by rounding to the cent.
function money(v, cur, cents) {
  if (Number.isInteger(cents)) return formatCents(cents, cur)
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  return formatCents(Math.round(Number(v) * 100), cur)
}


import { BillingOptions } from './BillingOptions.jsx'

/**
 * The recurring rate agreed on THIS deal.
 *
 * A package named "Custom" exists because its price is not in the catalogue.
 * Until this control existed, that meant a custom deal could not state a
 * monthly figure or a term anywhere — the panel simply reported that no rate
 * was configured and offered no way to configure one.
 *
 * Priced per unit rather than as a flat number so the BASIS is recorded, not
 * just the total: "$250 per active paying customer, 15 minimum" is $3,750 a
 * month AND the arithmetic behind it, which is what the customer's document
 * needs to print. Leave the unit blank for a genuinely flat rate.
 */
function CustomRate({ rate, currency, canEdit, disabled, onSave, onClear }) {
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ unit: '', label: '', min: '', term: '' })

  const begin = () => {
    setF({
      unit: rate?.unit_price != null ? String(rate.unit_price) : '',
      label: rate?.unit_label || '',
      min: rate?.min_units != null ? String(rate.min_units) : '1',
      term: rate?.term_months != null ? String(rate.term_months) : '',
    })
    setOpen(true)
  }

  // Shown live while typing, so nobody has to save to find out what the deal
  // actually costs per month.
  const v = validateCustomRate(f)
  const termN = v.ok && v.fields.custom_term_months ? v.fields.custom_term_months : 0

  if (!open) {
    return (
      <div className="sw-billing-summary" style={{ marginTop: 10 }}>
        {rate ? (
          <>
            <div className="sw-billing-row is-primary">
              <span>MONTHLY PLATFORM</span>
              <b>{money(rate.monthly_rate, currency, rate.monthly_rate_cents)}/mo</b>
            </div>
            {rate.basis && (
              <div className="sw-billing-row">
                <span>Basis</span><b>{rate.basis}</b>
              </div>
            )}
            <div className="sw-billing-row">
              <span>Term</span>
              <b>{rate.term_months
                ? rate.term_months + ' months · all ' + rate.term_months + ' payments required'
                : 'No term commitment'}</b>
            </div>
          </>
        ) : (
          <div style={{ padding: '2px 0 8px' }}>
            <b style={{ fontSize: 12, letterSpacing: '.4px' }}>NO RECURRING RATE YET</b>
            <p className="sw-subtle" style={{ margin: '4px 0 0', fontSize: 12 }}>
              This package is priced per deal. Set the rate this customer agreed
              to — it is what the proposal will quote.
            </p>
          </div>
        )}
        {canEdit && (
          <div className="sw-flex" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 8 }}>
            {rate && (
              <button className="sw-tiny" disabled={disabled} onClick={onClear}>Clear</button>
            )}
            <button className="sw-tiny sw-primary" disabled={disabled} onClick={begin}>
              {rate ? 'Edit rate' : 'Set custom rate'}
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ background: '#f8fafc', border: '1px solid #e5e7eb',
                  borderRadius: 8, padding: 12, marginTop: 10 }}>
      <div className="sw-subtle" style={{ marginBottom: 8 }}>
        Custom rate — manager only, and recorded on the deal timeline.
      </div>
      <div className="sw-grid-even">
        <div className="sw-field">
          <label>RATE PER UNIT / MONTH</label>
          <input className="sw-input" inputMode="decimal" value={f.unit}
                 placeholder="250"
                 onChange={e => setF({ ...f, unit: e.target.value })} />
        </div>
        <div className="sw-field">
          <label>UNIT IS CALLED</label>
          <input className="sw-input" value={f.label}
                 placeholder="active paying customer"
                 onChange={e => setF({ ...f, label: e.target.value })} />
        </div>
        <div className="sw-field">
          <label>MINIMUM UNITS</label>
          <input className="sw-input" type="number" min="1" step="1" value={f.min}
                 placeholder="15"
                 onChange={e => setF({ ...f, min: e.target.value })} />
        </div>
        <div className="sw-field">
          <label>TERM (MONTHS)</label>
          <input className="sw-input" type="number" min="0" step="1" value={f.term}
                 placeholder="13 — leave blank for no commitment"
                 onChange={e => setF({ ...f, term: e.target.value })} />
        </div>
      </div>

      {!v.ok && (f.unit !== '' || f.term !== '') && (
        <div role="alert" style={{ color: '#b91c1c', fontSize: 12, marginTop: 8 }}>{v.error}</div>
      )}
      {v.ok && (
        <div className="sw-billing-summary" style={{ marginTop: 10 }}>
          <div className="sw-billing-row is-primary">
            <span>MONTHLY PLATFORM</span>
            <b>{formatCents(v.monthlyCents, currency)}/mo</b>
          </div>
          {termN > 0 && (
            <div className="sw-billing-row">
              <span>{termN}-month platform commitment</span>
              <b>{formatCents(v.commitmentCents, currency)}</b>
            </div>
          )}
        </div>
      )}
      <p className="sw-subtle" style={{ margin: '8px 0 0', fontSize: 12 }}>
        Leave the unit name blank for a flat monthly rate. Leaving the term blank
        quotes month-to-month with no commitment.
      </p>

      <div className="sw-flex" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
        <button className="sw-btn" disabled={disabled} onClick={() => setOpen(false)}>Cancel</button>
        <button className="sw-btn sw-primary" disabled={disabled || !v.ok}
                onClick={async () => {
                  // Closes only when the server accepted it; a refusal leaves
                  // the form open beside the error.
                  const saved = await onSave({
                    ...v.fields,
                    custom_unit_label: f.label.trim() || null,
                  })
                  if (saved) setOpen(false)
                }}>Save rate</button>
      </div>
    </div>
  )
}

export default function ProposalPanel({ opp, packages = [], onChanged }) {
  const [data, setData] = useState(null)
  const [current, setCurrent] = useState(null)
  const [draft, setDraft] = useState({})
  const [activity, setActivity] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [reason, setReason] = useState('')
  // A rep's request for a price they cannot set (Checkpoint 5).
  const [askReason, setAskReason] = useState('')
  const [askAdj, setAskAdj] = useState('')
  const [adjText, setAdjText] = useState('')
  const [resource, setResource] = useState({ block_type: 'website_url', content: '', file_url: '' })

  const load = useCallback(async () => {
    setError(null)
    try {
      const r = await api.get('/sales/opportunities/' + opp.id + '/proposals')
      setData(r)
      const cur = (r.proposals || []).find(p => p.id === r.current_id) || null
      if (cur) {
        const full = await api.get('/sales/proposals/' + cur.id)
        setCurrent(full)
        setDraft(SECTIONS.reduce((acc, [k]) => ({ ...acc, [k]: full[k] || '' }), {}))
        try {
          setActivity(await api.get('/sales/proposals/' + cur.id + '/activity'))
        } catch { setActivity(null) }
      } else {
        setCurrent(null); setActivity(null)
      }
    } catch (e) { setError(e.message || 'Could not load proposals.') }
  }, [opp.id])

  useEffect(() => { load() }, [load])

  // Resolves true only when the server accepted the change. A refusal never
  // sets a success note; a 409 reloads so the screen shows the current price
  // while the refusal text stays visible.
  async function act(fn, okMsg) {
    setBusy(true); setError(null); setNote(null)
    try {
      await fn()
      if (okMsg) setNote(okMsg)
      await load()
      if (onChanged) await onChanged()
      return true
    } catch (e) {
      const msg = refusalMessage(e)
      if (refusalKind(e) === 'conflict') await load()
      setError(msg)
      return false
    } finally { setBusy(false) }
  }

  // Every proposal edit carries the updated_at it was made against, so a stale
  // screen cannot overwrite a newer price (server answers 409).
  const patchProposal = fields => api.patch('/sales/proposals/' + current.id,
    { ...fields, expected_updated_at: current.updated_at })

  const create = () => act(
    () => api.post('/sales/proposals', { opportunity_id: opp.id }),
    'Proposal created and prefilled from this opportunity.')

  const saveSections = () => act(
    () => patchProposal(draft),
    'Saved.')

  const publish = () => act(
    () => api.post('/sales/proposals/' + current.id + '/publish', {}),
    'Published to the deal room. Nothing has been sent yet.')

  const send = () => act(
    () => api.post('/sales/proposals/' + current.id + '/send', {}),
    'Sent. The customer now has a secure link.')

  // The customer's own view, with no email and no trace on the deal that the
  // customer opened anything. The server's dry run publishes and mints a real
  // key but sends nothing, so what you read here is exactly what they would
  // read - not a mock of it.
  //
  // The key is short-lived on purpose: a preview link is a live door into the
  // pricing, and one pasted into a chat should not still open next week.
  const previewAsCustomer = () => {
    // Opened synchronously, before any await, or the browser treats it as a
    // popup and blocks it.
    const w = window.open('', '_blank')
    act(async () => {
      try {
        const r = await api.post('/sales/proposals/' + current.id + '/send',
                                 { dry_run: true, valid_hours: 4 })
        if (!r.portal_url) throw new Error('No preview link came back.')
        if (w) w.location = r.portal_url
        else window.location.href = r.portal_url
      } catch (e) {
        if (w) w.close()
        throw e
      }
    }, 'Opened the customer view in a new tab. Nothing was sent, and the preview key expires in 4 hours.')
  }

  const newVersion = () => act(
    () => api.post('/sales/proposals/' + current.id + '/version', {}),
    'Version created. The previous one is kept as superseded.')

  const revoke = () => act(
    () => api.post('/sales/proposals/' + current.id + '/revoke-access', {}),
    'Every live link for this proposal has been revoked.')

  /* The placeholder option is not a package. Sending its empty value asked the
     server to look up a package with no id, which answered — correctly, but
     unhelpfully — "That package does not exist." A proposal always has a
     package once one is chosen; there is no "unchoose". */
  const setPackage = pid => {
    if (!pid) return
    return act(() => patchProposal({ package_id: pid }))
  }

  const saveCustomRate = fields => act(
    () => patchProposal(fields),
    'Custom rate saved. It is on the deal timeline.')

  const clearCustomRate = () => act(
    () => patchProposal({ clear_custom_rate: true }),
    'Custom rate cleared.')

  const applyDiscount = text => {
    const v = validateAdjustment(text)
    if (!v.ok) { setNote(null); setError(v.error); return }
    const t = totalAfter(current.base_amount_cents, v.cents)
    if (current.base_amount_cents != null && !t.ok) { setNote(null); setError(t.error); return }
    return act(async () => {
      await patchProposal({ adjustment: v.cents / 100, price_reason: reason })
      setAdjText('')
    }, 'Pricing updated.')
  }

  // Asks. Does not set a price — the manager's decision does that.
  const askForPrice = () => {
    const v = validateAdjustment(askAdj)
    if (!v.ok) { setNote(null); setError(v.error); return }
    return act(async () => {
      await api.post('/sales/proposals/' + current.id + '/pricing-request',
                     { requested_adjustment: v.cents / 100, reason: askReason })
      setAskReason(''); setAskAdj('')
    }, 'Sent to your manager. Nothing on the proposal has changed yet.')
  }

  const withdrawAsk = () => act(
    () => api.post('/sales/proposals/' + current.id + '/pricing-request/withdraw', {}),
    'Request withdrawn.')

  const addResource = () => act(async () => {
    await api.post('/sales/proposals/' + current.id + '/blocks', resource)
    setResource({ block_type: 'website_url', content: '', file_url: '' })
  }, 'Added to the deal room.')

  const removeBlock = id => act(
    () => api.delete('/sales/proposals/' + current.id + '/blocks/' + id))

  /**
   * Upload a document, deck or image into the deal room.
   *
   * Sent as multipart via fetch rather than the JSON api client, and the
   * Content-Type header is deliberately NOT set — the browser must add its own
   * multipart boundary, and setting it by hand produces a request the server
   * cannot parse.
   */
  async function upload(fileList) {
    const f = fileList && fileList[0]
    if (!f) return
    setBusy(true); setError(null); setNote(null)
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('label', f.name)
      const token = localStorage.getItem('af_token')
      const r = await fetch(API_BASE + '/sales/proposals/' + current.id + '/upload', {
        method: 'POST',
        headers: token ? { Authorization: 'Bearer ' + token } : {},
        body: fd,
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(body.detail || 'That upload did not work.')
      setNote('Added to the deal room.')
      await load()
      if (onChanged) await onChanged()
    } catch (e) { setError(e.message || 'That upload did not work.') }
    finally { setBusy(false) }
  }


  if (!data) {
    return <Card title="PROPOSAL"><div className="sw-subtle">Loading…</div></Card>
  }

  if (!current) {
    return (
      <Card title="PROPOSAL"
            sub="Built from this opportunity — you should not have to retype anything"
            right={<button className="sw-btn sw-primary" onClick={create} disabled={busy}>
              {busy ? 'Creating…' : 'Create proposal'}</button>}>
        <ErrorBar error={error} />
        <Empty title="No proposal yet">
          <b>Create proposal</b> pulls in the company, the contact, the selected
          package and its price, and turns the discovery answers into a first
          draft. You edit the wording, not the data.
        </Empty>
      </Card>
    )
  }

  const p = current
  const adjCheck = validateAdjustment(adjText)
  const adjTotal = adjCheck.ok && p.base_amount_cents != null
    ? totalAfter(p.base_amount_cents, adjCheck.cents) : null
  const adjPreview = adjTotal ? (adjTotal.ok ? formatCents(adjTotal.cents, p.currency) : adjTotal.error) : '—'
  const history = (data.proposals || []).filter(x => x.id !== p.id)
  const sent = !!p.sent_at

  return (
    <Card
      title={'PROPOSAL ' + (p.proposal_number || '')}
      sub={'Version ' + p.version + (p.editable ? ' · editable' : ' · locked, create a version to change it')}
      right={<Chip tone={STATUS_TONE[p.status]}>{p.status_label}</Chip>}>
      <ErrorBar error={error} />
      {note && <div className="sw-subtle" style={{ color: '#047857', marginBottom: 10 }}>{note}</div>}

      {/* ── pricing ─────────────────────────────────────────────────────── */}
      <div className="sw-field">
        <label>PACKAGE</label>
        <select className="sw-select" value={p.package_id || ''} disabled={!p.editable || busy}
                onChange={e => setPackage(e.target.value)}>
          <option value="">— choose a package —</option>
          {packages.map(pk => (
            <option key={pk.id} value={pk.id}>
              {pk.name}{pk.price != null
                ? ' — ' + money(pk.price, pk.currency) + ' setup'
                : ' — custom'}
            </option>
          ))}
        </select>
      </div>

      {/* The proposal must be able to say WHICH rate it quotes. Without this the
          document shows a monthly figure and the terms show a 13-month
          commitment, with nothing tying the two together. */}
      {/* Some deals are agreed before their economics are. This states that
          plainly instead of publishing a zero or a placeholder. */}
      {p.can_set_custom_rate && p.editable && (
        <label className="sw-flex" style={{ gap: 8, alignItems: 'flex-start',
                                            padding: '8px 0', cursor: 'pointer' }}>
          <input type="checkbox" checked={!!p.withhold_pricing} disabled={busy}
                 style={{ marginTop: 3 }}
                 onChange={e => act(
                   () => api.patch('/sales/proposals/' + current.id,
                                   { withhold_pricing: e.target.checked }),
                   e.target.checked
                     ? 'This proposal will quote no pricing.'
                     : 'This proposal will quote its pricing again.')} />
          <span>
            <b style={{ fontSize: 12 }}>Quote no pricing in this document</b>
            <div className="sw-subtle" style={{ fontSize: 12 }}>
              Removes the Investment section entirely. Use when the commercial
              terms are being agreed separately — nothing is shown as $0 or TBD.
            </div>
          </span>
        </label>
      )}
      {p.withhold_pricing && !(p.can_set_custom_rate && p.editable) && (
        <div className="sw-subtle" style={{ padding: '8px 0', fontSize: 12 }}>
          This proposal deliberately quotes no pricing.
        </div>
      )}

      {/* A custom package has no catalogue rate to choose between, so the
          choice is replaced by the agreement itself. Showing both would offer
          a month-to-month option at a rate that was negotiated with a term. */}
      {p.package_pricing && p.package_pricing.is_custom ? (
        <CustomRate rate={p.custom_rate}
                    currency={p.currency}
                    canEdit={p.can_set_custom_rate && p.editable}
                    disabled={busy}
                    onSave={saveCustomRate}
                    onClear={clearCustomRate} />
      ) : p.package_pricing ? (
        <BillingOptions pricing={p.package_pricing}
                        selected={p.billing_option || 'month_to_month'}
                        disabled={!p.editable || busy}
                        onChoose={opt => act(
                          () => patchProposal({ billing_option: opt }))} />
      ) : null}

      {/* `base_amount` is the ONE-TIME figure and is labelled as such. It is
          what the adjustment below applies to, and it is unchanged by the
          billing option - the setup fee is identical under both. */}
      <div className="sw-flex sw-between" style={{ padding: '10px 0' }}>
        <span className="sw-subtle">Implementation &amp; setup (one-time)</span>
        <b>{money(p.base_amount, p.currency, p.base_amount_cents)}</b>
      </div>
      {p.adjustment ? (
        <div className="sw-flex sw-between" style={{ padding: '4px 0' }}>
          <span className="sw-subtle">Adjustment</span>
          <b style={{ color: '#9e6722' }}>{money(p.adjustment, p.currency, p.adjustment_cents)}</b>
        </div>
      ) : null}
      <div className="sw-flex sw-between"
           style={{ padding: '10px 0', borderTop: '1px solid #eef2f5' }}>
        <b style={{ fontSize: 12 }}>Implementation total (one-time)</b>
        <b style={{ fontSize: 16 }}>{money(p.final_amount, p.currency, p.final_amount_cents)}</b>
      </div>

      {/* The recurring side, kept visually separate from the one-time total
          above so the two can never be read as one number. */}
      {p.commercials && p.commercials.monthly_rate != null && (
        <div className="sw-billing-summary" style={{ marginTop: 10 }}>
          <div className="sw-billing-row">
            <span>Monthly rate / MRR</span>
            <b>{money(p.commercials.monthly_rate, p.currency)}/mo</b>
          </div>
          <div className="sw-billing-row">
            <span>Term</span>
            <b>{p.commercials.term_months
              ? p.commercials.term_months + ' months · all ' +
                p.commercials.payments_required + ' payments required'
              : 'Month-to-month'}</b>
          </div>
          {p.commercials.recurring_contract_value != null && (
            <div className="sw-billing-row">
              <span>Recurring contract value</span>
              <b>{money(p.commercials.recurring_contract_value, p.currency)}</b>
            </div>
          )}
          {p.commercials.term_months && p.commercials.total_contract_value != null ? (
            <div className="sw-billing-row is-primary">
              <span>TOTAL CONTRACT VALUE</span>
              <b>{money(p.commercials.total_contract_value, p.currency)}</b>
            </div>
          ) : null}
        </div>
      )}

      {/* Manager-only. The server refuses a rep's discount either way — this
          just avoids showing a control that would always fail. */}
      {p.can_override_price && p.editable && (
        <div style={{ background: '#f8fafc', border: '1px solid #e5e7eb',
                      borderRadius: 8, padding: 12, marginTop: 10 }}>
          <div className="sw-subtle" style={{ marginBottom: 8 }}>
            Manager pricing adjustment — a reason is required and is recorded.
          </div>
          <input className="sw-input" placeholder="Reason (e.g. competitive vs Vendor X)"
                 value={reason} onChange={e => setReason(e.target.value)} />
          <div className="sw-flex" style={{ gap: 8, marginTop: 8 }}>
            <input className="sw-input" inputMode="decimal" placeholder="-500.00"
                   style={{ width: 130 }} value={adjText}
                   onChange={e => setAdjText(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter' && reason.trim()) applyDiscount(adjText) }} />
            <button className="sw-tiny" disabled={busy || !reason.trim() || !adjCheck.ok}
                    onClick={() => applyDiscount(adjText)}>
              Apply adjustment
            </button>
          </div>
          {adjText !== '' && (adjCheck.ok
            ? <div className="sw-subtle" style={{ marginTop: 6, fontSize: 12 }}>
                New one-time total: {adjPreview}
              </div>
            : <div role="alert" style={{ color: '#b91c1c', fontSize: 12, marginTop: 6 }}>
                {adjCheck.error}
              </div>)}
        </div>
      )}
      {/* A rep cannot set the price — but before Checkpoint 5 the only thing
          they could do about it was leave the product and ask on Slack, which
          left their manager with nothing to answer. This asks IN the record. */}
      {!p.can_override_price && p.editable && !p.pricing_request && (
        <div style={{ background: '#f8fafc', border: '1px solid #e5e7eb',
                      borderRadius: 8, padding: 12, marginTop: 10 }}>
          <div className="sw-subtle" style={{ marginBottom: 8 }}>
            Need a different price? Ask your manager. Nothing changes until they
            approve it.
          </div>
          <input className="sw-input" placeholder="Why do you need it? Your manager reads this."
                 value={askReason} onChange={e => setAskReason(e.target.value)} />
          <div className="sw-flex" style={{ gap: 8, marginTop: 8 }}>
            <input className="sw-input" inputMode="decimal" placeholder="-500.00"
                   style={{ width: 130 }} value={askAdj}
                   onChange={e => setAskAdj(e.target.value)} />
            <button className="sw-tiny" disabled={busy || !askReason.trim() || !validateAdjustment(askAdj).ok}
                    onClick={() => askForPrice()}>
              Ask my manager
            </button>
          </div>
        </div>
      )}
      {p.pricing_request && (
        <div style={{ background: '#fffdf7', border: '1px solid #f0e2c0',
                      borderRadius: 8, padding: 12, marginTop: 10 }}>
          <b style={{ fontSize: 12 }}>Waiting on your manager</b>
          <div className="sw-subtle" style={{ marginTop: 4 }}>
            You asked for {money(p.pricing_request.requested_adjustment, p.currency)}
            {' '}({money(p.pricing_request.requested_total, p.currency)} to the customer)
            {' '}on {dateTime(p.pricing_request.requested_at)}.
          </div>
          <div className="sw-subtle" style={{ marginTop: 4, fontStyle: 'italic' }}>
            “{p.pricing_request.reason}”
          </div>
          <button className="sw-tiny" style={{ marginTop: 8 }} disabled={busy}
                  onClick={() => withdrawAsk()}>
            Withdraw the request
          </button>
        </div>
      )}
      {p.price_override_reason && (
        <div className="sw-subtle" style={{ marginTop: 8 }}>
          Adjusted {dateTime(p.price_override_at)}
          {p.price_override_by_name ? ` by ${p.price_override_by_name}` : ''}
          {' — '}{p.price_override_reason}
        </div>
      )}


      {/* ── content ─────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
        {SECTIONS.map(([key, label, hint]) => (
          <div className="sw-field" key={key}>
            <label>{label}</label>
            <textarea className="sw-input" rows={key === 'executive_summary' ? 3 : 4}
                      value={draft[key] || ''} disabled={!p.editable}
                      placeholder={hint}
                      onChange={e => setDraft({ ...draft, [key]: e.target.value })} />
          </div>
        ))}
        {p.editable && (
          <div className="sw-flex" style={{ justifyContent: 'flex-end' }}>
            <button className="sw-btn" onClick={saveSections} disabled={busy}>
              {busy ? 'Saving…' : 'Save wording'}
            </button>
          </div>
        )}
      </div>

      {/* ── deal room content ───────────────────────────────────────────── */}
      <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
        <b style={{ fontSize: 11 }}>DEAL ROOM CONTENT</b>
        <div className="sw-subtle" style={{ margin: '4px 0 10px' }}>
          The proposal text above is generated automatically. Anything you add
          here — a demo, a deck, a document — sits alongside it and is never
          overwritten when you republish.
        </div>
        {(p.blocks || []).filter(b => !b.generated).map(b => (
          <div key={b.id} className="sw-flex sw-between" style={{ padding: '6px 0' }}>
            <div style={{ minWidth: 0 }}>
              <b style={{ fontSize: 11 }}>{b.content || b.block_type}</b>
              <div className="sw-subtle" style={{ overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.file_url}</div>
            </div>
            <button className="sw-tiny" disabled={busy}
                    onClick={() => removeBlock(b.id)}>Remove</button>
          </div>
        ))}
        <div className="sw-flex" style={{ gap: 8, marginTop: 8 }}>
          <select className="sw-select" style={{ width: 150 }} value={resource.block_type}
                  onChange={e => setResource({ ...resource, block_type: e.target.value })}>
            <option value="website_url">Demo / website</option>
            <option value="pdf">Document</option>
            <option value="video">Video</option>
            <option value="cta">Link</option>
          </select>
          <input className="sw-input" placeholder="Label" value={resource.content}
                 onChange={e => setResource({ ...resource, content: e.target.value })} />
          <input className="sw-input" placeholder="https://…" value={resource.file_url}
                 onChange={e => setResource({ ...resource, file_url: e.target.value })} />
          <button className="sw-tiny" disabled={busy || !resource.file_url.trim()}
                  onClick={addResource}>Add</button>
        </div>

        {/* Upload. Stored as bytes in proposal_files and served through the
            existing route — the file becomes visible to the customer only
            because a block references it in a PUBLISHED proposal. */}
        <div className="sw-flex" style={{ gap: 8, marginTop: 10 }}>
          <label className="sw-tiny" style={{ cursor: busy ? 'default' : 'pointer' }}>
            {busy ? 'Working…' : 'Upload a document'}
            <input type="file" style={{ display: 'none' }} disabled={busy}
                   accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.docx,.pptx,.xlsx,.doc,.ppt,.xls"
                   onChange={e => { upload(e.target.files); e.target.value = '' }} />
          </label>
          <span className="sw-subtle">
            PDF, image, Word, PowerPoint or Excel — up to 20MB
          </span>
        </div>
        {opp.demo_url && !(p.blocks || []).some(b => b.file_url === opp.demo_url) && (
          <button className="sw-tiny" style={{ marginTop: 8 }} disabled={busy}
                  onClick={() => { setResource({ block_type: 'website_url',
                    content: 'Your demo', file_url: opp.demo_url }) }}>
            Use this deal's demo: {opp.demo_url}
          </button>
        )}
      </div>

      {/* ── actions ─────────────────────────────────────────────────────── */}
      <div className="sw-flex" style={{ gap: 8, marginTop: 18, paddingTop: 14,
                                        borderTop: '1px solid #eef2f5', flexWrap: 'wrap' }}>
        <button className="sw-btn" onClick={previewAsCustomer} disabled={busy}>
          Preview as customer
        </button>
        <button className="sw-btn" onClick={publish} disabled={busy}>
          {p.is_published ? 'Republish' : 'Publish'}
        </button>
        <button className="sw-btn sw-primary" onClick={send} disabled={busy}>
          {sent ? 'Re-send to customer' : 'Send to customer'}
        </button>
        {sent && (
          <button className="sw-btn" onClick={newVersion} disabled={busy}>
            New version
          </button>
        )}
        {sent && (
          <button className="sw-tiny" onClick={revoke} disabled={busy}>
            Revoke access
          </button>
        )}
      </div>
      <div className="sw-subtle" style={{ marginTop: 8 }}>
        Preview opens the finished document exactly as the customer sees it, in a
        new tab, and sends nothing. Publishing puts it in the deal room and sends
        nothing. Sending emails the customer a private link.
      </div>


      {/* ── buyer activity ──────────────────────────────────────────────── */}
      {activity && (
        <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
          <b style={{ fontSize: 11 }}>BUYER ACTIVITY</b>
          {p.customer_response_note && (
            <div style={{ background: '#fff6e9', border: '1px solid #f2d5aa',
                          borderRadius: 8, padding: 10, margin: '8px 0' }}>
              <b style={{ fontSize: 11 }}>They said:</b>
              <div className="sw-subtle" style={{ marginTop: 4 }}>
                “{p.customer_response_note}”
              </div>
            </div>
          )}
          {activity.events.length === 0 ? (
            <div className="sw-subtle" style={{ marginTop: 6 }}>
              {sent ? 'Sent, but they have not opened it yet.'
                    : 'Nothing yet — this has not been sent.'}
            </div>
          ) : (
            <div style={{ marginTop: 8 }}>
              {activity.events.slice(0, 12).map(e => (
                <div key={e.id} className="sw-flex sw-between" style={{ padding: '5px 0' }}>
                  <span style={{ fontSize: 11 }}>
                    {e.label}{e.detail ? ' — ' + e.detail : ''}
                    {e.proposal_version && e.proposal_version !== p.version
                      ? ' (v' + e.proposal_version + ')' : ''}
                  </span>
                  <span className="sw-subtle">{dateTime(e.occurred_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── version history ─────────────────────────────────────────────── */}
      {history.length > 0 && (
        <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
          <b style={{ fontSize: 11 }}>EARLIER VERSIONS</b>
          <div className="sw-subtle" style={{ margin: '4px 0 8px' }}>
            Kept exactly as they were sent. Nothing is overwritten.
          </div>
          {history.map(h => (
            <div key={h.id} className="sw-flex sw-between" style={{ padding: '5px 0' }}>
              <span style={{ fontSize: 11 }}>
                v{h.version} · {money(h.final_amount, h.currency)}
              </span>
              <Chip tone={STATUS_TONE[h.status]}>{h.status_label}</Chip>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
