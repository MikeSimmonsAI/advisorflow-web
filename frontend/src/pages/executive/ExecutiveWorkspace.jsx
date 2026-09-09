/**
 * ExecutiveWorkspace — deal-room list for the Executive Suite.
 *
 * One item per customer engagement. Executives see only the organizations
 * they have been assigned; the server enforces that boundary on every request.
 *
 * All colours come from --ex-* tokens (ExecStyles) so light/dark appearance
 * is respected automatically. No hardcoded hex in inline styles.
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'

const STATUS_ORDER = ['Draft', 'Partner Review', 'Approved', 'Final']
const STATUS_COLOR = {
  Draft: '#6b7280',
  'Partner Review': '#d97706',
  Approved: '#2563eb',
  Final: '#16a34a',
}

function StatusPill({ status }) {
  const color = STATUS_COLOR[status] || '#6b7280'
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 10px',
      borderRadius: 12,
      fontSize: 12,
      fontWeight: 600,
      color: '#fff',
      background: color,
      letterSpacing: '0.03em',
    }}>
      {status}
    </span>
  )
}

function EmptyState({ filtered, onNew }) {
  if (filtered) return (
    <div style={{ textAlign: 'center', padding: '56px 0', color: 'var(--ex-ink2)' }}>
      <div style={{ fontSize: 32, marginBottom: 10, opacity: 0.5 }}>⊘</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--ex-ink)' }}>No items match this filter</div>
      <div style={{ fontSize: 13, marginTop: 6 }}>Try a different organization or status.</div>
    </div>
  )
  return (
    <div style={{
      textAlign: 'center', padding: '64px 32px',
      border: '1px dashed var(--ex-line)', borderRadius: 12,
      background: 'var(--ex-surface2)',
    }}>
      <div style={{ fontSize: 40, marginBottom: 14 }}>📋</div>
      <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ex-ink)', marginBottom: 8 }}>
        Your deal room is empty
      </div>
      <div style={{ fontSize: 14, color: 'var(--ex-ink2)', maxWidth: 360, margin: '0 auto 20px' }}>
        Create a workspace item for each customer engagement — proposals, approvals, and handoff documents all live here.
      </div>
      <button
        onClick={onNew}
        style={{
          background: 'var(--ex-accent)', color: 'var(--ex-accent-ink)', border: 'none',
          borderRadius: 8, padding: '10px 22px', fontSize: 14, fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        + Create first item
      </button>
    </div>
  )
}

// Force browser native form controls to match the current colour scheme
const EXEC_FORM_STYLES = `
[data-surface="executive"] input,
[data-surface="executive"] select,
[data-surface="executive"] textarea {
  color-scheme: light;
}
[data-appearance="dark"] [data-surface="executive"] input,
[data-appearance="dark"] [data-surface="executive"] select,
[data-appearance="dark"] [data-surface="executive"] textarea {
  color-scheme: dark;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-appearance="light"]) [data-surface="executive"] input,
  :root:not([data-appearance="light"]) [data-surface="executive"] select,
  :root:not([data-appearance="light"]) [data-surface="executive"] textarea {
    color-scheme: dark;
  }
}
`

const inputStyle = {
  width: '100%', padding: '7px 10px', borderRadius: 6,
  border: '1px solid var(--ex-line)', fontSize: 14,
  background: 'var(--ex-field)', color: 'var(--ex-ink)',
  boxSizing: 'border-box',
}

export default function ExecutiveWorkspace() {
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const filterOrg = searchParams.get('org') || ''
  const filterStatus = searchParams.get('status') || ''

  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const [newForm, setNewForm] = useState({ title: '', status: 'Draft', organization_id: '', working_notes: '' })
  const [orgs, setOrgs] = useState([])

  const load = useCallback(() => {
    setLoading(true)
    const qs = filterOrg ? `?organization_id=${encodeURIComponent(filterOrg)}` : ''
    api.get('/executive/workspace/' + qs)
      .then(d => { setItems(d.items || []); setError(null) })
      .catch(e => setError(e?.detail || 'Failed to load workspace items'))
      .finally(() => setLoading(false))
  }, [filterOrg])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    api.get('/executive/portfolio/health')
      .then(d => setOrgs(d.organizations || []))
      .catch(() => setOrgs([]))
  }, [])

  const visible = items.filter(i => !filterStatus || i.status === filterStatus)

  const handleCreate = async (e) => {
    e.preventDefault()
    if (!newForm.title.trim() || !newForm.organization_id) return
    try {
      const item = await api.post('/executive/workspace/', newForm)
      setCreating(false)
      setNewForm({ title: '', status: 'Draft', organization_id: '', working_notes: '' })
      nav(`/executive/workspace/${item.id}`)
    } catch (err) {
      alert(err?.detail || 'Could not create item')
    }
  }

  return (
    <div data-surface="executive" style={{ padding: '32px 40px', maxWidth: 960 }}>
      <style>{EXEC_FORM_STYLES}</style>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--ex-ink)' }}>Workspace</h1>
          <p style={{ margin: '4px 0 0', fontSize: 14, color: 'var(--ex-ink2)' }}>
            Deal-room content for your portfolio organizations.
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          style={{
            background: 'var(--ex-accent)', color: 'var(--ex-accent-ink)', border: 'none',
            borderRadius: 8, padding: '8px 18px', fontSize: 14, fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          + New item
        </button>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <select
          value={filterOrg}
          onChange={e => setSearchParams({ org: e.target.value, status: filterStatus })}
          style={{ ...inputStyle, width: 'auto' }}
        >
          <option value=''>All organizations</option>
          {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
        <select
          value={filterStatus}
          onChange={e => setSearchParams({ org: filterOrg, status: e.target.value })}
          style={{ ...inputStyle, width: 'auto' }}
        >
          <option value=''>All statuses</option>
          {STATUS_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        {(filterOrg || filterStatus) && (
          <button
            onClick={() => setSearchParams({})}
            style={{
              padding: '6px 12px', borderRadius: 6,
              border: '1px solid var(--ex-line)', background: 'none',
              fontSize: 13, cursor: 'pointer', color: 'var(--ex-ink2)',
            }}
          >
            Clear
          </button>
        )}
      </div>

      {/* Create form */}
      {creating && (
        <form onSubmit={handleCreate} style={{
          background: 'var(--ex-surface2)',
          border: '1px solid var(--ex-line)',
          borderRadius: 10, padding: '20px 24px', marginBottom: 24,
        }}>
          <div style={{ fontWeight: 600, marginBottom: 14, color: 'var(--ex-ink)' }}>New workspace item</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, color: 'var(--ex-ink2)', display: 'block', marginBottom: 4 }}>Title *</label>
              <input
                value={newForm.title}
                onChange={e => setNewForm(f => ({ ...f, title: e.target.value }))}
                required placeholder='e.g. Daniel Multi-Tenant Deal'
                style={inputStyle}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, color: 'var(--ex-ink2)', display: 'block', marginBottom: 4 }}>Organization *</label>
              <select
                value={newForm.organization_id}
                onChange={e => setNewForm(f => ({ ...f, organization_id: e.target.value }))}
                required style={inputStyle}
              >
                <option value=''>Select…</option>
                {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 12, color: 'var(--ex-ink2)', display: 'block', marginBottom: 4 }}>Status</label>
              <select
                value={newForm.status}
                onChange={e => setNewForm(f => ({ ...f, status: e.target.value }))}
                style={inputStyle}
              >
                {STATUS_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 12, color: 'var(--ex-ink2)', display: 'block', marginBottom: 4 }}>Notes</label>
              <input
                value={newForm.working_notes}
                onChange={e => setNewForm(f => ({ ...f, working_notes: e.target.value }))}
                placeholder='Optional' style={inputStyle}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
            <button type='submit' style={{
              background: 'var(--ex-accent)', color: 'var(--ex-accent-ink)', border: 'none',
              borderRadius: 6, padding: '8px 18px', fontSize: 14, fontWeight: 600, cursor: 'pointer',
            }}>Create</button>
            <button type='button' onClick={() => setCreating(false)} style={{
              background: 'none', border: '1px solid var(--ex-line)', color: 'var(--ex-ink2)',
              borderRadius: 6, padding: '8px 18px', fontSize: 14, cursor: 'pointer',
            }}>Cancel</button>
          </div>
        </form>
      )}

      {/* Content */}
      {loading && <div style={{ color: 'var(--ex-ink2)', padding: '40px 0' }}>Loading…</div>}
      {error && <div style={{ color: 'var(--ex-danger, #dc2626)', padding: '20px 0' }}>{error}</div>}

      {!loading && !error && visible.length === 0 && (
        <EmptyState filtered={!!(filterOrg || filterStatus)} onNew={() => setCreating(true)} />
      )}

      {!loading && !error && visible.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid var(--ex-line)' }}>
              {['Title', 'Status', 'Organization', 'Updated'].map(h => (
                <th key={h} style={{
                  textAlign: 'left', padding: '8px 12px', fontSize: 12,
                  fontWeight: 600, color: 'var(--ex-ink2)',
                  letterSpacing: '0.04em', textTransform: 'uppercase',
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map(item => {
              const org = orgs.find(o => o.id === item.organization_id)
              return (
                <tr
                  key={item.id}
                  onClick={() => nav(`/executive/workspace/${item.id}`)}
                  style={{ borderBottom: '1px solid var(--ex-line2)', cursor: 'pointer' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--ex-surface2)'}
                  onMouseLeave={e => e.currentTarget.style.background = ''}
                >
                  <td style={{ padding: '12px 12px', fontWeight: 500, color: 'var(--ex-ink)' }}>{item.title}</td>
                  <td style={{ padding: '12px 12px' }}><StatusPill status={item.status} /></td>
                  <td style={{ padding: '12px 12px', color: 'var(--ex-ink)', fontSize: 14 }}>
                    {org ? org.name : item.organization_id}
                  </td>
                  <td style={{ padding: '12px 12px', color: 'var(--ex-ink2)', fontSize: 13 }}>
                    {item.updated_at ? new Date(item.updated_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
