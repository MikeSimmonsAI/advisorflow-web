import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setOrgContext } from '../api/client'
import './OrgManager.css'

// THE FEATURE LIST NO LONGER LIVES HERE.
//
// This file used to carry its own thirteen-key list, `Layout.jsx` carried a
// different nine, and the server's registry held a third fourteen. Three
// vocabularies, one `enabled_features` column, and no agreement between them:
// seven of the keys this screen could write had never existed on the server, so
// writing them produced entitlements the God Features screen then reported as
// unknown.
//
// It is now fetched from GET /god/customers/{id}/features, whose `available`
// array is built from the server's registry. Add a feature in
// app/services/entitlements.py and it appears here; there is nothing to keep in
// sync because there is only one list.
//
// `a2p_10dlc` is deliberately gone rather than moved: A2P registration is not a
// feature a customer USES, it is infrastructure somebody ADMINISTERS, and it
// now lives behind the two delegation gates on the customer's Administration
// panel. Granting "all features" must never hand over the power to re-register
// a customer's carrier brand.
const FEATURES_FALLBACK = []
// Plan tiers — matches BookaBoost / EvoSys Pro pricing
const PLANS = [
  { value: 'trial',        label: 'Trial',        price: null },
  { value: 'starter',      label: 'Starter',      price: '$500/mo' },
  { value: 'growth',       label: 'Growth',       price: '$1,000/mo' },
  { value: 'professional', label: 'Professional', price: '$2,000/mo' },
  { value: 'enterprise',   label: 'Enterprise',   price: 'Custom' },
]

// Features each plan tier includes by default.
//
// `a2p_10dlc` HAS BEEN REMOVED FROM EVERY TIER. It was in growth, professional
// and standard, which meant buying the Growth plan silently included the right
// to register the customer's A2P brand and campaign against their Twilio
// account. A2P is no longer a feature at all: it is a capability behind the two
// delegation gates, granted per organization and then per named administrator
// on that customer's Administration panel. A plan can sell the SMS service; it
// cannot sell the carrier identity behind it.
const PLAN_FEATURES = {
  trial:        ['master_dashboard', 'users', 'reports', 'availability', 'tier_config', 'branding_settings', 'compliance', 'audit_log'],
  starter:      ['master_dashboard', 'users', 'reports', 'availability', 'tier_config', 'branding_settings', 'compliance', 'audit_log', 'campaigns'],
  growth:       ['master_dashboard', 'users', 'reports', 'availability', 'tier_config', 'branding_settings', 'compliance', 'audit_log', 'campaigns', 'lead_cleanup'],
  professional: ['master_dashboard', 'users', 'reports', 'availability', 'tier_config', 'branding_settings', 'compliance', 'audit_log', 'campaigns', 'lead_cleanup', 'crm', 'crm_connectors'],
  enterprise:   null, // null = all features
  // legacy alias kept so existing orgs on 'standard' still work
  standard:     ['master_dashboard', 'users', 'reports', 'availability', 'tier_config', 'branding_settings', 'compliance', 'audit_log', 'campaigns', 'lead_cleanup'],
}

function getPlanLabel(plan) {
  const found = PLANS.find(p => p.value === plan)
  return found ? found.label.toUpperCase() : (plan || 'TRIAL').toUpperCase()
}

function getBelowPlanCount(plan, currentFeatures) {
  const expected = PLAN_FEATURES[plan]
  if (!expected) return 0  // enterprise = all, never below
  if (currentFeatures === null) return 0  // already has all
  const missing = expected.filter(f => !currentFeatures.includes(f))
  return missing.length
}

// Color per platform slug — new platforms get a neutral fallback
const PLATFORM_COLORS = {
  bookaboost:    { bg: 'rgba(47,182,255,0.15)', border: 'rgba(47,182,255,0.4)',  text: '#2fb6ff' },
  evosyspro:     { bg: 'rgba(30,240,168,0.15)', border: 'rgba(30,240,168,0.4)',  text: '#1ef0a8' },
  harmonyhustle: { bg: 'rgba(245,158,11,0.15)', border: 'rgba(245,158,11,0.4)',  text: '#f59e0b' },
}
const PLATFORM_COLOR_DEFAULT = { bg: 'rgba(148,163,184,0.15)', border: 'rgba(148,163,184,0.4)', text: '#94a3b8' }

function platformColor(slug) {
  return PLATFORM_COLORS[slug] || PLATFORM_COLOR_DEFAULT
}

