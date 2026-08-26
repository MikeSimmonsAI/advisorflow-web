/**
 * God Mode — one implementation (Checkpoint 6 §11-§13, §18, §19, §23, §38, §39).
 *
 * Owner, status, milestones, blockers, the customer's admins, billing intent,
 * the sales handoff, and the history — on one page, because the owner's question
 * is always "what is happening with this customer", not "which of eight tabs".
 *
 * THE ACTIVATION LINK IS SHOWN ONCE. When an admin is created or an invite
 * resent, the link appears in a box that says so. It is not stored anywhere and
 * navigating away loses it; that is the design, not an oversight.
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import {
  Panel, Empty, Fact, Bar, StatusBadge, money, when, whenExact,
  errText, errWarnings,
} from './god/GodOpsShared'
import './god/GodOps.css'

export default function GodImplementationDetail() {
  const { implId } = useParams()
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [warnings, setWarnings] = useState([])
  const [busy, setBusy] = useState('')
  const [owners, setOwners] = useState([])
  const [link, setLink] = useState(null)
  const [admin, setAdmin] = useState({ full_name: '', email: '', role: 'org_admin' })
  const [blockNote, setBlockNote] = useState('')

  const load = () => api.get('/god/ops/implementations/' + implId)
    .then(setD).catch(e => setErr(errText(e)))

  useEffect(() => {
    load()
    // /god/ops/staff, not /god/users: the latter lists god/super/org admins,
    // which is mostly CUSTOMER administrators and almost none of the internal
    // people who do implementations.
    api.get('/god/ops/staff?implementation_id=' + implId).then(r => setOwners(r.staff || []))
      .catch(() => setOwners([]))
  }, [implId])

  if (err && !d) return <div className="go-scope"><div className="go-note err">{err}</div></div>
  if (!d) return <div className="go-scope"><div className="go-empty">Loading…</div></div>

  const i = d.implementation
  const c = d.completion || {}

  async function act(key, fn) {
    setBusy(key); setErr(''); setWarnings([])
    try { await fn(); await load() }
    catch (e) { setErr(errText(e)); setWarnings(errWarnings(e)) }
    finally { setBusy('') }
  }

  const setStatus = (status, extra) =>
    act('status', () => api.post('/god/ops/implementations/' + implId + '/status',
                                 { status, ...(extra || {}) }))

  const setMilestone = (key, status) =>
    act('ms:' + key, () => api.post(
      '/god/ops/implementations/' + implId + '/milestones/' + key, { status }))

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/implementations')}>← Implementations</button>
          <h1 style={{ marginTop: 8 }}>{i.organization_name}</h1>
          <p>
            {i.brand_sales_org ? i.brand_sales_org.name : '—'}
            {' · '}{i.platform ? i.platform.name : '—'}
            {' · '}{i.package ? i.package.name : 'no package'}
            {' · sold by '}{i.sold_by ? i.sold_by.name : '—'}
          </p>
        </div>
        <div style={{ textAlign: 'right' }}>
          <StatusBadge status={i.status} />
          <div style={{ fontSize: 12, color: 'var(--go-dim)', marginTop: 6 }}>
            {c.settled}/{c.total} milestones · {c.percent}%
          </div>
          <Bar percent={c.percent} />
        </div>
      </div>

      {err ? (
        <div className="go-note err">
          {err}
          {warnings.length ? <ul>{warnings.map((w, n) => <li key={n}>{w}</li>)}</ul> : null}
        </div>
      ) : null}

      {i.blocker_note ? (
        <div className="go-note err">
          <strong>Blocked</strong> since {when(i.blocked_at)} — {i.blocker_note}
        </div>
      ) : null}

      {link ? (
        <div className="go-note warn">
          <strong>Activation link — shown once, not recoverable.</strong>{' '}
          Send it to {link.email}. It expires {whenExact(link.expires_at)}. No password
          was created; the customer sets their own.
          <div className="go-secret">{link.url}</div>
          <div className="go-actions" style={{ marginTop: 10 }}>
            <button className="go-btn sm ghost"
                    onClick={() => { navigator.clipboard && navigator.clipboard.writeText(link.url) }}>
              Copy link
            </button>
            <button className="go-btn sm ghost" onClick={() => setLink(null)}>Dismiss</button>
          </div>
        </div>
      ) : null}

      {/* ── status and launch ─────────────────────────────────────────────── */}
      <Panel title="Lifecycle">
        <div className="go-body">
          <div className="go-fields" style={{ marginBottom: 14 }}>
            <div className="go-field">
              <label>Implementation owner</label>
              <select value={i.owner ? i.owner.id : ''} disabled={busy === 'owner'}
                      onChange={e => act('owner', () => api.post(
                        '/god/ops/implementations/' + implId + '/owner',
                        { owner_user_id: e.target.value || null }))}>
                <option value="">unassigned</option>
                {owners.filter(u => u.is_active !== false).map(u => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </select>
            </div>
            <div className="go-field">
              <label>Status</label>
              <select value={i.status} disabled={busy === 'status' || i.is_live}
                      onChange={e => {
                        const v = e.target.value
                        if (v === 'blocked') return
                        setStatus(v)
                      }}>
                {['not_started', 'kickoff_scheduled', 'configuration', 'data_migration',
                  'integrations', 'testing', 'training', 'ready_for_launch']
                  .map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
                {i.is_live ? <option value="live">live</option> : null}
                {i.status === 'blocked' ? <option value="blocked">blocked</option> : null}
              </select>
              {i.is_live ? <div className="hint">A live customer is not reopened from here.</div> : null}
            </div>
            <div className="go-field">
              <label>Target launch date</label>
              <input type="date" disabled={i.is_live}
                     defaultValue={i.target_launch_date ? String(i.target_launch_date).slice(0, 10) : ''}
                     onBlur={e => e.target.value && setStatus(i.status === 'blocked' ? 'configuration' : i.status,
                       { target_launch_date: new Date(e.target.value + 'T12:00:00').toISOString(),
                         blocker_note: null })} />
            </div>
          </div>

          {!i.is_live ? (
            <div className="go-fields">
              <div className="go-field full">
                <label>Record a blocker</label>
                <input value={blockNote} onChange={e => setBlockNote(e.target.value)}
                       placeholder="What is this waiting on?" />
              </div>
              <div className="go-field">
                <label>&nbsp;</label>
                <button className="go-btn ghost" disabled={!blockNote.trim() || busy === 'status'}
                        onClick={() => setStatus('blocked', { blocker_note: blockNote })}>
                  Mark blocked
                </button>
              </div>
            </div>
          ) : null}

          <div className="go-actions" style={{ marginTop: 16 }}>
            {!i.is_live ? (
              <>
                <button className="go-btn ghost" disabled={busy === 'status'}
                        onClick={() => setStatus('ready_for_launch')}>
                  Mark ready for launch
                </button>
                <button className="go-btn go" disabled={busy === 'launch'}
                        onClick={() => act('launch', () => api.post(
                          '/god/ops/implementations/' + implId + '/launch',
                          { acknowledge_warnings: warnings.length > 0 }))}>
                  {warnings.length ? 'Launch anyway' : 'Mark customer LIVE'}
                </button>
              </>
            ) : (
              <div className="go-note ok" style={{ margin: 0 }}>
                Live since {whenExact(i.launched_at)}.
              </div>
            )}
          </div>

          {!i.is_live && d.launch_warnings && d.launch_warnings.length ? (
            <div className="go-note warn" style={{ marginTop: 14 }}>
              <strong>Open before launch</strong>
              <ul>{d.launch_warnings.map((w, n) => <li key={n}>{w}</li>)}</ul>
              These are warnings, not blocks. You may launch anyway.
            </div>
          ) : null}
        </div>
      </Panel>

      {/* ── milestones ────────────────────────────────────────────────────── */}
      <Panel title="Onboarding milestones" count={c.total}>
        {!(d.milestones || []).length ? <Empty>No milestones on this implementation.</Empty> : (
          <ul className="go-ms">
            {d.milestones.map(m => (
              <li key={m.key}>
                <div className="lab">
                  {m.label}
                  {m.is_required ? <span className="go-badge warn" style={{ marginLeft: 8 }}>required</span> : null}
                  <small>{m.description || m.key}</small>
                </div>
                <select value={m.status} disabled={busy === 'ms:' + m.key}
                        onChange={e => setMilestone(m.key, e.target.value)}>
                  {(d.milestone_statuses || []).map(s => (
                    <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* ── customer admins ───────────────────────────────────────────────── */}
      <Panel title="Customer users" count={(d.customer_admins || []).length}>
        {!(d.customer_admins || []).length ? (
          <Empty>This customer has nobody who can sign in yet.</Empty>
        ) : (
          <table className="go-table">
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th>Invitation</th><th>Last login</th><th></th></tr>
            </thead>
            <tbody>
              {d.customer_admins.map(u => (
                <tr key={u.user_id}>
                  <td data-label="Name">{u.full_name}</td>
                  <td data-label="Email">{u.email}</td>
                  <td data-label="Role"><span className="go-badge">{u.role}</span></td>
                  <td data-label="Invitation">
                    {!u.invite ? <span className="go-badge warn">never invited</span>
                      : u.invite.status === 'accepted'
                        ? <span className="go-badge live">accepted {when(u.invite.accepted_at)}</span>
                        : u.invite.is_usable
                          ? <span className="go-badge ready">pending, expires {when(u.invite.expires_at)}</span>
                          : <span className="go-badge blocked">{u.invite.status}</span>}
                  </td>
                  <td data-label="Last login">{u.last_login_at ? when(u.last_login_at) : 'never'}</td>
                  <td data-label="">
                    {u.invite && u.invite.status !== 'accepted' ? (
                      <button className="go-btn sm ghost" disabled={busy === 'resend'}
                              onClick={() => act('resend', async () => {
                                const r = await api.post(
                                  '/god/ops/activations/' + u.invite.id + '/resend',
                                  { base_url: window.location.origin })
                                setLink({ url: r.activation_url, email: u.email,
                                          expires_at: r.activation.expires_at })
                              })}>
                        Resend
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          <div className="go-fields">
            <div className="go-field">
              <label>Full name</label>
              <input value={admin.full_name}
                     onChange={e => setAdmin({ ...admin, full_name: e.target.value })} />
            </div>
            <div className="go-field">
              <label>Email</label>
              <input value={admin.email}
                     onChange={e => setAdmin({ ...admin, email: e.target.value })} />
            </div>
            <div className="go-field">
              <label>Role</label>
              <select value={admin.role} onChange={e => setAdmin({ ...admin, role: e.target.value })}>
                <option value="org_admin">org_admin</option>
                <option value="advisor">advisor</option>
                <option value="viewer">viewer</option>
              </select>
            </div>
            <div className="go-field">
              <label>&nbsp;</label>
              <button className="go-btn"
                      disabled={busy === 'admin' || !admin.email.trim() || !admin.full_name.trim()}
                      onClick={() => act('admin', async () => {
                        const r = await api.post(
                          '/god/ops/implementations/' + implId + '/customer-admin',
                          { ...admin, base_url: window.location.origin })
                        setLink({ url: r.activation_url, email: r.user.email,
                                  expires_at: r.activation.expires_at })
                        setAdmin({ full_name: '', email: '', role: 'org_admin' })
                      })}>
                Create &amp; issue link
              </button>
            </div>
          </div>
          <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
            No password is created and nothing is emailed automatically. You get a
            one-time link to send however you choose; the customer sets their own
            password.
          </p>
        </div>
      </Panel>

      {/* ── handoff ───────────────────────────────────────────────────────── */}
      <Panel title="Sales handoff">
        <div className="go-body">
          <div className="go-facts">
            <Fact k="Company (as sold)" v={d.handoff.company} />
            <Fact k="Primary contact" v={d.handoff.primary_contact} />
            <Fact k="Email" v={d.handoff.contact_email} />
            <Fact k="Phone" v={d.handoff.contact_phone} />
            <Fact k="Website" v={d.handoff.website} />
            <Fact k="Industry" v={d.handoff.industry} />
            <Fact k="Timezone" v={d.handoff.timezone} />
            <Fact k="Sold by" v={d.handoff.sold_by} />
          </div>
          {d.handoff.discovery ? (
            <div className="go-facts" style={{ marginTop: 12 }}>
              {Object.keys(d.handoff.discovery).map(k => (
                <Fact key={k} k={d.handoff.discovery[k].label} v={d.handoff.discovery[k].value} />
              ))}
            </div>
          ) : null}
          {d.handoff.notes ? (
            <div className="go-fact" style={{ marginTop: 12 }}>
              <div className="k">Handoff notes</div>
              <div className="v" style={{ whiteSpace: 'pre-wrap' }}>{d.handoff.notes}</div>
            </div>
          ) : null}
        </div>
      </Panel>

      {/* ── billing ───────────────────────────────────────────────────────── */}
      <Panel title="Billing intent">
        <div className="go-body">
          <div className="go-facts">
            <Fact k="Billing status" v={d.billing.billing_status} />
            <Fact k="Implementation fee" v={d.billing.implementation_fee !== null
              ? money(d.billing.implementation_fee, d.billing.currency) : null} />
            <Fact k="Recurring" v={d.billing.recurring_amount !== null
              ? money(d.billing.recurring_amount, d.billing.currency) : null} />
            <Fact k="Billing starts" v={d.billing.billing_start_date ? when(d.billing.billing_start_date) : null} />
            <Fact k="Trial" v={d.billing.trial_start
              ? when(d.billing.trial_start) + ' → ' + when(d.billing.trial_end) : null} />
            <Fact k="External reference" v={d.billing.external_billing_ref} />
          </div>
          <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
            What was agreed in the sale, recorded so somebody can act on it. Nothing
            here charges the customer, and winning a deal never starts a subscription.
          </p>
        </div>
      </Panel>

      {/* ── timeline ──────────────────────────────────────────────────────── */}
      <Panel title="History" count={(d.timeline || []).length}>
        {!(d.timeline || []).length ? <Empty>Nothing recorded yet.</Empty> : (
          <ul className="go-tl">
            {d.timeline.map(t => (
              <li key={t.id}>
                <span>{t.action.replace(/_/g, ' ')}</span>
                {' — '}<span className="who">{t.actor || 'system'}</span>
                <div className="when">{whenExact(t.at)}{t.note ? ' · ' + t.note : ''}</div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}
