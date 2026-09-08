/**
 * ExecutiveWorkspaceItem — deal-room detail view.
 *
 * Shows item metadata, working notes editor, file list with upload/replace,
 * and a version history panel. Every action is org-gated server-side.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
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
      display: 'inline-block', padding: '2px 10px', borderRadius: 12,
      fontSize: 12, fontWeight: 600, color: '#fff', background: color,
    }}>
      {status}
    </span>
  )
}

function Section({ title, children }) {
  return (
    <section style={{ marginBottom: 36 }}>
      <h2 style={{ fontSize: 15, fontWeight: 700, color: '#374151', margin: '0 0 12px' }}>{title}</h2>
      {children}
    </section>
  )
}

function FileRow({ file, itemId, onReplace }) {
  const ext = (file.filename || '').split('.').pop().toUpperCase()
  const kb = Math.ceil((file.file_size || 0) / 1024)
  const url = `/api/executive/workspace/${itemId}/files/${file.id}/serve`

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0',
      borderBottom: '1px solid #f3f4f6' }}>
      <div style={{ width: 36, height: 36, borderRadius: 6, background: '#eff6ff',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 10, fontWeight: 700, color: '#2563eb', flexShrink: 0 }}>
        {ext || 'FILE'}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 500, fontSize: 14, whiteSpace: 'nowrap',
          overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {file.filename}
        </div>
        <div style={{ fontSize: 12, color: '#9ca3af' }}>
          {kb} KB · {file.uploaded_at ? new Date(file.uploaded_at).toLocaleDateString() : ''}
        </div>
      </div>
      <a
        href={url}
        download={file.filename}
        style={{ fontSize: 13, color: '#2563eb', textDecoration: 'none', fontWeight: 500 }}
        onClick={e => e.stopPropagation()}
      >
        Download
      </a>
      <button
        onClick={() => onReplace(file)}
        style={{ fontSize: 12, color: '#6b7280', border: '1px solid #e5e7eb',
          borderRadius: 5, padding: '4px 10px', cursor: 'pointer', background: 'none' }}
      >
        Replace
      </button>
    </div>
  )
}

export default function ExecutiveWorkspaceItem() {
  const { itemId } = useParams()
  const nav = useNavigate()

  const [item, setItem] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState({})
  const [saving, setSaving] = useState(false)

  const [files, setFiles] = useState([])
  const [versions, setVersions] = useState([])
  const [showVersions, setShowVersions] = useState(false)

  const [uploading, setUploading] = useState(false)
  const [replaceTarget, setReplaceTarget] = useState(null)
  const fileInputRef = useRef()
  const replaceInputRef = useRef()

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      api.get(`/executive/workspace/${itemId}`),
      api.get(`/executive/workspace/${itemId}/files`),
    ])
      .then(([i, f]) => {
        setItem(i)
        setEditForm({ title: i.title, status: i.status, working_notes: i.working_notes || '' })
        setFiles(f.files || [])
        setError(null)
      })
      .catch(e => setError(e?.detail || 'Failed to load item'))
      .finally(() => setLoading(false))
  }, [itemId])

  useEffect(() => { load() }, [load])

  const loadVersions = () => {
    api.get(`/executive/workspace/${itemId}/versions`)
      .then(d => setVersions(d.versions || []))
      .catch(() => {})
  }

  const handleSave = async (e) => {
    e.preventDefault()
    setSaving(true)
    try {
      const updated = await api.patch(`/executive/workspace/${itemId}`, editForm)
      setItem(updated)
      setEditing(false)
    } catch (err) {
      alert(err?.detail || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const newFile = await api.post(`/executive/workspace/${itemId}/files`, form)
      setFiles(f => [newFile, ...f])
    } catch (err) {
      alert(err?.detail || 'Upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleReplace = async (e) => {
    const file = e.target.files?.[0]
    if (!file || !replaceTarget) return
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const newFile = await api.put(`/executive/workspace/${itemId}/files/${replaceTarget.id}`, form)
      setFiles(fs => fs
        .filter(f => f.id !== replaceTarget.id)
        .concat([newFile])
        .filter(f => f.is_current)
      )
      setReplaceTarget(null)
    } catch (err) {
      alert(err?.detail || 'Replace failed')
    } finally {
      setUploading(false)
      if (replaceInputRef.current) replaceInputRef.current.value = ''
    }
  }

  const startReplace = (file) => {
    setReplaceTarget(file)
    setTimeout(() => replaceInputRef.current?.click(), 50)
  }

  if (loading) return <div style={{ padding: 40, color: '#6b7280' }}>Loading…</div>
  if (error) return <div style={{ padding: 40, color: '#dc2626' }}>{error}</div>
  if (!item) return null

  return (
    <div style={{ padding: '32px 40px', maxWidth: 800 }}>
      {/* Breadcrumb */}
      <button
        onClick={() => nav('/executive/workspace')}
        style={{ background: 'none', border: 'none', color: '#6b7280', fontSize: 13,
          cursor: 'pointer', padding: 0, marginBottom: 16 }}
      >
        ← Workspace
      </button>

      {/* Title + status */}
      {!editing ? (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, marginBottom: 28 }}>
          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700 }}>{item.title}</h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
              <StatusPill status={item.status} />
              <span style={{ fontSize: 13, color: '#9ca3af' }}>
                Updated {item.updated_at ? new Date(item.updated_at).toLocaleDateString() : '—'}
              </span>
            </div>
          </div>
          <button
            onClick={() => setEditing(true)}
            style={{ border: '1px solid #d1d5db', borderRadius: 7, padding: '7px 16px',
              fontSize: 13, cursor: 'pointer', background: 'none', fontWeight: 500 }}
          >
            Edit
          </button>
        </div>
      ) : (
        <form onSubmit={handleSave} style={{
          background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10,
          padding: '20px 24px', marginBottom: 28,
        }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, color: '#6b7280', display: 'block', marginBottom: 4 }}>Title</label>
              <input
                value={editForm.title}
                onChange={e => setEditForm(f => ({ ...f, title: e.target.value }))}
                required
                style={{ width: '100%', padding: '7px 10px', borderRadius: 6,
                  border: '1px solid #d1d5db', fontSize: 14, boxSizing: 'border-box' }}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, color: '#6b7280', display: 'block', marginBottom: 4 }}>Status</label>
              <select
                value={editForm.status}
                onChange={e => setEditForm(f => ({ ...f, status: e.target.value }))}
                style={{ padding: '7px 10px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 14 }}
              >
                {STATUS_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>
          <div style={{ marginTop: 12 }}>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block', marginBottom: 4 }}>Working notes</label>
            <textarea
              value={editForm.working_notes}
              onChange={e => setEditForm(f => ({ ...f, working_notes: e.target.value }))}
              rows={4}
              style={{ width: '100%', padding: '7px 10px', borderRadius: 6,
                border: '1px solid #d1d5db', fontSize: 14, resize: 'vertical', boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <button type='submit' disabled={saving} style={{
              background: '#2563eb', color: '#fff', border: 'none',
              borderRadius: 6, padding: '8px 18px', fontSize: 14, fontWeight: 600, cursor: 'pointer',
            }}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button type='button' onClick={() => setEditing(false)} style={{
              background: 'none', border: '1px solid #d1d5db',
              borderRadius: 6, padding: '8px 18px', fontSize: 14, cursor: 'pointer',
            }}>Cancel</button>
          </div>
        </form>
      )}

      {/* Working notes (read mode) */}
      {!editing && item.working_notes && (
        <Section title='Notes'>
          <p style={{ margin: 0, fontSize: 14, lineHeight: 1.7, color: '#374151',
            whiteSpace: 'pre-wrap' }}>
            {item.working_notes}
          </p>
        </Section>
      )}

      {/* Files */}
      <Section title='Files'>
        <input
          ref={fileInputRef}
          type='file'
          style={{ display: 'none' }}
          onChange={handleUpload}
        />
        <input
          ref={replaceInputRef}
          type='file'
          style={{ display: 'none' }}
          onChange={handleReplace}
        />

        {files.length === 0 && (
          <div style={{ color: '#9ca3af', fontSize: 14, marginBottom: 12 }}>No files yet.</div>
        )}
        {files.map(f => (
          <FileRow key={f.id} file={f} itemId={itemId} onReplace={startReplace} />
        ))}

        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          style={{
            marginTop: 14, border: '1px dashed #d1d5db', borderRadius: 8,
            padding: '10px 20px', fontSize: 13, color: '#6b7280',
            cursor: 'pointer', background: 'none', width: '100%',
          }}
        >
          {uploading ? 'Uploading…' : '+ Upload file'}
        </button>
      </Section>

      {/* Version history */}
      <Section title='Version history'>
        <button
          onClick={() => {
            if (!showVersions) loadVersions()
            setShowVersions(v => !v)
          }}
          style={{ fontSize: 13, color: '#2563eb', background: 'none',
            border: 'none', cursor: 'pointer', padding: 0, marginBottom: 12 }}
        >
          {showVersions ? 'Hide history' : 'Show history'}
        </button>

        {showVersions && (
          versions.length === 0
            ? <div style={{ color: '#9ca3af', fontSize: 14 }}>No versions recorded yet.</div>
            : versions.map(v => (
              <div key={v.id} style={{ padding: '10px 0', borderBottom: '1px solid #f3f4f6' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{v.snapshot_title || '(untitled)'}</span>
                  <span style={{ fontSize: 12, background: '#f3f4f6', borderRadius: 4,
                    padding: '1px 7px', color: '#6b7280' }}>
                    {v.snapshot_status}
                  </span>
                </div>
                {v.snapshot_notes && (
                  <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 3,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {v.snapshot_notes}
                  </div>
                )}
                <div style={{ fontSize: 11, color: '#d1d5db', marginTop: 3 }}>
                  {v.saved_at ? new Date(v.saved_at).toLocaleString() : ''}
                </div>
              </div>
            ))
        )}
      </Section>
    </div>
  )
}
