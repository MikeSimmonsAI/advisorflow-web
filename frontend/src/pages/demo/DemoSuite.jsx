/**
 * THE DEMO SUITE — the surface a salesperson presents the product from.
 *
 * ── WHAT THIS SCREEN IS FOR ───────────────────────────────────────────────
 * So that Mike does not have to attend ordinary sales meetings. The
 * salesperson runs the relationship; the SOFTWARE carries the product
 * knowledge — what to show, what happens when you click it, why the prospect
 * should care, and a sentence to say out loud.
 *
 * ── EVERY BUTTON DOES SOMETHING ───────────────────────────────────────────
 * Every action here posts to /demo-suite/{brand}/action, which writes to the
 * demonstration tenant's real tables and returns the refreshed world. Moving a
 * deal moves the deal. Booking an appointment creates the appointment. There
 * are no decorative controls on this screen, because a dead button in front of
 * a prospect is worse than a missing feature.
 *
 * The ONE thing that is simulated is the provider call: a demonstration send
 * writes the message row a real send would write and never reaches a carrier.
 * The screen says so, in the thread, rather than letting somebody wonder.
 *
 * ── WHY THE PANELS ARE OURS RATHER THAN THE TENANT SCREENS ────────────────
 * Dropping a presenter into the real customer app pointed at the demo
 * workspace would require them to hold a membership in it — which would make
 * demo access a species of workspace access, and those are deliberately
 * different entitlements. It would also mean demo actions firing inside the
 * same screens a real tenant uses. So the Suite reads the demo tenant through
 * its own endpoints and renders it itself. The DATA is the product's real data
 * model; the surface is built for presenting.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import DemoStyles from './DemoStyles'

const PANELS = [
  { key: 'priority', label: "Today's Priorities", group: 'OPERATOR', help: 'ai_prioritisation' },
  { key: 'leads', label: 'Leads', group: 'OPERATOR', help: 'lead_ownership' },
  { key: 'conversation', label: 'Conversation', group: 'OPERATOR', help: 'unified_thread' },
  { key: 'calendar', label: 'Calendar', group: 'OPERATOR', help: 'appointments' },
  { key: 'pipeline', label: 'Pipeline', group: 'BACK OFFICE', help: 'pipeline_stages' },
  { key: 'team', label: 'Team', group: 'BACK OFFICE', help: 'manager_view' },
  { key: 'revenue', label: 'Revenue & Performance', group: 'EXECUTIVE', help: 'executive_authority' },
  { key: 'customers', label: 'Customers', group: 'EXECUTIVE', help: 'executive_authority' },
  { key: 'launch', label: 'Customer Launch', group: 'EXECUTIVE', help: 'customer_launch' },
]

const money = n => n == null ? '—'
  : '$' + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 })

const dt = iso => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric',
                                    hour: 'numeric', minute: '2-digit' })
}

const day = iso => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString()
}

function Explain ({ topic, onOpen }) {
  if (!topic) return null
  return (
    <button className="ds-explain" title="Explain this"
            onClick={e => { e.stopPropagation(); onOpen(topic) }}>i</button>
  )
}

function Card ({ title, note, help, onExplain, actions, children }) {
  return (
    <div className="ds-card">
      <div className="ds-card-head">
        <h3 className="ds-card-title">{title}</h3>
        <Explain topic={help} onOpen={onExplain} />
        {note && <span className="ds-card-note">{note}</span>}
        <span style={{ flex: 1 }} />
        {actions}
      </div>
      {children}
    </div>
  )
}

/* ══ PANELS ═══════════════════════════════════════════════════════════════ */

function PriorityPanel ({ data, act, busy, onExplain, onPickLead }) {
  const bands = data.bands || {}
  return (
    <>
      <Card title="Today's Priorities"
            note="ordered by what needs a person, not by date"
            help="ai_prioritisation" onExplain={onExplain}
            actions={<button className="ds-btn primary" disabled={busy}
                             onClick={() => act('qualify_all')}>
              {busy ? 'WORKING…' : 'QUALIFY & PRIORITISE'}
            </button>}>
        <div className="ds-grid stats" style={{ marginBottom: 16 }}>
          <div className="ds-stat">
            <div className="k">Needs a person today</div>
            <div className="v">{data.needs_a_person_today ?? 0}</div>
            <div className="s">urgent band</div>
          </div>
          <div className="ds-stat">
            <div className="k">Worth a call this week</div>
            <div className="v">{bands.high || 0}</div>
            <div className="s">high band</div>
          </div>
          <div className="ds-stat">
            <div className="k">Working queue</div>
            <div className="v">{bands.standard || 0}</div>
            <div className="s">standard band</div>
          </div>
          <div className="ds-stat">
            <div className="k">Excluded</div>
            <div className="v">{(data.excluded || []).length}</div>
            <div className="s">and every one says why</div>
          </div>
        </div>

        {(data.queue || []).length === 0 &&
          <div className="ds-empty">Nothing in the queue.</div>}
        {(data.queue || []).slice(0, 10).map(item => (
          <button key={item.key} className="ds-row" onClick={() => onPickLead(item.key)}>
            <span className={'ds-pill ' + item.band}>{item.band}</span>
            <span>
              <div className="name">{item.name}</div>
              <div className="meta">{item.reason} · {item.owner}</div>
            </span>
            <span className="spacer" />
            <span className="ds-pill">{item.score ?? '—'}</span>
          </button>
        ))}
      </Card>

      {(data.excluded || []).length > 0 && (
        <Card title="Excluded, and why" note="reported rather than quietly skipped"
              help="compliance" onExplain={onExplain}>
          {data.excluded.map((e, i) => (
            <div key={i} className="ds-row" style={{ cursor: 'default' }}>
              <span className="ds-pill blocked">blocked</span>
              <span>
                <div className="name">{e.name}</div>
                <div className="meta">{e.reason}</div>
              </span>
            </div>
          ))}
        </Card>
      )}
    </>
  )
}

