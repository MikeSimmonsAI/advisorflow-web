import { useEffect, useState, useMemo } from 'react'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './Users.css'

const ROLE_LABELS = { advisor: 'Advisor', org_admin: 'Org Admin', super_admin: 'Super Admin' }
const ROLE_COLORS = { advisor: 'blue', org_admin: 'purple', super_admin: 'amber' }

function initials(name) {
  if (!name) return '?'
  return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
}

function avatarColor(name) {
  const colors = ['#2563eb','#7c3aed','#0891b2','#059669','#d97706','#dc2626']
  let h = 0
  for (let i = 0; i < (name || '').length; i++) h = (name.charCodeAt(i) + ((h << 5) - h))
  return colors[Math.abs(h) % colors.length]
}

// ── User Card ──────────────────────────────────────────────────────────────
function UserCard({ u, isSuperAdmin, stats, onDeactivate, onReactivate, onEdit, onResetPw, onClearSetup }) {
  const leads = stats?.leads_owned ?? stats?.total_leads ?? null
  const msgs  = stats?.messages_sent ?? null
  const color = avatarColor(u.full_name)
  const inactive = !u.is_active

  return (
    <div className={`uc ${inactive ? 'uc--off' : ''}`}>
      <div className="uc-avatar" style={{ background: color + '22', borderColor: color + '55' }}>
        <span style={{ color }}>{initials(u.full_name)}</span>
      </div>

      <div className="uc-body">
        <div className="uc-top">
          <div>
            <div className="uc-name">{u.full_name || <span style={{color:'var(--text-tertiary)'}}>No name</span>}</div>
            <div className="uc-email">{u.email}</div>
            {isSuperAdmin && u.organization_name && (
              <div className="uc-org">{u.organization_name}</div>
            )}
          </div>
          <div className="uc-badges">
            <span className={`badge badge--${ROLE_COLORS[u.role] || 'blue'}`}>{ROLE_LABELS[u.role] || u.role}</span>
            {inactive
              ? <span className="badge badge--neutral">Inactive</span>
              : <span className="badge badge--green">Active</span>}
            {u.must_change_password && !inactive && (
              <span className="badge badge--amber" title="User has not logged in yet">Pending</span>
            )}
          </div>
        </div>

        <div className="uc-stats">
          <div className="uc-stat">
            <span className="uc-stat-n">{leads ?? '—'}</span>
            <span className="uc-stat-l">Leads</span>
          </div>
          <div className="uc-stat">
            <span className="uc-stat-n">{msgs ?? '—'}</span>
            <span className="uc-stat-l">Messages</span>
          </div>
        </div>

        <div className="uc-actions">
          {isSuperAdmin && (
            <button className="btn btn--secondary btn--sm" onClick={() => onEdit(u)}>Edit</button>
          )}
          {u.must_change_password && !inactive && (
            <button className="btn btn--secondary btn--sm" title="Clear pending setup flag" onClick={() => onClearSetup(u.id)}>
              ✓ Mark active
            </button>
          )}
          <button className="btn btn--secondary btn--sm" onClick={() => onResetPw(u)}>Set password</button>
          {inactive
            ? <button className="btn btn--secondary btn--sm" onClick={() => onReactivate(u.id)}>Reactivate</button>
            : <button className="btn btn--danger btn--sm" onClick={() => onDeactivate(u.id)}>Deactivate</button>}
        </div>
      </div>
    </div>
  )
}

