/* PEOPLE — name, email, role, workspace, locations, invitation, last login,
   access. One row per human (the server de-duplicates both access routes). */
import { useEffect, useState } from 'react'
import { whenExact } from '../GodOpsShared'
import AddPerson from './AddPerson'
import ResetPasswordDialog from '../ResetPasswordDialog'
import { Pill } from './ccShared'

const INV_TONE = { accepted: 'good', pending: 'info', expired: 'warn', revoked: 'bad',
                   not_invited: 'warn', never_signed_in: 'warn' }
const ACC_TONE = { active: 'good', locked: 'bad', deactivated: 'neutral' }

export default function PeopleTab({ orgId, cc, reload, openAdd, onOpened }) {
  const [invite, setInvite] = useState(null)
  const [resetFor, setResetFor] = useState(null)
  const [flash, setFlash] = useState('')
  const [showAdd, setShowAdd] = useState(cc.people.length === 0)
  const ppl = cc.people

  useEffect(() => {
    if (openAdd) { setShowAdd(true); onOpened && onOpened() }
  }, [openAdd, onOpened])

  return (
    <div className="occ">
      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>People</h3>
            <p className="occ-sub">
              {ppl.length} {ppl.length === 1 ? 'person has' : 'people have'} access to this organization
            </p>
          </div>
          <button className="go-btn go-btn-primary" onClick={() => setShowAdd(s => !s)}>
            {showAdd ? 'Close' : 'Invite user'}
          </button>
        </div>
        {flash && <div className="go-note go-dismiss" onClick={() => setFlash('')}>{flash}</div>}
        {showAdd && (
          <AddPerson orgId={orgId} locations={cc.locations}
                     onAdded={(r) => { setInvite(r); reload() }} />
        )}
        {invite && invite.setup_url && (
          <div className="go-card go-pad" style={{ marginTop: 12 }}>
            <div className="go-card-title">One-time setup link</div>
            <p className="go-hint">
              Shown once. No password exists for this account — this link is the only way in.
              Nothing was emailed or texted; hand it over yourself.
            </p>
            <code className="go-code">{invite.setup_url}</code>
          </div>
        )}

        {ppl.length === 0 ? (
          <div className="occ-empty" style={{ marginTop: 12 }}>
            Nobody has access yet. Invite the organization's first administrator above.
          </div>
        ) : (
          <div className="occ-table-wrap" style={{ marginTop: 12 }}>
            <table className="occ-table">
              <thead>
                <tr>
                  <th>Name</th><th>Email</th><th>Role</th><th>Workspace</th><th>Locations</th>
                  <th>Invitation</th><th>Last login</th><th>Access</th><th />
                </tr>
              </thead>
              <tbody>
                {ppl.map(u => (
                  <tr key={u.id}>
                    <td>
                      <strong>{u.full_name || '—'}</strong>
                      {u.is_seconded && <div className="occ-muted">Seconded (home elsewhere)</div>}
                    </td>
                    <td className="occ-muted">{u.email}</td>
                    <td>{u.role}</td>
                    <td className="occ-muted">{u.workspace}</td>
                    <td className="occ-muted">{u.locations.join(', ') || '—'}</td>
                    <td title={u.invitation ? u.invitation.reason : ''}>
                      {u.invitation
                        ? <Pill tone={INV_TONE[u.invitation.status]} label={u.invitation.label} />
                        : <span className="occ-muted">Not tracked</span>}
                    </td>
                    <td className="occ-muted">{u.last_login_at ? whenExact(u.last_login_at) : 'Never'}</td>
                    <td><Pill tone={ACC_TONE[u.access.status]} label={u.access.label} /></td>
                    <td>
                      <button className="go-btn" onClick={() => setResetFor(u)}>Reset password</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {resetFor && (
        <ResetPasswordDialog
          user={resetFor}
          onCancel={() => setResetFor(null)}
          onDone={(forced) => {
            setFlash('Password reset for ' + (resetFor.full_name || resetFor.email) +
                     (forced ? '. They must change it at next sign-in.' : '.'))
            setResetFor(null)
            reload()
          }} />
      )}
    </div>
  )
}
