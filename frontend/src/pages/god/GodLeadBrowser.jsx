/**
 * GodLeadBrowser — cross-org lead browser (GOD-09), now with the platform's
 * own MASTER LEAD DATABASE as its default view.
 *
 * TWO SOURCES, ONE PAGE
 *
 *   Master database   GET /god/master/contacts — one row per APPEARANCE of a
 *                     person in an organization, carrying the platform, the
 *                     origin, when they were first and last seen, and how many
 *                     organizations that human has turned up in.
 *   Tenant leads      GET /god/leads — the original view, unchanged: the
 *                     customers' own operational records.
 *
 * The master view is the default because it is the question this page is
 * usually being asked: who is on this platform, and where did they come from.
 * The tenant view stays one click away because it is the only one that shows a
 * customer's own workflow state.
 *
 * STAGE 1 ONLY. The full visual redesign, advanced filters and prospecting
 * audience tools are deliberately not here.
 */
import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api/client'
import {
  PAGE_SIZE, MASTER_COLUMNS, buildMasterParams, buildTenantParams, formatSeen,
} from './masterLeadQuery'

const STATUSES = ['active', 'completed', 'dnc', 'inactive', 'new', 'prospect']

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

const TH = {
  textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 700,
  color: 'var(--gm-dim)', textTransform: 'uppercase', letterSpacing: '.06em',
  whiteSpace: 'nowrap',
}
const TD = { padding: '9px 14px', verticalAlign: 'top' }

