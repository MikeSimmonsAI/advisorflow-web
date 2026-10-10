/* Dashboard: hero, four KPIs, the priority action center, response activity,
   then the location/campaign/automation/reporting detail. Every number comes
   from GET /program/dashboard (and the chart from GET /program/responses). */
import { useEffect, useMemo, useState } from 'react'
import { api, getCurrentUser } from '../../../api/client'
import { LANDSCAPE, CLASS_LABEL, CLASS_TONE, Chip, Empty, Metric, Panel, assetSrc, num, when } from './ui'

const VERDICT_TONE = { READY: 'ok', CONDITIONAL: 'warn', BLOCKED: 'bad' }

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

export default function Dashboard({ data, goTab, setParam }) {
  const m = data.metrics
  const att = data.attention
  const sel = data.selected_location
  const rep = data.reporting
  const user = getCurrentUser() || {}
  const first = user.first_name || (user.full_name || '').split(' ')[0] || ''

  // Priority order: people waiting first, then data that blocks outreach.
  const actions = [
    { n: (att.hot_responses || 0), label: 'HOT replies needing a person', note: 'Read and respond', tone: 'bad', go: () => goTab('responses') },
    { n: att.unhandled_hot || 0, label: 'HOT replies past the response SLA', note: 'Waiting longer than the target', tone: 'bad', go: () => goTab('responses') },
    { n: att.new_sms_replies || 0, label: 'New text replies', note: 'Unread conversations', tone: 'warn', go: () => goTab('responses') },
    { n: att.new_email_replies || 0, label: 'New email replies', note: 'Unread conversations', tone: 'warn', go: () => goTab('responses') },
    { n: att.unmatched_replies || 0, label: 'Replies from unknown senders', note: 'Match to a family or route to review', tone: 'warn', go: () => goTab('responses') },
    { n: att.follow_ups_due || 0, label: 'Follow-ups due', note: 'Assign and track', tone: 'warn', go: () => goTab('responses') },
    { n: att.data_review || 0, label: 'Records with source data notes', note: 'Resolve before outreach', tone: 'warn', go: () => goTab('review', { queue: 'data_review' }) },
    { n: att.location_review || 0, label: 'Contacts without a location', note: 'Assign a cemetery; nothing is sent until then', tone: 'warn', go: () => goTab('review', { queue: 'location_review' }) },
    { n: att.duplicate_review || 0, label: 'Possible duplicates', note: 'Confirm or link', tone: '', go: () => goTab('review', { queue: 'duplicate_review' }) },
    { n: att.on_hold || 0, label: 'Records on hold', note: 'Excluded from outreach until released', tone: '', go: () => goTab('review', { queue: 'on_hold' }) },
  ]
  const open = actions.filter(a => a.n > 0)
  const rt = data.readiness_test

  return (
    <>
      <header className="sci-head">
        <div>
          <p className="sci-eyebrow">SCI / Family Service Center</p>
          <h1>{greeting()}{first ? `, ${first}` : ''}</h1>
          <p>{sel ? `Family service priorities for ${sel.official_name}.` : 'Your family service priorities across the SCI network.'}</p>
        </div>
      </header>

      <section className="sci-hero" style={{ backgroundImage: `url(${LANDSCAPE})` }} aria-label="Workspace">
        <div>
          <p className="sci-eyebrow">{data.program.hero_title || data.program.name}</p>
          <h2>Every family deserves a thoughtful response.</h2>
          <p>
            {data.program.primary_contact_name
              ? `Families hear from ${data.program.primary_contact_name}${data.program.primary_contact_title ? `, ${data.program.primary_contact_title}` : ''}, with their own cemetery's name.`
              : 'Everything important, in one calm place.'}
          </p>
        </div>
      </section>

      <section className="sci-metrics" aria-label="Key numbers">
        <Metric label="HOT responses" value={m.hot_responses} tone={m.hot_responses ? 'bad' : ''}
          note="Open replies marked HOT" onClick={() => goTab('responses')} />
        <Metric label="New responses" value={m.new_responses} tone={m.new_responses ? 'info' : ''}
          note="Replies not yet handled" onClick={() => goTab('responses')} />
        <Metric label="Appointments" value={rep?.appointments ?? 0} tone="ok"
          note="Booked or confirmed" onClick={() => { window.location.href = '/availability' }} />
        <Metric label="Mapped locations" value={m.locations}
          note={`${num(m.contacts)} contacts · ${num(m.leads)} leads`} onClick={() => goTab('locations')} />
      </section>

      <div className="sci-grid-2" style={{ marginBottom: 16 }}>
        <Panel title="Priority action center" labelledBy="sci-actions"
          aside={<Chip tone={open.length ? 'warn' : 'ok'}>{open.length ? `${open.length} open` : 'All clear'}</Chip>}>
          <div className="sci-rows">
            {open.length === 0 && <Empty title="Nothing needs you right now.">New replies and review items will appear here in order of urgency.</Empty>}
            {open.map(a => (
              <button key={a.label} type="button" className="sci-action" onClick={a.go}>
                <span className={`sci-action-num ${a.tone}`}>{num(a.n)}</span>
                <span className="sci-action-body"><b>{a.label}</b><small>{a.note}</small></span>
                <span aria-hidden="true" className="sci-muted">›</span>
              </button>
            ))}
            {rt && (
              <button type="button" className="sci-action" onClick={() => goTab('launch')}>
                <span className="sci-action-num"><Chip tone={VERDICT_TONE[rt.status] || ''} plain>{rt.status}</Chip></span>
                <span className="sci-action-body" style={{ paddingLeft: 26 }}><b>Launch readiness</b><small>{rt.reason}</small></span>
                <span aria-hidden="true" className="sci-muted">›</span>
              </button>
            )}
          </div>
        </Panel>
        <ResponseActivity locationId={sel?.location_id} />
      </div>

      <div className="sci-grid-even" style={{ marginBottom: 16 }}>
        <Panel title="Locations to watch" labelledBy="sci-watch">
          <LocationWatch rows={data.location_performance} pick={id => setParam('location', id)} />
        </Panel>
        <Panel title="Recent conversations" labelledBy="sci-recent"
          aside={<button type="button" className="sci-btn sm" onClick={() => goTab('responses')}>Open inbox</button>}>
          {data.recent_conversations.length === 0
            ? <Empty title="No replies yet.">Replies by text or email land here.</Empty>
            : (
              <div className="sci-rows">
                {data.recent_conversations.map(r => (
                  <div className="sci-kv" key={r.id}>
                    <span style={{ minWidth: 0 }}>
                      <b style={{ fontWeight: 600 }}>{r.name || 'Contact'}</b>{' '}
                      <Chip tone={CLASS_TONE[r.class]} plain>{CLASS_LABEL[r.class] || r.class}</Chip>
                      <span className="sci-micro sci-muted" style={{ display: 'block' }}>{r.channel === 'sms' ? 'Text' : 'Email'} · {r.location || 'Location review'}</span>
                    </span>
                    <span className="sci-micro">{when(r.received_at)}</span>
                  </div>
                ))}
              </div>
            )}
        </Panel>
      </div>

      <LocationPerformance rows={data.location_performance} pick={id => setParam('location', id)} />

      <div className="sci-grid-even" style={{ margin: '16px 0' }}>
        <Panel title="Campaign performance" labelledBy="sci-camp"
          aside={<button type="button" className="sci-btn sm" onClick={() => goTab('campaigns')}>Campaigns</button>}>
          {data.campaign_performance.length === 0 ? <Empty>No campaigns yet.</Empty> : (
            <div className="sci-rows">
              {data.campaign_performance.map(c => (
                <div className="sci-kv" key={c.key}>
                  <span>{c.name} {!c.is_active && <Chip plain>off</Chip>}</span>
                  <span>{num(c.leads)} leads · {num(c.responses)} replies</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
        <Automation a={data.automation} />
      </div>

      <div className="sci-grid-even" style={{ marginBottom: 16 }}>
        <Panel title="Lead pipeline" labelledBy="sci-pipe" aside={<span className="sci-micro sci-muted">{num(m.leads)} total</span>}>
          {data.pipeline.length === 0 ? <Empty>No leads staged yet.</Empty> : data.pipeline.map(p => (
            <div key={p.status} style={{ marginBottom: 12 }}>
              <div className="sci-kv" style={{ padding: '0 0 5px' }}><span>{p.status}</span><span>{num(p.count)} · {p.share}%</span></div>
              <div className="sci-progress" aria-hidden="true"><span style={{ width: `${p.share}%`, background: '#2f6a80' }} /></div>
            </div>
          ))}
        </Panel>
        <OutboundIdentity data={data} />
      </div>

      {rep && <Reporting r={rep} />}
    </>
  )
}

/** Replies per day for the last seven days, by channel, from the real queue. */
function ResponseActivity({ locationId }) {
  const [items, setItems] = useState(null)
  useEffect(() => {
    let alive = true
    const q = new URLSearchParams({ status: '' })
    if (locationId) q.set('location_id', locationId)
    api.get(`/program/responses?${q}`).then(d => { if (alive) setItems(d.items || []) }).catch(() => { if (alive) setItems([]) })
    return () => { alive = false }
  }, [locationId])
  const days = useMemo(() => {
    const out = []
    const today = new Date(); today.setHours(0, 0, 0, 0)
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today); d.setDate(today.getDate() - i)
      out.push({ key: d.toDateString(), label: d.toLocaleDateString([], { weekday: 'short' }), sms: 0, email: 0 })
    }
    ;(items || []).forEach(r => {
      const d = new Date(r.received_at); d.setHours(0, 0, 0, 0)
      const slot = out.find(x => x.key === d.toDateString())
      if (slot) slot[r.channel === 'sms' ? 'sms' : 'email'] += 1
    })
    return out
  }, [items])
  const max = Math.max(1, ...days.map(d => Math.max(d.sms, d.email)))
  const total = days.reduce((s, d) => s + d.sms + d.email, 0)
  return (
    <Panel title="Response activity" labelledBy="sci-activity" aside={<span className="sci-micro sci-muted">Last 7 days · {num(total)} replies</span>}>
      {items === null ? <p className="sci-loading">Loading…</p> : (
        <>
          <div className="sci-chart" role="img" aria-label={`Replies over the last seven days: ${days.map(d => `${d.label} ${d.sms} text, ${d.email} email`).join('; ')}`}>
            {days.map(d => (
              <div className="sci-chart-day" key={d.key}>
                <span className="sci-chart-bar" style={{ height: `${(d.sms / max) * 100}%` }} title={`${d.label}: ${d.sms} text`} />
                <span className="sci-chart-bar email" style={{ height: `${(d.email / max) * 100}%` }} title={`${d.label}: ${d.email} email`} />
              </div>
            ))}
          </div>
          <div className="sci-chart-labels" aria-hidden="true">{days.map(d => <span key={d.key}>{d.label}</span>)}</div>
          <div className="sci-legend"><span><i />Text replies</span><span><i className="email" />Email replies</span></div>
        </>
      )}
    </Panel>
  )
}

function LocationWatch({ rows, pick }) {
  const watch = (rows || [])
    .filter(r => !r.is_review_bucket && (r.hot > 0 || r.new_responses > 0))
    .sort((x, y) => (y.hot - x.hot) || (y.new_responses - x.new_responses))
    .slice(0, 5)
  if (!watch.length) return <Empty>No location has HOT or new responses right now.</Empty>
  return (
    <div className="sci-rows">
      {watch.map(r => (
        <button key={r.location_id} type="button" className="sci-action" onClick={() => pick(r.location_id)}>
          <span className={`sci-action-num ${r.hot > 0 ? 'bad' : 'warn'}`}>{num(r.hot)}</span>
          <span className="sci-action-body"><b>{r.name}</b><small>{num(r.hot)} HOT · {num(r.new_responses)} new · {num(r.leads)} leads</small></span>
          <span aria-hidden="true" className="sci-muted">›</span>
        </button>
      ))}
    </div>
  )
}

function LocationPerformance({ rows, pick }) {
  const [all, setAll] = useState(false)
  const list = rows || []
  return (
    <Panel title="Location performance" labelledBy="sci-locperf"
      aside={list.length > 10 && (
        <button type="button" className="sci-btn sm" onClick={() => setAll(v => !v)}>{all ? 'Show top 10' : `Show all ${list.length}`}</button>
      )}>
      <div className="sci-tablewrap">
        <table className="sci-table">
          <thead><tr><th>Location</th><th>Leads</th><th>Qualified</th><th>New responses</th><th>HOT</th><th>Response rate</th></tr></thead>
          <tbody>
            {(all ? list : list.slice(0, 10)).map(r => (
              <tr key={r.location_id} className="sci-click" tabIndex={0}
                onClick={() => pick(r.location_id)} onKeyDown={e => { if (e.key === 'Enter') pick(r.location_id) }}>
                <td>{r.name} {r.is_review_bucket && <Chip tone="warn" plain>review</Chip>}</td>
                <td>{num(r.leads)}</td><td>{num(r.qualified)}</td><td>{num(r.new_responses)}</td>
                <td>{r.hot ? <Chip tone="bad" plain>{r.hot}</Chip> : 0}</td>
                <td>{r.response_rate == null ? '—' : `${r.response_rate}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function Automation({ a }) {
  const on = a.active_campaign_families > 0
  return (
    <Panel title="Automation" labelledBy="sci-auto" aside={<Chip tone={on ? 'ok' : ''}>{on ? `${a.active_campaign_families} campaign(s) on` : 'Sending off'}</Chip>}>
      <div className="sci-grid-even" style={{ gap: 10 }}>
        {[['Active cadences', a.active_cadences], ['Paused by a reply', a.paused_cadences], ['Review needed', a.review_needed], ['Opted out', a.suppressed]].map(([k, v]) => (
          <div key={k} className="sci-card"><dt>{k}</dt><dd style={{ fontSize: 20, fontFamily: 'var(--serif)', fontWeight: 700 }}>{num(v)}</dd></div>
        ))}
      </div>
      <p className="sci-small sci-muted" style={{ marginBottom: 0 }}>
        {on ? `${a.active_campaign_families} campaign(s) switched on.` : 'No campaign is switched on. Nothing is being sent automatically.'}
        {a.staged_not_live > 0 && ` ${num(a.staged_not_live)} contacts are staged and not yet live.`}
      </p>
    </Panel>
  )
}

function OutboundIdentity({ data }) {
  const sel = data.selected_location
  return (
    <Panel title="Outbound identity" labelledBy="sci-id">
      {sel ? (
        <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
          <span className="sci-thumb" style={{ width: 88, height: 70, backgroundImage: `url(${sel.hero_url ? assetSrc(sel.hero_url) : LANDSCAPE})` }} aria-hidden="true" />
          <div style={{ minWidth: 0 }}>
            <b>{sel.official_name}</b>
            <div className="sci-micro sci-muted">{[sel.address?.address_line1, sel.address?.city, sel.address?.state].filter(Boolean).join(', ') || 'Address not on file yet'}</div>
            <div className="sci-small" style={{ marginTop: 6 }}>Email from: <b>{sel.email_display_name}</b></div>
            <div className="sci-small">Text sign-off: <b>{sel.sms_signoff}</b></div>
          </div>
        </div>
      ) : (
        <>
          <p className="sci-small" style={{ marginTop: 0 }}>{data.program.primary_contact_name || 'The primary contact'} is the person every family hears from; the sender name changes with each family's own location, for example:</p>
          <div className="sci-preview-email" style={{ fontWeight: 600 }}>{data.program.primary_contact_name || 'Kerry Allan'} | {data.locations.find(l => !l.is_review_bucket)?.name || 'Location'}</div>
          <p className="sci-micro sci-muted" style={{ marginBottom: 0 }}>Choose a location at the top to see its identity. Contacts with no location are never sent to.</p>
        </>
      )}
    </Panel>
  )
}

function Reporting({ r }) {
  const e = r.email
  const s = r.sms
  const rt = r.response_time_minutes
  const groups = [
    ['Email', [['Sent', e.sent], ['Delivered', e.delivered == null ? 'not tracked' : e.delivered], ['Bounced', e.bounced],
      ['Failed', e.failed], ['Opened', e.opened], ['Replies', e.replied], ['Unsubscribed', e.unsubscribed]]],
    ['Text', [['Sent', s.sent], ['Delivered', s.delivered], ['Failed', s.failed], ['Replies', s.replied]]],
    ['Outcomes', [['Appointments', r.appointments], ['Median response time', rt.median == null ? '—' : `${rt.median} min`]]],
  ]
  return (
    <Panel title="Delivery & response reporting" labelledBy="sci-rep">
      <div className="sci-grid-3">
        {groups.map(([title, cells]) => (
          <div key={title}>
            <h3 className="sci-h3">{title}</h3>
            <div className="sci-rows">
              {cells.map(([k, v]) => <div className="sci-kv" key={k}><span>{k}</span><span>{typeof v === 'number' ? num(v) : v}</span></div>)}
            </div>
          </div>
        ))}
      </div>
      <p className="sci-micro sci-muted" style={{ marginBottom: 0 }}>{e.note} Sent is never counted as delivered.</p>
    </Panel>
  )
}
