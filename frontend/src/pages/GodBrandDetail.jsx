/**
 * God Mode — one brand's operations (Checkpoint 6 §3, §4).
 *
 * Two contexts on one page, kept visibly separate: the brand's SALES operation
 * on the left of the mental model, and the CUSTOMERS it produced below it.
 * They are not the same tree and the screen does not pretend otherwise.
 *
 * The configuration section only shows settings backed by real columns. There
 * is no generic settings framework here on purpose — an editor for a field
 * nothing reads is worse than no editor at all.
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Kpi, Panel, Empty, Fact, money, when, whenExact, StatusBadge, errText, Bar } from './god/GodOpsShared'
import AddSalesUser from './god/AddSalesUser'
import PackagePricing from './god/PackagePricing'
import './god/GodOps.css'

export default function GodBrandDetail() {
  const { brandId } = useParams()
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [team, setTeam] = useState(null)
  const [busy, setBusy] = useState('')
  const [link, setLink] = useState(null)
  const [adding, setAdding] = useState(false)

  const loadTeam = () => api.get('/god/ops/brands/' + brandId + '/sales-team')
    .then(r => setTeam(r.team || []))
    .catch(() => setTeam([]))

  // Who can be named as somebody's reporting manager. The SERVER decides this
  // and sends a flag; the browser does not interpret a role string to work it
  // out. That keeps this dropdown and `assert_manager_ok` from ever disagreeing
  // about who is eligible, and keeps this screen out of the business of reading
  // roles at all — a habit that becomes a permission decision eventually.
  const managers = (team || []).filter(t => t.can_be_reporting_manager)

  // Role, reporting line and active state all go through the one PATCH, so the
  // table always redraws from the server's answer rather than from what the
  // browser assumed happened.
  async function patchMembership(u, body) {
    setBusy(u.user_id); setErr('')
    try {
      const r = await api.patch('/god/ops/sales-memberships/' + u.membership_id, body)
      setTeam(r.team || [])
    } catch (e) {
      setErr(errText(e))
      await loadTeam()          // put the row back to the truth
    } finally { setBusy('') }
  }

  // Named, because the pricing editor has to be able to redraw the catalogue
  // from the server after a save rather than patching what the browser assumed.
  const loadBrand = () => api.get('/god/ops/brands/' + brandId)
    .then(setD).catch(e => setErr(errText(e)))

  useEffect(() => {
    loadBrand()
    loadTeam()
  }, [brandId])

  // A one-time link, shown once. It is not stored anywhere and navigating away
  // loses it - that is the design, so the UI has to make it hard to miss.
  async function issueLink(u, purpose) {
    setBusy(u.user_id); setErr(''); setLink(null)
    try {
      const r = await api.post('/god/ops/sales-users/' + u.user_id + '/setup-link', {
        brand_sales_org_id: brandId, purpose, base_url: window.location.origin,
      })
      setLink({ url: r.setup_url, name: r.user.full_name, email: r.user.email,
                purpose: r.activation.purpose, expires_at: r.activation.expires_at })
      await loadTeam()
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }

  // An error BEFORE the brand loads is fatal - there is no page to draw. An
  // error after it is not: a rejected role change used to blank the entire
  // screen, which loses the operator's place and hides the very table that
  // would show them what actually happened. Once `d` exists the message goes
  // inline, above the panels, and everything else stays put.
  if (!d) {
    return (
      <div className="go-scope">
        {err ? <div className="go-note err">{err}</div>
             : <div className="go-empty">Loading…</div>}
      </div>
    )
  }

  const s = d.summary || {}
  const cfg = d.configuration || {}

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/sales-operations')}>← Sales Operations</button>
          <h1 style={{ marginTop: 8 }}>{s.brand_sales_org_name}</h1>
          <p>{s.platform ? s.platform.name + ' platform' : 'No platform'} ·{' '}
             {s.is_active ? 'active' : 'inactive'}</p>
        </div>
      </div>

      {err ? (
        <div className="go-note err">
          {err}
          <button className="go-btn sm ghost" style={{ marginLeft: 10 }}
                  onClick={() => setErr('')}>Dismiss</button>
        </div>
      ) : null}

      {s.attention && s.attention.length ? (
        <div className="go-note warn">
          <strong>Needs attention</strong>
          <ul>{s.attention.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      ) : null}

      <div className="go-kpis">
        <Kpi label="Open opportunities" value={s.open_opportunities} />
        <Kpi label="Pipeline value" value={money(s.pipeline_value)} />
        <Kpi label="Closing" value={s.closing_opportunities} />
        <Kpi label="Meetings" value={s.meetings_scheduled} />
        <Kpi label="Proposals out" value={s.proposals_outstanding}
             sub={(s.proposals_with_buyer_activity || 0) + ' viewed by the buyer'} />
        <Kpi label="Won" value={s.won_deals} sub={money(s.won_value)} />
        <Kpi label="Awaiting provisioning" value={s.won_awaiting_provisioning}
             tone={s.won_awaiting_provisioning > 0 ? 'alert' : undefined} />
        <Kpi label="Customers live" value={s.customers_live} tone="good" />
      </div>

      <Panel title="Sales team">
        <div className="go-body">
          <div className="go-facts">
            <Fact k="Sales managers"
                  v={s.managers && s.managers.length
                     ? s.managers.map(m => m.name + (m.is_active ? '' : ' (inactive)')).join(', ')
                     : null} />
            <Fact k="Representatives" v={s.rep_count} />
            <Fact k="Active representatives" v={s.active_rep_count} />
            <Fact k="Stalled opportunities" v={s.stalled_opportunities} />
            <Fact k="Overdue next actions" v={s.overdue_next_actions} />
          </div>
        </div>
      </Panel>

      <PackagePricing packages={cfg.packages} onSaved={loadBrand} />

      {link ? (
        <div className="go-note warn">
          <strong>One-time {link.purpose === 'reset' ? 'reset' : 'setup'} link — shown once,
          not recoverable.</strong>{' '}
          Send it to {link.name} ({link.email}). It expires {whenExact(link.expires_at)}.
          No password was created or changed, and their sales role is untouched.
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

      {adding ? (
        <AddSalesUser
          brandId={brandId}
          brandName={s.brand_sales_org_name}
          managers={managers}
          onClose={() => setAdding(false)}
          onAdded={r => {
            setAdding(false)
            setTeam(r.team || [])
            if (r.setup_url) {
              setLink({ url: r.setup_url, name: r.user.full_name, email: r.user.email,
                        purpose: r.activation?.purpose,
                        expires_at: r.activation?.expires_at,
                        created: r.user.created })
            }
          }}
        />
      ) : null}

      <Panel
        title="Sales team"
        count={team ? team.length : null}
        actions={
          <button className="go-btn sm" onClick={() => { setErr(''); setAdding(true) }}>
            + Add sales user
          </button>
        }
      >
        {team === null ? <Empty>Loading…</Empty>
          : !team.length ? (
            <Empty>
              Nobody holds a sales membership in this brand yet. Use
              <strong> + Add sales user</strong> — it looks the person up by email
              first, so an existing account is reused rather than duplicated.
            </Empty>
          ) : (
          <table className="go-table">
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th>Manager</th>
                  <th>Membership</th><th>Setup</th><th>Last login</th><th></th></tr>
            </thead>
            <tbody>
              {team.map(u => (
                <tr key={u.user_id}>
                  <td data-label="Name">
                    {u.full_name}
                    {!u.user_is_active
                      ? <span className="go-badge blocked" style={{ marginLeft: 8 }}>deactivated</span>
                      : null}
                    {u.organization_id
                      ? <div style={{ fontSize: 11, color: 'var(--go-red)', marginTop: 3 }}>
                          also inside a customer tenant — verify this is intended</div>
                      : null}
                  </td>
                  <td data-label="Email">{u.email}</td>
                  <td data-label="Role">
                    <select
                      className="go-input sm"
                      value={u.role}
                      disabled={busy === u.user_id || !u.membership_is_active}
                      onChange={e => patchMembership(u, { role: e.target.value })}
                    >
                      <option value="sales_rep">rep</option>
                      <option value="sales_manager">manager</option>
                    </select>
                  </td>
                  <td data-label="Manager">
                    {/* A manager reports to nobody inside their own brand, so
                        the cell is a dash rather than an empty control. The
                        server clears the line on promotion for the same reason. */}
                    {u.can_be_reporting_manager
                      ? <span className="go-badge">—</span>
                      : (
                        <select
                          className="go-input sm"
                          value={u.reports_to_user_id || ''}
                          disabled={busy === u.user_id || !u.membership_is_active}
                          onChange={e => patchMembership(u, {
                            set_reports_to: true,
                            reports_to_user_id: e.target.value || null,
                          })}
                        >
                          <option value="">unassigned</option>
                          {managers.map(m => (
                            <option key={m.user_id} value={m.user_id}>{m.full_name}</option>
                          ))}
                        </select>
                      )}
                  </td>
                  <td data-label="Membership">
                    {u.membership_is_active
                      ? <span className="go-badge live">active</span>
                      : <span className="go-badge blocked">inactive</span>}
                  </td>
                  <td data-label="Setup">
                    {!u.access.link ? <span className="go-badge">none issued</span>
                      : u.access.link.status === 'accepted'
                        ? <span className="go-badge live">used {when(u.access.link.accepted_at)}</span>
                        : u.access.link.is_usable
                          ? <span className="go-badge ready">live until {when(u.access.link.expires_at)}</span>
                          : <span className="go-badge">{u.access.link.status}</span>}
                  </td>
                  <td data-label="Last login">
                    {u.access.has_signed_in
                      ? when(u.access.last_login_at)
                      : <span className="go-badge warn">never</span>}
                  </td>
                  <td data-label="">
                    <div className="go-actions">
                      <button className="go-btn sm"
                              disabled={busy === u.user_id || !u.user_is_active || !u.membership_is_active}
                              onClick={() => issueLink(u, u.access.has_signed_in ? 'reset' : 'setup')}>
                        {u.access.has_signed_in ? 'Reset access' : 'Generate setup link'}
                      </button>
                      {u.access.link && u.access.link.is_usable ? (
                        <button className="go-btn sm ghost" disabled={busy === u.user_id}
                                onClick={async () => {
                                  setBusy(u.user_id)
                                  try {
                                    await api.post('/god/ops/staff-activations/' +
                                                   u.access.link.id + '/revoke')
                                    await loadTeam()
                                  } catch (e) { setErr(errText(e)) } finally { setBusy('') }
                                }}>
                          Revoke link
                        </button>
                      ) : null}
                      <button className="go-btn sm ghost" disabled={busy === u.user_id}
                              onClick={() => patchMembership(u, { is_active: !u.membership_is_active })}>
                        {u.membership_is_active ? 'Deactivate' : 'Reactivate'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--go-dim)' }}>
            Adding looks the person up by email first: an existing account is reused
            and its other memberships are left alone, so one human never becomes two
            rows. A new identity is created with <em>no</em> customer organisation.
            Deactivating a seat closes the workspace but keeps their opportunities,
            meetings and audit history. No password is ever created, shown or set on
            anyone's behalf — access arrives as a one-time link, and issuing a new
            one revokes any outstanding one.
          </p>
        </div>
      </Panel>

      <Panel title="Customers from this brand" count={(d.implementations || []).length}>
        {!(d.implementations || []).length ? (
          <Empty>No deals from this brand have been provisioned yet.</Empty>
        ) : (
          <table className="go-table">
            <thead>
              <tr><th>Customer</th><th>Package</th><th>Owner</th><th>Status</th>
                  <th>Progress</th><th>Launch</th></tr>
            </thead>
            <tbody>
              {d.implementations.map(i => (
                <tr key={i.implementation_id} className="clickable"
                    onClick={() => nav('/god/implementations/' + i.implementation_id)}>
                  <td data-label="Customer">{i.organization_name}</td>
                  <td data-label="Package">{i.package ? i.package.name : '—'}</td>
                  <td data-label="Owner">{i.owner ? i.owner.name : <span className="go-badge warn">unassigned</span>}</td>
                  <td data-label="Status"><StatusBadge status={i.status} /></td>
                  <td data-label="Progress">
                    {i.percent_complete}%<Bar percent={i.percent_complete} />
                  </td>
                  <td data-label="Launch">
                    {i.is_live ? when(i.launched_at)
                      : (i.target_launch_date
                         ? <span className={i.is_overdue ? 'go-badge blocked' : ''}>
                             {when(i.target_launch_date)}</span>
                         : '—')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}