// ── Modal wrapper ──────────────────────────────────────────────────────────
function Modal({ title, onClose, children }) {
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal-box">
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function Users() {
  const currentUser = getCurrentUser()
  const isSuperAdmin = currentUser?.role === 'super_admin'
  const isAdmin = currentUser?.role === 'org_admin' || isSuperAdmin

  const [users, setUsers] = useState([])
  const [stats, setStats] = useState({})   // keyed by user id
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')

  // Modals
  const [showCreate, setShowCreate] = useState(false)
  const [editUser, setEditUser]   = useState(null)
  const [resetUser, setResetUser] = useState(null)

  // Forms
  const blankCreate = { full_name: '', email: '', role: 'advisor', password: '' }
  const [createForm, setCreateForm] = useState(blankCreate)
  const [createError, setCreateError] = useState('')
  const [createLoading, setCreateLoading] = useState(false)

  const [editForm, setEditForm]   = useState({})
  const [editError, setEditError] = useState('')
  const [editLoading, setEditLoading] = useState(false)

  const [pwForm, setPwForm]       = useState({ password: '', confirm: '' })
  const [pwError, setPwError]     = useState('')
  const [pwLoading, setPwLoading] = useState(false)

  // ── Fetch ────────────────────────────────────────────────────────────────
  const fetchUsers = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.get('/admin/users')
      setUsers(Array.isArray(data) ? data : (data.users || []))
    } catch (e) {
      setError(e.message || 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }

  const fetchStats = async () => {
    try {
      const data = await api.get('/admin/dashboard')
      const map = {}
      const list = data?.per_advisor_stats ?? data?.advisors ?? []
      list.forEach(s => {
        const id = s.user_id ?? s.advisor_id
        if (id) map[id] = s
      })
      setStats(map)
    } catch (_) {}
  }

  useEffect(() => {
    fetchUsers()
    fetchStats()
  }, [])

  // ── Filter + search ──────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    let list = users
    if (filter === 'active')  list = list.filter(u => u.is_active && !u.must_change_password)
    if (filter === 'pending') list = list.filter(u => u.must_change_password && u.is_active)
    if (filter === 'inactive')list = list.filter(u => !u.is_active)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(u =>
        (u.full_name || '').toLowerCase().includes(q) ||
        (u.email || '').toLowerCase().includes(q) ||
        (u.organization_name || '').toLowerCase().includes(q)
      )
    }
    return list
  }, [users, filter, search])

  const counts = useMemo(() => ({
    all:      users.length,
    active:   users.filter(u => u.is_active && !u.must_change_password).length,
    pending:  users.filter(u => u.must_change_password && u.is_active).length,
    inactive: users.filter(u => !u.is_active).length,
  }), [users])

  // ── Actions ──────────────────────────────────────────────────────────────
  const deactivate = async (id) => {
    await api.patch(`/admin/users/${id}/deactivate`)
    await fetchUsers()
  }
  const reactivate = async (id) => {
    await api.patch(`/admin/users/${id}/reactivate`)
    await fetchUsers()
  }

  const clearSetup = async (id) => {
    try {
      await api.patch(`/admin/users/${id}/clear-setup`)
      setUsers(prev => prev.map(u => u.id === id ? { ...u, must_change_password: false } : u))
    } catch (e) {
      alert('Could not clear setup flag: ' + (e.message || e))
    }
  }

  const handleCreate = async (e) => {
    e.preventDefault()
    setCreateError('')
    if (!createForm.email) { setCreateError('Email is required'); return }
    setCreateLoading(true)
    try {
      await api.post('/admin/users', createForm)
      setShowCreate(false)
      setCreateForm(blankCreate)
      await fetchUsers()
    } catch (err) {
      setCreateError(err.message || 'Failed to create user')
    } finally {
      setCreateLoading(false)
    }
  }

  const handleEdit = async (e) => {
    e.preventDefault()
    setEditError('')
    setEditLoading(true)
    try {
      await api.patch(`/admin/users/${editUser.id}`, editForm)
      setEditUser(null)
      await fetchUsers()
    } catch (err) {
      setEditError(err.message || 'Failed to update user')
    } finally {
      setEditLoading(false)
    }
  }

  const handleResetPw = async (e) => {
    e.preventDefault()
    setPwError('')
    if (pwForm.password !== pwForm.confirm) { setPwError('Passwords do not match'); return }
    if (pwForm.password.length < 6) { setPwError('Password must be at least 6 characters'); return }
    setPwLoading(true)
    try {
      await api.post(`/admin/users/${resetUser.id}/reset-password`, { new_password: pwForm.password })
      setResetUser(null)
      setPwForm({ password: '', confirm: '' })
      await fetchUsers()
    } catch (err) {
      setPwError(err.message || 'Failed to reset password')
    } finally {
      setPwLoading(false)
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="page-wrap">
      {/* Header */}
      <div className="users-header">
        <div>
          <h1 className="page-title">Team</h1>
          <p className="page-subtitle">
            {counts.all} member{counts.all !== 1 ? 's' : ''}
            {counts.pending > 0 && <span className="header-pending-badge">{counts.pending} pending setup</span>}
          </p>
        </div>
        {isAdmin && (
          <button className="btn btn--primary" onClick={() => { setCreateForm(blankCreate); setCreateError(''); setShowCreate(true) }}>
            + Add member
          </button>
        )}
      </div>

      {/* Search + filters */}
      <div className="users-controls">
        <input
          className="users-search"
          placeholder="Search by name, email, or org…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <div className="filter-tabs">
          {[['all','All'],['active','Active'],['pending','Pending'],['inactive','Inactive']].map(([key, label]) => (
            <button key={key} className={`filter-tab ${filter === key ? 'filter-tab--active' : ''}`} onClick={() => setFilter(key)}>
              {label} <span className="filter-tab-count">{counts[key]}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      {loading && <div className="users-state">Loading members…</div>}
      {error   && <div className="users-state users-state--error">{error}</div>}
      {!loading && !error && filtered.length === 0 && (
        <div className="users-state">No members match this filter.</div>
      )}
      {!loading && !error && filtered.length > 0 && (
        <div className="users-grid">
          {filtered.map(u => (
            <UserCard
              key={u.id} u={u}
              isSuperAdmin={isSuperAdmin}
              stats={stats[u.id]}
              onDeactivate={deactivate}
              onReactivate={reactivate}
              onEdit={usr => { setEditUser(usr); setEditForm({ full_name: usr.full_name, email: usr.email, role: usr.role }); setEditError('') }}
              onResetPw={u => { setResetUser(u); setPwForm({ password: '', confirm: '' }); setPwError('') }}
              onClearSetup={clearSetup}
            />
          ))}
        </div>
      )}

      {/* ── Create Modal ── */}
      {showCreate && (
        <Modal title="Add team member" onClose={() => setShowCreate(false)}>
          <form onSubmit={handleCreate} className="modal-form">
            <label>Full name
              <input value={createForm.full_name} onChange={e => setCreateForm(f => ({...f, full_name: e.target.value}))} placeholder="Jane Smith" />
            </label>
            <label>Email <span className="req">*</span>
              <input type="email" required value={createForm.email} onChange={e => setCreateForm(f => ({...f, email: e.target.value}))} placeholder="jane@example.com" />
            </label>
            <label>Role
              <select value={createForm.role} onChange={e => setCreateForm(f => ({...f, role: e.target.value}))}>
                <option value="advisor">Advisor</option>
                <option value="org_admin">Org Admin</option>
                {isSuperAdmin && <option value="super_admin">Super Admin</option>}
              </select>
            </label>
            <label>Password <span style={{color:'var(--text-tertiary)',fontWeight:400,fontSize:12}}>(leave blank to auto-generate)</span>
              <input type="password" value={createForm.password} onChange={e => setCreateForm(f => ({...f, password: e.target.value}))} placeholder="Auto-generate if blank" />
            </label>
            {createError && <div className="form-error">{createError}</div>}
            <div className="modal-actions">
              <button type="button" className="btn btn--secondary" onClick={() => setShowCreate(false)}>Cancel</button>
              <button type="submit" className="btn btn--primary" disabled={createLoading}>{createLoading ? 'Creating…' : 'Create member'}</button>
            </div>
          </form>
        </Modal>
      )}

      {/* ── Edit Modal (super admin only) ── */}
      {editUser && (
        <Modal title={`Edit — ${editUser.full_name || editUser.email}`} onClose={() => setEditUser(null)}>
          <form onSubmit={handleEdit} className="modal-form">
            <label>Full name
              <input value={editForm.full_name || ''} onChange={e => setEditForm(f => ({...f, full_name: e.target.value}))} />
            </label>
            <label>Email
              <input type="email" value={editForm.email || ''} onChange={e => setEditForm(f => ({...f, email: e.target.value}))} />
            </label>
            <label>Role
              <select value={editForm.role || 'advisor'} onChange={e => setEditForm(f => ({...f, role: e.target.value}))}>
                <option value="advisor">Advisor</option>
                <option value="org_admin">Org Admin</option>
                <option value="super_admin">Super Admin</option>
              </select>
            </label>
            {editError && <div className="form-error">{editError}</div>}
            <div className="modal-actions">
              <button type="button" className="btn btn--secondary" onClick={() => setEditUser(null)}>Cancel</button>
              <button type="submit" className="btn btn--primary" disabled={editLoading}>{editLoading ? 'Saving…' : 'Save changes'}</button>
            </div>
          </form>
        </Modal>
      )}

      {/* ── Reset Password Modal ── */}
      {resetUser && (
        <Modal title={`Set password — ${resetUser.full_name || resetUser.email}`} onClose={() => setResetUser(null)}>
          <form onSubmit={handleResetPw} className="modal-form">
            <label>New password
              <input type="password" required value={pwForm.password} onChange={e => setPwForm(f => ({...f, password: e.target.value}))} placeholder="Min. 6 characters" />
            </label>
            <label>Confirm password
              <input type="password" required value={pwForm.confirm} onChange={e => setPwForm(f => ({...f, confirm: e.target.value}))} placeholder="Repeat password" />
            </label>
            {pwError && <div className="form-error">{pwError}</div>}
            <div className="modal-actions">
              <button type="button" className="btn btn--secondary" onClick={() => setResetUser(null)}>Cancel</button>
              <button type="submit" className="btn btn--primary" disabled={pwLoading}>{pwLoading ? 'Saving…' : 'Set password'}</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  )
}
