/* THE VISIT RECORD (build list #12 seller interview, #13 walkthrough).
 * Opened from an appointment. The rep fills it at the kitchen table and on
 * the walk-through; the office reads the same record. Server:
 * /wholesale/appointments/{id}/visit and /photos.
 * Works with no signal (#28): typing is kept on the phone as it is typed,
 * and Save / photos wait on the phone until signal comes back (outbox.js). */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { Alert, Drawer, Seg } from '../ds/ds'
import OfferTab from './OfferTab'
import AgreementTab from './AgreementTab'
import OutboxBar from './OutboxBar'
import { all, dropDraft, getKept, noSignal, readDraft, saveOrKeep, subscribe, writeDraft } from './outbox'
import './appts.css'

const LABELS = {
  timeline: { asap: 'As soon as possible', '30_days': 'Within 30 days', '60_90_days': '60-90 days', flexible: 'Flexible', just_exploring: 'Just exploring' },
  occupancy: { owner: 'Owner lives there', tenant: 'Tenant', vacant: 'Vacant', family: 'Family member' },
  behind_on_payments: { yes: 'Yes', no: 'No', unknown: "Don't know" },
  listed_with_agent: { yes: 'Listed now', no: 'Never listed', was_listed: 'Was listed before' },
  condition: { good: 'Good - touch-ups', fair: 'Fair - cosmetic', poor: 'Poor - kitchens, baths, systems', distressed: 'Distressed - full rehab' },
}
const money = (n) => (n || n === 0 ? '$' + Number(n).toLocaleString() : '')

