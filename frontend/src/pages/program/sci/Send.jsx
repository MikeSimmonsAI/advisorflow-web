/* Choose & Send: every loaded contact, one row per person. Tick who should get
   outreach, look at exactly what they'll receive, approve. Only approved
   contacts ever get an automated text or email (the server enforces it:
   programs/send_approval.py + identity.send_refusal). Holds, reviews, DNC and
   opt-outs still win - a blocked contact can't be ticked. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, PageHead, errText, num, when } from './ui'

const SHOW = [['not_approved', 'Not sent yet'], ['approved', 'Approved'], ['all', 'Everyone']]
const PAGE = 100

export default function Send({ locations, isManager, locationId }) {
  const [show, setShow] = useState('not_approved')
  const [search, setSearch] = useState('')
  const [loc, setLoc] = useState(locationId || '')
  const [fam, setFam] = useState('')
  const [fams, setFams] = useState([])
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [picked, setPicked] = useState({})          // lead_id -> name
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  useEffect(() => { api.get('/program/campaigns').then(d => setFams(Array.isArray(d) ? d : [])).catch(() => {}) }, [])
  const load = useCallback(() => {
    const q = new URLSearchParams({ show, limit: String(PAGE), offset: String(offset) })
    if (search.trim()) q.set('search', search.trim())
    if (loc) q.set('location_id', loc)
    if (fam) q.set('family', fam)
    api.get(`/program/send/contacts?${q}`).then(d => { setData(d); setErr('') }).catch(e => setErr(errText(e)))
  }, [show, search, loc, fam, offset])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])
  useEffect(() => { setOffset(0) }, [show, search, loc, fam])

  const ids = Object.keys(picked)
  const toggle = row => setPicked(p => {
    const n = { ...p }
    if (n[row.lead_id]) delete n[row.lead_id]; else n[row.lead_id] = row.name
    return n
  })
  const pickable = (data?.items || []).filter(r => !r.approved && !r.blocked)
  const allOnPage = pickable.length > 0 && pickable.every(r => picked[r.lead_id])
  const togglePage = () => setPicked(p => {
    const n = { ...p }
    if (allOnPage) pickable.forEach(r => { delete n[r.lead_id] })
    else pickable.forEach(r => { n[r.lead_id] = r.name })
    return n
  })

  const openPreview = async () => {
    setBusy(true); setErr(''); setMsg('')
    try { setPreview(await api.post('/program/send/preview', { lead_ids: ids })) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const approve = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/program/send/approve', { lead_ids: ids, confirm: `SEND ${ids.length}` })
      const parts = [`${num(r.approved)} approved.`]
      if (r.texting) parts.push(`${num(r.texting)} will get their first text at the next allowed hour in their own time zone (never at night).`)
      if (r.email_only) parts.push(`${num(r.email_only)} have email only.`)
      parts.push(r.email_sending_on ? 'Emails go out on the email schedule.' : 'Emails are switched off on the server right now, so only texts go for now.')
      if (r.skipped?.length) parts.push(`${num(r.skipped.length)} skipped: ${r.skipped.map(s => s.reason).join(', ')}.`)
      setMsg(parts.join(' ')); setPicked({}); setPreview(null); load()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const unapprove = async row => {
    try { await api.post('/program/send/unapprove', { lead_ids: [row.lead_id] }); setMsg(`${row.name}: approval taken back, no more automated messages.`); load() } catch (e) { setErr(errText(e)) }
  }

  const homes = (locations || []).filter(l => !l.is_review_bucket)
  const max = preview?.max_per_approval || 25
  const ready = (preview?.items || []).filter(i => !i.blocked)

  return (
    <>
      <PageHead title="Choose & Send" sub="Everyone you loaded is here. Tick who should get outreach, check what they'll receive, then approve. Nobody else gets anything." />

      <section className="sci-panel sci-pad" aria-labelledby="sci-send">
        <div className="sci-panel-head">
          <div>
            <h2 id="sci-send">Contacts</h2>
            <div className="sci-filterchips" role="group" aria-label="Show" style={{ marginTop: 10 }}>
              {SHOW.map(([k, label]) => (
                <button key={k} type="button" aria-pressed={show === k} onClick={() => setShow(k)}>{label}</button>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <select className="sci-input" style={{ minHeight: 40, fontSize: 13 }} aria-label="Location" value={loc} onChange={e => setLoc(e.target.value)}>
              <option value="">All locations</option>
              {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
            <select className="sci-input" style={{ minHeight: 40, fontSize: 13 }} aria-label="Campaign" value={fam} onChange={e => setFam(e.target.value)}>
              <option value="">All campaigns</option>
              {fams.map(f => <option key={f.key} value={f.key}>{f.name}</option>)}
            </select>
            <div className="sci-search" style={{ width: 240 }}>
              <label htmlFor="sci-send-search" className="sci-sr">Search contacts</label>
              <input id="sci-send-search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name, phone, email or Lead ID" />
            </div>
          </div>
        </div>

        <ErrorLine text={err} />
        {msg && <div className="sci-okmsg" role="status">{msg}</div>}

        {isManager && (
          <div className="sci-banner" style={{ margin: '8px 0 12px' }}>
            <div><strong>{ids.length ? `${num(ids.length)} ticked` : 'Nobody ticked yet'}</strong>
              {ids.length > max ? ` — approve at most ${max} at a time.` : ' — tick contacts below, then check and approve.'}</div>
            <div style={{ display: 'flex', gap: 8 }}>
              {ids.length > 0 && <button type="button" className="sci-btn" onClick={() => { setPicked({}); setPreview(null) }}>Clear</button>}
              <button type="button" className="sci-btn primary" disabled={!ids.length || ids.length > max || busy} onClick={openPreview}>
                Check what they'll get ({num(ids.length)})
              </button>
            </div>
          </div>
        )}

        {preview && (
          <div className="sci-panel sci-pad" style={{ marginBottom: 14, borderColor: '#16455d' }} aria-live="polite">
            <h2 style={{ marginTop: 0 }}>About to send to {num(ready.length)} {ready.length === 1 ? 'person' : 'people'}</h2>
            {!preview.email_sending_on && <div className="sci-micro sci-muted" style={{ marginBottom: 8 }}>Emails are switched off on the server right now, so only the texts go out.</div>}
            <div className="sci-tablewrap">
              <table className="sci-table">
                <thead><tr><th>Who</th><th>Location</th><th>First text they get</th><th>Email</th></tr></thead>
                <tbody>
                  {preview.items.map(i => (
                    <tr key={i.lead_id}>
                      <td><b style={{ fontWeight: 600 }}>{i.name || '—'}</b>{i.blocked && <div><Chip tone="bad">Won't send: {i.blocked}</Chip></div>}</td>
                      <td className="sci-small">{i.location || '—'}</td>
                      <td className="sci-small" style={{ maxWidth: 520 }}>{i.gets_text ? (i.text || 'No text template for this campaign') : <span className="sci-muted">No phone — no text</span>}</td>
                      <td className="sci-small">{i.gets_email ? 'Yes' : 'No'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
              <button type="button" className="sci-btn" onClick={() => setPreview(null)} disabled={busy}>Back</button>
              <button type="button" className="sci-btn gold" onClick={approve} disabled={busy || !ready.length}>
                {busy ? 'Approving…' : `Approve & send to ${num(ready.length)}`}
              </button>
            </div>
            <div className="sci-micro sci-muted" style={{ marginTop: 8 }}>Their follow-up texts continue on their own after this, on the SCI default cadence. A reply or STOP ends them.</div>
          </div>
        )}

        <div className="sci-micro sci-muted" style={{ marginBottom: 8 }}>
          {data ? `${num(data.total)} contact(s)` : 'Loading…'}
        </div>
        {data && !data.items.length ? <Empty title="No contacts here">{show === 'not_approved' ? 'Everyone in this view is already approved, or nothing has been loaded yet.' : 'Nothing matches.'}</Empty> : (
          <div className="sci-tablewrap">
            <table className="sci-table">
              <thead>
                <tr>
                  <th style={{ width: 36 }}>{isManager && pickable.length > 0 && (
                    <input type="checkbox" aria-label="Tick everyone on this page" checked={allOnPage} onChange={togglePage} />)}</th>
                  <th>Contact</th><th>Location</th><th>Campaign</th><th>Status</th><th><span className="sci-sr">Action</span></th>
                </tr>
              </thead>
              <tbody>
                {(data?.items || []).map(r => (
                  <tr key={r.lead_id}>
                    <td>{isManager && !r.approved && !r.blocked && (
                      <input type="checkbox" aria-label={`Tick ${r.name}`} checked={!!picked[r.lead_id]} onChange={() => toggle(r)} />)}</td>
                    <td>
                      <b style={{ fontWeight: 600 }}>{r.name}</b>
                      <div className="sci-micro sci-muted">{[r.phone, r.email].filter(Boolean).join(' · ') || 'No contact details'} · Lead {r.source_lead_id}</div>
                    </td>
                    <td className="sci-small">{r.location || <Chip tone="warn">No location</Chip>}</td>
                    <td className="sci-small">{r.campaign || '—'}</td>
                    <td>
                      {r.approved ? <Chip tone="ok">Approved {when(r.approved_at)}</Chip>
                        : r.blocked ? <Chip tone="bad">{r.blocked}</Chip>
                          : <Chip>Not sent</Chip>}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      {isManager && r.approved && <button type="button" className="sci-btn sm danger" onClick={() => unapprove(r)}>Stop</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > PAGE && (
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 10 }}>
            <button type="button" className="sci-btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
            <span className="sci-micro sci-muted" style={{ alignSelf: 'center' }}>{num(offset + 1)}–{num(Math.min(offset + PAGE, data.total))} of {num(data.total)}</span>
            <button type="button" className="sci-btn sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        )}
      </section>
    </>
  )
}
