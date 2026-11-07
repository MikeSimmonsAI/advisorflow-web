/* VOLT - "My Application" (#535, 2026-11-06), the applicant's home.
 *
 * Replaces the rep dashboard. An applicant is not a rep before approval/start,
 * so this page shows the APPLICATION reference (VOLT-xxxxxxxx), never a rep ID,
 * and there are no referral codes, invite links or leaderboards here.
 *
 * One application is shown at a time; a person with several switches between
 * them, and status, documents, the Zoom step and bookings all come from the
 * selected application. The Zoom step appears only when staff open it; the
 * picker closes itself only after the server confirms the booking, and stays
 * open with a clear message when it does not.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { VOLT_SUPPORT_EMAIL } from '../../utils/voltDomain'
import { DocRow } from './VoltApplication'
import { Modal } from './VoltDialogs'
import { fmtDate, volt } from './voltApi'

const KIND = { interview: 'interview', orientation: 'orientation' }

/** "Thu, Nov 12, 2026 · 10:00 AM CST" in the SESSION's own time zone, plus the zone id. */
export function zoomWhen(iso, tz) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const day = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: tz })
    const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: tz, timeZoneName: 'short' })
    return `${day} · ${time}`
  } catch { return new Date(iso).toLocaleString() }
}

// Progress is the REVIEW status only. Scheduling is shown in its own card, so
// an orientation never marks a fictional "Interview" step as done.
const STEPS = [['draft', 'Application'], ['submitted', 'Submitted'], ['review', 'Under review'], ['approved', 'Approved'], ['started', 'Started']]
function progress(status) {
  switch (status) {
    case 'draft': return { at: 0 }
    case 'submitted': return { at: 2 }
    case 'needs_update': return { at: 2, label: 'Updates needed' }
    case 'approved': return { at: 3 }
    case 'started': return { at: 5 }
    default: return { at: -1 }
  }
}

function Progress({ status }) {
  const p = progress(status)
  if (status === 'withdrawn') return <p className="v-muted" data-progress="closed">This application is closed.</p>
  return (
    <ol className="v-ma-steps" aria-label="Application progress" data-progress={status}>
      {STEPS.map(([k, label], i) => {
        const done = i < p.at; const now = i === p.at
        return (
          <li key={k} className={`v-ma-step${done ? ' done' : ''}${now ? ' now' : ''}`} aria-current={now ? 'step' : undefined}>
            <span aria-hidden="true">{done ? '✓' : now ? '●' : ''}</span> {now && p.label ? p.label : label}
            <span className="v-sr">{done ? ' (done)' : now ? ' (current)' : ''}</span>
          </li>
        )
      })}
    </ol>
  )
}

function statusTag(a) {
  if (a.status === 'withdrawn') return ['Closed', 'v-tag--line']
  if (a.zoom?.state === 'booked') return ['Zoom scheduled', 'v-tag--ok']
  if (a.zoom?.state === 'choose') return ['Ready to schedule', '']
  if (a.status === 'needs_update') return ['Updates needed', '']
  return [a.status_label, a.status === 'draft' ? 'v-tag--line' : 'v-tag--black']
}

