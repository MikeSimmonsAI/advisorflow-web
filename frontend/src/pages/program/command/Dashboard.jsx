/* SCI LIVE - Mike's four-screen mockup (Oct 16 package): side menu, top
   bar, screen tabs (01 Performance Home / 02 Metric Drill-down / 03 Inbox /
   04 Family Journey) and the Performance Home panels. Every number is real
   production data and opens the exact records behind it. On a phone the
   side menu and screen tabs give way to the bottom tabs. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getCurrentUser } from '../../../api/client'

const CT = { timeZone: 'America/Chicago' }
const n = v => (v === null || v === undefined ? '—' : Number(v).toLocaleString())
const clock = d => d.toLocaleTimeString([], { ...CT, hour: 'numeric', minute: '2-digit' })
const ctDate = (iso, o) => new Date(iso.length === 10 ? iso + 'T17:00:00Z' : iso).toLocaleDateString([], { ...CT, ...o })
const initials = s => (s || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase()
const titled = s => (s || '').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
const isToday = iso => new Date(iso).toLocaleDateString('en-CA', CT) === new Date().toLocaleDateString('en-CA', CT)
const dayWord = iso => (isToday(iso) ? 'today' : ctDate(iso, { weekday: 'long', month: 'short', day: 'numeric' }))

const P = {
  live: <><rect x="3" y="3" width="7" height="8" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="15" width="7" height="6" rx="1" /></>,
  fam: <path d="M12 3 21 12 12 21 3 12z" />,
  conv: <><rect x="3" y="5" width="18" height="14" rx="1.5" /><path d="M3 9h18M3 13h18" /></>,
  ai: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
  cad: <><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M7 4v16M11 4v16M15 4v16" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  appt: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5h4" /></>,
  cal: <><rect x="4" y="4" width="16" height="16" rx="1.5" /><rect x="8" y="8" width="8" height="8" /></>,
  ana: <><rect x="3" y="3" width="18" height="18" rx="1.5" /><path d="M3 15l6-6 4 4 8-8M3 21l18-18" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1" /></>,
}
const Ico = ({ n: name, s = 18 }) => (
  <svg viewBox="0 0 24 24" width={s} height={s} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{P[name]}</svg>
)

/* The side menu lives in SciNav.jsx - one menu for SCI Live and the program
   screens alike. Re-exported here for older imports. */
export { SideNav } from './SciNav'

export function TopBar({ batch }) {
  const [now, setNow] = useState(new Date())
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 30000); return () => clearInterval(t) }, [])
  const u = getCurrentUser() || {}
  const role = u.role === 'god_admin' ? 'Owner' : u.role === 'admin' ? 'Workspace admin' : 'Workspace advisor'
  return (
    <header className="sl-top">
      <div><h1>SCI Live <span>/ Outreach Operations</span></h1>
        <p>Family communication · AI conversations · In-person appointments</p></div>
      <div className="sl-top-r">
        <span className="sl-top-st"><b className={batch?.on ? 'ok' : 'off'}>● Automated intake {batch?.on ? 'ON' : 'OFF'}</b>
          <small>{now.toLocaleDateString([], { ...CT, weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })} · {clock(now)} CT</small></span>
        <span className="sl-av">{initials(u.full_name)}</span>
        <span className="sl-top-u"><b>{u.full_name || 'Signed in'}</b><small>{role}</small></span>
      </div>
    </header>
  )
}

export function ScreenTabs({ screen, go }) {
  const t = [['home', '01 · Performance Home'], ['drill', '02 · Metric Drill-down'], ['inbox', '03 · Inbox'], ['journey', '04 · Family Journey']]
  return <div className="sl-tabs" role="tablist">{t.map(([k, l]) => (
    <button key={k} role="tab" aria-selected={screen === k} className={screen === k ? 'on' : ''} onClick={() => go(k)}>{l}</button>))}
    <span className="sl-tabs-note">Live production data · Central Time</span></div>
}

