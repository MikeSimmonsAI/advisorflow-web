/**
 * GodLeadBrowser — Cross-org lead browser (GOD-09).
 * Consumes GET /god/leads — search, platform/org/status filters, pagination.
 */
import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api/client'

const STATUSES = ['active', 'completed', 'dnc', 'inactive', 'new', 'prospect']
const PAGE_SIZE = 50

function Card({ children, style }) {
  return (
    <div style={{
      background: 'var(--god-card, var(--gm-panel))',
      border: '1px solid var(--god-border, var(--gm-card-line))',
      borderRadius: 10, padding: '16px 20px', marginBottom: 16,
      ...style,
    }}>{children}</div>
  )
}

function Badge({ label, color = 'var(--gm-dim)' }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 100,
      background: color + '18', color, border: `1px solid ${color}33`,
      textTransform: 'uppercase', letterSpacing: '.04em',
    }}>{label}</span>
  )
}

const STATUS_COLORS = {
  active: 'var(--gm-teal)', new: 'var(--gm-blue)', prospect: 'var(--gm-purple)',
  completed: 'var(--gm-dim)', dnc: 'var(--gm-red)', inactive: 'var(--gm-text)',
}

export default function GodLeadBrowser() {
  const [leads, setLeads] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [skip, setSkip] = useState(0)

  const [search, setSearch] = useState('')
  const [platformSlug, setPlatformSlug] = useState('')
  const [orgId, setOrgId] = useState('')
  const [status, setStatus] = useState('')

  const [platforms, setPlatforms] = useState([])
  const [orgs, setOrgs] = useState([])

  useEffect(() => {
    api.get('/god/platforms').then(r => setPlatforms(r?.platforms || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (!platformSlug) { setOrgs([]); setOrgId(''); return }
    api.get('/god/organizations', { params: { platform_slug: platformSlug, limit: 200 } })
      .then(r => setOrgs(r?.organizations || []))
      .catch(() => setOrgs([]))
    setOrgId('')
  }, [platformSlug])

  const fetch = useCallback(async (s = skip) => {
    setLoading(true); setError(null)
    try {
      const params = { skip: s, limit: PAGE_SIZE }
      if (search)       params.search        = search
      if (platformSlug) params.platform_slug = platformSlug
      if (orgId)        params.org_id        = orgId
      if (status)       params.status        = status
      const r = await api.get('/god/leads', { params })
      setLeads(r.leads || [])
      setTotal(r.total || 0)
      setSkip(s)
    } catch (e) {
      setError(e.detail || e.message || 'Failed to load leads')
    } finally {
      setLoading(false)
    }
  }, [search, platformSlug, orgId, status, skip])

  useEffect(() => { fetch(0) }, [search, platformSlug, orgId, status])

  const page = Math.floor(skip / PAGE_SIZE)
  const totalPages = Math.ceil(total / PAGE_SIZE)

  const style = {
    padding: '24px 32px', maxWidth: 1100,
    fontFamily: 'var(--god-font, system-ui, sans-serif)',
    color: 'var(--god-text, var(--gm-blue))',
  }

  return (
    <div style={style}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Lead Browser</h1>
          <p style={{ margin: '5px 0 0', color: 'var(--gm-dim)', fontSize: 13 }}>
            All leads across all organizations — god only
          </p>
        </div>
        <div style={{ fontSize: 12, color: 'var(--gm-text)', paddingTop: 6 }}>
          {total.toLocaleString()} total
        </div>
      </div>

      {/* Filters */}
      <Card>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: '2 1 200px' }}>
            <div style={{ fontSize: 11, color: 'var(--gm-dim)', marginBottom: 4 }}>Search</div>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Name, email, or phone…"
              style={{ width: '100%', fontSize: 13, padding: '6px 10px',
                       border: '1px solid var(--gm-card-line)', borderRadius: 6, boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ flex: '1 1 140px' }}>
            <div style={{ fontSize: 11, color: 'var(--gm-dim)', marginBottom: 4 }}>Platform</div>
            <select value={platformSlug} onChange={e => setPlatformSlug(e.target.value)}
                    style={{ width: '100%', fontSize: 13, padding: '6px 8px',
                             border: '1px solid var(--gm-card-line)', borderRadius: 6 }}>
              <option value="">All platforms</option>
              {platforms.map(p => <option key={p.id} value={p.slug}>{p.name}</option>)}
            </select>
          </div>
          <div style={{ flex: '1 1 140px' }}>
            <div style={{ fontSize: 11, color: 'var(--gm-dim)', marginBottom: 4 }}>Organization</div>
            <select value={orgId} onChange={e => setOrgId(e.target.value)}
                    disabled={!platformSlug}
                    style={{ width: '100%', fontSize: 13, padding: '6px 8px',
                             border: '1px solid var(--gm-card-line)', borderRadius: 6,
                             opacity: platformSlug ? 1 : 0.5 }}>
              <option value="">All orgs</option>
              {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </div>
          <div style={{ flex: '1 1 120px' }}>
            <div style={{ fontSize: 11, color: 'var(--gm-dim)', marginBottom: 4 }}>Status</div>
            <select value={status} onChange={e => setStatus(e.target.value)}
                    style={{ width: '100%', fontSize: 13, padding: '6px 8px',
                             border: '1px solid var(--gm-card-line)', borderRadius: 6 }}>
              <option value="">All statuses</option>
              {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <button onClick={() => fetch(0)} disabled={loading}
                  style={{ fontSize: 12, padding: '6px 14px', borderRadius: 6,
                           border: '1px solid var(--gm-card-line)', background: 'var(--gm-panel)',
                           cursor: 'pointer', whiteSpace: 'nowrap' }}>
            ↻ Refresh
          </button>
        </div>
      </Card>

      {error && (
        <div style={{ background: 'var(--gm-pill-red-bg)', border: '1px solid var(--gm-pill-red-bd)',
                      borderRadius: 8, padding: 14, color: 'var(--gm-red)', marginBottom: 16 }}>
          {error}
        </div>
      )}

      {loading && <div style={{ color: 'var(--gm-text)', padding: '16px 0' }}>Loading…</div>}

      {!loading && leads.length === 0 && !error && (
        <div style={{ color: 'var(--gm-text)', padding: '24px 0', textAlign: 'center' }}>
          No leads match the current filters.
        </div>
      )}

      {leads.length > 0 && (
        <Card style={{ padding: 0 }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--gm-card-line)' }}>
                  {['Name', 'Contact', 'Status', 'Tier', 'Source', 'Organization', 'Created'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '10px 14px',
                                         fontSize: 11, fontWeight: 700, color: 'var(--gm-dim)',
                                         textTransform: 'uppercase', letterSpacing: '.06em',
                                         whiteSpace: 'nowrap' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {leads.map((lead, i) => (
                  <tr key={lead.id}
                      style={{ borderBottom: '1px solid var(--gm-card-line)',
                               background: i % 2 === 0 ? 'transparent' : 'var(--gm-panel)' }}>
                    <td style={{ padding: '9px 14px', fontWeight: 500,
                                  color: 'var(--gm-blue)', whiteSpace: 'nowrap' }}>
                      {lead.name || <span style={{ color: 'var(--gm-text)' }}>—</span>}
                    </td>
                    <td style={{ padding: '9px 14px' }}>
                      <div style={{ color: 'var(--gm-blue)', fontSize: 12 }}>{lead.email || '—'}</div>
                      <div style={{ color: 'var(--gm-text)', fontSize: 11 }}>{lead.phone || ''}</div>
                    </td>
                    <td style={{ padding: '9px 14px' }}>
                      {lead.status
                        ? <Badge label={lead.status} color={STATUS_COLORS[lead.status] || 'var(--gm-dim)'} />
                        : <span style={{ color: 'var(--gm-text)' }}>—</span>}
                    </td>
                    <td style={{ padding: '9px 14px', color: 'var(--gm-dim)' }}>
                      {lead.tier || '—'}
                    </td>
                    <td style={{ padding: '9px 14px', color: 'var(--gm-dim)', maxWidth: 160,
                                  overflow: 'hidden', textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap' }}>
                      {lead.source || '—'}
                    </td>
                    <td style={{ padding: '9px 14px', fontSize: 11,
                                  color: 'var(--gm-dim)', fontFamily: 'monospace' }}>
                      {lead.organization_id?.slice(0, 8) || '—'}
                    </td>
                    <td style={{ padding: '9px 14px', fontSize: 11, color: 'var(--gm-text)',
                                  whiteSpace: 'nowrap' }}>
                      {lead.created_at
                        ? new Date(lead.created_at).toLocaleDateString()
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div style={{ display: 'flex', justifyContent: 'space-between',
                          alignItems: 'center', padding: '12px 16px',
                          borderTop: '1px solid var(--gm-card-line)' }}>
              <span style={{ fontSize: 12, color: 'var(--gm-text)' }}>
                Page {page + 1} of {totalPages} ({total.toLocaleString()} total)
              </span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button onClick={() => fetch(skip - PAGE_SIZE)}
                        disabled={skip === 0 || loading}
                        style={{ fontSize: 12, padding: '5px 12px', borderRadius: 5,
                                 border: '1px solid var(--gm-card-line)', background: 'var(--gm-panel)',
                                 cursor: skip === 0 ? 'default' : 'pointer',
                                 opacity: skip === 0 ? 0.4 : 1 }}>
                  ← Prev
                </button>
                <button onClick={() => fetch(skip + PAGE_SIZE)}
                        disabled={skip + PAGE_SIZE >= total || loading}
                        style={{ fontSize: 12, padding: '5px 12px', borderRadius: 5,
                                 border: '1px solid var(--gm-card-line)', background: 'var(--gm-panel)',
                                 cursor: skip + PAGE_SIZE >= total ? 'default' : 'pointer',
                                 opacity: skip + PAGE_SIZE >= total ? 0.4 : 1 }}>
                  Next →
                </button>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
