import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import StatCard from '../components/StatCard'
import '../styles/shared.css'
import './UserDetail.css'

function formatDate(value) {
  if (!value) return 'Never'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function ActivityRow({ item, onClick }) {
  const isReply = item.type === 'reply'
  return (
    <li className="user-activity-row" onClick={onClick}>
      <span className={`user-activity-dot user-activity-dot--${isReply ? 'reply' : 'sent'}`} aria-hidden="true" />
      <div className="user-activity-body">
        <div className="user-activity-meta">
          <strong>{item.lead_name}</strong>
          <span className="user-activity-type">{isReply ? 'replied' : 'sent'}</span>
          {item.classification && <span className="badge badge--neutral-dim">{item.classification.replace('_', ' ')}</span>}
          <span className="user-activity-time mono">{formatDate(item.timestamp)}</span>
        </div>
        <p className="user-activity-text">{item.body}</p>
      </div>
    </li>
  )
}

export default function UserDetail() {
  const { userId } = useParams()
  const navigate = useNavigate()
  const currentUser = getCurrentUser()
  const isSuperAdmin = currentUser?.role === 'super_admin'

  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editEmail, setEditEmail] = useState('')
  const [editRole, setEditRole] = useState('advisor')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  function load() {
    setLoading(true)
    setError('')
    api.get(`/admin/users/${userId}/detail`)
      .then((data) => {
        setDetail(data)
        setEditName(data.full_name)
        setEditEmail(data.email)
        setEditRole(data.role)
      })
      .catch((err) => setError(err.message || 'Could not load this user.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [userId])

  async function handleSave() {
    setSaving(true)
    setSaveError('')
    try {
      await api.patch(`/admin/users/${userId}`, {
        full_name: editName,
        email: editEmail,
        role: editRole,
      })
      setEditing(false)
      load()
    } catch (err) {
      setSaveError(err.message || 'Failed to save changes.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="empty-state">Loading user…</div>
  if (error) return <div className="empty-state">{error}</div>
  if (!detail) return null

  const m = detail.metrics

  return (
    <div>
      <button className="back-link" onClick={() => navigate('/users')}>← Back to Users</button>

      <header className="page-header">
        <div>
          <h1 className="page-title">{detail.full_name}</h1>
          <p className="page-subtitle">
            {detail.email} · <span style={{ textTransform: 'capitalize' }}>{detail.role.replace('_', ' ')}</span>
            {!detail.is_active && ' · Deactivated'}
          </p>
        </div>
        {isSuperAdmin && !editing && (
          <button className="btn btn--secondary" onClick={() => setEditing(true)}>Edit profile</button>
        )}
      </header>

      {editing && (
        <section className="panel user-detail-edit-panel">
          <div className="panel-header">
            <h2 className="panel-title">Edit profile</h2>
            <button className="back-link" onClick={() => { setEditing(false); setSaveError('') }}>Cancel</button>
          </div>
          <div className="settings-form">
            <label className="settings-label">
              Full name
              <input className="settings-input" value={editName} onChange={(e) => setEditName(e.target.value)} />
            </label>
            <label className="settings-label">
              Email
              <input className="settings-input" type="email" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} />
            </label>
            {detail.role !== 'super_admin' && (
              <label className="settings-label">
                Role
                <select className="settings-input" value={editRole} onChange={(e) => setEditRole(e.target.value)}>
                  <option value="advisor">Advisor</option>
                  <option value="org_admin">Org Admin</option>
                </select>
              </label>
            )}
            <div className="settings-actions">
              {saveError && <div className="compose-error">{saveError}</div>}
              <button className="btn btn--primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </div>
        </section>
      )}

      <p className="user-detail-last-login">Last login: <span className="mono">{formatDate(detail.last_login_at)}</span></p>

      <div className="stat-grid user-detail-stats">
        <StatCard label="Leads owned" value={m.leads_owned} accent="blue" />
        <StatCard label="Messages sent" value={m.messages_sent} accent="neutral" />
        <StatCard label="Replies" value={m.replies} sublabel={`${m.reply_rate}% reply rate`} accent="amber" />
        <StatCard label="Hot replies" value={m.hot_replies} sublabel={`${m.hot_reply_rate}% hot rate`} accent="red" />
        <StatCard label="Booked" value={m.booked_leads} sublabel={`${m.booking_rate}% booking rate`} accent="green" />
        <StatCard label="DNC" value={m.dnc_leads} sublabel={`${m.dnc_rate}% DNC rate`} accent="neutral" />
      </div>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Recent activity</h2>
        </div>
        {detail.recent_activity.length === 0 ? (
          <div className="empty-state">No messages or replies yet for this person.</div>
        ) : (
          <ul className="user-activity-list">
            {detail.recent_activity.map((item, idx) => (
              <ActivityRow
                key={`${item.type}-${item.lead_id}-${idx}`}
                item={item}
                onClick={() => item.lead_id && navigate(`/leads/${item.lead_id}`)}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
