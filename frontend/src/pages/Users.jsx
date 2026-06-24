import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './Users.css'

export default function Users() {
  const navigate = useNavigate()
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newEmail, setNewEmail] = useState('')
  const [newName, setNewName] = useState('')
  const [newRole, setNewRole] = useState('advisor')
  const [newPasswordMode, setNewPasswordMode] = useState('generate') // 'generate' | 'specify'
  const [newPassword, setNewPassword] = useState('')
  const [creating, setCreating] = useState(false)
  const [justCreated, setJustCreated] = useState(null) // { email, temp_password }
  const currentUser = getCurrentUser()
  const isSuperAdmin = currentUser?.role === 'super_admin'
  const [sampleDataBusy, setSampleDataBusy] = useState(false)
  const [sampleDataMessage, setSampleDataMessage] = useState('')

  // Reset password — same generate-or-specify choice as create, since
  // Mike explicitly asked for both options on both flows, not just one.
  const [resettingUserId, setResettingUserId] = useState(null)
  const [resetPasswordMode, setResetPasswordMode] = useState('generate')
  const [resetPasswordValue, setResetPasswordValue] = useState('')
  const [resetSaving, setResetSaving] = useState(false)
  const [resetError, setResetError] = useState('')

  // Edit user (name/email/role) — super_admin only, fixes the gap where
  // a typo'd name or wrong email had no in-app fix.
  const [editingUserId, setEditingUserId] = useState(null)
  const [editName, setEditName] = useState('')
  const [editEmail, setEditEmail] = useState('')
  const [editRole, setEditRole] = useState('advisor')
  const [editCanImportLeads, setEditCanImportLeads] = useState(false)
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState('')

  function load() {
    setLoading(true)
    api.get('/admin/users').then(setUsers).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleCreate(e) {
    e.preventDefault()
    if (newPasswordMode === 'specify' && newPassword.trim().length < 8) {
      alert('Password must be at least 8 characters.')
      return
    }
    setCreating(true)
    try {
      const result = await api.post('/admin/users', {
        email: newEmail, full_name: newName, role: newRole,
        password: newPasswordMode === 'specify' ? newPassword.trim() : null,
      })
      setJustCreated({ email: result.email, temp_password: result.temp_password })
      setNewEmail('')
      setNewName('')
      setNewRole('advisor')
      setNewPasswordMode('generate')
      setNewPassword('')
      setShowCreate(false)
      load()
    } catch (err) {
      alert(`Failed to create account: ${err.message}`)
    } finally {
      setCreating(false)
    }
  }

  async function handleDeactivate(userId) {
    if (!confirm('Deactivate this account? They will no longer be able to log in. Their lead history stays intact.')) return
    try {
      await api.patch(`/admin/users/${userId}/deactivate`, {})
      load()
    } catch (err) {
      alert(`Failed: ${err.message}`)
    }
  }

  async function handleReactivate(userId) {
    try {
      await api.patch(`/admin/users/${userId}/reactivate`, {})
      load()
    } catch (err) {
      alert(`Failed: ${err.message}`)
    }
  }

  function startResettingPassword(userId) {
    setResettingUserId(userId)
    setResetPasswordMode('generate')
    setResetPasswordValue('')
    setResetError('')
  }

  async function handleResetPasswordSubmit(userId, userName) {
    if (resetPasswordMode === 'specify' && resetPasswordValue.trim().length < 8) {
      setResetError('Password must be at least 8 characters.')
      return
    }
    if (!confirm(`Reset ${userName}'s password? They will need to confirm/change it on their next login.`)) return

    setResetSaving(true)
    setResetError('')
    try {
      const result = await api.post(`/admin/users/${userId}/reset-password`, {
        password: resetPasswordMode === 'specify' ? resetPasswordValue.trim() : null,
      })
      setJustCreated({ email: result.email, temp_password: result.temp_password, isReset: true })
      setResettingUserId(null)
      setResetPasswordValue('')
    } catch (err) {
      setResetError(err.message || 'Failed to reset password.')
    } finally {
      setResetSaving(false)
    }
  }

  function startEditingUser(u) {
    setEditingUserId(u.id)
    setEditName(u.full_name)
    setEditEmail(u.email)
    setEditRole(u.role)
    setEditCanImportLeads(u.can_import_leads || false)
    setEditError('')
  }

  async function handleSaveUserEdit(userId) {
    setEditSaving(true)
    setEditError('')
    try {
      await api.patch(`/admin/users/${userId}`, {
        full_name: editName,
        email: editEmail,
        // role is only editable for advisor/org_admin accounts - the row
        // is rendered without a role selector for super_admin (see below),
        // so editRole will already equal the unchanged role in that case.
        role: editRole,
        can_import_leads: editCanImportLeads,
      })
      setEditingUserId(null)
      load()
    } catch (err) {
      setEditError(err.message || 'Failed to save changes.')
    } finally {
      setEditSaving(false)
    }
  }

  async function handleGenerateSampleData() {
    setSampleDataBusy(true)
    setSampleDataMessage('')
    try {
      const result = await api.post('/sample-data/generate', {})
      setSampleDataMessage(result.message)
    } catch (err) {
      alert(`Failed to generate sample data: ${err.message}`)
    } finally {
      setSampleDataBusy(false)
    }
  }

  async function handleClearSampleData() {
    if (!confirm('Clear all sample data? This only removes leads tagged as sample data — your real imported leads are never touched.')) return
    setSampleDataBusy(true)
    setSampleDataMessage('')
    try {
      const result = await api.delete('/sample-data/clear')
      setSampleDataMessage(result.message)
    } catch (err) {
      alert(`Failed to clear sample data: ${err.message}`)
    } finally {
      setSampleDataBusy(false)
    }
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Users</h1>
          <p className="page-subtitle">Create and manage advisor accounts for your organization.</p>
        </div>
        <button className="btn btn--primary" onClick={() => setShowCreate(true)}>
          + Create advisor
        </button>
      </header>

      {justCreated && (
        <section className="panel users-created-banner">
          <div className="panel-header">
            <h2 className="panel-title">{justCreated.isReset ? 'Password reset' : 'Account created'}</h2>
            <button className="back-link" onClick={() => setJustCreated(null)}>Dismiss</button>
          </div>
          <p className="users-temp-password-warning">
            This temporary password is shown <strong>once</strong> — copy it now and send it to {justCreated.isReset ? 'them' : 'the advisor'}.
            They'll be required to set their own password on next login.
          </p>
          <div className="users-temp-credentials">
            <div><span className="mono">{justCreated.email}</span></div>
            <div className="users-temp-password">{justCreated.temp_password}</div>
          </div>
        </section>
      )}

      {showCreate && (
        <section className="panel users-create-panel">
          <div className="panel-header">
            <h2 className="panel-title">Create advisor account</h2>
            <button className="back-link" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
          <form onSubmit={handleCreate} className="settings-form">
            <label className="settings-label">
              Full name
              <input className="settings-input" value={newName} onChange={(e) => setNewName(e.target.value)} required />
            </label>
            <label className="settings-label">
              Email
              <input className="settings-input" type="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} required />
            </label>
            <label className="settings-label">
              Role
              <select className="settings-input" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
                <option value="advisor">Advisor</option>
                <option value="org_admin">Org Admin</option>
              </select>
            </label>
            <div className="users-password-choice">
              <label className="users-radio-label">
                <input type="radio" checked={newPasswordMode === 'generate'} onChange={() => setNewPasswordMode('generate')} />
                Generate a temporary password for me
              </label>
              <label className="users-radio-label">
                <input type="radio" checked={newPasswordMode === 'specify'} onChange={() => setNewPasswordMode('specify')} />
                Set their password myself
              </label>
              {newPasswordMode === 'specify' && (
                <input
                  className="settings-input"
                  type="text"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="At least 8 characters"
                  required
                />
              )}
            </div>
            <p className="settings-help">
              Either way, they'll be required to confirm/change this password the first time they log in.
            </p>
            <div className="settings-actions">
              <button className="btn btn--primary" type="submit" disabled={creating}>
                {creating ? 'Creating…' : 'Create account'}
              </button>
            </div>
          </form>
        </section>
      )}

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading users…</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Import access</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isEditingRow = editingUserId === u.id
                return (
                  <tr key={u.id}>
                    {isEditingRow ? (
                      <>
                        <td>
                          <input
                            className="settings-input users-inline-input"
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                          />
                        </td>
                        <td>
                          <input
                            className="settings-input users-inline-input mono"
                            type="email"
                            value={editEmail}
                            onChange={(e) => setEditEmail(e.target.value)}
                          />
                        </td>
                        <td>
                          {u.role === 'super_admin' ? (
                            <span style={{ textTransform: 'capitalize' }}>{u.role.replace('_', ' ')}</span>
                          ) : (
                            <select
                              className="settings-input users-inline-input"
                              value={editRole}
                              onChange={(e) => setEditRole(e.target.value)}
                            >
                              <option value="advisor">Advisor</option>
                              <option value="org_admin">Org Admin</option>
                            </select>
                          )}
                        </td>
                        <td>
                          {u.is_active ? (
                            <span className="badge badge--green">Active</span>
                          ) : (
                            <span className="badge badge--neutral-dim">Deactivated</span>
                          )}
                        </td>
                        <td>
                          {u.role === 'org_admin' || u.role === 'super_admin' ? (
                            <span className="users-import-always-on" title="Admins always have import access">Always on</span>
                          ) : (
                            <label className="compose-checkbox">
                              <input
                                type="checkbox"
                                checked={editCanImportLeads}
                                onChange={(e) => setEditCanImportLeads(e.target.checked)}
                              />
                              Allowed
                            </label>
                          )}
                        </td>
                        <td>
                          <div style={{ display: 'flex', gap: 6, flexDirection: 'column', alignItems: 'flex-start' }}>
                            <div style={{ display: 'flex', gap: 6 }}>
                              <button className="btn btn--secondary" onClick={() => setEditingUserId(null)} disabled={editSaving}>Cancel</button>
                              <button className="btn btn--primary" onClick={() => handleSaveUserEdit(u.id)} disabled={editSaving}>
                                {editSaving ? 'Saving…' : 'Save'}
                              </button>
                            </div>
                            {editError && <div className="compose-error">{editError}</div>}
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td>
                          <button className="user-name-link" onClick={() => navigate(`/users/${u.id}`)}>
                            {u.full_name}
                          </button>
                        </td>
                        <td className="mono">{u.email}</td>
                        <td style={{ textTransform: 'capitalize' }}>{u.role.replace('_', ' ')}</td>
                        <td>
                          {u.is_active ? (
                            <span className="badge badge--green">Active</span>
                          ) : (
                            <span className="badge badge--neutral-dim">Deactivated</span>
                          )}
                          {u.must_change_password && u.is_active && (
                            <span className="badge badge--amber" style={{ marginLeft: 6 }}>Pending setup</span>
                          )}
                        </td>
                        <td>
                          {u.role === 'org_admin' || u.role === 'super_admin' ? (
                            <span className="users-import-always-on">Always on</span>
                          ) : u.can_import_leads ? (
                            <span className="badge badge--green">Allowed</span>
                          ) : (
                            <span className="badge badge--neutral-dim">Admin only</span>
                          )}
                        </td>
                        <td>
                          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                            {isSuperAdmin && (
                              <button className="btn btn--secondary" onClick={() => startEditingUser(u)}>Edit</button>
                            )}
                            {u.is_active ? (
                              <button className="btn btn--danger" onClick={() => handleDeactivate(u.id)}>Deactivate</button>
                            ) : (
                              <button className="btn btn--secondary" onClick={() => handleReactivate(u.id)}>Reactivate</button>
                            )}
                            {isSuperAdmin && (
                              <button className="btn btn--secondary" onClick={() => startResettingPassword(u.id)}>
                                Reset password
                              </button>
                            )}
                          </div>
                          {resettingUserId === u.id && (
                            <div className="users-reset-panel">
                              <label className="users-radio-label">
                                <input type="radio" checked={resetPasswordMode === 'generate'} onChange={() => setResetPasswordMode('generate')} />
                                Generate a temporary password
                              </label>
                              <label className="users-radio-label">
                                <input type="radio" checked={resetPasswordMode === 'specify'} onChange={() => setResetPasswordMode('specify')} />
                                Set their password myself
                              </label>
                              {resetPasswordMode === 'specify' && (
                                <input
                                  className="settings-input"
                                  type="text"
                                  value={resetPasswordValue}
                                  onChange={(e) => setResetPasswordValue(e.target.value)}
                                  placeholder="At least 8 characters"
                                />
                              )}
                              <div style={{ display: 'flex', gap: 6 }}>
                                <button className="btn btn--secondary" onClick={() => setResettingUserId(null)} disabled={resetSaving}>Cancel</button>
                                <button className="btn btn--primary" onClick={() => handleResetPasswordSubmit(u.id, u.full_name)} disabled={resetSaving}>
                                  {resetSaving ? 'Resetting…' : 'Reset password'}
                                </button>
                              </div>
                              {resetError && <div className="compose-error">{resetError}</div>}
                            </div>
                          )}
                        </td>
                      </>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      {isSuperAdmin && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-header">
            <h2 className="panel-title">Sample data</h2>
          </div>
          <p className="ai-quality-text">
            Generate realistic demo leads across every tier and status so you can see what the
            dashboard looks like in real use, then clear it all out when you're ready to start with
            real data. Sample leads are tagged internally and clearing them never touches anything
            you've actually imported.
          </p>
          {sampleDataMessage && (
            <p className="ai-quality-text" style={{ color: 'var(--signal-green)' }}>{sampleDataMessage}</p>
          )}
          <div className="settings-actions" style={{ marginTop: 10 }}>
            <button className="btn btn--primary" onClick={handleGenerateSampleData} disabled={sampleDataBusy}>
              {sampleDataBusy ? 'Working…' : 'Generate sample data'}
            </button>
            <button className="btn btn--danger" onClick={handleClearSampleData} disabled={sampleDataBusy}>
              {sampleDataBusy ? 'Working…' : 'Clear all sample data'}
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
