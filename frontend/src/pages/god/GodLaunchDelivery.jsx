/**
 * The implementation command centre for one customer.
 *
 * ===========================================================================
 * THE QUESTION THIS SCREEN ANSWERS
 * ===========================================================================
 *
 * "What do we need to do next?" — and it answers it in the first box, before
 * any list, because a screen that makes somebody read four tables to work out
 * what is outstanding has not answered it.
 *
 * READINESS IS FIRST AND IT IS COMPUTED. Every line in it is the server's
 * answer, derived from rows somebody actually created. Nothing here is a
 * checkbox a person ticks to say a thing was done, which is why the same
 * lines can be trusted to stand between a customer and Live.
 *
 * ===========================================================================
 * WHAT IS DELIBERATELY NOT HERE
 * ===========================================================================
 *
 * No progress percentage for delivery. The platform already publishes two
 * numbers — intake and implementation — and a third would compete with them.
 * Counts, per area, and a list of what is outstanding.
 *
 * No customer identity, no brand name, no integration named in code. Every
 * label on this screen arrives from the server, which reads it from the
 * brand's own launch template.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

const TONE = {
  neutral: { bg: 'var(--gm-pill-blue-bg)', text: 'var(--gm-dim)', border: 'var(--gm-card-line)' },
  warning: { bg: 'var(--gm-pill-amber-bg)', text: 'var(--gm-amber)', border: 'var(--gm-amber)' },
  info: { bg: 'var(--gm-pill-blue-bg)', text: 'var(--gm-blue)', border: 'var(--gm-blue)' },
  positive: { bg: 'var(--gm-pill-teal-bg)', text: 'var(--gm-teal)', border: 'var(--gm-teal)' },
  danger: { bg: 'var(--gm-pill-red-bg)', text: 'var(--gm-red)', border: 'var(--gm-pill-red-bd)' },
}

function Chip({ label, tone = 'neutral', title }) {
  const s = TONE[tone] || TONE.neutral
  return (
    <span title={title} style={{
      background: s.bg, color: s.text, border: '1px solid ' + s.border,
      borderRadius: 999, padding: '3px 10px', fontSize: 11, fontWeight: 700,
      whiteSpace: 'nowrap', display: 'inline-block',
    }}>{label}</span>
  )
}

const INTEGRATION_TONE = {
  required: 'neutral', credentials_received: 'info', configuring: 'info',
  connected: 'info', testing: 'warning', verified: 'positive',
  blocked: 'danger', not_applicable: 'neutral',
}

const CHECK_TONE = {
  not_tested: 'neutral', pass: 'positive', fail: 'danger', retest: 'warning',
}

const INTEGRATION_STATUSES = [
  'required', 'credentials_received', 'configuring', 'connected', 'testing',
  'verified', 'blocked', 'not_applicable',
]
const CHECK_STATUSES = ['not_tested', 'pass', 'fail', 'retest']
const PARTIES = ['customer', 'provider', 'partner', 'technical', 'approval']

const box = {
  background: 'var(--god-card, var(--gm-panel))',
  border: '1px solid var(--god-border, var(--gm-card-line))',
  borderRadius: 10, padding: '14px 16px', marginBottom: 12,
}

const label = {
  fontSize: 11, fontWeight: 700, letterSpacing: '.06em',
  textTransform: 'uppercase', color: 'var(--god-muted, var(--gm-dim))',
}

const control = {
  fontSize: 12, padding: '5px 8px', borderRadius: 7,
  border: '1px solid var(--god-border, var(--gm-card-line))',
  background: 'var(--god-card, var(--gm-panel))', color: 'var(--god-text, var(--gm-blue))',
}

const button = {
  ...control, cursor: 'pointer', fontWeight: 600, padding: '6px 12px',
}

/** A yes/no line with the reason attached. The reason is the useful half. */
function GateLine({ item }) {
  const muted = !item.required
  return (
    <div style={{
      display: 'flex', gap: 10, alignItems: 'baseline', padding: '5px 0',
      opacity: muted ? 0.55 : 1,
    }}>
      <span style={{ width: 14, flex: '0 0 14px', fontWeight: 700,
                     color: item.ok ? 'var(--gm-teal)' : (item.required ? 'var(--gm-red)' : 'var(--gm-text)') }}>
        {item.ok ? '✓' : '·'}
      </span>
      <span style={{ fontSize: 13, fontWeight: item.required ? 600 : 400,
                     minWidth: 220 }}>
        {item.label}
        {!item.required ? (
          <span style={{ fontSize: 10, color: 'var(--gm-text)', marginLeft: 6 }}>
            not required by this brand
          </span>
        ) : null}
      </span>
      <span style={{ fontSize: 12, color: 'var(--god-muted, var(--gm-dim))' }}>
        {item.detail}
      </span>
    </div>
  )
}

