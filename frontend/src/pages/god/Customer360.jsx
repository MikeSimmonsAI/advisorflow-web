/**
 * CUSTOMER 360 — one customer, whole.
 *
 * WHO THEY ARE, WHICH BRAND OWNS THEM, WHAT THEY BOUGHT, HOW MUCH THEY PAY,
 * WHO SOLD IT, WHAT DEAL CREATED THEM, WHERE THEIR IMPLEMENTATION STANDS, AND
 * — IF THEY LEFT — WHEN AND WHY.
 *
 * NOTHING ON THIS PAGE IS COMPUTED HERE. Every figure arrives resolved from the
 * server, which reads the pricing snapshot taken when the deal was won. A
 * formula in this file would be a second answer to what a customer agreed to.
 *
 * MISSING IS SAID OUT LOUD. A customer with no provable originating deal reads
 * CREATED OUTSIDE PIPELINE; one whose sale recorded no figures reads COMMERCIAL
 * DATA INCOMPLETE; one with no implementation record says so rather than
 * showing a dash. A dash is what this screen replaced, and it taught nobody
 * anything.
 *
 * CANCELLING IS NOT DELETING, and the offboarding dialog says so in the same
 * words the server does — it renders the server's own preview rather than a
 * reassuring sentence written here that could drift from what actually happens.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { Panel, Empty, money, when, whenExact, errText } from './GodOpsShared'
import './GodOps.css'

const STATUS_TONE = {
  active: 'live',
  cancellation_requested: 'warn',
  offboarding: 'warn',
  cancelled: 'blocked',
  archived: '',
}

export function StatusPill({ status, label }) {
  return (
    <span className={'go-badge ' + (STATUS_TONE[status] ?? '')}>
      {(label || status || '').toUpperCase()}
    </span>
  )
}

function Fact({ k, v, none }) {
  const empty = v === null || v === undefined || v === ''
  return (
    <div className="go-fact">
      <div className="k">{k}</div>
      <div className={'v' + (empty ? ' none' : '')}>
        {empty ? (none || 'not recorded') : v}
      </div>
    </div>
  )
}

/* ── what they pay ───────────────────────────────────────────────────────── */

function Commercials({ c }) {
  if (!c.complete) {
    return (
      <Panel title="Commercials">
        <div className="go-body">
          <div className="go-note warn" style={{ marginBottom: 0 }}>
            <b>COMMERCIAL DATA INCOMPLETE</b>
            <p style={{ margin: '6px 0 0' }}>{c.incomplete_reason}</p>
          </div>
        </div>
      </Panel>
    )
  }
  const cur = c.currency || 'USD'
  return (
    <Panel title="Commercials">
      <div className="go-body">
        <div className="go-facts">
          <Fact k="Setup / implementation"
                v={c.setup !== null ? money(c.setup, cur) : null} />
          <Fact k="MRR" v={c.mrr !== null ? money(c.mrr, cur) + '/mo' : null} />
          {/* MONTH-TO-MONTH IS A KNOWN STRUCTURE, said in words. An empty cell
              here would read as missing data, which is a different thing. */}
          <Fact k="Term" v={c.term_label} />
          <Fact k="Recurring contract value"
                v={c.recurring_contract_value !== null
                  ? money(c.recurring_contract_value, cur)
                  : null}
                none={c.structure === 'month_to_month'
                  ? 'No fixed RCV — month-to-month' : 'not recorded'} />
          <Fact k="Total contract value"
                v={c.total_contract_value !== null
                  ? money(c.total_contract_value, cur)
                  : null}
                none={c.structure === 'month_to_month'
                  ? 'No fixed TCV — month-to-month' : 'not recorded'} />
          <Fact k="Billing status" v={c.billing_status} />
        </div>
        <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
          These are the figures recorded when the deal was won, not today's
          catalogue. Repricing a package does not rewrite what this customer
          agreed to.
        </p>
      </div>
    </Panel>
  )
}

/* ── the offboarding dialog ──────────────────────────────────────────────── */

