/* SCI Live: one phone screen with the short version of what is happening
   today - who was contacted, who replied, who booked, who opened the booking
   page and who is next. Reads the same endpoints as the Program Center:
   /program/outbox, /program/responses, /program/appointments and
   /program/booking-activity. Refreshes every 60 seconds and the moment the
   phone comes back to this screen. Route: /program/live */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import './Live.css'

const REFRESH_MS = 60000
const time = iso => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '')
const dayTime = iso => {
  if (!iso) return ''
  const d = new Date(iso)
  const today = new Date().toDateString() === d.toDateString()
  return today ? time(iso) : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) + ' ' + time(iso)
}
const isToday = iso => iso && new Date(iso).toDateString() === new Date().toDateString()
const title = s => (s || '').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
const BAD = { failed: 'Failed', bounced: 'Bounced', undelivered: 'Not delivered' }
const CLASS = { hot: ['Hot', 'hot'], active: ['Replied', 'blue'], low: ['Replied', 'gray'], opt_out: ['Opted out', 'gray'],
                bad_data: ['Bad info', 'gray'], wrong_person: ['Wrong person', 'gray'] }

export default function Live() {
  const navigate = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [at, setAt] = useState(null)
  const [, tick] = useState(0)
  const busy = useRef(false)

  const load = useCallback(() => {
    if (busy.current) return
    busy.current = true
    const safe = p => api.get(p).catch(() => null)
    Promise.all([
      api.get('/program/outbox'),
      safe('/program/responses?limit=50'),
      safe('/program/appointments?when=upcoming'),
      safe('/program/booking-activity?days=1'),
    ]).then(([ob, rs, ap, ba]) => {
      setD({ ob, rs: rs?.items || [], ap: ap?.items || [], ba })
      setErr(''); setAt(new Date())
      try { sessionStorage.removeItem('sci_live_entry') } catch { /* private mode */ }
    }).catch(e => {
      const msg = (e && e.message) || 'Could not load.'
      // Opened in a fresh tab (a Home Screen shortcut, a link): no workspace is
      // chosen yet. Go through the SCI front door, which picks SCI and comes
      // straight back here.
      if (/no workspace|no outreach program/i.test(msg) && !sessionStorage.getItem('sci_live_entry')) {
        try { sessionStorage.setItem('sci_live_entry', '1') } catch { /* private mode */ }
        window.location.replace('/sci?to=live')
        return
      }
      setErr(msg)
    })
      .finally(() => { busy.current = false })
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, REFRESH_MS)
    const s = setInterval(() => tick(n => n + 1), 5000)
    const onShow = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onShow)
    window.addEventListener('focus', onShow)
    return () => { clearInterval(t); clearInterval(s); document.removeEventListener('visibilitychange', onShow); window.removeEventListener('focus', onShow) }
  }, [load])

  const ago = at ? Math.max(0, Math.round((Date.now() - at.getTime()) / 1000)) : null
  const open = id => id && navigate(`/program?person=${encodeURIComponent(id)}`)

  if (!d) {
    return <div className="live"><Header ago={ago} onRefresh={load} />{err ? <p className="live-err">{err}</p> : <p className="live-muted">Loading…</p>}</div>
  }
  const sent = d.ob?.sent || []
  const people = new Set(sent.map(s => s.lead_id || s.name)).size
  const texts = sent.filter(s => s.channel === 'text')
  const delivered = texts.filter(s => s.status === 'delivered').length
  const emails = sent.filter(s => s.channel === 'email').length
  const failed = d.ob?.failed || 0
  const repliesToday = d.rs.filter(r => isToday(r.received_at))
  const needs = d.rs.filter(r => r.status === 'new' && r.class !== 'opt_out')
  const optOuts = repliesToday.filter(r => r.class === 'opt_out').length
  const opened = d.ba?.opened_people ?? 0
  const upcoming = (d.ob?.upcoming || []).slice(0, 5)

  return (
    <div className="live">
      <Header ago={ago} onRefresh={load} />
      {err && <p className="live-err">Last refresh failed - showing the numbers from {time(at?.toISOString())}.</p>}

      <section className="live-tiles" aria-label="Today">
        <Tile big value={d.ap.length} label="Appointments booked" tone={d.ap.length ? 'green' : ''} />
        <Tile big value={repliesToday.length} label="Replies today" tone={needs.length ? 'amber' : ''}
              note={needs.length ? `${needs.length} need a look` : 'All handled'} />
        <Tile value={people} label="People contacted" note={`${d.ob?.coming_up ?? 0} still scheduled`} />
        <Tile value={opened} label="Opened booking page" note="Last 24 hours" />
        <Tile value={`${delivered}/${texts.length}`} label="Texts delivered" />
        <Tile value={emails} label="Emails sent" />
        <Tile value={optOuts} label="Opted out" />
        <Tile value={failed} label="Failed or bounced" tone={failed ? 'red' : ''} />
      </section>

      <Section title="Appointments" empty="No one has booked yet.">
        {d.ap.map(a => (
          <Row key={a.id} onClick={() => open(a.lead_id)} name={title(a.name)} where={a.location}
               right={<span className="live-chip green">{dayTime(a.when)}</span>} />
        ))}
      </Section>

      <Section title="Replies" empty="No replies yet.">
        {d.rs.slice(0, 12).map(r => {
          const [lbl, tone] = CLASS[r.class] || ['Replied', 'blue']
          const fresh = r.status === 'new' && r.class !== 'opt_out'
          return (
            <Row key={r.id} onClick={() => open(r.lead_id)} name={r.name || '(no name)'} where={r.location}
                 text={r.body} right={<>
                   <span className={`live-chip ${fresh ? 'amber' : tone}`}>{fresh ? 'New' : r.status === 'closed' ? 'Closed' : lbl}</span>
                   <span className="live-time">{dayTime(r.received_at)}</span>
                 </>} />
          )
        })}
      </Section>

      <Section title="Next up" empty="Nothing else scheduled today.">
        {upcoming.map((u, i) => (
          <Row key={i} onClick={() => open(u.lead_id)} name={title(u.name)} where={u.location}
               text={[u.what, u.channels].filter(Boolean).join(' · ')}
               right={<span className="live-time">{time(u.at)}</span>} />
        ))}
      </Section>

      <Section title="Just sent" empty="Nothing sent yet today.">
        {sent.slice(0, 10).map((s, i) => (
          <Row key={i} onClick={() => open(s.lead_id)} name={title(s.name)} where={s.location}
               right={<>
                 <span className={`live-chip ${BAD[s.status] ? 'red' : s.status === 'delivered' ? 'green' : 'gray'}`}>
                   {s.channel === 'text' ? 'Text' : 'Email'} · {BAD[s.status] || (s.status === 'delivered' ? 'Delivered' : s.status === 'opened' ? 'Opened' : 'Sent')}
                 </span>
                 <span className="live-time">{time(s.at)}</span>
               </>} />
        ))}
      </Section>

      <p className="live-foot">Updates every 60 seconds and whenever you come back to this screen.
        <button type="button" className="live-link" onClick={() => navigate('/program')}>Open full app</button></p>
    </div>
  )
}

