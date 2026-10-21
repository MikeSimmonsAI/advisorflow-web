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
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { useRoofing } from './RoofingShell'
import { Avatar, Empty, ErrorNote, Icon, Loading, Panel, Pill, ago, errText, localWhen, useApi, useToast } from './ui'
import './roofing-conv.css'
import './roofing-inspect.css'

const BASE = '/program/roofing/inspections'

function ymd(d) {
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const TABS = [
  ['upcoming', 'Upcoming', 'green', 'Booked and coming up - open the inspection when you arrive.'],
  ['awaiting', 'Awaiting confirmation', 'orange', 'Homeowner said yes or was offered times - no time confirmed yet.'],
  ['in_progress', 'In progress', 'blue', 'Being documented right now.'],
  ['unfinished', 'Unfinished reports', 'amber', 'The visit happened but the report isn’t complete.'],
  ['next_step', 'Needs estimate / follow-up', 'violet', 'Report complete - sales hasn’t taken the next step yet.'],
  ['completed', 'Completed', 'slate', 'Finished reports, attached to the homeowner.'],
]

export default function Inspections() {
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const leadId = params.get('lead') || ''
  const tab = params.get('tab') || ''
  const dash = useApi('/roofing-ops/inspections')
  const [toast, say] = useToast()
  const [busy, setBusy] = useState('')

  const B = dash.data?.buckets || {}
  const C = dash.data?.counts || {}
  const open = (id) => { const p = new URLSearchParams(params); if (id) p.set('lead', id); else p.delete('lead'); setParams(p) }
  const setTab = (k) => { const p = new URLSearchParams(params); p.set('tab', k); setParams(p) }

  const act = async (item) => {
    const k = item.action?.key
    if (k === 'book') return open(item.lead_id)
    if (item.inspection_id) {
      return nav(k === 'report' || k === 'next' ? `${BASE}/${item.inspection_id}/report` : `${BASE}/${item.inspection_id}`)
    }
    setBusy(item.booking_link_id || item.lead_id)
    try {
      const ins = await api.post('/roofing-ops/inspections', { lead_id: item.lead_id, booking_link_id: item.booking_link_id || null })
      nav(`${BASE}/${ins.id}`)
    } catch (e) { say(errText(e), 'err') } finally { setBusy('') }
  }
  const shown = tab ? TABS.filter((t) => t[0] === tab) : TABS

  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Inspections</h1>
          <p className="rf-lede">From booked appointment to finished report. Open an inspection on site to record the roof, the damage and photos - then hand it to sales.</p>
        </div>
        {!leadId && <button className="rf-btn rf-btn--primary" onClick={() => open('pick')}><Icon name="calendar" size={18} />Book an inspection</button>}
      </div>

      {leadId && (
        <div style={{ marginBottom: 20 }}>
          {leadId === 'pick'
            ? <LeadPicker onPick={(id) => open(id)} onCancel={() => open('')} />
            : <Booking key={leadId} leadId={leadId} onClose={() => open('')} say={say} onBooked={() => dash.reload()} />}
        </div>
      )}

      {dash.error && <ErrorNote onRetry={dash.reload}>{dash.error}</ErrorNote>}
      {dash.loading && !dash.data && <Loading />}
      {dash.data && (
        <>
          <div className="ri-strip" role="tablist" aria-label="Inspection status">
            <button role="tab" aria-selected={!tab} className={`ri-strip__t ${!tab ? 'on' : ''}`} onClick={() => setTab('')}>
              <b>{TABS.reduce((a, t) => a + (C[t[0]] || 0), 0)}</b><span>All</span>
            </button>
            {TABS.map(([k, label, tone]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={`ri-strip__t t-${tone} ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>
                <b>{C[k] || 0}</b><span>{label}</span>
              </button>
            ))}
          </div>
          {C.undocumented > 0 && (
            <div className="rf-note rf-note--warn" style={{ marginBottom: 16 }}><Icon name="alert" size={16} />
              <span>{C.undocumented} homeowner{C.undocumented === 1 ? ' was' : 's were'} marked inspected without a report.
                <button className="ri-linkbtn" onClick={() => setTab('undocumented')}>Add the report</button></span></div>
          )}
          <div className="ri-buckets">
            {(tab === 'undocumented' ? [['undocumented', 'Inspected without a report', 'amber', 'A stage was changed but nothing was documented.']] : shown).map(([k, label, tone, sub]) => (
              (tab || (B[k] || []).length > 0) && (
                <Panel key={k} title={label} sub={sub} actions={<Pill tone={tone}>{(B[k] || []).length}</Pill>}>
                  {!(B[k] || []).length && <Empty icon="calendar" title="Nothing here right now" />}
                  <ul className="rf-insp-list">
                    {(B[k] || []).map((c) => (
                      <li key={(c.inspection_id || c.booking_link_id || c.lead_id) + k} className="rf-insp-row ri-row">
                        {c.when
                          ? <span className="rf-insp-date">
                              <b>{localWhen(c.when, { weekday: 'short', month: undefined, day: undefined, hour: undefined, minute: undefined })}</b>
                              <span>{localWhen(c.when, { weekday: undefined, hour: undefined, minute: undefined })}</span>
                            </span>
                          : <Avatar name={c.name} size={40} />}
                        <span className="rf-insp-main">
                          <b>{c.name}{c.report_number ? <span className="ri-rn">{c.report_number}</span> : null}</b>
                          <span className="rf-muted">{c.address || 'No address on file'}</span>
                          <span className="rf-muted ri-row__meta">
                            {[c.when && localWhen(c.when, { weekday: undefined, month: undefined, day: undefined }),
                              c.inspector_name, c.severity_label && `${c.severity_label} damage`, c.recommendation_label,
                              c.photo_count != null && `${c.photo_count} photo${c.photo_count === 1 ? '' : 's'}`,
                              c.follow_up_at && `Follow up ${localWhen(c.follow_up_at, { hour: undefined, minute: undefined })}`,
                              !c.when && (c.summary || c.roof_issue)].filter(Boolean).join(' · ')}
                          </span>
                        </span>
                        <button className={`rf-btn rf-btn--sm ${['start', 'open', 'book', 'next'].includes(c.action?.key) ? 'rf-btn--primary' : 'rf-btn--ghost'}`}
                          disabled={busy && busy === (c.booking_link_id || c.lead_id)} onClick={() => act(c)}>
                          {c.action?.label || 'Open'}
                        </button>
                      </li>
                    ))}
                  </ul>
                </Panel>
              )
            ))}
            {!tab && TABS.every(([k]) => !(B[k] || []).length) && (
              <Empty icon="calendar" title="No inspections yet">Book one above. When the salesperson arrives, they open it here to document the roof.</Empty>
            )}
          </div>
        </>
      )}
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
