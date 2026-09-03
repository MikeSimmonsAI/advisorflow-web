/**
 * ExecutiveOrganizations — portfolio of customer organizations under this brand.
 *
 * Data source: GET /executive/organizations (server-side scoped to executive's platform).
 * Read-only. No enter/workspace links: executives VIEW the portfolio, not operate in it.
 */

import { useState, useEffect } from 'react'
import { api } from '../../api/client'

function useOrgs() {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    api.get('/executive/organizations')
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(() => setState({ loading: false, data: null, error: 'Failed to load organizations.' }))
  }, [])
  return state
}

export default function ExecutiveOrganizations() {
  const { loading, data, error } = useOrgs()

  if (loading) return <PageWrap><p style={styles.muted}>Loading…</p></PageWrap>
  if (error || !data) return <PageWrap><p style={styles.error}>{error}</p></PageWrap>

  const orgs = data.organizations || []

  return (
    <PageWrap>
      <h1 style={styles.heading}>Organizations</h1>
      <p style={styles.sub}>
        {data.platform_name} — {data.total} customer organization{data.total !== 1 ? 's' : ''}
      </p>
      {orgs.length === 0 ? (
        <p style={styles.empty}>No customer organizations provisioned yet.</p>
      ) : (
        <div style={styles.tableWrap}>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={styles.th}>Organization</th>
                <th style={styles.th}>Provisioned</th>
              </tr>
            </thead>
            <tbody>
              {orgs.map(org => (
                <tr key={org.id} style={styles.row}>
                  <td style={styles.td}>{org.name}</td>
                  <td style={styles.td}>
                    {org.created_at ? new Date(org.created_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PageWrap>
  )
}

function PageWrap({ children }) { return <div style={styles.page}>{children}</div> }

const styles = {
  page: { padding: '40px', maxWidth: 900 },
  heading: { fontSize: 26, fontWeight: 800, color: '#1a1f36', margin: '0 0 4px' },
  sub: { fontSize: 14, color: '#6b7280', margin: '0 0 32px' },
  empty: { color: '#9ca3af', fontSize: 14 },
  tableWrap: { overflowX: 'auto', background: '#fff', border: '1px solid #e8ecf4', borderRadius: 12 },
  table: { width: '100%', borderCollapse: 'collapse' },
  th: {
    textAlign: 'left', padding: '12px 16px', fontSize: 11, fontWeight: 700,
    color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.06em',
    borderBottom: '1px solid #e8ecf4', background: '#f9fafb',
  },
  row: { borderBottom: '1px solid #f0f2f8' },
  td: { padding: '14px 16px', fontSize: 14, color: '#1a1f36', verticalAlign: 'middle' },
  mono: { fontFamily: 'monospace', fontSize: 12, color: '#6b7280' },
  muted: { color: '#9ca3af', fontSize: 14 },
  error: { color: '#ef4444', fontSize: 14 },
}
