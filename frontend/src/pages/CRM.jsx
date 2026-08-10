import { useState, useEffect, useMemo } from 'react'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './CRM.css'

const PIPELINE_STAGES = [
  { key: 'new',          label: 'New',          color: '#2fb6ff' },
  { key: 'contacted',    label: 'Contacted',     color: '#7c3aed' },
  { key: 'qualified',    label: 'Qualified',     color: '#f59e0b' },
  { key: 'proposal',     label: 'Proposal Sent', color: '#06b6d4' },
  { key: 'negotiation',  label: 'Negotiation',   color: '#8b5cf6' },
  { key: 'closed_won',   label: 'Closed Won',    color: '#10b981' },
  { key: 'closed_lost',  label: 'Closed Lost',   color: '#ef4444' },
]

function stageColor(key) {
  return (PIPELINE_STAGES.find(s => s.key === key) || {}).color || '#6b7280'
}

function stageLabel(key) {
  return (PIPELINE_STAGES.find(s => s.key === key) || {}).label || key
}

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
}

function initials(name) {
  if (!name) return '?'
  return name.split(' ').filter(Boolean).map(w => w[0]).join('').toUpperCase().slice(0, 2)
}

// ── Contact Card (list view) ───────────────────────────────────────────────
function ContactCard({ contact, onClick }) {
  const stage = PIPELINE_STAGES.find(s => s.key === contact.pipeline_stage) || PIPELINE_STAGES[0]
  return (
    <div className="crm-card" onClick={() => onClick(contact)}>
      <div className="crm-card-avatar" style={{ background: stage.color + '22', borderColor: stage.color + '55', color: stage.color }}>
        {initials(contact.full_name)}
      </div>
      <div className="crm-card-body">
        <div className="crm-card-name">{contact.full_name || <span style={{color:'var(--text-tertiary)'}}>No name</span>}</div>
        <div className="crm-card-meta">
          {contact.phone && <span>{contact.phone}</span>}
          {contact.email && <span>{contact.email}</span>}
          {contact.company && <span>🏢 {contact.company}</span>}
        </div>
        <div className="crm-card-footer">
          <span className="crm-stage-badge" style={{ background: stage.color + '22', color: stage.color, borderColor: stage.color + '44' }}>
            {stage.label}
          </span>
          {contact.last_contact_at && (
            <span className="crm-card-date">Last contact: {fmtDate(contact.last_contact_at)}</span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Detail Panel ───────────────────────────────────────────────────────────
function ContactPanel({ contact, onClose, onSave }) {
  const [form, setForm] = useState({ ...contact })
  const [note, setNote] = useState('')
  const [notes, setNotes] = useState(contact.notes || [])
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    try {
      const updated = await api.patch(`/crm/contacts/${contact.id}`, form)
      onSave(updated)
    } catch (e) {
      alert(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleAddNote = async () => {
    if (!note.trim()) return
    try {
      const res = await api.post(`/crm/contacts/${contact.id}/notes`, { content: note.trim() })
      setNotes(prev => [res, ...prev])
      setNote('')
    } catch (e) {
      alert(e.message)
    }
  }

  return (
    <div className="crm-panel-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="crm-panel">
        <div className="crm-panel-header">
          <h2>{form.full_name || 'Contact'}</h2>
          <button className="crm-panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="crm-panel-body">
          <section className="crm-section">
            <h3 className="crm-section-title">Contact Info</h3>
            <div className="crm-form-grid">
              <label>Full Name
                <input value={form.full_name || ''} onChange={e => setForm(f => ({...f, full_name: e.target.value}))} />
              </label>
              <label>Phone
                <input value={form.phone || ''} onChange={e => setForm(f => ({...f, phone: e.target.value}))} />
              </label>
              <label>Email
                <input type="email" value={form.email || ''} onChange={e => setForm(f => ({...f, email: e.target.value}))} />
              </label>
              <label>Company
                <input value={form.company || ''} onChange={e => setForm(f => ({...f, company: e.target.value}))} />
              </label>
            </div>
          </section>

          <section className="crm-section">
            <h3 className="crm-section-title">Pipeline Stage</h3>
            <div className="crm-stage-picker">
              {PIPELINE_STAGES.map(s => (
                <button
                  key={s.key}
                  className={`crm-stage-btn ${form.pipeline_stage === s.key ? 'crm-stage-btn--active' : ''}`}
                  style={{ '--stage-color': s.color }}
                  onClick={() => setForm(f => ({...f, pipeline_stage: s.key}))}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </section>

          <section className="crm-section">
            <h3 className="crm-section-title">Notes</h3>
            <div className="crm-note-input">
              <textarea
                placeholder="Add a note…"
                value={note}
                onChange={e => setNote(e.target.value)}
                rows={3}
              />
              <button className="btn btn--primary btn--sm" onClick={handleAddNote} disabled={!note.trim()}>
                Add Note
              </button>
            </div>
            <div className="crm-notes-list">
              {notes.length === 0 && <div className="crm-empty-notes">No notes yet.</div>}
              {notes.map((n, i) => (
                <div key={i} className="crm-note">
                  <div className="crm-note-content">{n.content}</div>
                  <div className="crm-note-date">{fmtDate(n.created_at)}</div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="crm-panel-footer">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Create Contact Modal ───────────────────────────────────────────────────
function CreateModal({ onClose, onCreate }) {
  const blank = { full_name: '', phone: '', email: '', company: '', pipeline_stage: 'new' }
  const [form, setForm] = useState(blank)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const handleSubmit = async () => {
    if (!form.full_name && !form.phone) { setErr('Name or phone required'); return }
    setSaving(true); setErr('')
    try {
      const res = await api.post('/crm/contacts', form)
      onCreate(res)
      onClose()
    } catch (e) {
      setErr(e.message || 'Failed to create contact')
      setSaving(false)
    }
  }

  return (
    <div className="crm-panel-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="crm-panel crm-panel--sm">
        <div className="crm-panel-header">
          <h2>New Contact</h2>
          <button className="crm-panel-close" onClick={onClose}>✕</button>
        </div>
        <div className="crm-panel-body">
          <div className="crm-form-grid">
            <label>Full Name
              <input value={form.full_name} onChange={e => setForm(f => ({...f, full_name: e.target.value}))} placeholder="Jane Smith" />
            </label>
            <label>Phone
              <input value={form.phone} onChange={e => setForm(f => ({...f, phone: e.target.value}))} placeholder="(555) 000-0000" />
            </label>
            <label>Email
              <input type="email" value={form.email} onChange={e => setForm(f => ({...f, email: e.target.value}))} placeholder="jane@example.com" />
            </label>
            <label>Company
              <input value={form.company} onChange={e => setForm(f => ({...f, company: e.target.value}))} placeholder="Acme Corp" />
            </label>
            <label>Stage
              <select value={form.pipeline_stage} onChange={e => setForm(f => ({...f, pipeline_stage: e.target.value}))}>
                {PIPELINE_STAGES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
            </label>
          </div>
          {err && <div className="crm-error">{err}</div>}
        </div>
        <div className="crm-panel-footer">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={handleSubmit} disabled={saving}>{saving ? 'Creating…' : 'Create Contact'}</button>
        </div>
      </div>
    </div>
  )
}

// ── Main CRM Page ──────────────────────────────────────────────────────────
export default function CRM() {
  const user = getCurrentUser()
  const isSuperAdmin = user?.role === 'super_admin'

  const [contacts, setContacts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [stageFilter, setStageFilter] = useState('all')
  const [view, setView] = useState('list') // 'list' | 'pipeline'
  const [selected, setSelected] = useState(null)
  const [showCreate, setShowCreate] = useState(false)

  const fetchContacts = async () => {
    setLoading(true); setError(null)
    try {
      const data = await api.get('/crm/contacts')
      setContacts(Array.isArray(data) ? data : (data.contacts || []))
    } catch (e) {
      setError(e.message || 'Failed to load contacts')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchContacts() }, [])

  const filtered = useMemo(() => {
    let list = contacts
    if (stageFilter !== 'all') list = list.filter(c => c.pipeline_stage === stageFilter)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(c =>
        (c.full_name || '').toLowerCase().includes(q) ||
        (c.phone || '').includes(q) ||
        (c.email || '').toLowerCase().includes(q) ||
        (c.company || '').toLowerCase().includes(q)
      )
    }
    return list
  }, [contacts, stageFilter, search])

  const stageCounts = useMemo(() => {
    const map = { all: contacts.length }
    PIPELINE_STAGES.forEach(s => { map[s.key] = contacts.filter(c => c.pipeline_stage === s.key).length })
    return map
  }, [contacts])

  const handleSave = (updated) => {
    setContacts(prev => prev.map(c => c.id === updated.id ? updated : c))
    setSelected(updated)
  }

  const handleCreate = (created) => {
    setContacts(prev => [created, ...prev])
  }

  // ── Pipeline Board View ─────────────────────────────────────────────────
  const renderPipeline = () => (
    <div className="crm-pipeline">
      {PIPELINE_STAGES.map(stage => {
        const cols = filtered.filter(c => c.pipeline_stage === stage.key)
        return (
          <div key={stage.key} className="crm-pipeline-col">
            <div className="crm-pipeline-col-header" style={{ borderTopColor: stage.color }}>
              <span className="crm-pipeline-col-name" style={{ color: stage.color }}>{stage.label}</span>
              <span className="crm-pipeline-col-count">{cols.length}</span>
            </div>
            <div className="crm-pipeline-cards">
              {cols.map(c => (
                <div key={c.id} className="crm-pipeline-card" onClick={() => setSelected(c)}>
                  <div className="crm-pipeline-card-name">{c.full_name || '(no name)'}</div>
                  {c.company && <div className="crm-pipeline-card-company">{c.company}</div>}
                  {c.phone && <div className="crm-pipeline-card-phone">{c.phone}</div>}
                </div>
              ))}
              {cols.length === 0 && <div className="crm-pipeline-empty">No contacts</div>}
            </div>
          </div>
        )
      })}
    </div>
  )

  return (
    <div className="page-wrap">
      {/* Header */}
      <div className="crm-header">
        <div>
          <h1 className="page-title">CRM</h1>
          <p className="page-subtitle">{contacts.length} contact{contacts.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="crm-header-actions">
          <div className="crm-view-toggle">
            <button className={`crm-view-btn ${view === 'list' ? 'crm-view-btn--active' : ''}`} onClick={() => setView('list')}>
              ☰ List
            </button>
            <button className={`crm-view-btn ${view === 'pipeline' ? 'crm-view-btn--active' : ''}`} onClick={() => setView('pipeline')}>
              ⬜ Pipeline
            </button>
          </div>
          <button className="btn btn--primary" onClick={() => setShowCreate(true)}>+ Add Contact</button>
        </div>
      </div>

      {/* Filters */}
      <div className="crm-controls">
        <input
          className="crm-search"
          placeholder="Search contacts by name, phone, email, or company…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <div className="filter-tabs">
          <button className={`filter-tab ${stageFilter === 'all' ? 'filter-tab--active' : ''}`} onClick={() => setStageFilter('all')}>
            All <span className="filter-tab-count">{stageCounts.all}</span>
          </button>
          {PIPELINE_STAGES.map(s => (
            <button key={s.key} className={`filter-tab ${stageFilter === s.key ? 'filter-tab--active' : ''}`} onClick={() => setStageFilter(s.key)}>
              {s.label} <span className="filter-tab-count">{stageCounts[s.key] || 0}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      {loading && <div className="users-state">Loading contacts…</div>}
      {error   && <div className="users-state users-state--error">{error}</div>}
      {!loading && !error && filtered.length === 0 && (
        <div className="users-state">
          {contacts.length === 0 ? 'No contacts yet. Add your first contact to get started.' : 'No contacts match this filter.'}
        </div>
      )}
      {!loading && !error && filtered.length > 0 && (
        view === 'pipeline' ? renderPipeline() : (
          <div className="crm-list">
            {filtered.map(c => (
              <ContactCard key={c.id} contact={c} onClick={setSelected} />
            ))}
          </div>
        )
      )}

      {/* Panels + Modals */}
      {selected && (
        <ContactPanel
          contact={selected}
          onClose={() => setSelected(null)}
          onSave={handleSave}
        />
      )}
      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreate={handleCreate}
        />
      )}
    </div>
  )
}
