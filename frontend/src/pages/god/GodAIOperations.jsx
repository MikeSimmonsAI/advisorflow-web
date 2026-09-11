/*
 * GOD MODE → AI OPERATIONS. The platform-level view of the AI workforce.
 *
 * FOUR QUESTIONS, AND NOTHING ELSE.
 *
 *   1. Why is nothing sending? The dark-launch switches, the adapter each
 *      channel would actually resolve to, which T6 contracts this deployment
 *      has, and which tool authority every operation consumes. Stated as
 *      fact rather than inferred from an empty outbox.
 *   2. What is one customer's AI workforce doing? The supervisor read, by
 *      organization id, because God Mode is the one authority that may ask
 *      about a customer it is not standing inside.
 *   3. What arrived that nobody could place? Inbound events with no tenant.
 *   4. Does any of it work? The synthetic proofs and the evaluation harness.
 *
 * WHAT IS DELIBERATELY NOT HERE. No control. Nothing on this page makes an
 * AI employee send anything, and there is no endpoint that would let it.
 * The tenant controls — take over, release, stop — live on the customer
 * surface where the person who owns the conversation can reach them.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'

const TABS = [
  { key: 'platform', label: 'PLATFORM STATE' },
  { key: 'supervisor', label: 'SUPERVISOR' },
  { key: 'unrouted', label: 'UNROUTED INBOUND' },
  { key: 'proofs', label: 'PROOFS' },
]

// The reactivation scenarios the simulator knows. Sent as an optional
// `scenario`; omitting it runs every one of them.
const SCENARIOS = ['appointment', 'opt_out', 'no_response', 'handoff', 'blocked']

// The step keys the transcript always carries. Anything else a step recorded
// is rendered in the extras column rather than dropped — a proof that hides
// half of what it observed is not a proof.
const STEP_KEYS = ['step', 'ok', 'denial_code', 'denial_reason', 'decided_by',
                   'simulated', 'communication_state', 'channel', 'provider']

function when(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

function fmt(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function money(value) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return Number.isNaN(n) ? '—' : `$${n.toFixed(2)}`
}

function Pill({ tone = 'muted', children }) {
  const colors = {
    ok: ['var(--gm-teal)', 'var(--gm-teal-wash)'],
    warn: ['var(--gm-amber)', 'var(--gm-amber-wash)'],
    bad: ['var(--gm-red)', 'var(--gm-red-wash)'],
    info: ['var(--gm-blue)', 'var(--gm-blue-wash)'],
    muted: ['var(--gm-blue)', 'var(--gm-blue-wash)'],
  }
  const [fg, bg] = colors[tone] || colors.muted
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 999,
      fontSize: 11, fontWeight: 700, letterSpacing: '.02em',
      color: fg, background: bg, border: `1px solid ${fg}33`,
    }}>{children}</span>
  )
}

function Metric({ label, value, tone, onClick, hint }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={hint}
      className="gm-card"
      style={{
        textAlign: 'left', padding: '13px 15px', border: '1px solid var(--gm-pill-blue-bd)',
        background: 'var(--gm-pill-blue-bg)', borderRadius: 10, cursor: onClick ? 'pointer' : 'default',
        color: 'var(--gm-head)', minWidth: 130,
      }}
    >
      <div style={{
        fontSize: 10.5, letterSpacing: '.09em', textTransform: 'uppercase',
        color: 'var(--gm-blue)', fontWeight: 700,
      }}>{label}</div>
      <div style={{
        fontSize: 25, fontWeight: 700, marginTop: 4, lineHeight: 1,
        color: tone === 'bad' ? 'var(--gm-red)' : tone === 'warn' ? 'var(--gm-amber)' : 'var(--gm-head)',
      }}>{value === null || value === undefined ? '—' : value}</div>
    </button>
  )
}

function Section({ title, subtitle, right, children }) {
  return (
    <div className="gm-card" style={{
      border: '1px solid var(--gm-pill-blue-bd)', background: 'var(--gm-pill-blue-bg)', borderRadius: 12,
      marginBottom: 16, overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        gap: 12, padding: '13px 16px', borderBottom: '1px solid var(--gm-pill-blue-bd)',
      }}>
        <div>
          <div style={{ color: 'var(--gm-head)', fontWeight: 700, fontSize: 13.5 }}>{title}</div>
          {subtitle && (
            <div style={{ color: 'var(--gm-blue)', fontSize: 12, marginTop: 2 }}>{subtitle}</div>
          )}
        </div>
        {right}
      </div>
      {children}
    </div>
  )
}

function Empty({ children }) {
  return (
    <div style={{ padding: '26px 16px', color: 'var(--gm-blue)', fontSize: 12.5 }}>
      {children}
    </div>
  )
}

function Fact({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 10.5, color: 'var(--gm-blue)', fontWeight: 700,
                    letterSpacing: '.08em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ color: 'var(--gm-blue)', fontSize: 13, marginTop: 3 }}>{value}</div>
    </div>
  )
}

/* Three states, never collapsed: null is loading, an empty list is a
   sentence that says what being empty MEANS, and rows are the table. */
