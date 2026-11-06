import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'
import { BASE, useJewelry } from './JewelryShell'
import { CustomerPicker, Empty, ErrorNote, Field, Loading, Modal, Pill, ago, errText, n, useApi, useToast } from './jwui'

function Photo({ url, alt }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    let u = null, live = true
    fetchObjectUrl(url).then(x => { u = x; if (live) setSrc(x) }).catch(() => {})
    return () => { live = false; if (u) URL.revokeObjectURL(u) }
  }, [url])
  return src ? <img src={src} alt={alt} /> : <div className="jw-photo-ph" aria-hidden="true">◇</div>
}

function ItemForm({ item, status, onSaved, onClose, toast }) {
  const [f, setF] = useState({ name: item?.name || '', category: item?.category || 'jewelry', status: item?.status || 'available',
                               description: item?.description || '', sku: item?.sku || '', price_note: item?.price_note || '',
                               featured: !!item?.featured, tags: (item?.interest_tags || []).join(', ') })
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true); setErr(null)
    const body = { ...f, interest_tags: f.tags.split(',').map(s => s.trim()).filter(Boolean) }
    delete body.tags
    try { onSaved(item ? await api.patch('/jewelry-ops/items/' + item.id, body) : await api.post('/jewelry-ops/items', body)) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const upload = async (file) => {
    if (!file || !item) return
    const fd = new FormData(); fd.append('file', file)
    try { await api.post(`/jewelry-ops/items/${item.id}/photos`, fd); toast('Photo added.'); onSaved(null, true) } catch (e) { setErr(errText(e)) }
  }
  return (
    <Modal title={item ? 'Edit item' : 'Add item'} onClose={onClose} wide>
      <div className="jw-form2">
        <Field label="Name"><input className="jw-input" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} maxLength={200} /></Field>
        <Field label="Category"><select className="jw-input" value={f.category} onChange={e => setF({ ...f, category: e.target.value })}>
          {(status.item_categories || []).map(c => <option key={c.key} value={c.key}>{c.label}</option>)}</select></Field>
        <Field label="Status"><select className="jw-input" value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
          <option value="available">Available</option><option value="sold">Sold</option><option value="unavailable">Unavailable</option></select></Field>
        <Field label="SKU / tag (optional)"><input className="jw-input" value={f.sku} onChange={e => setF({ ...f, sku: e.target.value })} maxLength={80} /></Field>
        <Field label="Price note (optional)" hint="Whatever you want staff to see, e.g. “Ask in store”"><input className="jw-input" value={f.price_note} onChange={e => setF({ ...f, price_note: e.target.value })} maxLength={120} /></Field>
        <Field label="Interest keywords" hint="Matches customers with these interests, e.g. Morgan, bridal"><input className="jw-input" value={f.tags} onChange={e => setF({ ...f, tags: e.target.value })} /></Field>
      </div>
      <Field label="Description"><textarea className="jw-input" rows={3} value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></Field>
      <label className="jw-check"><input type="checkbox" checked={f.featured} onChange={e => setF({ ...f, featured: e.target.checked })} /> Featured offer</label>
      {item && <Field label="Add a photo"><input type="file" accept="image/*" onChange={e => upload(e.target.files?.[0])} /></Field>}
      {!item && <p className="jw-note">Save the item first, then add photos.</p>}
      <ErrorNote>{err}</ErrorNote>
      <div className="jw-row jw-row--end"><button className="jw-btn" onClick={onClose}>Cancel</button><button className="jw-btn jw-btn--gold" disabled={busy || !f.name.trim()} onClick={save}>{busy ? 'Saving…' : 'Save'}</button></div>
    </Modal>
  )
}

function Inquiries({ item, onClose, toast, onChanged }) {
  const { data, reload } = useApi(`/jewelry-ops/items/${item.id}/inquiries`)
  const [lead, setLead] = useState(null)
  const [note, setNote] = useState('')
  const [err, setErr] = useState(null)
  const add = async () => {
    try { await api.post(`/jewelry-ops/items/${item.id}/interest`, { lead_id: lead, note }); setLead(null); setNote(''); reload(); onChanged(); toast('Inquiry logged.') }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <Modal title={`Interest in ${item.name}`} onClose={onClose}>
      <Field label="Customer"><CustomerPicker value={lead} onChange={setLead} /></Field>
      <Field label="Note"><input className="jw-input" value={note} onChange={e => setNote(e.target.value)} maxLength={300} placeholder="e.g. Asked if it comes with a certificate" /></Field>
      <ErrorNote>{err}</ErrorNote>
      <div className="jw-row jw-row--end"><button className="jw-btn jw-btn--gold" disabled={!lead} onClick={add}>Log inquiry</button></div>
      {!data ? <Loading /> : data.inquiries.length === 0 ? <Empty title="No inquiries logged yet" /> : (
        <ul className="jw-list">{data.inquiries.map(i => <li key={i.id}><div><b>{i.customer_name}</b><small>{i.note || ''}</small></div><small>{ago(i.at)}</small></li>)}</ul>
      )}
    </Modal>
  )
}

