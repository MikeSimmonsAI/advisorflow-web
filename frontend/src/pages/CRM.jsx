import { useState, useEffect, useRef, useMemo } from 'react'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './CRM.css'

// Stages are loaded from /crm/stages at runtime so they're org-appropriate.
// This fallback is used only before the API responds.
const FALLBACK_STAGES = [
  { key: 'inquiry',           label: 'Inquiry',             color: '#64748b' },
  { key: 'pre_need',          label: 'Pre-Need',            color: '#6366f1' },
  { key: 'at_need',           label: 'At-Need',             color: '#f59e0b' },
  { key: 'arrangements',      label: 'Arrangements',        color: '#ef4444' },
  { key: 'services_complete', label: 'Services Complete',   color: '#10b981' },
  { key: 'aftercare',         label: 'Aftercare Follow-up', color: '#3b82f6' },
  { key: 'closed',            label: 'Closed',              color: '#374151' },
]

function stageColor(key, stages) {
  return ((stages || FALLBACK_STAGES).find(s => s.key === key) || {}).color || '#6b7280'
}

function stageLabel(key, stages) {
  return ((stages || FALLBACK_STAGES).find(s => s.key === key) || {}).label || key
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
function ContactCard({ contact, stages, onClick }) {
  const stageList = stages || FALLBACK_STAGES
  const stage = stageList.find(s => s.key === contact.stage) || stageList[0]
  const addr = [contact.address_city, contact.address_state].filter(Boolean).join(', ')
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
          {addr && <span>📍 {addr}</span>}
        </div>
        <div className="crm-card-footer">
          <span className="crm-stage-badge" style={{ background: stage.color + '22', color: stage.color, borderColor: stage.color + '44' }}>
            {stage.label}
          </span>
          {contact.last_contacted_at && (
            <span className="crm-card-date">Last contact: {fmtDate(contact.last_contacted_at)}</span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Detail Panel ───────────────────────────────────────────────────────────
function ContactPanel({ contact, stages, onClose, onSave, onDelete }) {
  const stageList = stages || FALLBACK_STAGES
  const [form, setForm] = useState({ ...contact })
  const [note, setNote] = useState('')
  const [notes, setNotes] = useState([])
  const [notesLoading, setNotesLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState('')
  const textareaRef = useRef(null)

  // Load notes when panel opens
  useEffect(() => {
    let active = true
    setNotesLoading(true)
    api.get(`/crm-native/contacts/${contact.id}/notes`)
      .then(data => { if (active) setNotes(Array.isArray(data) ? data : []) })
      .catch(() => {})
      .finally(() => { if (active) setNotesLoading(false) })
    return () => { active = false }
  }, [contact.id])

  const handleSave = async () => {
    setSaving(true); setErr('')
    try {
      const updated = await api.patch(`/crm-native/contacts/${contact.id}`, form)
      onSave(updated)
    } catch (e) {
      setErr(e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleAddNote = async () => {
    if (!note.trim()) return
    try {
      const res = await api.post(`/crm-native/contacts/${contact.id}/notes`, { content: note.trim() })
      setNotes(prev => [res, ...prev])
      setNote('')
    } catch (e) {
      setErr(e.message || 'Failed to add note')
    }
  }

  const handleDelete = async () => {
    if (!window.confirm(`Delete ${contact.full_name || 'this contact'}? This cannot be undone.`)) return
    setDeleting(true)
    try {
      await api.delete(`/crm-native/contacts/${contact.id}`)
      onDelete(contact.id)
      onClose()
    } catch (e) {
      setErr(e.message || 'Delete failed')
      setDeleting(false)
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
              <label>First Name
                <input value={form.first_name || ''} onChange={e => setForm(f => ({...f, first_name: e.target.value}))} />
              </label>
              <label>Last Name
                <input value={form.last_name || ''} onChange={e => setForm(f => ({...f, last_name: e.target.value}))} />
              </label>
              <label>Phone
                <input value={form.phone || ''} onChange={e => setForm(f => ({...f, phone: e.target.value}))} />
              </label>
              <label>Email
                <input type="email" value={form.email || ''} onChange={e => setForm(f => ({...f, email: e.target.value}))} />
              </label>
            </div>
          </section>

          <section className="crm-section">
            <h3 className="crm-section-title">Address</h3>
            <div className="crm-form-grid">
              <label style={{ gridColumn: '1 / -1' }}>Street
                <input value={form.address_street || ''} onChange={e => setForm(f => ({...f, address_street: e.target.value}))} placeholder="123 Main St" />
              </label>
              <label>City
                <input value={form.address_city || ''} onChange={e => setForm(f => ({...f, address_city: e.target.value}))} placeholder="City" />
              </label>
              <label>State
                <input value={form.address_state || ''} onChange={e => setForm(f => ({...f, address_state: e.target.value}))} placeholder="TX" maxLength={2} />
              </label>
              <label>ZIP
                <input value={form.address_zip || ''} onChange={e => setForm(f => ({...f, address_zip: e.target.value}))} placeholder="75001" />
              </label>
            </div>
          </section>

          <section className="crm-section">
            <h3 className="crm-section-title">Stage</h3>
            <div className="crm-stage-picker">
              {stageList.map(s => (
                <button
                  key={s.key}
                  className={`crm-stage-btn ${form.stage === s.key ? 'crm-stage-btn--active' : ''}`}
                  style={{ '--stage-color': s.color }}
                  onClick={() => setForm(f => ({...f, stage: s.key}))}
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
                ref={textareaRef}
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
              {notesLoading && <div className="crm-empty-notes">Loading notes…</div>}
              {!notesLoading && notes.length === 0 && <div className="crm-empty-notes">No notes yet.</div>}
              {!notesLoading && notes.map((n, i) => (
                <div key={n.id || i} className="crm-note">
                  <div className="crm-note-content">{n.content}</div>
                  <div className="crm-note-date">{fmtDate(n.created_at)}</div>
                </div>
              ))}
            </div>
          </section>
        </div>

        {err && <div className="crm-error" style={{ margin: '0 20px 4px' }}>{err}</div>}

        <div className="crm-panel-footer">
          <button className="btn btn--danger btn--sm" onClick={handleDelete} disabled={deleting} style={{ marginRight: 'auto' }}>
            {deleting ? 'Deleting…' : 'Delete contact'}
          </button>
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
function CreateModal({ stages, onClose, onCreate }) {
  const stageList = stages || FALLBACK_STAGES
  const blank = { first_name: '', last_name: '', phone: '', email: '', address_street: '', address_city: '', address_state: '', address_zip: '', stage: 'inquiry' }
  const [form, setForm] = useState(blank)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const handleSubmit = async () => {
    if (!form.first_name && !form.last_name && !form.phone) { setErr('Name or phone required'); return }
    setSaving(true); setErr('')
    try {
      const res = await api.post('/crm-native/contacts', form)
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
            <label>First Name
              <input value={form.first_name} onChange={e => setForm(f => ({...f, first_name: e.target.value}))} placeholder="Jane" />
            </label>
            <label>Last Name
              <input value={form.last_name} onChange={e => setForm(f => ({...f, last_name: e.target.value}))} placeholder="Smith" />
            </label>
            <label>Phone
              <input value={form.phone} onChange={e => setForm(f => ({...f, phone: e.target.value}))} placeholder="(555) 000-0000" />
            </label>
            <label>Email
              <input type="email" value={form.email} onChange={e => setForm(f => ({...f, email: e.target.value}))} placeholder="jane@example.com" />
            </label>
            <label style={{ gridColumn: '1 / -1' }}>Street Address
              <input value={form.address_street} onChange={e => setForm(f => ({...f, address_street: e.target.value}))} placeholder="123 Main St" />
            </label>
            <label>City
              <input value={form.address_city} onChange={e => setForm(f => ({...f, address_city: e.target.value}))} placeholder="City" />
            </label>
            <label>State
              <input value={form.address_state} onChange={e => setForm(f => ({...f, address_state: e.target.value}))} placeholder="TX" maxLength={2} />
            </label>
            <label>ZIP
              <input value={form.address_zip} onChange={e => setForm(f => ({...f, address_zip: e.target.value}))} placeholder="75001" />
            </label>
            <label>Stage
              <select value={form.stage} onChange={e => setForm(f => ({...f, stage: e.target.value}))}>
                {stageList.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
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

  const [stages, setStages] = useState(FALLBACK_STAGES)
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
      const data = await api.get('/crm-native/contacts')
      // Backend returns paginated envelope {items:[...], total:N}
      setContacts(Array.isArray(data) ? data : (data.items || data.contacts || []))
    } catch (e) {
      setError(e.message || 'Failed to load contacts')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    api.get('/crm-native/stages').then(s => { if (Array.isArray(s)) setStages(s) }).catch(() => {})
    fetchContacts()
  }, [])

  const filtered = useMemo(() => {
    let list = contacts
    if (stageFilter !== 'all') list = list.filter(c => c.stage === stageFilter)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(c =>
        (c.full_name || '').toLowerCase().includes(q) ||
        (c.phone || '').includes(q) ||
        (c.email || '').toLowerCase().includes(q) ||
        (c.address_city || '').toLowerCase().includes(q)
      )
    }
    return list
  }, [contacts, stageFilter, search])

  const stageCounts = useMemo(() => {
    const map = { all: contacts.length }
    stages.forEach(s => { map[s.key] = contacts.filter(c => c.stage === s.key).length })
    return map
  }, [contacts, stages])

  const handleSave = (updated) => {
    setContacts(prev => prev.map(c => c.id === updated.id ? updated : c))
    setSelected(updated)
  }

  const handleDelete = (deletedId) => {
    setContacts(prev => prev.filter(c => c.id !== deletedId))
    setSelected(null)
  }

  const handleCreate = (created) => {
    setContacts(prev => [created, ...prev])
  }

  // ── Pipeline Board View ─────────────────────────────────────────────────
  const renderPipeline = () => (
    <div className="crm-pipeline">
      {stages.map(stage => {
        const cols = filtered.filter(c => c.stage === stage.key)
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
                  {c.address_city && <div className="crm-pipeline-card-company">📍 {c.address_city}{c.address_state ? `, ${c.address_state}` : ''}</div>}
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
          {stages.map(s => (
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
              <ContactCard key={c.id} contact={c} stages={stages} onClick={setSelected} />
            ))}
          </div>
        )
      )}

      {/* Panels + Modals */}
      {selected && (
        <ContactPanel
          contact={selected}
          stages={stages}
          onClose={() => setSelected(null)}
          onSave={handleSave}
          onDelete={handleDelete}
        />
      )}
      {showCreate && (
        <CreateModal
          stages={stages}
          onClose={() => setShowCreate(false)}
          onCreate={handleCreate}
        />
      )}
    </div>
  )
}