function Rows({ rows, error, empty, head, children }) {
  if (error) return <Empty>{error}</Empty>
  if (rows === null || rows === undefined) return <Empty>Loading…</Empty>
  if (rows.length === 0) return <Empty>{empty}</Empty>
  return (
    <table className="gm-table">
      <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{rows.map(children)}</tbody>
    </table>
  )
}

export default function GodAIOperations() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('platform')
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    api.get('/god/ai-operations/state')
      .then((d) => { setState(d); setError(null) })
      .catch((e) => setError(e.detail || 'Could not load the AI operations platform state.'))
  }, [])

  useEffect(() => { load() }, [load])

  const dark = state ? state.dark_launch : null

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{
        position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto',
        padding: '24px 26px 60px',
      }}>
        <div style={{ padding: '8px 2px 20px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }}
                  onClick={() => navigate('/god')}>← COMMAND CENTER</button>
          <h1 style={{
            margin: 0, color: 'var(--gm-head)', fontSize: 27, letterSpacing: '-.04em',
            lineHeight: 1,
          }}>AI Operations</h1>
          <p style={{ margin: '9px 0 0', color: 'var(--gm-blue)', fontSize: 12, maxWidth: 820 }}>
            The platform-level view of the AI workforce: which brakes are on,
            what each channel would resolve to, what one customer&apos;s
            employees are doing, the inbound nobody could place, and the
            synthetic proofs. {dark && (
              <strong style={{ color: 'var(--gm-blue)' }}>
                {dark.live_send_enabled
                  ? 'Live sending is enabled in this deployment.'
                  : 'Nothing reaches a real person in this deployment.'}
              </strong>
            )}
          </p>
        </div>

        <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className="gm-btn"
              onClick={() => setTab(t.key)}
              style={{
                borderColor: tab === t.key ? 'var(--gm-blue)' : undefined,
                color: tab === t.key ? 'var(--gm-head)' : undefined,
              }}
            >{t.label}</button>
          ))}
        </div>

        {tab === 'platform' && <PlatformTab state={state} error={error} />}
        {tab === 'supervisor' && <SupervisorTab />}
        {tab === 'unrouted' && <UnroutedTab />}
        {tab === 'proofs' && <ProofsTab />}
      </div>
    </div>
  )
}

/* ── PLATFORM STATE ────────────────────────────────────────────────────── */

// The order the report reads them in, not the order a dictionary happens to
// iterate: this is the dependency chain, from the tables up to the queue.
const CONTRACT_ORDER = ['models', 'constants', 'registry', 'policy',
                        'activation', 'eligibility', 'queue']

const CONTRACT_LABEL = {
  models: 'Workforce models',
  constants: 'Workforce constants',
  registry: 'Tool registry',
  policy: 'Policy',
  activation: 'Activation',
  eligibility: 'Eligibility',
  queue: 'Queue',
}

