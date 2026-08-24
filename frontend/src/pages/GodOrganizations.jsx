import { useEffect, useState } from 'react'
import { api } from '../api/client'

export default function GodOrganizations({ onEnterOrg }) {
  const [orgs, setOrgs] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/god/orgs?limit=200')
      .then(data => setOrgs(Array.isArray(data) ? data : (data?.orgs || [])))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function handleEnter(org) {
    try {
      const session = await api.post(`/god/orgs/${org.id}/enter-session`)
      if (onEnterOrg) onEnterOrg({ org_id: org.id, name: org.name, ...session })
    } catch (e) {
      alert(e?.message || 'Could not enter org')
    }
  }

  return (
    <div style={{ padding: 40, maxWidth: 900, margin: '0 auto' }}>
      <h2 style={{ color: '#f59e0b', marginBottom: 24 }}>⚡ All Organizations</h2>
      {loading && <p style={{ color: '#6b7280' }}>Loading…</p>}
      <div style={{ display: 'grid', gap: 12 }}>
        {orgs.map(org => (
          <div key={org.id} style={{
            background: 'var(--surface-2, #1a1a2e)', border: '1px solid rgba(245,158,11,0.2)',
            borderRadius: 8, padding: '14px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <div>
              <div style={{ fontWeight: 600, color: '#f3f4f6' }}>{org.name}</div>
              <div style={{ fontSize: 12, color: '#6b7280' }}>{org.id}</div>
            </div>
            <button onClick={() => handleEnter(org)} style={{
              background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.35)',
              color: '#fbbf24', borderRadius: 6, padding: '6px 16px', cursor: 'pointer', fontWeight: 600, fontSize: 13,
            }}>Enter</button>
          </div>
        ))}
      </div>
    </div>
  )
}