export default function OrgManager() {
  const [orgs, setOrgs] = useState([])
  const [users, setUsers] = useState([])
  const [availablePlatforms, setAvailablePlatforms] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [platformFilter, setPlatformFilter] = useState('all')
  const [expanded, setExpanded] = useState({})
  const [featuresExpanded, setFeaturesExpanded] = useState({})
  // The server's feature registry. Empty until fetched, and left empty if the
  // fetch fails — see FEATURES_FALLBACK at the top of this file.
  const [allFeatures, setAllFeatures] = useState(FEATURES_FALLBACK)
  const [orgFeatures, setOrgFeatures] = useState({})
  const [saving, setSaving] = useState({})
  const [platformSaving, setPlatformSaving] = useState({})
  const [planSaving, setPlanSaving] = useState({})
  const [editingName, setEditingName] = useState({})   // { [orgId]: draftName }
  const [nameSaving, setNameSaving] = useState({})
  const navigate = useNavigate()

  useEffect(() => {
    async function load() {
      try {
        const [orgsData, usersData, platformsData] = await Promise.all([
          api.get('/admin/orgs'),
          api.get('/admin/users'),
          api.get('/admin/platforms'),
        ])
        setOrgs(orgsData)
        setUsers(usersData)
        setAvailablePlatforms(platformsData)
        const featInit = {}
        orgsData.forEach(o => {
          featInit[o.id] = (o.enabled_features !== undefined && o.enabled_features !== null)
            ? o.enabled_features : null
        })
        setOrgFeatures(featInit)

        // THE ONE FEATURE VOCABULARY, fetched rather than hardcoded.
        // `available` is built from the server registry, so this screen and
        // the God Features screen can no longer offer different keys.
        if (orgsData.length) {
          try {
            const rep = await api.get(`/god/customers/${orgsData[0].id}/features`)
            if (Array.isArray(rep?.available)) {
              setAllFeatures(rep.available.map(f => ({ key: f.key, label: f.label })))
            }
          } catch {
            // Leave the list empty rather than falling back to a local copy.
            // An empty panel that says so is honest; a stale local list is the
            // bug this change exists to remove.
          }
        }
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  // Derive unique platforms from org list
  const platforms = Array.from(
    new Map(
      orgs
        .filter(o => o.platform_slug)
        .map(o => [o.platform_slug, { slug: o.platform_slug, name: o.platform_name || o.platform_slug }])
    ).values()
  ).sort((a, b) => a.name.localeCompare(b.name))

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
      const asList = current === null ? allFeatures.map(f => f.key) : [...current]
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

  async function saveOrgPlan(orgId, newPlan) {
    setPlanSaving(prev => ({ ...prev, [orgId]: true }))
    try {
      await api.put(`/admin/organizations/${orgId}`, { plan: newPlan })
      setOrgs(prev => prev.map(o => o.id === orgId ? { ...o, plan: newPlan } : o))
    } catch (e) {
      alert('Failed to update plan: ' + e.message)
    } finally {
      setPlanSaving(prev => ({ ...prev, [orgId]: false }))
    }
  }

  function startEditName(org) {
    setEditingName(prev => ({ ...prev, [org.id]: org.name }))
  }

  function cancelEditName(orgId) {
    setEditingName(prev => { const n = { ...prev }; delete n[orgId]; return n })
  }

  async function saveOrgName(orgId) {
    const newName = (editingName[orgId] || '').trim()
    if (!newName) return
    setNameSaving(prev => ({ ...prev, [orgId]: true }))
    try {
      await api.put(`/admin/organizations/${orgId}`, { name: newName })
      setOrgs(prev => prev.map(o => o.id === orgId ? { ...o, name: newName } : o))
      cancelEditName(orgId)
    } catch (e) {
      alert('Failed to rename: ' + e.message)
    } finally {
      setNameSaving(prev => ({ ...prev, [orgId]: false }))
    }
  }

  async function assignPlatform(orgId, platformId) {
    setPlatformSaving(prev => ({ ...prev, [orgId]: true }))
    try {
      const result = await api.patch(`/admin/orgs/${orgId}/platform`, {
        platform_id: platformId || null,
      })
      setOrgs(prev => prev.map(o => o.id === orgId ? {
        ...o,
        platform_id: result.platform_id,
        platform_name: result.platform_name,
        platform_slug: result.platform_slug,
      } : o))
    } catch (e) {
      alert('Failed to assign platform: ' + e.message)
    } finally {
      setPlatformSaving(prev => ({ ...prev, [orgId]: false }))
    }
  }

  async function applyPlanDefaults(org) {
    const plan = (org.plan || 'trial').toLowerCase()
    const defaults = PLAN_FEATURES[plan] || PLAN_FEATURES.trial
    setOrgFeatures(prev => ({ ...prev, [org.id]: defaults }))
    setSaving(prev => ({ ...prev, [org.id]: true }))
    try {
      await api.patch(`/org-settings/features?org_id=${org.id}`, {
        enabled_features: defaults,
      })
    } catch (e) {
      alert('Failed to apply plan defaults: ' + e.message)
    } finally {
      setSaving(prev => ({ ...prev, [org.id]: false }))
    }
  }
  const usersByOrg = users.reduce((acc, u) => {
    if (!acc[u.organization_id]) acc[u.organization_id] = []
    acc[u.organization_id].push(u)
    return acc
  }, {})

  // Filter orgs by platform tab + search
  const filtered = orgs.filter(o => {
    const matchesPlatform = platformFilter === 'all' || o.platform_slug === platformFilter || (!o.platform_slug && platformFilter === 'unassigned')
    const matchesSearch = !search || o.name.toLowerCase().includes(search.toLowerCase())
    return matchesPlatform && matchesSearch
  })

  // Group filtered orgs by platform for display
  const grouped = filtered.reduce((acc, org) => {
    const key = org.platform_slug || 'unassigned'
    if (!acc[key]) acc[key] = { name: org.platform_name || (org.platform_slug ? org.platform_slug : 'Unassigned'), slug: key, orgs: [] }
    acc[key].orgs.push(org)
    return acc
  }, {})
  const groups = Object.values(grouped).sort((a, b) => {
    if (a.slug === 'unassigned') return 1
    if (b.slug === 'unassigned') return -1
    return a.name.localeCompare(b.name)
  })

  if (loading) return <div className="org-manager-loading">Loading organizations…</div>
  if (error)   return <div className="org-manager-error">Error: {error}</div>

  return (
    <div className="org-manager">
      {/* ── Header ── */}
      <div className="org-manager-header">
        <div>
          <h1 className="org-manager-title">Org Manager</h1>
          <p className="org-manager-subtitle">
            {orgs.length} org{orgs.length !== 1 ? 's' : ''} across {platforms.length} platform{platforms.length !== 1 ? 's' : ''}
          </p>
        </div>
        <input
          className="org-manager-search"
          placeholder="Search organizations…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* ── Platform filter tabs ── */}
      <div className="org-platform-tabs">
        <button
          className={`org-platform-tab ${platformFilter === 'all' ? 'org-platform-tab--active' : ''}`}
          onClick={() => setPlatformFilter('all')}
        >
          All <span className="org-tab-count">{orgs.length}</span>
        </button>
        {platforms.map(p => {
          const color = platformColor(p.slug)
          const count = orgs.filter(o => o.platform_slug === p.slug).length
          return (
            <button
              key={p.slug}
              className={`org-platform-tab ${platformFilter === p.slug ? 'org-platform-tab--active' : ''}`}
              style={platformFilter === p.slug ? { borderColor: color.border, color: color.text } : {}}
              onClick={() => setPlatformFilter(p.slug)}
            >
              <span
                className="org-platform-dot"
                style={{ background: color.text }}
              />
              {p.name}
              <span className="org-tab-count">{count}</span>
            </button>
          )
        })}
        {orgs.some(o => !o.platform_slug) && (
          <button
            className={`org-platform-tab ${platformFilter === 'unassigned' ? 'org-platform-tab--active' : ''}`}
            onClick={() => setPlatformFilter('unassigned')}
          >
            Unassigned <span className="org-tab-count">{orgs.filter(o => !o.platform_slug).length}</span>
          </button>
        )}
      </div>

      {filtered.length === 0 && (
        <div className="org-manager-empty">No organizations match your filter.</div>
      )}

      {/* ── Grouped org cards ── */}
      {groups.map(group => {
        const color = platformColor(group.slug)
        return (
          <div key={group.slug} className="org-platform-group">
            {/* Only show group header when showing all platforms */}
            {platformFilter === 'all' && (
              <div className="org-platform-group-header" style={{ borderColor: color.border }}>
                <span className="org-platform-group-dot" style={{ background: color.text }} />
                <span className="org-platform-group-name" style={{ color: color.text }}>{group.name}</span>
                <span className="org-platform-group-count">{group.orgs.length} org{group.orgs.length !== 1 ? 's' : ''}</span>
              </div>
            )}

            <div className="org-grid">
              {group.orgs.map(org => {
                const orgUsers = usersByOrg[org.id] || []
                const isExpanded = expanded[org.id]
                const isFeatExpanded = featuresExpanded[org.id]
                const adminCount = orgUsers.filter(u => u.role === 'org_admin').length
                const advisorCount = orgUsers.filter(u => u.role === 'advisor').length
                const features = orgFeatures[org.id]
                const pColor = platformColor(org.platform_slug)

                return (
                  <div key={org.id} className={`org-card ${!org.is_active ? 'org-card--inactive' : ''}`}
                    style={{ borderTopColor: pColor.text, borderTopWidth: 2 }}
                  >
                    <div className="org-card-top">
                      <div className="org-card-name-row">
                        {editingName[org.id] !== undefined ? (
                          <div className="org-name-edit">
                            <input
                              className="org-name-input"
                              value={editingName[org.id]}
                              autoFocus
                              onChange={e => setEditingName(prev => ({ ...prev, [org.id]: e.target.value }))}
                              onKeyDown={e => {
                                if (e.key === 'Enter') saveOrgName(org.id)
                                if (e.key === 'Escape') cancelEditName(org.id)
                              }}
                            />
                            <button className="org-name-save-btn" onClick={() => saveOrgName(org.id)} disabled={nameSaving[org.id]}>
                              {nameSaving[org.id] ? '…' : '✓'}
                            </button>
                            <button className="org-name-cancel-btn" onClick={() => cancelEditName(org.id)}>✕</button>
                          </div>
                        ) : (
                          <h2 className="org-card-name" title="Click to rename" onClick={() => startEditName(org)} style={{ cursor: 'pointer' }}>
                            {org.name} <span className="org-name-edit-hint">✏️</span>
                          </h2>
                        )}
                        {!org.is_active && <span className="org-badge org-badge--inactive">Inactive</span>}
                      </div>
                      <div className="org-card-badges">
                        {/* Platform badge */}
                        {org.platform_name && (
                          <span
                            className="org-badge org-badge--platform"
                            style={{ background: pColor.bg, border: `1px solid ${pColor.border}`, color: pColor.text }}
                          >
                            {org.platform_name}
                          </span>
                        )}
                        <span className={`org-badge org-badge--plan org-badge--${(org.plan || 'trial').toLowerCase()}`}>
                          {getPlanLabel(org.plan || 'trial')}
                        </span>
                        <span className="org-badge org-badge--industry">{org.industry || 'general'}</span>
                      </div>
                    </div>

                    {/* ── Plan selector ── */}
                    <div className="org-platform-assign">
                      <label className="org-platform-assign-label">Plan</label>
                      <select
                        className="org-platform-assign-select org-plan-select"
                        value={org.plan || 'trial'}
                        disabled={planSaving[org.id]}
                        onChange={e => saveOrgPlan(org.id, e.target.value)}
                      >
                        {PLANS.map(p => (
                          <option key={p.value} value={p.value}>
                            {p.label}{p.price ? ` — ${p.price}` : ''}
                          </option>
                        ))}
                      </select>
                      {planSaving[org.id] && <span className="org-platform-saving">Saving…</span>}
                    </div>

                    {/* ── Platform assignment ── */}
                    <div className="org-platform-assign">
                      <label className="org-platform-assign-label">Platform</label>
                      <select
                        className="org-platform-assign-select"
                        value={org.platform_id || ''}
                        disabled={platformSaving[org.id]}
                        onChange={e => assignPlatform(org.id, e.target.value || null)}
                        style={org.platform_slug ? { borderColor: pColor.border, color: pColor.text } : {}}
                      >
                        <option value="">— Unassigned —</option>
                        {availablePlatforms.map(p => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                      {platformSaving[org.id] && <span className="org-platform-saving">Saving…</span>}
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
                        {isExpanded ? '▾ Hide team' : `▸ Team (${orgUsers.length})`}
                      </div>
                      <div className="org-expand-toggle" onClick={() => toggleFeaturesExpand(org.id)}>
                        {isFeatExpanded ? '▾ Hide features' : '⚙️ Features'}
                      </div>
                      <button
                        type="button"
                        className="org-enter-btn"
                        onClick={() => handleEnterOrg(org)}
                      >
                        Enter Org →
                      </button>
                    </div>

                    {isFeatExpanded && (
                      <div className="org-features-section">
                        <div className="org-features-header">
                          <span className="org-features-title">
                            Admin Feature Access{' '}
                            {features === null
                              ? <span className="org-features-status org-features-status--all">All enabled</span>
                              : <span className="org-features-status">{features.length}/{allFeatures.length} enabled</span>
                            }
                          </span>
                          <button type="button" className="org-features-grant-all" onClick={() => grantAll(org.id)}>
                            Grant All
                          </button>
                        </div>
                        <div className="org-features-grid">
                          {allFeatures.length === 0 && (
                            <p className="org-user-empty">
                              Feature list unavailable — could not read the server
                              registry.
                            </p>
                          )}
                          {allFeatures.map(f => {
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
                          {saving[org.id] ? 'Saving…' : 'Save Features'}
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
      })}
    </div>
  )
}
