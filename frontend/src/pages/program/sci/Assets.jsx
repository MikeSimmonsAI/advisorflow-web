/* Assets & Flyers: the real library only (GET /program/assets). Thumbnails are
   the actual files (GET /program/assets/{id}/preview). Each upload is a new
   version; only the active version is used by campaigns. No sample covers or
   placeholder flyers are ever shown as if they were uploaded files - an empty
   library says so. Uploading and activating are manager-only on the server. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../../api/client'

// Read the file WITH the workspace headers (a manager viewing another workspace
// needs X-Org-Override, or the server answers 404).
const blobUrl = async path => URL.createObjectURL(await api.get(path, { asBlob: true }))
import {
  Chip, Drawer, Empty, ErrorLine, Field, FilterChips, Icon, Kpi, Loading, Notice, PageHead, Panel, SearchBox,
  errText, num, when,
} from './ui'

const KIND_LABEL = { flyer: 'Flyer', logo: 'Logo', facility_image: 'Facility image' }
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.svg'
const MAX_MB = 20
const groupKey = a => [a.kind, a.category || '', a.location_id || '', (a.title || '').trim().toLowerCase()].join('|')

export default function Assets({ isManager, locations }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState('')
  const [q, setQ] = useState('')
  // Active only by default: replaced versions stay on file but are not shown
  // unless asked for, so the library shows what campaigns actually use.
  const [filter, setFilter] = useState({ category: '', location: '', activeOnly: true })
  const [form, setForm] = useState({ kind: 'flyer', title: '', category: '', location_id: '', activate: false })
  const [file, setFile] = useState(null)
  const [over, setOver] = useState(false)
  const [open, setOpen] = useState(null)
  const fileRef = useRef(null)
  const load = () => api.get('/program/assets').then(d => {
    setData(d)
    setForm(f => (f.category ? f : { ...f, category: Object.keys(d.categories || {})[0] || '' }))
  }).catch(e => setErr(errText(e)))
  useEffect(() => { load() }, [])

  const cats = data?.categories || {}
  const homes = locations.filter(l => !l.is_review_bucket)
  const locName = useMemo(() => Object.fromEntries(locations.map(l => [l.location_id, l.name])), [locations])
  const all = data?.items || []
  const groups = useMemo(() => {
    const g = {}
    all.forEach(a => { (g[groupKey(a)] = g[groupKey(a)] || []).push(a) })
    Object.values(g).forEach(v => v.sort((x, y) => y.version - x.version))
    return g
  }, [all])
  const term = q.trim().toLowerCase()
  const items = all.filter(a =>
    (!kind || a.kind === kind) && (!filter.category || a.category === filter.category)
    && (!filter.location || (filter.location === 'shared' ? !a.location_id : a.location_id === filter.location))
    && (!filter.activeOnly || a.is_active)
    && (!term || `${a.title} ${a.filename || ''} ${cats[a.category] || ''}`.toLowerCase().includes(term)))
  const activeCount = all.filter(a => a.is_active).length
  const locSpecific = new Set(all.filter(a => a.location_id).map(a => a.location_id)).size

  const pickFile = f => {
    setErr('')
    if (!f) { setFile(null); return }
    if (f.size > MAX_MB * 1024 * 1024) { setErr(`That file is larger than ${MAX_MB} MB.`); return }
    if (!/\.(pdf|png|jpe?g|webp|svg)$/i.test(f.name)) { setErr('PDF, PNG, JPEG, WebP or SVG only.'); return }
    setFile(f)
    setForm(fm => (fm.title ? fm : { ...fm, title: f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ') }))
  }
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
      setMsg(`Uploaded ${a.title} (version ${a.version})${a.is_active ? ' — active' : ' — not active yet'}.`)
      setFile(null); setForm(f => ({ ...f, title: '', activate: false })); if (fileRef.current) fileRef.current.value = ''
      load()
    } catch (er) { setErr(errText(er)) } finally { setBusy(false) }
  }
  // ── many flyers at once: each file is matched to a location by its file name ──
  const norm = s => (s || '').toLowerCase().replace(/\.[^.]+$/, '').replace(/&/g, 'and').replace(/[^a-z0-9]+/g, '')
  const [bulk, setBulk] = useState([])
  const [bulkCat, setBulkCat] = useState('')
  const [bulkActive, setBulkActive] = useState(true)
  const [bulkDone, setBulkDone] = useState('')
  const pickMany = list => {
    setBulkDone(''); setErr('')
    const byName = Object.fromEntries(homes.map(l => [norm(l.name), l]))
    setBulk(Array.from(list || []).map(f => ({ f, loc: byName[norm(f.name)] || null, status: '' })))
  }
  const uploadMany = async () => {
    const cat = bulkCat || Object.keys(cats)[0] || ''
    setBusy(true); setErr(''); setBulkDone('')
    let ok = 0
    const rows = [...bulk]
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i]
      if (!r.loc || r.status === 'done') continue
      const fd = new FormData()
      fd.append('file', r.f); fd.append('kind', 'flyer')
      fd.append('title', `${cats[cat] || 'Flyer'} - ${r.loc.name}`)
      if (cat) fd.append('category', cat)
      fd.append('location_id', r.loc.location_id)
      fd.append('activate', bulkActive ? 'true' : 'false')
      try { await api.upload('/program/assets', fd); rows[i] = { ...r, status: 'done' }; ok++ }
      catch (er) { rows[i] = { ...r, status: errText(er) } }
      setBulk([...rows])
    }
    setBusy(false)
    setBulkDone(`${ok} flyer${ok === 1 ? '' : 's'} uploaded${bulkActive ? ' and active' : ''}.`)
    load()
  }

  const toggle = async a => {
    try { await api.post(`/program/assets/${a.id}/active`, { active: !a.is_active }); setMsg(`${a.title} v${a.version} ${a.is_active ? 'deactivated' : 'activated'}.`); load() } catch (e) { setErr(errText(e)) }
  }
  const view = async a => {
    try { const url = await blobUrl(`/program/assets/${a.id}/preview`); window.open(url, '_blank', 'noopener') } catch (e) { setErr(errText(e)) }
  }

  return (
    <>
      <PageHead title="Assets & Flyers" sub="A clean, version-controlled library of approved location marketing materials.">
        <Chip tone="info">Library · real files only</Chip>
        {isManager && <button type="button" className="sci-btn gold" onClick={() => fileRef.current?.click()}><Icon name="upload" />Upload asset</button>}
      </PageHead>

      <section className="sci-kpis four" aria-label="Asset numbers">
        <Kpi icon="assets" tone="blue" value={data ? all.length : null} label="Total uploaded files" note="All versions" onClick={() => { setKind(''); setFilter(f => ({ ...f, activeOnly: false })) }} />
        <Kpi icon="check" tone="green" value={data ? activeCount : null} label="Active versions" note="Used by campaigns" onClick={() => setFilter(f => ({ ...f, activeOnly: true }))} />
        <Kpi icon="pin" tone="purple" value={data ? locSpecific : null} label="Locations with their own files" note={`of ${homes.length} facilities`} />
        <Kpi icon="doc" tone="gold" value={3} label="Asset types" note="Flyers, logos, facility images" />
      </section>

      <ErrorLine text={err} />
      <Notice text={msg} onClose={() => setMsg('')} />

      <div className={isManager ? 'sci-grid-send' : ''}>
        <Panel title="Asset library" labelledBy="sci-library" aside={<span className="sci-micro sci-muted">{data ? `${num(items.length)} shown` : ''}</span>}>
          <div className="sci-toolbar">
            <div className="sci-grow"><SearchBox id="sci-a-q" value={q} onChange={setQ} placeholder="Search assets by name" label="Search assets" width="100%" /></div>
            <label className="sci-sr" htmlFor="sci-a-cat">Campaign category</label>
            <select id="sci-a-cat" className="sci-input sm" value={filter.category} onChange={e => setFilter(f => ({ ...f, category: e.target.value }))}>
              <option value="">All campaigns</option>{Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <label className="sci-sr" htmlFor="sci-a-loc">Location</label>
            <select id="sci-a-loc" className="sci-input sm" style={{ maxWidth: 200 }} value={filter.location} onChange={e => setFilter(f => ({ ...f, location: e.target.value }))}>
              <option value="">All locations</option><option value="shared">Shared (every location)</option>
              {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
          </div>
          <div className="sci-row-gap" style={{ marginBottom: 12, justifyContent: 'space-between' }}>
            <FilterChips items={[['', 'All assets'], ['flyer', 'Flyers'], ['logo', 'Logos'], ['facility_image', 'Facility images']]} value={kind} onChange={setKind} label="Asset type" />
            <label className="sci-check"><input type="checkbox" checked={filter.activeOnly} onChange={e => setFilter(f => ({ ...f, activeOnly: e.target.checked }))} /> Active only</label>
          </div>
          {!data ? <Loading /> : all.length === 0 ? (
            <Empty icon="image" title="Your approved asset library"
              action={isManager ? <button type="button" className="sci-btn gold" onClick={() => fileRef.current?.click()}><Icon name="upload" />Upload your first asset</button> : null}>
              No files are uploaded yet. Approved flyers, logos and facility images will appear here as the real files, with version history and location assignment.
            </Empty>
          ) : items.length === 0 ? <Empty icon="filter">No file matches these filters.</Empty> : (
            <div className="sci-gallery">
              {items.map(a => {
                const versions = groups[groupKey(a)] || [a]
                return (
                  <article key={a.id} className="sci-asset" aria-label={`${a.title} version ${a.version}`}>
                    <button type="button" className="sci-asset-open" onClick={() => setOpen(a)} aria-label={`Details for ${a.title}`}><Thumb a={a} /></button>
                    <div style={{ minWidth: 0 }}>
                      <b className="sci-asset-title" title={a.title}>{a.title}</b>
                      <div className="sci-micro sci-muted">{KIND_LABEL[a.kind] || a.kind}{a.category ? ` · ${cats[a.category] || a.category}` : ''}</div>
                      <div className="sci-micro sci-muted">{a.location_id ? locName[a.location_id] || 'Location' : 'Every location'} · v{a.version}{versions.length > 1 ? ` of ${versions.length}` : ''}</div>
                    </div>
                    <div className="sci-row-gap" style={{ justifyContent: 'space-between' }}>
                      <Chip tone={a.is_active ? 'ok' : ''} plain>{a.is_active ? 'Active' : 'Inactive'}</Chip>
                      <button type="button" className="sci-btn sm" onClick={() => setOpen(a)}>Details</button>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
          <p className="sci-footnote"><Icon name="info" />Counts come from the uploaded library. Nothing here is example artwork.</p>
        </Panel>

        {isManager && (
          <Panel title="Upload new asset" labelledBy="sci-upload" className="sci-sticky">
            <form className="sci-form one" onSubmit={upload}>
              <div className={`sci-drop${over ? ' over' : ''}`} role="button" tabIndex={0}
                onClick={() => fileRef.current?.click()} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileRef.current?.click() } }}
                onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
                onDrop={e => { e.preventDefault(); setOver(false); pickFile(e.dataTransfer.files?.[0]) }}>
                <Icon name="upload" />
                <b style={{ color: 'var(--ink)' }}>{file ? file.name : 'Drop a file here'}</b>
                <span>{file ? `${Math.round(file.size / 1024)} KB · click to change` : `or browse — PDF, PNG, JPEG, WebP or SVG up to ${MAX_MB} MB`}</span>
              </div>
              <input ref={fileRef} type="file" accept={ACCEPT} className="sci-sr" aria-label="Choose a file" onChange={e => pickFile(e.target.files?.[0] || null)} />
              <Field label="Asset type">
                <select value={form.kind} onChange={e => setForm(f => ({ ...f, kind: e.target.value }))}>
                  <option value="flyer">Flyer (PDF or image)</option><option value="logo">Logo</option><option value="facility_image">Facility image</option>
                </select>
              </Field>
              <Field label="Asset title"><input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} placeholder="e.g. Veteran Planning Guide" /></Field>
              {form.kind === 'flyer' && (
                <Field label="Campaign category">
                  <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
                    {Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </Field>
              )}
              <Field label="Assign location" hint="A location's own file is used first, then the shared one">
                <select value={form.location_id} onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
                  <option value="">Shared to all locations</option>
                  {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                </select>
              </Field>
              <label className="sci-check"><input type="checkbox" checked={form.activate} onChange={e => setForm(f => ({ ...f, activate: e.target.checked }))} /> Make this the active version</label>
              <div className="sci-banner" style={{ margin: 0 }}>
                <span className="sci-banner-icon" aria-hidden="true"><Icon name="info" /></span>
                <div className="sci-banner-body sci-micro">Uploading creates a new version. Activation is a separate choice — campaigns keep using the current active version until you switch.</div>
              </div>
              <button className="sci-btn gold" type="submit" disabled={busy || !file}><Icon name="upload" />{busy ? 'Uploading…' : 'Upload version'}</button>
            </form>
          </Panel>
        )}
        {isManager && (
          <Panel title="Upload many flyers at once" labelledBy="sci-bulk">
            <p className="sci-micro sci-muted" style={{ marginTop: 0 }}>Pick all the flyer files together. Each file name must be the location's name (for example <b>Leak Memory Chapel.jpg</b>) — that's how each flyer lands on its own location.</p>
            <input type="file" multiple accept={ACCEPT} aria-label="Choose flyer files" onChange={e => pickMany(e.target.files)} />
            {bulk.length > 0 && (
              <>
                <Field label="Campaign category">
                  <select value={bulkCat || Object.keys(cats)[0] || ''} onChange={e => setBulkCat(e.target.value)}>
                    {Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </Field>
                <label className="sci-check"><input type="checkbox" checked={bulkActive} onChange={e => setBulkActive(e.target.checked)} /> Make them the active flyers</label>
                <div className="sci-rows" style={{ maxHeight: 260, overflow: 'auto', marginTop: 8 }}>
                  {bulk.map((r, i) => (
                    <div className="sci-kv" key={i}>
                      <span>{r.f.name}</span>
                      <span>{r.status === 'done' ? '✓ uploaded' : r.status ? r.status : r.loc ? `→ ${r.loc.name}` : 'No matching location — skipped'}</span>
                    </div>
                  ))}
                </div>
                <button type="button" className="sci-btn gold" style={{ marginTop: 10 }} disabled={busy || !bulk.some(r => r.loc && r.status !== 'done')} onClick={uploadMany}>
                  <Icon name="upload" />{busy ? 'Uploading…' : `Upload ${bulk.filter(r => r.loc && r.status !== 'done').length} flyers`}
                </button>
              </>
            )}
            {bulkDone && <p className="sci-micro" style={{ marginTop: 8 }}>{bulkDone}</p>}
          </Panel>
        )}
      </div>

      <Drawer open={!!open} onClose={() => setOpen(null)} title={open?.title || ''} sub={open ? `${KIND_LABEL[open.kind] || open.kind} · version ${open.version}` : ''}
        footer={open && (
          <>
            <button type="button" className="sci-btn" onClick={() => view(open)}><Icon name="external" />Open file</button>
            {isManager && <button type="button" className={`sci-btn ${open.is_active ? 'danger' : 'gold'}`} onClick={() => { toggle(open); setOpen(null) }}>{open.is_active ? 'Deactivate' : 'Make active'}</button>}
          </>
        )}>
        {open && (
          <div className="sci-stack" style={{ gap: 12 }}>
            <Thumb a={open} big />
            <dl className="sci-kvlist">
              <div><dt>Status</dt><dd>{open.is_active ? 'Active' : 'Inactive'}</dd></div>
              <div><dt>Location</dt><dd>{open.location_id ? locName[open.location_id] || 'Location' : 'Every location (shared)'}</dd></div>
              <div><dt>Category</dt><dd className={open.category ? '' : 'missing'}>{open.category ? cats[open.category] || open.category : 'Not provided'}</dd></div>
              <div><dt>File</dt><dd>{open.filename || '—'} · {Math.round((open.size_bytes || 0) / 1024)} KB</dd></div>
              <div><dt>Uploaded</dt><dd>{when(open.created_at)}</dd></div>
              <div><dt>Type</dt><dd>{open.content_type || '—'}</dd></div>
            </dl>
            <h3 className="sci-h3">Version history</h3>
            <div className="sci-rows">
              {(groups[groupKey(open)] || [open]).map(v => (
                <div className="sci-kv" key={v.id}>
                  <span>v{v.version} · {when(v.created_at)}</span>
                  <span><Chip tone={v.is_active ? 'ok' : ''} plain>{v.is_active ? 'Active' : 'Inactive'}</Chip></span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Drawer>
    </>
  )
}

/** The real file as a thumbnail; PDFs and failed loads show a document icon. */
function Thumb({ a, big = false }) {
  const [url, setUrl] = useState(null)
  const [failed, setFailed] = useState(false)
  const isImage = /^image\//.test(a.content_type || '') || /\.(png|jpe?g|webp|svg)$/i.test(a.filename || '')
  useEffect(() => {
    if (!isImage) return undefined
    let alive = true
    let made = null
    blobUrl(`/program/assets/${a.id}/preview`).then(u => { made = u; if (alive) setUrl(u) }).catch(() => { if (alive) setFailed(true) })
    return () => { alive = false; if (made) URL.revokeObjectURL?.(made) }
  }, [a.id, isImage])
  return (
    <div className="sci-asset-cover" style={big ? { aspectRatio: '4 / 3' } : undefined}>
      {url ? <img src={url} alt={a.title} loading="lazy" /> : <span style={{ display: 'grid', placeItems: 'center', gap: 6 }}><Icon name="doc" />{!isImage ? (a.filename || 'Document') : failed ? 'Preview unavailable' : 'Loading…'}</span>}
    </div>
  )
}
