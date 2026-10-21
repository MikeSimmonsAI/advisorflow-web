/**
 * Book a Demo — "Set up a demo without extra steps." (Lane F, #281)
 *
 * Contact, date, time, type, optional note, one button. Underneath it is the
 * EXISTING scheduling engine, unchanged:
 *   GET  /sales/meeting-types          the brand's meeting types (Discovery, Demo …)
 *   POST /sales/availability/find      open times for that day (a helper, not a gate)
 *   POST /sales/appointments           books it; the server re-checks every
 *                                      calendar, refuses a clash (409), creates
 *                                      the video link and sends the prospect the
 *                                      usual invitation, exactly as before.
 *
 * The time a rep types is read in the sales team's time zone and converted to
 * the UTC the API stores (simpleSales.wallToUtcNaive).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell, { useSales } from './SalesShell'
import { wallTime } from './calendarTime'
import { wallDateTime } from './parts'
import { wallToUtcNaive, ymdPlusDays } from './simpleSales'
import { Icon, ErrorLine } from './SimpleParts'

const EARLY = ['prospect', 'contacted', 'discovery']

export default function BookDemo() {
  return (
    <SalesShell title="Book a Demo" subtitle="Set up a demo without extra steps.">
      <BookDemoBody />
    </SalesShell>
  )
}

/** Discovery first, then Demo, then the rest; internal meeting types are not for prospects. */
function orderTypes(types) {
  const rank = t => /discovery/i.test(t.key + ' ' + t.name) ? 0 : /demo/i.test(t.key + ' ' + t.name) ? 1 : 2
  return types.filter(t => !t.is_internal).sort((a, b) => rank(a) - rank(b))
}

