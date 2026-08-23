/**
 * GodOrganizations — God Mode tenant directory.
 * Premium Bloomberg Terminal aesthetic. Real backend data only.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'

const C = {
  bg: '#070c18', panel: '#0a1222', panel2: '#0c1628',
  border: '#1a2840', border2: '#162030',
  blue: '#2fb6ff', teal: '#1ef0a8', amber: '#f5b942', red: '#ff5f69',
  muted: '#3a5270', text: '#c8d6e5', textDim: '#5c7a96',
}

function Ico({ d, size = 14, children }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      {d ? <path d={d} /> : children}
    </svg>
  )
}

const ICO = {
  search: 'M21 21l-6-6m2-5a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
  enter: 'M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3',
  dots: 'M12 5h.01M12 12h.01M12 19h.01',
  check: 'M20 6L9 17l-5-5',
  x: 'M18 6L6 18M6 6l12 12',
  alert: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  refresh: 'M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15',
  arrowLeft: 'M19 12H5M12 19l-7-7 7-7',
  suspend: 'M10 9v6m4-6v6M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z',
  reactivate: 'M5 3l14 9-14 9V3z',
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
}

function healthColor(score) {
  if (score >= 80) return C.teal
  if (score >= 60) return C.amber
  return C.red
}
function healthLabel(score) {
  if (score >= 80) return 'HEALTHY'
  if (score >= 60) return 'ATTENTION'
  return 'CRITICAL'
}

function HealthBar({ score }) {
  const color = healthColor(score)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <div style={{ width: 60, height: 4, background: C.border, borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${score}%`, height: '100%', background: color, borderRadius: 2,
          boxShadow: `0 0 4px ${color}` }} />
      </div>
      <span style={{ color, fontSize: '11px', fontWeight: 700, fontVariantNumeric: 'tabular-nums',
        minWidth: 26, letterSpacing: '0.02em' }}>{score}</span>
    </div>
  )
}

function StatusBadge({ active }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <div style={{ width: 6, height: 6, borderRadius: '50%',
        background: active ? C.teal : C.muted,
        boxShadow: active ? `0 0 5px ${C.teal}` : 'none' }} />
      <span style={{ color: active ? C.teal : C.textDim, fontSize: '11px', fontWeight: 600,
        letterSpacing: '0.06em' }}>{active ? 'ACTIVE' : 'DORMANT'}</span>
    </div>
  )
}

function KpiCard({ label, value, color, sub }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 4,
      padding: '14px 18px', flex: 1, minWidth: 130 }}>
      <div style={{ color: C.textDim, fontSize: '10px', letterSpacing: '0.1em', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: '28px', fontWeight: 700, color: color || C.text,
        fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{value ?? '—'}</div>
      {sub && <div style={{ color: C.muted, fontSize: '10px', marginTop: 5 }}>{sub}</div>}
    </div>
  )
}

function TechRow({ label, status }) {
  const color = status === 'ok' ? C.teal : status === 'warn' ? C.amber : C.red
  const dot = status === 'ok' ? '●' : status === 'warn' ? '◐' : '○'
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '5px 0', borderBottom: `1px solid ${C.border2}` }}>
      <span style={{ color: C.textDim, fontSize: '11px' }}>{label}</span>
      <span style={{ color, fontSize: '11px', fontWeight: 600 }}>{dot} {status === 'ok' ? 'OK' : status === 'warn' ? 'WARN' : 'UNAVAIL'}</span>
    </div>
  )
}

function IntelligencePanel({ org, detail, loadingDetail }) {
  if (!org) return (
    <div style={{ padding: 24, color: C.muted, fontSize: '12px', textAlign: 'center', marginTop: 40 }}>
      Select an organization to view intelligence
    </div>
  )
  const panelLabel = { color: C.textDim, fontSize: '10px', fontWeight: 700,
    letterSpacing: '0.1em', marginBottom: 8, marginTop: 16 }
  const row = { display: 'flex', justifyContent: 'space-between', padding: '4px 0',
    borderBottom: `1px solid ${C.border2}` }
  const key = { color: C.textDim, fontSize: '11px' }
  const val = { color: C.text, fontSize: '11px', fontWeight: 500, textAlign: 'right', maxWidth: '55%' }

  return (
    <div style={{ padding: '16px 18px', overflowY: 'auto', height: '100%' }}>
      {/* Header */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ color: C.blue, fontSize: '14px', fontWeight: 700 }}>{org.name}</div>
        <div style={{ color: C.textDim, fontSize: '11px', marginTop: 2 }}>{org.slug}</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          <StatusBadge active={org.is_active} />
          <span style={{ color: healthColor(org.health_score), fontSize: '11px', fontWeight: 600,
            letterSpacing: '0.06em' }}>{healthLabel(org.health_score)} · {org.health_score}</span>
        </div>
      </div>

      <div style={panelLabel}>IDENTITY</div>
      <div style={row}><span style={key}>Org ID</span><span style={val}>{org.id}</span></div>
      <div style={row}><span style={key}>Plan</span><span style={val}>{(org.plan || 'standard').toUpperCase()}</span></div>
      <div style={row}><span style={key}>Platform</span><span style={val}>{org.platform_slug || '—'}</span></div>
      <div style={row}><span style={key}>Created</span><span style={val}>{org.created_at ? new Date(org.created_at).toLocaleDateString() : '—'}</span></div>

      <div style={{ color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 8, marginTop: 16 }}>LIVE SNAPSHOT</div>
      <div style={row}><span style={key}>Advisors</span><span style={val}>{org.advisor_count ?? '—'}</span></div>
      <div style={row}><span style={key}>Leads</span><span style={val}>{org.lead_count?.toLocaleString() ?? '—'}</span></div>
      <div style={row}><span style={key}>Messages / 30d</span><span style={val}>{org.messages_30d?.toLocaleString() ?? '—'}</span></div>
      <div style={row}><span style={key}>Last Activity</span><span style={val}>
        {org.last_activity ? new Date(org.last_activity).toLocaleDateString() : 'Never'}
      </span></div>

      {loadingDetail ? (
        <div style={{ color: C.muted, fontSize: '11px', marginTop: 16 }}>Loading detail…</div>
      ) : detail ? (
        <>
          {detail.advisors?.length > 0 && (
            <>
              <div style={{ color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 8, marginTop: 16 }}>ADVISORS</div>
              {detail.advisors.slice(0, 5).map(a => (
                <div key={a.id} style={{ display: 'flex', justifyContent: 'space-between',
                  padding: '4px 0', borderBottom: `1px solid ${C.border2}` }}>
                  <span style={{ color: C.text, fontSize: '11px' }}>{a.full_name || a.email}</span>
                  <span style={{ color: C.textDim, fontSize: '10px' }}>{a.role}</span>
                </div>
              ))}
              {detail.advisors.length > 5 && (
                <div style={{ color: C.muted, fontSize: '10px', marginTop: 4 }}>+{detail.advisors.length - 5} more</div>
              )}
            </>
          )}

          <div style={{ color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 8, marginTop: 16 }}>TECHNOLOGY STATUS</div>
          <TechRow label="SMS Messaging" status={detail.tech?.sms || 'ok'} />
          <TechRow label="Email Service" status={detail.tech?.email || 'ok'} />
          <TechRow label="AI Engine" status={detail.tech?.ai || 'ok'} />
          <TechRow label="Booking Engine" status={detail.tech?.booking || 'ok'} />
          <TechRow label="Webhooks" status={detail.tech?.webhooks || 'ok'} />
        </>
      ) : null}
    </div>
  )
}

