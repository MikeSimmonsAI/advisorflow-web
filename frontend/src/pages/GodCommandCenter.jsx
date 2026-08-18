/**
 * AdvisorFlow Command Center — god_admin only.
 * Invisible to every role below god_admin.
 * Shows every platform, org, lead, and user across the entire system.
 */

import { useEffect, useState, useCallback } from 'react'
import { apiRequest } from '../api/client'
import './GodCommandCenter.css'

// ── icons (inline SVG so no dep needed) ──────────────────────────────────────
const Icon = ({ name, size = 16 }) => {
  const paths = {
    globe:   <><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></>,
    building:<><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 22V12h6v10M3 9h18M9 3v6M15 3v6"/></>,
    users:   <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></>,
    leads:   <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></>,
    shield:  <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></>,
    search:  <><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></>,
    refresh: <><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></>,
    chevron: <><polyline points="9 18 15 12 9 6"/></>,
    zap:     <><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></>,
    eye:     <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></>,
    check:   <><polyline points="20 6 9 17 4 12"/></>,
    x:       <><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></>,
    star:    <><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></>,
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  )
}

// ── KPI tile ──────────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, color = '#4ade80', icon }) {
  return (
    <div className="gcc-stat-card">
      <div className="gcc-stat-icon" style={{ color }}><Icon name={icon} size={20} /></div>
      <div className="gcc-stat-body">
        <div className="gcc-stat-value" style={{ color }}>{value ?? '—'}</div>
        <div className="gcc-stat-label">{label}</div>
        {sub && <div className="gcc-stat-sub">{sub}</div>}
      </div>
    </div>
  )
}

