import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setOrgContext } from '../api/client'
import './OrgManager.css'

const ALL_FEATURES = [
  // Core admin tools
  { key: 'master_dashboard', label: 'Master Dashboard' },
  { key: 'reports',          label: 'Reports' },
  { key: 'users',            label: 'Users' },
  { key: 'availability',     label: 'Availability' },
  { key: 'campaigns',        label: 'Campaigns' },
  // CRM
  { key: 'crm',              label: 'CRM (Contact Management)' },
  { key: 'crm_connectors',   label: 'CRM Connectors (GoHighLevel / HubSpot)' },
  // Lead tools
  { key: 'lead_cleanup',     label: 'Lead Cleanup' },
  // System config
  { key: 'tier_config',      label: 'Tier Config' },
  { key: 'a2p_10dlc',        label: 'A2P 10DLC Registration' },
  { key: 'branding_settings',label: 'Branding & Settings' },
  // Compliance
  { key: 'compliance',       label: 'Compliance' },
  { key: 'audit_log',        label: 'Audit Log' },
]
export default function OrgManager() {
  const [orgs, setOrgs] = useState([])
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState({})
  const [featuresExpanded, setFeaturesExpanded] = useState({})
  const [orgFeatures, setOrgFeatures] = useState({})
  const [saving, setSaving] = useState({})
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
        const featInit = {}
        orgsData.forEach(o => {
          featInit[o.id] = (o.enabled_features !== undefined && o.enabled_features !== null)
            ? o.enabled_features : null
        })
        setOrgFeatures(featInit)
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

  function toggleFeaturesExpand(orgId) {
    setFeaturesExpanded(prev => ({ ...prev, [orgId]: !prev[orgId] }))
  }

  function toggleFeature(orgId, key) {
    setOrgFeatures(prev => {
      const current = prev[orgId]
      const asList = current === null ? ALL_FEATURES.map(f => f.key) : [...current]
      const idx = asList.indexOf(key)
      if (idx === -1) asList.push(key)
      else asList.splice(idx, 1)
      return { ...prev, [orgId]: asList }
    })
  }

  function grantAll(orgId) {
    setOrgFeatures(prev => ({ ...prev, [orgId]: null }))
  }

  async function saveFeatures(orgId) {
    setSaving(prev => ({ ...prev, [orgId]: true }))
    try {
      await api.patch(`/org-settings/features?org_id=${orgId}`, {
        enabled_features: orgFeatures[orgId],
      })
    } catch (e) {
      alert('Failed to save: ' + e.message)
    } finally {
      setSaving(prev => ({ ...prev, [orgId]: false }))
    }
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
          const isFeatExpanded = featuresExpanded[org.id]
          const adminCount = orgUsers.filter(u => u.role === 'org_admin').length
          const advisorCount = orgUsers.filter(u => u.role === 'advisor').length
          const features = orgFeatures[org.id]

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
                <div className="org-expand-toggle" onClick={() => toggleExpand(org.id)}>
                  {isExpanded ? '\u25be Hide team' : `\u25b8 Team (${orgUsers.length})`}
                </div>
                <div className="org-expand-toggle" onClick={() => toggleFeaturesExpand(org.id)}>
                  {isFeatExpanded ? '\u25be Hide features' : '\u2699\ufe0f Features'}
                </div>
                <button
                  type="button"
                  className="org-enter-btn"
                  onClick={() => handleEnterOrg(org)}
                  title={`View BookaBoost as ${org.name}`}
                >
                  Enter Org \u2192
                </button>
              </div>

              {isFeatExpanded && (
                <div className="org-features-section">
                  <div className="org-features-header">
                    <span className="org-features-title">
                      Admin Feature Access{' '}
                      {features === null
                        ? <span className="org-features-status org-features-status--all">All enabled</span>
                        : <span className="org-features-status">{features.length}/{ALL_FEATURES.length} enabled</span>
                      }
                    </span>
                    <button type="button" className="org-features-grant-all" onClick={() => grantAll(org.id)}>
                      Grant All
                    </button>
                  </div>
                  <div className="org-features-grid">
                    {ALL_FEATURES.map(f => {
                      const checked = features === null || features.includes(f.key)
                      return (
                        <label key={f.key} className="org-feature-checkbox">
                          <input type="checkbox" checked={checked} onChange={() => toggleFeature(org.id, f.key)} />
                          <span>{f.label}</span>
                        </label>
                      )
                    })}
                  </div>
                  <button
                    type="button"
                    className="org-features-save"
                    onClick={() => saveFeatures(org.id)}
                    disabled={saving[org.id]}
                  >
                    {saving[org.id] ? 'Saving...' : 'Save Features'}
                  </button>
                </div>
              )}

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
