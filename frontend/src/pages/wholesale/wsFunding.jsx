/* Funding on a deal: Need funding -> find appropriate options -> route / track.
 *
 * EvoSys is not the lender. Partners are third parties; their criteria are what
 * they stated (or a person verified), and every option shows WHY it fits or
 * does not. What a partner actually did with deals sent to them is measured. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Alert, Panel, Tag } from './ds/ds'
import { errText, fmtDate, fmtMoney } from './wsShared'

const STATUS_KIND = { approved: 'live', funded: 'live', term_sheet: 'info', declined: 'danger',
                      no_response: 'estimate' }

function Track({ t }) {
  if (!t || !t.deals_submitted) return <span className="ws-muted">no deals sent yet</span>
  return (
    <span>
      {t.deals_submitted} sent · {t.approvals} approved · {t.funded} funded · {t.declines} declined
      {t.avg_response_hours !== null && t.avg_response_hours !== undefined
        ? ` · answers in ~${t.avg_response_hours}h` : ''}
    </span>
  )
}

export function FundingWorkspace({ deal }) {
  const [meta, setMeta] = useState(null)
  const [product, setProduct] = useState('')
  const [amount, setAmount] = useState('')
  const [options, setOptions] = useState(null)
  const [subs, setSubs] = useState([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ name: '', contact_person: '', email: '', phone: '', products: [],
                                     states: 'TX', min_loan: '', max_loan: '', typical_close_days: '' })

  const load = useCallback(async () => {
    setErr('')
    try {
      const q = new URLSearchParams()
      if (product) q.set('product', product)
      if (amount) q.set('amount', amount)
      const [m, o, s] = await Promise.all([
        meta ? Promise.resolve(meta) : api.get('/wholesale/funding/products'),
        api.get(`/wholesale/funding/deals/${deal.id}/options?${q}`),
        api.get(`/wholesale/funding/deals/${deal.id}/submissions`),
      ])
      setMeta(m); setOptions(o); setSubs(s.submissions || [])
    } catch (e) { setErr(errText(e)) }
  }, [deal.id, product, amount, meta])

  useEffect(() => { load() }, [load])

  async function run(fn) {
    setBusy(true); setErr('')
    try { await fn(); await load() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function downloadPacket(submissionId) {
    setBusy(true); setErr('')
    try {
      const q = new URLSearchParams({ format: 'html' })
      if (submissionId) q.set('submission_id', submissionId)
      const html = await api.get(`/wholesale/funding/deals/${deal.id}/packet?${q}`)
      const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
      const a = document.createElement('a')
      a.href = url; a.download = `funding-packet-${deal.id.slice(0, 8)}.html`
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 5000)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const submit = (p) => run(() => api.post(`/wholesale/funding/deals/${deal.id}/submissions`,
    { partner_id: p.id, product: product || null, amount_requested: amount ? Number(amount) : null }))
  const respond = (s, status) => run(() => api.post(`/wholesale/funding/submissions/${s.id}/response`, { status }))
  const addPartner = () => run(async () => {
    await api.post('/wholesale/funding/partners', {
      ...form,
      states: form.states.split(',').map(x => x.trim()).filter(Boolean),
      min_loan: form.min_loan ? Number(form.min_loan) : null,
      max_loan: form.max_loan ? Number(form.max_loan) : null,
      typical_close_days: form.typical_close_days ? Number(form.typical_close_days) : null,
      is_test: !!deal.is_test,
    })
    setAdding(false)
  })

  const products = meta?.products || []
  return (
    <>
      {meta?.disclaimer ? <Alert kind="info">{meta.disclaimer}</Alert> : null}
      {err ? <Alert kind="warn">{err}</Alert> : null}
      <Panel title="Find funding options" hint="Ranked on each partner's stated criteria — every reason shown"
             action={<span style={{ display: 'flex', gap: 8 }}>
               <button className="btn btn--primary" disabled={busy} onClick={() => downloadPacket(null)}
                       title="Built only from this deal's data; missing items say 'Not on file'. Nothing is sent.">
                 Download deal packet</button>
               <a className="btn btn--ghost" href="/wholesale/funding">All partners</a>
               <button className="btn btn--secondary" onClick={() => setAdding(a => !a)}>
               {adding ? 'Cancel' : '+ Add funding partner'}</button></span>}>
        {adding ? (
          <div className="ws-form-grid" style={{ marginBottom: 16 }}>
            {[['name', 'Company / name'], ['contact_person', 'Contact person'], ['email', 'Email'],
              ['phone', 'Phone'], ['states', 'States (comma separated)'], ['min_loan', 'Min loan'],
              ['max_loan', 'Max loan'], ['typical_close_days', 'Typical close (days)']].map(([k, label]) => (
              <label key={k}>{label}
                <input className="settings-input" value={form[k]}
                       onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} />
              </label>
            ))}
            <fieldset style={{ gridColumn: '1 / -1', border: 0, padding: 0 }}>
              <legend className="ws-muted">Products they say they fund</legend>
              {products.map(p => (
                <label key={p.key} style={{ marginRight: 14 }}>
                  <input type="checkbox" checked={form.products.includes(p.key)}
                         onChange={e => setForm(f => ({ ...f, products: e.target.checked
                           ? [...f.products, p.key] : f.products.filter(x => x !== p.key) }))} /> {p.label}
                </label>
              ))}
            </fieldset>
            <div><button className="btn btn--primary" disabled={busy} onClick={addPartner}>Save partner</button></div>
          </div>
        ) : null}
        <div className="ws-form-grid">
          <label>Product needed
            <select className="filter-select" value={product} onChange={e => setProduct(e.target.value)}>
              <option value="">Any</option>
              {products.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </label>
          <label>Amount needed
            <input className="settings-input" type="number" value={amount} placeholder="optional"
                   onChange={e => setAmount(e.target.value)} />
          </label>
        </div>
        {!options ? null : !options.options.length ? (
          <p className="ws-muted">No funding partners yet. Add the lenders and capital sources you work with.</p>
        ) : (
          <table className="ws-table">
            <thead><tr><th>Partner</th><th>Fit</th><th>Stated criteria</th><th>Track record here</th><th /></tr></thead>
            <tbody>
              {options.options.map(o => (
                <tr key={o.partner.id} className={o.eligible ? '' : 'is-excluded'}>
                  <td>
                    <div className="ws-comp__addr">{o.partner.name}</div>
                    <div className="ws-comp__sub">{o.partner.contact_person || '—'}</div>
                    {o.partner.verified ? <Tag kind="live">Verified</Tag> : <Tag>Stated, unverified</Tag>}
                  </td>
                  <td>
                    {o.eligible ? <Tag kind="live">Fits</Tag>
                      : <Tag kind="danger" title={o.excluded_because.join(', ')}>
                          Outside: {o.excluded_because.join(', ')}</Tag>}
                    <div className="ws-comp__sub">
                      {o.reasons.map(r => `${r.criterion}: ${r.detail}`).join(' · ')}
                    </div>
                  </td>
                  <td className="ws-comp__sub">
                    {(o.partner.product_labels || []).join(', ') || 'products not stated'}
                    <div>{o.partner.states?.length ? o.partner.states.join(', ') : 'states not stated'}
                      {o.partner.max_loan ? ` · up to ${fmtMoney(o.partner.max_loan)}` : ''}</div>
                  </td>
                  <td className="ws-comp__sub"><Track t={o.partner.track_record} /></td>
                  <td>
                    <button className="btn btn--secondary" disabled={busy} onClick={() => submit(o.partner)}>
                      Record deal sent
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <Panel title="Submissions" count={subs.length}
             hint="What each partner did with this deal — their decision, recorded">
        {!subs.length ? <p className="ws-muted">This deal has not been sent to a funding partner.</p> : (
          <table className="ws-table">
            <thead><tr><th>Partner</th><th>Product</th><th>Requested</th><th>Status</th><th>Sent</th><th>Record answer</th></tr></thead>
            <tbody>
              {subs.map(s => (
                <tr key={s.id}>
                  <td>{s.partner_name}</td>
                  <td>{s.product_label || '—'}</td>
                  <td>{s.amount_requested ? fmtMoney(s.amount_requested) : '—'}</td>
                  <td><Tag kind={STATUS_KIND[s.status]}>{s.status.replace('_', ' ')}</Tag>
                    {s.approved_amount ? <div className="ws-comp__sub">approved {fmtMoney(s.approved_amount)}</div> : null}</td>
                  <td>{fmtDate(s.submitted_at)}</td>
                  <td>
                    <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => downloadPacket(s.id)}>Packet</button>
                    {['approved', 'declined', 'funded', 'no_response'].map(st => (
                      <button key={st} className="btn btn--ghost btn--sm" disabled={busy || s.status === st}
                              onClick={() => respond(s, st)}>{st.replace('_', ' ')}</button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  )
}
