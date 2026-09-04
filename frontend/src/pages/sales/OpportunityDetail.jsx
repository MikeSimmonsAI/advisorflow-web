/**
 * Opportunity Detail — the salesperson's working record for the deal.
 *
 * Not a contact card. This is where discovery is captured, the demo build is
 * tracked, the package is chosen, the value is derived (or overridden, with a
 * reason), the stage is moved, and the whole history is readable in one place.
 *
 * Every write goes to the real API and the timeline reloads from the server —
 * the record on screen is always what the database actually holds.
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import FindTeamTime from './FindTeamTime'
import ApptSyncPanel from './ApptSyncPanel'
import RescheduleDialog from './RescheduleDialog'
import ProposalPanel from './ProposalPanel'
import DemoSitesPanel from './DemoSitesPanel'
import ClosingPanel from './ClosingPanel'
import ReassignControl from './ReassignControl'
import {
  Card, Chip, Info, Empty, NotBuilt, ErrorBar,
  money, dateTime, dueLabel, wallDateTime,
} from './parts'
import { BillingOptions } from './BillingOptions.jsx'

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

const DEMO_STATUSES = [
  ['', '—'],
  ['not_requested', 'Not requested'],
  ['requested', 'Requested'],
  ['in_progress', 'In progress'],
  ['ready', 'Ready'],
  ['delivered', 'Delivered'],
]

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
          <Info label="PACKAGE INTEREST" value={opp.package_interest?.name} />
          <Info label="SELECTED PACKAGE" value={opp.selected_package?.name} />
          <Info label="DEAL VALUE" value={opp.deal_value != null ? money(opp.deal_value) : null} />
        </div>
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

function Discovery({ opp, onSave, saving }) {
  const [vals, setVals] = useState({})
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    const seed = {}
    ;(opp.discovery_fields || []).forEach(f => {
      seed[f.key] = (opp.discovery && opp.discovery[f.key]) || ''
    })
    setVals(seed)
    setDirty(false)
  }, [opp.id, opp.discovery])

  function set(k, v) { setVals(s => ({ ...s, [k]: v })); setDirty(true) }

  const completed = opp.discovery?.completed_at

  return (
    <Card
      title="DISCOVERY"
      sub={completed
        ? 'Completed ' + dateTime(completed)
            + (opp.discovery.completed_by_name ? ' by ' + opp.discovery.completed_by_name : '')
        : 'Structured, because these answers feed the demo build'}
      right={completed ? <Chip tone="green">Complete</Chip> : <Chip tone="amber">In progress</Chip>}
    >
      {(opp.discovery_fields || []).map(f => (
        <div className="sw-field" key={f.key}>
          <label>{f.label.toUpperCase()}</label>
          {f.key === 'team_size'
            ? <input className="sw-input" value={vals[f.key] || ''}
                     onChange={e => set(f.key, e.target.value)} />
            : <textarea className="sw-textarea" value={vals[f.key] || ''}
                        onChange={e => set(f.key, e.target.value)} />}
        </div>
      ))}
      <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
        <button className="sw-btn" disabled={!dirty || saving}
                onClick={() => onSave(vals, false)}>
          {saving ? 'Saving…' : 'Save discovery'}
        </button>
        {!completed && (
          <button className="sw-btn sw-primary" disabled={saving}
                  onClick={() => onSave(vals, true)}>
            Save &amp; mark complete
          </button>
        )}
      </div>
    </Card>
  )
}

function DemoBuild({ opp, team, onPatch, saving }) {
  const d = opp.demo || {}
  const [form, setForm] = useState({})
  useEffect(() => {
    setForm({
      demo_status: d.status || '',
      demo_owner_user_id: d.owner_user_id || '',
      demo_due_at: d.due_at ? String(d.due_at).slice(0, 10) : '',
      demo_requirements: d.requirements || '',
      demo_url: d.url || '',
      demo_notes: d.notes || '',
    })
  }, [opp.id, d.status, d.owner_user_id, d.due_at, d.requirements, d.url, d.notes])

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  function save() {
    const body = { ...form }
    body.demo_due_at = form.demo_due_at ? new Date(form.demo_due_at).toISOString() : null
    if (!body.demo_status) delete body.demo_status
    if (!body.demo_owner_user_id) body.demo_owner_user_id = null
    onPatch(body)
  }

  return (
    <Card title="DEMO BUILD"
          sub="See whether your demo is being built without asking anyone"
          right={d.status ? <Chip tone={d.status === 'ready' ? 'green' : 'amber'}>{d.status}</Chip> : null}>
      <div className="sw-grid-even">
        <div className="sw-field">
          <label>STATUS</label>
          <select className="sw-select" value={form.demo_status || ''}
                  onChange={e => set('demo_status', e.target.value)}>
            {DEMO_STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="sw-field">
          <label>BUILDER</label>
          <select className="sw-select" value={form.demo_owner_user_id || ''}
                  onChange={e => set('demo_owner_user_id', e.target.value)}>
            <option value="">Unassigned</option>
            {team.map(t => <option key={t.id} value={t.id}>{t.full_name}</option>)}
          </select>
        </div>
        <div className="sw-field">
          <label>TARGET COMPLETION</label>
          <input className="sw-input" type="date" value={form.demo_due_at || ''}
                 onChange={e => set('demo_due_at', e.target.value)} />
        </div>
        <div className="sw-field">
          <label>DEMO URL</label>
          <input className="sw-input" value={form.demo_url || ''}
                 placeholder="set when the environment exists"
                 onChange={e => set('demo_url', e.target.value)} />
        </div>
      </div>
      <div className="sw-field">
        <label>REQUIREMENTS</label>
        <textarea className="sw-textarea" value={form.demo_requirements || ''}
                  onChange={e => set('demo_requirements', e.target.value)} />
      </div>
      <div className="sw-field">
        <label>INTERNAL NOTES</label>
        <textarea className="sw-textarea" value={form.demo_notes || ''}
                  onChange={e => set('demo_notes', e.target.value)} />
      </div>
      <div className="sw-subtle sw-mt">
        Requested {d.requested_at ? dateTime(d.requested_at) : '—'}
        {d.ready_at ? ' · Ready ' + dateTime(d.ready_at) : ''}
      </div>
      <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
        <button className="sw-btn sw-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save demo build'}
        </button>
      </div>
    </Card>
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

  return (
    <Card title="PACKAGE &amp; DEAL VALUE"
          sub="Value derives from the package; an override is recorded and audited">
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

      <div className="sw-field">
        <label>DEAL VALUE {derived != null && (
          <span style={{ fontWeight: 400 }}>(derived {money(derived)} — one-time)</span>
        )}</label>
        <input className="sw-input" type="number" step="0.01" value={value}
               onChange={e => setValue(e.target.value)} />
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

      <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
        <button className="sw-btn sw-primary" disabled={saving || (needsReason && !reason.trim())}
                onClick={() => onPatch({
                  deal_value: value === '' ? null : Number(value),
                  deal_value_override_reason: reason.trim() || undefined,
                })}>
          {saving ? 'Saving…' : 'Save value'}
        </button>
      </div>
    </Card>
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

  useEffect(() => { load() }, [load])
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

  async function patch(body) {
    setSaving(true); setError(null)
    try { setOpp(await api.patch('/sales/opportunities/' + oppId, body)) }
    catch (e) { setError(e.message || 'Save failed.') }
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

  async function saveDiscovery(vals, complete) {
    setSaving(true); setError(null)
    try { setOpp(await api.put('/sales/opportunities/' + oppId + '/discovery',
                               { ...vals, mark_complete: !!complete })) }
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

  return (
    <SalesShell
      title={opp.company_name}
      subtitle={[opp.contact_name, opp.industry].filter(Boolean).join(' · ') || 'Opportunity record'}
      actions={
        <>
          <button className="sw-btn" onClick={() => nav('/sales/pipeline')}>← Pipeline</button>
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
          <button className="sw-btn sw-primary" onClick={() => setFinding(true)}>
            Find Team Time
          </button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      {postWon && postWon.provisioned ? (
        <div className="sw-card" style={{ marginBottom: 14 }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 13, textTransform: 'uppercase',
                       letterSpacing: '.6px' }}>After the sale</h3>
          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', fontSize: 13 }}>
            <div><strong>{postWon.customer_organization_name}</strong>
              <div className="sw-subtle">Customer organisation</div></div>
            <div><strong>{postWon.status_label}</strong>
              <div className="sw-subtle">Status</div></div>
            <div><strong>{postWon.implementation_owner || 'unassigned'}</strong>
              <div className="sw-subtle">Implementation owner</div></div>
            <div><strong>{postWon.percent_complete}%</strong>
              <div className="sw-subtle">Onboarding complete</div></div>
            <div><strong>{postWon.is_live
              ? 'Live'
              : (postWon.target_launch_date
                 ? new Date(postWon.target_launch_date).toLocaleDateString()
                 : 'not set')}</strong>
              <div className="sw-subtle">{postWon.is_live ? 'Launched' : 'Target launch'}</div></div>
          </div>
          {postWon.is_blocked ? (
            <p className="sw-subtle" style={{ marginBottom: 0 }}>
              This implementation is currently blocked. The implementation owner has the detail.
            </p>
          ) : null}
        </div>
      ) : postWon && postWon.is_won ? (
        <div className="sw-card" style={{ marginBottom: 14 }}>
          <h3 style={{ margin: '0 0 6px', fontSize: 13, textTransform: 'uppercase',
                       letterSpacing: '.6px' }}>After the sale</h3>
          <p className="sw-subtle" style={{ margin: 0 }}>
            Won — awaiting provisioning. A customer organisation is created
            deliberately, not automatically.
          </p>
        </div>
      ) : null}

      {finding && (
        <FindTeamTime
          opportunity={opp}
          onClose={() => setFinding(false)}
          onBooked={() => { setFinding(false); load() }}
        />
      )}

      <div className="sw-card">
        <div className="sw-card-b sw-head">
          <div>
            <div className="sw-chips" style={{ marginBottom: 8 }}>
              <Chip tone="green">{opp.stage_label}</Chip>
              {opp.owner_name && <Chip>Owner: {opp.owner_name}</Chip>}
              {opp.attention && <Chip tone="amber">{opp.attention}</Chip>}
              {opp.days_in_stage != null && <Chip>{opp.days_in_stage}d in stage</Chip>}
            </div>
            <h2>{opp.company_name}</h2>
            <p>{[opp.contact_name, opp.industry, opp.source].filter(Boolean).join(' · ')}</p>
          </div>
          <div className="sw-chips">
            <StageMover opp={opp} onMove={s => patch({ stage: s })} saving={saving} />
          </div>
        </div>
      </div>

      <div className="sw-mt sw-grid2">
        <div>
          <Card
            title="RECORD"
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
            <Card title="LIFECYCLE" sub="One continuous record — never re-created to change stage">
              <Lifecycle opp={opp} />
              {opp.customer_organization_id
                ? <div className="sw-subtle sw-mt">
                    Customer organization: {opp.customer_organization_id}
                  </div>
                : <div className="sw-subtle sw-mt">
                    No customer organization yet — provisioning happens when the deal is Won.
                  </div>}
            </Card>
          </div>

          <div className="sw-mt"><Discovery opp={opp} onSave={saveDiscovery} saving={saving} /></div>
          <div className="sw-mt"><DemoBuild opp={opp} team={team} onPatch={patch} saving={saving} /></div>
          {/* Directly under the demo build panel, because publishing the demo
              is the step that panel has been tracking. Publishing a platform
              walkthrough fills in DEMO URL and flips the status to ready on
              the server, so `load` refreshes the panel above rather than
              leaving it stale. */}
          <div className="sw-mt"><DemoSitesPanel opp={opp} onChanged={load} /></div>
          <div className="sw-mt">
            <PackageDeal opp={opp} packages={packages} onPatch={patch} saving={saving} />
          </div>
          {/* Checkpoint 4. Sits in the main column, after discovery/demo/package
              — the order a deal actually moves through. */}
          <div className="sw-mt">
            <ProposalPanel opp={opp} packages={packages} onChanged={load} />
          </div>
        </div>

        <div>
          {/* First in the right column: once a proposal exists, "what is
              stopping this closing" is the question a rep opens the deal to
              answer. */}
          <div style={{ marginBottom: 16 }}>
            <ClosingPanel opp={opp} />
          </div>

          <Meetings opp={opp} saving={saving}
                    onFind={() => setFinding(true)}
                    onConfirm={confirmAppt} onCancel={cancelAppt}
                    onMove={setMoving} />

          {moving && (
            <RescheduleDialog appt={moving}
                              onClose={() => setMoving(null)}
                              onDone={load} />
          )}

          {/* Checkpoint 3: this was a NotBuilt placeholder. It is now real —
              every state below comes from an actual provider call. */}
          <div className="sw-mt">
            <ApptSyncPanel opp={opp} onChanged={load} />
          </div>

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

          <div className="sw-mt">
            <Card title="ACTIVITY" sub="Append-only — corrections are new entries" bodyless>
              {opp.timeline?.length
                ? <div className="sw-card-b">
                    <div className="sw-timeline">
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
                  </div>
                : <Empty title="No activity yet" />}
            </Card>
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
    <select className="sw-select" style={{ width: 200 }} value={opp.stage}
            disabled={saving || !stages.length}
            onChange={e => onMove(e.target.value)}>
      {stages.length
        ? stages.map(s => <option key={s.key} value={s.key}>{s.label}</option>)
        : <option value={opp.stage}>{opp.stage_label}</option>}
      <option value="lost">Lost</option>
    </select>
  )
}
