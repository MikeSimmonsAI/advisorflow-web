/**
 * My Day — the salesperson's home screen.
 *
 * Every number and every row comes from /sales/my-day, computed from real
 * opportunity and appointment data.
 *
 * As of Checkpoint 2 the appointment panels are live. An empty "Today's
 * Schedule" now means the salesperson has nothing booked — which is a different
 * statement from the "not built yet" this screen used to make, and the empty
 * state says so.
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import NewProspect from './NewProspect'
import {
  Card, Chip, Metric, Empty, ErrorBar,
  money, dateTime, dueLabel,
} from './parts'

/** The team-timezone wall clock the server already resolved for us. */
function apptTime(a) {
  const iso = a.starts_at_local || a.starts_at
  const d = new Date(iso)
  if (isNaN(d)) return ''
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

const CONF_TONE = {
  confirmed: 'green', declined: 'red', no_show: 'red',
  cancelled: 'red', sent: 'amber', pending: 'amber',
}

export function ConfChip({ status }) {
  if (!status) return null
  return <Chip tone={CONF_TONE[status]}>{String(status).replace('_', ' ')}</Chip>
}

function ApptRow({ appt, onOpen, onConfirm }) {
  return (
    <div className="sw-row">
      <button className="sw-rowlink"
              onClick={() => appt.opportunity_id && onOpen(appt.opportunity_id)}>
        <b>{apptTime(appt)} · {appt.prospect_company || appt.title}</b>
        <p>
          {appt.meeting_type || 'Meeting'}
          {appt.participants?.length
            ? ' · ' + appt.participants.map(p => p.full_name.split(' ')[0]).join(', ')
            : ''}
        </p>
      </button>
      <div className="sw-actions">
        <ConfChip status={appt.confirmation_status} />
        {onConfirm && appt.confirmation_status !== 'confirmed' && (
          <button className="sw-tiny sw-primary"
                  onClick={() => onConfirm(appt.id)}>Confirm</button>
        )}
      </div>
    </div>
  )
}

function OppRow({ opp, onOpen, note }) {
  const due = dueLabel(opp.next_action_due_at)
  return (
    <div className="sw-row">
      <button className="sw-rowlink" onClick={() => onOpen(opp.id)}>
        <b>{opp.company_name}</b>
        <p>
          {opp.contact_name ? opp.contact_name + ' · ' : ''}
          {note || opp.next_action || opp.stage_label}
        </p>
      </button>
      <div className="sw-actions">
        {opp.attention && <Chip tone="amber">{opp.attention}</Chip>}
        {due.text && <Chip tone={due.tone}>{due.text}</Chip>}
        <button className="sw-tiny sw-primary" onClick={() => onOpen(opp.id)}>Open</button>
      </div>
    </div>
  )
}

export default function MyDay() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setData(await api.get('/sales/my-day')) }
    catch (e) { setError(e.message || 'Could not load My Day.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const open = id => nav('/sales/opportunities/' + id)
  const m = data?.metrics || {}

  /** Mark a meeting confirmed. Records the source as a staff action — that is
   *  weaker evidence than a prospect clicking a link, and the API stores which. */
  async function confirm(id) {
    try {
      await api.post('/sales/appointments/' + id + '/confirmation',
                     { confirmation_status: 'confirmed', source: 'staff_manual' })
      await load()
    } catch (e) { setError(e.message || 'Could not confirm.') }
  }

  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long', month: 'long', day: 'numeric',
  })

  return (
    <SalesShell
      title="My Day"
      subtitle={today + ' · your next actions and the deals that need you.'}
      actions={
        <>
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
          <button className="sw-btn sw-primary" onClick={() => setCreating(true)}>+ New Prospect</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      {creating && (
        <NewProspect
          onClose={() => setCreating(false)}
          onCreated={opp => { setCreating(false); open(opp.id) }}
        />
      )}

      <div className="sw-metrics">
        <Metric label="ACTIVE OPPORTUNITIES" value={m.active_opportunities ?? '—'} />
        <Metric label="FOLLOW-UPS DUE" value={m.follow_ups_due ?? '—'}
                attn={!!m.follow_ups_due} sub="today or overdue" />
        <Metric label="NEEDS ACTION" value={m.needs_action ?? '—'}
                attn={!!m.needs_action} />
        <Metric label="DEMOS TO BUILD" value={m.demos_to_build ?? '—'}
                attn={!!m.demos_to_build} />
        <Metric label="WON THIS MONTH" value={m.won_this_month ?? '—'}
                sub={m.won_value_this_month ? money(m.won_value_this_month) + ' booked' : null} />
        <Metric label="APPOINTMENTS TODAY" value={m.appointments_today ?? '—'}
                sub={m.appointments_today
                  ? (m.discoveries_today || 0) + ' discovery · ' + (m.demos_today || 0) + ' demo'
                  : null} />
        <Metric label="NEEDS CONFIRMATION" value={m.needs_confirmation ?? '—'}
                attn={!!m.needs_confirmation} />
      </div>

      <div className="sw-grid2">
        <div>
          <Card title="FOLLOW-UPS DUE" sub="Due today or already overdue" bodyless>
            {loading && !data ? <div className="sw-card-b sw-subtle">Loading…</div>
              : data?.follow_ups_due?.length
                ? data.follow_ups_due.map(o => <OppRow key={o.id} opp={o} onOpen={open} />)
                : <Empty title="Nothing due today">
                    Follow-ups appear here when an opportunity has a next action dated
                    today or earlier.
                  </Empty>}
          </Card>

          <div className="sw-mt">
            <Card title="DEALS NEEDING ACTION" sub="Derived from lifecycle state, not reminders" bodyless>
              {data?.deals_needing_action?.length
                ? data.deals_needing_action.map(o =>
                    <OppRow key={o.id} opp={o} onOpen={open} note={o.attention} />)
                : <Empty title="No deals flagged">
                    A deal is flagged when its next action is overdue, a demo is past due,
                    no next action is set, or it has stalled in one stage.
                  </Empty>}
            </Card>
          </div>

          <div className="sw-mt">
            <Card title="DEMOS TO BUILD" sub="So you never have to ask whether yours is started" bodyless>
              {data?.demos_to_build?.length
                ? data.demos_to_build.map(o =>
                    <OppRow key={o.id} opp={o} onOpen={open}
                            note={(o.demo_status || 'requested')
                                  + (o.demo_due_at ? ' · due ' + dateTime(o.demo_due_at) : '')} />)
                : <Empty title="No demos in the queue">
                    A demo enters this queue when an opportunity moves to Demo Build.
                  </Empty>}
            </Card>
          </div>
        </div>

        <div>
          {data?.next_appointment && (
            <div style={{ marginBottom: 16 }}>
              <Card title="UP NEXT" sub={data.next_appointment.meeting_type || 'Meeting'}>
                <div style={{ fontSize: 22, fontWeight: 800 }}>
                  {apptTime(data.next_appointment)}
                </div>
                <div style={{ fontSize: 13, fontWeight: 700, marginTop: 6 }}>
                  {data.next_appointment.prospect_company || data.next_appointment.title}
                </div>
                <div className="sw-subtle" style={{ marginTop: 4 }}>
                  {data.next_appointment.participants.map(p => p.full_name).join(' · ')}
                </div>
                <div className="sw-chips" style={{ marginTop: 10 }}>
                  <ConfChip status={data.next_appointment.confirmation_status} />
                  <Chip>{data.next_appointment.duration_minutes} min</Chip>
                </div>
                {data.next_appointment.opportunity_id && (
                  <button className="sw-btn sw-mt" style={{ width: '100%' }}
                          onClick={() => open(data.next_appointment.opportunity_id)}>
                    Open the deal
                  </button>
                )}
              </Card>
            </div>
          )}

          <Card title="TODAY'S SCHEDULE"
                sub={data ? (data.metrics.discoveries_today || 0) + ' discovery · '
                            + (data.metrics.demos_today || 0) + ' demo' : 'Appointments'}
                bodyless>
            {loading && !data ? <div className="sw-card-b sw-subtle">Loading…</div>
              : data?.todays_appointments?.length
                ? data.todays_appointments.map(a => (
                    <ApptRow key={a.id} appt={a} onOpen={open} />))
                : <Empty title="Nothing booked today">
                    Use <b>Find Team Time</b> on any opportunity to book a discovery
                    or demo with everyone who needs to be there.
                  </Empty>}
          </Card>

          <div className="sw-mt">
            <Card title="NEEDS CONFIRMATION" sub="Booked but not yet confirmed" bodyless>
              {data?.needs_confirmation?.length
                ? data.needs_confirmation.map(a => (
                    <ApptRow key={a.id} appt={a} onOpen={open} onConfirm={confirm} />))
                : <Empty title="Everything upcoming is confirmed" />}
            </Card>
          </div>

          <div className="sw-mt">
            <Card title="RECENT ACTIVITY" sub="Your pipeline's last movements" bodyless>
              {data?.recent_activity?.length
                ? <div className="sw-card-b">
                    <div className="sw-timeline">
                      {data.recent_activity.map(e => (
                        <div className="sw-event" key={e.id}>
                          <b>{e.summary}</b>
                          <p>
                            {dateTime(e.occurred_at)}
                            {e.actor_name ? ' · ' + e.actor_name : ''}
                            {e.detail ? ' · ' + e.detail : ''}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                : <Empty title="No activity yet">
                    Every stage change, note, package selection and discovery
                    completion lands here.
                  </Empty>}
            </Card>
          </div>
        </div>
      </div>
    </SalesShell>
  )
}