// ── The Zoom step ───────────────────────────────────────────────────────────
function ZoomPicker({ app, onClose, onBooked }) {
  const [times, setTimes] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const load = useCallback(async () => {
    try { const r = await volt.zoomTimes(app.id); setTimes(r.times) } catch (x) { setErr(x.message); setTimes([]) }
  }, [app.id])
  useEffect(() => { load() }, [load])
  async function choose(t) {
    if (busy) return
    setBusy(t.id); setErr('')
    try {
      const r = await volt.bookZoom(app.id, t.id)
      onBooked(r)                                   // only now: the server confirmed
    } catch (x) {
      setErr(x.message || 'That time could not be booked. Choose another time.')
      if (x.detail?.code === 'slot_unavailable' || x.status === 409) await load()
    } finally { setBusy('') }
  }
  const kind = KIND[app.zoom?.stage] || 'Zoom'
  return (
    <Modal title="Choose your Zoom date" onClose={busy ? () => {} : onClose} labelId="v-zoom-title" name="zoom-picker" wide>
      <p className="v-muted">Times set by VOLT recruiting staff for your {kind}. Each time shows its own time zone.</p>
      <p className="v-small v-muted">Choosing a time books it right away. This window closes once your booking is confirmed.</p>
      {err && <div className="v-alert v-alert--err" role="alert" data-zoom-error>{err}</div>}
      {times === null ? <p className="v-mono" role="status">Loading times…</p> : times.length === 0 ? (
        <p className="v-empty" data-zoom-empty>No times are open right now. Please check back, or email <a href={`mailto:${VOLT_SUPPORT_EMAIL}`}>{VOLT_SUPPORT_EMAIL}</a>.</p>
      ) : (
        <ul className="v-ma-times" aria-label="Available times">
          {times.map((t) => (
            <li key={t.id} className="v-doc" data-time={t.id}>
              <div>
                <b>{zoomWhen(t.starts_at, t.timezone)}</b>
                <p className="v-small v-muted">{t.duration_minutes} minutes · {t.timezone} · {t.spots_left} spot{t.spots_left === 1 ? '' : 's'} available{t.title ? ` · ${t.title}` : ''}</p>
              </div>
              <button type="button" className="v-btn v-btn--red v-btn--sm" disabled={!!busy} aria-busy={busy === t.id}
                      onClick={() => choose(t)} data-choose={t.id}>
                {busy === t.id ? 'Booking…' : 'Choose this time'}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="v-row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
        <button type="button" className="v-btn v-btn--line" onClick={onClose} disabled={!!busy}>Not now</button>
      </div>
    </Modal>
  )
}

function ZoomCard({ app, session, onPick, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [confirm, setConfirm] = useState(false)
  const kind = KIND[app.zoom?.stage] || 'Zoom'
  async function cancel() {
    setBusy(true); setErr('')
    try { await volt.cancelZoom(app.id); setConfirm(false); await onChanged('Booking canceled. You can choose another time.') } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  if (app.status === 'withdrawn') return null
  if (app.zoom?.state === 'booked' && session) {
    return (
      <section className="v-panel v-ma-zoom" data-zoom="booked" aria-labelledby="v-zoom-h">
        <h3 id="v-zoom-h">Zoom scheduled ✓</h3>
        <p><b data-zoom-when>{zoomWhen(session.starts_at, session.timezone)}</b></p>
        <p className="v-small v-muted">{session.duration_minutes} minutes · {session.timezone} · {session.kind === 'interview' ? 'Interview' : 'Orientation'}{session.title ? ` · ${session.title}` : ''}</p>
        {session.rescheduled_at && <p className="v-small"><b>New time.</b> Our team moved this session; the time above is current.</p>}
        <p className="v-muted">Your booking is confirmed. Your application stays active.</p>
        {session.instructions && <p className="v-small">{session.instructions}</p>}
        {session.join_url && <a className="v-btn v-btn--red v-ma-full" href={session.join_url} target="_blank" rel="noopener noreferrer" data-join>Join Zoom ↗</a>}
        {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
        {session.applicant_can_cancel ? (confirm ? (
          <div className="v-alert v-alert--info" role="group" aria-label="Confirm cancel">
            <p style={{ marginBottom: 8 }}>Cancel this booking? The time is released for someone else, and you can choose again.</p>
            <div className="v-row" style={{ justifyContent: 'flex-start' }}>
              <button type="button" className="v-btn v-btn--red v-btn--sm" onClick={cancel} disabled={busy} data-cancel-confirm>{busy ? 'Canceling…' : 'Yes, cancel booking'}</button>
              <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setConfirm(false)} disabled={busy}>Keep it</button>
            </div>
          </div>
        ) : <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setConfirm(true)} data-cancel>Cancel booking / choose again</button>)
          : <p className="v-small v-muted">Need to change it? Email <a href={`mailto:${VOLT_SUPPORT_EMAIL}`}>{VOLT_SUPPORT_EMAIL}</a>.</p>}
      </section>
    )
  }
  if (app.zoom?.state === 'choose') {
    return (
      <section className="v-panel v-ma-zoom" data-zoom="choose" aria-labelledby="v-zoom-h">
        <h3 id="v-zoom-h">Choose your Zoom date</h3>
        <p className="v-muted">You're ready for the next step. Choose a time for your {kind} from the times VOLT recruiting staff set.</p>
        <button type="button" className="v-btn v-btn--red v-ma-full" onClick={onPick} data-open-picker>Choose a Zoom date →</button>
      </section>
    )
  }
  return (
    <section className="v-panel v-ma-zoom" data-zoom="waiting" aria-labelledby="v-zoom-h">
      <h3 id="v-zoom-h">Zoom scheduling</h3>
      <p className="v-muted">When your application reaches this step, our team will open scheduling here and you'll choose a Zoom time.</p>
    </section>
  )
}

function nextButton(a, onPick) {
  if (a.status === 'draft') return <Link className="v-btn v-btn--red" to={`/volt/me/applications/${a.id}`} data-next-action>Continue application →</Link>
  if (a.status === 'needs_update') return <Link className="v-btn v-btn--red" to={`/volt/me/applications/${a.id}`} data-next-action>Make requested updates →</Link>
  if (a.zoom?.state === 'choose') return <button type="button" className="v-btn v-btn--red" onClick={onPick} data-next-action>Select a date</button>
  if (a.zoom?.state === 'booked') return <span className="v-tag v-tag--ok">Confirmed</span>
  return null
}

export default function MyApplication({ me, reload, portal }) {
  const loc = useLocation()
  const navigate = useNavigate()
  const apps = useMemo(() => me?.applications || [], [me])
  const wanted = new URLSearchParams(loc.search).get('application')
  const fallback = useMemo(() => {
    const act = apps.find((a) => a.next_action?.kind === 'applicant')
    return (act || apps[apps.length - 1] || {}).id
  }, [apps])
  const selectedId = apps.some((a) => a.id === wanted) ? wanted : fallback
  const a = apps.find((x) => x.id === selectedId)
  const [picker, setPicker] = useState(false)
  const [toast, setToast] = useState('')
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(''), 5000); return () => clearTimeout(t) }, [toast])
  if (!me?.has_profile) return <Navigate to="/volt#projects" replace />
  if (!a) return <div className="v-page"><p className="v-muted">No applications yet. <Link to="/volt#projects">See open opportunities</Link></p></div>
  const select = (id) => navigate(`/volt/me?application=${encodeURIComponent(id)}`, { replace: true })
  const session = (me.sessions || []).find((s) => s.application_id === a.id && s.status !== 'canceled')
  const canceled = (me.sessions || []).filter((s) => s.application_id === a.id && s.status === 'canceled')
  const docs = [...(a.documents || []), ...(me.profile_documents || [])]
  const updates = (me.updates || []).filter((u) => !u.application_id || u.application_id === a.id).slice(0, 5)
  const notApplied = (portal?.open_projects || []).filter((x) => !apps.some((y) => y.project?.key === x.key))
  const [tag, tagClass] = statusTag(a)
  async function booked() {
    setPicker(false)
    await reload()
    setToast('Zoom confirmed. Your time is booked and your application stays active.')
    setTimeout(() => document.querySelector('[data-zoom="booked"] h3')?.focus?.(), 50)
  }
  return (
    <div className="v-page v-ma" data-my-application={a.id}>
      <div className="v-ma-top">
        <div>
          <h1 className="v-ma-title">Hi {me.profile.first_name}. Your next step starts here.</h1>
          <p className="v-muted">Application reference <b data-reference>{a.reference}</b></p>
        </div>
        {apps.length > 1 && (
          <label className="v-ma-switch" htmlFor="v-app-switch">Your applications
            <select id="v-app-switch" value={a.id} onChange={(e) => select(e.target.value)} data-app-switch>
              {apps.map((x) => <option key={x.id} value={x.id}>{x.project?.name} · {x.reference}</option>)}
            </select>
          </label>
        )}
      </div>

      <section className="v-panel v-ma-status" aria-labelledby="v-ma-proj">
        <div>
          <h2 id="v-ma-proj" className="v-ma-proj">{a.project?.name}</h2>
          <span className={`v-tag ${tagClass}`} data-status-tag>{tag}</span>
        </div>
        <Progress status={a.status} />
      </section>

      <div className="v-grid2 v-ma-grid">
        <section className="v-panel" aria-labelledby="v-next-h">
          <h3 id="v-next-h">What happens next</h3>
          <p className="v-muted" data-next-detail>{a.next_action?.detail}</p>
          <div className="v-ma-action">
            <div><b>Your next action</b><div data-next-label>{a.next_action?.kind === 'applicant' ? a.next_action.label : a.next_action?.kind === 'done' ? a.next_action.label : "You're all caught up."}</div></div>
            {nextButton(a, () => setPicker(true))}
          </div>
        </section>
        <ZoomCard app={a} session={session} onPick={() => setPicker(true)}
                  onChanged={async (msg) => { await reload(); setToast(msg) }} />
        {canceled.length > 0 && (
          <section className="v-panel" data-zoom-canceled>
            <h3>Canceled Zoom session</h3>
            {canceled.map((s) => <p key={s.id} className="v-small">{zoomWhen(s.starts_at, s.timezone)} was canceled by our team{s.cancel_reason ? `: ${s.cancel_reason}` : '.'}</p>)}
          </section>
        )}
        <section className="v-panel" aria-labelledby="v-docs-h">
          <h3 id="v-docs-h">My documents</h3>
          {docs.length === 0 ? <p className="v-muted">No documents requested.</p>
            : docs.map((d) => <DocRow key={`${d.key}-${d.document?.id || 'none'}`} d={d}
                                      applicationId={a.documents?.includes(d) ? a.id : null}
                                      editable={a.documents?.includes(d) ? a.editable : true} onDone={reload} />)}
        </section>
        <section className="v-panel" aria-labelledby="v-upd-h">
          <h3 id="v-upd-h">Latest updates</h3>
          {updates.length === 0 ? <p className="v-muted">No updates yet. Your application is saved; you don't need to apply again.</p> : (
            <ul className="v-ma-updates">{updates.map((u, i) => <li key={i}><b>{u.text}</b> <span className="v-small v-muted">{fmtDate(u.at)}</span></li>)}</ul>
          )}
          <div className="v-row" style={{ justifyContent: 'flex-start', marginTop: 10 }}>
            <Link className="v-btn v-btn--line v-btn--sm" to="/volt/me/profile">Edit my information</Link>
            <Link className="v-btn v-btn--line v-btn--sm" to="/volt/me/support">Support</Link>
          </div>
        </section>
      </div>

      {notApplied.length > 0 && (
        <section className="v-panel">
          <h3>Other opportunities</h3>
          {notApplied.map((x) => (
            <div key={x.key} className="v-doc"><span>{x.name}</span><Link className="v-btn v-btn--line v-btn--sm" to={`/volt/apply/${x.key}`}>Apply →</Link></div>
          ))}
        </section>
      )}
      {picker && <ZoomPicker app={a} onClose={() => setPicker(false)} onBooked={booked} />}
      {toast && <div className="v-toast" role="status" data-toast>{toast}</div>}
    </div>
  )
}

export function SupportPage({ me }) {
  const email = me?.support_email || VOLT_SUPPORT_EMAIL
  return (
    <div className="v-page" style={{ maxWidth: 680 }}>
      <Link to="/volt/me" className="v-btn v-btn--line v-btn--sm">← My Application</Link>
      <section className="v-panel" style={{ marginTop: 14 }}>
        <h1 className="v-ma-title">We're here to help.</h1>
        <p>Questions about your application, a requested document or your Zoom time? Email us and include your application reference.</p>
        <p><a className="v-btn v-btn--red" href={`mailto:${email}`}>{email}</a></p>
        <p className="v-small v-muted">This opens your email app.</p>
      </section>
    </div>
  )
}
