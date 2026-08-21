import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const STATUS_COLORS = {
  draft: { bg: 'rgba(255,255,255,0.06)', text: '#aab' },
  published: { bg: 'rgba(25,214,124,0.15)', text: '#19d67c' },
  archived: { bg: 'rgba(255,255,255,0.06)', text: '#666' },
}

export default function Proposals() {
  const [proposals, setProposals] = useState([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [form, setForm] = useState({ title: '', client_name: '', client_email: '', client_company: '' })
  const navigate = useNavigate()

  useEffect(() => {
    api.get('/proposals/').then(r => {
      setProposals(r.data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  async function handleCreate(e) {
    e.preventDefault()
    if (!form.title.trim()) return
    setCreating(true)
    try {
      const r = await api.post('/proposals/', form)
      navigate(`/proposals/${r.data.id}`)
    } catch {
      setCreating(false)
    }
  }

  return (
    <div style={{ padding: '32px', maxWidth: 920, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700, color: 'var(--text-primary, #fff)' }}>
            Client Proposals
          </h1>
          <p style={{ margin: '4px 0 0', color: 'var(--text-muted, #888)', fontSize: 14 }}>
            Create and send secure, trackable proposals to your clients.
          </p>
        </div>
        <button
          onClick={() => setShowNew(true)}
          style={{
            background: 'var(--signal-blue, #087cff)',
            color: '#fff',
            border: 'none',
            borderRadius: 8,
            padding: '10px 20px',
            fontSize: 14,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          + New Proposal
        </button>
      </div>

      {/* New proposal modal */}
      {showNew && (
        <div
          onClick={() => setShowNew(false)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
          }}
        >
          <form
            onClick={e => e.stopPropagation()}
            onSubmit={handleCreate}
            style={{
              background: 'var(--bg-card, #081224)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 16,
              padding: 32,
              width: 480,
              maxWidth: '90vw',
            }}
          >
            <h2 style={{ margin: '0 0 24px', color: 'var(--text-primary, #fff)', fontSize: 20, fontWeight: 700 }}>
              New Proposal
            </h2>
            {[
              { key: 'title', label: 'Proposal Title', placeholder: 'Q3 Coverage Review', required: true },
              { key: 'client_name', label: 'Client Name', placeholder: 'Sarah Johnson' },
              { key: 'client_company', label: 'Company / Organization', placeholder: 'Acme Corp' },
              { key: 'client_email', label: 'Client Email', placeholder: 'sarah@acme.com', type: 'email' },
            ].map(f => (
              <div key={f.key} style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#aab', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  {f.label}{f.required ? ' *' : ''}
                </label>
                <input
                  type={f.type || 'text'}
                  required={f.required}
                  placeholder={f.placeholder}
                  value={form[f.key]}
                  onChange={e => setForm(p => ({ ...p, [f.key]: e.target.value }))}
                  style={{
                    width: '100%', boxSizing: 'border-box',
                    background: 'rgba(255,255,255,0.05)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: 8, padding: '10px 12px',
                    color: 'var(--text-primary, #fff)', fontSize: 14,
                    outline: 'none',
                  }}
                />
              </div>
            ))}
            <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
              <button
                type="button"
                onClick={() => setShowNew(false)}
                style={{
                  flex: 1, background: 'rgba(255,255,255,0.06)', color: '#aab',
                  border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8,
                  padding: '10px', fontSize: 14, cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={creating}
                style={{
                  flex: 1, background: 'var(--signal-blue, #087cff)', color: '#fff',
                  border: 'none', borderRadius: 8, padding: '10px',
                  fontSize: 14, fontWeight: 600, cursor: 'pointer',
                  opacity: creating ? 0.6 : 1,
                }}
              >
                {creating ? 'Creating…' : 'Create Proposal'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div style={{ color: '#666', textAlign: 'center', padding: 64 }}>Loading…</div>
      ) : proposals.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '80px 32px',
          background: 'rgba(255,255,255,0.02)',
          border: '1px dashed rgba(255,255,255,0.1)',
          borderRadius: 16,
        }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>📄</div>
          <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-primary, #fff)', marginBottom: 8 }}>
            No proposals yet
          </div>
          <div style={{ color: '#666', fontSize: 14, marginBottom: 24 }}>
            Create your first proposal and send it to a client with a secure magic link.
          </div>
          <button
            onClick={() => setShowNew(true)}
            style={{
              background: 'var(--signal-blue, #087cff)', color: '#fff',
              border: 'none', borderRadius: 8, padding: '10px 24px',
              fontSize: 14, fontWeight: 600, cursor: 'pointer',
            }}
          >
            Create Proposal
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {proposals.map(p => {
            const sc = STATUS_COLORS[p.status] || STATUS_COLORS.draft
            return (
              <div
                key={p.id}
                onClick={() => navigate(`/proposals/${p.id}`)}
                style={{
                  background: 'var(--bg-card, #081224)',
                  border: '1px solid rgba(255,255,255,0.07)',
                  borderRadius: 12,
                  padding: '20px 24px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 20,
                  transition: 'border-color 0.15s',
                }}
                onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(8,124,255,0.4)'}
                onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(255,255,255,0.07)'}
              >
                {/* Status dot */}
                <div style={{
                  width: 10, height: 10, borderRadius: '50%',
                  background: sc.text, flexShrink: 0,
                  boxShadow: p.status === 'published' ? `0 0 8px ${sc.text}` : 'none',
                }} />

                {/* Info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary, #fff)', marginBottom: 4 }}>
                    {p.title}
                  </div>
                  <div style={{ fontSize: 13, color: '#667', display: 'flex', gap: 16 }}>
                    {p.client_name && <span>{p.client_name}</span>}
                    {p.client_company && <span>{p.client_company}</span>}
                    <span>{p.block_count} block{p.block_count !== 1 ? 's' : ''}</span>
                  </div>
                </div>

                {/* Stats */}
                <div style={{ display: 'flex', gap: 24, textAlign: 'right', flexShrink: 0 }}>
                  <div>
                    <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary, #fff)' }}>{p.view_count}</div>
                    <div style={{ fontSize: 11, color: '#555', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Opens</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: sc.text, background: sc.bg, padding: '3px 10px', borderRadius: 20 }}>
                      {p.status}
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
