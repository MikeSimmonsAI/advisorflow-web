/* MANAGER APPROVAL QUEUE — pending price approvals for ONE brand.
 *
 * Everything shown comes from GET /sales/manager/approvals/queue (one server
 * decision). Money is the server's exact-cent display strings; a missing fact
 * is a dash. Each answer sends the row's `version` and the page reloads on any
 * 409, so a stale row can never show success. There are no send, charge,
 * invoice or provider controls here — answering only records the decision.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import { Card, Chip, Empty, ErrorBar, dateTime } from './parts'
import {
  usd, STATUS_TEXT, decisionBody, approveBlockedReason, errorMessage, countsLine,
} from '../../utils/approvalQueue'

const row = { display: 'flex', flexWrap: 'wrap', gap: 16, margin: '8px 0' }
const cell = { minWidth: 150, flex: '1 1 150px' }
const lbl = { color: 'var(--text-muted)', fontSize: 12 }

function Fig({ label, m }) {
  return <div style={cell}><div style={lbl}>{label}</div><b>{usd(m)}</b></div>
}

function Figures({ it }) {
  const m = it.money || {}
  if (it.kind === 'custom_deal') {
    return (
      <div style={row}>
        <Fig label="Requested unit price" m={m.requested_unit_price} />
        <div style={cell}><div style={lbl}>Min units · term</div>
          <b>{m.requested_min_units ?? '—'} · {m.requested_term_months ?? '—'} mo</b></div>
        <Fig label="Requested monthly" m={m.requested_monthly} />
        <Fig label="Implementation fee" m={m.requested_implementation_fee} />
        <Fig label="Current monthly" m={m.current_monthly} />
      </div>
    )
  }
  return (
    <div style={row}>
      <Fig label="List price" m={m.base} />
      <Fig label="Current adjustment" m={m.current_adjustment} />
      <Fig label="Requested adjustment" m={m.requested_adjustment} />
      <Fig label="Current total" m={m.current_total} />
      <Fig label="Customer would pay" m={m.requested_total} />
    </div>
  )
}

function Policy({ p }) {
  if (!p || !p.available) return <div className="sw-muted">Policy: {p?.note || 'unavailable'}</div>
  return (
    <div className="sw-muted">
      Policy: {p.summary || 'floor breached'}
      {(p.breaches || []).length ? ` (${p.breaches.length} breach${p.breaches.length > 1 ? 'es' : ''})` : ''}
    </div>
  )
}

export default function ApprovalQueue() {
  const nav = useNavigate()
  const [params] = useSearchParams()
  const brand = params.get('brand_sales_org_id') || ''
  const [q, setQ] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [notes, setNotes] = useState({})
  const [busyId, setBusyId] = useState(null)
  const inflight = useRef(false)        // double-click guard that survives re-render
  const seq = useRef(0)                 // ignore stale responses

  const load = useCallback(async () => {
    const mine = ++seq.current
    setLoading(true)
    try {
      const r = await api.get('/sales/manager/approvals/queue' +
        (brand ? '?brand_sales_org_id=' + encodeURIComponent(brand) : ''))
      if (mine === seq.current) { setQ(r); setError('') }
    } catch (e) {
      if (mine === seq.current) { setQ(null); setError(errorMessage(e).text) }
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [brand])

  useEffect(() => { load() }, [load])

  async function answer(it, approve) {
    if (inflight.current) return
    inflight.current = true
    setBusyId(it.id); setNotice(''); setError('')
    try {
      await api.post(`/sales/manager/approvals/${it.id}/decision`,
        decisionBody(it, approve, notes[it.id]))
      setNotice(approve ? 'Approved and recorded.' : 'Denied and recorded.')
      await load()
    } catch (e) {
      const m = errorMessage(e)
      setError(m.text)
      if (m.reload) await load()       // never leave a stale row on screen
    } finally {
      inflight.current = false
      setBusyId(null)
    }
  }

  const pending = q?.pending || []
  return (
    <SalesShell title="Approval queue"
                subtitle="Pricing requests waiting on a manager. Answering records a decision only; nothing is sent or charged.">
      {error ? <ErrorBar error={error} onRetry={load} /> : null}
      {notice ? <div role="status" className="sw-muted">{notice}</div> : null}
      {loading && !q ? <div role="status">Loading approvals…</div> : null}
      {q ? <div className="sw-muted" style={{ marginBottom: 8 }}>{countsLine(q)}</div> : null}
      {q && pending.length === 0 ? (
        <Empty title="Nothing is waiting on you">No pending pricing requests for this brand.</Empty>
      ) : null}
      {pending.map(it => {
        const blocked = approveBlockedReason(it)
        const busy = busyId !== null
        return (
          <Card key={it.id} title={`${it.company_name || 'Unnamed deal'} · ${it.kind === 'custom_deal' ? 'Custom deal pricing' : 'Proposal adjustment'}`}
                sub={`${it.requested_by_name || 'Unknown requester'} · ${it.requested_at ? dateTime(it.requested_at) : 'time unavailable'} · deal ${it.opportunity_id}`}
                right={<Chip tone={it.actionable ? 'amber' : null}>{it.actionable ? 'Actionable' : 'Blocked'}</Chip>}>
            <Figures it={it} />
            <Policy p={it.policy} />
            <blockquote className="sw-quote">{it.reason || 'No reason recorded.'}</blockquote>
            <div className="sw-muted">Version {it.version}{it.stage ? ` · stage ${it.stage}` : ''}</div>
            {blocked ? <div role="alert" className="sw-muted"><b>Cannot approve:</b> {blocked}</div> : null}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              <input className="sw-input" placeholder="Note (optional)"
                     value={notes[it.id] || ''}
                     onChange={e => setNotes(s => ({ ...s, [it.id]: e.target.value }))} />
              <button className="sw-btn sw-primary" disabled={busy || !!blocked}
                      onClick={() => answer(it, true)}>Approve</button>
              <button className="sw-btn" disabled={busy}
                      onClick={() => answer(it, false)}>Deny</button>
              <button className="sw-btn sw-ghost"
                      onClick={() => nav(`/sales/opportunities/${it.opportunity_id}`)}>Open the deal</button>
            </div>
          </Card>
        )
      })}
      {q && (q.history || []).length ? (
        <Card title="Recently resolved" sub="Not part of the pending count.">
          {q.history.map(h => (
            <div key={h.id} className="sw-muted">
              {STATUS_TEXT[h.status] || h.status} · {h.company_name || 'Unnamed deal'} · {h.requested_by_name || 'Unknown'}
              {h.decided_at ? ` · ${dateTime(h.decided_at)}` : ''}
            </div>
          ))}
        </Card>
      ) : null}
    </SalesShell>
  )
}
