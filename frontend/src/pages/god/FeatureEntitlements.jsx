/**
 * FEATURE ENTITLEMENTS — God Mode control plane (approved mockup 2026-09-28).
 *
 * Pick a scope (Platform / Brand / Organization / Workspace / Role / User) and
 * see every registered feature's effective state there, where it was inherited
 * from, and the override (if any) at that scope. The table, KPIs and detail
 * panel are the shared EntitlementsWorkbench; this page only owns the scope
 * selectors. Deep-linkable: ?scope=org&org_id=... ; no params opens the Platform scope.
 */
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { EntitlementsWorkbench } from './EntitlementsPanel'
import './FeatureEntitlements.css'

const SCOPES = [
  { id: 'platform', label: 'Platform' },
  { id: 'brand', label: 'Brand' },
  { id: 'org', label: 'Organization' },
  { id: 'workspace', label: 'Workspace' },
  { id: 'role', label: 'Role' },
  { id: 'user', label: 'User' },
]
const NEEDS = {
  platform: [], brand: ['platform_id'], org: ['org_id'],
  workspace: ['org_id', 'workspace_id'], role: ['org_id', 'role'], user: ['org_id', 'user_id'],
}

export default function FeatureEntitlements() {
  const [params, setParams] = useSearchParams()
  // No params -> the PLATFORM matrix, so the page is useful on arrival. An
  // org id in the URL (?org= / ?org_id=) still opens that organization.
  const [scope, setScope] = useState(params.get('scope')
    || ((params.get('org_id') || params.get('org')) ? 'org' : 'platform'))
  const [platformId, setPlatformId] = useState(params.get('platform_id') || '')
  const [orgId, setOrgId] = useState(params.get('org_id') || params.get('org') || '')
  const [workspaceId, setWorkspaceId] = useState(params.get('workspace_id') || '')
  const [role, setRole] = useState(params.get('role') || '')
  const [userId, setUserId] = useState(params.get('user_id') || '')
  const [orgQuery, setOrgQuery] = useState('')
  const [opts, setOpts] = useState({ brands: [], organizations: [], workspaces: [], roles: [], users: [] })
  const [err, setErr] = useState(null)

  useEffect(() => {
    const p = new URLSearchParams()
    if (platformId) p.set('platform_id', platformId)
    if (orgId) p.set('org_id', orgId)
    if (orgQuery.trim()) p.set('q', orgQuery.trim())
    const t = setTimeout(() => {
      api.get('/god/entitlements/scopes?' + p.toString())
        .then(d => { setOpts(d); setErr(null) })
        .catch(e => setErr(e.message))
    }, orgQuery ? 250 : 0)
    return () => clearTimeout(t)
  }, [platformId, orgId, orgQuery])

  // Default the brand to the organization's brand once known.
  useEffect(() => {
    if (!orgId || platformId) return
    const o = opts.organizations.find(x => x.id === orgId)
    if (o?.platform_id) setPlatformId(o.platform_id)
  }, [orgId, opts.organizations]) // eslint-disable-line react-hooks/exhaustive-deps

  const target = useMemo(() => {
    const t = { scope, platform_id: platformId, org_id: orgId, workspace_id: workspaceId, role, user_id: userId }
    const missing = NEEDS[scope].some(k => !t[k])
    if (missing) return null
    const out = { scope }
    if (scope === 'brand') out.platform_id = platformId
    if (scope !== 'platform' && scope !== 'brand') out.org_id = orgId
    if (scope === 'workspace') out.workspace_id = workspaceId
    if (scope === 'role') out.role = role
    if (scope === 'user') out.user_id = userId
    return out
  }, [scope, platformId, orgId, workspaceId, role, userId])

  useEffect(() => {
    const p = {}
    if (scope) p.scope = scope
    if (platformId) p.platform_id = platformId
    if (orgId) p.org_id = orgId
    if (workspaceId) p.workspace_id = workspaceId
    if (role) p.role = role
    if (userId) p.user_id = userId
    setParams(p, { replace: true })
  }, [scope, platformId, orgId, workspaceId, role, userId]) // eslint-disable-line react-hooks/exhaustive-deps

  const needs = NEEDS[scope]
  const selectedOrg = opts.organizations.find(o => o.id === orgId)
  const missingHint = !target
    ? `Select ${needs.filter(k => !({ platform_id: platformId, org_id: orgId, workspace_id: workspaceId, role, user_id: userId })[k])
        .map(k => ({ platform_id: 'a brand', org_id: 'an organization', workspace_id: 'a workspace', role: 'a role', user_id: 'a user' })[k]).join(' and ')} to view this scope.`
    : null

  return (
    <div className="go-scope fe-root">
      <header className="fe-header">
        <div>
          <div className="fe-crumb">Platform / Feature Entitlements</div>
          <h1>Feature Entitlements</h1>
          <p>Control feature access across Platform, Brand, Organization, Workspace, Role and User.
            Changes inherit unless explicitly overridden.</p>
        </div>
      </header>

      <div className="fe-scopebar fe-card">
        <label className="fe-field">
          <span>View scope</span>
          <select className="fe-input" value={scope} onChange={e => setScope(e.target.value)}>
            {SCOPES.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <label className="fe-field">
          <span>Brand</span>
          <select className="fe-input" value={platformId}
            onChange={e => {
              setPlatformId(e.target.value); setOrgId(''); setWorkspaceId(''); setUserId('')
              if (e.target.value && scope === 'platform') setScope('brand')
            }}>
            <option value="">All brands</option>
            {opts.brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </label>
        <label className="fe-field fe-field-wide">
          <span>Organization</span>
          <div className="fe-orgpick">
            <input className="fe-input" placeholder="Filter organizations…" value={orgQuery}
              onChange={e => setOrgQuery(e.target.value)} aria-label="Filter organizations" />
            <select className="fe-input" value={orgId}
              onChange={e => {
                setOrgId(e.target.value); setWorkspaceId(''); setUserId('')
                if (e.target.value && (scope === 'platform' || scope === 'brand')) setScope('org')
              }}>
              <option value="">{selectedOrg ? selectedOrg.name : 'Select organization'}</option>
              {opts.organizations.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </div>
        </label>
        <label className="fe-field">
          <span>Workspace</span>
          <select className="fe-input" value={workspaceId} disabled={!orgId}
            onChange={e => setWorkspaceId(e.target.value)}>
            <option value="">{orgId ? (opts.workspaces.length ? 'All workspaces' : 'No workspaces') : 'Select organization first'}</option>
            {opts.workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        <label className="fe-field">
          <span>Role</span>
          <select className="fe-input" value={role} onChange={e => setRole(e.target.value)} disabled={!orgId}>
            <option value="">All roles</option>
            {opts.roles.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="fe-field">
          <span>User</span>
          <select className="fe-input" value={userId} onChange={e => setUserId(e.target.value)} disabled={!orgId}>
            <option value="">All users</option>
            {opts.users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </label>
      </div>

      {err && <div className="fe-banner fe-banner-red">{err}</div>}
      {missingHint
        ? <div className="fe-card fe-empty">{missingHint}</div>
        : <EntitlementsWorkbench target={target} />}
    </div>
  )
}
