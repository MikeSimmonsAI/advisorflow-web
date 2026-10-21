/**
 * Inspections - what is actually on the calendar, what is only requested,
 * and the booking flow against real openings.
 *
 * Confirmed = a BookingLink that is booked (pipeline column inspection_booked).
 * Requested = the homeowner said yes but nothing is booked (mission lane
 * "requested"). The two are never mixed. Booking re-checks the slot on the
 * server; a taken slot comes back 409 and the openings reload.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { useRoofing } from './RoofingShell'
import { Avatar, Empty, ErrorNote, Icon, Loading, Panel, Pill, ago, errText, localWhen, useApi, useToast } from './ui'
import './roofing-conv.css'

function ymd(d) {
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export default function Inspections() {
  const [params, setParams] = useSearchParams()
  const leadId = params.get('lead') || ''
  const pipe = useApi('/roofing-ops/pipeline')
  const mission = useApi('/roofing-ops/mission')
  const [toast, say] = useToast()

  const confirmed = useMemo(() => {
    const col = (pipe.data?.columns || []).find((c) => c.key === 'inspection_booked')
    return [...(col?.cards || [])].filter((c) => c.inspection_at).sort((a, b) => a.inspection_at.localeCompare(b.inspection_at))
  }, [pipe.data])
  const isPast = (naive) => {
    const [d, t] = naive.split('T'); const [y, m, dd] = d.split('-').map(Number); const [hh, mm] = (t || '0:0').split(':').map(Number)
    return new Date(y, m - 1, dd, hh, mm).getTime() < Date.now()
  }
  const upcoming = confirmed.filter((c) => !isPast(c.inspection_at))
  const past = confirmed.filter((c) => isPast(c.inspection_at)).reverse()
  const markInspected = async (c) => {
    try {
      await api.post(`/roofing-ops/leads/${c.lead_id}/stage`, { stage: 'inspected', reason: 'Inspection completed' })
      say(`${c.name} marked inspected. Next step: send the estimate.`); pipe.reload(); mission.reload()
    } catch (e) { say(errText(e), 'err') }
  }
  const requested = useMemo(() => (mission.data?.lanes || []).find((l) => l.key === 'requested')?.cards || [], [mission.data])

  const open = (id) => { const p = new URLSearchParams(params); if (id) p.set('lead', id); else p.delete('lead'); setParams(p) }

  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Inspections</h1>
          <p className="rf-lede">Confirmed inspections are on a salesperson's calendar. Requests are homeowners who said yes but haven't picked a time yet.</p>
        </div>
        {!leadId && <button className="rf-btn rf-btn--primary" onClick={() => open('pick')}><Icon name="calendar" size={18} />Book an inspection</button>}
      </div>

      {leadId && (
        <div style={{ marginBottom: 20 }}>
          {leadId === 'pick'
            ? <LeadPicker onPick={(id) => open(id)} onCancel={() => open('')} />
            : <Booking key={leadId} leadId={leadId} onClose={() => open('')} say={say}
                onBooked={() => { pipe.reload(); mission.reload() }} />}
        </div>
      )}

      <div className="rf-insp-grid">
        <div className="rf-stack">
        <Panel title="Upcoming inspections" sub="Confirmed on a salesperson's calendar, soonest first"
          actions={<Pill tone="green">{upcoming.length} upcoming</Pill>}>
          {pipe.loading && !pipe.data && <Loading />}
          {pipe.error && <ErrorNote onRetry={pipe.reload}>{pipe.error}</ErrorNote>}
          {pipe.data && !upcoming.length && (
            <Empty icon="calendar" title="No upcoming inspections">When a homeowner picks a time - or you book one for them - it shows here with the salesperson who's going.</Empty>
          )}
          <ul className="rf-insp-list">
            {upcoming.map((c) => (
              <li key={c.lead_id} className="rf-insp-row">
                <span className="rf-insp-date">
                  <b>{localWhen(c.inspection_at, { weekday: 'short', month: undefined, day: undefined, hour: undefined, minute: undefined })}</b>
                  <span>{localWhen(c.inspection_at, { weekday: undefined, hour: undefined, minute: undefined })}</span>
                </span>
                <span className="rf-insp-main">
                  <b>{c.name}</b>
                  <span className="rf-muted">{c.address || 'No address on file'}</span>
                  {c.roof_issue && <span className="rf-muted">{c.roof_issue}</span>}
                </span>
                <span className="rf-insp-time">
                  <b>{localWhen(c.inspection_at, { weekday: undefined, month: undefined, day: undefined })}</b>
                  <span className="rf-muted">{c.owner_name || 'Unassigned'}</span>
                </span>
                <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`/program/roofing/conversations?lead=${c.lead_id}`}>Open</Link>
              </li>
            ))}
          </ul>
        </Panel>
        {past.length > 0 && (
          <Panel title="Past inspections - record the result" sub="The time has passed. Mark each one inspected so the estimate step can start."
            actions={<Pill tone="amber">{past.length} to update</Pill>}>
            <ul className="rf-insp-list">
              {past.map((c) => (
                <li key={c.lead_id} className="rf-insp-row">
                  <span className="rf-insp-date">
                    <b>{localWhen(c.inspection_at, { weekday: 'short', month: undefined, day: undefined, hour: undefined, minute: undefined })}</b>
                    <span>{localWhen(c.inspection_at, { weekday: undefined, hour: undefined, minute: undefined })}</span>
                  </span>
                  <span className="rf-insp-main">
                    <b>{c.name}</b>
                    <span className="rf-muted">{c.address || 'No address on file'}</span>
                  </span>
                  <span className="rf-insp-time"><span className="rf-muted">{c.owner_name || 'Unassigned'}</span></span>
                  <button className="rf-btn rf-btn--navy rf-btn--sm" onClick={() => markInspected(c)}>Mark inspected</button>
                </li>
              ))}
            </ul>
          </Panel>
        )}
        </div>

        <Panel title="Asked for an inspection" sub="Said yes - not booked yet. Offer times or book one now."
          actions={<Pill tone="orange">{requested.length} waiting</Pill>}>
          {mission.loading && !mission.data && <Loading />}
          {mission.error && <ErrorNote onRetry={mission.reload}>{mission.error}</ErrorNote>}
          {mission.data && !requested.length && (
            <Empty icon="check" title="No open requests">Everyone who asked for an inspection has a time, or is still talking with the AI.</Empty>
          )}
          <ul className="rf-insp-list">
            {requested.map((c) => (
              <li key={c.lead_id} className="rf-insp-row rf-insp-row--req">
                <Avatar name={c.name} size={36} />
                <span className="rf-insp-main">
                  <b>{c.name}</b>
                  <span className="rf-muted">{c.summary || c.address}</span>
                </span>
                <span className="rf-muted rf-insp-ago">{ago(c.last_activity_at)}</span>
                <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => open(c.lead_id)}>Book</button>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      {toast}
    </div>
  )
}

function LeadPicker({ onPick, onCancel }) {
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 300); return () => clearTimeout(t) }, [q])
  const res = useApi(`/roofing-ops/leads?page_size=20${query ? `&q=${encodeURIComponent(query)}` : ''}`)
  return (
    <Panel title="Who is the inspection for?" sub="Search your homeowners by name, phone or address"
      actions={<button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={onCancel}>Cancel</button>}>
      <div className="rf-search" style={{ marginBottom: 12 }}>
        <Icon name="search" size={18} />
        <input className="rf-input" autoFocus placeholder="Search homeowners" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search homeowners" />
      </div>
      {res.loading && !res.data && <Loading />}
      {res.error && <ErrorNote onRetry={res.reload}>{res.error}</ErrorNote>}
      {res.data && !res.data.items.length && <Empty icon="search" title="No homeowners found" />}
      <ul className="rf-insp-list">
        {(res.data?.items || []).map((l) => (
          <li key={l.lead_id} className="rf-insp-row rf-insp-row--req">
            <Avatar name={l.name} size={34} />
            <span className="rf-insp-main"><b>{l.name}</b><span className="rf-muted">{l.address || l.phone || l.email}</span></span>
            <Pill tone="slate">{l.stage_label}</Pill>
            <button className="rf-btn rf-btn--primary rf-btn--sm" disabled={l.status === 'dnc'} onClick={() => onPick(l.lead_id)}>
              {l.status === 'dnc' ? 'Opted out' : 'Choose'}
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function Booking({ leadId, onClose, onBooked, say }) {
  const { status } = useRoofing()
  const ws = useApi(`/roofing-ops/leads/${leadId}`)
  const types = status?.profile?.inspection_types?.length ? status.profile.inspection_types : ['Roof Inspection']
  const team = status?.team || []
  const days = useMemo(() => Array.from({ length: 10 }, (_, i) => { const d = new Date(); d.setDate(d.getDate() + i); return d }), [])
  const [step, setStep] = useState(1)
  const [type, setType] = useState(types[0])
  const [sp, setSp] = useState('')
  const [day, setDay] = useState(ymd(days[0]))
  const [open, setOpen] = useState(null)
  const [loadingSlots, setLoadingSlots] = useState(false)
  const [slotErr, setSlotErr] = useState(null)
  const [slot, setSlot] = useState(null)
  const [notes, setNotes] = useState('')
  const [confirmText, setConfirmText] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)

  useEffect(() => { if (ws.data && !sp) setSp(ws.data.lead.owner_id || '') }, [ws.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const loadSlots = async () => {
    setLoadingSlots(true); setSlotErr(null); setSlot(null)
    try {
      const qs = new URLSearchParams({ date_from: day, days: '1', inspection_type: type })
      if (sp) qs.set('salesperson_id', sp)
      setOpen(await api.get(`/roofing-ops/leads/${leadId}/openings?${qs}`))
    } catch (e) { setSlotErr(errText(e)); setOpen(null) } finally { setLoadingSlots(false) }
  }
  useEffect(() => { if (step === 1) loadSlots() }, [day, type, sp]) // eslint-disable-line react-hooks/exhaustive-deps
  // Open on the first day that actually has an opening, not on a full day.
  const [jumped, setJumped] = useState(false)
  useEffect(() => {
    if (jumped || !ws.data) return
    setJumped(true)
    const qs = new URLSearchParams({ date_from: ymd(days[0]), days: '10', inspection_type: type })
    if (sp || ws.data.lead.owner_id) qs.set('salesperson_id', sp || ws.data.lead.owner_id)
    api.get(`/roofing-ops/leads/${leadId}/openings?${qs}`).then((r) => {
      const first = r?.slots?.[0]?.starts_at_local
      if (first && first.slice(0, 10) !== day) setDay(first.slice(0, 10))
    }).catch(() => {})
  }, [ws.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const confirm = async () => {
    setBusy(true)
    try {
      const r = await api.post(`/roofing-ops/leads/${leadId}/book`, {
        starts_at: slot.starts_at, inspection_type: type, salesperson_id: sp || null,
        notes: notes.trim() || null, send_confirmation: confirmText,
      })
      setDone(r); setStep(3); say('Inspection confirmed'); onBooked()
    } catch (e) {
      if (e.status === 409) { say('That time was just taken - pick another', 'err'); setStep(1); loadSlots() } else say(errText(e), 'err')
    } finally { setBusy(false) }
  }

  const L = ws.data?.lead
  const optedOut = L?.status === 'dnc'
  return (
    <section className="rf-panel rf-book" aria-label="Book an inspection">
      <header className="rf-book__head">
        <div className="rf-book__who">
          <Avatar name={L?.name} size={46} />
          <div><h2 className="rf-panel__title">Book inspection{L ? ` - ${L.name}` : ''}</h2>
            <p className="rf-panel__sub">{L?.address || (ws.loading ? 'Loading…' : 'No address on file')}</p></div>
        </div>
        <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={onClose} aria-label="Close booking"><Icon name="close" size={16} /></button>
      </header>
      <ol className="rf-steps" aria-label="Progress">
        {['Select time', 'Confirm details', 'Booked'].map((s, i) => (
          <li key={s} className={step === i + 1 ? 'on' : step > i + 1 ? 'done' : ''}><span>{step > i + 1 ? <Icon name="check" size={14} /> : i + 1}</span>{s}</li>
        ))}
      </ol>
      {ws.error && <ErrorNote onRetry={ws.reload}>{ws.error}</ErrorNote>}
      {optedOut && <div className="rf-note rf-note--warn"><Icon name="alert" size={16} />This homeowner opted out. Inspections can't be booked from outreach for them.</div>}

      {step === 1 && !optedOut && (
        <div className="rf-book__body">
          <div className="rf-book__opts">
            <label className="rf-label">Inspection type
              <select className="rf-select" value={type} onChange={(e) => setType(e.target.value)}>
                {types.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            {team.length > 0 && (
              <label className="rf-label">Salesperson
                <select className="rf-select" value={sp} onChange={(e) => setSp(e.target.value)}>
                  <option value="">Lead owner / company default</option>
                  {team.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </label>
            )}
          </div>
          <div className="rf-book__days" role="tablist" aria-label="Day">
            {days.map((d) => {
              const k = ymd(d)
              return (
                <button key={k} role="tab" aria-selected={day === k} className="rf-book__day" onClick={() => setDay(k)}>
                  <b>{d.toLocaleDateString(undefined, { weekday: 'short' })}</b>
                  <span>{d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>
                </button>
              )
            })}
          </div>
          {loadingSlots && <Loading label="Checking the calendar" />}
          {slotErr && <ErrorNote onRetry={loadSlots}>{slotErr}</ErrorNote>}
          {!loadingSlots && open && open.availability_status !== 'ok' && (
            <div className="rf-note rf-note--warn"><Icon name="calendar" size={16} />{open.reason || 'No openings that day.'}</div>
          )}
          {!loadingSlots && open?.availability_status === 'ok' && (
            <>
              <p className="rf-muted rf-book__for">Open times for {open.salesperson_name} · {open.duration_minutes} min · {open.timezone}</p>
              <div className="rf-book__times">
                {open.slots.map((s) => (
                  <button key={s.starts_at} className={`rf-book__time ${slot?.starts_at === s.starts_at ? 'on' : ''}`}
                    aria-pressed={slot?.starts_at === s.starts_at} onClick={() => setSlot(s)}>
                    {s.label.split(' at ')[1] || s.label}
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="rf-book__foot">
            <button className="rf-btn rf-btn--primary" disabled={!slot} onClick={() => setStep(2)}>Continue</button>
          </div>
        </div>
      )}

      {step === 2 && slot && (
        <div className="rf-book__body">
          <div className="rf-book__summary">
            <Icon name="calendar" size={22} />
            <div><b>{slot.label}</b><span className="rf-muted">{type} · {open?.duration_minutes} min with {open?.salesperson_name}</span></div>
          </div>
          <label className="rf-label">Notes for the inspector
            <textarea className="rf-textarea" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Gate code, where the leak is, insurance adjuster visit…" />
          </label>
          <label className="rf-check">
            <input type="checkbox" checked={confirmText} onChange={(e) => setConfirmText(e.target.checked)} />
            <span><b>Text the homeowner a confirmation</b><span className="rf-muted">Goes out through the normal send checks (consent, opt-out, quiet hours). Leave off to confirm with them yourself.</span></span>
          </label>
          <div className="rf-book__foot">
            <button className="rf-btn rf-btn--ghost" onClick={() => setStep(1)}>Back</button>
            <button className="rf-btn rf-btn--primary" disabled={busy} onClick={confirm}>{busy ? 'Confirming…' : 'Confirm inspection'}</button>
          </div>
        </div>
      )}

      {step === 3 && done && (
        <div className="rf-book__body rf-book__done">
          <span className="rf-book__ok"><Icon name="check" size={26} /></span>
          <h3>Inspection confirmed</h3>
          <p><b>{done.label}</b> - {done.inspection_type} with {done.salesperson_name}</p>
          <ul className="rf-book__facts">
            <li>{done.calendar_synced ? <><Icon name="check" size={16} />Added to {done.salesperson_name}'s connected calendar</>
              : <><Icon name="alert" size={16} />Not on a connected calendar yet - connect a calendar in Settings</>}</li>
            <li><Icon name="bolt" size={16} />{done.team_alerted ? `${done.team_alerted} team member${done.team_alerted === 1 ? '' : 's'} alerted` : 'Team already alerted for this booking'}</li>
            <li>{done.confirmation?.requested
              ? (done.confirmation.sent ? <><Icon name="send" size={16} />Confirmation text handed to the send checks</> : <><Icon name="alert" size={16} />Confirmation text not sent{done.confirmation.error ? `: ${done.confirmation.error}` : ''}</>)
              : <><Icon name="chat" size={16} />No text sent to the homeowner</>}</li>
          </ul>
          <div className="rf-book__foot">
            <Link className="rf-btn rf-btn--ghost" to={`/program/roofing/conversations?lead=${leadId}`}>Open conversation</Link>
            <button className="rf-btn rf-btn--primary" onClick={onClose}>Done</button>
          </div>
        </div>
      )}
    </section>
  )
}
