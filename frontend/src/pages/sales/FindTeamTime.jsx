/**
 * Find Team Time — the shared-availability finder.
 *
 * Opened from an Opportunity (carrying the deal, company, prospect and owner
 * forward so nothing is retyped) or standalone from Team Availability.
 *
 * The slots shown are the INTERSECTION returned by the server: only times when
 * every required participant is free. Optional participants never remove a
 * slot — each opening reports which of them happen to be free, so a fuller room
 * can be preferred without a viable time being hidden.
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { Chip, ErrorBar } from './parts'

function isoDate(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10)
}

function localLabel(iso) {
  // The server sends the wall clock in the team's timezone already resolved.
  const d = new Date(iso)
  if (isNaN(d)) return iso
  return d.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  })
}

function dayKey(iso) {
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
}

export default function FindTeamTime({ opportunity, onClose, onBooked }) {
  const [types, setTypes] = useState([])
  const [typeId, setTypeId] = useState('')
  const [team, setTeam] = useState([])
  const [required, setRequired] = useState([])
  const [optional, setOptional] = useState([])
  const [from, setFrom] = useState(isoDate(new Date()))
  const [to, setTo] = useState(isoDate(new Date(Date.now() + 6 * 86400000)))
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [booking, setBooking] = useState(null)
  const [error, setError] = useState(null)

  const oppId = opportunity?.id

  useEffect(() => {
    const q = oppId ? '?opportunity_id=' + encodeURIComponent(oppId) : ''
    api.get('/sales/meeting-types' + q).then(t => {
      setTypes(t)
      if (t.length && !typeId) selectType(t[0], t)
    }).catch(e => setError(e.message))
    api.get('/sales/team').then(setTeam).catch(() => setTeam([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oppId])

  /** Pre-fill participants from the meeting type's resolved role slots. */
  function selectType(t, all) {
    setTypeId(t.id)
    setResult(null)
    const list = all || types
    const found = list.find(x => x.id === t.id) || t
    const req = [], opt = []
    ;(found.resolved?.required || []).forEach(s => {
      if (s.auto_selected_user_id) req.push(s.auto_selected_user_id)
      else if (s.candidates?.length === 1) req.push(s.candidates[0].id)
    })
    ;(found.resolved?.optional || []).forEach(s => {
      if (s.auto_selected_user_id) opt.push(s.auto_selected_user_id)
    })
    setRequired([...new Set(req)])
    setOptional([...new Set(opt.filter(u => !req.includes(u)))])
  }

  const selectedType = types.find(t => t.id === typeId)

  /** Role slots that could not be filled automatically — shown, never guessed. */
  const ambiguous = useMemo(() => {
    if (!selectedType) return []
    return (selectedType.resolved?.required || []).filter(
      s => !s.auto_selected_user_id && (s.candidates?.length || 0) !== 1)
  }, [selectedType])

  function toggle(list, setList, id, other, setOther) {
    if (list.includes(id)) setList(list.filter(x => x !== id))
    else {
      setList([...list, id])
      if (other.includes(id)) setOther(other.filter(x => x !== id))
    }
  }

  async function find() {
    setBusy(true); setError(null); setResult(null)
    try {
      setResult(await api.post('/sales/availability/find', {
        meeting_type_id: typeId || undefined,
        opportunity_id: oppId,
        required_user_ids: required,
        optional_user_ids: optional,
        date_from: from,
        date_to: to,
      }))
    } catch (e) {
      setError(e.message || 'Could not calculate availability.')
    } finally { setBusy(false) }
  }

  async function book(slot) {
    setBooking(slot.starts_at); setError(null)
    try {
      const slots = {}
      ;(selectedType?.resolved?.required || []).forEach(s => {
        if (s.auto_selected_user_id) slots[s.auto_selected_user_id] = s.slot
      })
      const appt = await api.post('/sales/appointments', {
        starts_at: slot.starts_at,
        meeting_type_id: typeId || undefined,
        opportunity_id: oppId,
        required_user_ids: required,
        optional_user_ids: optional,
        role_slot_by_user: slots,
      })
      onBooked && onBooked(appt)
    } catch (e) {
      // A 409 here means someone took it between the search and the click.
      setError(e.message || 'Could not book that time.')
      find()
    } finally { setBooking(null) }
  }

  const grouped = useMemo(() => {
    if (!result?.slots) return []
    const out = []
    result.slots.forEach(s => {
      const k = dayKey(s.starts_at)
      const last = out[out.length - 1]
      if (last && last.day === k) last.slots.push(s)
      else out.push({ day: k, slots: [s] })
    })
    return out
  }, [result])

  return (
    <div className="sw-modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sw-modal" style={{ maxWidth: 860 }}>
        <div className="sw-card-h">
          <div>
            <h3>FIND TEAM TIME</h3>
            <small>
              {opportunity
                ? opportunity.company_name + (opportunity.contact_name ? ' · ' + opportunity.contact_name : '')
                : 'Internal meeting'}
            </small>
          </div>
          <div className="sw-spacer" />
          <button className="sw-btn" onClick={onClose}>Close</button>
        </div>

        <div className="sw-card-b">
          <ErrorBar error={error} />

          <div className="sw-field">
            <label>MEETING TYPE</label>
            <select className="sw-select" value={typeId}
                    onChange={e => {
                      const t = types.find(x => x.id === e.target.value)
                      if (t) selectType(t)
                    }}>
              {types.map(t => (
                <option key={t.id} value={t.id}>{t.name} · {t.duration_minutes} min</option>
              ))}
            </select>
            {selectedType?.description && (
              <div className="sw-subtle" style={{ marginTop: 6 }}>{selectedType.description}</div>
            )}
          </div>

          {ambiguous.length > 0 && (
            <div className="sw-notbuilt" style={{ marginTop: 12 }}>
              <b>PICK WHO FILLS THESE ROLES</b>
              <p>
                {ambiguous.map(s => s.label).join(', ')} —{' '}
                {ambiguous.some(s => s.candidates?.length)
                  ? 'more than one person can fill this, so nothing was assumed.'
                  : 'nobody in this brand can fill this role yet.'}
              </p>
            </div>
          )}

          <div className="sw-field">
            <label>REQUIRED — every one of these must be free</label>
            <div className="sw-chips">
              {team.map(m => (
                <button key={m.id}
                        className={'sw-chip' + (required.includes(m.id) ? ' sw-green' : '')}
                        style={{ cursor: 'pointer' }}
                        onClick={() => toggle(required, setRequired, m.id, optional, setOptional)}>
                  {m.full_name}
                </button>
              ))}
            </div>
          </div>

          <div className="sw-field">
            <label>OPTIONAL — nice to have, never removes a time</label>
            <div className="sw-chips">
              {team.filter(m => !required.includes(m.id)).map(m => (
                <button key={m.id}
                        className={'sw-chip' + (optional.includes(m.id) ? ' sw-blue' : '')}
                        style={{ cursor: 'pointer' }}
                        onClick={() => toggle(optional, setOptional, m.id, required, setRequired)}>
                  {m.full_name}
                </button>
              ))}
            </div>
          </div>

          <div className="sw-grid-even">
            <div className="sw-field">
              <label>FROM</label>
              <input className="sw-input" type="date" value={from}
                     onChange={e => setFrom(e.target.value)} />
            </div>
            <div className="sw-field">
              <label>TO</label>
              <input className="sw-input" type="date" value={to}
                     onChange={e => setTo(e.target.value)} />
            </div>
          </div>

          <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
            <button className="sw-btn sw-primary" onClick={find}
                    disabled={busy || required.length === 0}>
              {busy ? 'Calculating…' : 'Find Openings'}
            </button>
          </div>

          {result && (
            <div className="sw-mt">
              <div className="sw-flex sw-between" style={{ marginBottom: 8 }}>
                <b style={{ fontSize: 12 }}>
                  {result.total} opening{result.total === 1 ? '' : 's'} where all{' '}
                  {result.required.length} required {result.required.length === 1 ? 'person is' : 'people are'} free
                </b>
                <span className="sw-subtle">{result.duration_minutes} min · {result.timezone}</span>
              </div>

              {result.total === 0 && (
                <div className="sw-notbuilt">
                  <b>NO SHARED OPENINGS</b>
                  {(result.blockers || []).map((b, i) => <p key={i}>{b}</p>)}
                  <p>Try a wider date range, or move someone to optional.</p>
                </div>
              )}

              {grouped.map(g => (
                <div key={g.day} style={{ marginBottom: 14 }}>
                  <div className="sw-subtle" style={{ marginBottom: 6, fontWeight: 800 }}>
                    {g.day.toUpperCase()}
                  </div>
                  <div className="sw-chips">
                    {g.slots.map(s => (
                      <button key={s.starts_at} className="sw-btn"
                              disabled={!!booking}
                              onClick={() => book(s)}
                              title={s.optional_available_count
                                ? 'Also free: ' + s.optional_available.map(o => o.full_name).join(', ')
                                : undefined}>
                        {booking === s.starts_at ? 'Booking…'
                          : new Date(s.starts_at).toLocaleTimeString(undefined,
                              { hour: 'numeric', minute: '2-digit' })}
                        {optional.length > 0 && (
                          <span style={{ marginLeft: 6, opacity: 0.65, fontWeight: 400 }}>
                            +{s.optional_available_count}/{optional.length}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              ))}

              {optional.length > 0 && result.total > 0 && (
                <div className="sw-subtle">
                  +n/{optional.length} shows how many optional people are also free at that time.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
