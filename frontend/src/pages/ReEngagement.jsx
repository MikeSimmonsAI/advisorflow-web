import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser, getBranding } from '../api/client'
import '../styles/shared.css'
import './ReEngagement.css'

const TABS = [
  { key: 'hot',  label: 'Hot',  color: '#ff4d4d', icon: '&#128293;', desc: 'Replied with interest or urgent tier' },
  { key: 'warm', label: 'Warm', color: '#f0c040', icon: '&#127777;&#65039;', desc: 'Active in cadence, recently touched' },
  { key: 'cold', label: 'Cold', color: '#64748b', icon: '&#10052;&#65039;', desc: 'No engagement in a long stretch' },
]

export default function ReEngagement() {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const _branding = getBranding()
  const industry = _branding?.industry || 'funeral'

  const [activeTab, setActiveTab] = useState('hot') // 'hot' | 'warm' | 'cold' | 'all'
  const [leads, setLeads] = useState({ hot: [], warm: [], cold: [] })
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(null) // lead_id being actioned

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api.get('/leads/?temperature=hot&page_size=200').catch(() => null),
      api.get('/leads/?temperature=warm&page_size=200').catch(() => null),
      api.get('/leads/?temperature=cold&page_size=200').catch(() => null),
    ]).then(([hot, warm, cold]) => {
      // API returns paginated envelope {items:[...], total:N} — extract items
      setLeads({
        hot:  Array.isArray(hot)  ? hot  : (hot?.items  || []),
        warm: Array.isArray(warm) ? warm : (warm?.items || []),
        cold: Array.isArray(cold) ? cold : (cold?.items || []),
      })
      setLoading(false)
    })
  }, [])

  const allLeads = [...leads.hot, ...leads.warm, ...leads.cold]
  const current = activeTab === 'all' ? allLeads : (leads[activeTab] || [])
  const tab = TABS.find(t => t.key === activeTab)

  function fmtDate(iso) {
    if (!iso) return '--'
    return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
  }

  return (
    <div className="re-page">

      {/* HEADER */}
      <div className="re-header">
        <div>
          <h1 className="re-title">Re-engagement</h1>
          <p className="re-subtitle">Work your leads by engagement temperature — focus on hot first, re-warm cold ones.</p>
        </div>
      </div>

      {/* STAT CHIPS */}
      <div className="re-stat-row">
        {TABS.map(t => (
          <div key={t.key} className={`re-stat-chip ${activeTab === t.key ? 're-stat-chip--active' : ''}`}
            style={{ '--chip-color': t.color }}
            onClick={() => setActiveTab(t.key)}>
            <span className="re-stat-icon" dangerouslySetInnerHTML={{ __html: t.icon }} />
            <div className="re-stat-body">
              <strong className="re-stat-count" style={{ color: t.color }}>
                {loading ? '--' : leads[t.key].length}
              </strong>
              <span className="re-stat-label">{t.label}</span>
            </div>
          </div>
        ))}
        <div
          className={`re-stat-chip ${activeTab === 'all' ? 're-stat-chip--active' : ''}`}
          style={{ '--chip-color': '#94a3b8' }}
          onClick={() => setActiveTab(activeTab === 'all' ? 'hot' : 'all')}
        >
          <span className="re-stat-icon">&#9889;</span>
          <div className="re-stat-body">
            <strong className="re-stat-count" style={{ color: '#94a3b8' }}>
              {loading ? '--' : (leads.hot.length + leads.warm.length + leads.cold.length)}
            </strong>
            <span className="re-stat-label">Total classified</span>
          </div>
        </div>
      </div>

      {/* TAB BAR */}
      <div className="re-tab-bar">
        {TABS.map(t => (
          <button key={t.key}
            className={`re-tab ${activeTab === t.key ? 're-tab--active' : ''}`}
            style={activeTab === t.key ? { borderBottomColor: t.color, color: t.color } : {}}
            onClick={() => setActiveTab(t.key)}>
            <span dangerouslySetInnerHTML={{ __html: t.icon }} /> {t.label}
            <span className="re-tab-count" style={{ background: activeTab === t.key ? t.color : 'rgba(255,255,255,0.08)' }}>
              {loading ? '…' : leads[t.key].length}
            </span>
          </button>
        ))}
      </div>

      {/* DESCRIPTION */}
      {tab && activeTab !== 'all' && (
        <div className="re-tab-desc" style={{ borderLeftColor: tab.color }}>
          <span dangerouslySetInnerHTML={{ __html: tab.icon }} /> {tab.desc}
        </div>
      )}
      {activeTab === 'all' && (
        <div className="re-tab-desc" style={{ borderLeftColor: '#94a3b8' }}>
          ⚡ All classified leads — {allLeads.length} total across Hot, Warm, and Cold.
        </div>
      )}

      {/* LEAD LIST */}
      {loading ? (
        <div className="empty-state" style={{ marginTop: 32 }}>Loading leads...</div>
      ) : current.length === 0 ? (
        <div className="re-empty">
          <span style={{ fontSize: 36, opacity: 0.3 }} dangerouslySetInnerHTML={{ __html: tab?.icon }} />
          <span>No {activeTab === 'all' ? 'classified' : activeTab} leads right now.</span>
          {activeTab === 'hot' && <span style={{ fontSize: 13, opacity: 0.55 }}>Hot leads are auto-classified from reply sentiment and tier urgency.</span>}
          {activeTab === 'warm' && <span style={{ fontSize: 13, opacity: 0.55 }}>Warm leads are in active cadence with recent touches.</span>}
          {activeTab === 'cold' && <span style={{ fontSize: 13, opacity: 0.55 }}>Cold leads haven't engaged recently. Import new leads or restart cadence to warm them up.</span>}
          {activeTab === 'all' && <span style={{ fontSize: 13, opacity: 0.55 }}>No leads have been classified yet.</span>}
        </div>
      ) : (
        <div className="re-list">
          {current.map(lead => {
            const name = [lead.first_name, lead.last_name].filter(Boolean).join(' ') || 'Unknown'
            return (
              <div key={lead.id} className="re-card">
                <div className="re-card-left">
                  <div className="re-card-name">{name}</div>
                  <div className="re-card-meta">
                    {lead.phone && <span>{lead.phone}</span>}
                    {activeTab === 'all' && lead.temperature && <span className="re-badge" style={{ background: lead.temperature === 'hot' ? '#ff4d4d33' : lead.temperature === 'warm' ? '#f0c04033' : '#64748b33', color: lead.temperature === 'hot' ? '#ff4d4d' : lead.temperature === 'warm' ? '#f0c040' : '#94a3b8', textTransform: 'capitalize' }}>{lead.temperature}</span>}
                    {lead.tier && <span className="re-badge re-badge--tier">{lead.tier.replace('_', ' ')}</span>}
                    {lead.status && <span className="re-badge re-badge--status">{lead.status}</span>}
                    <span className="re-card-date">Imported {fmtDate(lead.created_at)}</span>
                  </div>
                  {lead.city && lead.state && (
                    <div className="re-card-location">{lead.city}, {lead.state}</div>
                  )}
                </div>
                <div className="re-card-actions">
                  <button className="btn btn--primary" style={{ fontSize: 12, padding: '5px 14px' }}
                    onClick={() => navigate(`/leads/${lead.id}`)}>
                    Open lead &#8594;
                  </button>
                  {lead.phone && (
                    <button className="btn btn--secondary" style={{ fontSize: 12, padding: '5px 14px' }}
                      onClick={() => navigate(`/leads/${lead.id}?action=sms`)}>
                      &#128172; SMS
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