function OwnerAttention({ orgs, onInvestigate }) {
  const issues = []
  orgs.forEach(o => {
    if (!o.is_active) return
    if (o.health_score < 60) issues.push({ org: o, type: 'critical', msg: 'Critical health score' })
    else if (o.messages_30d === 0) issues.push({ org: o, type: 'warn', msg: 'No messages in 30 days' })
    else if (o.advisor_count === 0) issues.push({ org: o, type: 'warn', msg: 'No advisors configured' })
  })
  return (
    <div style={{ borderTop: `1px solid ${C.border}`, padding: '14px 18px' }}>
      <div style={{ color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 10 }}>
        OWNER ATTENTION {issues.length > 0 && <span style={{ color: C.amber }}>({issues.length})</span>}
      </div>
      {issues.length === 0 ? (
        <div style={{ color: C.muted, fontSize: '11px' }}>No issues detected</div>
      ) : issues.slice(0, 5).map((iss, i) => (
        <div key={i} style={{ background: C.panel2, border: `1px solid ${iss.type === 'critical' ? C.red : C.amber}22`,
          borderRadius: 3, padding: '8px 10px', marginBottom: 6 }}>
          <div style={{ color: iss.type === 'critical' ? C.red : C.amber, fontSize: '10px', fontWeight: 700, marginBottom: 3 }}>
            {iss.msg}
          </div>
          <div style={{ color: C.textDim, fontSize: '11px', marginBottom: 6 }}>{iss.org.name}</div>
          <button onClick={() => onInvestigate(iss.org)}
            style={{ background: 'none', border: `1px solid ${C.muted}`, borderRadius: 2,
              color: C.textDim, cursor: 'pointer', fontSize: '10px', fontWeight: 700,
              letterSpacing: '0.08em', padding: '2px 8px' }}>
            INVESTIGATE
          </button>
        </div>
      ))}
    </div>
  )
}

