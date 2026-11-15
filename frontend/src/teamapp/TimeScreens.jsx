/**
 * TIME — Clock In / Clock Out, My Time and Team Time.
 *
 * The server stamps every clock action with its own time; this screen sends
 * no time at all. After each tap it shows what the server SAVED, and the
 * elapsed clock counts from the server's clock-in time against the server's
 * "now" (plus seconds since that answer arrived), not the phone's clock.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { addDays, elapsedSince, fmtDate, fmtElapsed, fmtHours, fmtLocalDay, fmtTime, LABELS,
         localYmd, toLocalInput } from './teamHelpers'
import { ErrorNote, Icon, Loading, ScreenTitle, useTeam, useTeamApi } from './TeamApp'

const OPTS = { skipRedirect: true, noOrgContext: true }

function useTick(on) {
  const [, setT] = useState(0)
  useEffect(() => {
    if (!on) return undefined
    const t = setInterval(() => setT(x => x + 1), 1000)
    return () => clearInterval(t)
  }, [on])
}

function EntryRow({ e, tz, onOpen, showName }) {
  return (
    <li>
      <button type="button" className="ta-row ta-entry" onClick={onOpen ? () => onOpen(e) : undefined} disabled={!onOpen}>
        <span className="ta-row-main">
          {showName && <span className="ta-row-name">{e.user_name}</span>}
          <span className={showName ? 'ta-row-sub' : 'ta-row-name'}>{fmtDate(e.clock_in_at, tz)}</span>
          <span className="ta-row-sub">{fmtTime(e.clock_in_at, tz)} – {e.open ? 'now' : fmtTime(e.clock_out_at, tz)}
            {e.clock_out_at && localYmd(e.clock_out_at, tz) !== localYmd(e.clock_in_at, tz) ? ' (next day)' : ''}
            {e.corrections > 0 ? ' · corrected' : ''}</span>
        </span>
        <span className={'ta-entry-hours' + (e.open ? ' is-open' : '')}>{fmtHours(e.seconds)}</span>
        {onOpen && <Icon name="chev" size={18} />}
      </button>
    </li>
  )
}

function History({ entryId, tz }) {
  const h = useTeamApi('/team-app/time/entries/' + entryId + '/history')
  if (h.loading) return <Loading />
  if (h.error) return <ErrorNote error={h.error} />
  if (!h.data.history.length) return <p className="ta-muted">No corrections.</p>
  return (
    <ul className="ta-history">
      {h.data.history.map(c => (
        <li key={c.id}>
          <strong>{c.changed_by_name}</strong> · {fmtDate(c.changed_at, tz)} {fmtTime(c.changed_at, tz)}
          <div>{fmtTime(c.before.clock_in_at, tz)}–{c.before.clock_out_at ? fmtTime(c.before.clock_out_at, tz) : 'open'} → {fmtTime(c.after.clock_in_at, tz)}–{c.after.clock_out_at ? fmtTime(c.after.clock_out_at, tz) : 'open'}</div>
          <div className="ta-muted">Reason: {c.reason}</div>
        </li>
      ))}
    </ul>
  )
}

// ── Time (Clock + My Time) ──────────────────────────────────────────────────
export function TimeScreen() {
  const navigate = useNavigate()
  const { tz, me } = useTeam()
  const [status, setStatus] = useState(null)
  const [received, setReceived] = useState(Date.now())
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [weekOf, setWeekOf] = useState(null)
  const [open, setOpen] = useState(null)
  const week = useTeamApi('/team-app/time/mine' + (weekOf ? '?week_of=' + weekOf : ''), { deps: [status && status.clocked_in] })

  const load = () => api.get('/team-app/time/status', OPTS)
    .then(s => { setStatus(s); setReceived(Date.now()); setErr(null) }).catch(setErr)
  useEffect(() => { load() }, [])
  useTick(!!(status && status.clocked_in))

  async function act(kind) {
    setBusy(true); setErr(null); setNote(null)
    let res = null; let lastErr = null
    // Retry is safe: the server answers a repeat with the saved record.
    for (let i = 0; i < 2 && !res; i++) {
      try { res = await api.post('/team-app/time/' + kind, {}) } catch (e) { lastErr = e; if (e.status && e.status < 500) break }
    }
    setBusy(false)
    if (!res) { setErr(lastErr); return }
    setStatus(res); setReceived(Date.now())
    if (res.already) setNote(kind === 'clock-in' ? 'You were already clocked in.' : 'You were already clocked out.')
    else if (kind === 'clock-out' && res.last_entry) setNote('Clocked out. Shift: ' + fmtHours(res.last_entry.seconds) + '.')
    week.reload()
  }

  if (me && me.access && !me.access.time) return <div className="ta-screen"><ErrorNote error={{ status: 403, message: 'Time is not turned on for your account.' }} /></div>
  const clocked = status && status.clocked_in
  const elapsed = clocked ? elapsedSince(status.entry.clock_in_at, status.server_now, received) : 0
  const w = week.data
  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.time} action={me && me.access && me.access.time_manage
        ? <button type="button" className="ta-btn ta-btn--ghost ta-btn--sm" onClick={() => navigate('/app/time/team')}>{LABELS.teamTime}</button> : null} />
      <section className={'ta-clock' + (clocked ? ' is-in' : '')} aria-live="polite">
        {!status && !err && <Loading />}
        {status && <>
          <div className="ta-clock-status">{clocked ? 'Clocked in' : 'Clocked out'}</div>
          {clocked
            ? <><div className="ta-clock-elapsed">{fmtElapsed(elapsed)}</div>
              <div className="ta-clock-since">since {fmtTime(status.entry.clock_in_at, tz)} · {fmtDate(status.entry.clock_in_at, tz)}</div></>
            : <div className="ta-clock-since">Tap to start your shift</div>}
          <button type="button" className={'ta-clock-btn' + (clocked ? ' is-out' : '')} disabled={busy}
                  onClick={() => act(clocked ? 'clock-out' : 'clock-in')}>
            {busy ? 'Saving…' : clocked ? LABELS.clockOut : LABELS.clockIn}</button>
        </>}
        {note && <div className="ta-note" role="status">{note}</div>}
        {err && <ErrorNote error={err} onRetry={load} />}
      </section>

      <div className="ta-section-head">
        <h2 className="ta-h2">{LABELS.myTime}</h2>
        {w && <div className="ta-weeknav">
          <button type="button" className="ta-icon-btn" aria-label="Previous week" onClick={() => setWeekOf(addDays(w.week_start, -7))}><Icon name="back" size={18} /></button>
          <span>Week of {fmtLocalDay(w.week_start)}</span>
          <button type="button" className="ta-icon-btn ta-flip" aria-label="Next week" onClick={() => setWeekOf(addDays(w.week_start, 7))}><Icon name="back" size={18} /></button>
        </div>}
      </div>
      {week.loading && !w && <Loading />}
      {week.error && <ErrorNote error={week.error} onRetry={week.reload} />}
      {w && <>
        <div className="ta-total"><span>Week total</span><strong>{fmtHours(w.total_seconds)}</strong></div>
        <div className="ta-days">{w.days.map(d => (
          <div key={d.date} className={'ta-day' + (d.seconds ? ' has-time' : '')}><span>{fmtLocalDay(d.date).slice(0, 3)}</span><strong>{d.seconds ? fmtHours(d.seconds).replace(' ', '') : '–'}</strong></div>))}
        </div>
        {w.entries.length === 0 ? <div className="ta-state">No time this week.</div>
          : <ul className="ta-list">{w.entries.map(e => <EntryRow key={e.id} e={e} tz={tz} onOpen={e.corrections ? setOpen : null} />)}</ul>}
        <p className="ta-muted ta-small">Times shown in {w.time_zone.replace('_', ' ')}.</p>
      </>}
      {open && (
        <div className="ta-sheet" role="dialog" aria-modal="true" aria-label="Correction history">
          <div className="ta-sheet-card">
            <div className="ta-sheet-head"><h2 className="ta-h2">Correction history</h2><button type="button" className="ta-icon-btn" onClick={() => setOpen(null)} aria-label="Close"><Icon name="x" /></button></div>
            <History entryId={open.id} tz={tz} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Team Time (platform + assigned managers) ────────────────────────────────
export function TeamTimeScreen() {
  const navigate = useNavigate()
  const { tz } = useTeam()
  const today = localYmd(new Date().toISOString(), tz)
  const [from, setFrom] = useState(addDays(today, -6))
  const [to, setTo] = useState(today)
  const [who, setWho] = useState('')
  const [edit, setEdit] = useState(null)
  const q = '/team-app/time/team?date_from=' + from + '&date_to=' + to + (who ? '&user_id=' + who : '')
  const team = useTeamApi(q)
  const d = team.data
  return (
    <div className="ta-screen">
      <div className="ta-subhead"><button type="button" className="ta-icon-btn" onClick={() => navigate('/app/time')} aria-label="Back"><Icon name="back" /></button><h1 className="ta-title">{LABELS.teamTime}</h1></div>
      <div className="ta-filters">
        <label>From<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
        <label>To<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
      </div>
      {team.loading && !d && <Loading />}
      {team.error && <ErrorNote error={team.error} onRetry={team.reload} />}
      {d && <>
        {d.people.length === 0 && <div className="ta-state">Nobody is assigned to you yet.</div>}
        <ul className="ta-list">
          {d.people.map(p => (
            <li key={p.user_id}><button type="button" className={'ta-row' + (who === p.user_id ? ' is-picked' : '')} onClick={() => setWho(w => w === p.user_id ? '' : p.user_id)}>
              <span className="ta-row-main"><span className="ta-row-name">{p.name}</span><span className="ta-row-sub">{p.clocked_in ? 'Clocked in now' : 'Clocked out'}</span></span>
              <span className="ta-entry-hours">{fmtHours(p.total_seconds)}</span></button></li>
          ))}
        </ul>
        <h2 className="ta-h2">Entries{who ? ' · ' + (d.people.find(p => p.user_id === who) || {}).name : ''}</h2>
        {d.entries.length === 0 ? <div className="ta-state">No entries in this range.</div>
          : <ul className="ta-list">{d.entries.map(e => <EntryRow key={e.id} e={e} tz={tz} showName onOpen={setEdit} />)}</ul>}
      </>}
      {edit && <CorrectSheet entry={edit} tz={tz} onClose={() => setEdit(null)} onSaved={() => { team.reload() }} />}
    </div>
  )
}

function CorrectSheet({ entry, tz, onClose, onSaved }) {
  const [inAt, setInAt] = useState(toLocalInput(entry.clock_in_at, tz))
  const [outAt, setOutAt] = useState(toLocalInput(entry.clock_out_at, tz))
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [n, setN] = useState(0)
  async function save(e) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      await api.patch('/team-app/time/entries/' + entry.id, { clock_in_at: inAt, clock_out_at: outAt || null, reason })
      setReason(''); setN(x => x + 1); onSaved()
    } catch (ex) { setErr(ex.message) } finally { setBusy(false) }
  }
  return (
    <div className="ta-sheet" role="dialog" aria-modal="true" aria-label="Correct time entry">
      <form className="ta-sheet-card" onSubmit={save}>
        <div className="ta-sheet-head"><h2 className="ta-h2">Correct entry · {entry.user_name}</h2><button type="button" className="ta-icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></div>
        <label className="ta-form-field">{LABELS.clockIn}<input type="datetime-local" value={inAt} onChange={e => setInAt(e.target.value)} required /></label>
        <label className="ta-form-field">{LABELS.clockOut}<input type="datetime-local" value={outAt} onChange={e => setOutAt(e.target.value)} /></label>
        <label className="ta-form-field">Reason (required)<textarea value={reason} onChange={e => setReason(e.target.value)} required minLength={3} rows={2} placeholder="Why is this changing?" /></label>
        <p className="ta-muted ta-small">Times are in {tz.replace('_', ' ')}. Who changed it, when and why is recorded.</p>
        {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
        <button type="submit" className="ta-btn ta-btn--primary ta-btn--block" disabled={busy || reason.trim().length < 3}>{busy ? 'Saving…' : 'Save correction'}</button>
        <h3 className="ta-h3">History</h3>
        <History key={n} entryId={entry.id} tz={tz} />
      </form>
    </div>
  )
}
