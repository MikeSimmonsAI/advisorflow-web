/* Wholesale > Canvassing (build list #17 route by area, #18 door-knock form).
 * You or a manager build a route from a ZIP and hand it to a canvasser. On
 * the canvasser's phone: the doors in order, Directions, one tap for what
 * happened, and a quick form. A hot door goes to their manager as a call-back. */
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { Alert, Empty, EvoApp, PageHead, Seg, Skeleton, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import { dayLabel, tomorrowCentral } from './apptTime'
import './appts.css'
import OutboxBar from './OutboxBar'
import { all, getKept, saveOrKeep, subscribe } from './outbox'
import { ev } from '../engine'

// What is still waiting on this phone shows as done, marked "on this phone".
async function withWaiting(prefix, rows) {
  let ops = []
  try { ops = await all() } catch { /* no store */ }
  const mine = Object.fromEntries(ops.filter((o) => o.key.startsWith(prefix) && !o.error).map((o) => [o.key.slice(prefix.length), o.body]))
  return rows.map((x) => (mine[x.id] ? { ...x, ...mine[x.id], waiting: true } : x))
}
const WAIT = <span className="vd-wait">Saved on this phone - sends when signal is back</span>

const RESULTS = [['not_home', 'Not home'], ['talked', 'Talked'], ['not_interested', 'Not interested'], ['hot', 'Hot lead'], ['bad_address', 'Bad address']]
const LABEL = Object.fromEntries(RESULTS)
const TONE = { hot: 'live', not_interested: 'danger', bad_address: 'danger', talked: 'info' }

function BuildRoute({ onBuilt }) {
  const [zips, setZips] = useState('')
  const [count, setCount] = useState(null)
  const [limit, setLimit] = useState(40)
  const [name, setName] = useState('')
  const [rep, setRep] = useState('')
  const [day, setDay] = useState(tomorrowCentral())
  const [reps, setReps] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  useEffect(() => { api.get('/wholesale/appointments/reps').then((r) => setReps(r.items || [])).catch(() => setReps([])) }, [])
  useEffect(() => {
    setCount(null)
    if (!/^\s*\d{5}(\s*,\s*\d{5})*\s*$/.test(zips)) return undefined
    const t = setTimeout(() => api.get(`/wholesale/canvass/preview?zips=${encodeURIComponent(zips)}`)
      .then((r) => setCount(r.doors)).catch(() => setCount(null)), 300)
    return () => clearTimeout(t)
  }, [zips])
  async function build() {
    setBusy(true); setErr(null)
    try { onBuilt(await api.post('/wholesale/canvass/routes', { zips, limit: Number(limit), name, assigned_to_id: rep || null, route_date: day })) }
    catch (e) { setErr(e.message || 'Could not build it.') } finally { setBusy(false) }
  }
  return (
    <section className="evo-panel" style={{ marginBottom: 16 }}>
      <h2 className="wt-h2" style={{ fontSize: 16, margin: '0 0 10px' }}>Build a route</h2>
      <div className="ap-form">
        <label className="ap-lab">ZIP code(s)<input className="ap-input" inputMode="numeric" placeholder="75208, 75211" value={zips} onChange={(e) => setZips(e.target.value)} /></label>
        <label className="ap-lab">How many doors<input className="ap-input" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value)} /></label>
        <label className="ap-lab">Canvasser
          <select className="ap-input" value={rep} onChange={(e) => setRep(e.target.value)}>
            <option value="">Not decided yet</option>
            {reps.map((r) => <option key={r.user_id} value={r.user_id}>{r.name}{r.role_label ? ` - ${r.role_label}` : ''}</option>)}
          </select></label>
        <label className="ap-lab">Day<input type="date" className="ap-input" value={day} onChange={(e) => setDay(e.target.value)} /></label>
        <label className="ap-lab ap-lab--full">Name (optional)<input className="ap-input" placeholder="e.g. Oak Cliff Saturday" value={name} onChange={(e) => setName(e.target.value)} /></label>
      </div>
      <p className="evo-small evo-muted">{count === null ? ev('Doors come from EvoSense findings in those ZIPs, best first. Owners who opted out are skipped.')
        : `${count} door${count === 1 ? '' : 's'} available there.`}</p>
      <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !count} onClick={build}>{busy ? 'Building…' : 'Build route'}</button>
      <Alert>{err}</Alert>
    </section>
  )
}

