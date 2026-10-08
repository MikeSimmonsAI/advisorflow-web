import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../api/client'
import {
  formatCents, validateLine, validateAdjustmentAmount, validateNewDraft, validateTransition,
  controlsFor, TRANSITION_LABEL, STATE_LABEL, NOT_SENT_NOTICE, TAX_NOTICE,
  idempotencyKey, refusalMessage, refusalKind, describeEvent,
} from '../utils/invoiceDraft'
import './InvoiceDrafts.css'

// LOCAL invoice drafts. Authority is the server's workspace-admin gate (the
// route is also requireAdmin). This screen stops at approval-ready: it has no
// send, finalize, pay or provider control, and says so.

function hashText(s) {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0
  return h.toString(36)
}

export default function InvoiceDrafts() {
  const [drafts, setDrafts] = useState([])
  const [listState, setListState] = useState('loading')   // loading | ready | error
  const [listError, setListError] = useState('')
  const [draft, setDraft] = useState(null)
  const [notice, setNotice] = useState(null)               // { kind: 'ok' | 'refused', text }
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)                            // blocks double-click before React re-renders
  const [newForm, setNewForm] = useState({ customerName: '', memo: '' })
  const [lineForm, setLineForm] = useState({ description: '', quantity: '1', unitPrice: '' })
  const [adjForm, setAdjForm] = useState({ discount: '', tax: '' })
  const [voidReason, setVoidReason] = useState('')

  const loadList = useCallback(async () => {
    try {
      const r = await api.get('/invoice-drafts')
      setDrafts(Array.isArray(r && r.drafts) ? r.drafts : [])
      setListState('ready')
      setListError('')
    } catch (e) {
      setListState('error')
      setListError(refusalMessage(e).replace('Not saved', 'Could not load'))
    }
  }, [])

  const syncForms = (d) => setAdjForm({ discount: formatCents(d.discount_cents).slice(1), tax: formatCents(d.tax_cents).slice(1) })

  const openDraft = useCallback(async (id) => {
    try {
      const d = await api.get('/invoice-drafts/' + id)
      setDraft(d)
      syncForms(d)
      return d
    } catch (e) {
      setNotice({ kind: 'refused', text: refusalMessage(e).replace('Not saved', 'Could not load') })
      return null
    }
  }, [])

  useEffect(() => { loadList() }, [loadList])

  // One mutation path: validate before calling, one request at a time, the
  // loaded expected_version and a payload-derived Idempotency-Key on every call.
  // A refusal keeps its message, reloads current data, and is never shown as success.
  async function mutate(action, path, payload, okText) {
    if (busyRef.current) return false
    busyRef.current = true
    setBusy(true)
    setNotice(null)
    const body = { ...payload, expected_version: draft ? draft.version : undefined }
    const key = idempotencyKey(action, draft && draft.id, draft && draft.version, hashText(JSON.stringify(body)))
    try {
      const d = await api.post(path, body, { headers: { 'Idempotency-Key': key } })
      setDraft(d)
      syncForms(d)
      setNotice({ kind: 'ok', text: okText })
      loadList()
      return true
    } catch (e) {
      setNotice({ kind: 'refused', text: refusalMessage(e), refusal: refusalKind(e) })
      if (draft) await openDraft(draft.id)    // show current server data after any refusal
      return false
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  async function createDraft(e) {
    e.preventDefault()
    const v = validateNewDraft(newForm)
    if (!v.ok) return setNotice({ kind: 'refused', text: v.error })
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setNotice(null)
    try {
      const key = idempotencyKey('create', null, 0, hashText(JSON.stringify(v.body)))
      const d = await api.post('/invoice-drafts', v.body, { headers: { 'Idempotency-Key': key } })
      setDraft(d)
      syncForms(d)
      setNewForm({ customerName: '', memo: '' })
      setNotice({ kind: 'ok', text: 'Draft created locally. Nothing has been sent.' })
      loadList()
    } catch (err) {
      setNotice({ kind: 'refused', text: refusalMessage(err), refusal: refusalKind(err) })
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  async function addLine(e) {
    e.preventDefault()
    const v = validateLine(lineForm)
    if (!v.ok) return setNotice({ kind: 'refused', text: v.error })
    const ok = await mutate('line_add', `/invoice-drafts/${draft.id}/lines`, v.line, 'Line added.')
    if (ok) setLineForm({ description: '', quantity: '1', unitPrice: '' })
  }

  function removeLine(n) {
    return mutate('line_remove', `/invoice-drafts/${draft.id}/lines/${n}/remove`, {}, 'Line removed.')
  }

  function saveAdjustments(e) {
    e.preventDefault()
    const d = validateAdjustmentAmount(adjForm.discount, 'Discount')
    if (!d.ok) return setNotice({ kind: 'refused', text: d.error })
    const t = validateAdjustmentAmount(adjForm.tax, 'Tax')
    if (!t.ok) return setNotice({ kind: 'refused', text: t.error })
    return mutate('adjust', `/invoice-drafts/${draft.id}/adjustments`,
      { discount_cents: d.cents, tax_cents: t.cents }, 'Discount and tax saved.')
  }

  async function transition(to) {
    const v = validateTransition(to, voidReason)
    if (!v.ok) return setNotice({ kind: 'refused', text: v.error })
    const ok = await mutate('transition_' + to, `/invoice-drafts/${draft.id}/transition`,
      { to, reason: v.reason }, to === 'approval_ready'
        ? 'Marked approval-ready locally. It has not been sent, finalized or charged.'
        : to === 'void' ? 'Invoice voided.' : 'Returned to draft.')
    if (ok) setVoidReason('')
  }

  const c = controlsFor(draft)

  return (
    <div className="invd-page">
      <div className="invd-head">
        <h1>Invoice Drafts</h1>
        <p>Local drafts for this workspace. Nothing here is sent, finalized, charged or created with a payment provider.</p>
      </div>

      {notice && (
        <div className={'invd-notice ' + (notice.kind === 'ok' ? 'invd-ok' : 'invd-refused')} role={notice.kind === 'ok' ? 'status' : 'alert'}>
          {notice.text}
        </div>
      )}

      <div className="invd-cols">
        <section className="invd-panel invd-list" aria-label="Drafts">
          <h2>Drafts</h2>
          <form onSubmit={createDraft} className="invd-form">
            <input aria-label="Customer name" placeholder="Customer name" maxLength={200}
              value={newForm.customerName} onChange={e => setNewForm({ ...newForm, customerName: e.target.value })} />
            <input aria-label="Memo" placeholder="Memo (optional)" maxLength={2000}
              value={newForm.memo} onChange={e => setNewForm({ ...newForm, memo: e.target.value })} />
            <button type="submit" disabled={busy}>Create draft</button>
          </form>
          {listState === 'loading' && <p className="invd-muted">Loading…</p>}
          {listState === 'error' && (
            <div className="invd-notice invd-refused" role="alert">{listError}
              <button type="button" onClick={loadList}>Retry</button></div>
          )}
          {listState === 'ready' && drafts.length === 0 && <p className="invd-muted">No drafts yet.</p>}
          <ul className="invd-rows">
            {drafts.map(d => (
              <li key={d.id}>
                <button type="button" className={'invd-row' + (draft && draft.id === d.id ? ' is-open' : '')}
                  onClick={() => { setNotice(null); openDraft(d.id) }}>
                  <span>{d.customer_name}</span>
                  <span className={'invd-state invd-state-' + d.state}>{STATE_LABEL[d.state] || d.state}</span>
                  <span className="invd-money">{formatCents(d.total_cents)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="invd-panel invd-detail" aria-label="Draft detail">
          {!draft && <p className="invd-muted">Select or create a draft.</p>}
          {draft && (
            <>
              <h2>{draft.customer_name}</h2>
              <dl className="invd-meta">
                <dt>State</dt><dd><span className={'invd-state invd-state-' + draft.state}>{STATE_LABEL[draft.state] || draft.state}</span></dd>
                <dt>Version</dt><dd>{draft.version}</dd>
                <dt>Provider status</dt><dd>{draft.provider_status}</dd>
                <dt>Editable</dt><dd>{c.canEdit ? 'Yes - draft' : 'No - ' + (draft.refusal_reason || 'locked')}</dd>
                <dt>Memo</dt><dd>{draft.memo || '—'}</dd>
              </dl>

              <div className="invd-table-wrap">
                <table className="invd-table">
                  <thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Line total</th>{c.canEdit && <th></th>}</tr></thead>
                  <tbody>
                    {draft.lines.length === 0 && <tr><td colSpan={c.canEdit ? 5 : 4} className="invd-muted">No lines.</td></tr>}
                    {draft.lines.map(l => (
                      <tr key={l.line_no}>
                        <td>{l.description}</td><td>{l.quantity}</td>
                        <td className="invd-money">{formatCents(l.unit_price_cents)}</td>
                        <td className="invd-money">{formatCents(l.line_total_cents)}</td>
                        {c.canEdit && <td><button type="button" disabled={busy} onClick={() => removeLine(l.line_no)}>Remove</button></td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <dl className="invd-totals">
                <dt>Subtotal</dt><dd>{formatCents(draft.subtotal_cents)}</dd>
                <dt>Discount</dt><dd>-{formatCents(draft.discount_cents)}</dd>
                <dt>Tax (operator-entered)</dt><dd>{formatCents(draft.tax_cents)}</dd>
                <dt>Total</dt><dd><strong>{formatCents(draft.total_cents)}</strong></dd>
              </dl>
              <p className="invd-muted">{draft.tax_note || TAX_NOTICE}</p>

              {c.canEdit && (
                <>
                  <form onSubmit={addLine} className="invd-form" aria-label="Add line">
                    <input aria-label="Line description" placeholder="Description" maxLength={200}
                      value={lineForm.description} onChange={e => setLineForm({ ...lineForm, description: e.target.value })} />
                    <input aria-label="Quantity" inputMode="numeric" placeholder="Qty"
                      value={lineForm.quantity} onChange={e => setLineForm({ ...lineForm, quantity: e.target.value })} />
                    <input aria-label="Unit price in dollars" inputMode="decimal" placeholder="Unit price (e.g. 19.99)"
                      value={lineForm.unitPrice} onChange={e => setLineForm({ ...lineForm, unitPrice: e.target.value })} />
                    <button type="submit" disabled={busy}>Add line</button>
                  </form>
                  <form onSubmit={saveAdjustments} className="invd-form" aria-label="Discount and tax">
                    <input aria-label="Discount in dollars" inputMode="decimal" placeholder="Discount"
                      value={adjForm.discount} onChange={e => setAdjForm({ ...adjForm, discount: e.target.value })} />
                    <input aria-label="Tax in dollars (operator-entered)" inputMode="decimal" placeholder="Tax (operator-entered)"
                      value={adjForm.tax} onChange={e => setAdjForm({ ...adjForm, tax: e.target.value })} />
                    <button type="submit" disabled={busy}>Save discount and tax</button>
                  </form>
                </>
              )}

              <div className="invd-transitions">
                {c.blocker && <p className="invd-muted">Not approval-ready yet: {c.blocker}</p>}
                {c.transitions.includes('void') && (
                  <input aria-label="Void reason" placeholder="Reason (required to void)" maxLength={500}
                    value={voidReason} onChange={e => setVoidReason(e.target.value)} />
                )}
                {c.transitions.map(t => (
                  <button key={t} type="button" disabled={busy} onClick={() => transition(t)}>{TRANSITION_LABEL[t]}</button>
                ))}
                {c.transitions.length === 0 && <p className="invd-muted">No further actions: this invoice is final locally.</p>}
              </div>
              <p className="invd-banner">{NOT_SENT_NOTICE}</p>

              <h3>History</h3>
              <ol className="invd-history">
                {(draft.events || []).map((ev, i) => {
                  const x = describeEvent(ev)
                  return <li key={i}><strong>{x.what}</strong> · {x.who} · {x.when}{x.detail ? <div className="invd-muted">{x.detail}</div> : null}</li>
                })}
              </ol>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
