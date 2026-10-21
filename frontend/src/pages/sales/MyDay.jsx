/**
 * My Day — the salesperson's home screen (Lane F redesign, #281).
 *
 * Layout follows Mike's approved "Simplified Sales Workflow – My Day" design:
 * greeting, four workload cards, three big actions, today's priorities.
 * Every number is real. Nothing is sample data.
 *
 * WHOSE NUMBERS. The cards are always the viewer's OWN work
 * (/sales/my-day?scope=mine + their own deals). A manager additionally gets
 * one clearly labelled "Your team" row from the unscoped /sales/my-day, so a
 * personal number and a team number never share a card.
 *
 * What the old My Day showed that a rep still needs (meetings to confirm,
 * proposals waiting on them) appears here only when there is something to do,
 * so an empty day stays simple.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell, { useSales } from './SalesShell'
import { wallTime } from './parts'
import { followUpList, greeting, firstName, groupForSimpleBoard } from './simpleSales'
import { Icon, ErrorLine, Skeleton } from './SimpleParts'

export default function MyDay() {
  return (
    <SalesShell title="My Day" subtitle="Your simple plan for selling today.">
      <MyDayBody />
    </SalesShell>
  )
}

function MyDayBody() {
  const nav = useNavigate()
  const ctx = useSales()
  const [day, setDay] = useState(null)
  const [deals, setDeals] = useState(null)
  const [team, setTeam] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [d, o] = await Promise.all([
        api.get('/sales/my-day?scope=mine'),
        api.get('/sales/opportunities').catch(() => null),
      ])
      setDay(d); setDeals(o)
      // Team figures only for a manager, and only in their own labelled row.
      setTeam(d?.is_manager ? await api.get('/sales/my-day').catch(() => null) : null)
    } catch (e) {
      setError(e.message || 'Could not load your day.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  // A manager's /sales/opportunities is the whole brand. My Day is "my" work,
  // so narrow to the deals this person owns — the same rule My Pipeline uses.
  const mine = useMemo(() => {
    if (!deals) return []
    const all = (deals.stages || []).flatMap(s => s.opportunities || [])
    return deals.is_manager ? all.filter(c => c.owner_user_id === deals.viewer_user_id) : all
  }, [deals])
  const tz = ctx?.brand_sales_org?.timezone
  const due = useMemo(() => followUpList(mine, 'today', '', new Date(), tz), [mine, tz])
  const overdue = due.filter(c => c.due_bucket === 'overdue').length
  const board = useMemo(() => groupForSimpleBoard({ stages: [{ opportunities: mine }] }), [mine])

  const m = day?.metrics || {}
  const followCount = deals ? due.length : (m.follow_ups_due || 0)
  const newLeads = deals ? board.new.length : (day?.stage_counts?.prospect || 0)
  const meetingsToday = m.appointments_today || 0
  const toConfirm = day?.needs_confirmation || []
  const P = day?.proposals || {}
  const proposals = [...(P.ready_to_send || []), ...(P.follow_up_required || [])]

  async function confirm(id) {
    try {
      await api.post('/sales/appointments/' + id + '/confirmation',
                     { confirmation_status: 'confirmed', source: 'staff_manual' })
      await load()
    } catch (e) { setError(e.message || 'Could not confirm that meeting.') }
  }

  const name = firstName(ctx?.user?.full_name)
  const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many)
  const summary = loading && !day ? 'Loading your day…'
    : 'You have ' + plural(followCount, 'follow up', 'follow ups') + ' and ' +
      plural(meetingsToday, 'meeting', 'meetings') + ' on your plate today.'

  return (
    <>
      <ErrorLine error={error} onRetry={load} />

      <div className="ss-hero">
        <div className="ss-sun" aria-hidden="true">☀️</div>
        <div>
          <h2>{greeting()}{name ? ', ' + name : ''}!</h2>
          <p>{summary}</p>
        </div>
      </div>

      {day?.is_manager && (
        <div className="ss-scopehead">
          <h2>Your numbers</h2>
          <span>Only deals and meetings that are yours. Your team's totals are further down.</span>
        </div>
      )}
      <div className="ss-stats">
        <button type="button" className="ss-stat" onClick={() => nav('/sales/pipeline')}>
          <div className="ss-num">{loading && !day ? '–' : newLeads}</div>
          <div className="ss-name">New Leads</div>
        </button>
        <button type="button" className="ss-stat" onClick={() => nav('/sales/follow-ups')}>
          <div className="ss-num">{loading && !day ? '–' : followCount}</div>
          <div className="ss-name">Follow Ups</div>
          <div className={'ss-detail' + (overdue ? '' : ' ss-calm')}>{overdue} overdue</div>
        </button>
        <button type="button" className="ss-stat" onClick={() => nav('/sales/demos')}>
          <div className="ss-num">{loading && !day ? '–' : (m.demos_to_build || 0)}</div>
          <div className="ss-name">Demos to Build</div>
        </button>
        <button type="button" className="ss-stat" onClick={() => nav('/sales/appointments')}>
          <div className="ss-num">{loading && !day ? '–' : meetingsToday}</div>
          <div className="ss-name">Appointments today</div>
        </button>
      </div>

      <div className="ss-quick">
        <button type="button" className="ss-qgreen" onClick={() => nav('/sales/follow-ups')}>
          <Icon name="phone" size={29} />
          <span><strong>Start Follow Ups</strong><small>{plural(followCount, 'person', 'people')} to contact</small></span>
        </button>
        <button type="button" className="ss-qblue" onClick={() => nav('/sales/book')}>
          <Icon name="calendar" size={29} />
          <span><strong>Book a Demo</strong><small>Schedule a meeting</small></span>
        </button>
        <button type="button" className="ss-qgray" onClick={() => nav('/sales/pipeline')}>
          <Icon name="users" size={29} />
          <span><strong>View Pipeline</strong><small>See all deals</small></span>
        </button>
      </div>

      {team && (
        <section className="ss-teamrow" aria-labelledby="ss-team-h">
          <div className="ss-teamrow-h">
            <div>
              <h2 id="ss-team-h">Your team</h2>
              <span>Everyone you manage, including you</span>
            </div>
            <button type="button" className="ss-link" onClick={() => nav('/sales/manager')}>
              Open Team Command <Icon name="arrow" size={14} />
            </button>
          </div>
          <div className="ss-teamstats">
            <TeamStat n={team.metrics?.follow_ups_due} label="Follow ups due" />
            <TeamStat n={team.metrics?.needs_action} label="Deals needing action" />
            <TeamStat n={team.metrics?.demos_to_build} label="Demos to build" />
            <TeamStat n={team.metrics?.appointments_today} label="Appointments today" />
            <TeamStat n={team.metrics?.won_this_month} label="Won this month" />
          </div>
        </section>
      )}

      <div className="ss-columns">
        <div>
          <section className="ss-panel" aria-labelledby="ss-prio">
            <div className="ss-panelhd">
              <div>
                <h3 id="ss-prio">Today's Priorities</h3>
                <p>A clear plan for your day</p>
              </div>
              <button type="button" className="ss-link"
                      onClick={() => due[0] ? nav('/sales/leads/' + due[0].id) : nav('/sales/follow-ups')}>
                Start working <Icon name="arrow" size={14} />
              </button>
            </div>
            <div className="ss-panelbody">
              {loading && !day ? <Skeleton rows={3} /> : (
                <>
                  <Priority n={1} tone="" done={!followCount} onClick={() => nav('/sales/follow-ups')}
                    text={followCount
                      ? 'Call or text ' + plural(followCount, 'follow up', 'follow ups') + (overdue ? ' — ' + overdue + ' overdue first' : '')
                      : 'No follow ups due — nice work'} />
                  <Priority n={2} tone="ss-blue-step" onClick={() => nav(meetingsToday ? '/sales/appointments' : '/sales/book')}
                    text={meetingsToday
                      ? 'Be ready for ' + plural(meetingsToday, 'meeting', 'meetings') + ' today'
                      : 'Book 1–2 demos'} />
                  <Priority n={3} tone="ss-green-step" onClick={() => nav('/sales/pipeline')}
                    text={board.follow.length
                      ? 'Move interested prospects to the next stage (' + board.follow.length + ' in Follow Up)'
                      : 'Move interested prospects to the next stage'} />
                </>
              )}
            </div>
          </section>

          {(day?.todays_appointments || []).length > 0 && (
            <section className="ss-panel" aria-labelledby="ss-today">
              <div className="ss-panelhd">
                <div><h3 id="ss-today">Today's meetings</h3><p>In your team's time zone</p></div>
                <button type="button" className="ss-link" onClick={() => nav('/sales/appointments')}>
                  Open calendar <Icon name="arrow" size={14} />
                </button>
              </div>
              <div className="ss-upnext">
                {day.todays_appointments.map(a => (
                  <div key={a.id} className="ss-compact">
                    <strong>{wallTime(a.starts_at_local || a.starts_at)}</strong>
                    <span className="ss-sp">{a.title || a.meeting_type}{a.prospect_company ? ' — ' + a.prospect_company : ''}</span>
                    {a.video?.join_url && <a className="ss-btn ss-white ss-xs" href={a.video.join_url} target="_blank" rel="noreferrer">Join</a>}
                    {a.opportunity_id && <button type="button" className="ss-btn ss-white ss-xs"
                      onClick={() => nav('/sales/leads/' + a.opportunity_id)}>Open</button>}
                  </div>
                ))}
              </div>
            </section>
          )}

          {(toConfirm.length > 0 || proposals.length > 0) && (
            <section className="ss-panel" aria-labelledby="ss-waiting">
              <div className="ss-panelhd">
                <div><h3 id="ss-waiting">Also waiting on you</h3><p>Only shown when there is something to do</p></div>
              </div>
              <div className="ss-upnext">
                {toConfirm.map(a => (
                  <div key={'c' + a.id} className="ss-compact">
                    <span className="ss-status ss-amber">Confirm</span>
                    <span className="ss-sp">{a.title || a.meeting_type} · {wallTime(a.starts_at_local || a.starts_at)}
                      {a.prospect_company ? ' — ' + a.prospect_company : ''}</span>
                    <button type="button" className="ss-btn ss-white ss-xs" onClick={() => confirm(a.id)}>Mark confirmed</button>
                  </div>
                ))}
                {proposals.map(p => (
                  <div key={'p' + p.proposal_id} className="ss-compact">
                    <span className="ss-status ss-sblue">Proposal</span>
                    <span className="ss-sp"><strong>{p.company || p.proposal_number}</strong>{p.reason ? ' — ' + p.reason : ''}</span>
                    <button type="button" className="ss-btn ss-white ss-xs"
                            onClick={() => nav('/sales/opportunities/' + p.opportunity_id)}>Open deal</button>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="ss-motivate">
          <div className="ss-trophy" aria-hidden="true">🏆</div>
          {m.won_this_month
            ? <><h3>{plural(m.won_this_month, 'deal', 'deals')} won this month.</h3>
                <p>Keep going — your next one is in today's list.</p></>
            : <><h3>Small steps.<br />Big results.</h3>
                <p>Your next deal is in today's list.</p></>}
        </div>
      </div>
    </>
  )
}

function TeamStat({ n, label }) {
  return (
    <div className="ss-teamstat">
      <b>{n ?? '–'}</b>
      <span>{label}</span>
    </div>
  )
}

function Priority({ n, tone, text, onClick, done }) {
  return (
    <div className="ss-prior">
      <span className={'ss-step ' + (done ? 'ss-done' : tone)} aria-hidden="true">{done ? '✓' : n}</span>
      <span className="ss-sp">{text}</span>
      <button type="button" className="ss-link" onClick={onClick} aria-label={'Go: ' + text}>
        <Icon name="chevright" size={16} />
      </button>
    </div>
  )
}