function Switch({ label, on, warnWhenOn, onLabel = 'ON', offLabel = 'OFF', note }) {
  return (
    <div style={{ minWidth: 190 }}>
      <div style={{ fontSize: 10.5, color: 'var(--gm-blue)', fontWeight: 700,
                    letterSpacing: '.08em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ marginTop: 5 }}>
        <Pill tone={on ? (warnWhenOn ? 'warn' : 'info') : 'ok'}>
          {on ? onLabel : offLabel}
        </Pill>
      </div>
      {note && (
        <div style={{ color: 'var(--gm-blue)', fontSize: 11.5, marginTop: 5, maxWidth: 240 }}>
          {note}
        </div>
      )}
    </div>
  )
}

function PlatformTab({ state, error }) {
  if (error) return <Section title="Not available"><Empty>{error}</Empty></Section>
  if (!state) return <Section title="Platform state"><Empty>Loading…</Empty></Section>

  const dark = state.dark_launch || {}
  const providers = state.providers || {}
  const channels = providers.channels || {}
  const contracts = state.workforce_contracts || {}
  const operations = state.operations || []
  const authority = state.operation_authority || {}
  const variables = Object.entries(dark.variables || {})
  const channelRows = Object.keys(channels)

  return (
    <>
      <Section
        title="Dark launch"
        subtitle="Three independent brakes, read from the environment. Nothing here can be released by a customer, a brand or an employee."
      >
        <div style={{
          display: 'flex', gap: 20, flexWrap: 'wrap', padding: '14px 16px',
          borderBottom: '1px solid var(--gm-pill-blue-bd)',
        }}>
          <Switch label="Operations enabled" on={!!dark.operations_enabled}
                  note="With this off, every operation refuses before any other question is asked." />
          <Switch label="Live send enabled" on={!!dark.live_send_enabled} warnWhenOn
                  note="On means a real provider adapter can be resolved. Dark is the intended state." />
          <Switch label="Live voice enabled" on={!!dark.live_voice_enabled} warnWhenOn
                  note="On means an AI voice call can be placed in this deployment." />
          <Switch label="Kill engaged" on={!!dark.kill_engaged} warnWhenOn
                  onLabel="ENGAGED" offLabel="NOT ENGAGED"
                  note="The environment-level brake, checked first, everywhere." />
        </div>
        <div style={{ padding: '13px 16px', color: 'var(--gm-blue)', fontSize: 13,
                      borderBottom: '1px solid var(--gm-pill-blue-bd)' }}>
          {dark.explanation || 'The deployment returned no explanation.'}
        </div>
        <Rows
          rows={variables}
          empty="The state carried no environment variables."
          head={['Variable', 'Value as set']}
        >
          {([name, value]) => (
            <tr key={name}>
              <td style={{ fontFamily: 'monospace' }}>{name}</td>
              <td>{value === null || value === undefined
                ? <Pill>UNSET</Pill>
                : <span style={{ fontFamily: 'monospace' }}>{String(value)}</span>}</td>
            </tr>
          )}
        </Rows>
      </Section>

      <Section
        title="Providers per channel"
        subtitle="What each channel would resolve to right now. While live sending is off, the simulated adapter answers for every channel and every organization."
        right={
          <div style={{ display: 'flex', gap: 8 }}>
            <Pill tone={providers.live_send_enabled ? 'warn' : 'ok'}>
              {providers.live_send_enabled ? 'LIVE SEND ON' : 'LIVE SEND OFF'}
            </Pill>
            <Pill tone={providers.live_voice_enabled ? 'warn' : 'ok'}>
              {providers.live_voice_enabled ? 'LIVE VOICE ON' : 'LIVE VOICE OFF'}
            </Pill>
          </div>
        }
      >
        <Rows
          rows={channelRows}
          empty="No channel is registered in this deployment."
          head={['Channel', 'Live adapter', 'Simulated adapter', 'Resolves to']}
        >
          {(channel) => (
            <tr key={channel}>
              <td>{channel}</td>
              <td style={{ fontFamily: 'monospace' }}>
                {fmt((channels[channel] || {}).live_adapter)}
              </td>
              <td style={{ fontFamily: 'monospace' }}>
                {fmt((channels[channel] || {}).simulated_adapter)}
              </td>
              <td>{providers.live_send_enabled
                ? <Pill tone="warn">LIVE</Pill>
                : <Pill tone="ok">SIMULATED</Pill>}</td>
            </tr>
          )}
        </Rows>
      </Section>

      <Section
        title="Workforce contracts"
        subtitle="Which T6 contracts this deployment actually has. A missing module is a normal state on a branch cut before T6 merged, and it is never a reason to fail open."
      >
        <Rows
          rows={CONTRACT_ORDER.filter((k) => k in contracts)}
          empty="The state reported no workforce contracts."
          head={['Contract', 'Availability']}
        >
          {(key) => (
            <tr key={key}>
              <td>{CONTRACT_LABEL[key] || key}<br />
                <span style={{ fontFamily: 'monospace', color: 'var(--gm-blue)', fontSize: 11 }}>
                  {key}
                </span>
              </td>
              <td>{contracts[key]
                ? <Pill tone="ok">PRESENT</Pill>
                : <Pill tone="warn">ABSENT</Pill>}</td>
            </tr>
          )}
        </Rows>
      </Section>

      <Section
        title="Operation authority"
        subtitle="Every operation and the workforce tool whose authority it consumes. An operation with no tool is unauthorised by construction — the lookup returns nothing and the orchestrator refuses."
      >
        <Rows
          rows={operations}
          empty="No operation is registered in this deployment."
          head={['Operation', 'Tool authority']}
        >
          {(op) => (
            <tr key={op}>
              <td style={{ fontFamily: 'monospace' }}>{op}</td>
              <td>{authority[op]
                ? <span style={{ fontFamily: 'monospace' }}>{authority[op]}</span>
                : <Pill tone="bad">UNAUTHORISED BY CONSTRUCTION</Pill>}</td>
            </tr>
          )}
        </Rows>
      </Section>
    </>
  )
}

/* ── SUPERVISOR ────────────────────────────────────────────────────────── */

/*
 * AN ORGANIZATION ID, TYPED. There is no endpoint on this surface that lists
 * organizations, and a dropdown built from some other console's list would be
 * a second, quietly different answer to "which customers exist". The id is
 * the argument the contract takes, so the id is what this asks for.
 */
function SupervisorTab() {
  const [orgId, setOrgId] = useState('')
  const [asked, setAsked] = useState(null)
  const [payload, setPayload] = useState(undefined)
  const [error, setError] = useState(null)

  function load(event) {
    if (event) event.preventDefault()
    const id = orgId.trim()
    if (!id) return
    setAsked(id)
    setPayload(null)
    setError(null)
    api.get(`/god/ai-operations/supervisor/${encodeURIComponent(id)}`)
      .then((d) => { setPayload(d); setError(null) })
      .catch((e) => {
        setPayload(undefined)
        setError(e.detail || 'Could not load that organization.')
      })
  }

  const overview = payload ? (payload.overview || {}) : null
  const budget = overview && overview.budget ? (overview.budget.organization || {}) : {}

  return (
    <>
      <Section
        title="One customer's AI workforce"
        subtitle="God Mode is the one authority that may ask about a customer it is not standing inside. Every other route on this surface answers only for the caller's own tenant."
      >
        <form onSubmit={load} style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
                                       padding: '13px 16px', alignItems: 'center' }}>
          <input
            className="gm-input"
            style={{ minWidth: 340 }}
            placeholder="Organization id…"
            value={orgId}
            onChange={(e) => setOrgId(e.target.value)}
          />
          <button className="gm-btn" type="submit" disabled={!orgId.trim()}>LOAD</button>
          {asked && (
            <span style={{ color: 'var(--gm-blue)', fontSize: 11.5, fontFamily: 'monospace' }}>
              {asked}
            </span>
          )}
        </form>
        {error && <Empty>{error}</Empty>}
        {!error && payload === undefined && (
          <Empty>Enter an organization id to read its operational state.</Empty>
        )}
        {!error && payload === null && <Empty>Loading…</Empty>}
      </Section>

      {payload && (
        <>
          <div style={{
            display: 'grid', gap: 10, marginBottom: 18,
            gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
          }}>
            <Metric label="Open threads" value={overview.open_threads} />
            <Metric label="Handoffs waiting" value={overview.handoffs_waiting}
                    tone={overview.handoffs_waiting ? 'warn' : undefined} />
            <Metric label="Scheduled pending" value={overview.scheduled_pending} />
            <Metric label="Unrouted inbound 24h" value={overview.unrouted_inbound_24h}
                    tone={overview.unrouted_inbound_24h ? 'warn' : undefined}
                    hint="Inbound for this organization that was not routed." />
            <Metric label="Estimated cost today"
                    value={money(budget.estimated_cost_usd)}
                    hint={budget.cost_ceiling_usd !== undefined
                      ? `Operational ceiling ${money(budget.cost_ceiling_usd)}.`
                      : undefined} />
          </div>

          <Section title="Active objectives"
                   subtitle="Conversations in flight, newest activity first.">
            <Rows rows={payload.active || []}
                  empty="No conversation is open for this customer."
                  head={['Thread', 'Employee', 'Objective', 'State', 'Channel',
                         'Out', 'In', 'Next action', 'Updated']}>
              {(t) => (
                <tr key={t.thread_id}>
                  <td style={{ fontFamily: 'monospace' }}>{t.thread_id}</td>
                  <td>{fmt(t.employee_id)}</td>
                  <td style={{ maxWidth: 320 }}>{fmt(t.objective)}</td>
                  <td><Pill tone="info">{fmt(t.state)}</Pill></td>
                  <td>{fmt(t.last_channel)}</td>
                  <td>{t.outbound}</td>
                  <td>{t.inbound}</td>
                  <td>{when(t.next_action_at)}</td>
                  <td>{when(t.updated_at)}</td>
                </tr>
              )}
            </Rows>
          </Section>

          <Section title="Waiting"
                   subtitle="Sent, and waiting on the other side to answer.">
            <Rows rows={payload.waiting || []}
                  empty="Nothing is waiting on a reply."
                  head={['Thread', 'Employee', 'State', 'Reason', 'Last outbound',
                         'Last inbound']}>
              {(t) => (
                <tr key={t.thread_id}>
                  <td style={{ fontFamily: 'monospace' }}>{t.thread_id}</td>
                  <td>{fmt(t.employee_id)}</td>
                  <td>{fmt(t.state)}</td>
                  <td style={{ maxWidth: 320 }}>{fmt(t.state_reason)}</td>
                  <td>{when(t.last_outbound_at)}</td>
                  <td>{when(t.last_inbound_at)}</td>
                </tr>
              )}
            </Rows>
          </Section>

          <Section title="Scheduled"
                   subtitle="Work the engine will pick up, and when.">
            <Rows rows={payload.scheduled || []}
                  empty="Nothing is scheduled."
                  head={['Action', 'Operation', 'Channel', 'Thread', 'Status',
                         'Due', 'Attempt', 'Reason']}>
              {(a) => (
                <tr key={a.scheduled_action_id}>
                  <td style={{ fontFamily: 'monospace' }}>{a.scheduled_action_id}</td>
                  <td>{fmt(a.operation)}</td>
                  <td>{fmt(a.channel)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(a.thread_id)}</td>
                  <td>{fmt(a.status)}</td>
                  <td>{when(a.scheduled_for)}</td>
                  <td>{a.attempt} / {a.max_attempts}</td>
                  <td style={{ maxWidth: 300 }}>{fmt(a.reason)}</td>
                </tr>
              )}
            </Rows>
          </Section>

          <Section title="Blocked"
                   subtitle="Everything stuck, and why. Blocked and review-required are listed together because to an operator they are one question.">
            <Rows rows={payload.blocked || []}
                  empty="Nothing is blocked or waiting for review."
                  head={['Thread', 'State', 'Reason', 'Employee', 'Subject',
                         'Failures', 'Since']}>
              {(b) => (
                <tr key={b.thread_id}>
                  <td style={{ fontFamily: 'monospace' }}>{b.thread_id}</td>
                  <td><Pill tone="bad">{fmt(b.state)}</Pill></td>
                  <td style={{ maxWidth: 340 }}>{fmt(b.reason)}</td>
                  <td>{fmt(b.employee_id)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(b.subject_id)}</td>
                  <td>{b.consecutive_failures}</td>
                  <td>{when(b.since)}</td>
                </tr>
              )}
            </Rows>
          </Section>

          <Section title="Handoffs"
                   subtitle="Conversations waiting for a person, oldest first. The AI stops and waits rather than competing with whoever it just asked for help.">
            <Rows rows={payload.handoffs || []}
                  empty="Nobody is waiting on a person."
                  head={['Thread', 'Employee', 'Objective', 'Reason', 'Reference',
                         'Waiting since']}>
              {(h) => (
                <tr key={h.thread_id}>
                  <td style={{ fontFamily: 'monospace' }}>{h.thread_id}</td>
                  <td>{fmt(h.employee_id)}</td>
                  <td style={{ maxWidth: 300 }}>{fmt(h.objective)}</td>
                  <td>{fmt(h.reason_label || h.reason)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(h.handoff_ref)}</td>
                  <td>{when(h.waiting_since)}</td>
                </tr>
              )}
            </Rows>
          </Section>

          <Section title="Recent activity"
                   subtitle="The audit trail. Every consequential action, allowed or refused.">
            <Rows rows={payload.recent_activity || []}
                  empty="Nothing has been recorded for this customer."
                  head={['When', 'Event', 'Decision', 'Operation', 'Tool',
                         'Channel', 'Denial', 'Simulated', 'Message']}>
              {(a) => (
                <tr key={a.id}>
                  <td>{when(a.at)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(a.event)}</td>
                  <td>{a.decision === 'denied'
                    ? <Pill tone="bad">DENIED</Pill>
                    : <Pill tone={a.decision ? 'ok' : 'muted'}>{fmt(a.decision)}</Pill>}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(a.operation)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(a.tool_key)}</td>
                  <td>{fmt(a.channel)}</td>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(a.denial_code)}</td>
                  <td>{a.simulated ? <Pill>SIMULATED</Pill> : '—'}</td>
                  <td style={{ maxWidth: 360 }}>{fmt(a.message)}</td>
                </tr>
              )}
            </Rows>
          </Section>
        </>
      )}
    </>
  )
}