const LEAD_FILTERS = [
  { key: 'all', label: 'Everyone' },
  { key: 'new', label: 'Never contacted' },
  { key: 'dormant', label: 'Dormant' },
  { key: 'hot', label: 'Hot' },
  { key: 'blocked', label: 'Do-not-contact' },
]

function LeadsPanel ({ data, onExplain, onPickLead }) {
  const [filter, setFilter] = useState('all')
  const rows = (data.leads || []).filter(l => {
    if (filter === 'new') return l.never_contacted
    if (filter === 'dormant') return l.dormant
    if (filter === 'hot') return l.temperature === 'hot'
    if (filter === 'blocked') return l.blocked
    return true
  })
  return (
    <Card title={data.organization || 'Leads'} note={`${rows.length} of ${data.total}`}
          help="lead_ownership" onExplain={onExplain}
          actions={
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {LEAD_FILTERS.map(f => (
                <button key={f.key}
                        className={'ds-btn small ' + (filter === f.key ? '' : 'ghost')}
                        onClick={() => setFilter(f.key)}>{f.label}</button>
              ))}
            </div>}>
      {rows.length === 0 && <div className="ds-empty">Nothing matches that filter.</div>}
      {rows.map(l => (
        <button key={l.key} className="ds-row" onClick={() => onPickLead(l.key)}>
          <span className={'ds-pill ' + (l.blocked ? 'blocked' : l.temperature)}>
            {l.blocked ? 'do not contact' : l.temperature}
          </span>
          <span>
            <div className="name">{l.name}</div>
            <div className="meta">
              {l.owner || 'Unassigned'} · {l.source}
              {l.never_contacted
                ? ' · never contacted'
                : ` · last touched ${l.days_since_contact}d ago`}
            </div>
          </span>
          <span className="spacer" />
          <span className="ds-pill">{l.status}</span>
        </button>
      ))}
    </Card>
  )
}

