/* OVERVIEW — Activation Readiness, Essentials, Quick Actions, Activity, Metrics.
   Every value is from GET /god/customers/{id}/control-center. */
import { Pill, fmtDate, ago, num, humanAction } from './ccShared'

export default function OverviewTab({ cc, onAction, onActivate, busy }) {
  const r = cc.readiness
  const e = cc.essentials
  const m = cc.metrics
  const primary = e.primary_location
  const items = r.items

  return (
    <div className="occ">
      {/* ── Activation Readiness ─────────────────────────────── */}
      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>Activation Readiness</h3>
            <p className="occ-sub">
              Blueprint: <strong>{cc.blueprint.label}</strong> — {cc.blueprint.selected_because}
            </p>
          </div>
          <div className="occ-progress">
            <span className="occ-muted" style={{ fontSize: 12 }}>
              {r.complete} of {r.total} complete
            </span>
            <div className="occ-progress-bar"><i style={{ width: r.percent + '%' }} /></div>
            <strong style={{ fontSize: 13 }}>{r.percent}%</strong>
          </div>
        </div>
        <div className="occ-strip">
          {items.map(i => {
            const actionable = i.action && i.status !== 'configured'
            return (
              <button key={i.key} type="button"
                      className={'occ-step ' + i.status + (actionable ? ' clickable' : '')}
                      onClick={actionable ? () => onAction(i.action) : undefined}
                      title={i.reason}>
                <span className="occ-step-l">{i.label}</span>
                <Pill status={i.status} label={i.status_label} />
                <span className="occ-step-r">{i.reason}</span>
                {actionable && <span className="occ-step-a">{i.action.label} →</span>}
              </button>
            )
          })}
        </div>
        {r.blockers.length > 0 && (
          <div className="go-warn" style={{ marginTop: 12 }}>
            <strong>Activation blocked:</strong>
            <ul className="go-plain-list">{r.blockers.map((b, k) => <li key={k}>{b}</li>)}</ul>
          </div>
        )}
        {cc.blueprint.missing_required_features.length > 0 && (
          <p className="occ-sub" style={{ marginTop: 10, marginBottom: 0 }}>
            Blueprint features not yet enabled:{' '}
            <strong>{cc.blueprint.missing_required_features.join(', ')}</strong>{' '}
            <button className="go-btn" style={{ marginLeft: 6 }}
                    onClick={() => onAction({ tab: 'entitlements' })}>Review</button>
          </p>
        )}
        <div className="go-actions" style={{ marginTop: 12 }}>
          <button className="go-btn go-btn-primary" disabled={busy || !r.can_activate}
                  onClick={onActivate}>
            {cc.header.is_active ? 'Re-run activation checks' : 'Activate organization'}
          </button>
        </div>
      </section>

      <div className="occ-grid-2">
        {/* ── Organization Essentials ─────────────────────────── */}
        <section className="occ-card">
          <h3>Organization Essentials</h3>
          <p className="occ-sub">Key information and quick actions</p>
          <div className="occ-ess">
            <div>
              <span className="occ-ess-t">Company Information</span>
              <span className="occ-ess-v">{e.company.name}</span>
              <span className="occ-ess-v occ-muted">
                {cc.header.industry_label || e.company.industry || '—'} · {e.company.plan || '—'}
              </span>
              <span className="occ-ess-v occ-muted">Created {fmtDate(e.company.created_at)}</span>
            </div>
            <div>
              <span className="occ-ess-t">Primary Location</span>
              {primary ? (
                <>
                  <span className="occ-ess-v">{primary.name}</span>
                  <span className="occ-ess-v occ-muted">
                    {[primary.address_line1, primary.city, primary.state].filter(Boolean).join(', ') || 'No address'}
                  </span>
                  <span className="occ-ess-v occ-muted">{primary.timezone || 'No timezone'}</span>
                </>
              ) : <span className="occ-ess-v occ-muted">No location yet</span>}
              <button className="go-btn" onClick={() => onAction({ tab: 'locations' })}>
                Manage Locations
              </button>
            </div>
            <div>
              <span className="occ-ess-t">Users</span>
              <span className="occ-ess-v">{e.users.active} active user{e.users.active === 1 ? '' : 's'}</span>
              <span className="occ-ess-v occ-muted">
                {e.users.pending} never signed in · {e.users.admins} admin{e.users.admins === 1 ? '' : 's'}
              </span>
              <button className="go-btn" onClick={() => onAction({ tab: 'people' })}>
                Manage People
              </button>
            </div>
            <div>
              <span className="occ-ess-t">Calendar &amp; Booking</span>
              <Pill status={e.booking.status} />
              <span className="occ-ess-v occ-muted">{e.booking.reason}</span>
              <button className="go-btn" onClick={() => onAction({ tab: 'locations' })}>
                Configure Booking
              </button>
            </div>
            <div>
              <span className="occ-ess-t">Communication Setup</span>
              <span className="occ-ess-v">SMS <Pill status={e.communications.sms} /></span>
              <span className="occ-ess-v">Email <Pill status={e.communications.email} /></span>
              <button className="go-btn" onClick={() => onAction({ tab: 'operations' })}>
                View details
              </button>
            </div>
          </div>
        </section>

        {/* ── Quick Actions — existing flows only ──────────────── */}
        <section className="occ-card">
          <h3>Quick Actions</h3>
          <p className="occ-sub">Each opens an existing, working flow</p>
          <div className="occ-qa">
            <button onClick={() => onAction({ tab: 'locations', open: 'add-location' })}>
              <b>Add Location</b><span>Create a new business location</span>
            </button>
            <button onClick={() => onAction({ tab: 'people', open: 'add-person' })}>
              <b>Invite User</b><span>Add a person and issue a one-time setup link</span>
            </button>
            <button onClick={() => onAction({ tab: 'locations' })}>
              <b>Configure Booking</b><span>Locations, hours and default booking route</span>
            </button>
            <button onClick={() => onAction({ href: '/god/entitlements?org=' + cc.header.id })}>
              <b>Manage Entitlements</b><span>View and configure feature access</span>
            </button>
            <button onClick={() => onAction({ href: '/god/audit?organization_id=' + cc.header.id })}>
              <b>View Activity</b><span>Full audit log for this organization</span>
            </button>
          </div>
        </section>
      </div>

      <div className="occ-grid-2e">
        {/* ── Recent Activity (audit log) ──────────────────────── */}
        <section className="occ-card">
          <div className="occ-card-head">
            <h3>Recent Activity</h3>
            <button className="go-btn"
                    onClick={() => onAction({ href: '/god/audit?organization_id=' + cc.header.id })}>
              View all
            </button>
          </div>
          {cc.recent_activity.length === 0
            ? <div className="occ-empty">No audited activity for this organization yet.</div>
            : (
              <ul className="occ-list">
                {cc.recent_activity.map(a => (
                  <li key={a.id}>
                    <span>
                      <strong>{a.actor || 'System'}</strong>{' '}
                      <span className="occ-muted">{humanAction(a.action)}</span>
                      {a.details && a.details.name && <> — {a.details.name}</>}
                      {a.details && a.details.email && <> — {a.details.email}</>}
                    </span>
                    <span className="occ-muted" title={a.at}>{ago(a.at)}</span>
                  </li>
                ))}
              </ul>
            )}
        </section>

        {/* ── Organization Metrics (all-time counts; no invented trends) ── */}
        <section className="occ-card">
          <h3>Organization Metrics</h3>
          <p className="occ-sub">Counts from stored records. No trend is shown unless one is computed.</p>
          <div className="occ-metrics">
            <div>
              <div className="occ-metric-v">{num(m.leads.total)}</div>
              <div className="occ-metric-k">Leads</div>
              {m.leads.last_30_days !== null &&
                <div className="occ-metric-s">{num(m.leads.last_30_days)} in last 30 days</div>}
            </div>
            <div>
              <div className="occ-metric-v">{num(m.contacts.total)}</div>
              <div className="occ-metric-k">Contacts</div>
            </div>
            <div>
              <div className="occ-metric-v">{num(m.conversations.total)}</div>
              <div className="occ-metric-k">Conversations</div>
            </div>
            <div>
              <div className="occ-metric-v">{num(m.appointments.total)}</div>
              <div className="occ-metric-k">Appointments</div>
            </div>
            <div>
              <div className="occ-metric-v" style={m.deals.total === null ? { fontSize: 13 } : null}>
                {num(m.deals.total)}
              </div>
              <div className="occ-metric-k">Deals</div>
              {m.deals.note && <div className="occ-metric-s">{m.deals.note}</div>}
            </div>
          </div>
        </section>
      </div>

      {/* ── Enabled Tools ───────────────────────────────────────── */}
      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>Enabled Tools</h3>
            <p className="occ-sub">
              {cc.enabled_tools.mode === 'all'
                ? 'Legacy mode: every feature is enabled (no allow-list stored).'
                : cc.enabled_tools.enabled_count + ' feature(s) enabled for this organization.'}
            </p>
          </div>
          <button className="go-btn" onClick={() => onAction({ tab: 'entitlements' })}>View all</button>
        </div>
        {cc.enabled_tools.tools.filter(t => t.enabled).length === 0
          ? <div className="occ-empty">No tools are enabled yet.</div>
          : (
            <div className="occ-tools">
              {cc.enabled_tools.tools.filter(t => t.enabled).map(t => (
                <div key={t.key} className="occ-tool">
                  <b>{t.label}</b>
                  <small>{t.key}</small>
                  <Pill tone={t.tier === 'optional' ? 'info' : 'good'}
                        label={t.tier === 'core_required' || t.tier === 'core' ? 'Core'
                          : t.tier === 'vertical' || t.tier === 'vertical_required' ? 'Vertical'
                          : t.tier === 'optional' ? 'Optional' : 'Not in blueprint'} />
                </div>
              ))}
            </div>
          )}
      </section>
    </div>
  )
}
