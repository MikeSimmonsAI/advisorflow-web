/*
 * GOD MODE → SUPPORT. The command centre.
 *
 * THIS IS NOT ANOTHER ROOT SHELL. It renders inside GodShell like every other
 * God section, uses the same gm-* styling, and is reached from the same nav.
 *
 * WHAT IT OPTIMISES FOR: DECISIONS, NOT LOGS.
 * The queue leads with what is past its target and what nobody owns. The
 * incident view leads with a likely root cause and a recommended remediation,
 * not with an error count. The brief leads with the actions somebody should
 * take today. A wall of events would be easier to build and useless to read.
 *
 * SCOPE IS SERVER-DECIDED. A brand support operator and the owner see the
 * same screens; the API returns a narrower set for the operator and the
 * header says which. Nothing here decides what anybody may see.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'

const TABS = [
  { key: 'queue', label: 'QUEUE' },
  { key: 'incidents', label: 'INCIDENTS' },
  { key: 'recurring', label: 'RECURRING' },
  { key: 'brief', label: 'DAILY BRIEF' },
  { key: 'fixer', label: 'AUTO-FIXER' },
  { key: 'config', label: 'CONFIGURATION' },
]

const SLA_TONE = {
  within: 'ok', met: 'ok', at_risk: 'warn', breached: 'bad',
  paused: 'info', not_applicable: 'muted',
}

const RISK_TONE = {
  safe_auto: 'ok', controlled: 'warn', god_approval: 'bad', engineering: 'info',
}

function when(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

function Pill({ tone = 'muted', children }) {
  const colors = {
    ok: ['#19d67c', 'rgba(25,214,124,.13)'],
    warn: ['#f5a524', 'rgba(245,165,36,.13)'],
    bad: ['#ff5c68', 'rgba(255,92,104,.13)'],
    info: ['#3aa0ff', 'rgba(58,160,255,.13)'],
    muted: ['#8fa3b8', 'rgba(143,163,184,.12)'],
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
        textAlign: 'left', padding: '13px 15px', border: '1px solid #1b2838',
        background: '#0b1220', borderRadius: 10, cursor: onClick ? 'pointer' : 'default',
        color: '#fff', minWidth: 130,
      }}
    >
      <div style={{
        fontSize: 10.5, letterSpacing: '.09em', textTransform: 'uppercase',
        color: '#758ba4', fontWeight: 700,
      }}>{label}</div>
      <div style={{
        fontSize: 25, fontWeight: 700, marginTop: 4, lineHeight: 1,
        color: tone === 'bad' ? '#ff5c68' : tone === 'warn' ? '#f5a524' : '#fff',
      }}>{value === null || value === undefined ? '—' : value}</div>
    </button>
  )
}

function Section({ title, subtitle, right, children }) {
  return (
    <div className="gm-card" style={{
      border: '1px solid #1b2838', background: '#0b1220', borderRadius: 12,
      marginBottom: 16, overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        gap: 12, padding: '13px 16px', borderBottom: '1px solid #1b2838',
      }}>
        <div>
          <div style={{ color: '#fff', fontWeight: 700, fontSize: 13.5 }}>{title}</div>
          {subtitle && (
            <div style={{ color: '#758ba4', fontSize: 12, marginTop: 2 }}>{subtitle}</div>
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
    <div style={{ padding: '26px 16px', color: '#758ba4', fontSize: 12.5 }}>
      {children}
    </div>
  )
}

export default function GodSupport() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('queue')
  const [overview, setOverview] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState({})

  const loadOverview = useCallback(() => {
    api.get('/god/support/overview')
      .then((d) => { setOverview(d); setError(null) })
      .catch((e) => setError(e.detail || 'Could not load the support console.'))
  }, [])

  useEffect(() => { loadOverview() }, [loadOverview])

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
            margin: 0, color: '#fff', fontSize: 27, letterSpacing: '-.04em',
            lineHeight: 1,
          }}>Support</h1>
          <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 820 }}>
            Every brand&apos;s support queue, the platform&apos;s own diagnosis of
            what is wrong, what it repaired by itself, and what it wants you to
            decide. {overview && (
              <strong style={{ color: '#9fb4c9' }}>
                Scope: {overview.scope.label}.
              </strong>
            )}
          </p>
        </div>

        {error && (
          <Section title="Not available">
            <Empty>{error}</Empty>
          </Section>
        )}

        {overview && (
          <>
            <div style={{
              display: 'grid', gap: 10, marginBottom: 18,
              gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
            }}>
              <Metric label="Open" value={overview.open_tickets}
                      onClick={() => { setFilter({ status: 'open' }); setTab('queue') }} />
              <Metric label="Critical" value={overview.critical}
                      tone={overview.critical ? 'bad' : undefined}
                      onClick={() => { setFilter({ severity: 'P1' }); setTab('queue') }} />
              <Metric label="High" value={overview.high}
                      onClick={() => { setFilter({ severity: 'P2' }); setTab('queue') }} />
              <Metric label="SLA at risk" value={overview.sla_at_risk}
                      tone={overview.sla_at_risk ? 'warn' : undefined}
                      onClick={() => { setFilter({ sla_state: 'at_risk' }); setTab('queue') }} />
              <Metric label="SLA breached" value={overview.sla_breached}
                      tone={overview.sla_breached ? 'bad' : undefined}
                      onClick={() => { setFilter({ sla_state: 'breached' }); setTab('queue') }} />
              <Metric label="Unassigned" value={overview.unassigned}
                      onClick={() => { setFilter({ unassigned: true }); setTab('queue') }} />
              <Metric label="Waiting on customer" value={overview.waiting_on_customer}
                      onClick={() => { setFilter({ status: 'waiting_on_customer' }); setTab('queue') }} />
              <Metric label="Resolved today" value={overview.resolved_today} />
              <Metric label="Auto-fixed today" value={overview.auto_fixed_today}
                      hint="Verified repairs only — a repair that ran and was not
                            checked is not counted here."
                      onClick={() => setTab('fixer')} />
              <Metric label="Auto-fix failed" value={overview.auto_fix_failed_today}
                      tone={overview.auto_fix_failed_today ? 'warn' : undefined}
                      onClick={() => setTab('fixer')} />
              <Metric label="Awaiting approval" value={overview.awaiting_approval}
                      tone={overview.awaiting_approval ? 'warn' : undefined}
                      onClick={() => setTab('fixer')} />
              <Metric label="Incidents" value={overview.platform_incidents}
                      tone={overview.platform_incidents ? 'warn' : undefined}
                      onClick={() => setTab('incidents')} />
              <Metric label="Recurring" value={overview.recurring_issues}
                      onClick={() => setTab('recurring')} />
              <Metric
                label="Avg first response"
                value={overview.average_first_response_minutes === null
                  ? 'No data'
                  : `${overview.average_first_response_minutes}m`}
                hint="Business minutes. Null rather than zero when nothing has
                      been answered — an average of nothing is not instant."
              />
            </div>

            <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
              {TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className="gm-btn"
                  onClick={() => setTab(t.key)}
                  style={{
                    borderColor: tab === t.key ? '#3aa0ff' : undefined,
                    color: tab === t.key ? '#fff' : undefined,
                  }}
                >{t.label}</button>
              ))}
            </div>

            {tab === 'queue' && <QueueTab filter={filter} setFilter={setFilter}
                                          onChanged={loadOverview} />}
            {tab === 'incidents' && <IncidentsTab onChanged={loadOverview} />}
            {tab === 'recurring' && <RecurringTab />}
            {tab === 'brief' && <BriefTab />}
            {tab === 'fixer' && <FixerTab onChanged={loadOverview} />}
            {tab === 'config' && <ConfigTab />}
          </>
        )}
      </div>
    </div>
  )
}

/* ── QUEUE ─────────────────────────────────────────────────────────────── */