/* ── UNROUTED INBOUND ──────────────────────────────────────────────────── */

function UnroutedTab() {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    setRows(null)
    api.get('/god/ai-operations/unrouted')
      .then((d) => { setRows(d.events || []); setError(null) })
      .catch((e) => setError(e.detail || 'Could not load unrouted inbound.'))
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <Section
      title="Inbound that could not be attributed"
      subtitle="An inbound message whose tenant could not be established is recorded here and never routed by guesswork. Showing it to a guess would be the disclosure the router refused to make."
      right={<button className="gm-btn" type="button" onClick={load}>REFRESH</button>}
    >
      <Rows rows={rows} error={error}
            empty="Every inbound message was attributed to a tenant."
            head={['Received', 'Provider', 'Channel', 'Reason', 'Preview',
                   'Opt-out', 'Organization']}>
        {(e) => (
          <tr key={e.event_id}>
            <td>{when(e.received_at)}</td>
            <td>{fmt(e.provider)}</td>
            <td>{fmt(e.channel)}</td>
            <td style={{ maxWidth: 280 }}>{fmt(e.reason)}</td>
            <td style={{ maxWidth: 380 }}>{fmt(e.preview)}</td>
            <td>{e.is_opt_out ? <Pill tone="warn">OPT-OUT</Pill> : '—'}</td>
            <td style={{ fontFamily: 'monospace' }}>{fmt(e.organization_id)}</td>
          </tr>
        )}
      </Rows>
    </Section>
  )
}

