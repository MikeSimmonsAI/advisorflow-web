/* ADMINISTRATION — who can change what, in three plain groups:
     Organization self-management   (org admins, no delegation needed)
     Authorized administrators      (delegated capabilities — both gates)
     Platform-only controls         (never delegable)
   The switches for the two delegation gates are the existing ones
   (AdministrationGates), unchanged. */
import AdministrationGates from './AdministrationGates'
import { Pill } from './ccShared'

export default function AdministrationTab({ orgId, cc, reload }) {
  const a = cc.administration
  return (
    <div className="occ">
      <section className="occ-card">
        <h3>Organization self-management</h3>
        <p className="occ-sub">
          What this organization's own admins (org_admin) can change today, with nothing delegated.
        </p>
        <ul className="occ-list">
          {a.self_manageable.map(s => (
            <li key={s.key}>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>{s.label}</strong>
                <div className="occ-muted">{s.changed_by} · {s.gate}</div>
              </span>
              <Pill tone={s.available ? 'good' : 'neutral'}
                    label={s.available ? 'Self-managed' : 'Feature off'} />
            </li>
          ))}
        </ul>
      </section>

      <section className="occ-card">
        <h3>Authorized administrators</h3>
        <p className="occ-sub">
          Infrastructure an organization may administer only when the platform allows it
          AND a specific administrator is granted it.
        </p>
        <ul className="occ-list">
          {a.authorized_admin.map(c => (
            <li key={c.key}>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>{c.label}</strong>
                <div className="occ-muted">
                  {c.holders.length ? 'Held by ' + c.holders.join(', ') : 'Nobody holds this'}
                  {c.blocked_reason ? ' · ' + c.blocked_reason : ''}
                </div>
              </span>
              <Pill tone={c.self_management_allowed ? 'good' : 'neutral'}
                    label={c.self_management_allowed ? 'Delegated' : 'Platform-managed'} />
            </li>
          ))}
        </ul>
      </section>

      <section className="occ-card">
        <h3>Platform-only controls</h3>
        <p className="occ-sub">Changed only by the platform. The server refuses to delegate these.</p>
        <ul className="occ-list">
          {a.platform_only.map(c => (
            <li key={c.key}>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>{c.label}</strong>
                <div className="occ-muted">{c.why}</div>
              </span>
              <span className="occ-muted" style={{ fontSize: 12 }}>{c.changed_by}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="occ-card">
        <h3>Delegation switches</h3>
        <p className="occ-sub">The two gates, set separately.</p>
        <AdministrationGates orgId={orgId} onChanged={reload} />
      </section>
    </div>
  )
}