function BookDemoBody() {
  const nav = useNavigate()
  const ctx = useSales()
  const [params] = useSearchParams()
  const tz = ctx?.brand_sales_org?.timezone || 'America/Chicago'
  const me = ctx?.user?.id

  const [deals, setDeals] = useState([])
  const [types, setTypes] = useState([])
  const [oppId, setOppId] = useState(params.get('opp') || '')
  const [date, setDate] = useState(() => ymdPlusDays(1, new Date(), ctx?.brand_sales_org?.timezone))
  const [time, setTime] = useState('10:00')
  const [typeId, setTypeId] = useState('')
  const [notes, setNotes] = useState('')
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)   // one booking per click, however fast it is pressed
  const [error, setError] = useState(null)
  const [booked, setBooked] = useState(null)
  const [moved, setMoved] = useState(false)

  useEffect(() => {
    api.get('/sales/opportunities').then(o => {
      const all = (o.stages || []).flatMap(s => s.opportunities || [])
        .filter(c => c.status === 'open')
        .sort((a, b) => String(a.contact_name || a.company_name).localeCompare(String(b.contact_name || b.company_name)))
      setDeals(all)
    }).catch(e => setError(e.message || 'Could not load your contacts.'))
  }, [])

  useEffect(() => {
    const q = oppId ? '?opportunity_id=' + encodeURIComponent(oppId) : ''
    api.get('/sales/meeting-types' + q).then(t => {
      const ordered = orderTypes(t || [])
      setTypes(ordered)
      setTypeId(prev => (ordered.some(x => x.id === prev) ? prev
        : ((ordered.find(x => x.key === 'discovery') || ordered[0])?.id || '')))
    }).catch(e => setError(e.message || 'Could not load meeting types.'))
  }, [oppId])

  const type = types.find(t => t.id === typeId)
  // The approved form offers two choices. The brand's exact Discovery and
  // Demo types fill them; every other type is one click away under Other.
  const primary = useMemo(() => {
    const exact = k => types.find(t => t.key === k)
    const pick = [exact('discovery') || types.find(t => /discovery/i.test(t.key + t.name)),
                  exact('demo') || types.find(t => /demo/i.test(t.key + t.name) && !/discovery/i.test(t.key + t.name))]
    return [...new Set(pick.filter(Boolean))]
  }, [types])
  const others = types.filter(t => !primary.includes(t))
  const opp = deals.find(d => d.id === oppId)

  // Who has to be there: the type's own role slots when they resolve to one
  // person, otherwise the rep booking it — the same seeding FindTeamTime does.
  const people = useMemo(() => {
    const req = []; const slots = {}
    ;(type?.resolved?.required || []).forEach(s => {
      const id = s.auto_selected_user_id || (s.candidates?.length === 1 ? s.candidates[0].id : null)
      if (id) { req.push(id); slots[id] = s.slot }
    })
    if (!req.length && me) req.push(me)
    return { required: [...new Set(req)], slots }
  }, [type, me])

  const findOpen = useCallback(async () => {
    if (!typeId || !date || !people.required.length) { setOpen(null); return }
    try {
      const r = await api.post('/sales/availability/find', {
        meeting_type_id: typeId, opportunity_id: oppId || undefined,
        required_user_ids: people.required, optional_user_ids: [],
        date_from: date, date_to: date,
      })
      setOpen(r?.slots || [])
    } catch { setOpen(null) }
  }, [typeId, date, oppId, people])
  useEffect(() => { findOpen() }, [findOpen])

  async function create() {
    setError(null)
    if (!oppId) { setError('Pick the contact this demo is for.'); return }
    const startsAt = wallToUtcNaive(date, time, tz)
    if (!startsAt) { setError('Pick a date and time.'); return }
    if (submitting.current) return
    submitting.current = true
    setBusy(true)
    try {
      const appt = await api.post('/sales/appointments', {
        starts_at: startsAt,
        meeting_type_id: typeId || undefined,
        duration_minutes: typeId ? undefined : 30,
        opportunity_id: oppId,
        required_user_ids: people.required,
        optional_user_ids: [],
        role_slot_by_user: people.slots,
        notes: notes.trim() || undefined,
      })
      setBooked(appt)
    } catch (e) {
      setError((e.message || 'Could not book that time.') + ' Pick another time — open times are listed under the form.')
      findOpen()
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  async function moveToDemo() {
    try {
      await api.patch('/sales/opportunities/' + oppId, { stage: 'demo_proposal' })
      setMoved(true)
    } catch (e) { setError(e.message || 'Could not move the deal.') }
  }

  if (booked) {
    return (
      <>
        <div className="ss-okbar" role="status">
          <Icon name="check" />
          <span><strong>Booked.</strong> {booked.title || type?.name} · {wallDateTime(booked.starts_at_local || startLocal(date, time))}</span>
        </div>
        <section className="ss-panel">
          <div className="ss-panelbody">
            <p style={{ marginTop: 0 }}>The scheduler saved it on your calendar.</p>
            <p className="ss-count-note"><InviteStatus appt={booked} email={opp?.email} /></p>
            {opp && EARLY.includes(opp.stage) && !moved && (
              <div className="ss-callout" style={{ marginBottom: 14 }}>
                {opp.contact_name || opp.company_name} is still in <strong>{opp.stage_label}</strong>.
                {' '}<button type="button" className="ss-link" onClick={moveToDemo}>Move to Demo Scheduled</button>
              </div>
            )}
            {moved && <div className="ss-okbar">Moved to Demo Scheduled (Demo / Proposal).</div>}
            <div className="ss-actrow" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button type="button" className="ss-btn ss-blue" onClick={() => nav('/sales/appointments?date=' + date)}>
                <Icon name="calendar" /> See it in Appointments
              </button>
              <button type="button" className="ss-btn ss-white" onClick={() => nav('/sales/leads/' + oppId)}>Back to lead</button>
              <button type="button" className="ss-btn ss-white" onClick={() => { setBooked(null); setMoved(false); setNotes('') }}>Book another</button>
            </div>
          </div>
        </section>
      </>
    )
  }

  return (
    <>
      <div className="ss-sections">
        <div>
          <h2>Book a Demo</h2>
          <p>Create a discovery call or product demo in seconds.</p>
        </div>
        <button type="button" className="ss-btn ss-white" onClick={() => nav('/sales/appointments')}>
          <Icon name="calendar" /> View Calendar
        </button>
      </div>
      <ErrorLine error={error} />
      <div className="ss-twoform">
        <section className="ss-panel">
          <form className="ss-formpanel" onSubmit={e => { e.preventDefault(); create() }}>
            <div className="ss-field">
              <label htmlFor="bk-contact">Contact *</label>
              <select id="bk-contact" required value={oppId} onChange={e => setOppId(e.target.value)}>
                <option value="">Choose a contact…</option>
                {deals.map(d => (
                  <option key={d.id} value={d.id}>
                    {(d.contact_name || d.company_name) + (d.contact_name ? ' — ' + d.company_name : '')}
                  </option>
                ))}
              </select>
            </div>
            <div className="ss-twobox">
              <div className="ss-field">
                <label htmlFor="bk-date">Date *</label>
                <input id="bk-date" type="date" required min={ymdPlusDays(0, new Date(), tz)} value={date}
                       onChange={e => setDate(e.target.value)} />
              </div>
              <div className="ss-field">
                <label htmlFor="bk-time">Time * <small>({tz.replace(/_/g, ' ')})</small></label>
                <input id="bk-time" type="time" required step={900} value={time}
                       onChange={e => setTime(e.target.value)} />
              </div>
            </div>
            {open && (
              <div className="ss-field">
                <span className="ss-label">Open times that day</span>
                {open.length ? (
                  <div className="ss-times">
                    {open.slice(0, 16).map(s => {
                      const hm = String(s.starts_at_local || '').slice(11, 16)
                      return (
                        <button key={s.starts_at} type="button" className="ss-time"
                                aria-pressed={hm === time} onClick={() => setTime(hm)}>
                          {wallTime(s.starts_at_local || s.starts_at)}
                        </button>
                      )
                    })}
                  </div>
                ) : <small>No open times that day — try another date.</small>}
              </div>
            )}
            <div className="ss-field" role="radiogroup" aria-labelledby="bk-type">
              <span className="ss-label" id="bk-type">Type</span>
              <div>
                {types.length === 0 && <small>No meeting types are set up yet — a 30-minute meeting will be booked.</small>}
                {primary.map(t => (
                  <label key={t.id} className="ss-choice">
                    <input type="radio" name="bk-type" value={t.id} checked={typeId === t.id}
                           onChange={() => setTypeId(t.id)} />
                    {t.name}
                  </label>
                ))}
                {others.length > 0 && (
                  <label className="ss-choice">
                    <input type="radio" name="bk-type" value="other" checked={!!typeId && !primary.some(t => t.id === typeId)}
                           onChange={() => setTypeId(others[0].id)} />
                    Other
                  </label>
                )}
              </div>
              {others.length > 0 && !!typeId && !primary.some(t => t.id === typeId) && (
                <select aria-label="Other meeting type" value={typeId} onChange={e => setTypeId(e.target.value)}>
                  {others.map(t => <option key={t.id} value={t.id}>{t.name}{t.duration_minutes ? ' (' + t.duration_minutes + ' min)' : ''}</option>)}
                </select>
              )}
              {type?.duration_minutes ? <small>{type.duration_minutes} minutes</small> : null}
            </div>
            <div className="ss-field" style={{ marginBottom: 0 }}>
              <label htmlFor="bk-notes">Notes (optional)</label>
              <textarea id="bk-notes" value={notes} maxLength={2000}
                        placeholder="Anything your teammate needs to know…" onChange={e => setNotes(e.target.value)} />
            </div>
          </form>
        </section>
        <div className="ss-formside">
          <div className="ss-icon" aria-hidden="true">🗓️</div>
          <h3>That's it!</h3>
          <p>Choose the contact, day and time. The appointment appears on your calendar
            {opp?.email ? ', and the usual invitation is sent to ' + opp.email : ''}.</p>
          <button type="button" className="ss-btn ss-blue" disabled={busy || !oppId} onClick={create}>
            {busy ? 'Booking…' : 'Create Demo'}
          </button>
          {!oppId && <p>Pick a contact to continue.</p>}
        </div>
      </div>
    </>
  )
}

function startLocal(date, time) { return date + 'T' + time + ':00' }

/**
 * What actually happened to the invitation, from the booking response — never
 * more than the server knows. "Sent" means handed to the mail service, not
 * delivered or read; the prospect's own confirmation shows on Appointments.
 */
function InviteStatus({ appt, email }) {
  if (!email) return <>This lead has no email, so no invitation was sent.</>
  const st = appt?.confirmation_status
  if (appt?.confirmation_sent_at || st === 'sent') {
    return <>Invitation email handed to the mail service for {email}. Delivery isn't confirmed — when they
      confirm, it shows on Appointments.</>
  }
  if (st === 'confirmed') return <>The prospect has already confirmed.</>
  return <>No invitation has gone out yet (status: {st || 'unknown'}). Open the meeting on Appointments to resend it.</>
}