function OffboardDialog({ orgId, name, onClose, onDone }) {
  const [preview, setPreview] = useState(null)
  const [reason, setReason] = useState('')
  const [effective, setEffective] = useState('')
  const [note, setNote] = useState('')
  const [obligations, setObligations] = useState('')
  const [reasons, setReasons] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/god/customer-360/customers/' + orgId + '/offboarding-preview')
      .then(setPreview).catch(e => setErr(errText(e)))
    api.get('/god/customer-360/customers?limit=1')
      .then(r => setReasons(r.vocabulary?.reasons || [])).catch(() => {})
  }, [orgId])

  async function submit() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/customer-360/customers/' + orgId + '/request-cancellation', {
        reason: reason || null,
        effective_at: effective ? new Date(effective + 'T00:00:00').toISOString() : null,
        note: note || null,
        obligations_note: obligations || null,
      })
      onDone()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="go-modal-back" onClick={onClose}>
      <div className="go-modal" style={{ maxWidth: 720, width: '94%', maxHeight: '88vh',
                                         overflowY: 'auto' }}
           onClick={e => e.stopPropagation()}>
        <div className="go-modal-h">
          <h3>Cancel / offboard {name}</h3>
          <button className="go-btn sm ghost" onClick={onClose}>Close</button>
        </div>

        <div className="go-body">
          <div className="go-note" style={{ marginBottom: 14 }}>
            <b>THIS IS NOT A DELETE.</b> Recording a cancellation changes this
            customer's status and nothing else — their workspace stays open
            until the cancellation is completed.
          </div>

          {err ? <div className="go-note err">{err}</div> : null}

          {preview ? (
            <>
              <div style={{ display: 'grid', gap: 12,
                            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
                <div>
                  <div className="go-label">What will happen</div>
                  <ul className="go-plain-list" style={{ fontSize: 12.5 }}>
                    {preview.will_happen.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
                <div>
                  <div className="go-label">What will NOT happen</div>
                  <ul className="go-plain-list" style={{ fontSize: 12.5 }}>
                    {preview.will_not_happen.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              </div>

              {preview.contract?.note ? (
                <div className="go-note warn" style={{ marginTop: 14 }}>
                  <b>CONTRACTUAL POSITION</b>
                  <p style={{ margin: '6px 0 0' }}>{preview.contract.note}</p>
                </div>
              ) : null}

              {/* THE HONEST PART. These steps are not automated and this screen
                  does not pretend they are. */}
              {preview.manual_steps?.length ? (
                <div style={{ marginTop: 14 }}>
                  <div className="go-label">Still needs a human</div>
                  <ul className="go-plain-list" style={{ fontSize: 12.5 }}>
                    {preview.manual_steps.map(s => (
                      <li key={s.key} style={{ margin: '5px 0' }}>
                        <strong>{s.label}</strong> — {s.detail}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : <Empty>Loading what this will affect…</Empty>}

          <div className="go-fields" style={{ marginTop: 16 }}>
            <div className="go-field">
              <label>Reason</label>
              <select value={reason} onChange={e => setReason(e.target.value)}>
                <option value="">Not stated</option>
                {reasons.map(r => (
                  <option key={r.key} value={r.key}>{r.label}</option>
                ))}
              </select>
            </div>
            <div className="go-field">
              <label>Effective cancellation date</label>
              <input type="date" value={effective}
                     onChange={e => setEffective(e.target.value)} />
              <div className="hint">
                When service actually ends. Often not today — a notice period or
                a paid-through date is normal.
              </div>
            </div>
            <div className="go-field full">
              <label>Admin notes</label>
              <input type="text" value={note} onChange={e => setNote(e.target.value)}
                     placeholder="What happened, in your words" />
            </div>
            <div className="go-field full">
              <label>Outstanding obligations</label>
              <input type="text" value={obligations}
                     onChange={e => setObligations(e.target.value)}
                     placeholder="Remaining term, unpaid invoices, anything still owed" />
              <div className="hint">
                Recorded on the cancellation. Nothing here bills or collects —
                it exists so a cancellation cannot quietly erase an obligation.
              </div>
            </div>
          </div>

          <div className="go-actions" style={{ marginTop: 14, justifyContent: 'flex-start' }}>
            <button className="go-btn sm" onClick={submit} disabled={busy || !preview}>
              {busy ? 'Recording…' : 'Record cancellation request'}
            </button>
            <button className="go-btn sm ghost" onClick={onClose} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── test-data cleanup (preview → confirm → execute) ─────────────────────── */

function CustomerCleanupPanel({ orgId }) {
  const [open, setOpen]           = useState(false)
  const [rules, setRules]         = useState(null)      // [{key, description}]
  const [selected, setSelected]   = useState([])
  const [preview, setPreview]     = useState(null)      // preview response
  const [typed, setTyped]         = useState('')
  const [busy, setBusy]           = useState(false)
  const [receipt, setReceipt]     = useState(null)
  const [err, setErr]             = useState(null)
  const [history, setHistory]     = useState(null)

  // Load rules the first time the panel is expanded
  useEffect(() => {
    if (!open || rules) return
    api.get('/god/customers/cleanup/rules')
      .then(r => { setRules(r.rules || []); setErr(null) })
      .catch(e => setErr(errText(e)))
  }, [open, rules])

  // Load cleanup history (all, then filter client-side for this org)
  useEffect(() => {
    if (!open || history) return
    api.get('/god/customers/cleanup/history', { params: { limit: 100 } })
      .then(r => {
        const mine = (r.executions || []).filter(ex =>
          !ex.org_ids?.length || ex.org_ids.includes(orgId)
        )
        setHistory(mine)
      })
      .catch(() => setHistory([]))
  }, [open, history, orgId])

  function toggle(key) {
    setSelected(s => s.includes(key) ? s.filter(k => k !== key) : [...s, key])
    setPreview(null); setReceipt(null); setErr(null)
  }

  async function runPreview() {
    if (!selected.length) return
    setBusy(true); setErr(null); setPreview(null); setReceipt(null)
    try {
      const res = await api.post('/god/customers/cleanup/preview', {
        rules: selected, org_ids: [orgId],
      })
      setPreview(res); setTyped('')
    } catch (e) { setErr(errText(e)) }
    finally { setBusy(false) }
  }

  async function runExecute() {
    if (!preview || typed !== preview.confirmation_phrase) return
    setBusy(true); setErr(null)
    try {
      const res = await api.post('/god/customers/cleanup/execute', {
        rules: selected, org_ids: [orgId],
        confirmation: typed, execution_id: preview.execution_id,
      })
      setReceipt(res); setPreview(null); setTyped('')
      setHistory(null)  // invalidate so it reloads next time
    } catch (e) { setErr(errText(e)) }
    finally { setBusy(false) }
  }

  const STATUS_COLOR = { succeeded: 'var(--gm-teal)', failed: 'var(--gm-red)', previewed: 'var(--gm-text)', superseded: 'var(--gm-amber)' }

  return (
    <Panel title="Test-data cleanup">
      <div className="go-body">
        <p style={{ margin: '0 0 10px', fontSize: 12, color: 'var(--go-dim)' }}>
          Preview what test leads would be removed for this customer, then confirm before anything is deleted.
          Organizations, users and integrations are never touched.
        </p>

        <button className="go-btn sm ghost" onClick={() => setOpen(o => !o)}>
          {open ? '▲ Collapse' : '▼ Expand cleanup tool'}
        </button>

        {open && (
          <div style={{ marginTop: 14 }}>
            {err && <div className="go-note err" style={{ marginBottom: 10 }}>{err}</div>}

            {/* Rule selection */}
            {rules === null ? (
              <div style={{ color: 'var(--go-dim)', fontSize: 12 }}>Loading rules…</div>
            ) : (
              <>
                <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 6,
                              color: 'var(--go-dim)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
                  Select rules to apply
                </div>
                {rules.map(r => (
                  <label key={r.key} style={{ display: 'flex', alignItems: 'flex-start',
                                             gap: 8, marginBottom: 6, cursor: 'pointer' }}>
                    <input type="checkbox" checked={selected.includes(r.key)}
                           onChange={() => toggle(r.key)}
                           style={{ marginTop: 2, flexShrink: 0 }} />
                    <span style={{ fontSize: 12 }}>
                      <b style={{ fontFamily: 'monospace' }}>{r.key}</b>
                      {' — '}{r.description}
                    </span>
                  </label>
                ))}
                <button className="go-btn sm" onClick={runPreview}
                        disabled={busy || !selected.length} style={{ marginTop: 8 }}>
                  {busy ? 'Running…' : 'Preview'}
                </button>
              </>
            )}

            {/* Preview results */}
            {preview && !receipt && (
              <div style={{ marginTop: 14, padding: '12px 14px',
                            border: '1px solid var(--gm-amber)', borderRadius: 8,
                            background: 'var(--gm-pill-amber-bg)' }}>
                <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>
                  Preview: {preview.total_records} record{preview.total_records !== 1 ? 's' : ''} would be deleted
                </div>
                {(preview.categories || []).filter(c => c.count > 0).map(c => (
                  <div key={c.key} style={{ display: 'flex', gap: 10, fontSize: 12,
                                            borderBottom: '1px solid var(--gm-amber)', padding: '4px 0' }}>
                    <span style={{ fontFamily: 'monospace', minWidth: 100 }}>{c.key}</span>
                    <span style={{ fontWeight: 700 }}>{c.count}</span>
                    <span style={{ color: 'var(--gm-amber)' }}>{c.description}</span>
                  </div>
                ))}
                {preview.total_records === 0 ? (
                  <div style={{ fontSize: 12, color: 'var(--gm-amber)' }}>
                    Nothing matches the selected rules for this customer.
                  </div>
                ) : (
                  <div style={{ marginTop: 12 }}>
                    <div style={{ fontSize: 12, marginBottom: 6 }}>
                      Type exactly: <code style={{ fontWeight: 700 }}>{preview.confirmation_phrase}</code>
                    </div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input value={typed} onChange={e => setTyped(e.target.value)}
                             placeholder="Type the phrase above"
                             style={{ fontSize: 12, padding: '5px 8px', borderRadius: 6,
                                      border: '1px solid var(--gm-amber)', flex: 1 }} />
                      <button className="go-btn sm danger"
                              disabled={busy || typed !== preview.confirmation_phrase}
                              onClick={runExecute}>
                        {busy ? 'Deleting…' : 'Execute'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Receipt */}
            {receipt && (
              <div style={{ marginTop: 14, padding: '12px 14px',
                            border: '1px solid var(--gm-teal)', borderRadius: 8,
                            background: 'var(--gm-pill-teal-bg)' }}>
                <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--gm-teal)', marginBottom: 4 }}>
                  Done — {receipt.actual_total ?? 0} record{receipt.actual_total !== 1 ? 's' : ''} deleted
                </div>
                <div style={{ fontSize: 11, color: 'var(--gm-teal)' }}>
                  Execution ID: <code>{receipt.execution_id}</code>
                </div>
              </div>
            )}

            {/* History */}
            {history?.length > 0 && (
              <div style={{ marginTop: 18 }}>
                <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 8,
                              color: 'var(--go-dim)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
                  Past cleanups touching this customer
                </div>
                {history.map(ex => (
                  <div key={ex.execution_id} style={{
                    display: 'flex', gap: 12, alignItems: 'flex-start',
                    padding: '6px 0', borderBottom: '1px solid var(--go-line)',
                    fontSize: 12,
                  }}>
                    <span style={{ fontWeight: 700, color: STATUS_COLOR[ex.status] || 'var(--gm-dim)',
                                   minWidth: 80 }}>{ex.status}</span>
                    <span style={{ color: 'var(--go-dim)' }}>
                      {ex.rules?.join(', ') || '—'}
                    </span>
                    <span style={{ marginLeft: 'auto', color: 'var(--go-dim)', whiteSpace: 'nowrap' }}>
                      {ex.target_lead_count} leads · {ex.created_at ? new Date(ex.created_at).toLocaleDateString() : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}

/* ── permanent delete ────────────────────────────────────────────────────── */

function DeleteDialog({ orgId, name, onClose, onDone }) {
  const [impact, setImpact] = useState(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/god/customer-360/customers/' + orgId + '/deletion-impact')
      .then(setImpact).catch(e => setErr(errText(e)))
  }, [orgId])

  async function submit() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/customer-360/customers/' + orgId + '/permanent-delete',
                     { confirmation: typed })
      onDone()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="go-modal-back" onClick={onClose}>
      <div className="go-modal" style={{ maxWidth: 620, width: '94%' }}
           onClick={e => e.stopPropagation()}>
        <div className="go-modal-h">
          <h3>Permanently delete {name}</h3>
          <button className="go-btn sm ghost" onClick={onClose}>Close</button>
        </div>
        <div className="go-body">
          {err ? <div className="go-note err">{err}</div> : null}
          {!impact ? <Empty>Checking what points at this customer…</Empty> : (
            <>
              <div className="go-facts" style={{ marginBottom: 14 }}>
                {Object.entries(impact.counts).map(([k, v]) => (
                  <div className="go-fact" key={k}>
                    <div className="k">{k.replace(/_/g, ' ')}</div>
                    <div className="v">{v}</div>
                  </div>
                ))}
              </div>

              {impact.may_delete ? (
                <>
                  <div className="go-note err">
                    <b>THIS CANNOT BE UNDONE.</b>
                    <p style={{ margin: '6px 0 0' }}>
                      This organization has no commercial history, so deleting it
                      orphans nothing. Its users and leads go with it.
                    </p>
                  </div>
                  <div className="go-field">
                    <label>Type <code>{name}</code> to confirm</label>
                    <input type="text" value={typed}
                           onChange={e => setTyped(e.target.value)} />
                  </div>
                  <div className="go-actions" style={{ marginTop: 12, justifyContent: 'flex-start' }}>
                    <button className="go-btn sm danger" onClick={submit}
                            disabled={busy || typed.trim() !== (name || '').trim()}>
                      {busy ? 'Deleting…' : 'Permanently delete'}
                    </button>
                    <button className="go-btn sm ghost" onClick={onClose}>Cancel</button>
                  </div>
                </>
              ) : (
                <div className="go-note warn" style={{ marginBottom: 0 }}>
                  <b>REFUSED — this customer has history worth keeping</b>
                  <p style={{ margin: '6px 0 0' }}>{impact.refusal}</p>
                  <p style={{ margin: '6px 0 0' }}>{impact.alternative}</p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── the page ────────────────────────────────────────────────────────────── */

export default function Customer360() {
  const { orgId } = useParams()
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [offboard, setOffboard] = useState(false)
  const [del, setDel] = useState(false)

  const load = useCallback(() => {
    api.get('/god/customer-360/customers/' + orgId)
      .then(r => { setD(r); setErr('') })
      .catch(e => setErr(errText(e)))
  }, [orgId])

  useEffect(() => { load() }, [load])

  async function act(path, body) {
    setBusy(true); setErr('')
    try {
      setD(await api.post('/god/customer-360/customers/' + orgId + '/' + path,
                          body || {}))
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  if (err && !d) return <div className="go-scope"><div className="go-note err">{err}</div></div>
  if (!d) return <div className="go-scope"><div className="go-empty">Loading…</div></div>

  const s = d.lifecycle_status
  const c = d.commercials
  const comp = d.compensation

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/customers')}>
            ← Customers
          </button>
          <h1 style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            {d.name}
            <StatusPill status={s} label={d.lifecycle_status_label} />
            {!d.workspace_active
              ? <span className="go-badge blocked">WORKSPACE CLOSED</span>
              : null}
          </h1>
          <p>
            {d.platform ? d.platform.name : 'No brand'} ·{' '}
            {d.source === 'pipeline'
              ? 'From pipeline'
              : 'Created outside the pipeline'}
            {d.customer_since ? ' · customer since ' + when(d.customer_since) : ''}
          </p>
        </div>
      </div>

      {err ? <div className="go-note err">{err}</div> : null}

      {/* WHY THE WORKSPACE STATE IS SHOWN SEPARATELY FROM THE STATUS. A
          cancelled customer served through a notice period is both cancelled
          and open, and one badge cannot say that. */}
      {s !== 'active' ? (
        <div className="go-note warn">
          <b>{d.lifecycle_status_label.toUpperCase()}</b>
          <p style={{ margin: '6px 0 0' }}>
            {d.cancellation_requested_at
              ? 'Requested ' + when(d.cancellation_requested_at) : ''}
            {d.cancellation_effective_at
              ? ' · effective ' + when(d.cancellation_effective_at) : ''}
            {d.cancellation_reason_label ? ' · ' + d.cancellation_reason_label : ''}
            {d.cancelled_at ? ' · cancelled ' + when(d.cancelled_at) : ''}
            {d.archived_at ? ' · archived ' + when(d.archived_at) : ''}
          </p>
          {d.cancellation_note ? (
            <p style={{ margin: '6px 0 0' }}>{d.cancellation_note}</p>
          ) : null}
          <p style={{ margin: '8px 0 0', fontSize: 12 }}>
            Every record is retained — the originating deal, the proposal, the
            pricing agreed, and any commission earned.
          </p>
        </div>
      ) : null}

      {/* ── lifecycle actions ── */}
      <Panel title="Lifecycle">
        <div className="go-body">
          <div className="go-actions">
            {s === 'active' ? (
              <button className="go-btn sm" onClick={() => setOffboard(true)}>
                Cancel / offboard customer
              </button>
            ) : null}
            {s === 'cancellation_requested' ? (
              <button className="go-btn sm" disabled={busy}
                      onClick={() => act('start-offboarding')}>
                Start offboarding
              </button>
            ) : null}
            {(s === 'cancellation_requested' || s === 'offboarding') ? (
              <button className="go-btn sm danger" disabled={busy}
                      onClick={() => act('complete-cancellation')}>
                Complete cancellation
              </button>
            ) : null}
            {(s === 'active' || s === 'cancelled') ? (
              <button className="go-btn sm ghost" disabled={busy}
                      onClick={() => act('archive')}>
                Archive
              </button>
            ) : null}
            {s !== 'active' ? (
              <button className="go-btn sm ghost" disabled={busy}
                      onClick={() => act('reactivate')}>
                Reactivate
              </button>
            ) : null}
            <button className="go-btn sm ghost"
                    onClick={() => nav('/god/customers/' + orgId)}>
              Administration
            </button>
          </div>
          <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
            Cancelling records what happened and, on completion, closes the
            workspace. It deletes nothing and it is reversible.
          </p>
          {/* Deliberately not beside ENTER, deliberately not beside Archive,
              and it refuses outright for any customer with history. */}
          <div style={{ marginTop: 14, paddingTop: 12,
                        borderTop: '1px solid var(--go-line)' }}>
            <button className="go-btn sm ghost" onClick={() => setDel(true)}
                    style={{ opacity: .75 }}>
              Permanent deletion…
            </button>
            <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--go-dim)' }}>
              For accidental test or duplicate records only. Refused for any
              customer with commercial history.
            </span>
          </div>
        </div>
      </Panel>

      {/* ── the sale ── */}
      <Panel title="The sale">
        <div className="go-body">
          {d.source === 'pipeline' ? (
            <>
              <div className="go-facts">
                <Fact k="Originating opportunity"
                      v={d.opportunity ? d.opportunity.company_name : null} />
                <Fact k="Sold by" v={d.sold_by ? d.sold_by.name : null} />
                <Fact k="Sales manager" v={d.sales_manager ? d.sales_manager.name : null}
                      none="no manager on the org chart" />
                <Fact k="Sales organization"
                      v={d.sales_organization ? d.sales_organization.name : null} />
                <Fact k="Package" v={d.package ? d.package.name : null} />
                <Fact k="Deal type"
                      v={c.structure === 'fixed_term' ? 'Fixed term'
                        : c.structure === 'month_to_month' ? 'Month-to-month'
                        : c.structure === 'one_time_only' ? 'One-time' : null} />
                <Fact k="Proposal"
                      v={d.proposal
                        ? (d.proposal.number || d.proposal.id) +
                          ' v' + (d.accepted_proposal_version ?? d.proposal.version)
                        : null}
                      none="no proposal recorded on the sale" />
                <Fact k="Proposal status" v={d.proposal ? d.proposal.status : null} />
              </div>
              {d.opportunity ? (
                <div className="go-actions" style={{ marginTop: 12 }}>
                  <button className="go-btn sm ghost"
                          onClick={() => nav('/sales/opportunities/' + d.opportunity.id)}>
                    View original deal
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="go-note warn" style={{ marginBottom: 0 }}>
              <b>CREATED OUTSIDE PIPELINE</b>
              <p style={{ margin: '6px 0 0' }}>
                This organization has no implementation record, so no originating
                opportunity, proposal or salesperson can be proven. Matching one
                by name would invent a relationship, so none is shown.
              </p>
            </div>
          )}
        </div>
      </Panel>

      <Commercials c={c} />

      {/* ── delivery ── */}
      <Panel title="Implementation">
        <div className="go-body">
          {d.implementation ? (
            <div className="go-facts">
              <Fact k="Status" v={(d.implementation.status || '').replace(/_/g, ' ')} />
              <Fact k="Owner" v={d.implementation.owner ? d.implementation.owner.name : null}
                    none="unassigned" />
              <Fact k="Target launch" v={when(d.implementation.target_launch_date)} />
              <Fact k="Kickoff" v={when(d.implementation.kickoff_at)} />
              <Fact k="Launched" v={when(d.implementation.launched_at)} />
              <Fact k="Blocker" v={d.implementation.blocker_note} none="none" />
            </div>
          ) : (
            <div className="go-note warn" style={{ marginBottom: 0 }}>
              <b>NO IMPLEMENTATION RECORD</b>
              <p style={{ margin: '6px 0 0' }}>
                This customer was never provisioned through Won → Provision. A
                workspace existing is not evidence that an implementation
                happened, so nothing is assumed about it.
              </p>
            </div>
          )}
          {d.implementation ? (
            <div className="go-actions" style={{ marginTop: 12 }}>
              <button className="go-btn sm ghost"
                      onClick={() => nav('/god/implementations/' + d.implementation.id)}>
                Open implementation
              </button>
            </div>
          ) : null}
        </div>
      </Panel>

      {/* ── workspace ── */}
      <Panel title="Workspace">
        <div className="go-body">
          <div className="go-facts">
            <Fact k="Workspace" v={d.workspace_active ? 'Open' : 'Closed'} />
            <Fact k="Users" v={d.user_count} />
            <Fact k="Leads" v={d.lead_count} />
            <Fact k="Plan" v={d.plan} />
            <Fact k="Industry" v={d.industry} />
            <Fact k="Slug" v={d.slug} />
          </div>
        </div>
      </Panel>

      <CustomerCleanupPanel orgId={orgId} />

      {/* ── compensation, god-only and served as such ── */}
      {comp ? (
        <Panel title="Sales compensation">
          <div className="go-body">
            {comp.available ? (
              <>
                <div className="go-facts">
                  <Fact k="Projected"
                        v={comp.projected !== null && comp.projected !== undefined
                          ? money(comp.projected) : null}
                        none={comp.projected_status === 'unconfigured'
                          ? 'no plan configured' : 'not available'} />
                  <Fact k="Earned" v={comp.earned !== null ? money(comp.earned) : null}
                        none="none yet" />
                  <Fact k="Payable" v={comp.payable !== null ? money(comp.payable) : null}
                        none="none" />
                  <Fact k="Paid" v={comp.paid !== null ? money(comp.paid) : null}
                        none="none" />
                </div>
                <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
                  Projected is what this deal would pay under today's plan.
                  Earned, payable and paid are records that already exist and are
                  never recalculated — including after a cancellation.
                </p>
              </>
            ) : (
              <Empty>{comp.reason}</Empty>
            )}
          </div>
        </Panel>
      ) : null}

      {/* ── history ── */}
      {d.lifecycle_history?.length ? (
        <Panel title="Lifecycle history" count={d.lifecycle_history.length}>
          <ul className="go-tl">
            {d.lifecycle_history.map(h => (
              <li key={h.id}>
                <strong>{h.event.replace(/_/g, ' ')}</strong>
                {h.reason_label ? ' · ' + h.reason_label : ''}
                <div className="when">
                  {whenExact(h.created_at)} · <span className="who">{h.actor_name}</span>
                  {h.effective_at ? ' · effective ' + when(h.effective_at) : ''}
                </div>
                {h.note ? <div style={{ marginTop: 4 }}>{h.note}</div> : null}
                {h.obligations_note ? (
                  <div style={{ marginTop: 4, color: 'var(--go-amber)' }}>
                    Obligations: {h.obligations_note}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {offboard ? (
        <OffboardDialog orgId={orgId} name={d.name}
                        onClose={() => setOffboard(false)}
                        onDone={() => { setOffboard(false); load() }} />
      ) : null}
      {del ? (
        <DeleteDialog orgId={orgId} name={d.name}
                      onClose={() => setDel(false)}
                      onDone={() => nav('/god/customers')} />
      ) : null}
    </div>
  )
}
