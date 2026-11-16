/**
 * MOBILE WORKER VIEW (#452/#506): My Jobs · Today · Upcoming · Job details ·
 * Complete Job. Same EvoSys login; the server returns only the jobs this
 * person is assigned to, and access codes only for those jobs.
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'
import { Icon, Loading, errText, useFont, useLoad, useToast, when } from './ui'
import './acquisition.css'
import './hub.css'

export const WORK = '/program/acquisition/work'

function dayKey(iso, tz) {
  try { return new Date(iso).toLocaleDateString('en-CA', { timeZone: tz }) } catch { return '' }
}

function Shell({ children, title, back }) {
  useFont()
  return (
    <div className="aq aq-dark wk">
      <header className="wk-top">{back ? <Link to={back} className="wk-back" aria-label="Back"><Icon name="back" /></Link> : <Icon name="spark" />}
        <b>{title}</b><a className="wk-switch" href="/workspaces">Switch</a></header>
      <main className="wk-main">{children}</main>
    </div>
  )
}

export function WorkerHome() {
  const [tab, setTab] = useState('today')
  const [d, err] = useLoad('/service-ops/jobs?mine=true&status=scheduled,assigned,confirmed,in_progress,needs_attention,completed,customer_review')
  const [me] = useLoad('/service-ops/me')
  if (err) return <Shell title="My Jobs"><div className="aq-err">{errText(err)}</div></Shell>
  if (!d) return <Shell title="My Jobs"><Loading /></Shell>
  const tz = me?.timezone || 'America/Chicago'
  const today = new Date().toLocaleDateString('en-CA', { timeZone: tz })
  const items = d.items.filter(j => j.scheduled_start)
  const shown = tab === 'today' ? items.filter(j => dayKey(j.scheduled_start, j.timezone) === today || j.status === 'in_progress')
    : items.filter(j => dayKey(j.scheduled_start, j.timezone) > today && !['completed', 'customer_review'].includes(j.status))
  return (
    <Shell title="My Jobs">
      <div className="wk-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'today'} className={tab === 'today' ? 'on' : ''} onClick={() => setTab('today')}>Today</button>
        <button role="tab" aria-selected={tab === 'upcoming'} className={tab === 'upcoming' ? 'on' : ''} onClick={() => setTab('upcoming')}>Upcoming</button></div>
      {!shown.length ? <div className="aq-empty">{tab === 'today' ? 'Nothing assigned to you today.' : 'Nothing upcoming yet.'}</div>
        : shown.map(j => (
          <Link key={j.id} to={`${WORK}/jobs/${j.id}`} className="wk-card">
            <div className="wk-card__time">{new Date(j.scheduled_start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: j.timezone })}
              {tab === 'upcoming' && <small>{new Date(j.scheduled_start).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', timeZone: j.timezone })}</small>}</div>
            <div className="wk-card__main"><b>{j.service_type}</b><span>{j.location?.name}</span><span className="hb-muted">{j.location?.address}</span></div>
            <span className={`wk-st wk-st--${j.status}`}>{j.status_label}</span>
          </Link>))}
    </Shell>
  )
}

function Photo({ jid, a }) {
  const [src, setSrc] = useState(null)
  useEffect(() => { let u; fetchObjectUrl(`/service-ops/jobs/${jid}/photos/${a.id}`).then(x => { u = x; setSrc(x) }).catch(() => {}); return () => u && URL.revokeObjectURL(u) }, [jid, a.id])
  return src ? <img src={src} alt={a.caption || 'Job photo'} className="wk-photo" /> : null
}

export function WorkerJob() {
  const { id } = useParams()
  const [j, err, reload] = useLoad(`/service-ops/jobs/${id}`)
  const [notes, setNotes] = useState('')
  const [flag, setFlag] = useState(null)
  const [busy, setBusy] = useState(false)
  const [toast, say] = useToast()
  const nav = useNavigate()
  const act = async (fn, ok) => { setBusy(true); try { await fn(); await reload(); if (ok) say(ok) } catch (e) { say(errText(e)) } finally { setBusy(false) } }
  if (err) return <Shell title="Job" back={WORK}><div className="aq-err">{errText(err)}</div></Shell>
  if (!j) return <Shell title="Job" back={WORK}><Loading /></Shell>
  const loc = j.location_detail || {}
  const live = j.status === 'in_progress'
  const canStart = j.next.includes('in_progress') && j.is_mine
  const missing = j.checklist.filter(i => i.required && !i.done).length
  const upload = async e => {
    const f = e.target.files?.[0]
    if (!f) return
    const fd = new FormData()
    fd.append('file', f)
    await act(() => api.upload(`/service-ops/jobs/${j.id}/photos`, fd), 'Photo saved (private)')
    e.target.value = ''
  }
  return (
    <Shell title={j.service_type} back={WORK}>{toast}
      <section className="wk-sec">
        <div className="wk-row"><Icon name="clock" /><span>{when(j.scheduled_start)} · {j.duration_min} min</span></div>
        <div className="wk-row"><Icon name="building" /><span><b>{loc.name}</b><br />{[loc.address, loc.city, loc.state].filter(Boolean).join(', ')}</span></div>
        {loc.address && <a className="aq-btn aq-btn--sm" href={`https://maps.google.com/?q=${encodeURIComponent([loc.address, loc.city, loc.state].filter(Boolean).join(', '))}`} target="_blank" rel="noreferrer">Directions</a>}
        {(loc.contact_name || loc.contact_phone) && <div className="wk-row"><Icon name="users" /><span>{loc.contact_name}{loc.contact_phone && <> · <a href={`tel:${loc.contact_phone}`}>{loc.contact_phone}</a></>}</span></div>}
        {loc.access_instructions && <div className="wk-note"><b>Access</b>{loc.access_instructions}</div>}
        {loc.special_requirements && <div className="wk-note"><b>Special requirements</b>{loc.special_requirements}</div>}
        {j.instructions && <div className="wk-note"><b>Instructions</b>{j.instructions}</div>}
        {j.supplies && <div className="wk-note"><b>Bring</b>{j.supplies}</div>}
      </section>
      {canStart && <button className="aq-btn aq-btn--primary wk-big" disabled={busy} onClick={() => act(() => api.post(`/service-ops/jobs/${j.id}/start`, {}), 'Started')}>I’ve arrived — start job</button>}
      <section className="wk-sec"><h2>Checklist</h2>
        {j.checklist.length ? j.checklist.map(i => (
          <label key={i.key} className={`wk-check ${i.done ? 'done' : ''}`}><input type="checkbox" checked={!!i.done} disabled={!live || busy}
            onChange={e => act(() => api.post(`/service-ops/jobs/${j.id}/checklist`, { key: i.key, done: e.target.checked }))} />
            <span>{i.label}{!i.required && <small> optional</small>}</span></label>)) : <div className="hb-muted">No checklist for this job.</div>}
        {!live && !['completed', 'customer_review', 'approved'].includes(j.status) && <p className="hb-muted">Start the job to tick items off.</p>}
      </section>
      {(live || j.attachments.length > 0) && <section className="wk-sec"><h2>Photos</h2>
        <div className="wk-photos">{j.attachments.map(a => <Photo key={a.id} jid={j.id} a={a} />)}</div>
        {live && <label className="aq-btn aq-btn--sm">Add photo<input type="file" accept="image/*" capture="environment" hidden onChange={upload} /></label>}</section>}
      {live && <section className="wk-sec"><h2>Finish</h2>
        <textarea aria-label="Completion notes" placeholder="Notes for the office (optional)" value={notes} onChange={e => setNotes(e.target.value)} />
        <button className="aq-btn aq-btn--primary wk-big" disabled={busy || missing > 0}
          onClick={() => act(async () => { await api.post(`/service-ops/jobs/${j.id}/complete`, { notes }); nav(WORK) }, 'Job completed')}>
          {missing ? `${missing} required item${missing > 1 ? 's' : ''} left` : 'Complete job'}</button></section>}
      {['completed', 'customer_review', 'approved'].includes(j.status) && <div className="wk-done"><Icon name="check" /> Completed {j.actual_end ? when(j.actual_end) : ''}</div>}
      {j.is_mine && !['approved', 'cancelled'].includes(j.status) && <section className="wk-sec">
        {!flag ? <div className="wk-flags">
          <button className="aq-btn aq-btn--sm" onClick={() => setFlag({ kind: 'issue', body: '' })}>Flag an issue</button>
          <button className="aq-btn aq-btn--sm" onClick={() => setFlag({ kind: 'help', body: '' })}>Ask for help</button>
          <button className="aq-btn aq-btn--sm" onClick={() => setFlag({ kind: 'reassign', body: '' })}>Request reassignment</button>
          <button className="aq-btn aq-btn--sm" onClick={() => setFlag({ kind: 'note', body: '' })}>Add note</button></div>
          : <><textarea aria-label="Message to the office" autoFocus placeholder="What's going on?" value={flag.body} onChange={e => setFlag({ ...flag, body: e.target.value })} />
            <div className="wk-flags"><button className="aq-btn aq-btn--sm" onClick={() => setFlag(null)}>Cancel</button>
              <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!flag.body.trim() || busy} onClick={() => act(async () => { await api.post(`/service-ops/jobs/${j.id}/note`, flag); setFlag(null) }, 'Sent to the office')}>Send</button></div></>}
      </section>}
    </Shell>
  )
}

// ── The company's own customers (optional buyer portal) ───────────────────
export const BUYER = '/program/acquisition/customer'

export function BuyerPortal() {
  useFont()
  const [home, err] = useLoad('/service-ops/buyer/home')
  const [hist] = useLoad('/service-ops/buyer/history')
  const [inv, , reloadInv] = useLoad('/service-ops/buyer/invoices')
  const [est, , reloadEst] = useLoad('/service-ops/buyer/estimates')
  const [req, , reloadReq] = useLoad('/service-ops/buyer/requests')
  const [form, setForm] = useState(null)
  const [open, setOpen] = useState(null)
  const [toast, say] = useToast()
  const usd = v => '$' + Number(v || 0).toFixed(2)
  const pdf = async id => { try { const u = await fetchObjectUrl(`/service-ops/buyer/invoices/${id}/pdf`); window.open(u, '_blank', 'noopener') } catch (e) { say(errText(e)) } }
  const visit = async id => { try { setOpen(await api.get(`/service-ops/buyer/visits/${id}`)) } catch (e) { say(errText(e)) } }
  if (err) return <div className="aq aq-dark aq-gate"><div className="aq-card"><h1 className="aq-h1">Customer portal unavailable</h1><p className="aq-lede">{errText(err)}</p><a className="aq-btn" href="/workspaces">Back</a></div></div>
  if (!home) return <div className="aq aq-dark"><Loading /></div>
  return (
    <div className="aq aq-dark wk">{toast}
      <header className="wk-top"><Icon name="building" /><b>{home.company} · {home.customer.name}</b><a className="wk-switch" href="/workspaces">Switch</a></header>
      <main className="wk-main wk-wide">
        <section className="wk-sec"><h2>Your locations</h2>{home.locations.map(l => <div key={l.id} className="wk-row"><Icon name="building" /><span><b>{l.name}</b> · {l.category_label}<br />{[l.address, l.city].filter(Boolean).join(', ')}</span></div>)}</section>
        <section className="wk-sec"><h2>Upcoming visits</h2>{home.upcoming.length ? home.upcoming.map(j => <div key={j.id} className="wk-row"><Icon name="calendar" />
          <span><b>{j.service_type}</b> · {j.location?.name}<br />{j.scheduled_start ? when(j.scheduled_start) : 'To be scheduled'} · {j.status_label}</span></div>) : <div className="hb-muted">No visits scheduled.</div>}</section>
        <section className="wk-sec"><h2>Service history</h2>{(hist?.items || []).length ? hist.items.map(j => <button key={j.id} className="wk-card" onClick={() => visit(j.id)}>
          <div className="wk-card__main"><b>{j.service_type}</b><span>{j.location?.name} · {j.actual_end ? when(j.actual_end) : when(j.scheduled_start)}</span></div><span className="wk-st">{j.status_label}</span></button>) : <div className="hb-muted">No completed visits yet.</div>}
          {open && <div className="wk-note"><b>{open.service_type} — {open.status_label}</b>
            {open.checklist.map(i => <div key={i.key}>{i.done ? '☑' : '☐'} {i.label}</div>)}
            {open.completion_notes && <p>{open.completion_notes}</p>}
            {open.status === 'customer_review' && <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={async () => { try { await api.post(`/service-ops/buyer/visits/${open.id}/approve`, {}); setOpen(null); say('Thanks — approved') } catch (e) { say(errText(e)) } }}>Approve this visit</button>}
            <button className="aq-btn aq-btn--sm" onClick={() => setOpen(null)}>Close</button></div>}</section>
        {(est?.items || []).length > 0 && <section className="wk-sec"><h2>Estimates</h2>{est.items.map(e => <div key={e.id} className="wk-row"><Icon name="doc" />
          <span><b>{e.number}</b> · {usd(e.total)} · {e.status}</span>
          {e.can_decide && <><button className="aq-btn aq-btn--sm aq-btn--primary" onClick={async () => { try { await api.post(`/service-ops/buyer/estimates/${e.id}`, { accept: true }); reloadEst() } catch (x) { say(errText(x)) } }}>Approve</button>
            <button className="aq-btn aq-btn--sm" onClick={async () => { try { await api.post(`/service-ops/buyer/estimates/${e.id}`, { accept: false }); reloadEst() } catch (x) { say(errText(x)) } }}>Decline</button></>}</div>)}</section>}
        <section className="wk-sec"><h2>Invoices</h2>{(inv?.items || []).length ? inv.items.map(i => <div key={i.id} className="wk-row"><Icon name="dollar" />
          <span><b>{i.number}</b> · {usd(i.total)} · {i.status_label}{i.amount_reported > 0 ? ' · payment reported, awaiting confirmation' : ''}</span>
          <button className="aq-btn aq-btn--sm" onClick={() => pdf(i.id)}>PDF</button>
          {['issued', 'partially_paid', 'overdue'].includes(i.status) && <button className="aq-btn aq-btn--sm" onClick={async () => { const a = window.prompt('Amount you paid ($)', i.balance); if (!a) return; try { await api.post(`/service-ops/buyer/invoices/${i.id}/payments`, { amount: a, method: 'other' }); reloadInv(); say('Thanks — the company will confirm it') } catch (x) { say(errText(x)) } }}>I paid this</button>}</div>) : <div className="hb-muted">No invoices.</div>}</section>
        <section className="wk-sec"><h2>Requests</h2>
          {!form ? <div className="wk-flags">{(req?.kinds || []).map(k => <button key={k.key} className="aq-btn aq-btn--sm" onClick={() => setForm({ kind: k.key, body: '', location_id: home.locations[0]?.id })}>{k.label}</button>)}</div>
            : <><textarea aria-label="Request" autoFocus placeholder="Tell us what you need" value={form.body} onChange={e => setForm({ ...form, body: e.target.value })} />
              <div className="wk-flags"><button className="aq-btn aq-btn--sm" onClick={() => setForm(null)}>Cancel</button>
                <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!form.body.trim()} onClick={async () => { try { await api.post('/service-ops/buyer/requests', form); setForm(null); reloadReq(); say('Request sent') } catch (e) { say(errText(e)) } }}>Send</button></div></>}
          {(req?.items || []).map(r => <div key={r.id} className="wk-row"><Icon name="chat" /><span><b>{r.kind_label}</b> · {r.status}<br />{r.body}{r.response && <><br /><i>{r.response}</i></>}</span></div>)}
        </section>
      </main>
    </div>
  )
}
