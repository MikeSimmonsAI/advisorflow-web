/**
 * MISSION CONTROL - the roofing owner's front door.
 *
 * Five-second answers, in this order on the page:
 *   1 What is my AI team doing right now?   (hero strip, live from /mission.ai)
 *   2 What needs a person?                  (Needs your attention, newest first)
 *   3 What is the work producing?           (outcome ribbon, every % with its denominator)
 * The AI workboard is the big panel; tables and the stage board live deeper.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useRoofing } from './RoofingShell'
import {
  Avatar, Empty, ErrorNote, Icon, Loading, Pill, SampleTag, ago, errText, localWhen, n, pct, until, useApi, useToast,
} from './ui'
import './roofing-mission.css'

const LANE_ICON = { human_needed: 'hand', requested: 'star', booked: 'calendar', replied: 'chat', working: 'spark' }
const BUCKET_LABEL = { needs_human: 'Needs you', ready_to_book: 'Ready to book', new_reply: 'New reply' }

function Roofline() {
  // Abstract shingle courses and a ridge line - texture for the hero, not data.
  const rows = Array.from({ length: 9 }, (_, i) => i)
  return (
    <svg className="mc-roofline" viewBox="0 0 520 260" preserveAspectRatio="xMaxYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id="mc-roof-fade" x1="0" x2="1">
          <stop offset="0" stopColor="#12284a" stopOpacity="0" />
          <stop offset=".45" stopColor="#12284a" stopOpacity=".9" />
          <stop offset="1" stopColor="#1c3760" />
        </linearGradient>
        <pattern id="mc-shingle" width="34" height="18" patternUnits="userSpaceOnUse">
          <path d="M0 18V2h34v16" fill="none" stroke="#27477a" strokeWidth="1.4" />
          <path d="M17 2v16" stroke="#27477a" strokeWidth="1.2" />
        </pattern>
        <clipPath id="mc-roof-clip"><path d="M60 260 300 40 560 260z" /></clipPath>
      </defs>
      <rect width="520" height="260" fill="url(#mc-roof-fade)" />
      <g clipPath="url(#mc-roof-clip)">
        <rect width="560" height="260" fill="#16305a" />
        <rect width="560" height="260" fill="url(#mc-shingle)" />
        {rows.map((i) => <path key={i} d={`M0 ${70 + i * 22}H560`} stroke="#0f2340" strokeOpacity=".35" />)}
      </g>
      <path d="M60 260 300 40 560 260" fill="none" stroke="#f26a1b" strokeWidth="5" strokeLinejoin="round" />
      <path d="M150 260 300 122 470 260" fill="none" stroke="#f26a1b" strokeOpacity=".35" strokeWidth="3" />
      <rect x="380" y="92" width="34" height="58" fill="#0f2340" />
      <rect x="375" y="86" width="44" height="10" rx="2" fill="#1c3760" />
    </svg>
  )
}

function Hero({ m, status }) {
  const ai = m.ai
  const live = ai.sequences_running > 0
  return (
    <section className="mc-hero">
      <Roofline />
      <div className="mc-hero__copy">
        <p className="mc-hero__state">
          <span className={`mc-pulse ${live ? 'on' : ''}`} />
          {!live ? 'No follow-up running yet' : status.texts_on ? 'AI follow-up is running' : 'Follow-up scheduled, automated texts paused'}
          {m.sample_data && <SampleTag />}
        </p>
        <h1 className="mc-hero__title">Your AI team is working your roofing leads.</h1>
        <p className="mc-hero__live">
          <b>{n(ai.sequences_running)}</b> homeowners in follow-up. <b>{n(ai.touches_sent_today)}</b> messages
          sent today, <b>{n(ai.touches_due_24h)}</b> due in the next 24 hours.
          {ai.eligible_not_started > 0 && <> <b>{n(ai.eligible_not_started)}</b> more are ready to start.</>}
        </p>
        {!status.texts_on && (
          <p className="mc-hero__warn"><Icon name="alert" size={16} />Automated texts are off for this workspace, so scheduled touches wait until the platform owner turns them on.</p>
        )}
        <div className="mc-hero__actions">
          <Link className="rf-btn rf-btn--primary" to={`${BASE}/leads`}><Icon name="upload" size={18} />Import my leads</Link>
          <Link className="rf-btn rf-btn--on-dark" to={`${BASE}/conversations?bucket=ready_to_book`}>
            <Icon name="star" size={18} />Review hot opportunities
          </Link>
        </div>
      </div>
      <dl className="mc-today" aria-label="Today">
        <div><dt>Replies today</dt><dd>{n(ai.replies_today)}</dd></div>
        <div><dt>Inspections booked today</dt><dd>{n(ai.inspections_booked_today)}</dd></div>
        <div><dt>Waiting on you</dt><dd className={m.attention_total ? 'hot' : ''}>{n(m.attention_total)}</dd></div>
      </dl>
    </section>
  )
}

function Ribbon({ o }) {
  const d = o.denominators || {}
  const seg = [
    { label: 'Homeowners contacted', big: n(o.contacted), small: `of ${n(o.total_leads)} leads` },
    { label: 'Responded', big: pct(o.response_pct), small: `${n(o.responded)} of ${n(o.contacted)} ${d.response_pct ? 'contacted' : ''}` },
    { label: 'Engaged', big: pct(o.engaged_pct), small: `${n(o.engaged)} of ${n(o.contacted)} contacted` },
    { label: 'Inspections confirmed', big: n(o.inspections_confirmed), small: o.booking_pct_of_engaged == null ? 'no engaged homeowners yet' : `${pct(o.booking_pct_of_engaged)} of ${n(o.engaged)} engaged`, accent: true },
    { label: 'Upcoming inspections', big: n(o.upcoming_inspections), small: 'on the calendar' },
  ]
  return (
    <section className="mc-ribbon rf-panel" aria-label="What the work is producing">
      {seg.map((s) => (
        <div key={s.label} className={`mc-ribbon__seg ${s.accent ? 'accent' : ''}`}>
          <span className="mc-ribbon__label">{s.label}</span>
          <span className="mc-ribbon__big">{s.big}</span>
          <span className="mc-ribbon__small">{s.small}</span>
        </div>
      ))}
      {(o.estimates_open > 0 || o.won > 0) && (
        <div className="mc-ribbon__money">
          <span>{n(o.estimates_open)} open estimates worth <b>${n(o.estimate_value_open)}</b></span>
          <span>{n(o.won)} jobs won, <b>${n(o.won_value)}</b></span>
          <span className="rf-muted">From amounts your team entered</span>
        </div>
      )}
    </section>
  )
}

function LeadCard({ c, lane, onTakeover, busy }) {
  const nav = useNavigate()
  const open = () => nav(`${BASE}/conversations?lead=${c.lead_id}`)
  return (
    <article className="mc-card">
      <div className="mc-card__top">
        <Avatar name={c.name} size={38} />
        <div className="mc-card__who">
          <b>{c.name}</b>
          <span>{c.roof_issue || c.address || 'No roof issue recorded'}</span>
        </div>
        <span className="mc-card__touch" title="Messages sent to this homeowner">Touch {c.touch || 0}</span>
      </div>
      {c.summary && <p className="mc-card__sum">{c.summary}</p>}
      <div className="mc-card__foot">
        <span className="mc-card__next">
          <Icon name={lane === 'booked' ? 'calendar' : 'clock'} size={15} />
          {lane === 'booked' && c.inspection_at ? `Inspection ${localWhen(c.inspection_at)}`
            : lane === 'working' && c.next_touch_at ? `Next touch ${until(c.next_touch_at)}`
            : lane === 'human_needed' ? (c.needs_human_reason || 'Answer personally')
            : lane === 'requested' ? 'Offer or confirm a time'
            : c.last_activity_at ? `Last activity ${ago(c.last_activity_at)}` : ''}
        </span>
        <span className="mc-card__btns">
          {lane === 'requested' && <Link className="rf-btn rf-btn--primary rf-btn--sm" to={`${BASE}/inspections?lead=${c.lead_id}`}>Book inspection</Link>}
          {lane === 'human_needed' && <button className="rf-btn rf-btn--navy rf-btn--sm" disabled={busy} onClick={() => onTakeover(c)}>Take over</button>}
          <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={open}>Open conversation</button>
        </span>
      </div>
    </article>
  )
}

function Workboard({ m, onTakeover, busy }) {
  const first = (m.lanes.find((l) => l.count > 0) || m.lanes[0]).key
  const [lane, setLane] = useState(first)
  const cur = m.lanes.find((l) => l.key === lane) || m.lanes[0]
  return (
    <section className="rf-panel mc-board" aria-labelledby="mc-board-title">
      <header className="rf-panel__head">
        <div>
          <h2 className="rf-panel__title" id="mc-board-title">AI workboard</h2>
          <p className="rf-panel__sub">Every homeowner the AI is working, by what happens next.</p>
        </div>
        <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${BASE}/queue`}>Open work queue</Link>
      </header>
      <div className="mc-lanes" role="tablist" aria-label="Workboard lanes">
        {m.lanes.map((l) => (
          <button key={l.key} role="tab" aria-selected={l.key === lane} className={`mc-lane mc-lane--${l.key}`} onClick={() => setLane(l.key)}>
            <Icon name={LANE_ICON[l.key]} size={17} />
            <span className="mc-lane__label">{l.label}</span>
            <span className="mc-lane__n">{l.count}</span>
          </button>
        ))}
      </div>
      <p className="mc-lane__hint">{cur.hint}</p>
      <div className="mc-cards">
        {cur.cards.length === 0
          ? <Empty icon={LANE_ICON[cur.key]} title={`No homeowners in “${cur.label}”`}>
              {cur.key === 'working' ? 'Start follow-up for homeowners who are ready from the AI Work Queue.' : 'This lane fills as homeowners reply and book.'}
            </Empty>
          : cur.cards.map((c) => <LeadCard key={c.lead_id} c={c} lane={cur.key} onTakeover={onTakeover} busy={busy} />)}
        {cur.count > cur.cards.length && (
          <Link className="mc-more" to={`${BASE}/queue?lane=${cur.key}`}>See all {cur.count} in the work queue</Link>
        )}
      </div>
    </section>
  )
}

function Attention({ m, onTakeover, busy }) {
  const nav = useNavigate()
  return (
    <section className="rf-panel mc-attn" aria-labelledby="mc-attn-title">
      <header className="rf-panel__head">
        <div>
          <h2 className="rf-panel__title" id="mc-attn-title">Needs your attention <span className="mc-attn__n">{m.attention_total}</span></h2>
          <p className="rf-panel__sub">Newest replies first. Questions and hand-offs on top.</p>
        </div>
      </header>
      {m.attention.length === 0
        ? <Empty icon="check" title="Nothing waiting on you">New replies, questions and booking requests show up here the moment they arrive.</Empty>
        : (
          <ul className="mc-attn__list">
            {m.attention.map((it) => (
              <li key={it.lead_id} className={`mc-attn__item mc-attn__item--${it.bucket}`}>
                <div className="mc-attn__row">
                  <Avatar name={it.name} size={34} />
                  <div className="mc-attn__who">
                    <b>{it.name}</b>
                    <span>{ago(it.latest_reply_at)}{it.unread > 1 ? `, ${it.unread} unread` : ''}</span>
                  </div>
                  <Pill tone={it.bucket}>{BUCKET_LABEL[it.bucket]}</Pill>
                </div>
                {it.latest_reply && <p className="mc-attn__quote">“{it.latest_reply}”</p>}
                <div className="mc-attn__btns">
                  <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => nav(`${BASE}/conversations?lead=${it.lead_id}`)}>Open conversation</button>
                  {it.bucket === 'ready_to_book' && <Link className="rf-btn rf-btn--primary rf-btn--sm" to={`${BASE}/inspections?lead=${it.lead_id}`}>Book inspection</Link>}
                  {it.bucket === 'needs_human' && it.mode !== 'human_active' && (
                    <button className="rf-btn rf-btn--navy rf-btn--sm" disabled={busy} onClick={() => onTakeover(it)}>Take over</button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      {m.attention_total > m.attention.length && (
        <Link className="mc-more" to={`${BASE}/conversations`}>See all {m.attention_total} in Conversations</Link>
      )}
    </section>
  )
}

function Snapshot() {
  const { data: d } = useApi('/roofing-ops/dashboard')
  if (!d) return null
  const funnel = d.funnel.filter((f) => ['imported', 'contacted', 'engaged', 'inspection_offered', 'inspection_booked'].includes(f.key))
  const top = Math.max(1, ...funnel.map((f) => f.count))
  const touches = d.by_touch
  const best = [...touches].filter((t) => t.received > 0 && t.reply_rate != null).sort((a, b) => b.reply_rate - a.reply_rate)[0]
  const tmax = Math.max(1, ...touches.map((t) => t.reply_rate || 0))
  return (
    <section className="rf-panel mc-snap" aria-labelledby="mc-snap-title">
      <header className="rf-panel__head">
        <div>
          <h2 className="rf-panel__title" id="mc-snap-title">Last 30 days</h2>
          <p className="rf-panel__sub">{d.population}.</p>
        </div>
        <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${BASE}/performance`}>Full performance</Link>
      </header>
      {!d.has_data ? <Empty icon="chart" title="No homeowners imported in the last 30 days">Import your existing leads and the conversion numbers start here.</Empty> : (
        <div className="mc-snap__grid">
          <div>
            <h3 className="mc-snap__h">From imported to booked</h3>
            <ol className="mc-funnel">
              {funnel.map((f) => (
                <li key={f.key}>
                  <span className="mc-funnel__label">{f.label}</span>
                  <span className="mc-funnel__bar"><i style={{ width: `${Math.max(2, (f.count / top) * 100)}%` }} /></span>
                  <span className="mc-funnel__n">{n(f.count)}</span>
                  <span className="mc-funnel__p">{pct(f.pct_of_imported)}</span>
                </li>
              ))}
            </ol>
            <p className="mc-snap__foot">Percent of homeowners imported in the period.</p>
          </div>
          <div>
            <h3 className="mc-snap__h">Which touch gets replies</h3>
            <div className="mc-touch">
              {touches.map((t) => (
                <div key={t.touch} className={`mc-touch__col ${best && best.touch === t.touch ? 'best' : ''}`}>
                  <span className="mc-touch__v">{pct(t.reply_rate)}</span>
                  <span className="mc-touch__bar"><i style={{ height: `${t.reply_rate ? Math.max(4, (t.reply_rate / tmax) * 100) : 0}%` }} /></span>
                  <span className="mc-touch__l">Touch {t.touch}</span>
                  <span className="mc-touch__d">{n(t.replied)} of {n(t.received)}</span>
                </div>
              ))}
            </div>
            <p className="mc-snap__foot">
              {best ? <>Touch {best.touch} has the best reply rate: {pct(best.reply_rate)} of homeowners who received it replied before the next touch.</>
                : 'Reply rates appear once touches have gone out.'}
            </p>
          </div>
        </div>
      )}
    </section>
  )
}

export default function MissionControl() {
  const { status, reloadCounts } = useRoofing()
  const { data: m, error, loading, reload } = useApi('/roofing-ops/mission')
  const [busy, setBusy] = useState(false)
  const [toast, say] = useToast()

  const takeover = async (it) => {
    setBusy(true)
    try {
      await api.post(`/roofing-ops/leads/${it.lead_id}/takeover`, { human: true, reason: 'Taken over from Mission Control' })
      say(`You took over ${it.name}. Automated follow-up is paused for them.`)
      reload(); reloadCounts()
    } catch (e) { say(errText(e), 'err') } finally { setBusy(false) }
  }

  if (loading && !m) return <div className="rf-page"><Loading label="Reading what your AI team is doing" /></div>
  if (error && !m) return <div className="rf-page"><ErrorNote onRetry={reload}>{error}</ErrorNote></div>

  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
  return (
    <main className="rf-page mc">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Mission Control</h1>
          <p className="rf-lede">{today}. Updated {ago(m.as_of)}.</p>
        </div>
        <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => { reload(); reloadCounts() }}><Icon name="refresh" size={16} />Refresh</button>
      </div>
      {!m.has_leads ? (
        <>
          <Hero m={m} status={status} />
          <section className="rf-panel" style={{ marginTop: 20 }}>
            <Empty icon="upload" title="Bring in the leads you already have"
              action={<Link className="rf-btn rf-btn--primary" to={`${BASE}/leads`}>Import my leads</Link>}>
              Upload a spreadsheet of past estimates, inspection requests or storm inquiries. EvoSys checks who can be contacted, then the AI starts the follow-up.
            </Empty>
          </section>
        </>
      ) : (
        <>
          <Hero m={m} status={status} />
          <Ribbon o={m.outcomes} />
          <div className="mc-floor">
            <Workboard m={m} onTakeover={takeover} busy={busy} />
            <Attention m={m} onTakeover={takeover} busy={busy} />
          </div>
          <Snapshot />
        </>
      )}
      {toast}
    </main>
  )
}
