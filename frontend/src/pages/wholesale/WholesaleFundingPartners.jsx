/* Funding Partners — lenders and capital sources this workspace works with.
 * Each partner is a contact in the shared contact database (a partner, never a
 * lead). What they fund is what THEY SAID, labelled as such until a person
 * verifies it; what they actually did with deals sent to them is measured.
 * EvoSys is not the lender. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './wholesale.css'
import { Alert, EvoApp, Hero, Panel, Tag } from './ds/ds'
import './ds/evo-pages.css'
import { errText, fmtMoney, fmtWhen } from './wsShared'
import ContactLookup from './wsContactLookup'

const BLANK = { name: '', contact_person: '', email: '', phone: '', products: [], states: '', markets: '',
                property_types: '', min_loan: '', max_loan: '', max_ltv_pct: '', max_ltc_pct: '',
                min_credit_score: '', typical_close_days: '', referral_relationship: '', notes: '' }
const LIST = ['states', 'markets', 'property_types']
const NUM = ['min_loan', 'max_loan', 'max_ltv_pct', 'max_ltc_pct', 'min_credit_score', 'typical_close_days']

function toForm(p) {
  const f = { ...BLANK }
  Object.keys(BLANK).forEach(k => {
    const v = p[k]
    f[k] = LIST.includes(k) ? (v || []).join(', ') : (k === 'products' ? (v || []) : (v ?? ''))
  })
  return f
}
function toBody(f) {
  const b = { ...f }
  LIST.forEach(k => { b[k] = f[k].split(',').map(x => x.trim()).filter(Boolean) })
  NUM.forEach(k => { b[k] = f[k] === '' ? null : Number(f[k]) })
  return b
}

function PartnerForm({ products, initial, onSave, onCancel, busy }) {
  const [f, setF] = useState(initial)
  const set = (k, v) => setF(x => ({ ...x, [k]: v }))
  const field = (k, label, type = 'text') => (
    <label key={k}>{label}
      <input className="settings-input" type={type} value={f[k]} onChange={e => set(k, e.target.value)} />
    </label>
  )
  return (
    <div className="ws-form-grid" style={{ marginBottom: 16 }}>
      {field('name', 'Company / name')}{field('contact_person', 'Contact person')}
      {field('email', 'Email')}{field('phone', 'Phone')}
      {field('states', 'States (comma separated)')}{field('markets', 'Markets (comma separated)')}
      {field('property_types', 'Property types (comma separated)')}
      {field('min_loan', 'Min loan', 'number')}{field('max_loan', 'Max loan', 'number')}
      {field('max_ltv_pct', 'Max LTV %', 'number')}{field('max_ltc_pct', 'Max LTC %', 'number')}
      {field('min_credit_score', 'Min credit score', 'number')}
      {field('typical_close_days', 'Typical close (days)', 'number')}
      {field('referral_relationship', 'Referral relationship')}
      <fieldset style={{ gridColumn: '1 / -1', border: 0, padding: 0 }}>
        <legend className="ws-muted">Products they say they fund</legend>
        {products.map(p => (
          <label key={p.key} style={{ marginRight: 14 }}>
            <input type="checkbox" checked={f.products.includes(p.key)}
                   onChange={e => set('products', e.target.checked ? [...f.products, p.key]
                     : f.products.filter(x => x !== p.key))} /> {p.label}
          </label>
        ))}
      </fieldset>
      <label style={{ gridColumn: '1 / -1' }}>Notes
        <textarea className="settings-input" rows={2} value={f.notes} onChange={e => set('notes', e.target.value)} />
      </label>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn--primary" disabled={busy || !f.name.trim()} onClick={() => onSave(toBody(f))}>Save</button>
        <button className="btn btn--secondary" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

export default function WholesaleFundingPartners() {
  const [meta, setMeta] = useState(null)
  const [data, setData] = useState(null)
  const [showInactive, setShowInactive] = useState(false)
  const [editing, setEditing] = useState(null)      // 'new' | partner id
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [picked, setPicked] = useState({})
  const [lookupIds, setLookupIds] = useState(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const [m, d] = await Promise.all([meta ? Promise.resolve(meta) : api.get('/wholesale/funding/products'),
        api.get(`/wholesale/funding/partners?include_inactive=${showInactive}`)])
      setMeta(m); setData(d)
    } catch (e) { setErr(errText(e)) }
  }, [meta, showInactive])
  useEffect(() => { load() }, [load])

  async function run(fn) {
    setBusy(true); setErr('')
    try { await fn(); setEditing(null); await load() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const partners = data?.partners || []
  const products = meta?.products || []
  const nPicked = partners.filter((p) => picked[p.id]).length
  const allPicked = partners.length > 0 && partners.every((p) => picked[p.id])
  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Capital Network" title="Funding Partners"
            sub="Lenders and capital sources you work with — what they say they fund, and what they actually did with the deals you sent."
            actions={<>
              <button className="btn btn--secondary" disabled={!nPicked}
                      title={nPicked ? 'Look up phone numbers and emails for the partners you ticked' : 'Tick the partners first'}
                      onClick={() => setLookupIds(partners.filter((p) => picked[p.id]).map((p) => p.id))}>
                Get phones &amp; emails{nPicked ? ` (${nPicked})` : ''}</button>
              <button className="btn btn--primary" onClick={() => setEditing('new')}>+ Add funding partner</button>
            </>} />
      {meta?.disclaimer ? <Alert kind="info">{meta.disclaimer}</Alert> : null}
      {err ? <Alert kind="warn">{err}</Alert> : null}
      {note ? <Alert kind="ok">{note}</Alert> : null}
      {editing === 'new' ? (
        <Panel title="New funding partner" hint="Saved as a contact classified partner — never a lead">
          <PartnerForm products={products} initial={BLANK} busy={busy} onCancel={() => setEditing(null)}
                       onSave={body => run(() => api.post('/wholesale/funding/partners', body))} />
        </Panel>
      ) : null}
      <Panel title="Partners" count={partners.length}
             action={<label className="ws-muted"><input type="checkbox" checked={showInactive}
                            onChange={e => setShowInactive(e.target.checked)} /> Show inactive</label>}>
        {!data ? null : !partners.length ? (
          <p className="ws-muted">No funding partners yet. Add the lenders and capital sources you work with.</p>
        ) : (
          <table className="ws-table">
            <thead><tr><th style={{ width: 32 }}><input type="checkbox" aria-label="Select all partners" checked={allPicked}
                   onChange={(e) => setPicked(e.target.checked ? Object.fromEntries(partners.map((p) => [p.id, true])) : {})} /></th>
              <th>Partner</th><th>Says they fund</th><th>Loan range</th><th>Track record here</th><th /></tr></thead>
            <tbody>
              {partners.map(p => editing === p.id ? (
                <tr key={p.id}><td colSpan={6}>
                  <PartnerForm products={products} initial={toForm(p)} busy={busy} onCancel={() => setEditing(null)}
                               onSave={body => run(() => api.patch(`/wholesale/funding/partners/${p.id}`, body))} />
                </td></tr>
              ) : (
                <tr key={p.id} className={p.is_active ? '' : 'is-excluded'}>
                  <td><input type="checkbox" aria-label={`Select ${p.name}`} checked={!!picked[p.id]}
                             onChange={(e) => setPicked((x) => ({ ...x, [p.id]: e.target.checked }))} /></td>
                  <td>
                    <div className="ws-comp__addr">{p.name}</div>
                    <div className="ws-comp__sub">{[p.contact_person, p.email, p.phone].filter(Boolean).join(' · ') || '—'}</div>
                    {p.verified ? <Tag kind="live" title={`verified ${fmtWhen(p.verified_at)}`}>Criteria verified</Tag>
                      : <Tag title="What the partner told you; nobody has verified it">Stated, unverified</Tag>}
                    {p.is_test ? <Tag kind="sandbox">Test</Tag> : null}
                  </td>
                  <td className="ws-comp__sub">
                    {(p.product_labels || []).join(', ') || 'products not stated'}
                    <div>{p.states?.length ? p.states.join(', ') : 'states not stated'}</div>
                    {p.typical_close_days ? <div>closes in ~{p.typical_close_days} days (stated)</div> : null}
                  </td>
                  <td className="ws-comp__sub">
                    {p.min_loan || p.max_loan
                      ? `${p.min_loan ? fmtMoney(p.min_loan) : '—'} to ${p.max_loan ? fmtMoney(p.max_loan) : '—'}`
                      : 'not stated'}
                    {p.max_ltv_pct ? <div>LTV up to {p.max_ltv_pct}%</div> : null}
                    {p.max_ltc_pct ? <div>LTC up to {p.max_ltc_pct}%</div> : null}
                  </td>
                  <td className="ws-comp__sub">
                    {p.track_record?.deals_submitted
                      ? <>{p.track_record.deals_submitted} sent · {p.track_record.approvals} approved · {p.track_record.funded} funded · {p.track_record.declines} declined
                          {p.track_record.avg_response_hours != null ? <div>answers in ~{p.track_record.avg_response_hours}h</div> : null}</>
                      : 'no deals sent yet'}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn btn--ghost btn--sm" onClick={() => setEditing(p.id)}>Edit</button>
                    <button className="btn btn--ghost btn--sm" disabled={busy}
                            onClick={() => run(() => api.patch(`/wholesale/funding/partners/${p.id}`, { verified: !p.verified }))}>
                      {p.verified ? 'Unverify' : 'Mark verified'}</button>
                    <button className="btn btn--ghost btn--sm" disabled={busy}
                            onClick={() => run(() => api.patch(`/wholesale/funding/partners/${p.id}`, { is_active: !p.is_active }))}>
                      {p.is_active ? 'Deactivate' : 'Reactivate'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      {lookupIds ? <ContactLookup kind="funding_partner" what="funding partner" ids={lookupIds}
                                  onClose={() => setLookupIds(null)}
                                  onDone={(msg) => { setNote(msg); setPicked({}); load() }} /> : null}
    </EvoApp>
  )
}