function Header({ ago, onRefresh }) {
  return (
    <header className="live-head">
      <div>
        <h1>SCI Live</h1>
        <p className="live-muted">{ago == null ? 'Loading…' : ago < 10 ? 'Updated just now' : `Updated ${ago < 60 ? ago + ' sec' : Math.round(ago / 60) + ' min'} ago`}</p>
      </div>
      <button type="button" className="live-btn" onClick={onRefresh}>Refresh</button>
    </header>
  )
}

function Tile({ value, label, note, tone = '', big = false }) {
  return (
    <div className={`live-tile ${tone} ${big ? 'big' : ''}`}>
      <div className="live-val">{value}</div>
      <div className="live-lbl">{label}</div>
      {note && <div className="live-note">{note}</div>}
    </div>
  )
}

function Section({ title: t, empty, children }) {
  const items = [].concat(children || []).filter(Boolean)
  return (
    <section className="live-sec">
      <h2>{t}</h2>
      {items.length ? <ul className="live-list">{items}</ul> : <p className="live-muted pad">{empty}</p>}
    </section>
  )
}

function Row({ name, where, text, right, onClick }) {
  return (
    <li>
      <button type="button" className="live-row" onClick={onClick}>
        <span className="live-main">
          <span className="live-name">{name}</span>
          {where && <span className="live-where">{where}</span>}
          {text && <span className="live-text">{text}</span>}
        </span>
        <span className="live-right">{right}</span>
      </button>
    </li>
  )
}
