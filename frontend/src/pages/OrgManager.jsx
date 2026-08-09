import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setOrgContext } from '../api/client'
import './OrgManager.css'

export default function OrgManager() {
  const [orgs, setOrgs] = useState([])
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState({})
  const navigate = useNavigate()

  useEffect(() => {
    async function load() {
      try {
        const [orgsData, usersData] = await Promise.all([
          api.get('/admin/orgs'),
          api.get('/admin/users'),
        ])
        setOrgs(orgsData)
        setUsers(usersData)
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  function handleEnterOrg(org) {
    setOrgContext(org.id, org.name)
    window.location.href = '/'
  }

  function toggleExpand(orgId) {
    setExpanded(prev => ({ ...prev, [orgId]: !prev[orgId] }))
  }

  const usersByOrg = users.reduce((acc, u) => {
    if (!acc[u.organization_id]) acc[u.organization_id] = []
    acc[u.organization_id].push(u)
    return acc
  }, {})

  const filtered = orgs.filter(o =>
    !search || o.name.toLowerCase().includes(search.toLowerCase())
  )

  if (loading) return <div className="org-manager-loading">Loading organizations...</div>
  if (error) return <div className="org-manager-error">Error: {error}</div>

  return (
    <div className="org-manager">
      <div className="org-manager-header">
        <div>
          <h1 className="org-manager-title">Org Manager</h1>
          <p className="org-manager-subtitle">{orgs.length} organization{orgs.length !== 1 ? 's' : ''} on the platform</p>
        </div>
        <input
          className="org-manager-search"
          placeholder="Search organizations..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {filtered.length === 0 && (
        <div className="org-manager-empty">No organizations match your search.</div>
      )}

      <div className="org-grid">
        {filtered.map(org => {
          const orgUsers = usersByOrg[org.id] || []
          const isExpanded = expanded[org.id]
          const adminCount = orgUsers.filter(u => u.role === 'org_admin').length
          const advisorCount = orgUsers.filter(u => u.role === 'advisor').length

          return (
            <div key={org.id} className={`org-card ${!org.is_active ? 'org-card--inactive' : ''}`}>
              <div className="org-card-top">
                <div className="org-card-name-row">
                  <h2 className="org-card-name">{org.name}</h2>
                  {!org.is_active && <span className="org-badge org-badge--inactive">Inactive</span>}
                </div>
                <div className="org-card-badges">
                  <span className={`org-badge org-badge--plan org-badge--${(org.plan || 'trial').toLowerCase()}`}>
                    {org.plan || 'trial'}
                  </span>
                  <span className="org-badge org-badge--industry">{org.industry || 'general'}</span>
                </div>
              </div>

              <div className="org-card-stats">
                <div className="org-stat">
                  <span className="org-stat-value">{orgUsers.length}</span>
                  <span className="org-stat-label">users</span>
                </div>
                <div className="org-stat">
                  <span className="org-stat-value">{adminCount}</span>
                  <span className="org-stat-label">admins</span>
                </div>
                <div className="org-stat">
                  <span className="org-stat-value">{advisorCount}</span>
                  <span className="org-stat-label">advisors</span>
                </div>
              </div>

              <div className="org-card-slug">/{org.slug}</div>

              <div className="org-card-actions">
                <div
                  className="org-expand-toggle"
                  onClick={() => toggleExpand(org.id)}
                >
                  {isExpanded ? '▾ Hide team' : `▸ Team (${orgUsers.length})`}
                </div>
                <button
                  type="button"
                  className="org-enter-btn"
                  onClick={() => handleEnterOrg(org)}
                  title={`View BookaBoost as ${org.name}`}
                >
                  Enter Org →
                </button>
              </div>

              {isExpanded && (
                <div className="org-user-list">
                  {orgUsers.length === 0 && (
                    <p className="org-user-empty">No users in this org yet.</p>
                  )}
                  {orgUsers.map(u => (
                    <div key={u.id} className={`org-user-row ${!u.is_active ? 'org-user-row--inactive' : ''}`}>
                      <div className="org-user-avatar">{(u.full_name || '?')[0].toUpperCase()}</div>
                      <div className="org-user-info">
                        <span className="org-user-name">{u.full_name}</span>
                        <span className="org-user-email">{u.email}</span>
                      </div>
                      <div className="org-user-right">
                        <span className={`role-tag role-tag--${u.role}`}>{u.role.replace(/_/g, ' ')}</span>
                        {!u.is_active && <span className="org-badge org-badge--inactive">off</span>}
                        {u.must_change_password && <span className="org-badge org-badge--warn">setup</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
