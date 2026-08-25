/**
 * My Day — the salesperson's home screen.
 *
 * Every number and every row here comes from /sales/my-day, computed from real
 * opportunity data. The appointment-shaped sections render NotBuilt, because
 * the scheduling engine is Checkpoint 2 and an empty "Today's Schedule" would
 * read as "you have nothing on" rather than "this does not exist yet".
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import NewProspect from './NewProspect'
import {
  Card, Chip, Metric, Empty, NotBuilt, ErrorBar,
  money, dateTime, dueLabel,
} from './parts'

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
        <Metric label="APPOINTMENTS TODAY" value="—" sub="scheduling: Checkpoint 2" />
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
          <Card title="TODAY'S SCHEDULE" sub="Appointments">
            <NotBuilt label="SCHEDULING NOT BUILT YET" block={data?.todays_appointments} />
            <p className="sw-subtle" style={{ marginTop: 10, lineHeight: 1.7 }}>
              Checkpoint 2 brings the availability engine, shared multi-person time
              finding, and appointment creation. Until then no appointment data
              exists — this panel will not show a false empty schedule.
            </p>
          </Card>

          <div className="sw-mt">
            <Card title="NEEDS CONFIRMATION" sub="Meeting confirmation">
              <NotBuilt label="NOT BUILT YET" block={data?.needs_confirmation} />
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