/* ── PROOFS ────────────────────────────────────────────────────────────── */

/*
 * BOTH BUTTONS CREATE DATA, AND SAY SO BEFORE THEY ARE PRESSED.
 *
 * `confirm_synthetic_data: true` is the acknowledgement the server demands,
 * and it is sent because the person read the line above the buttons — not
 * because a default made it convenient. The organizations these build are
 * clearly marked and their contacts are invented; nothing either run does
 * can reach a real person.
 */
function ProofsTab() {
  const [profiles, setProfiles] = useState(null)
  const [note, setNote] = useState(null)
  const [profile, setProfile] = useState('reactivation')
  const [scenario, setScenario] = useState('')
  const [simulation, setSimulation] = useState(undefined)
  const [evaluation, setEvaluation] = useState(undefined)
  const [running, setRunning] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.get('/god/ai-operations/profiles')
      .then((d) => { setProfiles(d.profiles || []); setNote(d.note || null) })
      .catch((e) => setError(e.detail || 'Could not load the profiles.'))
  }, [])

  async function runScenario() {
    setRunning('simulate')
    setError(null)
    try {
      const body = { confirm_synthetic_data: true, profile }
      if (profile === 'reactivation' && scenario) body.scenario = scenario
      setSimulation(await api.post('/god/ai-operations/simulate', body))
    } catch (e) {
      setError(e.detail || 'Could not run that scenario.')
    } finally {
      setRunning(null)
    }
  }

  async function runEvaluation() {
    setRunning('evaluate')
    setError(null)
    try {
      setEvaluation(await api.post('/god/ai-operations/evaluate',
                                   { confirm_synthetic_data: true }))
    } catch (e) {
      setError(e.detail || 'Could not run the evaluation.')
    } finally {
      setRunning(null)
    }
  }

  const busy = running !== null
  const reports = simulation ? (simulation.reports || []) : null
  const cases = evaluation ? (evaluation.cases || []) : null

  return (
    <>
      <Section title="Profiles" subtitle={note || undefined}>
        <Rows rows={profiles} error={error && !profiles ? error : null}
              empty="No profile can be built in this deployment."
              head={['Key', 'Profile', 'What it builds']}>
          {(p) => (
            <tr key={p.key}>
              <td style={{ fontFamily: 'monospace' }}>{p.key}</td>
              <td>{p.label}</td>
              <td style={{ maxWidth: 560 }}>{p.description}</td>
            </tr>
          )}
        </Rows>
      </Section>

      <Section
        title="Run a proof"
        subtitle="Both runs go through the real engine and the whole gate chain. What they prove is what the engine actually does, not what a fixture says it does."
      >
        <div style={{ padding: '13px 16px 0', color: 'var(--gm-amber)', fontSize: 12.5 }}>
          Running either of these creates clearly-marked synthetic
          organizations and invented contacts in this deployment. Nothing
          they do can reach a real person.
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
                      padding: '12px 16px 14px' }}>
          <select className="gm-input" value={profile} disabled={busy}
                  onChange={(e) => setProfile(e.target.value)}>
            {(profiles || [{ key: 'reactivation', label: 'Reactivation' }]).map((p) => (
              <option key={p.key} value={p.key}>{p.label}</option>
            ))}
          </select>
          <select className="gm-input" value={scenario} disabled={busy || profile !== 'reactivation'}
                  onChange={(e) => setScenario(e.target.value)}
                  title={profile === 'reactivation'
                    ? 'Leave on every scenario to run all five.'
                    : 'Scenarios apply to the reactivation profile only.'}>
            <option value="">Every scenario</option>
            {SCENARIOS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button className="gm-btn" type="button" disabled={busy} onClick={runScenario}>
            {running === 'simulate' ? 'RUNNING…' : 'RUN SCENARIO'}
          </button>
          <button className="gm-btn" type="button" disabled={busy} onClick={runEvaluation}>
            {running === 'evaluate' ? 'RUNNING…' : 'RUN FULL EVALUATION'}
          </button>
          {busy && (
            <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>
              This takes a few seconds — the engine is running the whole chain.
            </span>
          )}
        </div>
        {error && profiles && <Empty>{error}</Empty>}
      </Section>

      {(running === 'simulate' || simulation) && (
        <Section
          title="Scenario transcript"
          subtitle={simulation && simulation.profile
            ? `${simulation.profile.organization_name} · ${simulation.profile.organization_id}`
            : undefined}
        >
          {running === 'simulate' && <Empty>Running…</Empty>}
          {running !== 'simulate' && reports && reports.length === 0 && (
            <Empty>The run completed and produced no report.</Empty>
          )}
          {running !== 'simulate' && reports && reports.map((r, i) => (
            <Report key={`${r.scenario}-${i}`} report={r} />
          ))}
        </Section>
      )}

      {(running === 'evaluate' || evaluation) && (
        <Section
          title="Adversarial evaluation"
          subtitle={evaluation && evaluation.suite ? String(evaluation.suite) : undefined}
          right={evaluation && (
            <div style={{ display: 'flex', gap: 8 }}>
              <Pill tone="ok">{fmt(evaluation.passed)} PASSED</Pill>
              <Pill tone={evaluation.failed ? 'bad' : 'muted'}>
                {fmt(evaluation.failed)} FAILED
              </Pill>
              <Pill>{fmt(evaluation.total)} CASES</Pill>
            </div>
          )}
        >
          {running === 'evaluate' && <Empty>Running…</Empty>}
          {running !== 'evaluate' && (
            <Rows rows={cases}
                  empty="The harness ran and asserted nothing."
                  head={['Case', 'Dimension', 'Result', 'Expected', 'Actual', 'Detail']}>
              {(c, i) => (
                <tr key={c.key || i}>
                  <td style={{ fontFamily: 'monospace' }}>{fmt(c.key)}</td>
                  <td>{fmt(c.dimension)}</td>
                  <td>{c.passed
                    ? <Pill tone="ok">PASSED</Pill>
                    : <Pill tone="bad">FAILED</Pill>}</td>
                  <td style={{ maxWidth: 240 }}>{fmt(c.expected)}</td>
                  <td style={{ maxWidth: 240 }}>{fmt(c.actual)}</td>
                  <td style={{ maxWidth: 400 }}>{fmt(c.detail)}</td>
                </tr>
              )}
            </Rows>
          )}
        </Section>
      )}
    </>
  )
}