export default function GodLaunchDelivery({ orgId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [newBlocker, setNewBlocker] = useState(null)

  const load = useCallback(() => {
    api.get('/god/launch/' + orgId + '/delivery')
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e?.detail || 'Could not load the delivery programme.'))
  }, [orgId])

  useEffect(load, [load])

  const act = useCallback(async fn => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      load()
    } catch (e) {
      setError(e?.detail || 'That did not go through.')
    } finally {
      setBusy(false)
    }
  }, [load])

  if (error && !data) {
    return <p style={{ fontSize: 13, color: 'var(--gm-red)' }}>{error}</p>
  }
  if (!data) {
    return <p style={{ fontSize: 13, color: 'var(--gm-text)' }}>Loading delivery…</p>
  }

  const base = '/god/launch/' + orgId
  const r = data.readiness
  const p = data.progress
  const outstanding = r.outstanding || []
  const readOnly = data.can_manage === false

  return (
    <div style={{ marginTop: 16 }}>
      {error ? (
        <div style={{ ...box, background: 'var(--gm-pill-red-bg)', borderColor: 'var(--gm-pill-red-bd)',
                      color: 'var(--gm-red)', fontSize: 12 }}>{error}</div>
      ) : null}

      {readOnly ? (
        <div style={{ ...box, background: 'var(--gm-pill-amber-bg)', borderColor: 'var(--gm-amber)',
                      color: 'var(--gm-amber)', fontSize: 12 }}>
          You can read this launch but not change it. Managing an
          implementation belongs to god or its assigned owner.
        </div>
      ) : null}

      {/* ── WHAT IS OUTSTANDING, FIRST ────────────────────────────────── */}
      <div style={{ ...box, borderLeft: '3px solid '
                    + (r.ready ? 'var(--gm-teal)' : 'var(--gm-amber)') }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                      marginBottom: 8, flexWrap: 'wrap' }}>
          <span style={label}>Go-live gate</span>
          <Chip label={r.already_live ? 'Live'
            : r.ready ? 'Ready for go-live'
              : outstanding.length + ' outstanding'}
            tone={r.already_live ? 'info' : r.ready ? 'positive' : 'warning'} />
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 11, color: 'var(--god-muted, var(--gm-dim))' }}>
            Connections {p.integrations.required_done}/{p.integrations.required_total}
            {' · '}Checks {p.checks.required_done}/{p.checks.required_total}
            {' · '}Training {p.training.required_done}/{p.training.required_total}
            {' · '}Blockers {p.blockers_open}
          </span>
        </div>
        {/* CAN THEY EVEN GET IN? Above the gate, because a launch where
            nobody has been invited is not 8% done, it has not started — and
            that is invisible on a screen of green ticks about our own work. */}
        {data.access ? (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                        flexWrap: 'wrap', paddingBottom: 8, marginBottom: 4,
                        borderBottom: '1px solid var(--god-border, var(--gm-card-line))' }}>
            <span style={{ fontSize: 13, fontWeight: 600, minWidth: 220 }}>
              Customer access
            </span>
            <Chip
              label={{
                accepted: 'Account active',
                invited: 'Invited — not accepted yet',
                users_exist: 'Users exist, no invitation on record',
                not_invited: 'Nobody has been invited',
              }[data.access.state] || data.access.state}
              tone={{
                accepted: 'positive', invited: 'info',
                users_exist: 'warning', not_invited: 'danger',
              }[data.access.state] || 'neutral'} />
            <span style={{ fontSize: 12, color: 'var(--god-muted, var(--gm-dim))' }}>
              {data.access.active_users} active user
              {data.access.active_users === 1 ? '' : 's'}
              {data.access.invitations.length
                ? ' · last sent ' + (data.access.invitations[0].last_sent_at
                  ? new Date(data.access.invitations[0].last_sent_at).toLocaleString()
                  : 'not recorded')
                : ''}
            </span>
            <a href="/god/implementations" style={{ fontSize: 12 }}>
              Invite or resend
            </a>
          </div>
        ) : null}

        {(r.items || []).map(i => <GateLine key={i.key} item={i} />)}
      </div>

      {/* ── connections ───────────────────────────────────────────────── */}
      <div style={box}>
        <div style={{ ...label, marginBottom: 8 }}>Integrations</div>
        {(data.integrations || []).length === 0 ? (
          <p style={{ fontSize: 12, color: 'var(--gm-text)', margin: 0 }}>
            This brand's launch template tracks no connections.
          </p>
        ) : data.integrations.map(i => (
          <div key={i.id} style={{ display: 'flex', gap: 10, alignItems: 'center',
                                   flexWrap: 'wrap', padding: '6px 0',
                                   borderTop: '1px solid var(--god-border, var(--gm-card-line))' }}>
            <b style={{ fontSize: 13, flex: '1 1 200px' }}>
              {i.label}
              {i.provider ? (
                <span style={{ fontWeight: 400, color: 'var(--gm-text)' }}> · {i.provider}</span>
              ) : null}
              {!i.is_required ? (
                <span style={{ fontSize: 10, color: 'var(--gm-text)' }}> · optional</span>
              ) : null}
            </b>
            <Chip label={i.status_label} tone={INTEGRATION_TONE[i.status] || 'neutral'} />
            <select
              value={i.status} disabled={busy || readOnly} style={control}
              onChange={e => act(() => api.patch(
                base + '/integrations/' + i.id, { status: e.target.value }))}>
              {INTEGRATION_STATUSES.map(s => (
                <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
              ))}
            </select>
          </div>
        ))}
      </div>

      {/* ── testing ───────────────────────────────────────────────────── */}
      <div style={box}>
        <div style={{ ...label, marginBottom: 8 }}>
          Testing — our verdict, and the customer's
        </div>
        {(data.checks || []).length === 0 ? (
          <p style={{ fontSize: 12, color: 'var(--gm-text)', margin: 0 }}>
            This brand's launch template defines no checks.
          </p>
        ) : data.checks.map(c => (
          <div key={c.id} style={{ display: 'flex', gap: 10, alignItems: 'center',
                                   flexWrap: 'wrap', padding: '6px 0',
                                   borderTop: '1px solid var(--god-border, var(--gm-card-line))' }}>
            <b style={{ fontSize: 13, flex: '1 1 220px' }}>
              {c.label}
              {c.category ? (
                <span style={{ fontWeight: 400, color: 'var(--gm-text)' }}> · {c.category}</span>
              ) : null}
            </b>
            <Chip label={c.status_label} tone={CHECK_TONE[c.status] || 'neutral'} />
            {c.customer_approved_at
              ? <Chip label="Customer approved" tone="positive" />
              : <Chip label="Awaiting customer" tone="neutral"
                      title="Only the customer can give this" />}
            <select
              value={c.status} disabled={busy || readOnly} style={control}
              onChange={e => act(() => api.patch(
                base + '/checks/' + c.id, { status: e.target.value }))}>
              {CHECK_STATUSES.map(s => (
                <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
              ))}
            </select>
          </div>
        ))}
      </div>

      {/* ── training ──────────────────────────────────────────────────── */}
      <div style={box}>
        <div style={{ ...label, marginBottom: 8 }}>Training</div>
        {(data.training || []).length === 0 ? (
          <p style={{ fontSize: 12, color: 'var(--gm-text)', margin: 0 }}>
            This brand's launch template schedules no training.
          </p>
        ) : data.training.map(t => (
          <div key={t.id} style={{ display: 'flex', gap: 10, alignItems: 'center',
                                   flexWrap: 'wrap', padding: '6px 0',
                                   borderTop: '1px solid var(--god-border, var(--gm-card-line))' }}>
            <b style={{ fontSize: 13, flex: '1 1 200px' }}>{t.title}</b>
            <input
              type="date" style={control} disabled={busy || readOnly}
              value={t.scheduled_at ? t.scheduled_at.slice(0, 10) : ''}
              onChange={e => act(() => api.patch(
                base + '/training/' + t.id,
                { scheduled_at: e.target.value ? e.target.value + 'T09:00:00' : null }))} />
            <label style={{ fontSize: 12, display: 'flex', gap: 6,
                            alignItems: 'center' }}>
              <input type="checkbox" checked={!!t.completed_at}
                     disabled={busy || readOnly}
                     onChange={e => act(() => api.patch(
                       base + '/training/' + t.id, { completed: e.target.checked }))} />
              Delivered
            </label>
            {t.customer_acknowledged_at
              ? <Chip label="Customer confirmed" tone="positive" />
              : <Chip label="Not confirmed" tone="neutral" />}
            <span style={{ fontSize: 11, color: 'var(--gm-text)' }}>
              {(t.attendees || []).length} attendee
              {(t.attendees || []).length === 1 ? '' : 's'}
            </span>
          </div>
        ))}
      </div>

      {/* ── blockers ──────────────────────────────────────────────────── */}
      <div style={box}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                      marginBottom: 8 }}>
          <span style={label}>Blockers</span>
          <span style={{ flex: 1 }} />
          {!readOnly ? (
            <button style={button} disabled={busy}
                    onClick={() => setNewBlocker(newBlocker ? null : {
                      title: '', detail: '', party: 'provider',
                      customer_visible: false, customer_action: '' })}>
              {newBlocker ? 'Cancel' : '+ Add blocker'}
            </button>
          ) : null}
        </div>

        {newBlocker ? (
          <div style={{ display: 'grid', gap: 8, marginBottom: 12,
                        gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))' }}>
            <input style={control} placeholder="What is stuck"
                   value={newBlocker.title}
                   onChange={e => setNewBlocker({ ...newBlocker, title: e.target.value })} />
            <select style={control} value={newBlocker.party}
                    onChange={e => setNewBlocker({ ...newBlocker, party: e.target.value })}>
              {PARTIES.map(p2 => (
                <option key={p2} value={p2}>{p2}</option>
              ))}
            </select>
            <input style={control} placeholder="Internal detail (never shown to them)"
                   value={newBlocker.detail}
                   onChange={e => setNewBlocker({ ...newBlocker, detail: e.target.value })} />
            <label style={{ fontSize: 12, display: 'flex', gap: 6,
                            alignItems: 'center' }}>
              <input type="checkbox" checked={newBlocker.customer_visible}
                     onChange={e => setNewBlocker({
                       ...newBlocker, customer_visible: e.target.checked })} />
              Show the customer
            </label>
            {newBlocker.customer_visible ? (
              <input style={{ ...control, gridColumn: '1 / -1' }}
                     placeholder="What you are asking them to do, in their words"
                     value={newBlocker.customer_action}
                     onChange={e => setNewBlocker({
                       ...newBlocker, customer_action: e.target.value })} />
            ) : null}
            <button style={{ ...button, gridColumn: '1 / -1' }} disabled={busy}
                    onClick={() => act(async () => {
                      await api.post(base + '/blockers', newBlocker)
                      setNewBlocker(null)
                    })}>Open this blocker</button>
          </div>
        ) : null}

        {(data.blockers || []).length === 0 ? (
          <p style={{ fontSize: 12, color: 'var(--gm-text)', margin: 0 }}>
            Nothing is blocked.
          </p>
        ) : data.blockers.map(b => (
          <div key={b.id} style={{ display: 'flex', gap: 10, alignItems: 'baseline',
                                   flexWrap: 'wrap', padding: '6px 0',
                                   borderTop: '1px solid var(--god-border, var(--gm-card-line))',
                                   opacity: b.status === 'resolved' ? 0.5 : 1 }}>
            <b style={{ fontSize: 13, flex: '1 1 220px' }}>{b.title}</b>
            <Chip label={b.party_label}
                  tone={b.status === 'resolved' ? 'neutral' : 'danger'} />
            {b.customer_visible
              ? <Chip label="Customer sees this" tone="info" /> : null}
            <span style={{ fontSize: 11, color: 'var(--gm-text)' }}>
              {b.opened_at ? new Date(b.opened_at).toLocaleDateString() : ''}
            </span>
            {b.status !== 'resolved' && !readOnly ? (
              <button style={button} disabled={busy}
                      onClick={() => act(() => api.post(
                        base + '/blockers/' + b.id + '/resolve',
                        { resolution: window.prompt('How was it resolved?') || null }))}>
                Resolve
              </button>
            ) : null}
            {b.detail ? (
              <div style={{ flexBasis: '100%', fontSize: 12,
                            color: 'var(--god-muted, var(--gm-dim))' }}>{b.detail}</div>
            ) : null}
          </div>
        ))}
      </div>

      {/* ── the two signatures ────────────────────────────────────────── */}
      <div style={box}>
        <div style={{ ...label, marginBottom: 8 }}>Go-live approvals</div>
        {['customer_signoff', 'provider_signoff'].map(kind => {
          const a = (data.approvals || {})[kind]
          return (
            <div key={kind} style={{ display: 'flex', gap: 10, alignItems: 'center',
                                     flexWrap: 'wrap', padding: '6px 0',
                                     borderTop: '1px solid var(--god-border, var(--gm-card-line))' }}>
              <b style={{ fontSize: 13, flex: '1 1 240px' }}>
                {kind === 'customer_signoff'
                  ? 'Customer approval to go live'
                  : 'Implementation team approval to go live'}
              </b>
              {a ? (
                <>
                  <Chip label={'Given' + (a.given_name ? ' by ' + a.given_name : '')}
                        tone="positive" />
                  <span style={{ fontSize: 11, color: 'var(--gm-text)' }}>
                    {a.given_at ? new Date(a.given_at).toLocaleString() : ''}
                  </span>
                  {!readOnly ? (
                    <button style={button} disabled={busy}
                            onClick={() => act(() => api.delete(
                              base + '/approvals/' + kind))}>Withdraw</button>
                  ) : null}
                </>
              ) : (
                <>
                  <Chip label="Not given" tone="warning" />
                  {!readOnly ? (
                    <button style={button} disabled={busy}
                            onClick={() => {
                              const who = window.prompt(
                                'Who gave this approval? Their name, not yours.')
                              if (who === null) return
                              act(() => api.post(base + '/approvals/' + kind,
                                                 { given_name: who || null }))
                            }}>Record approval</button>
                  ) : null}
                </>
              )}
            </div>
          )
        })}
        <p style={{ fontSize: 11, color: 'var(--gm-text)', margin: '10px 0 0' }}>
          Both signatures are recorded here, including the customer's — theirs
          usually arrives on a call or in an email, and a record that says who
          gave it and who wrote it down is more honest than one that pretends
          they clicked.
        </p>
      </div>
    </div>
  )
}
