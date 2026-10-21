/**
 * AI WORK QUEUE - who the AI is working, what it sent, what's next.
 *
 * The running playbook sits on top (goal, audience, touches, stop rules and
 * its real results). Below: every homeowner by lane, plus the homeowners who
 * are eligible but not started. Pause = a person takes the conversation (the
 * sequence pauses); Resume hands it back. Nothing here sends a message.
 */
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useRoofing } from './RoofingShell'
import { Avatar, Empty, ErrorNote, Icon, Loading, Pill, SampleTag, ago, errText, localWhen, n, until, useApi, useToast } from './ui'
import './roofing-mission.css'

const TOUCHES = 5

function Playbook({ pb, canManage, onEnroll, enrolling }) {
  const a = pb.active
  return (
    <section className="rf-panel">
      <div className="wq-play">
        <div>
          <p className="wq-play__name">
            {a.name}
            <Pill tone={a.texts_on ? 'green' : 'amber'}>{a.texts_on ? 'Running' : 'Texts paused'}</Pill>
          </p>
          <p className="wq-play__goal">{a.goal}. Sent to: {a.audience.toLowerCase()}.</p>
          <ol className="wq-steps" aria-label="Touches in this playbook">
            {a.touches.map((t) => <li key={t.touch} title={t.text}>Touch {t.touch}: day {t.day}, {t.channel === 'sms' ? 'text' : t.channel}</li>)}
          </ol>
          <p className="rf-muted" style={{ fontSize: 13, marginTop: 10 }}>{a.stop_rules.join('. ')}.</p>
          {!a.texts_on && <p className="rf-note rf-note--warn" style={{ marginTop: 12 }}><Icon name="alert" size={16} />Automated texting is off for this workspace. Homeowners can be enrolled, but no scheduled text goes out until the platform owner turns it on.</p>}
        </div>
        <div>
          <dl className="wq-stats">
            <div><dt>In follow-up</dt><dd>{n(a.enrolled)}</dd></div>
            <div><dt>Engaged</dt><dd>{n(a.engaged)}</dd></div>
            <div><dt>Booked</dt><dd>{n(a.booked)}</dd></div>
            <div><dt>Opted out</dt><dd>{n(a.opted_out)}</dd></div>
          </dl>
          {canManage && (
            <button className="rf-btn rf-btn--primary" style={{ marginTop: 14, width: '100%' }} disabled={enrolling} onClick={onEnroll}>
              <Icon name="play" size={16} />{enrolling ? 'Starting…' : 'Start follow-up for ready homeowners'}
            </button>
          )}
          <Link className="mc-more" to={`${BASE}/settings`}>Playbook details and other playbooks</Link>
        </div>
      </div>
    </section>
  )
}