function ConversationPanel ({ data, act, busy, onExplain, onPickLead }) {
  const lead = data.lead
  if (!lead) return <div className="ds-empty">No conversation selected.</div>
  return (
    <>
      <Card title={lead.name} note={`${lead.status} · ${lead.owner}`}
            help="unified_thread" onExplain={onExplain}
            actions={
              <select className="ds-select" value={lead.key}
                      onChange={e => onPickLead(e.target.value)}>
                {(data.options || []).map(o =>
                  <option key={o.key} value={o.key}>{o.name}</option>)}
              </select>}>
        {lead.note && (
          <div className="ds-ok" style={{ marginBottom: 14 }}>{lead.note}</div>
        )}
        {lead.blocked && (
          <div className="ds-error" style={{ marginBottom: 14 }}>
            This person replied STOP. They stay in the database and stay
            unreachable — no channel, no exceptions, enforced on the server.
          </div>
        )}

        <div className="ds-thread">
          {(data.timeline || []).length === 0 &&
            <div className="ds-empty">Nothing has been sent to this person yet.</div>}
          {(data.timeline || []).map((m, i) => (
            <div key={i} className={'ds-msg ' + m.direction}>
              <div className="who">
                <span>{m.who}</span>
                <span>{dt(m.at)}</span>
                {m.kind === 'email' && <span className="ds-pill">email</span>}
                {m.simulated && <span className="ds-pill">simulated</span>}
              </div>
              {m.subject && <div style={{ fontWeight: 600, marginBottom: 5 }}>{m.subject}</div>}
              <div dangerouslySetInnerHTML={{ __html: String(m.body || '')
                .replace(/<(?!\/?(p|br|strong|em|b|i)\b)[^>]*>/gi, '') }} />
            </div>
          ))}
        </div>

        {data.draft && (
          <div className="ds-draft">
            <div className="label">Drafted by the assistant · waiting for approval</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{data.draft.body}</div>
            <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
              <button className="ds-btn primary" disabled={busy}
                      onClick={() => act('approve_draft', { target: lead.key })}>
                APPROVE &amp; SEND
              </button>
              <span style={{ fontSize: 11, color: 'var(--ds-ghost)', alignSelf: 'center' }}>
                Nothing has been sent. The gate is deliberate.
              </span>
            </div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
          <button className="ds-btn primary" disabled={busy || lead.blocked}
                  onClick={() => act('send_sms', { target: lead.key })}>
            SEND FIRST RESPONSE
          </button>
          <button className="ds-btn" disabled={busy || lead.blocked}
                  onClick={() => act('simulate_reply', { target: lead.key })}>
            SIMULATE THEIR REPLY
          </button>
          <button className="ds-btn" disabled={busy || lead.blocked}
                  onClick={() => act('ai_follow_up', { target: lead.key })}>
            DRAFT A FOLLOW-UP
          </button>
          <button className="ds-btn" disabled={busy || lead.blocked}
                  onClick={() => act('book_appointment', { target: lead.key })}>
            BOOK CONSULTATION
          </button>
          <button className="ds-btn ghost" disabled={busy}
                  onClick={() => act('qualify_lead', { target: lead.key })}>
            QUALIFY THIS RECORD
          </button>
        </div>
        {lead.blocked && (
          <p style={{ fontSize: 11, color: 'var(--ds-ghost)', marginTop: 10 }}>
            The send controls are disabled here because the record is
            do-not-contact. The server would refuse them anyway — hiding a
            button is never the control.
          </p>
        )}
      </Card>
    </>
  )
}

function CalendarPanel ({ data, onExplain, onPickLead }) {
  return (
    <Card title="Calendar" note={`${data.upcoming} upcoming`}
          help="appointments" onExplain={onExplain}>
      {(data.appointments || []).length === 0 &&
        <div className="ds-empty">No consultations booked yet.</div>}
      {(data.appointments || []).map(a => (
        <button key={a.id} className="ds-row"
                onClick={() => a.lead_key && onPickLead(a.lead_key)}>
          <span className={'ds-pill ' + (a.future ? 'ok' : '')}>
            {a.future ? 'upcoming' : 'completed'}
          </span>
          <span>
            <div className="name">{a.lead}</div>
            <div className="meta">{a.label} · {a.minutes} min · {a.advisor}</div>
          </span>
          <span className="spacer" />
          <span style={{ fontSize: 12, color: 'var(--ds-dim)' }}>{dt(a.when)}</span>
        </button>
      ))}
    </Card>
  )
}

function PipelinePanel ({ data, act, busy, onExplain }) {
  const [move, setMove] = useState(null)   // {key, company}
  const [stage, setStage] = useState('')
  return (
    <Card title={`${data.brand} — pipeline`} note="time in stage is where deals die"
          help="pipeline_stages" onExplain={onExplain}>
      {move && (
        <div className="ds-ok" style={{ marginBottom: 14, display: 'flex',
                                        gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span>Move <strong>{move.company}</strong> to</span>
          <select className="ds-select" value={stage} onChange={e => setStage(e.target.value)}>
            <option value="">choose a stage…</option>
            {(data.stage_options || []).map(s =>
              <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <button className="ds-btn primary" disabled={busy || !stage}
                  onClick={() => { act('move_stage', { target: move.key, to_stage: stage });
                                   setMove(null); setStage('') }}>
            MOVE
          </button>
          <button className="ds-btn ghost" onClick={() => { setMove(null); setStage('') }}>
            CANCEL
          </button>
        </div>
      )}
      <div className="ds-board">
        {(data.columns || []).map(col => (
          <div className="ds-col" key={col.stage}>
            <h4>{col.label}</h4>
            <div className="sum">{col.count} · {money(col.value)}</div>
            {col.deals.length === 0 &&
              <div style={{ color: 'var(--ds-ghost)', fontSize: 11, padding: '8px 0' }}>—</div>}
            {col.deals.map(d => (
              <div key={d.key} className={'ds-deal' + (d.stalled ? ' stalled' : '')}>
                <div className="co">{d.company}</div>
                <div className="mm">{d.owner} · {money(d.value)}</div>
                <div className="mm">
                  {d.days_in_stage != null ? `${d.days_in_stage} days in stage` : ''}
                  {d.stalled ? ' · stalled' : ''}
                </div>
                {d.next_action && (
                  <div className="mm" style={{ color: d.overdue ? 'var(--ds-amber)' : undefined }}>
                    Next: {d.next_action}{d.overdue ? ' (overdue)' : ''}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                  <button className="ds-btn small"
                          onClick={() => { setMove({ key: d.key, company: d.company }); setStage('') }}>
                    MOVE
                  </button>
                  {d.next_action && (
                    <button className="ds-btn small ghost" disabled={busy}
                            onClick={() => act('complete_task', { target: d.key })}>
                      COMPLETE
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Card>
  )
}

function TeamPanel ({ data, act, busy, onExplain }) {
  return (
    <Card title={`${data.brand} — team`}
          note={`${data.overdue_total} overdue action${data.overdue_total === 1 ? '' : 's'}`}
          help="manager_view" onExplain={onExplain}>
      {(data.people || []).map(p => (
        <div key={p.name} style={{ marginBottom: 16 }}>
          <div className="ds-row" style={{ cursor: 'default' }}>
            <span className={'ds-pill ' + (p.overdue ? 'warm' : 'ok')}>{p.role_label}</span>
            <span>
              <div className="name">{p.name}</div>
              <div className="meta">
                {p.open_deals} open · {money(p.pipeline_value)} pipeline
                {p.overdue ? ` · ${p.overdue} overdue` : ''}
              </div>
            </span>
          </div>
          {p.next_actions.map(a => (
            <div key={a.key} style={{ display: 'flex', alignItems: 'center', gap: 10,
                                      padding: '8px 14px 8px 46px' }}>
              <span style={{ flex: 1, fontSize: 12,
                             color: a.overdue ? 'var(--ds-amber)' : 'var(--ds-dim)' }}>
                {a.company} — {a.action}
                {a.due ? ` · due ${day(a.due)}` : ''}
              </span>
              <button className="ds-btn small" disabled={busy}
                      onClick={() => act('complete_task', { target: a.key })}>
                COMPLETE
              </button>
            </div>
          ))}
        </div>
      ))}
      {(data.people || []).length === 0 && <div className="ds-empty">No team members.</div>}
    </Card>
  )
}

function RevenuePanel ({ data, onExplain }) {
  const [showWeights, setShowWeights] = useState(false)
  return (
    <Card title={`${data.brand} — revenue & performance`}
          note="computed from the deals, not assembled at month end"
          help="executive_authority" onExplain={onExplain}
          actions={<button className="ds-btn small ghost"
                           onClick={() => setShowWeights(v => !v)}>
            {showWeights ? 'HIDE' : 'WEIGHTED HOW?'}
          </button>}>
      <div className="ds-grid stats">
        <div className="ds-stat">
          <div className="k">Open pipeline</div>
          <div className="v">{money(data.open_pipeline)}</div>
          <div className="s">{data.open_count} deals</div>
        </div>
        <div className="ds-stat">
          <div className="k">Weighted projection</div>
          <div className="v">{money(data.weighted_projection)}</div>
          <div className="s">by stage probability</div>
        </div>
        <div className="ds-stat">
          <div className="k">Closed</div>
          <div className="v">{money(data.closed_value)}</div>
          <div className="s">{data.closed_count} customers</div>
        </div>
      </div>

      {showWeights && (
        <div className="ds-ok" style={{ marginTop: 14 }}>
          <div style={{ marginBottom: 8 }}>
            The projection is each open deal's value times its stage's
            probability. The weights are:
          </div>
          {(data.weights || []).map(w => (
            <div key={w.stage} style={{ fontSize: 12 }}>
              {w.label} — {Math.round(w.weight * 100)}%
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        {(data.by_stage || []).map(s => (
          <div key={s.stage} className="ds-row" style={{ cursor: 'default' }}>
            <span className="ds-pill">{s.count}</span>
            <span><div className="name">{s.label}</div></span>
            <span className="spacer" />
            <span style={{ color: 'var(--ds-dim)', fontSize: 12 }}>{money(s.value)}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}

function CustomersPanel ({ data, onExplain }) {
  return (
    <Card title={`${data.brand} — customers`}
          note={`${data.needing_attention} needing attention`}
          help="executive_authority" onExplain={onExplain}>
      {(data.customers || []).map((c, i) => (
        <div key={i} className="ds-row" style={{ cursor: 'default' }}>
          <span className={'ds-pill ' + (c.attention ? 'warm' : 'ok')}>{c.state}</span>
          <span>
            <div className="name">{c.company}</div>
            <div className="meta">
              {c.industry}{c.contact ? ` · ${c.contact}` : ''}
              {c.note ? ` · ${c.note}` : ''}
            </div>
          </span>
          <span className="spacer" />
          <span style={{ color: 'var(--ds-dim)', fontSize: 12 }}>
            {c.value != null ? money(c.value) : ''}
          </span>
        </div>
      ))}
    </Card>
  )
}

function LaunchPanel ({ data, onExplain }) {
  return (
    <Card title="Customer launch"
          note={data.customer ? `${data.customer} · ${data.completed}/${data.total} complete`
                              : 'no won customer yet'}
          help="customer_launch" onExplain={onExplain}>
      <div className="ds-progress">
        <i style={{ width: `${data.total ? (data.completed / data.total) * 100 : 0}%` }} />
      </div>
      {(data.steps || []).map(s => (
        <div key={s.key} className="ds-row" style={{ cursor: 'default' }}>
          <span className={'ds-pill ' + (s.state === 'complete' ? 'ok'
            : s.state === 'in_progress' ? 'warm' : '')}>
            {s.state.replace('_', ' ')}
          </span>
          <span>
            <div className="name">{s.title}</div>
            <div className="meta">{s.detail}</div>
          </span>
          <span className="spacer" />
          <span className="ds-pill">{s.owner}</span>
        </div>
      ))}
      <p style={{ marginTop: 14, fontSize: 11, color: 'var(--ds-ghost)' }}>{data.source}</p>
    </Card>
  )
}

/* ══ THE COACH ════════════════════════════════════════════════════════════ */

function Coach ({ collapsed, setCollapsed, scenarios, scenarioKey, setScenarioKey,
                  session, busy, onRunStep, onMarkStep, onResetSession, onExplain,
                  onGoToPanel }) {
  const [openStep, setOpenStep] = useState(null)

  const steps = session?.steps || []
  const current = useMemo(() => {
    if (openStep != null) return steps[openStep]
    return steps.find(s => s.current) || steps[0]
  }, [steps, openStep])

  useEffect(() => { setOpenStep(null) }, [scenarioKey])

  if (collapsed) {
    return (
      <aside className="ds-coach collapsed">
        <button className="ds-btn small" title="Open the presenter coach"
                onClick={() => setCollapsed(false)}
                style={{ writingMode: 'vertical-rl', padding: '14px 6px' }}>
          COACH
        </button>
      </aside>
    )
  }

  const done = steps.filter(s => s.done).length

  return (
    <aside className="ds-coach">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <div style={{ flex: 1, color: 'var(--ds-ghost)', fontSize: 10,
                      letterSpacing: '.16em', textTransform: 'uppercase' }}>
          Presenter coach
        </div>
        <button className="ds-btn small ghost" onClick={() => setCollapsed(true)}
                title="Give the prospect a clean screen">HIDE</button>
      </div>

      <select className="ds-select" style={{ width: '100%' }}
              value={scenarioKey} onChange={e => setScenarioKey(e.target.value)}>
        {(scenarios || []).map(s => (
          <option key={s.key} value={s.key}>
            {s.name} · {s.minutes} min
          </option>
        ))}
      </select>

      {session && (
        <>
          <div className="ds-progress" style={{ marginTop: 12 }}>
            <i style={{ width: `${steps.length ? (done / steps.length) * 100 : 0}%` }} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--ds-ghost)', marginBottom: 12 }}>
            {done} of {steps.length} · {session.session.status}
          </div>

          {done === 0 && session.scenario.opening && (
            <div className="ds-ok" style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 10, letterSpacing: '.12em', textTransform: 'uppercase',
                            marginBottom: 6, color: 'var(--ds-teal)' }}>Open with</div>
              {session.scenario.opening}
            </div>
          )}

          {current && (
            <div className="ds-card" style={{ padding: 14, marginBottom: 14 }}>
              <div className="n" style={{ color: 'var(--ds-ghost)', fontSize: 10,
                                          letterSpacing: '.1em', textTransform: 'uppercase' }}>
                Step {current.index + 1} of {steps.length}
              </div>
              <div style={{ color: '#fff', fontSize: 14, margin: '4px 0 10px' }}>
                {current.label}
              </div>

              <div className="field"><div className="k" style={{ color: 'var(--ds-blue)',
                fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase' }}>Show</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{current.what_we_show}</div>
              </div>
              <div className="field" style={{ marginTop: 12 }}>
                <div style={{ color: 'var(--ds-blue)', fontSize: 10, letterSpacing: '.14em',
                              textTransform: 'uppercase' }}>Where</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{current.where_to_click}</div>
              </div>
              <div className="field" style={{ marginTop: 12 }}>
                <div style={{ color: 'var(--ds-blue)', fontSize: 10, letterSpacing: '.14em',
                              textTransform: 'uppercase' }}>What happens</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{current.what_happens}</div>
              </div>
              <div className="field" style={{ marginTop: 12 }}>
                <div style={{ color: 'var(--ds-blue)', fontSize: 10, letterSpacing: '.14em',
                              textTransform: 'uppercase' }}>Why it matters</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{current.why_it_matters}</div>
              </div>
              <div className="field" style={{ marginTop: 12 }}>
                <div style={{ color: 'var(--ds-blue)', fontSize: 10, letterSpacing: '.14em',
                              textTransform: 'uppercase' }}>They should notice</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                  {current.prospect_should_notice}
                </div>
              </div>

              <div className="ds-say">“{current.presenter_says}”</div>

              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 14 }}>
                <button className="ds-btn small ghost"
                        onClick={() => onGoToPanel(current.panel)}>
                  GO TO {current.panel_label.toUpperCase()}
                </button>
                {current.action ? (
                  <button className="ds-btn primary small" disabled={busy}
                          onClick={() => onRunStep(current)}>
                    {busy ? 'WORKING…' : 'RUN THIS STEP'}
                  </button>
                ) : (
                  <button className="ds-btn small" disabled={busy || current.done}
                          onClick={() => onMarkStep(current)}>
                    {current.done ? 'DONE' : 'MARK AS SHOWN'}
                  </button>
                )}
                {current.help_key &&
                  <button className="ds-btn small ghost"
                          onClick={() => onExplain(current.help_key)}>EXPLAIN</button>}
              </div>

              <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                <button className="ds-btn small ghost" disabled={current.index === 0}
                        onClick={() => setOpenStep(Math.max(0, current.index - 1))}>← BACK</button>
                <button className="ds-btn small ghost"
                        disabled={current.index >= steps.length - 1}
                        onClick={() => setOpenStep(Math.min(steps.length - 1, current.index + 1))}>
                  NEXT →
                </button>
              </div>

              {current.deep_dive && (
                <details style={{ marginTop: 12 }}>
                  <summary style={{ cursor: 'pointer', color: 'var(--ds-dim)', fontSize: 11 }}>
                    Optional deep dive
                  </summary>
                  <div style={{ fontSize: 12, lineHeight: 1.6, marginTop: 8,
                                color: 'var(--ds-dim)' }}>{current.deep_dive}</div>
                </details>
              )}
            </div>
          )}

          <div style={{ color: 'var(--ds-ghost)', fontSize: 10, letterSpacing: '.14em',
                        textTransform: 'uppercase', margin: '6px 0 8px' }}>
            Running order
          </div>
          {steps.map(s => (
            <button key={s.key}
                    className={'ds-step' + (s.done ? ' done' : '') +
                               (current && s.index === current.index ? ' on' : '')}
                    onClick={() => setOpenStep(s.index)}>
              <div className="n">{s.done ? '✓ ' : ''}Step {s.index + 1} · {s.panel_label}</div>
              <div className="t">{s.label}</div>
            </button>
          ))}

          {done === steps.length && steps.length > 0 && session.scenario.closing && (
            <div className="ds-ok" style={{ marginTop: 12 }}>
              <div style={{ fontSize: 10, letterSpacing: '.12em', textTransform: 'uppercase',
                            marginBottom: 6, color: 'var(--ds-teal)' }}>Close with</div>
              {session.scenario.closing}
            </div>
          )}

          <button className="ds-btn ghost small" style={{ marginTop: 14, width: '100%' }}
                  onClick={onResetSession} disabled={busy}>
            RESET MY RUN-THROUGH
          </button>
          <p style={{ fontSize: 10.5, color: 'var(--ds-ghost)', marginTop: 8, lineHeight: 1.5 }}>
            This puts your own step counter back to the start. It does not touch
            anybody else's demonstration or the seeded records.
          </p>
        </>
      )}
    </aside>
  )
}

/* ══ THE SCREEN ═══════════════════════════════════════════════════════════ */

export default function DemoSuite () {
  const { platformId } = useParams()
  const navigate = useNavigate()

  const [access, setAccess] = useState(null)
  const [world, setWorld] = useState(null)
  const [panel, setPanel] = useState('priority')
  const [leadKey, setLeadKey] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [flash, setFlash] = useState('')
  const [loading, setLoading] = useState(true)

  const [scenarios, setScenarios] = useState([])
  const [scenarioKey, setScenarioKey] = useState('lead_to_appointment')
  const [session, setSession] = useState(null)
  const [collapsed, setCollapsed] = useState(false)
  const [help, setHelp] = useState(null)
  const [railOpen, setRailOpen] = useState(false)

  /* which brands may this person present */
  useEffect(() => {
    let cancelled = false
    api.get('/demo-suite/me')
      .then(a => { if (!cancelled) setAccess(a) })
      .catch(e => { if (!cancelled) setErr(e?.message || 'Could not check your access.') })
    return () => { cancelled = true }
  }, [])

  const loadWorld = useCallback(async () => {
    if (!platformId) { setLoading(false); return }
    setLoading(true); setErr('')
    try {
      const w = await api.get('/demo-suite/' + platformId + '/world'
        + (leadKey ? '?lead=' + encodeURIComponent(leadKey) : ''))
      setWorld(w)
    } catch (e) {
      setErr(e?.message || 'Could not open this demonstration.')
      setWorld(null)
    } finally { setLoading(false) }
  }, [platformId, leadKey])
  useEffect(() => { loadWorld() }, [loadWorld])

  useEffect(() => {
    if (!platformId) return
    api.get('/demo-suite/' + platformId + '/scenarios')
      .then(s => setScenarios(s.scenarios || []))
      .catch(() => setScenarios([]))
  }, [platformId, world?.environment?.id])

  const loadSession = useCallback(async () => {
    if (!platformId || !scenarioKey) return
    try {
      setSession(await api.get('/demo-suite/' + platformId + '/scenarios/' + scenarioKey))
    } catch { setSession(null) }
  }, [platformId, scenarioKey])
  useEffect(() => { loadSession() }, [loadSession])

  const act = useCallback(async (action, params = {}, step = null) => {
    setBusy(true); setErr(''); setFlash('')
    try {
      const out = await api.post('/demo-suite/' + platformId + '/action', {
        action, params,
        scenario: step ? scenarioKey : null,
        step: step ? step.key : null,
      })
      if (out.panels) setWorld(w => w ? { ...w, panels: out.panels } : w)
      if (out.narration) setFlash(out.narration)
      if (params.target) setLeadKey(k => k)
      await loadSession()
    } catch (e) {
      setErr(e?.message || 'That action could not be completed.')
    } finally { setBusy(false) }
  }, [platformId, scenarioKey, loadSession])

  const runStep = useCallback(async (step) => {
    if (!step.action) return
    setPanel(step.panel)
    const params = { ...step.action }
    delete params.action
    if (params.target && step.panel === 'conversation') setLeadKey(params.target)
    await act(step.action.action, params, step)
  }, [act])

  const markStep = useCallback(async (step) => {
    setBusy(true); setErr('')
    try {
      setSession(await api.post(
        '/demo-suite/' + platformId + '/scenarios/' + scenarioKey + '/step',
        { step: step.key }))
      setPanel(step.panel)
    } catch (e) { setErr(e?.message || 'Could not record that step.') }
    finally { setBusy(false) }
  }, [platformId, scenarioKey])

  const resetSession = useCallback(async () => {
    setBusy(true)
    try {
      setSession(await api.post(
        '/demo-suite/' + platformId + '/scenarios/' + scenarioKey + '/reset'))
      setFlash('Your run-through is back at step one.')
    } catch (e) { setErr(e?.message || 'Could not reset.') }
    finally { setBusy(false) }
  }, [platformId, scenarioKey])

  const rebuild = useCallback(async () => {
    if (!window.confirm(
      'Rebuild this brand\'s demonstration world?\n\n' +
      'This re-seeds the shared environment for everybody presenting this ' +
      'brand — including anybody mid-meeting. It cannot affect any real ' +
      'customer.')) return
    setBusy(true); setErr('')
    try {
      await api.post('/demo-suite/' + platformId + '/rebuild')
      setFlash('The demonstration world has been rebuilt.')
      await loadWorld(); await loadSession()
    } catch (e) { setErr(e?.message || 'Could not rebuild.') }
    finally { setBusy(false) }
  }, [platformId, loadWorld, loadSession])

  const openHelp = useCallback(async (key) => {
    try { setHelp(await api.get('/demo-suite/help/' + key)) }
    catch { setHelp({ title: 'Not available', what_it_is: 'No help for this yet.' }) }
  }, [])

  const pickLead = useCallback((key) => {
    setLeadKey(key)
    setPanel('conversation')
  }, [])

  /* ── brand chooser ── */
  if (!platformId) {
    return (
      <div className="ds-scope">
        <DemoStyles />
        <div style={{ maxWidth: 760, margin: '0 auto', padding: '60px 24px' }}>
          <h1 className="ds-title">Demo Suite</h1>
          <p className="ds-sub">
            Choose the brand you are presenting. Everything inside is seeded
            fictional data in an isolated environment — no customer's records
            are reachable from here, and nothing sends.
          </p>
          {err && <div className="ds-error" style={{ marginTop: 20 }}>{err}</div>}
          {!access && !err && <div className="ds-loading">Checking your access…</div>}
          {access && access.brands.length === 0 && (
            <div className="ds-empty" style={{ marginTop: 24 }}>
              You do not have Demo Suite access for any brand yet. The platform
              owner grants it from God Mode → Manage Access.
            </div>
          )}
          <div style={{ marginTop: 24 }}>
            {access?.brands.map(b => (
              <button key={b.platform_id} className="ds-row"
                      onClick={() => navigate('/demo-suite/' + b.platform_id)}>
                <span className={'ds-pill ' + (b.environment_ready ? 'ok' : 'warm')}>
                  {b.environment_ready ? 'ready' : b.environment_status}
                </span>
                <span>
                  <div className="name">{b.platform_name}</div>
                  <div className="meta">
                    {b.environment_ready
                      ? 'Seeded and ready to present'
                      : 'The environment has not been built yet'}
                    {b.may_admin ? ' · you can rebuild it' : ''}
                  </div>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  const panels = world?.panels
  const grouped = PANELS.reduce((acc, p) => {
    (acc[p.group] = acc[p.group] || []).push(p); return acc
  }, {})

  return (
    <div className="ds-scope">
      <DemoStyles />
      <div className="ds-shell">
        <aside className={'ds-rail' + (railOpen ? ' open' : '')}>
          <div className="ds-brand">
            <div className="ds-brand-name">{world?.brand?.name || 'Demo Suite'}</div>
            <div className="ds-brand-sub">Demonstration</div>
          </div>
          {Object.entries(grouped).map(([group, items]) => (
            <div key={group}>
              <div className="ds-navhead">{group}</div>
              {items.map(p => (
                <button key={p.key}
                        className={'ds-nav' + (panel === p.key ? ' on' : '')}
                        onClick={() => { setPanel(p.key); setRailOpen(false) }}>
                  <span className="ds-nav-dot" />
                  <span style={{ flex: 1 }}>{p.label}</span>
                </button>
              ))}
            </div>
          ))}
          <div style={{ flex: 1 }} />
          <button className="ds-nav" onClick={() => navigate('/demo-suite')}>
            <span className="ds-nav-dot" />
            <span>Change brand</span>
          </button>
          {world?.may_admin && (
            <button className="ds-nav" onClick={rebuild} disabled={busy}>
              <span className="ds-nav-dot" />
              <span>Rebuild the world</span>
            </button>
          )}
        </aside>

        <main className="ds-main">
          <div className="ds-topbar">
            <div>
              <h1 className="ds-title">
                {PANELS.find(p => p.key === panel)?.label}
              </h1>
              <p className="ds-sub">
                {world?.environment?.workspace} · {world?.environment?.sales_org}
              </p>
            </div>
            <span style={{ flex: 1 }} />
            <button className="ds-btn ghost small" onClick={loadWorld} disabled={busy}>
              REFRESH
            </button>
          </div>

          <div className="ds-banner">
            Demonstration environment — seeded fictional records. Nothing here
            sends a message, charges a card or touches a customer's data.
            {world?.environment?.stale && ' This world was built from an older seed; rebuilding it is worth doing before a meeting.'}
          </div>

          {err && <div className="ds-error" style={{ marginBottom: 14 }}>{err}</div>}
          {flash && (
            <div className="ds-ok" style={{ marginBottom: 14, display: 'flex', gap: 10 }}>
              <span style={{ flex: 1 }}>{flash}</span>
              <button className="ds-btn small ghost" onClick={() => setFlash('')}>DISMISS</button>
            </div>
          )}

          {loading && <div className="ds-loading">Opening the demonstration…</div>}

          {!loading && panels && (
            <>
              {panel === 'priority' &&
                <PriorityPanel data={panels.priority} act={act} busy={busy}
                               onExplain={openHelp} onPickLead={pickLead} />}
              {panel === 'leads' &&
                <LeadsPanel data={panels.leads} onExplain={openHelp} onPickLead={pickLead} />}
              {panel === 'conversation' &&
                <ConversationPanel data={panels.conversation} act={act} busy={busy}
                                   onExplain={openHelp} onPickLead={pickLead} />}
              {panel === 'calendar' &&
                <CalendarPanel data={panels.calendar} onExplain={openHelp}
                               onPickLead={pickLead} />}
              {panel === 'pipeline' &&
                <PipelinePanel data={panels.pipeline} act={act} busy={busy}
                               onExplain={openHelp} />}
              {panel === 'team' &&
                <TeamPanel data={panels.team} act={act} busy={busy} onExplain={openHelp} />}
              {panel === 'revenue' &&
                <RevenuePanel data={panels.revenue} onExplain={openHelp} />}
              {panel === 'customers' &&
                <CustomersPanel data={panels.customers} onExplain={openHelp} />}
              {panel === 'launch' &&
                <LaunchPanel data={panels.launch} onExplain={openHelp} />}
            </>
          )}
        </main>

        <Coach collapsed={collapsed} setCollapsed={setCollapsed}
               scenarios={scenarios} scenarioKey={scenarioKey}
               setScenarioKey={setScenarioKey} session={session} busy={busy}
               onRunStep={runStep} onMarkStep={markStep}
               onResetSession={resetSession} onExplain={openHelp}
               onGoToPanel={setPanel} />
      </div>

      {help && (
        <>
          <div className="ds-scrim" onClick={() => setHelp(null)} />
          <div className="ds-drawer">
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <h3 style={{ flex: 1 }}>{help.title}</h3>
              <button className="ds-btn small ghost" onClick={() => setHelp(null)}>CLOSE</button>
            </div>
            {help.what_it_is && (
              <div className="field"><div className="k">What it is</div>
                <div className="v">{help.what_it_is}</div></div>)}
            {help.what_it_does && (
              <div className="field"><div className="k">What it does</div>
                <div className="v">{help.what_it_does}</div></div>)}
            {help.why_customer_cares && (
              <div className="field"><div className="k">Why the customer cares</div>
                <div className="v">{help.why_customer_cares}</div></div>)}
            {help.what_happens_next && (
              <div className="field"><div className="k">What happens next</div>
                <div className="v">{help.what_happens_next}</div></div>)}
            {help.presenter_says && (
              <div className="field"><div className="k">You can say</div>
                <div className="ds-say">“{help.presenter_says}”</div></div>)}
          </div>
        </>
      )}
    </div>
  )
}