export default function GodLeadBrowser() {
  const [mode, setMode] = useState('master')     // 'master' | 'tenant'
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [skip, setSkip] = useState(0)

  const [search, setSearch] = useState('')
  const [platformSlug, setPlatformSlug] = useState('')
  const [orgId, setOrgId] = useState('')
  const [status, setStatus] = useState('')
  // PRODUCTION ONLY IS THE DEFAULT. Demo orgs, acceptance tests and proof
  // scenarios all write real lead rows; a master database where a quarter of
  // the people are props is one nobody trusts.
  const [includeSynthetic, setIncludeSynthetic] = useState(false)

  const [platforms, setPlatforms] = useState([])
  const [orgs, setOrgs] = useState([])

  useEffect(() => {
    api.get('/god/platforms').then(r => setPlatforms(r?.platforms || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (!platformSlug) { setOrgs([]); setOrgId(''); return }
    // `/god/orgs`, returning `{orgs}`. This asked `/god/organizations` for
    // `r.organizations`, and no such route or key has ever existed — the call
    // 404'd, the `.catch` swallowed it, and the Organization filter was
    // permanently empty while looking merely unused. Demo organizations are
    // excluded by that endpoint unless asked for, which matches this page's
    // own Production-only default.
    api.get('/god/orgs', {
      params: { platform_slug: platformSlug, limit: 200,
                include_demo: includeSynthetic },
    })
      .then(r => setOrgs(r?.orgs || []))
      .catch(() => setOrgs([]))
    setOrgId('')
  }, [platformSlug, includeSynthetic])

  const platformId = platforms.find(p => p.slug === platformSlug)?.id || ''

  const fetch = useCallback(async (s = skip) => {
    setLoading(true); setError(null)
    try {
      if (mode === 'master') {
        const params = buildMasterParams({
          search, platformId, orgId, includeSynthetic, skip: s, limit: PAGE_SIZE,
        })
        const r = await api.get('/god/master/contacts', { params })
        setRows(r.rows || [])
        setTotal(r.total || 0)
      } else {
        const params = buildTenantParams({
          search, platformSlug, orgId, status, skip: s, limit: PAGE_SIZE,
        })
        const r = await api.get('/god/leads', { params })
        setRows(r.leads || [])
        setTotal(r.total || 0)
      }
      setSkip(s)
    } catch (e) {
      setError(e.detail || e.message || 'Failed to load leads')
    } finally {
      setLoading(false)
    }
  }, [mode, search, platformSlug, platformId, orgId, status, includeSynthetic, skip])

  useEffect(() => { fetch(0) },
    [mode, search, platformSlug, orgId, status, includeSynthetic])

  const page = Math.floor(skip / PAGE_SIZE)
  const totalPages = Math.ceil(total / PAGE_SIZE)
  const isMaster = mode === 'master'

  // THE CLIPPING FIX. This page was capped at maxWidth: 1100 inside a
  // full-width shell, so the table was squeezed into a column narrower than
  // its own content and the right-hand columns were cut off rather than
  // scrolled to. The page now uses the width it is given; the table itself
  // still scrolls horizontally inside its own container on a small screen.
  const style = {
    padding: '24px 32px', width: '100%', maxWidth: '100%', minWidth: 0,
    boxSizing: 'border-box', overflowX: 'hidden',
    fontFamily: 'var(--god-font, system-ui, sans-serif)',
    color: 'var(--god-text, var(--gm-blue))',
  }

  const tabStyle = (active) => ({
    fontSize: 12, fontWeight: 600, padding: '6px 14px', borderRadius: 6,
    border: '1px solid var(--gm-card-line)', cursor: 'pointer',
    background: active ? 'var(--gm-blue)' : 'var(--gm-panel)',
    color: active ? '#fff' : 'var(--gm-text)',
  })

  return (
    <div style={style}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 20, gap: 16,
                    flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Lead Browser</h1>
          <p style={{ margin: '5px 0 0', color: 'var(--gm-dim)', fontSize: 13 }}>
            {isMaster
              ? 'The AdvisorFlow master lead database — every person, and every organization they arrived through. God only.'
              : 'Customers’ own operational lead records across all organizations — god only'}
          </p>
        </div>
        <div style={{ fontSize: 12, color: 'var(--gm-text)', paddingTop: 6 }}>
          {total.toLocaleString()} {isMaster ? 'appearances' : 'leads'}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <button style={tabStyle(isMaster)} onClick={() => setMode('master')}>
          Master database
        </button>
        <button style={tabStyle(!isMaster)} onClick={() => setMode('tenant')}>
          Tenant leads
        </button>
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

          {isMaster ? (
            <div style={{ flex: '1 1 190px' }}>
              <div style={{ fontSize: 11, color: 'var(--gm-dim)', marginBottom: 4 }}>Records</div>
              <select value={includeSynthetic ? 'all' : 'production'}
                      onChange={e => setIncludeSynthetic(e.target.value === 'all')}
                      style={{ width: '100%', fontSize: 13, padding: '6px 8px',
                               border: '1px solid var(--gm-card-line)', borderRadius: 6 }}>
                <option value="production">Production only</option>
                <option value="all">Include QA / Test</option>
              </select>
            </div>
          ) : (
            <div style={{ flex: '1 1 120px' }}>
              <div style={{ fontSize: 11, color: 'var(--gm-dim)', marginBottom: 4 }}>Status</div>
              <select value={status} onChange={e => setStatus(e.target.value)}
                      style={{ width: '100%', fontSize: 13, padding: '6px 8px',
                               border: '1px solid var(--gm-card-line)', borderRadius: 6 }}>
                <option value="">All statuses</option>
                {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          )}

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

      {!loading && rows.length === 0 && !error && (
        <div style={{ color: 'var(--gm-text)', padding: '24px 0', textAlign: 'center' }}>
          {isMaster
            ? 'No master records match the current filters.'
            : 'No leads match the current filters.'}
        </div>
      )}

      {rows.length > 0 && (
        <Card style={{ padding: 0 }}>
          <div style={{ overflowX: 'auto', width: '100%' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--gm-card-line)' }}>
                  {(isMaster
                    ? MASTER_COLUMNS
                    : ['Name', 'Contact', 'Status', 'Tier', 'Source', 'Organization', 'Created']
                  ).map(h => <th key={h} style={TH}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {isMaster
                  ? rows.map((r, i) => (
                    <tr key={r.occurrence_id}
                        style={{ borderBottom: '1px solid var(--gm-card-line)',
                                 background: i % 2 === 0 ? 'transparent' : 'var(--gm-panel)' }}>
                      <td style={{ ...TD, whiteSpace: 'nowrap', color: 'var(--gm-dim)' }}>
                        {r.platform_name || '—'}
                      </td>
                      <td style={{ ...TD, maxWidth: 200 }}>
                        <div style={{ color: 'var(--gm-blue)' }}>
                          {r.organization_name || r.organization_id?.slice(0, 8) || '—'}
                        </div>
                        {r.is_synthetic && (
                          <div style={{ marginTop: 3 }}>
                            <Badge label="QA / test" color="var(--gm-purple)" />
                          </div>
                        )}
                      </td>
                      <td style={{ ...TD, fontWeight: 500, color: 'var(--gm-blue)',
                                    whiteSpace: 'nowrap' }}>
                        {r.name || <span style={{ color: 'var(--gm-text)' }}>—</span>}
                        {r.needs_review && (
                          <div style={{ marginTop: 3 }} title={r.review_reason || ''}>
                            <Badge label="needs review" color="var(--gm-red)" />
                          </div>
                        )}
                      </td>
                      <td style={{ ...TD, fontSize: 12, color: 'var(--gm-blue)',
                                    maxWidth: 230, overflow: 'hidden',
                                    textOverflow: 'ellipsis' }}>
                        {r.email || '—'}
                      </td>
                      <td style={{ ...TD, fontSize: 12, color: 'var(--gm-text)',
                                    whiteSpace: 'nowrap' }}>
                        {r.phone || '—'}
                      </td>
                      <td style={{ ...TD, color: 'var(--gm-dim)', maxWidth: 180,
                                    overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {r.source || '—'}
                        {r.source_detail && (
                          <div style={{ fontSize: 11, color: 'var(--gm-text)',
                                        overflow: 'hidden', textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap' }}>
                            {r.source_detail}
                          </div>
                        )}
                      </td>
                      <td style={{ ...TD, fontSize: 11, color: 'var(--gm-text)',
                                    whiteSpace: 'nowrap' }}>
                        {formatSeen(r.first_seen_at)}
                      </td>
                      <td style={{ ...TD, fontSize: 11, color: 'var(--gm-text)',
                                    whiteSpace: 'nowrap' }}>
                        {formatSeen(r.last_seen_at)}
                      </td>
                      <td style={{ ...TD, fontWeight: 700,
                                    color: r.occurrence_count > 1
                                      ? 'var(--gm-teal)' : 'var(--gm-dim)' }}>
                        {r.occurrence_count ?? 1}
                      </td>
                    </tr>
                  ))
                  : rows.map((lead, i) => (
                    <tr key={lead.id}
                        style={{ borderBottom: '1px solid var(--gm-card-line)',
                                 background: i % 2 === 0 ? 'transparent' : 'var(--gm-panel)' }}>
                      <td style={{ ...TD, fontWeight: 500, color: 'var(--gm-blue)',
                                    whiteSpace: 'nowrap' }}>
                        {lead.name || <span style={{ color: 'var(--gm-text)' }}>—</span>}
                      </td>
                      <td style={TD}>
                        <div style={{ color: 'var(--gm-blue)', fontSize: 12 }}>{lead.email || '—'}</div>
                        <div style={{ color: 'var(--gm-text)', fontSize: 11 }}>{lead.phone || ''}</div>
                      </td>
                      <td style={TD}>
                        {lead.status
                          ? <Badge label={lead.status} color={STATUS_COLORS[lead.status] || 'var(--gm-dim)'} />
                          : <span style={{ color: 'var(--gm-text)' }}>—</span>}
                      </td>
                      <td style={{ ...TD, color: 'var(--gm-dim)' }}>{lead.tier || '—'}</td>
                      <td style={{ ...TD, color: 'var(--gm-dim)', maxWidth: 160,
                                    overflow: 'hidden', textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap' }}>
                        {lead.source || '—'}
                      </td>
                      <td style={{ ...TD, fontSize: 11, color: 'var(--gm-dim)',
                                    fontFamily: 'monospace' }}>
                        {lead.organization_id?.slice(0, 8) || '—'}
                      </td>
                      <td style={{ ...TD, fontSize: 11, color: 'var(--gm-text)',
                                    whiteSpace: 'nowrap' }}>
                        {lead.created_at ? new Date(lead.created_at).toLocaleDateString() : '—'}
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