function DoorCard({ d, n, onSaved }) {
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ talked_to: d.talked_to || '', phone: d.phone || '', permission: d.permission || '', notes: d.notes || '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  async function save(body, keep = false) {
    setBusy(true); setErr(null)
    try {
      const r = await saveOrKeep({ key: `door:${d.id}`, method: 'PATCH', merge: true, path: `/wholesale/canvass/doors/${d.id}`,
                                   body, label: `the door at ${d.address || 'a house'}` })
      onSaved(r.queued ? { ...d, ...body, waiting: true } : r.result)
      if (!keep) setOpen(false)
    }
    catch (e) { setErr(e.message || 'That did not save.') } finally { setBusy(false) }
  }
  const on = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))
  return (
    <li className={`ap-card${d.result ? ' is-done' : ''}`}>
      <div className="ap-card__top">
        <span className="cv-n">{n}</span>
        <span className="ap-title">{d.address}</span>
        {d.result ? <Tag kind={TONE[d.result]}>{LABEL[d.result]}</Tag> : null}
        {d.handed_off ? <Tag kind="info">Sent to manager</Tag> : null}
      </div>
      {d.waiting ? WAIT : null}
      <div className="ap-meta">
        {d.owner_of_record ? <span>Owner: <strong>{d.owner_of_record}</strong></span> : null}
        {d.why ? <span>{d.why}</span> : null}
      </div>
      <div className="ap-actions">
        {d.directions_url ? <a className="evo-btn evo-btn--secondary evo-btn--sm" href={d.directions_url} target="_blank" rel="noreferrer">Directions</a> : null}
        {RESULTS.filter(([k]) => k !== 'hot').map(([k, l]) => (
          <button key={k} type="button" className={`evo-btn evo-btn--sm ${d.result === k ? 'evo-btn--primary' : 'evo-btn--ghost'}`} disabled={busy}
                  onClick={() => (k === 'talked' ? (setOpen(true), save({ result: k }, true)) : save({ result: k }))}>{l}</button>
        ))}
        <button type="button" className="evo-btn evo-btn--success evo-btn--sm" disabled={busy} onClick={() => setOpen(true)}>Hot lead</button>
      </div>
      {open ? (
        <div className="ap-outcome">
          <div className="ap-form">
            <label className="ap-lab">Who you talked to<input className="ap-input" value={f.talked_to} onChange={on('talked_to')} placeholder="Name, owner or relative" /></label>
            <label className="ap-lab">Best phone<input className="ap-input" inputMode="tel" value={f.phone} onChange={on('phone')} /></label>
            <label className="ap-lab">OK to call or text them?
              <select className="ap-input" value={f.permission} onChange={on('permission')}>
                <option value="">Didn't ask</option><option value="yes">Yes</option><option value="no">No</option>
              </select></label>
            <label className="ap-lab ap-lab--full">What they said<textarea className="ap-input" rows={2} value={f.notes} onChange={on('notes')} /></label>
          </div>
          <div className="ap-actions">
            <button type="button" className="evo-btn evo-btn--success" disabled={busy} onClick={() => save({ ...f, result: 'hot' })}>Hot - send to my manager</button>
            <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={() => save({ ...f, result: d.result || 'talked' })}>Save notes</button>
            <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setOpen(false)}>Close</button>
          </div>
        </div>
      ) : null}
      <Alert>{err}</Alert>
    </li>
  )
}

