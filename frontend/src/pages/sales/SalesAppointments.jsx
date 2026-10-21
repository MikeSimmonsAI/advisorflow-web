/**
 * Appointments — "See your schedule and book meetings." (Lane F, #281)
 *
 * Day / Week / Month over the rep's OWN meetings, from the existing calendar
 * endpoint (GET /sales/calendar/view?scope=mine — the server's own narrowing).
 * Opening a meeting offers what the existing calendar already does — join,
 * open the lead, move it (RescheduleDialog), record what happened
 * (OutcomeDialog). The manager's full Team Calendar is unchanged.
 *
 * Times are the wall clock the server resolved in the team's time zone
 * (`starts_at_local`), never re-read through the browser's zone.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import RescheduleDialog from './RescheduleDialog'
import OutcomeDialog from './OutcomeDialog'
import {
  ymd, addDays, dayFromYmd, startOfWeek, startOfMonth, endOfMonth, monthGridRange,
  wallMinutes, wallTime, hourLabel,
} from './calendarTime'
import { Icon, ErrorLine, Skeleton, Modal, useNotice } from './SimpleParts'

const HOUR_H = 56
const VIEWS = [['day', 'Day'], ['week', 'Week'], ['month', 'Month']]

function rangeFor(view, anchor) {
  if (view === 'day') return { from: anchor, to: anchor }
  if (view === 'week') { const s = startOfWeek(anchor); return { from: s, to: addDays(s, 6) } }
  const g = monthGridRange(anchor)
  return { from: g.first, to: g.last }
}

function label(view, anchor) {
  if (view === 'day') return anchor.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
  if (view === 'week') {
    const s = startOfWeek(anchor), e = addDays(s, 6)
    return s.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' – ' +
      e.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  }
  return anchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

const isDemo = a => /demo/i.test((a.meeting_type || '') + ' ' + (a.title || ''))
const dayKey = a => String(a.starts_at_local || '').slice(0, 10)
const who = a => a.prospect?.name || a.opportunity_company || a.prospect?.company || ''

export default function SalesAppointments() {
  return (
    <SalesShell title="Appointments" subtitle="See your schedule and book meetings.">
      <AppointmentsBody />
    </SalesShell>
  )
}

function AppointmentsBody() {
  const nav = useNavigate()
  const [params] = useSearchParams()
  const [view, setView] = useState('day')
  const [anchor, setAnchor] = useState(() => (params.get('date') ? dayFromYmd(params.get('date')) : new Date()))
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [openAppt, setOpenAppt] = useState(null)
  const [moving, setMoving] = useState(null)
  const [outcomeFor, setOutcomeFor] = useState(null)
  const [notice, notify] = useNotice()

  const range = useMemo(() => rangeFor(view, anchor), [view, anchor])
  const from = ymd(range.from), to = ymd(range.to)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const q = new URLSearchParams({ date_from: from, date_to: to, scope: 'mine', include_external: 'false' })
      setData(await api.get('/sales/calendar/view?' + q.toString()))
    } catch (e) {
      setError(e.message || 'Could not load your appointments.')
    } finally {
      setLoading(false)
    }
  }, [from, to])
  useEffect(() => { load() }, [load])

  const appts = useMemo(() => (data?.appointments || [])
    .slice().sort((a, b) => String(a.starts_at_local).localeCompare(String(b.starts_at_local))), [data])

  function step(dir) {
    if (view === 'month') setAnchor(new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1, 12))
    else setAnchor(addDays(anchor, dir * (view === 'week' ? 7 : 1)))
  }

  async function after(msg) {
    setOpenAppt(null); setMoving(null); setOutcomeFor(null)
    notify(msg)
    await load()
  }

  return (
    <>
      <ErrorLine error={error} onRetry={load} />
      <section className="ss-panel">
        <div className="ss-calendarbar">
          <div className="ss-calcontrols">
            <button type="button" className="ss-btn ss-white ss-xs" onClick={() => setAnchor(new Date())}>Today</button>
            <button type="button" className="ss-btn ss-white ss-xs" aria-label="Previous" onClick={() => step(-1)}><Icon name="chevleft" /></button>
            <button type="button" className="ss-btn ss-white ss-xs" aria-label="Next" onClick={() => step(1)}><Icon name="chevright" /></button>
            <strong aria-live="polite">{label(view, anchor)}</strong>
          </div>
          <div className="ss-calcontrols">
            {VIEWS.map(([k, l]) => (
              <button key={k} type="button" className="ss-btn ss-white ss-xs" aria-pressed={view === k}
                      onClick={() => setView(k)}>{l}</button>
            ))}
            <button type="button" className="ss-btn ss-blue ss-xs" onClick={() => nav('/sales/book')}>
              <Icon name="plus" /> New Appointment
            </button>
          </div>
        </div>
        {loading && !data ? <Skeleton rows={6} /> : (
          view === 'day' ? <DayGrid appts={appts.filter(a => dayKey(a) === from)} onOpen={setOpenAppt} />
          : view === 'week' ? <WeekGrid start={range.from} appts={appts} onOpen={setOpenAppt} />
          : <MonthGrid anchor={anchor} range={range} appts={appts}
                       onPick={d => { setAnchor(d); setView('day') }} />
        )}
      </section>
      <p className="ss-footnote">
        Showing meetings you are on or that belong to your deals, in your team's time zone
        {data?.brand_sales_org?.timezone ? ' (' + data.brand_sales_org.timezone.replace(/_/g, ' ') + ')' : ''}.
        {' '}<button type="button" className="ss-link" onClick={() => nav('/sales/availability')}>Set your available hours</button>
      </p>

      {openAppt && (
        <Modal title={openAppt.meeting_type || openAppt.title || 'Meeting'}
               sub={new Date(dayKey(openAppt) + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }) +
                    ' · ' + wallTime(openAppt.starts_at_local) + ' – ' + wallTime(openAppt.ends_at_local)}
               onClose={() => setOpenAppt(null)}
               footer={<>
                 {openAppt.opportunity_id && <button type="button" className="ss-btn ss-white"
                   onClick={() => nav('/sales/leads/' + openAppt.opportunity_id)}>Open lead</button>}
                 {openAppt.status === 'scheduled' && <button type="button" className="ss-btn ss-white"
                   onClick={() => { setMoving(openAppt); setOpenAppt(null) }}>Reschedule</button>}
                 {openAppt.status !== 'cancelled' && <button type="button" className="ss-btn ss-green"
                   onClick={() => { setOutcomeFor(openAppt); setOpenAppt(null) }}>Record what happened</button>}
               </>}>
          <div className="ss-callout">
            <strong>{who(openAppt) || 'No prospect attached'}</strong>
            {openAppt.opportunity_company && who(openAppt) !== openAppt.opportunity_company ? ' · ' + openAppt.opportunity_company : ''}<br />
            {openAppt.confirmation_status === 'confirmed' ? 'Prospect confirmed.'
              : openAppt.confirmation_status === 'declined' ? 'Prospect declined.'
              : 'Prospect has not confirmed yet.'}
            {openAppt.status !== 'scheduled' ? ' · ' + String(openAppt.status).replace('_', ' ') : ''}
          </div>
          {(openAppt.participants || []).length > 0 && (
            <p className="ss-count-note">With: {openAppt.participants.map(p => p.full_name).join(', ')}</p>
          )}
          {(openAppt.video?.join_url || openAppt.meeting_url) && (
            <a className="ss-btn ss-blue" href={openAppt.video?.join_url || openAppt.meeting_url} target="_blank" rel="noreferrer">
              Join meeting
            </a>
          )}
          {openAppt.location && <p className="ss-count-note">Location: {openAppt.location}</p>}
        </Modal>
      )}
      {moving && <RescheduleDialog appt={moving} onClose={() => setMoving(null)} onDone={() => after('Meeting moved.')} />}
      {outcomeFor && <OutcomeDialog appt={outcomeFor} onClose={() => setOutcomeFor(null)} onRecorded={() => after('Outcome recorded.')} />}
      {notice}
    </>
  )
}

function DayGrid({ appts, onOpen }) {
  // 8 AM – 5 PM like the approved screen, widened to fit any meeting outside it.
  const mins = appts.map(a => wallMinutes(a.starts_at_local)).filter(m => m != null)
  const ends = appts.map(a => wallMinutes(a.ends_at_local)).filter(m => m != null)
  const startH = Math.min(8, ...mins.map(m => Math.floor(m / 60)))
  const endH = Math.max(17, ...ends.map(m => Math.ceil(m / 60)))
  const hours = Array.from({ length: endH - startH }, (_, i) => startH + i)
  return (
    <>
      {appts.length === 0 && (
        <div className="ss-emptylist" style={{ padding: '18px 18px 4px' }}>
          <strong>No meetings this day.</strong>
        </div>
      )}
      <div className="ss-calgrid">
        <div className="ss-timecol" aria-hidden="true">
          {hours.map(h => <div key={h} className="ss-timecell">{hourLabel(h)}</div>)}
        </div>
        <div className="ss-slotcol">
          {hours.map(h => <div key={h} className="ss-timeslot" />)}
          {appts.map(a => {
            const s = wallMinutes(a.starts_at_local) ?? startH * 60
            let e = wallMinutes(a.ends_at_local)
            if (e == null || e <= s) e = s + 30
            const top = (s - startH * 60) / 60 * HOUR_H + 2
            const h = Math.max(30, (e - s) / 60 * HOUR_H - 4)
            return (
              <button key={a.id} type="button" onClick={() => onOpen(a)}
                      className={'ss-event' + (isDemo(a) ? ' ss-green' : '') + (a.status === 'cancelled' ? ' ss-cancelled' : '')}
                      style={{ top, height: h }}>
                <strong>{(a.meeting_type || a.title || 'Meeting') + (who(a) ? ' — ' + who(a) : '')}</strong>
                <small>{wallTime(a.starts_at_local)} · {a.meeting_type || 'Meeting'}</small>
              </button>
            )
          })}
        </div>
      </div>
    </>
  )
}

function WeekGrid({ start, appts, onOpen }) {
  const today = ymd(new Date())
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i))
  return (
    <div className="ss-week">
      {days.map(d => {
        const k = ymd(d)
        const items = appts.filter(a => dayKey(a) === k)
        return (
          <div key={k} className="ss-weekday">
            <h4 className={k === today ? 'ss-today' : ''}>
              {d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
            </h4>
            {items.length === 0 && <span className="ss-count-note">—</span>}
            {items.map(a => (
              <button key={a.id} type="button" onClick={() => onOpen(a)}
                      className={'ss-chipevent' + (isDemo(a) ? ' ss-green' : '')}>
                <strong>{wallTime(a.starts_at_local)} {a.meeting_type || a.title}</strong>
                {who(a)}
              </button>
            ))}
          </div>
        )
      })}
    </div>
  )
}

function MonthGrid({ anchor, range, appts, onPick }) {
  const today = ymd(new Date())
  const first = startOfMonth(anchor), last = endOfMonth(anchor)
  const cells = []
  for (let d = range.from; d <= range.to; d = addDays(d, 1)) cells.push(d)
  const names = Array.from({ length: 7 }, (_, i) => addDays(range.from, i)
    .toLocaleDateString(undefined, { weekday: 'short' }))
  return (
    <div className="ss-month">
      {names.map(n => <div key={n} className="ss-monthhead">{n}</div>)}
      {cells.map(d => {
        const k = ymd(d)
        const n = appts.filter(a => dayKey(a) === k).length
        const out = d < first || d > last
        return (
          <button key={k} type="button" onClick={() => onPick(d)}
                  className={'ss-monthcell' + (out ? ' ss-out' : '') + (k === today ? ' ss-today' : '')}
                  aria-label={d.toDateString() + (n ? ', ' + n + ' meetings' : '')}>
            <span className="ss-dnum">{d.getDate()}</span>
            {n > 0 && <span className="ss-dcount">{n} meeting{n === 1 ? '' : 's'}</span>}
          </button>
        )
      })}
    </div>
  )
}
