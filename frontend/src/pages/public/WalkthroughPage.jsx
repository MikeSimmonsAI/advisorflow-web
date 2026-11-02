// Issue #452 P4 - a prospect business picks a time for a cleaning walkthrough.
// No login: the link is the authorization. Times come from the platform's one
// slot engine against the client's connected calendar; nothing is offered that
// the calendar doesn't show as free.
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE } from '../../api/client'

const S = {
  page: { minHeight: '100vh', background: '#f6f3ff', fontFamily: 'Inter, Arial, sans-serif', color: '#1f1640', padding: '32px 16px' },
  card: { maxWidth: 560, margin: '0 auto', background: '#fff', borderRadius: 16, padding: 24, boxShadow: '0 6px 24px rgba(60,30,140,.08)' },
  h1: { fontSize: 22, margin: '0 0 4px' },
  sub: { color: '#6b6290', margin: '0 0 18px', fontSize: 14 },
  days: { display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 6, marginBottom: 14 },
  day: on => ({ flex: '0 0 auto', border: '1px solid ' + (on ? '#6d3fd9' : '#ddd3f5'), background: on ? '#6d3fd9' : '#fff',
    color: on ? '#fff' : '#1f1640', borderRadius: 10, padding: '8px 12px', cursor: 'pointer', fontSize: 13, textAlign: 'center' }),
  slots: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 8 },
  slot: on => ({ border: '1px solid ' + (on ? '#6d3fd9' : '#ddd3f5'), background: on ? '#efe8ff' : '#fff', borderRadius: 10,
    padding: '10px 8px', cursor: 'pointer', fontSize: 14 }),
  btn: { marginTop: 18, width: '100%', background: '#6d3fd9', color: '#fff', border: 0, borderRadius: 10, padding: '12px', fontSize: 15, cursor: 'pointer' },
  note: { color: '#6b6290', fontSize: 13, marginTop: 10 },
  err: { background: '#fde8ee', color: '#8f1c35', borderRadius: 10, padding: '10px 12px', marginTop: 12 },
  ok: { background: '#e6f6ec', color: '#17633a', borderRadius: 10, padding: '14px', marginTop: 8 },
}

function centralToday() {
  const s = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Chicago' })
  const [y, m, d] = s.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}
const iso = d => d.toISOString().slice(0, 10)
const dayLabel = d => d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' })
const timeLabel = s => { const [h, m] = s.slice(11, 16).split(':').map(Number); return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}` }
const whenLabel = s => `${dayLabel(new Date(s.slice(0, 10) + 'T00:00:00Z'))} at ${timeLabel(s)}`

export default function WalkthroughPage() {
  const { token } = useParams()
  const base = `${API_BASE}/acquisition-public/walkthrough/${token}`
  const [info, setInfo] = useState(null)
  const [fail, setFail] = useState(null)
  const [day, setDay] = useState(null)
  const [slots, setSlots] = useState(null)
  const [reason, setReason] = useState(null)
  const [pick, setPick] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const [tick, setTick] = useState(0)

  const days = useMemo(() => {
    const out = []; const t = centralToday()
    for (let i = 0; i <= (info?.days || 14); i++) {
      const d = new Date(t.getTime() + i * 86400000)
      if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.push(d)
    }
    return out
  }, [info])

  useEffect(() => {
    fetch(base).then(async r => { if (!r.ok) throw new Error((await r.json()).detail || 'This link is not valid'); return r.json() })
      .then(setInfo).catch(e => setFail(e.message))
  }, [base])
  useEffect(() => { if (info && !day && days.length) setDay(iso(days[0])) }, [info, day, days])
  useEffect(() => {
    if (!day || !info || info.status !== 'pending') return
    setSlots(null); setReason(null); setPick(null)
    fetch(`${base}/slots?date=${day}`).then(r => r.json()).then(j => { setSlots(j.slots || []); setReason(j.reason || null) })
      .catch(() => { setSlots([]); setReason('Times could not be loaded. Please try again.') })
  }, [base, day, info, tick])

  const book = async () => {
    setBusy(true); setFail(null)
    try {
      const r = await fetch(`${base}/book`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slot: pick }) })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail || 'Could not book that time')
      setDone(j)
    } catch (e) { setFail(e.message); setTick(t => t + 1) } finally { setBusy(false) }
  }

  if (fail && !info) return <div style={S.page}><div style={S.card}><h1 style={S.h1}>Walkthrough</h1><div style={S.err}>{fail}</div></div></div>
  if (!info) return <div style={S.page}><div style={S.card}>Loading…</div></div>
  const header = (
    <><h1 style={S.h1}>{info.company}: free cleaning walkthrough</h1>
      <p style={S.sub}>{info.for ? `For ${info.for} · ` : ''}about {info.minutes} minutes · Central Time</p></>)
  if (done || info.status === 'booked' || info.status === 'confirmed') {
    const t = done?.booked_time || info.booked_time
    return <div style={S.page}><div style={S.card}>{header}<div style={S.ok}><b>You're booked.</b><br />{t ? whenLabel(t) : ''}. We'll see you then. Just reply to our email if anything changes.</div></div></div>
  }
  if (info.status !== 'pending') return <div style={S.page}><div style={S.card}>{header}<div style={S.err}>This link has expired. Reply to our email and we'll send a new one.</div></div></div>
  return (
    <div style={S.page}><div style={S.card}>
      {header}
      <div style={S.days}>{days.map(d => <button key={iso(d)} style={S.day(iso(d) === day)} onClick={() => setDay(iso(d))}>{dayLabel(d)}</button>)}</div>
      {slots === null ? <div style={S.note}>Checking the calendar…</div>
        : slots.length ? <div style={S.slots}>{slots.map(s => <button key={s} style={S.slot(s === pick)} onClick={() => setPick(s)}>{timeLabel(s)}</button>)}</div>
          : <div style={S.note}>{reason || 'No open times this day. Try another day.'}</div>}
      <button style={{ ...S.btn, opacity: pick && !busy ? 1 : .5 }} disabled={!pick || busy} onClick={book}>{busy ? 'Booking…' : pick ? `Book ${whenLabel(pick)}` : 'Pick a time'}</button>
      {fail && <div style={S.err}>{fail}</div>}
      <p style={S.note}>Only times that are actually open are shown.</p>
    </div></div>
  )
}