function RouteView({ id, onBack, canBuild }) {
  const [r, setR] = useState(null)
  const [err, setErr] = useState(null)
  const load = useCallback(() => getKept(`/wholesale/canvass/routes/${id}`)
    .then(async (route) => setR({ ...route, doors: await withWaiting('door:', route.doors || []) }))
    .catch((e) => setErr(e.message)), [id])
  useEffect(() => { load() }, [load])
  // When door knocks kept on the phone go through, show the saved ones.
  useEffect(() => {
    let n = -1
    return subscribe((xs) => { const m = xs.filter((x) => x.key.startsWith('door:')).length; if (n > m) load(); n = m })
  }, [load])
  const replace = (d) => setR((x) => {
    const doors = x.doors.map((y) => (y.id === d.id ? d : y))
    return { ...x, doors, doors_done: doors.filter((y) => y.result).length, hot: doors.filter((y) => y.result === 'hot').length }
  })
  if (err) return <Alert>{err}</Alert>
  if (!r) return <Skeleton rows={5} height={60} />
  return (
    <>
      <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={onBack}>← All routes</button>
      <h2 className="cv-h">{r.name}</h2>
      <Alert kind="info">{r._offline ? 'No signal - this is the route as it was last opened on this phone. Keep knocking; taps save on the phone.' : null}</Alert>
      <div className="ap-meta" style={{ marginBottom: 12 }}>
        <span>{r.route_date ? dayLabel(r.route_date) : 'Any day'}</span><span>{r.assigned_to_name || 'No canvasser yet'}</span>
        <span><strong>{r.doors_done}</strong> of {r.doors_total} doors</span><span><strong>{r.hot}</strong> hot</span>
        {r.status === 'open' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm"
          onClick={() => api.patch(`/wholesale/canvass/routes/${id}`, { status: 'done' }).then(load)}>Mark route done</button> : <Tag>Done</Tag>}
      </div>
      <ul className="ap-list">{r.doors.map((d, i) => <DoorCard key={d.id} d={d} n={i + 1} onSaved={replace} />)}</ul>
    </>
  )
}

export default function Canvass() {
  const [sp, setSp] = useSearchParams()
  const routeId = sp.get('route')
  const [status, setStatus] = useState('open')
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const load = useCallback(() => getKept(`/wholesale/canvass/routes?status=${status}`).then(setData).catch((e) => setErr(e.message)), [status])
  useEffect(() => { if (!routeId) load() }, [load, routeId])
  const go = (id) => setSp(id ? { route: id } : {})
  return (
    <EvoApp world="operations">
      <PageHead world="operations" eyebrow="Wholesale" title="Canvassing"
                sub="Doors to knock, in order. One tap for what happened; hot leads go straight to a manager." />
      <OutboxBar />
      {routeId ? <RouteView id={routeId} onBack={() => go(null)} canBuild={data?.can_build} /> : (
        <>
          {data?.can_build ? <BuildRoute onBuilt={(r) => go(r.id)} /> : null}
          <div className="ap-bar"><Seg label="Routes" value={status} onChange={setStatus} items={[{ key: 'open', label: 'Open' }, { key: 'done', label: 'Done' }, { key: 'all', label: 'All' }]} /></div>
          <Alert>{err}</Alert>
          {!data && !err ? <Skeleton rows={3} height={50} /> : null}
          {data && !data.items.length ? <Empty title="No routes here">{data.can_build ? 'Build one above from a ZIP code.' : 'When your manager gives you a route it shows up here.'}</Empty> : null}
          <ul className="ap-list">
            {(data?.items || []).map((r) => (
              <li key={r.id} className="ap-card">
                <div className="ap-card__top"><span className="ap-title">{r.name}</span>{r.is_test ? <Tag kind="sandbox">Test</Tag> : null}</div>
                <div className="ap-meta"><span>{r.route_date ? dayLabel(r.route_date) : 'Any day'}</span><span>{r.assigned_to_name || 'No canvasser yet'}</span>
                  <span>{r.doors_done} of {r.doors_total} doors</span><span>{r.hot} hot</span></div>
                <div className="ap-actions"><button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={() => go(r.id)}>Open route</button></div>
              </li>
            ))}
          </ul>
        </>
      )}
    </EvoApp>
  )
}