function Row({ c, lane, onToggle, busy }) {
  const paused = c.mode === 'human_active'
  return (
    <div className="wq-row">
      <div className="rf-cell-person">
        <Avatar name={c.name} size={36} />
        <div><b>{c.name}</b><span>{c.roof_issue || c.address || '—'}</span></div>
      </div>
      <div title={`${c.touch || 0} messages sent`}>
        <div className="wq-touch">{Array.from({ length: TOUCHES }, (_, i) => <i key={i} className={i < (c.touch || 0) ? 'on' : ''} />)}</div>
        <span className="rf-muted" style={{ fontSize: 12 }}>Touch {c.touch || 0}{(c.touch || 0) > TOUCHES ? '' : ` of ${TOUCHES}`}</span>
      </div>
      <div className="wq-row__sum">{c.summary || (lane === 'eligible' ? 'Ready to contact. Not started.' : '—')}</div>
      <div className="wq-row__when">
        {lane === 'booked' && c.inspection_at ? `Inspection ${localWhen(c.inspection_at)}`
          : c.next_touch_at ? `Next touch ${until(c.next_touch_at)}`
          : c.last_activity_at ? `Active ${ago(c.last_activity_at)}` : ''}
      </div>
      <div className="wq-row__btns">
        {lane !== 'eligible' && (
          <button className="rf-btn rf-btn--ghost rf-btn--sm" disabled={busy} onClick={() => onToggle(c, !paused)}
            title={paused ? 'Give this conversation back to the AI' : 'Pause the AI and handle this homeowner yourself'}>
            <Icon name={paused ? 'play' : 'pause'} size={14} />{paused ? 'Resume AI' : 'Pause AI'}
          </button>
        )}
        <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${BASE}/conversations?lead=${c.lead_id}`}>Open</Link>
      </div>
    </div>
  )
}

export default function WorkQueue() {
  const { status, reloadCounts } = useRoofing()
  const [sp, setSp] = useSearchParams()
  const { data: m, error, loading, reload } = useApi('/roofing-ops/mission?lane_limit=500')
  const { data: pb, reload: reloadPb } = useApi('/roofing-ops/playbooks')
  const { data: elig, reload: reloadElig } = useApi('/roofing-ops/leads?stage=qualified&page_size=200')
  const [busy, setBusy] = useState(false)
  const [modes, setModes] = useState({})
  const [enrollOut, setEnrollOut] = useState(null)
  const [toast, say] = useToast()
  const lane = sp.get('lane') || 'working'

  const lanes = useMemo(() => {
    if (!m) return []
    const ls = m.lanes.map((l) => ({ ...l }))
    ls.push({ key: 'eligible', label: 'Ready, not started', count: elig?.total ?? 0,
      cards: (elig?.items || []).map((r) => ({ ...r, touch: 0 })) })
    return ls
  }, [m, elig])

  const toggle = async (c, human) => {
    setBusy(true)
    try {
      const r = await api.post(`/roofing-ops/leads/${c.lead_id}/takeover`, { human })
      setModes((x) => ({ ...x, [c.lead_id]: r.mode }))
      say(human ? `AI paused for ${c.name}. You're handling this conversation.` : `AI resumed for ${c.name}.`)
      reloadCounts()
    } catch (e) { say(errText(e), 'err') } finally { setBusy(false) }
  }
  const enroll = async () => {
    setBusy(true)
    try {
      const r = await api.post('/roofing-ops/outreach/enroll', {})
      setEnrollOut(r)
      say(r.enrolled ? `Started follow-up for ${r.enrolled} homeowners.` : 'No homeowners were ready to start.')
      reload(); reloadPb(); reloadElig()
    } catch (e) { say(errText(e), 'err') } finally { setBusy(false) }
  }

  if (loading && !m) return <div className="rf-page"><Loading label="Loading the work queue" /></div>
  if (error && !m) return <div className="rf-page"><ErrorNote onRetry={reload}>{error}</ErrorNote></div>
  const cur = lanes.find((l) => l.key === lane) || lanes[0]

  return (
    <main className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">AI Work Queue</h1>
          <p className="rf-lede">Who the AI is working, what it has sent, and what happens next.</p>
        </div>
        {m.sample_data && <SampleTag />}
      </div>
      <div className="rf-stack">
        {pb && <Playbook pb={pb} canManage={status.can_manage} onEnroll={enroll} enrolling={busy} />}
        {enrollOut && (
          <div className="rf-note">
            <Icon name="check" size={16} />
            <span>
              Started {n(enrollOut.enrolled)} of {n(enrollOut.considered)} homeowners checked.
              {enrollOut.skipped.length > 0 && <> Not started: {enrollOut.skipped.slice(0, 3).map((s) => `${s.reason} (${s.count})`).join('; ')}.</>}
              {enrollOut.note && <> {enrollOut.note}</>}
            </span>
          </div>
        )}
        <section className="rf-panel">
          <div className="wq-head">
            <div className="rf-tabs" role="tablist">
              {lanes.map((l) => (
                <button key={l.key} role="tab" className="rf-tab" aria-selected={l.key === cur.key}
                  onClick={() => setSp(l.key === 'working' ? {} : { lane: l.key })}>
                  {l.label}<span className="rf-tab__n">{l.count}</span>
                </button>
              ))}
            </div>
          </div>
          {cur.cards.length === 0
            ? <Empty icon="spark" title={`No homeowners in “${cur.label}”`}>
                {cur.key === 'eligible'
                  ? 'Everyone who can be contacted is already in follow-up, or nobody has passed the contact checks yet. Leads & Data shows why.'
                  : 'Homeowners move here as the AI works them.'}
              </Empty>
            : cur.cards.map((c) => (
              <Row key={c.lead_id} c={{ ...c, mode: modes[c.lead_id] || c.mode }} lane={cur.key} onToggle={toggle} busy={busy} />
            ))}
        </section>
      </div>
      {toast}
    </main>
  )
}
