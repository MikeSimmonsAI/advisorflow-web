/* Dashboard - the command center. One compact first viewport: six KPI tiles,
   pipeline, the priority action center, outreach state, location and campaign
   performance, recent activity, then the delivery summary and surveys.
   Every number comes from GET /program/dashboard (the 7-day activity from
   GET /program/responses). Nothing is estimated: a channel the server does not
   track says "Not tracked", never 0 delivered.

   39 vs 40: the location filter counts real, mapped facilities. The
   performance table also has the "Location review" holding bucket (contacts
   with no location yet) when it has contacts - it is shown as its own labeled
   row, never as a 40th facility. */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../../../api/client'
import Surveys from './Surveys'
import {
  BarRow, CLASS_LABEL, CLASS_TONE, Chip, Empty, Icon, Kpi, MoreLink, PageHead, Panel, ago, num, pct,
} from './ui'

const VERDICT_TONE = { READY: 'ok', CONDITIONAL: 'warn', BLOCKED: 'bad' }
const PIPE_TONE = { Qualified: 'teal', Open: 'blue', Unworked: 'slate', 'Not Interested': 'gold', Disqualified: 'red' }

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

export default function Dashboard({ data, goTab, setParam }) {
  const m = data.metrics
  const att = data.attention
  const sel = data.selected_location
  const rep = data.reporting
  const auto = data.automation
  const navigate = useNavigate()
  const user = getCurrentUser() || {}
  const first = user.first_name || (user.full_name || '').split(' ')[0] || ''
  const [allActions, setAllActions] = useState(false)
  const sendingOn = auto.active_campaign_families > 0
  const reviewTotal = (att.data_review || 0) + (att.location_review || 0) + (att.duplicate_review || 0)

  // Priority order: people waiting first, then data that blocks outreach.
  const actions = [
    { n: att.survey_alerts || 0, label: 'Low survey ratings (1–3 stars)', note: 'Call the family back, then mark handled', tone: 'bad', icon: 'star', go: () => document.getElementById('sci-surveys')?.scrollIntoView({ behavior: 'smooth' }) },
    { n: att.hot_responses || 0, label: 'HOT replies needing a person', note: 'Read and respond', tone: 'bad', icon: 'bolt', go: () => goTab('responses') },
    { n: att.unhandled_hot || 0, label: 'HOT replies past the response SLA', note: 'Waiting longer than the target', tone: 'bad', icon: 'clock', go: () => goTab('responses') },
    { n: att.location_review || 0, label: 'Location assignments needed', note: 'Contacts without a location — nothing is sent until then', tone: 'bad', icon: 'pin', go: () => goTab('review', { queue: 'location_review' }) },
    { n: att.new_sms_replies || 0, label: 'New text replies', note: 'Unread conversations', tone: 'info', icon: 'sms', go: () => goTab('responses') },
    { n: att.new_email_replies || 0, label: 'New email replies', note: 'Unread conversations', tone: 'info', icon: 'mail', go: () => goTab('responses') },
    { n: att.data_review || 0, label: 'Records with source data notes', note: 'Resolve before outreach', tone: 'warn', icon: 'database', go: () => goTab('review', { queue: 'data_review' }) },
    { n: att.duplicate_review || 0, label: 'Possible duplicates', note: 'Confirm or link', tone: 'warn', icon: 'users', go: () => goTab('review', { queue: 'duplicate_review' }) },
    { n: att.unmatched_replies || 0, label: 'Replies from unknown senders', note: 'Match to a family or mark handled', tone: 'info', icon: 'inbox', go: () => goTab('responses') },
    { n: att.follow_ups_due || 0, label: 'Follow-ups due', note: 'Opened but not yet answered', tone: 'warn', icon: 'responses', go: () => goTab('responses') },
    { n: att.on_hold || 0, label: 'Records on hold', note: 'Excluded from outreach until released', tone: 'slate', icon: 'pause', go: () => goTab('review', { queue: 'on_hold' }) },
  ]
  const open = actions.filter(a => a.n > 0)
  const shown = allActions ? open : open.slice(0, 4)
  const rt = data.readiness_test
  const scopeText = sel ? sel.official_name : `all ${num(m.locations)} locations`

  return (
    <>
      <PageHead title="Command Center" sub={`${greeting()}${first ? `, ${first}` : ''}. Your family service priorities across ${scopeText}.`}>
        <Chip tone={sendingOn ? 'ok' : 'bad'}>{sendingOn ? `${auto.active_campaign_families} campaign(s) on` : 'Sending paused'}</Chip>
        {rt && <Chip tone={VERDICT_TONE[rt.status] || ''}>Launch: {rt.status}</Chip>}
        <button type="button" className="sci-btn gold" onClick={() => goTab('launch')}><Icon name="launch" />Launch Readiness</button>
      </PageHead>

      <section className="sci-kpis" aria-label="Key numbers">
        <Kpi icon="users" tone="blue" value={m.leads} label="Total leads" note={`${num(m.contacts)} distinct contacts`}
          onClick={() => goTab('review', { queue: 'all' })} title="Open every staged record" />
        <Kpi icon="check" tone="green" value={m.qualified} label="Qualified" meter={pct(m.qualified, m.leads)}
          note={`${pct(m.qualified, m.leads)}% of leads`} />
        <Kpi icon="alert" tone="gold" value={reviewTotal} label="Need review" note={att.on_hold ? `${num(att.on_hold)} on hold` : 'Location, data, duplicates'}
          onClick={() => goTab('review')} />
        <Kpi icon="calendar" tone="purple" value={rep?.appointments ?? 0} label="Appointments" note="Booked or confirmed"
          onClick={() => navigate('/availability')} title="Open appointments" />
        <Kpi icon="responses" tone="teal" value={m.new_responses} label="New replies" note={m.hot_responses ? `${num(m.hot_responses)} HOT open` : 'Not yet handled'}
          onClick={() => goTab('responses')} />
        <Kpi icon="pin" tone="green" value={m.locations} label={sel ? 'Location selected' : 'Locations'} note={sel ? sel.official_name : 'Mapped facilities'}
          onClick={() => goTab('locations')} />
      </section>

      <div className="sci-grid-dash-a">
        <Panel title="Lead pipeline" labelledBy="sci-pipe" aside={<MoreLink onClick={() => goTab('review', { queue: 'all' })}>View records</MoreLink>}>
          {data.pipeline.length === 0 ? <Empty icon="users" title="No leads staged yet.">Load a source file from Settings → Safety & Launch.</Empty> : (
            <>
              {data.pipeline.slice(0, 6).map(p => (
                <BarRow key={p.status} label={p.status} value={p.count} total={m.leads} tone={PIPE_TONE[p.status] || 'slate'} />
              ))}
              <p className="sci-footnote">{num(m.leads)} total · status as supplied in the source file</p>
            </>
          )}
        </Panel>

        <Panel title="Priority action center" labelledBy="sci-actions"
          aside={open.length > 4 ? <MoreLink onClick={() => setAllActions(v => !v)}>{allActions ? 'Show top 4' : `View all ${open.length}`}</MoreLink>
            : <Chip tone={open.length ? 'warn' : 'ok'}>{open.length ? `${open.length} open` : 'All clear'}</Chip>}>
          <div className="sci-rows">
            {open.length === 0 && <Empty icon="check" title="Nothing needs you right now.">New replies and review items appear here in order of urgency.</Empty>}
            {shown.map(a => (
              <button key={a.label} type="button" className="sci-action" onClick={a.go}>
                <span className={`sci-action-ico ${a.tone}`} aria-hidden="true"><Icon name={a.icon} /></span>
                <span className={`sci-action-num ${a.tone}`}>{num(a.n)}</span>
                <span className="sci-action-body"><b>{a.label}</b><small>{a.note}</small></span>
                <span className="sci-chev" aria-hidden="true"><Icon name="chevron" /></span>
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="Outreach & campaigns" labelledBy="sci-outreach"
          aside={<Chip tone={sendingOn ? 'ok' : 'bad'}>{sendingOn ? 'Sending on' : 'Sending paused'}</Chip>}>
          <div className="sci-rows">
            <div className="sci-action" style={{ cursor: 'default' }}>
              <span className="sci-action-ico warn" aria-hidden="true"><Icon name="campaigns" /></span>
              <span className="sci-action-num" style={{ color: 'var(--ink)' }}>{num(auto.active_campaign_families)}</span>
              <span className="sci-action-body"><b>Active campaigns</b><small>Switched on in Campaigns</small></span>
            </div>
            <div className="sci-action" style={{ cursor: 'default' }}>
              <span className="sci-action-ico info" aria-hidden="true"><Icon name="users" /></span>
              <span className="sci-action-num" style={{ color: 'var(--ink)' }}>{num(auto.staged_not_live)}</span>
              <span className="sci-action-body"><b>Contacts staged, not live</b><small>Loaded but not yet a lead</small></span>
            </div>
          </div>
          <div className={`sci-banner ${sendingOn ? 'ok' : ''}`} style={{ margin: '10px 0' }}>
            <div className="sci-banner-body sci-micro">
              {sendingOn ? `${auto.active_campaign_families} campaign(s) switched on. Messages go only to approved, enrolled contacts after the server's consent, STOP and do-not-call checks.`
                : 'No campaign is switched on. Nothing is sent automatically.'}
            </div>
          </div>
          <div className="sci-row-gap">
            <button type="button" className="sci-btn gold sm" onClick={() => goTab('launch')}><Icon name="launch" />Open readiness</button>
            <button type="button" className="sci-btn sm" onClick={() => goTab('campaigns')}>Manage campaigns</button>
          </div>
        </Panel>
      </div>

      <div className="sci-grid-dash-b">
        <LocationPerformance rows={data.location_performance} sel={sel} pick={id => setParam('location', id)} goTab={goTab} />
        <CampaignPerformance rows={data.campaign_performance} goTab={goTab} />
        <RecentActivity recent={data.recent_conversations} locationId={sel?.location_id} goTab={goTab} />
      </div>

      <div className="sci-grid-2">
        {rep ? <DeliverySummary r={rep} a={auto} goTab={goTab} /> : <Panel title="Delivery & response summary"><Empty>Not reported by the server.</Empty></Panel>}
        <Surveys locationId={sel?.location_id} navigate={navigate} compact />
      </div>
    </>
  )
}

function LocationPerformance({ rows, sel, pick, goTab }) {
  const list = rows || []
  const real = list.filter(r => !r.is_review_bucket)
  const bucket = list.find(r => r.is_review_bucket)
  return (
    <Panel title="Location performance" labelledBy="sci-locperf" aside={<MoreLink onClick={() => goTab('locations')}>View all locations</MoreLink>}>
      {real.length === 0 && !bucket ? <Empty icon="pin">No location has contacts yet.</Empty> : (
        <div className="sci-tablewrap">
          <table className="sci-table compact">
            <thead><tr><th>Location</th><th className="num">Leads</th><th className="num">Qualified</th><th className="num">Replies</th><th><span className="sci-sr">Open</span></th></tr></thead>
            <tbody>
              {real.slice(0, 5).map(r => (
                <tr key={r.location_id} className={`sci-click${sel?.location_id === r.location_id ? ' sci-sel' : ''}`} tabIndex={0}
                  onClick={() => pick(r.location_id)} onKeyDown={e => { if (e.key === 'Enter') pick(r.location_id) }}
                  title={`Show ${r.name} only`}>
                  <td className="trunc" title={r.name}>{r.name}{r.hot > 0 && <> <Chip tone="bad" plain>{r.hot} HOT</Chip></>}</td>
                  <td className="num">{num(r.leads)}</td><td className="num">{num(r.qualified)}</td>
                  <td className="num">{num(r.new_responses)}</td>
                  <td className="sci-chev"><Icon name="chevron" /></td>
                </tr>
              ))}
              {bucket && (
                <tr className="sci-click sci-bucket" tabIndex={0} onClick={() => goTab('review', { queue: 'location_review' })}
                  onKeyDown={e => { if (e.key === 'Enter') goTab('review', { queue: 'location_review' }) }}
                  title="Contacts with no location — assign one in Review">
                  <td className="trunc" title={`${bucket.name} — contacts with no location yet; not a facility`}><Chip tone="warn" plain>Review bucket</Chip> {bucket.name}</td>
                  <td className="num">{num(bucket.leads)}</td><td className="num">{num(bucket.qualified)}</td>
                  <td className="num">{num(bucket.new_responses)}</td>
                  <td className="sci-chev"><Icon name="chevron" /></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <p className="sci-footnote">Top 5 of {num(real.length)} facilities by leads. Click a row to filter every screen to it.</p>
    </Panel>
  )
}

function CampaignPerformance({ rows, goTab }) {
  const list = rows || []
  return (
    <Panel title="Campaign performance" labelledBy="sci-campperf" aside={<MoreLink onClick={() => goTab('campaigns')}>View all</MoreLink>}>
      {list.length === 0 ? <Empty icon="campaigns">No campaigns yet.</Empty> : (
        <div className="sci-tablewrap">
          <table className="sci-table compact">
            <thead><tr><th>Campaign</th><th>Status</th><th className="num">Leads</th><th className="num">Replies</th><th><span className="sci-sr">Open</span></th></tr></thead>
            <tbody>
              {list.slice(0, 6).map(c => (
                <tr key={c.key} className="sci-click" tabIndex={0} onClick={() => goTab('campaigns')}
                  onKeyDown={e => { if (e.key === 'Enter') goTab('campaigns') }}>
                  <td className="trunc" title={c.name}>{c.name}</td>
                  <td>{c.key === 'unmapped' ? <Chip plain>No campaign</Chip> : <Chip tone={c.is_active ? 'ok' : ''} plain>{c.is_active ? 'On' : 'Off'}</Chip>}</td>
                  <td className="num">{num(c.leads)}</td><td className="num">{num(c.responses)}</td>
                  <td className="sci-chev"><Icon name="chevron" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

/** Recent replies plus a seven-day count by channel, both from the real queue. */
function RecentActivity({ recent, locationId, goTab }) {
  const [items, setItems] = useState(null)
  useEffect(() => {
    let alive = true
    const q = new URLSearchParams({ status: '' })
    if (locationId) q.set('location_id', locationId)
    api.get(`/program/responses?${q}`).then(d => { if (alive) setItems(d.items || []) }).catch(() => { if (alive) setItems([]) })
    return () => { alive = false }
  }, [locationId, recent])
  const days = useMemo(() => {
    const out = []
    const today = new Date(); today.setHours(0, 0, 0, 0)
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today); d.setDate(today.getDate() - i)
      out.push({ key: d.toDateString(), label: d.toLocaleDateString([], { weekday: 'narrow' }), full: d.toLocaleDateString([], { weekday: 'long' }), sms: 0, email: 0 })
    }
    ;(items || []).forEach(r => {
      const d = new Date(r.received_at); d.setHours(0, 0, 0, 0)
      const slot = out.find(x => x.key === d.toDateString())
      if (slot) slot[r.channel === 'sms' ? 'sms' : 'email'] += 1
    })
    return out
  }, [items])
  const max = Math.max(1, ...days.map(d => d.sms + d.email))
  const total = days.reduce((s, d) => s + d.sms + d.email, 0)

  return (
    <Panel title="Recent activity" labelledBy="sci-recent" aside={<span className="sci-micro sci-muted">Last 7 days · {items === null ? '…' : `${num(total)} replies`}</span>}>
      <div className="sci-mini-chart" role="img"
        aria-label={`Replies over the last seven days: ${days.map(d => `${d.full} ${d.sms} text, ${d.email} email`).join('; ')}`}>
        {days.map(d => (
          <span key={d.key} className="sci-mini-day" title={`${d.full}: ${d.sms} text, ${d.email} email`}>
            <span className="sci-mini-stack">
              <i className="email" style={{ height: `${(d.email / max) * 100}%` }} />
              <i style={{ height: `${(d.sms / max) * 100}%` }} />
            </span>
            <small>{d.label}</small>
          </span>
        ))}
      </div>
      {(recent || []).length === 0 ? (
        <Empty icon="responses" title="No replies yet." action={<button type="button" className="sci-btn sm" onClick={() => goTab('responses')}>View all responses</button>}>
          Replies by text or email will appear here.
        </Empty>
      ) : (
        <>
          <div className="sci-rows">
            {recent.slice(0, 4).map(r => (
              <button type="button" className="sci-action" key={r.id} onClick={() => goTab('responses')}>
                <span className={`sci-action-ico ${r.class === 'hot' ? 'bad' : 'info'}`} aria-hidden="true"><Icon name={r.channel === 'sms' ? 'sms' : 'mail'} /></span>
                <span className="sci-action-body">
                  <b>{r.name || 'Contact'} <Chip tone={CLASS_TONE[r.class]} plain>{CLASS_LABEL[r.class] || r.class}</Chip></b>
                  <small>{r.location || 'Location review'} · {ago(r.received_at)}</small>
                </span>
              </button>
            ))}
          </div>
          <div style={{ marginTop: 8 }}><MoreLink onClick={() => goTab('responses')}>View all responses</MoreLink></div>
        </>
      )}
    </Panel>
  )
}

function DeliverySummary({ r, a, goTab }) {
  const e = r.email
  const s = r.sms
  const med = r.response_time_minutes?.median
  const groups = [
    { title: 'Email', icon: 'mail', tone: 'gold', cells: [['Sent', e.sent], ['Delivered', e.delivered], ['Opens', e.opened], ['Replies', e.replied], ['Bounced', e.bounced]] },
    { title: 'Text / SMS', icon: 'sms', tone: 'teal', cells: [['Sent', s.sent], ['Delivered', s.delivered], ['Replies', s.replied], ['Failed', s.failed]] },
    { title: 'Automation', icon: 'bolt', tone: 'blue', cells: [['Active cadences', a.active_cadences], ['Paused by reply', a.paused_cadences], ['Review needed', a.review_needed], ['Opted out', a.suppressed]] },
  ]
  return (
    <Panel title="Delivery & response summary" labelledBy="sci-rep" aside={<MoreLink onClick={() => goTab('health')}>Diagnostics</MoreLink>}>
      <div className="sci-grid-3" style={{ gap: 12 }}>
        {groups.map(g => (
          <div key={g.title} className="sci-sumgroup">
            <div className="sci-sumhead"><span className={`sci-kpi-icon ${g.tone}`} style={{ width: 28, height: 28 }} aria-hidden="true"><Icon name={g.icon} /></span>{g.title}</div>
            <div className="sci-statstrip">
              {g.cells.map(([k, v]) => (
                <div className="sci-stat" key={k}>
                  {v == null ? <b className="nt" title="This channel's delivery is not reported back to us">Not tracked</b> : <b>{num(v)}</b>}
                  <span>{k}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="sci-footnote"><Icon name="info" />{e.note ? `${e.note} ` : ''}Sent is never counted as delivered. Median response time: {med == null ? 'no replies handled yet' : `${med} min`}.</p>
    </Panel>
  )
}