export default function Inventory() {
  const { status } = useJewelry()
  const nav = useNavigate()
  const [cat, setCat] = useState('all')
  const [st, setSt] = useState('all')
  const { data, error, loading, reload } = useApi(`/jewelry-ops/items?category=${cat}&status=${st}`, [cat, st])
  const [editing, setEditing] = useState(null)
  const [inq, setInq] = useState(null)
  const [toast, toastNode] = useToast()
  const promote = async (it) => {
    try { const c = await api.post(`/jewelry-ops/items/${it.id}/promote`, {}); nav(`${BASE}/campaigns?c=${c.id}`) } catch (e) { toast(errText(e), 'err') }
  }
  const toggleFeatured = async (it) => { try { await api.patch('/jewelry-ops/items/' + it.id, { featured: !it.featured }); reload() } catch (e) { toast(errText(e), 'err') } }
  return (
    <div>
      <div className="jw-head">
        <div><div className="jw-eyebrow">Inventory & Offers</div><h1>Items & featured offers</h1><p>An internal marketing list - not an online store. There is no point-of-sale or inventory feed connected; items are added here by staff.</p></div>
        {status.can_manage && <button className="jw-btn jw-btn--gold" onClick={() => setEditing('new')}>+ Add item</button>}
      </div>
      <div className="jw-chips jw-chips--wrap">
        <button className={`jw-chip ${cat === 'all' ? 'on' : ''}`} onClick={() => setCat('all')}>All categories</button>
        {(status.item_categories || []).map(c => <button key={c.key} className={`jw-chip ${cat === c.key ? 'on' : ''}`} onClick={() => setCat(c.key)}>{c.label}</button>)}
        <select className="jw-input jw-input--sm" value={st} onChange={e => setSt(e.target.value)} aria-label="Status"><option value="all">Any status</option><option value="available">Available</option><option value="sold">Sold</option><option value="unavailable">Unavailable</option></select>
      </div>
      <ErrorNote onRetry={reload}>{error}</ErrorNote>
      {loading && !data && <Loading />}
      {data && data.items.length === 0 && <div className="jw-card"><Empty title="No items yet" action={status.can_manage && <button className="jw-btn jw-btn--gold" onClick={() => setEditing('new')}>Add the first item</button>}>Add pieces you want to feature or tell collectors about.</Empty></div>}
      {data && data.items.length > 0 && (
        <div className="jw-items">{data.items.map(it => (
          <div key={it.id} className="jw-item">
            <div className="jw-item__img">{it.photos[0] ? <Photo url={it.photos[0].url} alt={it.name} /> : <div className="jw-photo-ph" aria-hidden="true">◇</div>}
              {it.featured && <span className="jw-item__badge">Featured</span>}</div>
            <div className="jw-item__body">
              <b>{it.name}</b>
              <small>{it.category_label}{it.sku ? ' · ' + it.sku : ''}{it.price_note ? ' · ' + it.price_note : ''}</small>
              <div className="jw-row"><Pill tone={it.status === 'available' ? 'ok' : it.status === 'sold' ? '' : 'warn'}>{it.status}</Pill><small>{n(it.matching_customers)} interested · {n(it.inquiries)} inquiries</small></div>
              <div className="jw-row jw-row--wrap">
                <button className="jw-btn jw-btn--sm" onClick={() => setInq(it)}>Inquiries</button>
                {status.can_manage && <button className="jw-btn jw-btn--sm" onClick={() => setEditing(it)}>Edit</button>}
                {status.can_manage && <button className="jw-btn jw-btn--sm" onClick={() => toggleFeatured(it)}>{it.featured ? 'Unfeature' : 'Feature'}</button>}
                {status.can_manage && <button className="jw-btn jw-btn--sm jw-btn--gold" onClick={() => promote(it)}>Promote</button>}
              </div>
            </div>
          </div>
        ))}</div>
      )}
      {editing && <ItemForm item={editing === 'new' ? null : editing} status={status} toast={toast} onClose={() => setEditing(null)}
                            onSaved={(it, keepOpen) => { reload(); if (keepOpen) return; setEditing(null); toast('Item saved.') }} />}
      {inq && <Inquiries item={inq} onClose={() => setInq(null)} toast={toast} onChanged={reload} />}
      {toastNode}
    </div>
  )
}