function QueueTab({ filter, setFilter, onChanged }) {
  const [rows, setRows] = useState(null)
  const [openId, setOpenId] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    const params = { ...filter }
    if (!params.status && !params.sla_state && !params.severity && !params.unassigned) {
      params.status = 'open'
    }
    api.get('/god/support/tickets', { params })
      .then((d) => { setRows(d.tickets); setError(null) })
      .catch((e) => setError(e.detail || 'Could not load the queue.'))
  }, [filter])

  useEffect(() => { load() }, [load])

  if (openId) {
    return (
      <TicketDetail
        ticketId={openId}
        onBack={() => { setOpenId(null); load(); onChanged() }}
      />
    )
  }

  return (
    <Section
      title="Queue"
      subtitle="Worst first. Every metric above filters this list."
      right={
        <button className="gm-btn" type="button" onClick={() => setFilter({})}>
          CLEAR FILTERS
        </button>
      }
    >
      {error && <Empty>{error}</Empty>}
      {!error && rows === null && <Empty>Loading…</Empty>}
      {!error && rows && rows.length === 0 && <Empty>Nothing matches.</Empty>}
      {!error && rows && rows.length > 0 && (
        <table className="gm-table">
          <thead>
            <tr>
              <th>Reference</th><th>Customer</th><th>Subject</th><th>Severity</th>
              <th>Queue</th><th>Status</th><th>SLA</th><th>Raised</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} style={{ cursor: 'pointer' }} onClick={() => setOpenId(t.id)}>
                <td style={{ fontFamily: 'monospace' }}>{t.ticket_number}</td>
                <td>{t.organization_name || '—'}</td>
                <td>{t.subject}</td>
                <td>{t.severity_label}</td>
                <td>{t.queue_label}</td>
                <td>{t.status_label}</td>
                <td><Pill tone={SLA_TONE[t.sla_state]}>{t.sla_label}</Pill></td>
                <td>{when(t.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  )
}

function TicketDetail({ ticketId, onBack }) {
  const [ticket, setTicket] = useState(null)
  const [note, setNote] = useState('')
  const [internal, setInternal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  const load = useCallback(() => {
    api.get(`/god/support/tickets/${ticketId}`)
      .then(setTicket)
      .catch((e) => setMessage(e.detail || 'Could not load that ticket.'))
  }, [ticketId])

  useEffect(() => { load() }, [load])

  async function act(promise, ok) {
    setBusy(true)
    try {
      const next = await promise
      if (next && next.ticket_number) setTicket(next)
      else load()
      setMessage(ok)
    } catch (e) {
      setMessage(e.detail || 'That did not work.')
    } finally {
      setBusy(false)
    }
  }

  if (!ticket) return <Section title="Ticket"><Empty>{message || 'Loading…'}</Empty></Section>

  return (
    <>
      <Section
        title={`${ticket.ticket_number} — ${ticket.subject}`}
        subtitle={`${ticket.organization_name || 'Unknown customer'} · ${ticket.category_label} · ${ticket.queue_label} queue`}
        right={<button className="gm-btn" type="button" onClick={onBack}>← QUEUE</button>}
      >
        <div style={{
          display: 'flex', gap: 18, flexWrap: 'wrap', padding: '13px 16px',
          borderBottom: '1px solid #1b2838',
        }}>
          <Fact label="Status" value={ticket.status_label} />
          <Fact label="Severity" value={ticket.severity_label} />
          <Fact label="Support plan" value={ticket.support_plan || '—'} />
          <Fact label="Customer said" value={ticket.customer_reported_severity || '—'} />
          <Fact label="First response due" value={when(ticket.first_response_due_at)} />
          <Fact label="First response" value={when(ticket.first_response_at)} />
          <div>
            <div style={{ fontSize: 10.5, color: '#758ba4', fontWeight: 700,
                          letterSpacing: '.08em', textTransform: 'uppercase' }}>SLA</div>
            <Pill tone={SLA_TONE[ticket.sla.state]}>{ticket.sla.label}</Pill>
          </div>
        </div>

        {(ticket.ai_summary || ticket.ai_suspected_cause) && (
          <div style={{ padding: '13px 16px', borderBottom: '1px solid #1b2838' }}>
            <div style={{ fontSize: 10.5, color: '#758ba4', fontWeight: 700,
                          letterSpacing: '.08em', textTransform: 'uppercase' }}>
              Platform diagnosis
            </div>
            <div style={{ color: '#cfe0f0', fontSize: 13, marginTop: 5 }}>
              {ticket.ai_summary || 'No fault was detected in the checks that ran.'}
            </div>
            <div style={{ marginTop: 7, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {ticket.ai_suspected_cause && (
                <Pill tone="info">{ticket.ai_suspected_cause_label}</Pill>
              )}
              {ticket.ai_confidence && <Pill>Confidence: {ticket.ai_confidence}</Pill>}
              {ticket.issue_signature && (
                <Pill>{ticket.issue_signature}</Pill>
              )}
              {ticket.incident && (
                <Pill tone="warn">
                  {ticket.incident.incident_number} · {ticket.incident.organizations_affected} orgs
                </Pill>
              )}
            </div>
          </div>
        )}

        <div style={{ padding: '13px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {ticket.messages.map((m) => (
            <div key={m.id} style={{
              border: '1px solid #1b2838', borderRadius: 9, padding: '9px 11px',
              background: m.author_kind === 'customer' ? '#0e1726' : '#0a1018',
            }}>
              <div style={{ color: '#758ba4', fontSize: 11, marginBottom: 4 }}>
                {m.author} · {when(m.created_at)}
                {m.is_first_response && ' · first response'}
              </div>
              <div style={{ color: '#cfe0f0', fontSize: 13, whiteSpace: 'pre-wrap' }}>
                {m.body}
              </div>
            </div>
          ))}
          {ticket.internal_notes.map((m) => (
            <div key={m.id} style={{
              border: '1px dashed #3a2a12', borderRadius: 9, padding: '9px 11px',
              background: '#16100a',
            }}>
              <div style={{ color: '#c79a4a', fontSize: 11, marginBottom: 4 }}>
                INTERNAL · {m.author} · {when(m.created_at)}
              </div>
              <div style={{ color: '#e2cfae', fontSize: 13, whiteSpace: 'pre-wrap' }}>
                {m.body}
              </div>
            </div>
          ))}
        </div>

        <div style={{ padding: '0 16px 14px' }}>
          <textarea
            className="gm-input"
            rows={3}
            style={{ width: '100%' }}
            placeholder={internal ? 'Internal note — the customer never sees this…'
                                  : 'Reply to the customer…'}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center',
                        marginTop: 8, flexWrap: 'wrap' }}>
            <label style={{ color: '#9fb4c9', fontSize: 12, display: 'flex',
                            gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={internal}
                     onChange={(e) => setInternal(e.target.checked)} />
              Internal note
            </label>
            <button
              className="gm-btn"
              type="button"
              disabled={busy || !note.trim()}
              onClick={() => act(
                api.post(`/god/support/tickets/${ticketId}/reply`,
                         { body: note, internal }).then((t) => { setNote(''); return t }),
                internal ? 'Note added.' : 'Reply sent to the customer.',
              )}
            >{internal ? 'ADD NOTE' : 'SEND REPLY'}</button>

            <button className="gm-btn" type="button" disabled={busy}
                    onClick={() => act(
                      api.post(`/god/support/tickets/${ticketId}/status`,
                               { status: 'waiting_on_customer' }),
                      'Clock paused — waiting on the customer.',
                    )}>WAITING ON CUSTOMER</button>

            <button className="gm-btn" type="button" disabled={busy || !note.trim()}
                    onClick={() => act(
                      api.post(`/god/support/tickets/${ticketId}/resolve`,
                               { resolution: note }).then((t) => { setNote(''); return t }),
                      'Resolved and the customer has been told.',
                    )}
                    title="Uses the text above as the resolution the customer reads.">
              RESOLVE
            </button>

            <select
              className="gm-input"
              value={ticket.severity}
              disabled={busy}
              onChange={(e) => act(
                api.post(`/god/support/tickets/${ticketId}/severity`,
                         { severity: e.target.value, reason: note || null }),
                'Severity changed and the response target recomputed.',
              )}
            >
              {['P1', 'P2', 'P3', 'P4'].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>

            <button className="gm-btn" type="button" disabled={busy}
                    onClick={() => act(
                      api.post(`/god/support/tickets/${ticketId}/diagnose`, {}),
                      'Diagnostics re-run.',
                    )}>RE-RUN DIAGNOSTICS</button>
          </div>
          {message && (
            <div style={{ color: '#9fb4c9', fontSize: 12, marginTop: 8 }}>{message}</div>
          )}
        </div>
      </Section>

      {ticket.fix_runs.length > 0 && (
        <Section title="Repairs on this ticket"
                 subtitle="A repair is only reported as fixed when verification confirmed it.">
          <table className="gm-table">
            <thead>
              <tr><th>Repair</th><th>Risk</th><th>Status</th><th>Verified</th>
                <th>Authorized by</th><th>When</th></tr>
            </thead>
            <tbody>
              {ticket.fix_runs.map((f) => (
                <tr key={f.id}>
                  <td style={{ fontFamily: 'monospace' }}>{f.action_key}</td>
                  <td><Pill tone={RISK_TONE[f.risk_class]}>{f.risk_label}</Pill></td>
                  <td>{f.status}</td>
                  <td>{f.verified
                    ? <Pill tone="ok">VERIFIED</Pill>
                    : <Pill tone="warn">NOT VERIFIED</Pill>}</td>
                  <td>{f.authorization_source || '—'}</td>
                  <td>{when(f.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {ticket.diagnostic && (
        <Section title="Evidence"
                 subtitle="The technical view. The customer sees a redacted version of the same run.">
          <pre style={{
            margin: 0, padding: '13px 16px', color: '#9fb4c9', fontSize: 11.5,
            whiteSpace: 'pre-wrap', maxHeight: 380, overflow: 'auto',
          }}>{JSON.stringify(ticket.diagnostic, null, 2)}</pre>
        </Section>
      )}
    </>
  )
}

function Fact({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 10.5, color: '#758ba4', fontWeight: 700,
                    letterSpacing: '.08em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ color: '#cfe0f0', fontSize: 13, marginTop: 3 }}>{value}</div>
    </div>
  )
}


/* ── INCIDENTS ─────────────────────────────────────────────────────────── */

function IncidentsTab({ onChanged }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(null)
  const [statement, setStatement] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.get('/god/support/incidents', { params: { status: 'open' } })
      .then((d) => { setRows(d.incidents); setError(null) })
      .catch((e) => setError(e.detail || 'Incidents are owner-only.'))
  }, [])

  useEffect(() => { load() }, [load])

  async function act(promise, done) {
    setBusy(true)
    try {
      await promise
      setStatement('')
      setOpen(null)
      load()
      onChanged()
    } catch (e) {
      setError(e.detail || done)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section
      title="Platform incidents"
      subtitle="The same fault at two or more customers. Cross-brand by construction, which is why it is owner-only."
      right={
        <button className="gm-btn" type="button"
                onClick={() => api.post('/god/support/correlate', {}).then(load)}>
          CORRELATE NOW
        </button>
      }
    >
      {error && <Empty>{error}</Empty>}
      {!error && rows === null && <Empty>Loading…</Empty>}
      {!error && rows && rows.length === 0 && (
        <Empty>No open incidents. Nothing is affecting more than one customer.</Empty>
      )}
      {!error && rows && rows.map((i) => (
        <div key={i.id} style={{ borderTop: '1px solid #1b2838', padding: '13px 16px' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'monospace', color: '#fff' }}>{i.incident_number}</span>
            <Pill tone={i.status === 'suspected' ? 'warn' : 'info'}>{i.status}</Pill>
            <Pill tone="bad">{i.organizations_affected} organizations</Pill>
            {i.platforms_affected > 1 && <Pill tone="bad">{i.platforms_affected} brands</Pill>}
            {i.classification && <Pill tone="info">{i.classification_label}</Pill>}
            {i.confidence && <Pill>Confidence: {i.confidence}</Pill>}
            <button className="gm-btn" type="button" style={{ marginLeft: 'auto' }}
                    onClick={() => setOpen(open === i.id ? null : i.id)}>
              {open === i.id ? 'HIDE' : 'DETAIL'}
            </button>
          </div>
          <div style={{ color: '#cfe0f0', fontSize: 13.5, marginTop: 7 }}>{i.title}</div>
          {i.likely_root_cause && (
            <div style={{ color: '#9fb4c9', fontSize: 12.5, marginTop: 5 }}>
              <strong style={{ color: '#cfe0f0' }}>Likely root cause. </strong>
              {i.likely_root_cause}
            </div>
          )}

          {open === i.id && (
            <div style={{ marginTop: 11, display: 'flex', flexDirection: 'column', gap: 9 }}>
              <Detail label="Impact" value={i.impact_summary} />
              <Detail label="Recommended remediation" value={i.recommended_remediation} />
              <Detail label="Suggested validation" value={i.suggested_validation} />
              <Detail label="Risk" value={i.risk_assessment} />
              <Detail label="Provider correlation" value={i.provider_health_note} />
              <Detail label="Evidence" value={JSON.stringify(i.evidence)} mono />

              <div>
                <div style={{ fontSize: 10.5, color: '#758ba4', fontWeight: 700,
                              letterSpacing: '.08em', textTransform: 'uppercase' }}>
                  What customers are told
                </div>
                <textarea
                  className="gm-input"
                  rows={2}
                  style={{ width: '100%', marginTop: 5 }}
                  placeholder={i.customer_facing_statement
                    || 'Optional. Leave blank and customers are told nothing about this.'}
                  value={statement}
                  onChange={(e) => setStatement(e.target.value)}
                />
              </div>

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="gm-btn" type="button" disabled={busy}
                        onClick={() => act(
                          api.post(`/god/support/incidents/${i.id}/acknowledge`,
                                   { customer_statement: statement || null }),
                          'Could not acknowledge.')}>ACKNOWLEDGE</button>
                <button className="gm-btn" type="button" disabled={busy}
                        onClick={() => act(
                          api.post(`/god/support/incidents/${i.id}/status`,
                                   { status: 'investigating' }),
                          'Could not update.')}>INVESTIGATING</button>
                <button className="gm-btn" type="button" disabled={busy}
                        onClick={() => act(
                          api.post(`/god/support/incidents/${i.id}/status`,
                                   { status: 'resolved', note: statement || null }),
                          'Could not resolve.')}>RESOLVED</button>
                <button className="gm-btn" type="button" disabled={busy}
                        onClick={() => act(
                          api.post(`/god/support/incidents/${i.id}/status`,
                                   { status: 'dismissed', note: statement || null }),
                          'Could not dismiss.')}>DISMISS</button>
              </div>
            </div>
          )}
        </div>
      ))}
    </Section>
  )
}

function Detail({ label, value, mono }) {
  if (!value) return null
  return (
    <div>
      <div style={{ fontSize: 10.5, color: '#758ba4', fontWeight: 700,
                    letterSpacing: '.08em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{
        color: '#9fb4c9', fontSize: 12.5, marginTop: 3,
        fontFamily: mono ? 'monospace' : undefined, wordBreak: 'break-word',
      }}>{value}</div>
    </div>
  )
}

/* ── RECURRING ─────────────────────────────────────────────────────────── */

function RecurringTab() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [days, setDays] = useState(7)

  useEffect(() => {
    api.get('/god/support/recurring', { params: { days } })
      .then((d) => { setData(d); setError(null) })
      .catch((e) => setError(e.detail || 'Could not load recurring issues.'))
  }, [days])

  return (
    <Section
      title="Recurring issues"
      subtitle="What keeps coming back, and what that means. A repair that keeps working on a problem that keeps returning is treating a symptom."
      right={
        <select className="gm-input" value={days}
                onChange={(e) => setDays(Number(e.target.value))}>
          <option value={1}>Last 24 hours</option>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
        </select>
      }
    >
      {error && <Empty>{error}</Empty>}
      {!error && !data && <Empty>Loading…</Empty>}
      {!error && data && data.signatures.length === 0 && (
        <Empty>Nothing has recurred in this window.</Empty>
      )}
      {!error && data && data.signatures.map((s) => (
        <div key={s.signature} style={{ borderTop: '1px solid #1b2838', padding: '12px 16px' }}>
          <div style={{ display: 'flex', gap: 9, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'monospace', color: '#cfe0f0', fontSize: 12 }}>
              {s.signature}
            </span>
            {s.engineering_candidate && (
              <Pill tone="bad">ENGINEERING CANDIDATE</Pill>
            )}
            {s.known_remediation_key && <Pill tone="ok">HAS A REPAIR</Pill>}
          </div>
          <div style={{ color: '#9fb4c9', fontSize: 12.5, marginTop: 5 }}>{s.narrative}</div>
          <div style={{ color: '#546b82', fontSize: 11.5, marginTop: 4 }}>
            First seen {when(s.first_seen_at)} · last seen {when(s.last_seen_at)}
          </div>
        </div>
      ))}
    </Section>
  )
}

/* ── DAILY BRIEF ───────────────────────────────────────────────────────── */

function BriefTab() {
  const [brief, setBrief] = useState(undefined)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.get('/god/support/brief')
      .then((d) => { setBrief(d.brief); setError(null) })
      .catch((e) => setError(e.detail || 'Could not load the brief.'))
  }, [])

  useEffect(() => { load() }, [load])

  async function generate() {
    setBusy(true)
    try {
      await api.post('/god/support/brief/generate', {})
      load()
    } catch (e) {
      setError(e.detail || 'Could not generate the brief.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Section
        title="Daily platform support brief"
        subtitle="Persisted, not generated on read — so the trend line does not move when somebody looks at it."
        right={
          <button className="gm-btn" type="button" disabled={busy} onClick={generate}>
            {busy ? 'RUNNING…' : 'RUN INTELLIGENCE PASS'}
          </button>
        }
      >
        {error && <Empty>{error}</Empty>}
        {!error && brief === undefined && <Empty>Loading…</Empty>}
        {!error && brief === null && (
          <Empty>No brief has been generated yet. Run the pass to produce one.</Empty>
        )}
        {brief && (
          <>
            <div style={{
              display: 'grid', gap: 10, padding: '14px 16px',
              gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
            }}>
              <Metric label="Detected" value={brief.detected} />
              <Metric label="Auto-fixed" value={brief.auto_fixed} />
              <Metric label="Auto-fix failed" value={brief.auto_fix_failed}
                      tone={brief.auto_fix_failed ? 'warn' : undefined} />
              <Metric label="Human-fixed" value={brief.human_fixed} />
              <Metric label="Unresolved" value={brief.unresolved} />
              <Metric label="Awaiting approval" value={brief.awaiting_god_approval}
                      tone={brief.awaiting_god_approval ? 'warn' : undefined} />
              <Metric label="Customer config" value={brief.customer_configuration_issues} />
              <Metric label="Provider" value={brief.provider_issues} />
              <Metric label="Platform defects" value={brief.platform_defects}
                      tone={brief.platform_defects ? 'bad' : undefined} />
              <Metric label="Multi-org incidents" value={brief.multi_org_incidents}
                      tone={brief.multi_org_incidents ? 'bad' : undefined} />
              <Metric label="SLA breached" value={brief.sla_breached}
                      tone={brief.sla_breached ? 'bad' : undefined} />
              <Metric
                label="Auto-fix success"
                /* NULL is meaningful and is rendered as such. A rate computed
                   from zero attempts is not 100%. */
                value={brief.auto_fix_success_rate === null
                  ? 'Nothing eligible'
                  : `${brief.auto_fix_success_rate}%`}
              />
            </div>

            <div style={{ padding: '0 16px 14px', color: '#758ba4', fontSize: 12 }}>
              {brief.brief_date} · generated {when(brief.generated_at)} ·{' '}
              {brief.has_baseline
                ? `compared against ${brief.trend.compared_to}`
                : 'no previous brief to compare against'}
            </div>
          </>
        )}
      </Section>

      {brief && brief.recommended_actions.length > 0 && (
        <Section title="Recommended actions"
                 subtitle="Concrete next steps, in priority order.">
          {brief.recommended_actions.map((a, i) => (
            <div key={i} style={{ borderTop: '1px solid #1b2838', padding: '12px 16px' }}>
              <div style={{ display: 'flex', gap: 9, alignItems: 'center' }}>
                <Pill tone={a.priority === 'high' ? 'bad' : 'warn'}>{a.priority}</Pill>
                <span style={{ color: '#fff', fontSize: 13.5 }}>{a.action}</span>
              </div>
              <div style={{ color: '#9fb4c9', fontSize: 12.5, marginTop: 4 }}>
                {a.reference} — {a.why}
              </div>
              {a.recommendation && (
                <div style={{ color: '#758ba4', fontSize: 12, marginTop: 4 }}>
                  {a.recommendation}
                </div>
              )}
            </div>
          ))}
        </Section>
      )}

      {brief && brief.top_signatures.length > 0 && (
        <Section title="Most frequent signatures that day">
          <table className="gm-table">
            <thead>
              <tr><th>Signature</th><th>Occurrences</th><th>Organizations</th>
                <th>Auto-fixed</th></tr>
            </thead>
            <tbody>
              {brief.top_signatures.map((s) => (
                <tr key={s.signature}>
                  <td style={{ fontFamily: 'monospace' }}>{s.signature}</td>
                  <td>{s.occurrences}</td>
                  <td>{s.organizations}</td>
                  <td>{s.auto_fixed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
    </>
  )
}


/* ── AUTO-FIXER ────────────────────────────────────────────────────────── */

function FixerTab({ onChanged }) {
  const [registry, setRegistry] = useState(null)
  const [runs, setRuns] = useState(null)
  const [policies, setPolicies] = useState(null)
  const [error, setError] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.get('/god/support/repairs').then((d) => setRegistry(d.repairs)).catch(() => {})
    api.get('/god/support/fix-runs', { params: { days: 7 } })
      .then((d) => setRuns(d.runs))
      .catch((e) => setError(e.detail || 'Could not load repair history.'))
    // Policies are owner-only; a brand operator simply does not see the panel.
    api.get('/god/support/policies')
      .then((d) => setPolicies(d))
      .catch(() => setPolicies(null))
  }, [])

  useEffect(() => { load() }, [load])

  async function decide(runId, accept) {
    setBusy(true)
    try {
      await api.post(`/god/support/fix-runs/${runId}/${accept ? 'approve' : 'reject'}`,
                     { note: note || null })
      setNote('')
      load()
      onChanged()
    } catch (e) {
      setError(e.detail || 'That did not work.')
    } finally {
      setBusy(false)
    }
  }

  const awaiting = (runs || []).filter((r) => r.status === 'approval_required')

  return (
    <>
      {awaiting.length > 0 && (
        <Section
          title="Waiting on you"
          subtitle="Diagnosed and prepared. Each one is a real problem that is still live."
        >
          <div style={{ padding: '10px 16px 0' }}>
            <input
              className="gm-input"
              style={{ width: '100%' }}
              placeholder="Optional note, recorded against whichever decision you make…"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          {awaiting.map((r) => (
            <div key={r.id} style={{ borderTop: '1px solid #1b2838', padding: '12px 16px' }}>
              <div style={{ display: 'flex', gap: 9, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontFamily: 'monospace', color: '#fff' }}>{r.action_key}</span>
                <Pill tone={RISK_TONE[r.risk_class]}>{r.risk_label}</Pill>
                <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                  <button className="gm-btn" type="button" disabled={busy}
                          onClick={() => decide(r.id, true)}>APPROVE &amp; RUN</button>
                  <button className="gm-btn" type="button" disabled={busy}
                          onClick={() => decide(r.id, false)}>REJECT</button>
                </span>
              </div>
              <div style={{ color: '#9fb4c9', fontSize: 12.5, marginTop: 5 }}>
                {r.technical_explanation}
              </div>
              {r.diagnosis && (
                <div style={{ color: '#758ba4', fontSize: 12, marginTop: 4 }}>
                  Diagnosis: {r.diagnosis}
                </div>
              )}
              {r.before_state && (
                <pre style={{
                  margin: '7px 0 0', color: '#546b82', fontSize: 11,
                  whiteSpace: 'pre-wrap',
                }}>{JSON.stringify(r.before_state)}</pre>
              )}
            </div>
          ))}
        </Section>
      )}

      <Section
        title="Repair registry"
        subtitle="Every repair the platform can perform. Anything not on this list has nothing to call."
      >
        {!registry && <Empty>Loading…</Empty>}
        {registry && (
          <table className="gm-table">
            <thead>
              <tr><th>Repair</th><th>Risk</th><th>Idempotent</th>
                <th>What it does</th></tr>
            </thead>
            <tbody>
              {registry.map((r) => (
                <tr key={r.action_key}>
                  <td style={{ fontFamily: 'monospace' }}>{r.action_key}</td>
                  <td><Pill tone={RISK_TONE[r.risk_class]}>{r.risk_label}</Pill></td>
                  <td>{r.idempotent ? 'Yes' : 'No'}</td>
                  <td style={{ maxWidth: 520 }}>{r.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {policies && (
        <Section
          title="Automation policy"
          subtitle={policies.note}
        >
          {policies.policies.length === 0 && (
            <Empty>
              No automatic repairs are enabled anywhere. That is the default, and
              it stays that way until somebody enables one for a named scope.
            </Empty>
          )}
          {policies.policies.length > 0 && (
            <table className="gm-table">
              <thead>
                <tr><th>Repair</th><th>Scope</th><th>Automatic</th>
                  <th>Daily cap</th><th>Reason</th><th /></tr>
              </thead>
              <tbody>
                {policies.policies.map((p) => (
                  <tr key={p.id}>
                    <td style={{ fontFamily: 'monospace' }}>{p.action_key}</td>
                    <td>{p.organization_id ? 'One customer'
                      : p.platform_id ? 'One brand' : '—'}</td>
                    <td>{p.auto_execute
                      ? <Pill tone="warn">ON</Pill>
                      : <Pill>OFF</Pill>}</td>
                    <td>{p.max_runs_per_day ?? 'Uncapped'}</td>
                    <td>{p.reason || '—'}</td>
                    <td>
                      {p.is_active && (
                        <button className="gm-btn" type="button" disabled={busy}
                                onClick={async () => {
                                  await api.delete(`/god/support/policies/${p.id}`)
                                  load()
                                }}>DISABLE</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      <Section title="Repairs in the last 7 days"
               subtitle="Verified means a second, independent read confirmed the condition was gone.">
        {error && <Empty>{error}</Empty>}
        {!error && !runs && <Empty>Loading…</Empty>}
        {!error && runs && runs.length === 0 && <Empty>Nothing has run.</Empty>}
        {!error && runs && runs.length > 0 && (
          <table className="gm-table">
            <thead>
              <tr><th>Repair</th><th>Risk</th><th>Status</th><th>Verified</th>
                <th>Authorized by</th><th>Result</th><th>When</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontFamily: 'monospace' }}>{r.action_key}</td>
                  <td><Pill tone={RISK_TONE[r.risk_class]}>{r.risk_label}</Pill></td>
                  <td>{r.status}</td>
                  <td>{r.verified
                    ? <Pill tone="ok">VERIFIED</Pill>
                    : <Pill tone="warn">NO</Pill>}</td>
                  <td>{r.authorization_source || '—'}</td>
                  <td style={{ maxWidth: 360 }}>{r.message || '—'}</td>
                  <td>{when(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  )
}

/* ── CONFIGURATION ─────────────────────────────────────────────────────── */

function ConfigTab() {
  const [brands, setBrands] = useState(null)
  const [selected, setSelected] = useState(null)
  const [config, setConfig] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [naming, setNaming] = useState({})

  useEffect(() => {
    api.get('/god/support/brands')
      .then((d) => setBrands(d.brands))
      .catch((e) => setError(e.detail || 'Could not list brands.'))
  }, [])

  const loadConfig = useCallback((platformId) => {
    setSelected(platformId)
    api.get(`/god/support/config/${platformId}`)
      .then((d) => {
        setConfig(d)
        setNaming({
          assistant_name: d.branding.assistant_name || '',
          help_center_name: d.branding.help_center_name || '',
          support_display_name: d.branding.support_display_name || '',
        })
        setError(null)
      })
      .catch((e) => setError(e.detail || 'Support configuration is owner-only.'))
  }, [])

  async function saveNaming() {
    setBusy(true)
    try {
      await api.put(`/god/support/config/${selected}/brand`, naming)
      loadConfig(selected)
    } catch (e) {
      setError(e.detail || 'Could not save.')
    } finally {
      setBusy(false)
    }
  }

  async function savePackage(plan) {
    setBusy(true)
    try {
      await api.put(`/god/support/config/${selected}/entitlements`, plan)
      loadConfig(selected)
    } catch (e) {
      setError(e.detail || 'Could not save.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Section title="Brands"
               subtitle="A package with no configuration runs on the frozen default. From a customer's SLA page that is indistinguishable from a decision.">
        {error && <Empty>{error}</Empty>}
        {!brands && !error && <Empty>Loading…</Empty>}
        {brands && (
          <table className="gm-table">
            <thead>
              <tr><th>Brand</th><th>Assistant</th><th>Configured</th>
                <th>Unconfigured</th><th>Hours</th><th /></tr>
            </thead>
            <tbody>
              {brands.map((b) => (
                <tr key={b.id}>
                  <td>{b.name}</td>
                  <td>{b.assistant_name}</td>
                  <td>{b.packages_configured}</td>
                  <td>{b.packages_unconfigured > 0
                    ? <Pill tone="warn">{b.packages_unconfigured}</Pill>
                    : <Pill tone="ok">0</Pill>}</td>
                  <td>{b.hours_source === 'config'
                    ? <Pill tone="ok">SET</Pill>
                    : <Pill tone="warn">DEFAULT</Pill>}</td>
                  <td>
                    <button className="gm-btn" type="button"
                            onClick={() => loadConfig(b.id)}>CONFIGURE</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {config && (
        <>
          <Section title={`${config.platform.name} — the face`}
                   subtitle="What this brand's customers see. Leave blank to fall back to the brand's own display name.">
            <div style={{ padding: '13px 16px', display: 'grid', gap: 10,
                          gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))' }}>
              {[['assistant_name', 'Assistant name', 'Ask Evo'],
                ['help_center_name', 'Help centre name', 'EvoSys Pro Help Centre'],
                ['support_display_name', 'Support team name', 'EvoSys Pro Support'],
              ].map(([key, label, placeholder]) => (
                <label key={key} style={{ display: 'flex', flexDirection: 'column',
                                          gap: 5, color: '#9fb4c9', fontSize: 12 }}>
                  {label}
                  <input
                    className="gm-input"
                    placeholder={placeholder}
                    value={naming[key] || ''}
                    onChange={(e) => setNaming({ ...naming, [key]: e.target.value })}
                  />
                </label>
              ))}
            </div>
            <div style={{ padding: '0 16px 14px' }}>
              <button className="gm-btn" type="button" disabled={busy}
                      onClick={saveNaming}>SAVE NAMES</button>
            </div>
          </Section>

          <Section title={`${config.platform.name} — support hours`}
                   subtitle={config.hours.text}>
            <div style={{ padding: '13px 16px', color: '#9fb4c9', fontSize: 12.5 }}>
              Targets are measured in these hours, so a request raised at 16:45
              on a Friday starts its clock when support opens again.
              {config.hours.source === 'default' && (
                <strong style={{ color: '#f5a524' }}>
                  {' '}This brand has not set its own hours and is running on the
                  platform default.
                </strong>
              )}
            </div>
          </Section>

          <Section title={`${config.platform.name} — packages`}
                   subtitle="First-response targets in BUSINESS minutes. Blank inherits the package rule rather than zeroing it.">
            <table className="gm-table">
              <thead>
                <tr><th>Package</th><th>Queue</th><th>Normal</th><th>High</th>
                  <th>Critical</th><th>Included minutes</th><th>Source</th><th /></tr>
              </thead>
              <tbody>
                {config.entitlements.packages.map((p) => (
                  <PackageRow key={p.plan_key} pkg={p} busy={busy}
                              onSave={savePackage}
                              queues={config.entitlements.queues} />
                ))}
              </tbody>
            </table>
          </Section>
        </>
      )}
    </>
  )
}

function PackageRow({ pkg, onSave, busy, queues }) {
  const [draft, setDraft] = useState({
    queue: pkg.queue,
    first_response_normal_minutes: pkg.first_response_minutes.P3,
    first_response_high_minutes: pkg.first_response_minutes.P2,
    first_response_critical_minutes: pkg.first_response_minutes.P1,
    included_assistance_minutes: pkg.included_assistance_minutes,
  })

  return (
    <tr>
      <td>{pkg.display_name}<br />
        <span style={{ fontFamily: 'monospace', color: '#546b82', fontSize: 11 }}>
          {pkg.plan_key}
        </span>
      </td>
      <td>
        <select className="gm-input" value={draft.queue}
                onChange={(e) => setDraft({ ...draft, queue: e.target.value })}>
          {queues.map((q) => <option key={q.key} value={q.key}>{q.label}</option>)}
        </select>
      </td>
      {['first_response_normal_minutes', 'first_response_high_minutes',
        'first_response_critical_minutes', 'included_assistance_minutes'].map((key) => (
        <td key={key}>
          <input
            className="gm-input"
            style={{ width: 78 }}
            type="number"
            min={0}
            value={draft[key] ?? ''}
            onChange={(e) => setDraft({
              ...draft,
              [key]: e.target.value === '' ? null : Number(e.target.value),
            })}
          />
        </td>
      ))}
      <td>{pkg.configured
        ? <Pill tone="ok">SET</Pill>
        : <Pill tone="warn">DEFAULT</Pill>}</td>
      <td>
        <button className="gm-btn" type="button" disabled={busy}
                onClick={() => onSave({ plan_key: pkg.plan_key, ...draft })}>
          SAVE
        </button>
      </td>
    </tr>
  )
}
