/**
 * ONE HOMEOWNER - /program/roofing/homeowners/:leadId
 *
 * The record a salesperson works from: who they are, the property, the roof,
 * and the obvious next buttons - book an inspection, start one on site
 * (unscheduled: the real visit time, no invented booking), continue the
 * open one, view finished reports and take the next sales step. Every
 * inspection here is the same #285 inspection form and report.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE } from './RoofingShell'
import { Booking } from './Inspections'
import { Avatar, ErrorNote, Icon, Loading, Pill, ago, errText, localWhen, money, phoneFmt, useApi, useToast } from './ui'
import './roofing-ops.css'
import './roofing-conv.css'
import './roofing-inspect.css'

const QUAL = { READY_TO_SEND: ['green', 'Ready to contact'], REVIEW_REQUIRED: ['amber', 'Needs review'], EXCLUDED: ['slate', 'Not contactable'] }
const nice = (v) => (v === null || v === undefined || v === '' ? null : String(v).replace(/_/g, ' '))

export default function Homeowner() {
  const { leadId } = useParams()
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const ws = useApi(`/roofing-ops/leads/${leadId}`)
  const [toast, say] = useToast()
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const booking = params.get('book') === '1'
  const setBooking = (on) => { const p = new URLSearchParams(params); if (on) p.set('book', '1'); else p.delete('book'); setParams(p, { replace: true }) }

  if (ws.error) return <div className="rf-page"><ErrorNote onRetry={ws.reload}>{ws.error}</ErrorNote></div>
  if (!ws.data) return <div className="rf-page"><Loading label="Opening homeowner" /></div>
  const d = ws.data
  const L = d.lead
  const R = d.roofing || {}
  const O = d.opportunity || {}
  const list = d.inspections || []
  const open = list.find((i) => i.status === 'in_progress')
  const done = list.filter((i) => i.status === 'completed')
  const bookedForm = d.inspection && list.find((i) => i.booking_link_id === d.inspection.booking_id)
  const booked = d.inspection && !(bookedForm && bookedForm.status === 'completed') ? d.inspection : null   // still to happen
  const closed = O.stage === 'won' || O.stage === 'lost'

  const start = async (bookingId) => {
    setBusy(bookingId || 'now')
    try {
      const ins = await api.post('/roofing-ops/inspections', { lead_id: leadId, booking_link_id: bookingId || null })
      nav(`${BASE}/inspections/${ins.id}`)
    } catch (e) { say(errText(e), 'err'); setBusy('') }
  }
  const addNote = async () => {
    if (!note.trim()) return
    try { await api.post(`/roofing-ops/leads/${leadId}/notes`, { body: note.trim() }); setNote(''); say('Note added'); ws.reload() } catch (e) { say(errText(e), 'err') }
  }

  return (
    <div className="rf-page rf-ho">
      <div className="ri-top">
        <Link to={`${BASE}/leads`} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="back" size={16} />Leads &amp; data</Link>
        <Link to={`${BASE}/conversations?lead=${leadId}`} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="chat" size={16} />Conversation</Link>
      </div>

      <section className="rf-panel rf-ho__head">
        <Avatar name={L.name} size={54} />
        <div className="rf-ho__who">
          <h1 className="rf-h1">{L.name}</h1>
          <p className="rf-muted"><Icon name="pin" size={15} /> {L.address || 'No address on file'}</p>
          <div className="rf-ho__pills">
            <Pill tone={O.stage === 'won' ? 'green' : O.stage === 'lost' ? 'slate' : 'blue'}>{O.stage_label}</Pill>
            {R.urgency && <Pill tone={R.urgency === 'emergency' ? 'red' : R.urgency === 'high' ? 'orange' : 'slate'}>{R.urgency}</Pill>}
            {L.is_test && <Pill tone="amber">Sample data</Pill>}
          </div>
        </div>
      </section>

      <section className="rf-panel rf-ho__actions" aria-label="Inspection">
        <h2 className="rf-panel__title">Inspection</h2>
        {booked && <p className="rf-ho__booked"><Icon name="calendar" size={16} />Booked: <b>{localWhen(d.inspection.at_local)}</b> · {d.inspection.label} with {d.inspection.salesperson_name || 'the team'}</p>}
        <div className="rf-ho__btns">
          {open ? (
            <Link className="rf-btn rf-btn--primary rf-add__big" to={`${BASE}/inspections/${open.id}`}><Icon name="clipboard" size={18} />Continue inspection</Link>
          ) : booked && !bookedForm ? (
            <button className="rf-btn rf-btn--primary rf-add__big" disabled={!!busy} onClick={() => start(d.inspection.booking_id)}><Icon name="clipboard" size={18} />{busy ? 'Opening…' : 'Open inspection form'}</button>
          ) : (
            <button className={`rf-btn ${closed || done.length ? 'rf-btn--ghost' : 'rf-btn--primary'} rf-add__big`} disabled={!!busy} onClick={() => start(null)}><Icon name="clipboard" size={18} />{busy ? 'Opening…' : done.length ? 'Start another inspection' : 'Start inspection now'}</button>
          )}
          {!booking && <button className="rf-btn rf-btn--ghost rf-add__big" onClick={() => setBooking(true)}><Icon name="calendar" size={18} />{booked ? 'Book another time' : 'Book inspection'}</button>}
        </div>
        {!open && !booked && !done.length && <p className="rf-muted rf-small">On site now? “Start inspection now” records the real visit time - no calendar booking is made.</p>}
        {booking && <div style={{ marginTop: 14 }}><Booking leadId={leadId} onClose={() => setBooking(false)} say={say} onBooked={() => ws.reload()} /></div>}
        {done.length > 0 && (
          <ul className="rf-ho__reports">
            {done.map((i) => (
              <li key={i.id}>
                <div><b>Report {i.report_number}</b><span className="rf-muted">Completed {i.completed_at ? new Date(i.completed_at).toLocaleDateString() : ''}{i.recommendation_label ? ` · ${i.recommendation_label}` : ''}</span></div>
                <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${BASE}/inspections/${i.id}/report`}><Icon name="file" size={15} />View report</Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rf-panel" aria-label="Sales">
        <h2 className="rf-panel__title">Sales</h2>
        <p><b>{d.next_action?.label}</b></p>
        <p className="rf-muted">{O.estimate_amount != null ? `Estimate ${money(O.estimate_amount, false)}` : 'No estimate yet'}{O.won_amount != null ? ` · job ${money(O.won_amount, false)}` : ''}</p>
        {done[0] && !closed && (
          <Link className="rf-btn rf-btn--primary rf-add__big" style={{ marginTop: 10 }} to={`${BASE}/inspections/${done[0].id}/report`}><Icon name="dollar" size={18} />Next sales step</Link>
        )}
      </section>

      <div className="rf-grid-2">
        <section className="rf-panel">
          <h2 className="rf-panel__title">Homeowner</h2>
          <dl className="rf-dl">
            <dt>Phone</dt><dd>{L.phone ? <a href={`tel:${L.phone}`}>{phoneFmt(L.phone)}</a> : '—'}</dd>
            <dt>Email</dt><dd>{L.email || '—'}</dd>
            <dt>Address</dt><dd>{L.address || '—'}</dd>
            <dt>Source</dt><dd>{L.source_detail || L.source || '—'}</dd>
            <dt>Salesperson</dt><dd>{L.owner_name || 'Unassigned'}</dd>
            <dt>Added</dt><dd>{L.created_at ? ago(L.created_at) : '—'}</dd>
          </dl>
          <h3 className="rf-ho__h3">Can we contact them?</h3>
          {Object.entries(d.qualification || {}).map(([ch, q]) => {
            const [tone, label] = QUAL[q.bucket] || ['slate', q.bucket]
            return <p key={ch} className="rf-ho__qual"><span>{ch === 'sms' ? 'Text' : 'Email'}</span><Pill tone={tone}>{label}</Pill>
              {q.reasons?.[0] && <span className="rf-muted">{q.reasons[0].detail || q.reasons[0].label || q.reasons[0].code}</span>}</p>
          })}
        </section>
        <section className="rf-panel">
          <h2 className="rf-panel__title">Roof</h2>
          <dl className="rf-dl">
            <dt>Problem</dt><dd>{R.roof_issue || '—'}</dd>
            <dt>Repair or replace</dt><dd>{nice(R.service_type) || '—'}</dd>
            <dt>Urgency</dt><dd>{nice(R.urgency) || '—'}</dd>
            <dt>Roof age</dt><dd>{R.roof_age != null && R.roof_age !== '' ? `${R.roof_age} years` : '—'}</dd>
          </dl>
          <h3 className="rf-ho__h3">Notes</h3>
          <div className="rf-conv__noteadd">
            <input className="rf-input" placeholder="Add a note" value={note} onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addNote() }} aria-label="New note" />
            <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={addNote} disabled={!note.trim()}>Add</button>
          </div>
          {(d.notes || []).slice(0, 8).map((nt) => (
            <div key={nt.id} className="rf-conv__note"><p>{nt.body}</p><span className="rf-muted">{nt.author || 'Team'} · {ago(nt.at)}</span></div>
          ))}
        </section>
      </div>
      {toast}
    </div>
  )
}