// ── Platform badge ────────────────────────────────────────────────────────────
const PLATFORM_COLORS = {
  bookaboost: '#3b82f6',
  evosys:     '#0ea5e9',
  harmony:    '#10b981',
  default:    '#6366f1',
}
function PlatformBadge({ slug, name }) {
  const color = PLATFORM_COLORS[slug] || PLATFORM_COLORS.default
  return (
    <span className="gcc-platform-badge" style={{ background: color + '22', color, border: `1px solid ${color}44` }}>
      {name || slug}
    </span>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────
const TABS = [
  { key: 'overview',  label: 'Overview',  icon: 'globe' },
  { key: 'platforms', label: 'Platforms', icon: 'zap' },
  { key: 'orgs',      label: 'Orgs',      icon: 'building' },
  { key: 'leads',     label: 'Leads',     icon: 'leads' },
  { key: 'users',     label: 'Users',     icon: 'users' },
]

// ── Role badge ────────────────────────────────────────────────────────────────
const ROLE_COLOR = {
  god_admin:   '#f59e0b',
  super_admin: '#6366f1',
  org_admin:   '#3b82f6',
  advisor:     '#6b7280',
  viewer:      '#374151',
}
function RoleBadge({ role }) {
  const color = ROLE_COLOR[role] || '#6b7280'
  return (
    <span className="gcc-role-badge" style={{ background: color + '22', color, border: `1px solid ${color}44` }}>
      {role?.replace('_', ' ')}
    </span>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function GodCommandCenter() {
  const [tab, setTab]             = useState('overview')
  const [stats, setStats]         = useState(null)
  const [platforms, setPlatforms] = useState([])
  const [orgs, setOrgs]           = useState({ total: 0, orgs: [] })
  const [leads, setLeads]         = useState({ total: 0, leads: [] })
  const [users, setUsers]         = useState({ total: 0, users: [] })

  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState(null)

  // filters
  const [platformFilter, setPlatformFilter] = useState('')
  const [search, setSearch]                 = useState('')
  const [roleFilter, setRoleFilter]         = useState('')
  const [statusFilter, setStatusFilter]     = useState('')

  // user role edit
  const [editingUser, setEditingUser] = useState(null)
  const [newRole, setNewRole]         = useState('')
  const [saving, setSaving]           = useState(false)
  const [toast, setToast]             = useState(null)

  const showToast = (msg, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  // ── Fetch helpers ────────────────────────────────────────────────────────────
  const loadStats = useCallback(async () => {
    try {
      const d = await apiRequest('/god/stats')
      setStats(d)
    } catch (e) { setError(e.message) }
  }, [])

  const loadPlatforms = useCallback(async () => {
    try {
      const d = await apiRequest('/god/platforms')
      setPlatforms(d)
    } catch (e) { setError(e.message) }
  }, [])

  const loadOrgs = useCallback(async () => {
    const params = new URLSearchParams({ limit: 100 })
    if (platformFilter) params.set('platform_slug', platformFilter)
    if (search)         params.set('search', search)
    try {
      const d = await apiRequest(`/god/orgs?${params}`)
      setOrgs(d)
    } catch (e) { setError(e.message) }
  }, [platformFilter, search])

  const loadLeads = useCallback(async () => {
    const params = new URLSearchParams({ limit: 100 })
    if (platformFilter) params.set('platform_slug', platformFilter)
    if (search)         params.set('search', search)
    if (statusFilter)   params.set('status', statusFilter)
    try {
      const d = await apiRequest(`/god/leads?${params}`)
      setLeads(d)
    } catch (e) { setError(e.message) }
  }, [platformFilter, search, statusFilter])

  const loadUsers = useCallback(async () => {
    const params = new URLSearchParams({ limit: 100 })
    if (roleFilter) params.set('role', roleFilter)
    if (search)     params.set('search', search)
    try {
      const d = await apiRequest(`/god/users?${params}`)
      setUsers(d)
    } catch (e) { setError(e.message) }
  }, [roleFilter, search])

  // ── Refresh current tab ─────────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      if (tab === 'overview')  { await loadStats(); await loadPlatforms() }
      if (tab === 'platforms') { await loadPlatforms() }
      if (tab === 'orgs')      { await loadOrgs() }
      if (tab === 'leads')     { await loadLeads() }
      if (tab === 'users')     { await loadUsers() }
    } finally { setLoading(false) }
  }, [tab, loadStats, loadPlatforms, loadOrgs, loadLeads, loadUsers])

  useEffect(() => { refresh() }, [tab, platformFilter, search, roleFilter, statusFilter]) // eslint-disable-line

  // ── Role update ──────────────────────────────────────────────────────────────
  const applyRoleChange = async () => {
    if (!editingUser || !newRole) return
    setSaving(true)
    try {
      await apiRequest(`/god/users/${editingUser.id}/role`, {
        method: 'PATCH',
        body: JSON.stringify({ role: newRole }),
      })
      showToast(`${editingUser.email} → ${newRole}`)
      setEditingUser(null)
      loadUsers()
    } catch (e) {
      showToast(e.message, 'error')
    } finally { setSaving(false) }
  }

  const toggleActive = async (u) => {
    const ep = u.is_active ? 'deactivate' : 'activate'
    try {
      await apiRequest(`/god/users/${u.id}/${ep}`, { method: 'POST' })
      showToast(`${u.email} ${u.is_active ? 'deactivated' : 'activated'}`)
      loadUsers()
    } catch (e) { showToast(e.message, 'error') }
  }

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div className="gcc-root">
      {/* Toast */}
      {toast && (
        <div className={`gcc-toast gcc-toast--${toast.type}`}>{toast.msg}</div>
      )}

      {/* Role edit modal */}
      {editingUser && (
        <div className="gcc-modal-overlay" onClick={() => setEditingUser(null)}>
          <div className="gcc-modal" onClick={e => e.stopPropagation()}>
            <div className="gcc-modal-title">Change Role</div>
            <div className="gcc-modal-email">{editingUser.email}</div>
            <select
              className="gcc-select"
              value={newRole}
              onChange={e => setNewRole(e.target.value)}
            >
              <option value="">— select role —</option>
              {['god_admin','super_admin','org_admin','advisor','viewer'].map(r => (
                <option key={r} value={r}>{r.replace('_',' ')}</option>
              ))}
            </select>
            <div className="gcc-modal-actions">
              <button className="gcc-btn gcc-btn--ghost" onClick={() => setEditingUser(null)}>Cancel</button>
              <button className="gcc-btn gcc-btn--primary" onClick={applyRoleChange} disabled={!newRole || saving}>
                {saving ? 'Saving…' : 'Apply'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="gcc-header">
        <div className="gcc-header-left">
          <div className="gcc-logo">⚡</div>
          <div>
            <div className="gcc-title">AdvisorFlow Command Center</div>
            <div className="gcc-subtitle">god_admin · full system access</div>
          </div>
        </div>
        <button className="gcc-btn gcc-btn--ghost gcc-refresh" onClick={refresh} disabled={loading}>
          <Icon name="refresh" size={14} />
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && <div className="gcc-error">⚠ {error}</div>}

      {/* Tab bar */}
      <div className="gcc-tabs">
        {TABS.map(t => (
          <button
            key={t.key}
            className={`gcc-tab ${tab === t.key ? 'gcc-tab--active' : ''}`}
            onClick={() => { setTab(t.key); setSearch(''); setPlatformFilter(''); setRoleFilter(''); setStatusFilter('') }}
          >
            <Icon name={t.icon} size={14} />
            {t.label}
          </button>
        ))}
      </div>

      {/* ── OVERVIEW ── */}
      {tab === 'overview' && stats && (
        <div className="gcc-content">
          <div className="gcc-stats-grid">
            <StatCard label="Platforms"     value={stats.total_platforms}  icon="zap"      color="#f59e0b" />
            <StatCard label="Total Orgs"    value={stats.total_orgs}       icon="building" color="#6366f1" />
            <StatCard label="Active Orgs"   value={stats.active_orgs}      icon="building" color="#10b981" />
            <StatCard label="Total Leads"   value={stats.total_leads}      icon="leads"    color="#3b82f6" sub={`+${stats.new_leads_30d} last 30d`} />
            <StatCard label="Total Users"   value={stats.total_users}      icon="users"    color="#06b6d4" />
            <StatCard label="Admin Accounts" value={stats.total_admins}    icon="shield"   color="#f59e0b" />
          </div>

          {platforms.length > 0 && (
            <>
              <div className="gcc-section-title">Platforms</div>
              <div className="gcc-platform-cards">
                {platforms.map(p => (
                  <div key={p.id || p.slug} className="gcc-platform-card">
                    <div className="gcc-platform-name">
                      <PlatformBadge slug={p.slug} name={p.name} />
                      {p.is_active !== false
                        ? <span className="gcc-active-dot" title="Active">●</span>
                        : <span className="gcc-inactive-dot" title="Inactive">●</span>}
                    </div>
                    {p.domain && <div className="gcc-platform-domain">{p.domain}</div>}
                    <div className="gcc-platform-meta">
                      <span>{p.org_count} orgs</span>
                      <span>{p.lead_count} leads</span>
                      <span>{p.user_count} users</span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* ── PLATFORMS ── */}
      {tab === 'platforms' && (
        <div className="gcc-content">
          {platforms.length === 0 && !loading && (
            <div className="gcc-empty">No platforms found — seed the platforms table first.</div>
          )}
          <div className="gcc-platform-cards">
            {platforms.map(p => (
              <div key={p.id || p.slug} className="gcc-platform-card gcc-platform-card--lg">
                <div className="gcc-platform-name">
                  <PlatformBadge slug={p.slug} name={p.name} />
                </div>
                <div className="gcc-kv-row"><span>Slug</span><code>{p.slug}</code></div>
                {p.domain        && <div className="gcc-kv-row"><span>Domain</span><code>{p.domain}</code></div>}
                {p.support_email && <div className="gcc-kv-row"><span>Support Email</span><code>{p.support_email}</code></div>}
                <div className="gcc-kv-row"><span>Status</span><span>{p.is_active !== false ? '🟢 Active' : '🔴 Inactive'}</span></div>
                <div className="gcc-platform-meta">
                  <span>{p.org_count} orgs</span>
                  <span>{p.lead_count} leads</span>
                  <span>{p.user_count} users</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── ORGS ── */}
      {tab === 'orgs' && (
        <div className="gcc-content">
          <div className="gcc-filter-bar">
            <div className="gcc-search-wrap">
              <Icon name="search" size={14} />
              <input className="gcc-search" placeholder="Search orgs…" value={search}
                onChange={e => setSearch(e.target.value)} />
            </div>
            <select className="gcc-select" value={platformFilter} onChange={e => setPlatformFilter(e.target.value)}>
              <option value="">All Platforms</option>
              {platforms.map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
            </select>
            <div className="gcc-total">{orgs.total} orgs</div>
          </div>

          <div className="gcc-table-wrap">
            <table className="gcc-table">
              <thead>
                <tr>
                  <th>Organization</th>
                  <th>Platform</th>
                  <th>Leads</th>
                  <th>Users</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {orgs.orgs.map(o => (
                  <tr key={o.id}>
                    <td><span className="gcc-org-name">{o.name}</span></td>
                    <td>{o.platform_id
                      ? <PlatformBadge slug={platforms.find(p=>p.id===o.platform_id)?.slug || ''} name={platforms.find(p=>p.id===o.platform_id)?.name || o.platform_id} />
                      : <span className="gcc-dim">—</span>}
                    </td>
                    <td>{o.lead_count}</td>
                    <td>{o.user_count}</td>
                    <td className="gcc-dim">{o.created_at ? o.created_at.slice(0,10) : '—'}</td>
                  </tr>
                ))}
                {orgs.orgs.length === 0 && !loading && (
                  <tr><td colSpan={5} className="gcc-empty">No orgs found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── LEADS ── */}
      {tab === 'leads' && (
        <div className="gcc-content">
          <div className="gcc-filter-bar">
            <div className="gcc-search-wrap">
              <Icon name="search" size={14} />
              <input className="gcc-search" placeholder="Search name, email, phone…" value={search}
                onChange={e => setSearch(e.target.value)} />
            </div>
            <select className="gcc-select" value={platformFilter} onChange={e => setPlatformFilter(e.target.value)}>
              <option value="">All Platforms</option>
              {platforms.map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}
            </select>
            <select className="gcc-select" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
              <option value="">All Statuses</option>
              {['new','contacted','replied','qualified','booked','closed','lost','dnc'].map(s =>
                <option key={s} value={s}>{s}</option>
              )}
            </select>
            <div className="gcc-total">{leads.total.toLocaleString()} leads</div>
          </div>

          <div className="gcc-table-wrap">
            <table className="gcc-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Phone</th>
                  <th>Status</th>
                  <th>Tier</th>
                  <th>Source</th>
                  <th>Org</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {leads.leads.map(l => (
                  <tr key={l.id}>
                    <td><span className="gcc-lead-name">{l.name || '—'}</span></td>
                    <td className="gcc-dim">{l.email || '—'}</td>
                    <td className="gcc-dim">{l.phone || '—'}</td>
                    <td>
                      {l.status && (
                        <span className="gcc-status-badge" data-status={l.status}>
                          {l.status}
                        </span>
                      )}
                    </td>
                    <td className="gcc-dim">{l.tier || '—'}</td>
                    <td className="gcc-dim">{l.source || '—'}</td>
                    <td className="gcc-dim gcc-mono">{l.organization_id?.slice(0,8)}…</td>
                    <td className="gcc-dim">{l.created_at ? l.created_at.slice(0,10) : '—'}</td>
                  </tr>
                ))}
                {leads.leads.length === 0 && !loading && (
                  <tr><td colSpan={8} className="gcc-empty">No leads found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── USERS ── */}
      {tab === 'users' && (
        <div className="gcc-content">
          <div className="gcc-filter-bar">
            <div className="gcc-search-wrap">
              <Icon name="search" size={14} />
              <input className="gcc-search" placeholder="Search name or email…" value={search}
                onChange={e => setSearch(e.target.value)} />
            </div>
            <select className="gcc-select" value={roleFilter} onChange={e => setRoleFilter(e.target.value)}>
              <option value="">Admins Only</option>
              {['god_admin','super_admin','org_admin','advisor','viewer'].map(r =>
                <option key={r} value={r}>{r.replace('_',' ')}</option>
              )}
            </select>
            <div className="gcc-total">{users.total} users</div>
          </div>

          <div className="gcc-table-wrap">
            <table className="gcc-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.users.map(u => (
                  <tr key={u.id}>
                    <td><span className="gcc-email">{u.email}</span></td>
                    <td className="gcc-dim">{u.name || '—'}</td>
                    <td><RoleBadge role={u.role} /></td>
                    <td>
                      <span className={`gcc-status-dot ${u.is_active !== false ? 'gcc-status-dot--on' : 'gcc-status-dot--off'}`}>
                        {u.is_active !== false ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="gcc-dim">{u.created_at ? u.created_at.slice(0,10) : '—'}</td>
                    <td>
                      <div className="gcc-action-row">
                        <button className="gcc-action-btn" title="Change role"
                          onClick={() => { setEditingUser(u); setNewRole(u.role) }}>
                          <Icon name="shield" size={13} />
                        </button>
                        <button
                          className={`gcc-action-btn ${u.is_active !== false ? 'gcc-action-btn--warn' : 'gcc-action-btn--ok'}`}
                          title={u.is_active !== false ? 'Deactivate' : 'Activate'}
                          onClick={() => toggleActive(u)}
                        >
                          {u.is_active !== false ? <Icon name="x" size={13} /> : <Icon name="check" size={13} />}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {users.users.length === 0 && !loading && (
                  <tr><td colSpan={6} className="gcc-empty">No users found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
