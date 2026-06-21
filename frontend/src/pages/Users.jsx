import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Users.css'

export default function Users() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newEmail, setNewEmail] = useState('')
  const [newName, setNewName] = useState('')
  const [newRole, setNewRole] = useState('advisor')
  const [creating, setCreating] = useState(false)
  const [justCreated, setJustCreated] = useState(null) // { email, temp_password }

  function load() {
    setLoading(true)
    api.get('/admin/users').then(setUsers).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleCreate(e) {
    e.preventDefault()
    setCreating(true)
    try {
      const result = await api.post('/admin/users', {
        email: newEmail, full_name: newName, role: newRole,
      })
      setJustCreated({ email: result.email, temp_password: result.temp_password })
      setNewEmail('')
      setNewName('')
      setNewRole('advisor')
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
            <h2 className="panel-title">Account created</h2>
            <button className="back-link" onClick={() => setJustCreated(null)}>Dismiss</button>
          </div>
          <p className="users-temp-password-warning">
            This temporary password is shown <strong>once</strong> — copy it now and send it to the advisor.
            They'll be required to set their own password on first login.
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
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.full_name}</td>
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
                    {u.is_active ? (
                      <button className="btn btn--danger" onClick={() => handleDeactivate(u.id)}>Deactivate</button>
                    ) : (
                      <button className="btn btn--secondary" onClick={() => handleReactivate(u.id)}>Reactivate</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