function Choice({ k, value, onChange, label }) {
  return (
    <label className="ap-lab">{label}
      <select className="ap-input" value={value || ''} onChange={(e) => onChange(k, e.target.value)}>
        <option value="">-</option>
        {Object.entries(LABELS[k]).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select></label>
  )
}

function Text({ k, value, onChange, label, area, placeholder, money: isMoney }) {
  const props = { className: 'ap-input', value: value ?? '', placeholder, onChange: (e) => onChange(k, e.target.value) }
  return (
    <label className={`ap-lab${area ? ' ap-lab--full' : ''}`}>{label}
      {area ? <textarea rows={3} {...props} /> : <input inputMode={isMoney ? 'decimal' : undefined} {...props} />}</label>
  )
}

function Interview({ v, set }) {
  const on = (k, val) => set({ ...v, [k]: val })
  return (
    <div className="ap-form">
      <Text k="motivation" label="Why are they selling?" area value={v.motivation} onChange={on} placeholder="In their words" />
      <Choice k="timeline" label="When do they want to sell?" value={v.timeline} onChange={on} />
      <Choice k="occupancy" label="Who lives there?" value={v.occupancy} onChange={on} />
      <Text k="price_wanted" label="Price they want" money value={v.price_wanted} onChange={on} placeholder="$" />
      <Text k="owed" label="What is owed on it" money value={v.owed} onChange={on} placeholder="$ mortgage + liens" />
      <Choice k="behind_on_payments" label="Behind on payments?" value={v.behind_on_payments} onChange={on} />
      <Text k="monthly_payment" label="Monthly payment" money value={v.monthly_payment} onChange={on} placeholder="$" />
      <Text k="back_taxes" label="Back taxes" money value={v.back_taxes} onChange={on} placeholder="$" />
      <Choice k="listed_with_agent" label="Listed with an agent?" value={v.listed_with_agent} onChange={on} />
      <Choice k="condition" label="Condition (their view)" value={v.condition} onChange={on} />
      <Text k="on_deed" label="Who is on the deed?" value={v.on_deed} onChange={on} placeholder="Everyone who has to sign" />
      <Text k="decision_makers" label="Who else decides?" value={v.decision_makers} onChange={on} placeholder="Spouse, siblings, attorney…" />
      <Text k="notes" label="Anything else" area value={v.notes} onChange={on} />
    </div>
  )
}

function Repairs({ r, set, items }) {
  const lines = r.items || {}
  const line = (it) => lines[it] || {}
  const put = (it, patch) => set({ ...r, items: { ...lines, [it]: { ...line(it), ...patch } } })
  const total = Object.values(lines).reduce((t, l) => t + (Number(String(l.amount ?? '').replace(/[$,]/g, '')) || 0), 0)
  return (
    <>
      <label className="ap-lab" style={{ marginBottom: 10 }}>Overall condition
        <select className="ap-input" value={r.condition || ''} onChange={(e) => set({ ...r, condition: e.target.value })}>
          <option value="">-</option>
          {Object.entries(LABELS.condition).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select></label>
      <ul className="vd-repairs">
        {items.map((it) => (
          <li key={it} className={line(it).needed ? 'is-on' : ''}>
            <label className="vd-check"><input type="checkbox" checked={!!line(it).needed}
                   onChange={(e) => put(it, { needed: e.target.checked })} /> {it}</label>
            {line(it).needed ? (
              <div className="vd-line">
                <input className="ap-input" inputMode="decimal" placeholder="$ estimate (optional)"
                       value={line(it).amount ?? ''} onChange={(e) => put(it, { amount: e.target.value })} aria-label={`${it} estimate`} />
                <input className="ap-input" placeholder="What you saw" value={line(it).note ?? ''}
                       onChange={(e) => put(it, { note: e.target.value })} aria-label={`${it} note`} />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      <p className="vd-total">Repairs so far: <strong>{money(total) || '$0'}</strong></p>
    </>
  )
}

function Photo({ p, apptId, onRemove }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    let url = null
    api.get(p.url, { asBlob: true }).then((b) => { url = URL.createObjectURL(b); setSrc(url) }).catch(() => setSrc(null))
    return () => { if (url) URL.revokeObjectURL(url) }
  }, [p.url])
  return (
    <figure className="vd-photo">
      {src ? <img src={src} alt={p.caption || p.room || 'Walkthrough photo'} /> : <div className="vd-photo__ph" />}
      <figcaption>{p.room || 'Photo'}{p.caption ? ` - ${p.caption}` : ''}
        <button type="button" className="vd-x" onClick={() => onRemove(p)} aria-label="Remove photo">×</button></figcaption>
    </figure>
  )
}

function WaitingPhoto({ op }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    const url = URL.createObjectURL(op.blob)
    setSrc(url)
    return () => URL.revokeObjectURL(url)
  }, [op.blob])
  return (
    <figure className="vd-photo is-waiting">
      {src ? <img src={src} alt={op.room || 'Photo waiting to send'} /> : <div className="vd-photo__ph" />}
      <figcaption>{op.room || 'Photo'}<span className="vd-wait">{op.error ? `Not sent: ${op.error}` : 'On this phone - sends when signal is back'}</span></figcaption>
    </figure>
  )
}

function Photos({ apptId, photos, setPhotos, rooms, onSynced }) {
  const [room, setRoom] = useState(rooms[0])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [waiting, setWaiting] = useState([])
  const seen = useRef(0)
  useEffect(() => {
    const mine = (xs) => xs.filter((x) => x.kind === 'photo' && x.appt === apptId)
    const take = (xs) => {
      const w = mine(xs)
      if (w.length < seen.current) onSynced()          // some went through: show the saved ones
      seen.current = w.length
      setWaiting(w)
    }
    const off = subscribe(take)
    all().then(take).catch(() => {})
    return off
  }, [apptId, onSynced])
  async function add(files) {
    setBusy(true); setErr(null)
    try {
      let i = 0
      for (const f of files) {
        i += 1
        const r = await saveOrKeep({ key: `photo:${apptId}:${Date.now()}-${i}`, kind: 'photo', file: f, room,
                                     path: `/wholesale/appointments/${apptId}/photos`, label: `a ${room.toLowerCase()} photo`, appt: apptId })
        if (!r.queued) setPhotos((xs) => (xs.some((x) => x.id === r.result.id) ? xs : [...xs, r.result]))
      }
    } catch (e) { setErr(e.message || 'A photo did not save.') } finally { setBusy(false) }
  }
  async function remove(p) {
    try { await api.delete(p.url); setPhotos((xs) => xs.filter((x) => x.id !== p.id)) }
    catch (e) { setErr(noSignal(e) ? 'No signal - remove it when you have signal.' : (e.message || 'Could not remove it.')) }
  }
  return (
    <div className="vd-photos">
      <div className="ap-bar">
        <select className="ap-input" value={room} onChange={(e) => setRoom(e.target.value)} aria-label="Room">
          {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <label className={`evo-btn evo-btn--primary${busy ? ' is-busy' : ''}`}>
          {busy ? 'Saving…' : 'Take or add photos'}
          <input type="file" accept="image/*" capture="environment" multiple hidden disabled={busy}
                 onChange={(e) => { const fs = [...e.target.files]; e.target.value = ''; if (fs.length) add(fs) }} />
        </label>
      </div>
      <Alert>{err}</Alert>
      {photos.length || waiting.length ? (
        <div className="vd-grid">
          {photos.map((p) => <Photo key={p.id} p={p} apptId={apptId} onRemove={remove} />)}
          {waiting.map((op) => <WaitingPhoto key={op.key} op={op} />)}
        </div>
      ) : <p className="evo-muted evo-small">No photos yet. Pick the room, then take the photo.</p>}
    </div>
  )
}

export default function VisitDrawer({ open, onClose, appt }) {
  const [tab, setTab] = useState('interview')
  const [data, setData] = useState(null)
  const [interview, setInterview] = useState({})
  const [repairs, setRepairs] = useState({ items: {} })
  const [photos, setPhotos] = useState([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  const [note, setNote] = useState(null)
  const typed = useRef(false)
  const draftKey = appt ? `visit:${appt.id}` : null
  const path = appt ? `/wholesale/appointments/${appt.id}/visit` : null
  useEffect(() => {
    if (!open || !appt) return
    setData(null); setMsg(null); setErr(null); setNote(null); typed.current = false
    getKept(path).then((v) => {
      setData(v); setPhotos(v.photos || [])
      // Notes typed on this phone and not yet on the server win over the
      // server's copy: they are newer, and they are what the rep sees.
      const d = readDraft(draftKey)
      if (d) {
        setInterview(d.interview || {}); setRepairs(d.repairs || { items: {} })
        setNote('Notes you typed on this phone are filled in. They have not reached the office yet - tap Save.')
      } else {
        setInterview(v.interview || {}); setRepairs(v.repairs || { items: {} })
        if (v._offline) setNote('No signal - showing this visit as it was last opened on this phone.')
      }
    }).catch((e) => {
      const d = readDraft(draftKey)
      if (d) {
        setData({ questions: { repair_items: Object.keys(d.repairs?.items || {}), rooms: ['Kitchen', 'Other'] }, photos: [] })
        setInterview(d.interview || {}); setRepairs(d.repairs || { items: {} })
        setNote('No signal - showing the notes saved on this phone.')
      } else setErr(noSignal(e) ? 'No signal, and this visit has not been opened on this phone before. Try again when you have signal.' : (e.message || 'Could not load the visit.'))
    })
  }, [open, appt, path, draftKey])
  // Keep what the rep types on the phone as they type it.
  useEffect(() => {
    if (typed.current && draftKey) writeDraft(draftKey, { interview, repairs })
  }, [interview, repairs, draftKey])
  const editInterview = (v) => { typed.current = true; setInterview(v) }
  const editRepairs = (v) => { typed.current = true; setRepairs(v) }
  const reload = useCallback(() => {
    if (path) api.get(path).then((v) => setPhotos(v.photos || [])).catch(() => {})
  }, [path])
  async function save() {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await saveOrKeep({ key: draftKey, method: 'PUT', path, body: { interview, repairs },
                                   label: 'visit notes', draftKey })
      if (r.queued) {
        setMsg('Weak or no signal - saved on this phone. It sends by itself when signal comes back.')
      } else {
        const v = r.result
        dropDraft(draftKey); typed.current = false; setNote(null)
        setData(v); setInterview(v.interview); setRepairs(v.repairs); setPhotos(v.photos || []); setMsg('Saved.')
      }
    } catch (e) { setErr(e.message || 'That did not save.') } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} onClose={onClose} wide title="Visit notes" sub={appt ? `${appt.local_time || ''} · ${appt.address || ''}` : ''}
            footer={<>
              <span className="evo-muted evo-small" style={{ marginRight: 'auto' }}>{data?.updated_at ? `Last saved by ${data.updated_by_name || 'someone'}` : 'Not saved yet'}</span>
              <button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Close</button>
              <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !data} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
            </>}>
      <OutboxBar />
      <Seg label="Visit notes" value={tab} onChange={setTab}
           items={[{ key: 'interview', label: 'Interview' }, { key: 'repairs', label: 'Repairs' }, { key: 'photos', label: 'Photos', count: photos.length }, { key: 'offer', label: 'Offer' }, { key: 'agreement', label: 'Agreement' }]} />
      <Alert kind="info">{note}</Alert>
      <div style={{ marginTop: 14 }}>
        {!data ? (err ? null : <p className="evo-muted">Loading…</p>)
          : tab === 'interview' ? <Interview v={interview} set={editInterview} />
            : tab === 'repairs' ? <Repairs r={repairs} set={editRepairs} items={data.questions.repair_items} />
              : tab === 'offer' ? <OfferTab apptId={appt.id} repairsTotal={repairs.total} />
                : tab === 'agreement' ? <AgreementTab apptId={appt.id} sellerGuess={interview.on_deed || appt.seller_name || ''} />
                : <Photos apptId={appt.id} photos={photos} setPhotos={setPhotos} rooms={data.questions.rooms} onSynced={reload} />}
      </div>
      {tab === 'photos' ? <p className="evo-muted evo-small">Photos save as you take them. With no signal they stay on this phone and send by themselves.</p> : null}
      <Alert kind="ok">{msg}</Alert>
      <Alert>{err}</Alert>
    </Drawer>
  )
}
