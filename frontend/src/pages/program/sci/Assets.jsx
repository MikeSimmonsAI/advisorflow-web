/* Assets & Flyers: the real library only (GET /program/assets). Thumbnails are
   the actual files (GET /program/assets/{id}/preview). Each upload is a new
   version; only the active version is used by campaigns. No sample covers are
   shown as if they were uploaded files. */
import { useEffect, useMemo, useState } from 'react'
import { api, fetchObjectUrl } from '../../../api/client'
import { Chip, Empty, ErrorLine, Field, Icon, Loading, PageHead, errText } from './ui'

const KIND_LABEL = { flyer: 'Flyer', logo: 'Logo', facility_image: 'Facility image' }

export default function Assets({ isManager, locations }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const [filter, setFilter] = useState({ kind: '', category: '', location: '', activeOnly: false })
  const [form, setForm] = useState({ kind: 'flyer', title: '', category: 'veteran_planning_guide', location_id: '', activate: false })
  const [file, setFile] = useState(null)
  const load = () => api.get('/program/assets').then(setData).catch(e => setErr(errText(e)))
  useEffect(() => { load() }, [])

  const cats = data?.categories || {}
  const homes = locations.filter(l => !l.is_review_bucket)
  const locName = useMemo(() => Object.fromEntries(locations.map(l => [l.location_id, l.name])), [locations])
  const items = (data?.items || []).filter(a =>
    (!filter.kind || a.kind === filter.kind) && (!filter.category || a.category === filter.category)
    && (!filter.location || (filter.location === 'shared' ? !a.location_id : a.location_id === filter.location))
    && (!filter.activeOnly || a.is_active))

  const upload = async e => {
    e.preventDefault(); setErr(''); setMsg('')
    if (!file || !form.title.trim()) { setErr('Choose a file and give it a title.'); return }
    const fd = new FormData()
    fd.append('file', file); fd.append('kind', form.kind); fd.append('title', form.title.trim())
    if (form.kind === 'flyer' && form.category) fd.append('category', form.category)
    if (form.location_id) fd.append('location_id', form.location_id)
    fd.append('activate', form.activate ? 'true' : 'false')
    setBusy(true)
    try {
      const a = await api.upload('/program/assets', fd)
      setMsg(`Uploaded ${a.title} (version ${a.version}).`); setFile(null); setForm(f => ({ ...f, title: '' })); setShowUpload(false); load()
    } catch (er) { setErr(errText(er)) } finally { setBusy(false) }
  }
  const toggle = async a => {
    try { await api.post(`/program/assets/${a.id}/active`, { active: !a.is_active }); load() } catch (e) { setErr(errText(e)) }
  }
  const open = async a => {
    try { const url = await fetchObjectUrl(`/program/assets/${a.id}/preview`); window.open(url, '_blank', 'noopener') } catch (e) { setErr(errText(e)) }
  }

  return (
    <>
      <PageHead title="Assets & flyers" sub="Approved marketing materials, versions and location-specific content.">
        {isManager && <button type="button" className="sci-btn primary" aria-expanded={showUpload} onClick={() => setShowUpload(v => !v)}><Icon name="upload" />{showUpload ? 'Close upload' : 'Upload a file'}</button>}
      </PageHead>
      <ErrorLine text={err} />
      {msg && <div className="sci-okmsg" role="status">{msg}</div>}

      {isManager && showUpload && (
        <section className="sci-panel sci-pad" style={{ marginBottom: 16 }} aria-labelledby="sci-upload">
          <h2 id="sci-upload" className="sci-h2" style={{ marginBottom: 14 }}>Upload a file</h2>
          <form className="sci-form" onSubmit={upload}>
            <Field label="Type">
              <select value={form.kind} onChange={e => setForm(f => ({ ...f, kind: e.target.value }))}>
                <option value="flyer">Flyer (PDF or image)</option><option value="logo">Logo</option><option value="facility_image">Facility image</option>
              </select>
            </Field>
            <Field label="Title"><input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} placeholder="e.g. Veteran Planning Guide" /></Field>
            {form.kind === 'flyer' && (
              <Field label="Flyer category">
                <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
                  {Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </Field>
            )}
            <Field label="Location" hint="A location's own flyer is used first, then the shared one">
              <select value={form.location_id} onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
                <option value="">Every location (shared)</option>
                {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
              </select>
            </Field>
            <Field label="File" hint="PDF, PNG, JPEG, WebP or SVG · up to 20 MB">
              <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.svg" onChange={e => setFile(e.target.files?.[0] || null)} />
            </Field>
            <label className="sci-check" style={{ alignSelf: 'end', paddingBottom: 10 }}>
              <input type="checkbox" checked={form.activate} onChange={e => setForm(f => ({ ...f, activate: e.target.checked }))} /> Make this the active version
            </label>
            <div className="full sci-savebar" style={{ marginTop: 0 }}>
              <button className="sci-btn primary" type="submit" disabled={busy}>{busy ? 'Uploading…' : 'Upload'}</button>
            </div>
          </form>
        </section>
      )}

      <section className="sci-panel sci-pad" aria-labelledby="sci-library">
        <div className="sci-panel-head">
          <h2 id="sci-library">Library</h2>
          <div className="sci-row-gap">
            <label className="sci-sr" htmlFor="sci-a-kind">Type</label>
            <select id="sci-a-kind" className="sci-input" style={{ width: 'auto', minHeight: 36, fontSize: 13 }} value={filter.kind} onChange={e => setFilter(f => ({ ...f, kind: e.target.value }))}>
              <option value="">All types</option>{Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <label className="sci-sr" htmlFor="sci-a-cat">Category</label>
            <select id="sci-a-cat" className="sci-input" style={{ width: 'auto', minHeight: 36, fontSize: 13 }} value={filter.category} onChange={e => setFilter(f => ({ ...f, category: e.target.value }))}>
              <option value="">All categories</option>{Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <label className="sci-sr" htmlFor="sci-a-loc">Location</label>
            <select id="sci-a-loc" className="sci-input" style={{ width: 'auto', minHeight: 36, fontSize: 13, maxWidth: 220 }} value={filter.location} onChange={e => setFilter(f => ({ ...f, location: e.target.value }))}>
              <option value="">All locations</option><option value="shared">Shared (every location)</option>
              {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
            <label className="sci-check"><input type="checkbox" checked={filter.activeOnly} onChange={e => setFilter(f => ({ ...f, activeOnly: e.target.checked }))} /> Active only</label>
          </div>
        </div>
        {!data ? <Loading /> : (data.items || []).length === 0 ? (
          <Empty title="No files uploaded yet.">
            Approved flyers, logos and facility images will appear here as real files with their versions.
            {isManager ? ' Use “Upload a file” to add the first one.' : ''}
          </Empty>
        ) : items.length === 0 ? <Empty>No file matches these filters.</Empty> : (
          <div className="sci-gallery">
            {items.map(a => (
              <article key={a.id} className="sci-asset" aria-label={`${a.title} version ${a.version}`}>
                <Thumb a={a} />
                <div>
                  <b style={{ fontSize: 15 }}>{a.title}</b>
                  <div className="sci-micro sci-muted">{KIND_LABEL[a.kind] || a.kind}{a.category ? ` · ${cats[a.category] || a.category}` : ''}</div>
                  <div className="sci-micro sci-muted">{a.location_id ? locName[a.location_id] : 'Every location'} · v{a.version} · {Math.round((a.size_bytes || 0) / 1024)} KB</div>
                </div>
                <div className="sci-row-gap" style={{ justifyContent: 'space-between' }}>
                  <Chip tone={a.is_active ? 'ok' : ''}>{a.is_active ? 'Active' : 'Inactive'}</Chip>
                  <span className="sci-row-gap">
                    <button type="button" className="sci-btn sm" onClick={() => open(a)}>Open</button>
                    {isManager && <button type="button" className="sci-btn sm" onClick={() => toggle(a)}>{a.is_active ? 'Deactivate' : 'Activate'}</button>}
                  </span>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  )
}

/** The real file as a thumbnail; PDFs and failed loads show a document icon. */
function Thumb({ a }) {
  const [url, setUrl] = useState(null)
  const isImage = /^image\//.test(a.content_type || '') || /\.(png|jpe?g|webp|svg)$/i.test(a.filename || '')
  useEffect(() => {
    if (!isImage) return undefined
    let alive = true
    let made = null
    fetchObjectUrl(`/program/assets/${a.id}/preview`).then(u => { made = u; if (alive) setUrl(u) }).catch(() => {})
    return () => { alive = false; if (made) URL.revokeObjectURL?.(made) }
  }, [a.id, isImage])
  return (
    <div className="sci-asset-cover">
      {url ? <img src={url} alt={a.title} loading="lazy" /> : <span style={{ display: 'grid', placeItems: 'center', gap: 6 }}><Icon name="doc" />{isImage ? 'Loading…' : (a.filename || 'Document')}</span>}
    </div>
  )
}
