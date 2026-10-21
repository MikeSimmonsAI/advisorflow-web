/**
 * Public demo screens: Mission Control, AI Work Queue, Performance,
 * Pipeline, Leads & Data and a Roofing Setup preview. Same markup and CSS
 * as the real Roofing screens; data from ./fixtures.js only.
 */
import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Avatar, Empty, Icon, Panel, Pill, n, pct } from '../ui'
import {
  COMPANY, DEMO_BASE, HOMEOWNERS, OUTCOMES, PIPELINE_TOTALS, PLAYBOOK, STAGES, TEAM, TOUCHES, TREND,
  agoText, byId, fmtWhen, inspectionsFor, pctOf,
} from './fixtures'
import { BOOK_DEMO, usePreview } from './demoCtx'

const LANE_ICON = { human_needed: 'hand', requested: 'star', booked: 'calendar', replied: 'chat', working: 'spark' }
const LANES = [
  { key: 'human_needed', label: 'Needs a person', hint: 'The AI stopped and handed these to your team - a question it should not answer alone.' },
  { key: 'requested', label: 'Inspection requested', hint: 'Said yes to an inspection. Confirm a time.' },
  { key: 'booked', label: 'Inspection booked', hint: 'On the calendar. Open the inspection when you arrive.' },
  { key: 'replied', label: 'Replied', hint: 'Answered the follow-up. The AI keeps the conversation going.' },
  { key: 'working', label: 'AI working', hint: 'In the follow-up sequence. Nobody has replied yet.' },
]
const LANE_TOTAL = { human_needed: 1, requested: 1, booked: 9, replied: 2, working: 486 }
const BUCKET_LABEL = { needs_human: 'Needs you', ready_to_book: 'Ready to book', new_reply: 'New reply' }

export function SampleNote({ children }) {
  return <p className="dm-note"><span className="rf-sample">Sample data</span>{children || 'Illustrative numbers for a fictional roofing company - not real results.'}</p>
}

function Roofline() {
  const rows = Array.from({ length: 9 }, (_, i) => i)
  return (
    <svg className="mc-roofline" viewBox="0 0 520 260" preserveAspectRatio="xMaxYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id="dm-roof-fade" x1="0" x2="1">
          <stop offset="0" stopColor="#12284a" stopOpacity="0" />
          <stop offset=".45" stopColor="#12284a" stopOpacity=".9" />
          <stop offset="1" stopColor="#1c3760" />
        </linearGradient>
        <pattern id="dm-shingle" width="34" height="18" patternUnits="userSpaceOnUse">
          <path d="M0 18V2h34v16" fill="none" stroke="#27477a" strokeWidth="1.4" />
          <path d="M17 2v16" stroke="#27477a" strokeWidth="1.2" />
        </pattern>
        <clipPath id="dm-roof-clip"><path d="M60 260 300 40 560 260z" /></clipPath>
      </defs>
      <rect width="520" height="260" fill="url(#dm-roof-fade)" />
      <g clipPath="url(#dm-roof-clip)">
        <rect width="560" height="260" fill="#16305a" />
        <rect width="560" height="260" fill="url(#dm-shingle)" />
        {rows.map((i) => <path key={i} d={`M0 ${70 + i * 22}H560`} stroke="#0f2340" strokeOpacity=".35" />)}
      </g>
      <path d="M60 260 300 40 560 260" fill="none" stroke="#f26a1b" strokeWidth="5" strokeLinejoin="round" />
      <path d="M150 260 300 122 470 260" fill="none" stroke="#f26a1b" strokeOpacity=".35" strokeWidth="3" />
      <rect x="380" y="92" width="34" height="58" fill="#0f2340" />
      <rect x="375" y="86" width="44" height="10" rx="2" fill="#1c3760" />
    </svg>
  )
}

// ── Mission Control ───────────────────────────────────────────────────────
function LeadCard({ h, lane }) {
  const nav = useNavigate()
  const preview = usePreview()
  return (
    <article className="mc-card">
      <div className="mc-card__top">
        <Avatar name={h.name} size={38} />
        <div className="mc-card__who"><b>{h.name}</b><span>{h.roof_issue}</span></div>
        <span className="mc-card__touch" title="Messages sent to this homeowner">Touch {h.touch || 0}</span>
      </div>
      <p className="mc-card__sum">{h.summary}</p>
      <div className="mc-card__foot">
        <span className="mc-card__next">
          <Icon name={lane === 'booked' ? 'calendar' : 'clock'} size={15} />
          {lane === 'booked' && h.inspection_at ? `Inspection ${fmtWhen(h.inspection_at)}`
            : lane === 'working' && h.next_touch_at ? `Next touch ${fmtWhen(h.next_touch_at)}`
            : lane === 'human_needed' ? h.reason : lane === 'requested' ? 'Offer or confirm a time' : `Last activity ${agoText(h.last)}`}
        </span>
        <span className="mc-card__btns">
          {lane === 'requested' && <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => preview('Book the inspection', `In a live workspace this opens the booking screen with ${h.first}'s salesperson's real open times. Booking puts it on the calendar and (only if you choose) texts a confirmation.`)}>Book inspection</button>}
          {lane === 'human_needed' && <button className="rf-btn rf-btn--navy rf-btn--sm" onClick={() => preview('Take over the conversation', `Taking over pauses the AI and the follow-up for ${h.first}, so a person answers from here. Hand it back to the AI with one click.`)}>Take over</button>}
          {lane === 'booked' && inspectionsFor(h.id)[0]
            ? <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => nav(`${DEMO_BASE}/inspections/${inspectionsFor(h.id)[0].id}`)}>Open inspection</button>
            : <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => nav(`${DEMO_BASE}/conversations?lead=${h.id}`)}>Open conversation</button>}
        </span>
      </div>
    </article>
  )
}