export default function GodOrganizations({ onEnterOrg }) {
  const [orgs, setOrgs] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [healthFilter, setHealthFilter] = useState('all')
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [enterModal, setEnterModal] = useState(null)
  const [actionMenu, setActionMenu] = useState(null) // { orgId, x, y }
  const [confirmModal, setConfirmModal] = useState(null) // { type:'suspend'|'reactivate', org }
  const [actionLoading, setActionLoading] = useState(false)

  const loadOrgs = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (search) params.set('search', search)
      if (statusFilter !== 'all') params.set('status', statusFilter)
      if (healthFilter !== 'all') params.set('health', healthFilter)
      const [orgsRes, statsRes] = await Promise.all([
        api.get(`/god/orgs?${params}`),
        api.get('/god/stats'),
      ])
      setOrgs(orgsRes.data)
      setStats(statsRes.data)
    } catch (e) {
      console.error('Failed to load orgs', e)
    } finally { setLoading(false) }
  }, [search, statusFilter, healthFilter])

  useEffect(() => { loadOrgs() }, [loadOrgs])

  async function loadDetail(org) {
    setSelected(org)
    setDetail(null)
    setLoadingDetail(true)
    try {
      const r = await api.get(`/god/orgs/${org.id}/detail`)
      setDetail(r.data)
    } catch (e) {} finally { setLoadingDetail(false) }
  }

  async function handleEnterOrg() {
    if (!enterModal) return
    setActionLoading(true)
    try {
      const r = await api.post(`/god/orgs/${enterModal.id}/impersonate`)
      if (onEnterOrg) onEnterOrg({ org_id: enterModal.id, org_name: enterModal.name, session_id: r.data.session_id })
      setEnterModal(null)
    } catch (e) { alert('Failed to enter organization') }
    finally { setActionLoading(false) }
  }

  async function handleSuspendReactivate() {
    if (!confirmModal) return
    setActionLoading(true)
    try {
      const endpoint = confirmModal.type === 'suspend'
        ? `/god/orgs/${confirmModal.org.id}/suspend`
        : `/god/orgs/${confirmModal.org.id}/reactivate`
      await api.post(endpoint)
      setConfirmModal(null)
      await loadOrgs()
    } catch (e) { alert('Action failed') }
    finally { setActionLoading(false) }
  }

  const thStyle = { color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em',
    padding: '8px 12px', textAlign: 'left', borderBottom: `1px solid ${C.border}`,
    background: C.panel, whiteSpace: 'nowrap' }
  const tdStyle = { padding: '9px 12px', borderBottom: `1px solid ${C.border2}`,
    fontSize: '12px', color: C.text, verticalAlign: 'middle' }

  const filterBtn = (active) => ({
    background: active ? 'rgba(47,182,255,0.12)' : 'transparent',
    border: `1px solid ${active ? C.blue : C.border}`,
    borderRadius: 3, color: active ? C.blue : C.textDim,
    cursor: 'pointer', fontSize: '11px', fontWeight: active ? 700 : 400,
    padding: '4px 10px', letterSpacing: '0.04em',
  })

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', fontFamily: "'Inter',system-ui,sans-serif" }}>
      {/* ── Left: main content ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: 20 }}>
        {/* Page title */}
        <div style={{ marginBottom: 16 }}>
          <h1 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: C.text, letterSpacing: '0.06em' }}>
            ORGANIZATIONS
          </h1>
          <div style={{ color: C.muted, fontSize: '11px', marginTop: 2 }}>Tenant directory — platform-wide visibility</div>
        </div>

        {/* KPI cards */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          <KpiCard label="TOTAL ORGS"     value={stats?.total_orgs}    color={C.text} />
          <KpiCard label="ACTIVE"         value={stats?.active_orgs}   color={C.teal} />
          <KpiCard label="DORMANT"        value={stats?.total_orgs != null && stats?.active_orgs != null
            ? stats.total_orgs - stats.active_orgs : null} color={C.muted} />
          <KpiCard label="TOTAL ADVISORS" value={stats?.total_users}   color={C.blue} />
          <KpiCard label="TOTAL LEADS"    value={stats?.total_leads?.toLocaleString()} color={C.blue} />
        </div>

        {/* Search + filters */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ position: 'relative', flex: '1 1 220px', maxWidth: 320 }}>
            <div style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)',
              color: C.muted, pointerEvents: 'none' }}>
              <Ico d={ICO.search} size={13} />
            </div>
            <input value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Search name, slug, ID…"
              style={{ width: '100%', background: C.panel, border: `1px solid ${C.border}`,
                borderRadius: 3, color: C.text, fontSize: '12px', outline: 'none',
                padding: '6px 10px 6px 28px', boxSizing: 'border-box' }} />
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            {['all','active','dormant'].map(s => (
              <button key={s} onClick={() => setStatusFilter(s)} style={filterBtn(statusFilter === s)}>
                {s.toUpperCase()}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            {['all','healthy','attention','critical'].map(h => (
              <button key={h} onClick={() => setHealthFilter(h)} style={filterBtn(healthFilter === h)}>
                {h.toUpperCase()}
              </button>
            ))}
          </div>
          <button onClick={loadOrgs}
            style={{ background: 'none', border: `1px solid ${C.border}`, borderRadius: 3,
              color: C.muted, cursor: 'pointer', padding: '5px 8px' }}>
            <Ico d={ICO.refresh} size={13} />
          </button>
        </div>

        {/* Table */}
        <div style={{ flex: 1, overflow: 'auto', border: `1px solid ${C.border}`, borderRadius: 4 }}>
          {loading ? (
            <div style={{ padding: 40, textAlign: 'center', color: C.muted, fontSize: '12px' }}>Loading organizations…</div>
          ) : orgs.length === 0 ? (
            <div style={{ padding: 40, textAlign: 'center', color: C.muted, fontSize: '12px' }}>No organizations found</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  <th style={thStyle}>ORGANIZATION</th>
                  <th style={thStyle}>PLAN</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>ADVISORS</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>LEADS</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>MSGS/30D</th>
                  <th style={thStyle}>HEALTH</th>
                  <th style={thStyle}>STATUS</th>
                  <th style={thStyle}>LAST ACTIVITY</th>
                  <th style={{ ...thStyle, textAlign: 'center' }}>ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {orgs.map(org => {
                  const isSelected = selected?.id === org.id
                  const rowBg = isSelected ? 'rgba(47,182,255,0.05)' : 'transparent'
                  const rowBorder = isSelected ? `1px solid ${C.blue}33` : undefined
                  return (
                    <tr key={org.id}
                      onClick={() => loadDetail(org)}
                      style={{ background: rowBg, cursor: 'pointer', outline: rowBorder }}
                      onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'rgba(255,255,255,0.02)' }}
                      onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}>

                      {/* Organization identity cell */}
                      <td style={tdStyle}>
                        <div style={{ fontWeight: 600, color: isSelected ? C.blue : C.text }}>{org.name}</div>
                        <div style={{ color: C.muted, fontSize: '10px', marginTop: 2 }}>
                          {org.slug} · <span style={{ color: C.border }}>{org.id?.slice(0,8)}</span>
                        </div>
                      </td>
                      <td style={tdStyle}>
                        <span style={{ background: C.panel2, border: `1px solid ${C.border}`,
                          borderRadius: 2, color: C.textDim, fontSize: '10px', fontWeight: 700,
                          letterSpacing: '0.06em', padding: '2px 6px' }}>
                          {(org.plan || 'standard').toUpperCase()}
                        </span>
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {org.advisor_count ?? '—'}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {org.lead_count?.toLocaleString() ?? '—'}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                        color: org.messages_30d === 0 ? C.red : C.text }}>
                        {org.messages_30d?.toLocaleString() ?? '—'}
                      </td>
                      <td style={tdStyle}><HealthBar score={org.health_score ?? 0} /></td>
                      <td style={tdStyle}><StatusBadge active={org.is_active} /></td>
                      <td style={{ ...tdStyle, color: C.textDim, fontSize: '11px', whiteSpace: 'nowrap' }}>
                        {org.last_activity ? new Date(org.last_activity).toLocaleDateString() : 'Never'}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'center' }} onClick={e => e.stopPropagation()}>
                        <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                          <button onClick={() => setEnterModal(org)}
                            title="Enter Organization"
                            style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'rgba(47,182,255,0.1)',
                              border: `1px solid ${C.blue}55`, borderRadius: 2, color: C.blue,
                              cursor: 'pointer', fontSize: '10px', fontWeight: 700, letterSpacing: '0.06em',
                              padding: '3px 8px' }}>
                            <Ico d={ICO.enter} size={11} />ENTER
                          </button>

                          <button
                            onClick={e => setActionMenu(actionMenu?.orgId === org.id ? null : { orgId: org.id, org })}
                            style={{ background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 2,
                              color: C.textDim, cursor: 'pointer', padding: '3px 6px' }}>
                            <Ico d={ICO.dots} size={12} />
                          </button>
                          {actionMenu?.orgId === org.id && (
                            <div style={{ position: 'absolute', right: 0, zIndex: 99, background: C.panel,
                              border: `1px solid ${C.border}`, borderRadius: 4, minWidth: 160,
                              boxShadow: '0 8px 24px rgba(0,0,0,0.5)' }}>
                              {[
                                { label: 'View Intelligence', action: () => { loadDetail(org); setActionMenu(null) } },
                                { label: org.is_active ? 'Suspend Org' : 'Reactivate Org',
                                  action: () => { setConfirmModal({ type: org.is_active ? 'suspend' : 'reactivate', org }); setActionMenu(null) },
                                  color: org.is_active ? C.red : C.teal },
                              ].map((item, i) => (
                                <button key={i} onClick={item.action}
                                  style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none',
                                    border: 'none', borderBottom: `1px solid ${C.border2}`, color: item.color || C.text,
                                    cursor: 'pointer', fontSize: '12px', padding: '9px 14px' }}
                                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                                  onMouseLeave={e => e.currentTarget.style.background = 'none'}>
                                  {item.label}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* ── Right: intelligence panel ── */}
      <div style={{ width: 300, flexShrink: 0, borderLeft: `1px solid ${C.border}`,
        background: C.panel, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '12px 18px', borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
          <div style={{ color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em' }}>
            INTELLIGENCE PANEL
          </div>
        </div>
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <div style={{ flex: 1, overflowY: 'auto' }}>
            <IntelligencePanel org={selected} detail={detail} loadingDetail={loadingDetail} />
          </div>
          {orgs.length > 0 && (
            <OwnerAttention orgs={orgs} onInvestigate={org => { loadDetail(org); window.scrollTo(0,0) }} />
          )}
        </div>
      </div>

      {/* ── Enter Org Modal ── */}
      {enterModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 200,
          display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setEnterModal(null)}>
          <div style={{ background: C.panel, border: `1px solid ${C.amber}44`, borderRadius: 6,
            padding: 28, maxWidth: 420, width: '90%' }}
            onClick={e => e.stopPropagation()}>
            <div style={{ color: C.amber, fontSize: '11px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 12 }}>
              ⚠ ENTER ORGANIZATION — AUDIT ACTION
            </div>
            <div style={{ color: C.text, fontSize: '14px', fontWeight: 700, marginBottom: 8 }}>
              {enterModal.name}
            </div>
            <div style={{ color: C.textDim, fontSize: '12px', marginBottom: 20, lineHeight: 1.6 }}>
              You are about to enter this organization as platform owner. This action will be recorded
              in the audit log with your user ID, the organization ID, a session ID, and a timestamp.
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => setEnterModal(null)}
                style={{ flex: 1, background: 'none', border: `1px solid ${C.border}`, borderRadius: 3,
                  color: C.textDim, cursor: 'pointer', fontSize: '13px', padding: '9px 0' }}>
                CANCEL
              </button>
              <button onClick={handleEnterOrg} disabled={actionLoading}
                style={{ flex: 1, background: 'rgba(47,182,255,0.15)', border: `1px solid ${C.blue}`,
                  borderRadius: 3, color: C.blue, cursor: actionLoading ? 'wait' : 'pointer',
                  fontSize: '13px', fontWeight: 700, padding: '9px 0' }}>
                {actionLoading ? 'ENTERING…' : 'ENTER ORGANIZATION'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Suspend / Reactivate Modal ── */}
      {confirmModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 200,
          display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setConfirmModal(null)}>
          <div style={{ background: C.panel, border: `1px solid ${confirmModal.type === 'suspend' ? C.red : C.teal}44`,
            borderRadius: 6, padding: 28, maxWidth: 400, width: '90%' }}
            onClick={e => e.stopPropagation()}>
            <div style={{ color: confirmModal.type === 'suspend' ? C.red : C.teal,
              fontSize: '11px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 12 }}>
              {confirmModal.type === 'suspend' ? '⚠ SUSPEND ORGANIZATION' : '✓ REACTIVATE ORGANIZATION'}
            </div>
            <div style={{ color: C.text, fontSize: '14px', fontWeight: 700, marginBottom: 8 }}>
              {confirmModal.org.name}
            </div>
            <div style={{ color: C.textDim, fontSize: '12px', marginBottom: 20, lineHeight: 1.6 }}>
              {confirmModal.type === 'suspend'
                ? 'This will deactivate the organization. Users will be unable to log in. This action is reversible.'
                : 'This will reactivate the organization and restore user access.'}
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => setConfirmModal(null)}
                style={{ flex: 1, background: 'none', border: `1px solid ${C.border}`, borderRadius: 3,
                  color: C.textDim, cursor: 'pointer', fontSize: '13px', padding: '9px 0' }}>
                CANCEL
              </button>
              <button onClick={handleSuspendReactivate} disabled={actionLoading}
                style={{ flex: 1, background: confirmModal.type === 'suspend' ? 'rgba(255,95,105,0.12)' : 'rgba(30,240,168,0.1)',
                  border: `1px solid ${confirmModal.type === 'suspend' ? C.red : C.teal}`,
                  borderRadius: 3, color: confirmModal.type === 'suspend' ? C.red : C.teal,
                  cursor: actionLoading ? 'wait' : 'pointer', fontSize: '13px', fontWeight: 700, padding: '9px 0' }}>
                {actionLoading ? 'PROCESSING…' : confirmModal.type === 'suspend' ? 'SUSPEND' : 'REACTIVATE'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