export function Home({ a, sched, openMetric, openFamily }) {
  if (!a) return <p className="cmd-muted">Loading live numbers…</p>
  const o = a.overview || {}
  const pipe = Object.fromEntries((o.pipeline || []).map(p => [p.key, p.value]))
  const sn = o.scheduled_new || {}
  const firstToday = (o.today || []).find(r => r.key === 'first')?.value || 0
  const ex = a.streams?.existing, nw = a.streams?.new
  const b = a.daily_batch || {}
  const go = (metric, extra) => openMetric({ metric, ...(extra || {}) })
  const retest = a.link_retest || {}
  const queued = (sched?.items || []).filter(i => i.next_touch_at).sort((x, y) => new Date(x.next_touch_at) - new Date(y.next_touch_at))
  const upcoming = queued.filter(i => new Date(i.next_touch_at) > new Date(Date.now() - 10 * 60000))
  const sentShare = (sn.value || 0) + firstToday ? Math.round((100 * firstToday) / ((sn.value || 0) + firstToday)) : 0
  return <div className="sl-home-body">
    {sn.value ? (
      <section className="sl-hero2">
        <div><strong>{sn.value} new families queued{sn.first_start ? ` for ${dayWord(sn.first_start)}` : ''}</strong>
          <p>First touches start {sn.first_start ? clock(new Date(sn.first_start)) : 'when sending opens'}. Existing family follow-ups continue independently.</p>
          <p className="sl-hero2-consent"><b>{n(sn.sms_ready)} of {n(sn.value)}</b> have SMS consent of record and will be texted.
            {sn.no_consent ? <> <b>{n(sn.no_consent)}</b> have none: their texts are blocked by the consent gate (emails still go).</> : null}</p></div>
        {sn.first_start && <div className="sl-hero2-date"><b>{ctDate(sn.first_start, { weekday: 'long', month: 'short', day: 'numeric' })}</b>
          <span>{clock(new Date(sn.first_start))} — {clock(new Date(sn.last_start))} CT</span>
          <span>1 new first touch / {b.spacing_minutes || 10} min</span></div>}
      </section>
    ) : (
      <section className="sl-hero2">
        <div><strong>{b.on ? 'No new families waiting to start' : 'Automatic enrollment is OFF'}</strong>
          <p>{b.on ? `The next daily batch adds ${b.count} new families${a.next_batch ? ` on ${ctDate(a.next_batch, { weekday: 'long', month: 'short', day: 'numeric' })}` : ''} at ${b.start}.`
            : 'No new families are being added. Turn it on in Choose & Send › Every day, automatically.'}</p></div>
      </section>
    )}

    <div className="sl-cards5">
      <button className="sl-metric" onClick={() => go('loaded')}><span>Imported families</span><b>{n(pipe.loaded)}</b><small>Real leads in source pool</small></button>
      <button className="sl-metric" onClick={() => go('approved')}><span>Approved total</span><b>{n(pipe.approved)}</b>
        <small>{n((pipe.approved || 0) - (sn.value || 0))} earlier + {n(sn.value)} queued</small></button>
      <button className="sl-metric" onClick={() => go('scheduled_new')}><span>New families queued</span><b className="blue">{n(sn.value)}</b>
        <small>{n(sn.sms_ready)} SMS-eligible · {n(sn.no_consent)} no SMS consent</small></button>
      <button className="sl-metric" onClick={() => go('in_followup')}><span>Active follow-up cadence</span><b>{n(o.in_followup)}</b><small>Previously contacted families</small></button>
      <button className="sl-metric" onClick={() => openMetric({ metric: 'appointments', range: { preset: 'all' } })}><span>Confirmed new appointments</span>
        <b className="green">{a.metrics?.appointments?.all_time ? n(a.metrics.appointments.all_time) : <i className="sl-dash-line" />}</b>
        <small>{a.metrics?.appointments?.all_time ? 'On the booking calendar' : 'Await verified booking result'}</small></button>
    </div>

    <div className="sl-split">
      <section className="sl-card">
        <h3>Two independent outreach streams <button className="sl-a" onClick={() => go('attempts', { range: { preset: '7d' } })}>View touch analytics →</button></h3>
        <div className="sl-stream">
          <div><b>NEW family first touches</b><small>{sn.first_start ? `Next window · ${ctDate(sn.first_start, { weekday: 'long', month: 'short', day: 'numeric' })}`
            : nw ? `Most recent day · ${ctDate(nw.date, { month: 'short', day: 'numeric' })}` : 'None sent yet'}</small></div>
          <button className="sl-tag blue" onClick={() => go(sn.value ? 'scheduled_new' : 'new_families', { range: { preset: 'today' } })}>
            {sn.value ? `${sn.value} queued · ${sn.sms_ready || 0} SMS-eligible` : `${firstToday} sent today`}</button>
        </div>
        <div className="sl-prog"><i style={{ width: `${sentShare}%` }} /><span>{firstToday} of {firstToday + (sn.value || 0)} sent</span></div>
        <div className="sl-stream">
          <div><b>EXISTING family follow-ups</b><small>{ex ? `Most recent sending day · ${ctDate(ex.date, { month: 'short', day: 'numeric' })}` : 'None yet'}</small></div>
          {ex && <button className="sl-tag green" onClick={() => go('attempts', { touch: '2+', range: { preset: 'custom', start: ex.date, end: ex.date } })}>{ex.sent} sent to provider</button>}
        </div>
        {ex && <>
          <button className="sl-row" onClick={() => go('attempts', { touch: '2', range: { preset: 'custom', start: ex.date, end: ex.date } })}><span>2nd touch</span><b>{ex.touch2}</b></button>
          <button className="sl-row" onClick={() => go('attempts', { touch: '3+', range: { preset: 'custom', start: ex.date, end: ex.date } })}><span>3rd+ touch</span><b>{ex.touch3plus}</b></button>
          <button className="sl-row" onClick={() => go('delivered', { touch: '2+', range: { preset: 'custom', start: ex.date, end: ex.date } })}><span>Confirmed delivered</span><b>{ex.delivered}</b></button>
          <button className="sl-row" onClick={() => go('failed', { touch: '2+', range: { preset: 'custom', start: ex.date, end: ex.date } })}><span>Failed</span><b className={ex.failed ? 'red' : ''}>{ex.failed}</b></button>
        </>}
      </section>

      <section className="sl-card">
        <h3>Delivery readiness <Link className="sl-a" to="/program?tab=send">Open controls →</Link></h3>
        <div className="sl-row"><span>Weekday automatic enrollment</span><em className={`sl-tag ${b.on ? 'green' : 'orange'}`}>{b.on ? `ON · ${b.count} / day` : 'OFF'}</em></div>
        <div className="sl-row"><span>Next automatic batch</span><b>{a.next_batch ? ctDate(a.next_batch, { weekday: 'short', month: 'short', day: 'numeric' }) : '—'}{a.next_batch && b.start ? ` · ${b.start}` : ''}</b></div>
        <div className="sl-row"><span>Quiet hours &amp; STOP / DNC</span><em className="sl-tag green">Gated</em></div>
        <button className="sl-row" onClick={() => go(sn.no_consent ? 'scheduled_no_consent' : 'scheduled_sms_ready')}><span>SMS consent of record (queued families)</span>
          <em className={`sl-tag ${sn.value && sn.sms_ready === sn.value ? 'green' : 'red'}`}>{n(sn.sms_ready)} of {n(sn.value)}</em></button>
        <button className="sl-row" onClick={() => openMetric({ metric: 'consent_blocked', range: { preset: 'today' } })}><span>Texts blocked today - no consent</span>
          <b>{n((o.today || []).find(r => r.key === 'blocked')?.value)}</b></button>
        <div className="sl-row"><span>SMS link retest (owner number)</span>
          <em className={`sl-tag ${retest.status === 'delivered' ? 'green' : 'orange'}`}>{retest.status === 'delivered' ? 'Delivered'
            : retest.at && isToday(retest.at) ? (retest.status || 'Sent') : '9:05 AM pending'}</em></div>
        <p className="sl-warn">Delivery confirmation comes from Twilio callbacks. A queued or submitted text is <b>not</b> counted as delivered.
          A text is only sent with an SCI SMS consent of record; the daily batch only picks families that have one.</p>
      </section>
    </div>

    <div className="sl-split">
      <section className="sl-card">
        <h3>Next scheduled first touches <button className="sl-a" onClick={() => go('scheduled_new')}>View queue →</button></h3>
        {!upcoming.length ? <p className="cmd-muted">{b.on ? 'Nothing queued right now.' : 'Automatic enrollment is off.'}</p> : (
          <table className="sl-table">
            <thead><tr><th>Slot (CT)</th><th>Recipient</th><th>Stream</th><th>Status</th></tr></thead>
            <tbody>{upcoming.slice(0, 6).map(i => (
              <tr key={i.lead_id} onClick={() => openFamily(i.lead_id, 'journey')}>
                <td>{isToday(i.next_touch_at) ? '' : ctDate(i.next_touch_at, { weekday: 'short' }) + ' '}{clock(new Date(i.next_touch_at))}</td>
                <td className="sl-ell">{titled(i.name)} · {i.location}</td>
                <td>First touch</td>
                <td>{(i.text || '').startsWith('No SMS consent') ? <em className="sl-tag orange">Text blocked: no consent</em> : <em className="sl-tag blue">Queued</em>}</td></tr>))}</tbody>
          </table>)}
      </section>
      <section className="sl-card">
        <h3>Sending schedule <Link className="sl-a" to="/program?tab=health">Operations log →</Link></h3>
        {[
          retest.status !== 'delivered' ? ['9:05 AM', 'Owner-number SMS link retest (Twilio ticket)'] : null,
          sn.first_start ? [clock(new Date(sn.first_start)), `First new-family text · ${ctDate(sn.first_start, { weekday: 'short', month: 'short', day: 'numeric' })}`] : null,
          sn.last_start ? [clock(new Date(sn.last_start)), 'Last new-family text of this batch'] : null,
          a.next_batch ? [b.start || '9:00', `Next automatic batch · ${ctDate(a.next_batch, { weekday: 'short', month: 'short', day: 'numeric' })}`] : null,
        ].filter(Boolean).map(([t, l]) => <div key={l} className="sl-row"><b>{t}</b><span>{l}</span></div>)}
      </section>
    </div>
  </div>
}

export function useHomeData() {
  const [a, setA] = useState(null)
  const [sched, setSched] = useState(null)
  useEffect(() => {
    const go = () => {
      api.get('/program/command/analytics?range=today').then(setA).catch(() => {})
      api.get('/program/command/analytics/records?metric=scheduled_new&range=all&limit=100').then(setSched).catch(() => {})
    }
    go()
    const t = setInterval(() => { if (document.visibilityState === 'visible') go() }, 60000)
    return () => clearInterval(t)
  }, [])
  return { a, sched }
}