export function DemoMission() {
  const o = OUTCOMES
  const [lane, setLane] = useState('human_needed')
  const preview = usePreview()
  const nav = useNavigate()
  const cur = LANES.find((l) => l.key === lane)
  const cards = HOMEOWNERS.filter((h) => h.lane === lane)
  const attention = HOMEOWNERS.filter((h) => h.bucket).sort((a, b) => b.last - a.last)
  const funnel = [['Imported', o.imported], ['Contacted', o.contacted], ['Engaged', o.engaged], ['Inspection requested', o.offered], ['Inspection booked', o.confirmed]]
  const tmax = Math.max(...TOUCHES.map((t) => pctOf(t.replied, t.received)))
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
  const ribbon = [
    { label: 'Homeowners contacted', big: n(o.contacted), small: `of ${n(o.imported)} leads` },
    { label: 'Responded', big: pct(pctOf(o.responded, o.contacted)), small: `${n(o.responded)} of ${n(o.contacted)} contacted` },
    { label: 'Engaged', big: pct(pctOf(o.engaged, o.contacted)), small: `${n(o.engaged)} of ${n(o.contacted)} contacted` },
    { label: 'Inspections confirmed', big: n(o.confirmed), small: `${pct(pctOf(o.confirmed, o.engaged))} of ${n(o.engaged)} engaged`, accent: true, to: `${DEMO_BASE}/inspections` },
    { label: 'Reports completed', big: n(o.reports), small: `of ${n(o.confirmed)} inspections`, to: `${DEMO_BASE}/inspections?tab=completed` },
  ]
  return (
    <main className="rf-page mc">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Mission Control</h1>
          <p className="rf-lede">{today}. {COMPANY.name} - a sample roofing company.</p>
        </div>
        <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${DEMO_BASE}/tour`}><Icon name="play" size={16} />See how it works</Link>
      </div>
      <section className="dm-start rf-panel">
        <div><b>New here? Follow one homeowner from lead to won job.</b><span>9 short steps. Or click anything on this page - it all opens.</span></div>
        <Link className="rf-btn rf-btn--primary rf-btn--sm" to={`${DEMO_BASE}/tour`}>Start here</Link>
      </section>
      <section className="mc-hero">
        <Roofline />
        <div className="mc-hero__copy">
          <p className="mc-hero__state"><span className="mc-pulse on" />AI follow-up is running <span className="rf-sample">Sample data</span></p>
          <h1 className="mc-hero__title">Your AI team is working your roofing leads.</h1>
          <p className="mc-hero__live">
            <b>{n(o.in_follow_up)}</b> homeowners in follow-up. <b>{n(o.sent_today)}</b> messages sent today, <b>{n(o.due_24h)}</b> due in the next 24 hours.
          </p>
          <div className="mc-hero__actions">
            <Link className="rf-btn rf-btn--primary" to={`${DEMO_BASE}/leads`}><Icon name="upload" size={18} />Import my leads</Link>
            <Link className="rf-btn rf-btn--on-dark" to={`${DEMO_BASE}/conversations`}><Icon name="star" size={18} />Review hot opportunities</Link>
          </div>
        </div>
        <dl className="mc-today" aria-label="Today">
          <div><dt>Replies today</dt><dd>{n(o.replies_today)}</dd></div>
          <div><dt>Inspections booked today</dt><dd>{n(o.booked_today)}</dd></div>
          <div><dt>Waiting on you</dt><dd className="hot">{n(o.attention)}</dd></div>
        </dl>
      </section>

      <section className="mc-ribbon rf-panel" aria-label="What the work is producing">
        {ribbon.map((s) => {
          const inner = (<><span className="mc-ribbon__label">{s.label}</span><span className="mc-ribbon__big">{s.big}</span><span className="mc-ribbon__small">{s.small}</span></>)
          return s.to
            ? <Link key={s.label} to={s.to} className={`mc-ribbon__seg dm-link ${s.accent ? 'accent' : ''}`}>{inner}</Link>
            : <div key={s.label} className={`mc-ribbon__seg ${s.accent ? 'accent' : ''}`}>{inner}</div>
        })}
        <div className="mc-ribbon__money">
          <span>{n(o.estimates)} estimates recorded · open estimates worth <b>${n(o.estimate_value_open)}</b></span>
          <span>{n(o.won)} jobs won, <b>${n(o.won_value)}</b> · {n(o.lost)} lost</span>
          <span className="rf-muted">Sample figures for a fictional company</span>
        </div>
      </section>

      <div className="mc-floor">
        <section className="rf-panel mc-board" aria-labelledby="dm-board-title">
          <header className="rf-panel__head">
            <div>
              <h2 className="rf-panel__title" id="dm-board-title">AI workboard</h2>
              <p className="rf-panel__sub">Every homeowner the AI is working, by what happens next.</p>
            </div>
            <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${DEMO_BASE}/queue`}>Open work queue</Link>
          </header>
          <div className="mc-lanes" role="tablist" aria-label="Workboard lanes">
            {LANES.map((l) => (
              <button key={l.key} role="tab" aria-selected={l.key === lane} className={`mc-lane mc-lane--${l.key}`} onClick={() => setLane(l.key)}>
                <Icon name={LANE_ICON[l.key]} size={17} />
                <span className="mc-lane__label">{l.label}</span>
                <span className="mc-lane__n">{LANE_TOTAL[l.key]}</span>
              </button>
            ))}
          </div>
          <p className="mc-lane__hint">{cur.hint}</p>
          <div className="mc-cards">
            {cards.map((h) => <LeadCard key={h.id} h={h} lane={lane} />)}
            {LANE_TOTAL[lane] > cards.length && <Link className="mc-more" to={`${DEMO_BASE}/queue?lane=${lane}`}>See {lane === 'working' ? 'more' : `all ${LANE_TOTAL[lane]}`} in the work queue</Link>}
          </div>
        </section>

        <section className="rf-panel mc-attn" aria-labelledby="dm-attn-title">
          <header className="rf-panel__head">
            <div>
              <h2 className="rf-panel__title" id="dm-attn-title">Needs your attention <span className="mc-attn__n">{attention.length}</span></h2>
              <p className="rf-panel__sub">Newest replies first. Questions and hand-offs on top.</p>
            </div>
          </header>
          <ul className="mc-attn__list">
            {attention.map((h) => (
              <li key={h.id} className={`mc-attn__item mc-attn__item--${h.bucket}`}>
                <div className="mc-attn__row">
                  <Avatar name={h.name} size={34} />
                  <div className="mc-attn__who"><b>{h.name}</b><span>{agoText(h.last)}</span></div>
                  <Pill tone={h.bucket}>{BUCKET_LABEL[h.bucket]}</Pill>
                </div>
                <p className="mc-attn__quote">{h.reason}</p>
                <div className="mc-attn__btns">
                  <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => nav(`${DEMO_BASE}/conversations?lead=${h.id}`)}>Open conversation</button>
                  {h.bucket === 'ready_to_book' && <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => preview('Book the inspection', `In a live workspace this opens ${h.first}'s booking screen with real open times from the salesperson's calendar.`)}>Book inspection</button>}
                  {h.bucket === 'needs_human' && <button className="rf-btn rf-btn--navy rf-btn--sm" onClick={() => preview('Take over the conversation', `The AI and follow-up pause for ${h.first}; your team answers personally.`)}>Take over</button>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="rf-panel mc-snap" aria-labelledby="dm-snap-title">
        <header className="rf-panel__head">
          <div>
            <h2 className="rf-panel__title" id="dm-snap-title">Last 30 days</h2>
            <p className="rf-panel__sub">Sample company: {n(o.imported)} homeowners imported.</p>
          </div>
          <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${DEMO_BASE}/performance`}>Full performance</Link>
        </header>
        <div className="mc-snap__grid">
          <div>
            <h3 className="mc-snap__h">From imported to booked</h3>
            <ol className="mc-funnel">
              {funnel.map(([label, count]) => (
                <li key={label}>
                  <span className="mc-funnel__label">{label}</span>
                  <span className="mc-funnel__bar"><i style={{ width: `${Math.max(2, (count / o.imported) * 100)}%` }} /></span>
                  <span className="mc-funnel__n">{n(count)}</span>
                  <span className="mc-funnel__p">{pct(pctOf(count, o.imported))}</span>
                </li>
              ))}
            </ol>
            <p className="mc-snap__foot">Percent of homeowners imported.</p>
          </div>
          <div>
            <h3 className="mc-snap__h">Which touch gets replies</h3>
            <div className="mc-touch">
              {TOUCHES.map((t) => {
                const r = pctOf(t.replied, t.received)
                return (
                  <div key={t.touch} className={`mc-touch__col ${t.touch === 1 ? 'best' : ''}`}>
                    <span className="mc-touch__v">{pct(r)}</span>
                    <span className="mc-touch__bar"><i style={{ height: `${Math.max(4, (r / tmax) * 100)}%` }} /></span>
                    <span className="mc-touch__l">Touch {t.touch}</span>
                    <span className="mc-touch__d">{n(t.replied)} of {n(t.received)}</span>
                  </div>
                )
              })}
            </div>
            <p className="mc-snap__foot">Touch 1 has the best reply rate: {pct(pctOf(TOUCHES[0].replied, TOUCHES[0].received))} of homeowners who received it replied.</p>
          </div>
        </div>
      </section>
      <SampleNote />
    </main>
  )
}

// ── AI Work Queue ─────────────────────────────────────────────────────────
export function DemoQueue() {
  const [sp, setSp] = useSearchParams()
  const preview = usePreview()
  const lane = sp.get('lane') || 'working'
  const lanes = [...LANES, { key: 'eligible', label: 'Ready, not started' }]
  const rows = HOMEOWNERS.filter((h) => h.lane === lane)
  return (
    <main className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">AI Work Queue</h1>
          <p className="rf-lede">Who the AI is working, what it has sent, and what happens next.</p>
        </div>
        <span className="rf-sample">Sample data</span>
      </div>
      <div className="rf-stack">
        <section className="rf-panel">
          <div className="wq-play">
            <div>
              <p className="wq-play__name">{PLAYBOOK.name}<Pill tone="green">Running</Pill></p>
              <p className="wq-play__goal">{PLAYBOOK.goal}. Sent to: {PLAYBOOK.audience.toLowerCase()}.</p>
              <ol className="wq-steps" aria-label="Touches in this playbook">
                {PLAYBOOK.touches.map((t) => <li key={t.touch} title={t.text}>Touch {t.touch}: day {t.day}, {t.channel}</li>)}
              </ol>
              <p className="rf-muted" style={{ fontSize: 13, marginTop: 10 }}>{PLAYBOOK.stop_rules.join('. ')}.</p>
            </div>
            <div>
              <dl className="wq-stats">
                <div><dt>In follow-up</dt><dd>{n(OUTCOMES.in_follow_up)}</dd></div>
                <div><dt>Engaged</dt><dd>{n(OUTCOMES.engaged)}</dd></div>
                <div><dt>Booked</dt><dd>{n(OUTCOMES.confirmed)}</dd></div>
                <div><dt>Opted out</dt><dd>{n(OUTCOMES.opted_out)}</dd></div>
              </dl>
              <button className="rf-btn rf-btn--primary" style={{ marginTop: 14, width: '100%' }}
                onClick={() => preview('Start follow-up', 'In a live workspace this enrolls every homeowner who passes the contact checks (consent, valid number, not opted out) into the playbook. Nothing is sent from this demo.')}>
                <Icon name="play" size={16} />Start follow-up for ready homeowners
              </button>
              <Link className="mc-more" to={`${DEMO_BASE}/setup`}>Playbook details</Link>
            </div>
          </div>
        </section>
        <section className="rf-panel">
          <div className="rf-tabs" role="tablist" aria-label="Lanes" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
            {lanes.map((l) => (
              <button key={l.key} className="rf-tab" role="tab" aria-selected={lane === l.key} onClick={() => setSp({ lane: l.key })}>
                {l.label} <span className="rf-tab__n">{l.key === 'eligible' ? 1 : LANE_TOTAL[l.key]}</span>
              </button>
            ))}
          </div>
          {rows.length === 0 ? <Empty icon="inbox" title="No sample homeowners in this lane" /> : rows.map((h) => (
            <div className="wq-row" key={h.id}>
              <div className="rf-cell-person">
                <Avatar name={h.name} size={36} />
                <div><b>{h.name}</b><span>{h.roof_issue} · {h.source}</span></div>
              </div>
              <div>
                <div className="wq-touch">{Array.from({ length: 5 }, (_, i) => <i key={i} className={i < (h.touch || 0) ? 'on' : ''} />)}</div>
                <span className="rf-muted" style={{ fontSize: 12 }}>Touch {h.touch || 0} of 5</span>
              </div>
              <div className="wq-row__sum"><b className="dm-pri">{h.urgency === 'high' ? 'High priority' : h.urgency === 'low' ? 'Low priority' : 'Normal priority'}</b> {h.summary}</div>
              <div className="wq-row__when">{h.next}</div>
              <div className="wq-row__btns">
                <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${DEMO_BASE}/homeowners/${h.id}`}>Open</Link>
              </div>
            </div>
          ))}
          {LANE_TOTAL[lane] > rows.length && <p className="rf-muted" style={{ fontSize: 13, marginTop: 10 }}>Showing a few sample homeowners of {n(LANE_TOTAL[lane])} in this lane.</p>}
        </section>
      </div>
    </main>
  )
}