function Report({ report }) {
  const thread = report.thread
  const steps = report.steps || []
  return (
    <div style={{ borderTop: '1px solid var(--gm-pill-blue-bd)', padding: '13px 16px' }}>
      <div style={{ display: 'flex', gap: 9, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'monospace', color: 'var(--gm-head)' }}>{report.scenario}</span>
        <Pill tone="info">{report.profile}</Pill>
        {thread && <Pill>{fmt(thread.state)}</Pill>}
        {thread && thread.stop_reason && <Pill tone="warn">{thread.stop_reason}</Pill>}
        {thread && thread.appointment_ref && <Pill tone="ok">APPOINTMENT BOOKED</Pill>}
      </div>

      {thread ? (
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', margin: '11px 0 4px' }}>
          <Fact label="Thread" value={thread.thread_id} />
          <Fact label="Status" value={fmt(thread.status)} />
          <Fact label="Channels used" value={fmt(thread.channels_used)} />
          <Fact label="Outbound" value={fmt(thread.outbound)} />
          <Fact label="Inbound" value={fmt(thread.inbound)} />
          <Fact label="Appointment" value={fmt(thread.appointment_ref)} />
          <Fact label="Human owner" value={fmt(thread.human_owner_user_id)} />
        </div>
      ) : (
        <div style={{ color: 'var(--gm-blue)', fontSize: 12.5, margin: '9px 0 4px' }}>
          The scenario opened no conversation, which is itself the outcome.
        </div>
      )}

      <div style={{ marginTop: 10 }}>
        <Rows rows={steps}
              empty="The scenario recorded no step."
              head={['Step', 'Result', 'Denial', 'Channel', 'Provider',
                     'Communication', 'Also recorded']}>
          {(s, i) => (
            <tr key={`${s.step}-${i}`}>
              <td style={{ fontFamily: 'monospace' }}>{s.step}</td>
              <td>{s.ok === undefined
                ? '—'
                : s.ok
                  ? <Pill tone="ok">OK</Pill>
                  : <Pill tone="bad">REFUSED</Pill>}</td>
              <td>
                {s.denial_code
                  ? <span style={{ fontFamily: 'monospace' }}>{s.denial_code}</span>
                  : '—'}
                {s.denial_reason && (
                  <div style={{ color: 'var(--gm-blue)', fontSize: 11.5, marginTop: 3,
                                maxWidth: 300 }}>{s.denial_reason}</div>
                )}
              </td>
              <td>{fmt(s.channel)}</td>
              <td>{fmt(s.provider)}</td>
              <td>{fmt(s.communication_state)}</td>
              <td style={{ maxWidth: 360, color: 'var(--gm-blue)', fontSize: 11.5 }}>
                {Object.keys(s).filter((k) => STEP_KEYS.indexOf(k) === -1)
                  .map((k) => `${k}: ${fmt(s[k])}`).join(' · ') || '—'}
              </td>
            </tr>
          )}
        </Rows>
      </div>
    </div>
  )
}
