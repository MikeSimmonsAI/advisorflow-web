/**
 * AdvisorFlow Command Center — God Mode landing page.
 * Wrapped by GodModeLayout (GodShell) in App.jsx.
 * The /god/organizations route has a full native implementation.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const C = {
  bg:       '#070c18',
  panel:    '#0a1222',
  border:   '#1a2840',
  blue:     '#2fb6ff',
  teal:     '#1ef0a8',
  amber:    '#f5b942',
  red:      '#ff5f69',
  muted:    '#3a5270',
  text:     '#c8d6e5',
  textDim:  '#5c7a96',
}

function Stat({ label, value, color, note }) {
  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${C.border}`,
      borderRadius: 4,
      padding: '16px 20px',
      flex: 1,
      minWidth: 140,
    }}>
      <div style={{ color: C.textDim, fontSize: '10px', letterSpacing: '0.1em', marginBottom: 8 }}>
        {label}
      </div>
      <div style={{
        fontSize: '30px', fontWeight: 700,
        color: color || C.text,
        fontVariantNumeric: 'tabular-nums',
        lineHeight: 1,
      }}>
        {value ?? '—'}
      </div>
      {note && <div style={{ color: C.muted, fontSize: '10px', marginTop: 6 }}>{note}</div>}
    </div>
  )
}

export default function GodCommandCenter() {
  const navigate = useNavigate()
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/god/stats')
      .then(r => setStats(r))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const quickLinks = [
    { label: 'Organizations', path: '/god/organizations', desc: 'Tenant directory, health scores, intelligence panel' },
    { label: 'Users', path: '/god/users-all', desc: 'All platform users — roles, activity, access control' },
    { label: 'Activity Feed', path: '/god/activity', desc: 'Real-time platform events across all tenants' },
    { label: 'System Health', path: '/god/system-health', desc: 'SMS, email, AI, integrations status' },
    { label: 'Audit Logs', path: '/god/audit', desc: 'God Mode session history and privileged actions' },
    { label: 'Feature Flags', path: '/god/features', desc: 'Per-org feature access control' },
  ]

  return (
    <div style={{ padding: 24, background: C.bg, minHeight: '100%', fontFamily: "'Inter', system-ui, sans-serif" }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: '18px', fontWeight: 700, color: C.text, letterSpacing: '0.06em' }}>
          COMMAND CENTER
        </h1>
        <div style={{ color: C.muted, fontSize: '11px', marginTop: 3 }}>
          Platform-wide visibility · AdvisorFlow God Mode
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: 24, flexWrap: 'wrap' }}>
        {loading ? (
          <div style={{ color: C.muted, fontSize: '12px' }}>Loading platform stats…</div>
        ) : (
          <>
            <Stat label="TOTAL PLATFORMS" value={stats?.total_platforms} color={C.blue} />
            <Stat label="TOTAL ORGS"      value={stats?.total_orgs}      color={C.text} />
            <Stat label="ACTIVE ORGS"     value={stats?.active_orgs}     color={C.teal} />
            <Stat label="TOTAL LEADS"     value={stats?.total_leads?.toLocaleString()} color={C.blue} />
            <Stat label="LEADS (30D)"     value={stats?.new_leads_30d?.toLocaleString()} color={C.teal} note="new this month" />
            <Stat label="TOTAL USERS"     value={stats?.total_users}     color={C.text} />
          </>
        )}
      </div>

      {stats?.platforms?.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div style={{ color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 10 }}>
            PLATFORMS
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {stats.platforms.map(p => (
              <div key={p.slug} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 4, padding: '10px 16px', minWidth: 140 }}>
                <div style={{ color: C.text, fontWeight: 600, marginBottom: 4 }}>{p.name}</div>
                <div style={{ color: C.muted, fontSize: '11px' }}>{p.org_count} org{p.org_count !== 1 ? 's' : ''}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <div style={{ color: C.textDim, fontSize: '10px', fontWeight: 700, letterSpacing: '0.1em', marginBottom: 10 }}>
          QUICK ACCESS
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 }}>
          {quickLinks.map(q => (
            <button key={q.path} onClick={() => navigate(q.path)}
              style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 4, padding: '14px 16px', textAlign: 'left', cursor: 'pointer', color: 'inherit', transition: 'border-color 0.15s, background 0.15s' }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = C.blue; e.currentTarget.style.background = 'rgba(47,182,255,0.04)' }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.background = C.panel }}
            >
              <div style={{ color: C.text, fontWeight: 600, marginBottom: 4, fontSize: '13px' }}>{q.label}</div>
              <div style={{ color: C.muted, fontSize: '11px', lineHeight: 1.5 }}>{q.desc}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