// ── Performance ───────────────────────────────────────────────────────────
const SERIES = [
  { key: 'sent', label: 'Texts & emails sent', color: '#2f6fed' },
  { key: 'replies', label: 'Replies', color: '#12925e' },
  { key: 'booked', label: 'Inspections booked', color: '#f26a1b' },
]
export function DemoPerformance() {
  const o = OUTCOMES
  const kpis = [
    { label: 'Homeowners imported', value: o.imported, hint: 'In the last 30 days' },
    { label: 'Contacted', value: o.contacted, den: o.imported, dl: 'imported' },
    { label: 'Responded', value: o.responded, den: o.contacted, dl: 'contacted' },
    { label: 'Inspections booked', value: o.confirmed, den: o.engaged, dl: 'engaged' },
    { label: 'Estimates recorded', value: o.estimates, den: o.reports, dl: 'reports' },
    { label: 'Jobs won', value: o.won, den: o.won + o.lost, dl: 'decided estimates' },
  ]
  const funnel = [['Imported', o.imported], ['Contacted', o.contacted], ['Responded', o.responded], ['Engaged', o.engaged],
    ['Inspection requested', o.offered], ['Inspection booked', o.confirmed], ['Report completed', o.reports], ['Estimate recorded', o.estimates], ['Won', o.won]]
  const COLORS = ['#2f6fed', '#3d7cf0', '#4b89f3', '#12925e', '#f26a1b', '#d9580c', '#6d4fd8', '#5a3fc0', '#12925e']
  const W = 900, Hh = 220, L = 34, R = 10, T = 12, B = 26
  const max = Math.max(...TREND.flatMap((p) => SERIES.map((s) => p[s.key])))
  const x = (i) => L + (i * (W - L - R)) / (TREND.length - 1)
  const y = (v) => T + (Hh - T - B) * (1 - v / max)
  const sentTotal = TOUCHES.reduce((a, t) => a + t.received, 0)
  return (
    <main className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Performance</h1>
          <p className="rf-lede">How existing leads turn into conversations, booked inspections and won jobs.</p>
        </div>
        <div className="rf-tabs" role="tablist" aria-label="Date range"><button className="rf-tab" role="tab" aria-selected>30 days</button></div>
      </div>
      <div className="rf-stack">
        <p className="rf-pop"><span className="rf-sample">Sample data</span><span>{COMPANY.name} (fictional), last 30 days. Every percentage shows what it is measured against.</span></p>
        <Panel pad={false}>
          <div className="rf-kpis">
            {kpis.map((k) => (
              <div className="rf-kpi" key={k.label}>
                <div className="rf-kpi__label">{k.label}</div>
                <div className="rf-kpi__value">{n(k.value)}</div>
                {k.den ? <div className="rf-kpi__pct"><b>{pct(pctOf(k.value, k.den))}</b><br />{n(k.value)} of {n(k.den)} {k.dl}</div> : <div className="rf-kpi__pct">{k.hint}</div>}
              </div>
            ))}
          </div>
        </Panel>
        <div className="rf-grid-2">
          <Panel title="Lead conversion funnel" sub="Homeowners who reached each stage. Bars are shares of everyone imported.">
            <div className="rf-funnel">
              {funnel.map(([label, count], i) => (
                <div className="rf-funnel__row" key={label}>
                  <span className="rf-funnel__name">{label}</span>
                  <div className="rf-funnel__track" aria-hidden="true">
                    <div className="rf-funnel__bar" style={{ width: `${(count / o.imported) * 100}%`, background: COLORS[i] }}>{n(count)}</div>
                  </div>
                  <span className="rf-funnel__nums"><b>{pct(pctOf(count, o.imported))}</b> of imported
                    {i > 0 && <><br />{pct(pctOf(count, funnel[i - 1][1]))} of previous step</>}</span>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="Response by touch" sub="A reply is credited to the most recent text or email before it.">
            <div className="rf-touches">
              {TOUCHES.map((t) => {
                const r = pctOf(t.replied, t.received)
                return (
                  <div key={t.touch} className={`rf-touch ${t.touch === 1 ? 'rf-touch--best' : ''}`}>
                    <div className="rf-touch__name">Touch {t.touch}</div>
                    <div className="rf-touch__rate">{pct(r)}</div>
                    <div className="rf-touch__sub">{n(t.replied)} of {n(t.received)} replied</div>
                    <div className="rf-touch__sub">{pct(pctOf(t.opted_out, t.received))} opted out ({n(t.opted_out)})</div>
                    <div className="rf-touch__bar" aria-hidden="true"><span style={{ width: `${r}%` }} /></div>
                  </div>
                )
              })}
            </div>
            <p className="rf-callout">Best performer: <b>touch 1</b> - {pct(pctOf(TOUCHES[0].replied, TOUCHES[0].received))} of the {n(TOUCHES[0].received)} homeowners who received it replied.</p>
          </Panel>
        </div>
        <Panel title="Engagement by day" sub={`${n(sentTotal)} messages, ${n(o.responded)} replies and ${n(o.confirmed)} inspections booked over 30 days.`}
          actions={<div className="rf-legend">{SERIES.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}</div>}>
          <svg className="rf-chart" viewBox={`0 0 ${W} ${Hh}`} role="img" aria-label="Messages sent, replies and inspections booked per day">
            {[0, Math.round(max / 2), max].map((t) => (
              <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#e1e7ef" /><text x={L - 6} y={y(t) + 4} textAnchor="end">{t}</text></g>
            ))}
            {TREND.map((p, i) => (i % 4 === 0 ? <text key={p.period} x={x(i)} y={Hh - 6} textAnchor="middle">{new Date(p.period + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</text> : null))}
            {SERIES.map((s) => (
              <polyline key={s.key} fill="none" stroke={s.color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round"
                points={TREND.map((p, i) => `${x(i)},${y(p[s.key])}`).join(' ')} />
            ))}
          </svg>
        </Panel>
        <div className="rf-grid-2">
          <Panel title="Inspection booking conversion" sub="From an engaged homeowner to an inspection on the calendar.">
            <div className="rf-steps">
              <div className="rf-step"><span className="rf-step__label">Asked for an inspection<span>{n(o.offered)} of {n(o.engaged)} engaged homeowners</span></span><span className="rf-step__val">{pct(pctOf(o.offered, o.engaged))}</span></div>
              <div className="rf-step"><span className="rf-step__label">Booked<span>{n(o.confirmed)} of {n(o.offered)} who asked</span></span><span className="rf-step__val">{pct(pctOf(o.confirmed, o.offered))}</span></div>
              <div className="rf-step"><span className="rf-step__label">Engaged to booked<span>{n(o.confirmed)} of {n(o.engaged)} engaged homeowners</span></span><span className="rf-step__val">{pct(pctOf(o.confirmed, o.engaged))}</span></div>
            </div>
          </Panel>
          <Panel title="From inspection to won job" sub="What happened after the roof was documented.">
            <div className="rf-steps">
              <div className="rf-step"><span className="rf-step__label">Reports completed<span>{n(o.reports)} of {n(o.confirmed)} inspections booked</span></span><span className="rf-step__val">{pct(pctOf(o.reports, o.confirmed))}</span></div>
              <div className="rf-step"><span className="rf-step__label">Estimates recorded<span>{n(o.estimates)} of {n(o.reports)} reports</span></span><span className="rf-step__val">{pct(pctOf(o.estimates, o.reports))}</span></div>
              <div className="rf-step"><span className="rf-step__label">Won<span>{n(o.won)} of {n(o.won + o.lost)} decided, {n(o.estimates - o.won - o.lost)} still open</span></span><span className="rf-step__val">{pct(pctOf(o.won, o.won + o.lost))}</span></div>
              <div className="rf-step"><span className="rf-step__label">Average touches<span>Per contacted homeowner</span></span><span className="rf-step__val">{(sentTotal / o.contacted).toFixed(1)}</span></div>
            </div>
          </Panel>
        </div>
        <SampleNote />
      </div>
    </main>
  )
}

// ── Pipeline ──────────────────────────────────────────────────────────────
export function DemoPipeline() {
  const nav = useNavigate()
  const total = Object.values(PIPELINE_TOTALS).reduce((a, b) => a + b, 0)
  const cols = STAGES.map((s) => ({ ...s, count: PIPELINE_TOTALS[s.key], cards: HOMEOWNERS.filter((h) => h.stage === s.key) }))
  const open = (h) => { const ins = inspectionsFor(h.id).find((i) => i.status === 'completed'); nav(ins ? `${DEMO_BASE}/homeowners/${h.id}` : `${DEMO_BASE}/homeowners/${h.id}`) }
  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Roofing sales pipeline</h1>
          <p className="rf-lede">Every homeowner from import to a closed job. Replies and bookings move cards forward automatically; your team moves them after the inspection.</p>
        </div>
      </div>
      <div className="rf-stack">
        <div className="rf-totals">
          <span className="rf-sample">Sample data</span>
          <span><b>{n(total)}</b> homeowners</span>
          <span><b>${n(OUTCOMES.estimate_value_open)}</b> in open estimates</span>
          <span><b>${n(OUTCOMES.won_value)}</b> won</span>
        </div>
        <div className="rf-board" role="list" aria-label="Pipeline stages">
          {cols.map((c) => (
            <section className="rf-col" key={c.key} role="listitem" aria-label={c.label}>
              <header className="rf-col__head" style={{ borderTopColor: c.color }}>
                <h3>{c.label}</h3>
                <div className="rf-col__count">{n(c.count)}</div>
                <div className="rf-col__hint">{pct(pctOf(c.count, total))} of {n(total)} homeowners</div>
              </header>
              <div className="rf-col__cards">
                {c.cards.map((h) => {
                  const ins = inspectionsFor(h.id).find((i) => i.status === 'completed')
                  return (
                    <article className="rf-card dm-link" key={h.id} tabIndex={0} onClick={() => open(h)} onKeyDown={(e) => { if (e.key === 'Enter') open(h) }}>
                      <div className="rf-card__top"><Avatar name={h.name} size={30} /><div style={{ minWidth: 0 }}><b>{h.name}</b></div></div>
                      <div className="rf-card__meta">
                        <div>{h.address}</div>
                        <div>{h.roof_issue}</div>
                        <div>{h.owner}</div>
                        {h.inspection_at && <div><b>Inspection {fmtWhen(h.inspection_at)}</b></div>}
                        {ins?.estimate ? <div>Estimate ${n(ins.estimate)}</div> : null}
                        {ins?.won ? <div>Won ${n(ins.won)}</div> : null}
                        {h.lost_reason && <div>Lost: {h.lost_reason}</div>}
                        {ins && <div>Report {ins.report}</div>}
                      </div>
                      <div className="rf-card__next">{h.next}</div>
                    </article>
                  )
                })}
                {c.count > c.cards.length && <p className="rf-muted" style={{ fontSize: 12.5, padding: '0 4px' }}>Showing {c.cards.length} sample of {n(c.count)}.</p>}
              </div>
            </section>
          ))}
        </div>
        <SampleNote>The board shows where homeowners are right now. Click a card to open the homeowner.</SampleNote>
      </div>
    </div>
  )
}

// ── Leads & Data ──────────────────────────────────────────────────────────
const SOURCES = ['Phone call', 'Website', 'Referral', 'Door visit', 'Past estimate', 'Other']
export function DemoLeads() {
  const preview = usePreview()
  const [adding, setAdding] = useState(false)
  const [f, setF] = useState({ first: '', last: '', phone: '', street: '', city: '', zip: '', issue: '', source: 'Phone call' })
  const [added, setAdded] = useState([])          // this visitor's preview only - never sent anywhere
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const save = (e) => {
    e.preventDefault()
    if (!f.first.trim()) return
    setAdded([{ ...f, at: new Date() }, ...added]); setAdding(false)
    setF({ first: '', last: '', phone: '', street: '', city: '', zip: '', issue: '', source: 'Phone call' })
  }
  const rows = useMemo(() => HOMEOWNERS, [])
  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Leads &amp; data</h1>
          <p className="rf-lede">Add a homeowner by hand, or upload the leads you already have. Follow-up only starts when you start it.</p>
        </div>
        <div className="rf-head-btns">
          <button className="rf-btn rf-btn--primary rf-add__cta" onClick={() => setAdding(true)}><Icon name="plus" size={18} />Add Homeowner</button>
        </div>
      </div>
      <div className="rf-stack">
        {adding && (
          <section className="rf-panel rf-add" aria-label="Add a homeowner (demo)">
            <header className="rf-add__head">
              <div><h2 className="rf-panel__title">Add a homeowner <span className="dm-sim">Simulated</span></h2>
                <p className="rf-panel__sub">Try it - what you type stays in this browser tab and is never saved or sent.</p></div>
              <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => setAdding(false)} aria-label="Close"><Icon name="close" size={16} /></button>
            </header>
            <form className="rf-add__form" onSubmit={save}>
              <div className="rf-add__row">
                <label className="rf-label">First name *<input className="rf-input" value={f.first} onChange={set('first')} autoFocus /></label>
                <label className="rf-label">Last name<input className="rf-input" value={f.last} onChange={set('last')} /></label>
              </div>
              <div className="rf-add__row">
                <label className="rf-label">Phone<input className="rf-input" value={f.phone} onChange={set('phone')} placeholder="(214) 555-0100" /></label>
                <label className="rf-label">Lead source<select className="rf-select" value={f.source} onChange={set('source')}>{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></label>
              </div>
              <label className="rf-label">Street address<input className="rf-input" value={f.street} onChange={set('street')} /></label>
              <div className="rf-add__row rf-add__row--3">
                <label className="rf-label">City<input className="rf-input" value={f.city} onChange={set('city')} /></label>
                <label className="rf-label">State<input className="rf-input" defaultValue="TX" maxLength={2} /></label>
                <label className="rf-label">ZIP<input className="rf-input" value={f.zip} onChange={set('zip')} /></label>
              </div>
              <label className="rf-label">Roofing problem<input className="rf-input" value={f.issue} onChange={set('issue')} placeholder="e.g. Leak over the kitchen after the storm" /></label>
              <p className="rf-muted rf-small">In a live company, saving adds the homeowner to that company only, checks for duplicates, and sends nothing until you start follow-up.</p>
              <div className="rf-add__foot">
                <button type="button" className="rf-btn rf-btn--ghost" onClick={() => setAdding(false)}>Cancel</button>
                <button type="submit" className="rf-btn rf-btn--primary" disabled={!f.first.trim()}><Icon name="check" size={18} />Save homeowner (preview)</button>
              </div>
            </form>
          </section>
        )}
        {added.length > 0 && (
          <div className="rf-note"><Icon name="check" size={18} />
            <span><b>{added[0].first} {added[0].last}</b> added to this preview only. In a live workspace you’d now see <b>Open homeowner</b>, <b>Book inspection</b> and <b>Start inspection now</b>. Nothing was saved or sent.</span></div>
        )}
        <Panel title="Import & qualify your leads" sub="Your contacts stay yours. Nothing is sent while you import.">
          <div className="rf-import">
            <div className="rf-stack" style={{ gap: 14 }}>
              <div className="rf-drop" role="button" tabIndex={0}
                onClick={() => preview('Upload a spreadsheet', 'In a live workspace you drop a CSV or Excel file here (up to 250,000 rows). EvoSys matches the columns, checks duplicates and qualifies every homeowner before anything is contacted. Uploads are switched off in this demo.')}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.click() }}>
                <Icon name="upload" size={30} /><b>Drag &amp; drop your file here</b><span>CSV, Excel or tab-separated. (Uploads are off in the demo.)</span>
              </div>
              <div className="rf-chips">{['Property address', 'Roof issue', 'Repair or replacement', 'Urgency', 'Roof age', 'Lead source'].map((c) => <span className="rf-chip" key={c}>{c}</span>)}</div>
            </div>
            <div>
              <p className="rf-label" style={{ marginBottom: 12 }}>What happens next</p>
              <ol className="rf-checklist">
                {[['File uploaded', 'CSV or Excel, up to 250,000 rows'], ['Match columns', 'Roofing columns are matched automatically'], ['Check duplicates', 'Against the file and the homeowners you already have'], ['Qualify', 'Ready, needs review, or excluded - with the reason'], ['Start follow-up', 'Only homeowners ready to contact are enrolled']].map(([t, s], i) => (
                  <li key={t}><span className="rf-dot">{i + 1}</span><div>{t}<span>{s}</span></div></li>
                ))}
              </ol>
            </div>
          </div>
        </Panel>
        <Panel title="Homeowners" sub="Sample homeowners in this fictional company. Click one to open it.">
          <div className="rf-scroll-x">
            <table className="rf-table">
              <thead><tr><th>Homeowner</th><th>Stage</th><th>Roof issue</th><th>Repair or replace</th><th>Urgency</th><th>Owner</th><th>Source</th></tr></thead>
              <tbody>
                {added.map((a, i) => (
                  <tr key={`new${i}`}><td><div className="rf-cell-person"><Avatar name={`${a.first} ${a.last}`} size={32} /><div><b>{a.first} {a.last}</b><span>{[a.street, a.city].filter(Boolean).join(', ') || a.phone || 'Preview only'}</span></div></div></td>
                    <td><Pill tone="amber">Preview only</Pill></td><td>{a.issue || '—'}</td><td>—</td><td>—</td><td>Unassigned</td><td className="rf-muted">{a.source}</td></tr>
                ))}
                {rows.map((h) => (
                  <tr key={h.id} className="rf-row-link" tabIndex={0}>
                    <td><Link className="dm-rowlink" to={`${DEMO_BASE}/homeowners/${h.id}`}><div className="rf-cell-person"><Avatar name={h.name} size={32} /><div><b>{h.name}</b><span>{h.address}</span></div></div></Link></td>
                    <td><Pill tone={h.stage === 'won' ? 'green' : h.stage === 'lost' ? 'slate' : 'blue'}>{STAGES.find((s) => s.key === h.stage)?.label}</Pill></td>
                    <td>{h.roof_issue}</td><td>{h.service.replace('_', ' ')}</td>
                    <td><Pill tone={h.urgency === 'high' ? 'orange' : h.urgency === 'low' ? 'slate' : 'blue'}>{h.urgency}</Pill></td>
                    <td>{h.owner}</td><td className="rf-muted">{h.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </div>
  )
}

// ── Roofing Setup (informational preview) ─────────────────────────────────
export function DemoSetup() {
  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Roofing setup</h1>
          <p className="rf-lede">What each roofing company tailors to the way it works. A preview - settings can’t be changed in the demo.</p>
        </div>
        <span className="rf-sample">Sample company</span>
      </div>
      <div className="rf-grid-2">
        <Panel title="Your company" sub="Shown on texts, emails and inspection reports.">
          <dl className="rf-dl">
            <dt>Company name</dt><dd>{COMPANY.name}</dd>
            <dt>Office phone</dt><dd>{COMPANY.phone}</dd>
            <dt>Service area</dt><dd>{COMPANY.city} and nearby</dd>
            <dt>Brand color</dt><dd><span className="dm-swatch" /> Orange</dd>
          </dl>
        </Panel>
        <Panel title="Inspection types and hours" sub="What can be booked, and when.">
          <div className="rf-chips">{['Roof Inspection - 60 min', 'Storm Damage Assessment - 75 min', 'Insurance Claim Review - 60 min', 'Estimate Appointment - 45 min'].map((t) => <span key={t} className="rf-chip">{t}</span>)}</div>
          <p className="rf-muted" style={{ marginTop: 10, fontSize: 13.5 }}>Mon-Sat, 8 AM - 5 PM. Bookings respect each salesperson’s connected calendar.</p>
        </Panel>
        <Panel title="The follow-up playbook" sub={PLAYBOOK.goal}>
          <ol className="dm-playlist">
            {PLAYBOOK.touches.map((t) => <li key={t.touch}><b>Touch {t.touch} · day {t.day} · {t.channel}</b><span>{t.text}</span></li>)}
          </ol>
        </Panel>
        <Panel title="Your team" sub="Who gets which homeowners, and who is alerted when an inspection is booked.">
          <ul className="dm-team">{TEAM.map((u) => <li key={u.id}><Avatar name={u.name} size={30} /><b>{u.name}</b><span>{u.role}</span></li>)}</ul>
          <p className="rf-muted" style={{ marginTop: 10, fontSize: 13 }}>Sample people. Real teams, phone numbers and integrations are set up privately for each company.</p>
        </Panel>
      </div>
      <section className="rf-panel dm-cta" style={{ marginTop: 18 }}>
        <div><b>Want to see it set up for your company?</b><span>We’ll walk through your lead list, your team and your inspection process.</span></div>
        <a className="rf-btn rf-btn--primary" href={BOOK_DEMO}>Book a live demo</a>
      </section>
    </div>
  )
}

export { byId }
