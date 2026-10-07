/* Funding Partners — lenders and capital sources this workspace works with.
 * Each partner is a contact in the shared contact database (a partner, never a
 * lead). What they fund is what THEY SAID, labelled as such until a person
 * verifies it; what they actually did with deals sent to them is measured.
 * EvoSys is not the lender.
 *
 * Load states follow wsListState: a failed refresh keeps the last good partners
 * and says they may be out of date; an unsent value reads "not stated", never 0;
 * a failed save keeps the form exactly as typed. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './wholesale.css'
import { Alert, EvoApp, Hero, Panel, Tag } from './ds/ds'
import './ds/evo-pages.css'
import { errText, fmtMoney, fmtWhen } from './wsShared'
import {
  findById, initialList, listView, loadFailed, loadStarted, loadSucceeded, retryDisabledReason,
  supportCode, trackRecordText, validatePartner,
} from './wsListState'

const BLANK = { name: '', contact_person: '', email: '', phone: '', products: [], states: '', markets: '',
                property_types: '', min_loan: '', max_loan: '', max_ltv_pct: '', max_ltc_pct: '',
                min_credit_score: '', typical_close_days: '', referral_relationship: '', notes: '' }
const LIST = ['states', 'markets', 'property_types']
const NUM = ['min_loan', 'max_loan', 'max_ltv_pct', 'max_ltc_pct', 'min_credit_score', 'typical_close_days']

const withCode = (e) => errText(e) + (supportCode(e) ? ` (support code ${supportCode(e)})` : '')
const stated = (v) => v !== null && v !== undefined && v !== ''

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
  NUM.forEach(k => { b[k] = String(f[k]).trim() === '' ? null : Number(f[k]) })
  return b
}

function PartnerForm({ products, productsFailed, initial, onSave, onCancel, busy, error }) {
  const [f, setF] = useState(initial)
  const [showErrs, setShowErrs] = useState(false)
  const set = (k, v) => setF(x => ({ ...x, [k]: v }))
  const errs = validatePartner(f)
  const shown = showErrs ? errs : {}
  const root = useRef(null)
  function save() {
    const first = Object.keys(errs)[0]
    if (first) {
      setShowErrs(true)
      root.current?.querySelector(`[name="${first}"]`)?.focus()
      return
    }
    onSave(toBody(f))
  }
  const field = (k, label, type = 'text') => (
    <label key={k}>{label}
      <input className="settings-input" type={type} name={k} value={f[k]} onChange={e => set(k, e.target.value)}
             inputMode={type === 'number' ? 'decimal' : undefined}
             aria-invalid={shown[k] ? true : undefined} aria-describedby={shown[k] ? `fp-err-${k}` : undefined} />
      {shown[k] ? <span id={`fp-err-${k}`} className="ws-fp__err" role="alert">{shown[k]}</span> : null}
    </label>
  )
  return (
    <div className="ws-form-grid ws-fp__form" ref={root} style={{ marginBottom: 16 }}>
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
        {productsFailed ? <p className="ws-muted">The product list could not be loaded. Products already chosen are kept.</p> : null}
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
      {error ? <div style={{ gridColumn: '1 / -1' }}><Alert kind="warn">{error}. Nothing you typed was lost — try Save again.</Alert></div> : null}
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" className="btn btn--primary" disabled={busy} onClick={save}
                title={busy ? 'A save is already in progress.' : undefined}>Save</button>
        <button type="button" className="btn btn--secondary" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

export default function WholesaleFundingPartners() {
  const [meta, setMeta] = useState(null)
  const [metaFailed, setMetaFailed] = useState(false)
  const [list, setList] = useState(initialList)
  const [showInactive, setShowInactive] = useState(false)
  const [editing, setEditing] = useState(null)      // 'new' | partner id
  const [pending, setPending] = useState({})        // action key -> true, so unrelated actions stay usable
  const [formErr, setFormErr] = useState('')
  const [rowErr, setRowErr] = useState('')
  const [gone, setGone] = useState('')
  const latch = useRef(new Set())                   // synchronous per-action duplicate guard
  const latest = useRef(0)                          // mirrors list.latest so a response can be checked synchronously
  const metaRef = useRef(null)

  const loadMeta = useCallback(async () => {
    if (metaRef.current) return
    try { metaRef.current = await api.get('/wholesale/funding/products'); setMeta(metaRef.current); setMetaFailed(false) }
    catch { setMetaFailed(true) }
  }, [])

  const load = useCallback(async () => {
    const gen = latest.current + 1
    latest.current = gen
    setList((prev) => loadStarted({ ...prev, latest: gen - 1 }))
    loadMeta()
    try {
      const d = await api.get(`/wholesale/funding/partners?include_inactive=${showInactive}`)
      if (gen === latest.current) setList((prev) => loadSucceeded(prev, gen, d.partners, Array.isArray(d.partners) ? d.partners.length : null))
    } catch (e) {
      if (gen === latest.current) setList((prev) => loadFailed(prev, gen, errText(e), supportCode(e)))
    }
  }, [showInactive, loadMeta])
  useEffect(() => { load() }, [load])

  const partners = list.rows || []
  const view = listView(list)

  // A partner being edited can vanish after a refresh (deactivated elsewhere, hidden by the filter).
  useEffect(() => {
    if (editing && editing !== 'new' && (view === 'ready' || view === 'empty') && !findById(partners, editing)) {
      setEditing(null)
      setGone('The partner you were editing is no longer in this list. Show inactive partners to find it.')
    }
  }, [editing, view, partners])

  async function run(key, fn, { isForm = false } = {}) {
    if (latch.current.has(key)) return
    latch.current.add(key)
    setPending((p) => ({ ...p, [key]: true }))
    setFormErr(''); setRowErr(''); setGone('')
    try { await fn(); if (isForm) setEditing(null); load() }
    catch (e) { (isForm ? setFormErr : setRowErr)(withCode(e)) }
    finally {
      latch.current.delete(key)
      setPending((p) => { const n = { ...p }; delete n[key]; return n })
    }
  }
  const patch = (p, body) => api.patch(`/wholesale/funding/partners/${p.id}`, body)

  function changeInactive(v) {
    setList((prev) => ({ ...initialList(), latest: prev.latest }))   // a different filter is a different list
    setShowInactive(v)
  }
  function openEditor(v) { setFormErr(''); setGone(''); setEditing(v) }

  const products = meta?.products || []
  const known = list.rows !== null
  const retry = retryDisabledReason(list)
  const formBusy = !!pending.form
  const editRow = editing && editing !== 'new' ? findById(partners, editing) : null
  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Capital Network" title="Funding Partners"
            sub="Lenders and capital sources you work with — what they say they fund, and what they actually did with the deals you sent."
            actions={<button type="button" className="btn btn--primary" onClick={() => openEditor('new')}>+ Add funding partner</button>} />
      {meta?.disclaimer ? <Alert kind="info">{meta.disclaimer}</Alert> : null}
      {rowErr ? <Alert kind="warn">{rowErr}</Alert> : null}
      {gone ? <Alert kind="info">{gone}</Alert> : null}
      {view === 'stale' ? (
        <div className="evo-alert evo-alert--warn" role="alert">
          The partner list could not be refreshed: {list.error}
          {list.supportCode ? <> (support code {list.supportCode})</> : null}.
          {' '}The partners below were loaded earlier and may be out of date.
          {' '}<button type="button" className="btn btn--secondary btn--sm" onClick={load}
                      disabled={!!retry} title={retry || undefined}>Try again</button>
        </div>
      ) : null}
      <p className="evo-sr" role="status">{view === 'refreshing' ? 'Refreshing funding partners.' : ''}</p>
      {editing === 'new' ? (
        <Panel title="New funding partner" hint="Saved as a contact classified partner — never a lead">
          <PartnerForm products={products} productsFailed={metaFailed} initial={BLANK} busy={formBusy} error={formErr}
                       onCancel={() => { setEditing(null); setFormErr('') }}
                       onSave={body => run('form', () => api.post('/wholesale/funding/partners', body), { isForm: true })} />
        </Panel>
      ) : null}
      <Panel title="Partners" count={known ? partners.length : undefined}
             action={<label className="ws-muted"><input type="checkbox" checked={showInactive}
                            onChange={e => changeInactive(e.target.checked)} /> Show inactive</label>}>
        {view === 'loading' ? <p className="ws-muted" role="status">Loading funding partners…</p> : null}
        {view === 'error' ? (
          <div role="alert">
            <p>The partner list could not be loaded: {list.error}
              {list.supportCode ? <> (support code {list.supportCode})</> : null}</p>
            <p className="ws-muted">Nothing has been changed.</p>
            <button type="button" className="btn btn--secondary" onClick={load}
                    disabled={!!retry} title={retry || undefined} autoFocus>Try again</button>
          </div>
        ) : null}
        {view === 'empty' ? (
          <p className="ws-muted">{showInactive ? 'No funding partners yet. Add the lenders and capital sources you work with.'
            : 'No active funding partners. Add one, or show inactive partners.'}</p>
        ) : null}
        {partners.length ? (
          <div className="ws-fp__wrap">
            <table className="ws-table ws-fp">
              <caption className="evo-sr">Funding partners</caption>
              <thead><tr><th scope="col">Partner</th><th scope="col">Says they fund</th><th scope="col">Loan range</th>
                <th scope="col">Track record here</th><th scope="col"><span className="evo-sr">Actions</span></th></tr></thead>
              <tbody>
                {partners.map(p => editRow && editRow.id === p.id ? (
                  <tr key={p.id}><td colSpan={5}>
                    <PartnerForm products={products} productsFailed={metaFailed} initial={toForm(p)} busy={formBusy} error={formErr}
                                 onCancel={() => { setEditing(null); setFormErr('') }}
                                 onSave={body => run('form', () => patch(p, body), { isForm: true })} />
                  </td></tr>
                ) : (
                  <tr key={p.id} className={p.is_active ? '' : 'is-excluded'}>
                    <td>
                      <div className="ws-comp__addr">{p.name}{p.is_active ? '' : ' (inactive)'}</div>
                      <div className="ws-comp__sub">{[p.contact_person, p.email, p.phone].filter(Boolean).join(' · ') || '—'}</div>
                      {p.verified ? <Tag kind="live" title={`verified ${fmtWhen(p.verified_at)}`}>Criteria verified</Tag>
                        : <Tag title="What the partner told you; nobody has verified it">Stated, unverified</Tag>}
                      {p.is_test ? <Tag kind="sandbox">Test</Tag> : null}
                    </td>
                    <td className="ws-comp__sub">
                      {(p.product_labels || []).join(', ') || 'products not stated'}
                      <div>{p.states?.length ? p.states.join(', ') : 'states not stated'}</div>
                      {stated(p.typical_close_days) ? <div>closes in ~{p.typical_close_days} days (stated)</div> : null}
                    </td>
                    <td className="ws-comp__sub">
                      {stated(p.min_loan) || stated(p.max_loan)
                        ? `${stated(p.min_loan) ? fmtMoney(p.min_loan) : '—'} to ${stated(p.max_loan) ? fmtMoney(p.max_loan) : '—'}`
                        : 'not stated'}
                      {stated(p.max_ltv_pct) ? <div>LTV up to {p.max_ltv_pct}%</div> : null}
                      {stated(p.max_ltc_pct) ? <div>LTC up to {p.max_ltc_pct}%</div> : null}
                    </td>
                    <td className="ws-comp__sub">
                      {trackRecordText(p.track_record)}
                      {p.track_record?.deals_submitted && p.track_record.avg_response_hours != null
                        ? <div>answers in ~{p.track_record.avg_response_hours}h</div> : null}
                    </td>
                    <td className="ws-fp__actions">
                      <button type="button" className="btn btn--ghost btn--sm" onClick={() => openEditor(p.id)}>Edit</button>
                      <button type="button" className="btn btn--ghost btn--sm" disabled={!!pending['v' + p.id]}
                              title={pending['v' + p.id] ? 'Saving…' : undefined}
                              onClick={() => run('v' + p.id, () => patch(p, { verified: !p.verified }))}>
                        {p.verified ? 'Unverify' : 'Mark verified'}</button>
                      <button type="button" className="btn btn--ghost btn--sm" disabled={!!pending['a' + p.id]}
                              title={pending['a' + p.id] ? 'Saving…' : undefined}
                              onClick={() => run('a' + p.id, () => patch(p, { is_active: !p.is_active }))}>
                        {p.is_active ? 'Deactivate' : 'Reactivate'}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>
    </EvoApp>
  )
}
