import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const BLOCK_ICONS = {
  text: '¶',
  image: '🖼',
  pdf: '📄',
  video: '▶',
  divider: '—',
  cta: '⚡',
  website_url: '🌐',
}

const BLOCK_LABELS = {
  text: 'Text Section',
  image: 'Image',
  pdf: 'PDF Document',
  video: 'Video Embed',
  divider: 'Divider',
  cta: 'Call to Action',
  website_url: 'Live Site Preview',
}

function BlockCard({ block, onUpdate, onDelete, onMoveUp, onMoveDown, isFirst, isLast, onUpload }) {
  const [editing, setEditing] = useState(false)
  const [localContent, setLocalContent] = useState(block.content || '')
  const [localUrl, setLocalUrl] = useState(block.file_url || '')
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef(null)

  async function saveBlock() {
    setSaving(true)
    try {
      await onUpdate(block.id, { content: localContent, file_url: localUrl })
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  async function handleFileDrop(e) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer?.files?.[0] || e.target?.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await onUpload(file)
      setEditing(false)
    } catch (err) {
      alert(err.message || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: 10,
      padding: '16px 20px',
      display: 'flex',
      gap: 16,
      alignItems: 'flex-start',
    }}>
      {/* Drag / reorder controls */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, paddingTop: 2, flexShrink: 0 }}>
        <button
          onClick={onMoveUp}
          disabled={isFirst}
          title="Move up"
          style={{ background: 'none', border: 'none', color: isFirst ? '#333' : '#888', cursor: isFirst ? 'default' : 'pointer', fontSize: 12, padding: '2px 4px' }}
        >▲</button>
        <button
          onClick={onMoveDown}
          disabled={isLast}
          title="Move down"
          style={{ background: 'none', border: 'none', color: isLast ? '#333' : '#888', cursor: isLast ? 'default' : 'pointer', fontSize: 12, padding: '2px 4px' }}
        >▼</button>
      </div>

      {/* Block body */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: editing ? 12 : 6 }}>
          <span style={{ fontSize: 13 }}>{BLOCK_ICONS[block.block_type]}</span>
          <span style={{ fontSize: 12, color: '#778', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {BLOCK_LABELS[block.block_type] || block.block_type}
          </span>
        </div>

        {editing ? (
          <div>
            {block.block_type !== 'divider' && (
              <div style={{ marginBottom: 10 }}>
                <label style={{ fontSize: 11, color: '#667', display: 'block', marginBottom: 4 }}>
                  {block.block_type === 'text' ? 'Content (Markdown supported)' :
                   block.block_type === 'cta' ? 'Button Label' : 'Caption / Title'}
                </label>
                {block.block_type === 'text' ? (
                  <textarea
                    value={localContent}
                    onChange={e => setLocalContent(e.target.value)}
                    rows={6}
                    style={{
                      width: '100%', boxSizing: 'border-box',
                      background: 'rgba(0,0,0,0.3)',
                      border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: 6, padding: '8px 10px',
                      color: '#fff', fontSize: 13, resize: 'vertical',
                      outline: 'none', fontFamily: 'inherit',
                    }}
                  />
                ) : (
                  <input
                    value={localContent}
                    onChange={e => setLocalContent(e.target.value)}
                    style={{
                      width: '100%', boxSizing: 'border-box',
                      background: 'rgba(0,0,0,0.3)',
                      border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: 6, padding: '8px 10px',
                      color: '#fff', fontSize: 13, outline: 'none',
                    }}
                  />
                )}
              </div>
            )}
            {['image', 'pdf'].includes(block.block_type) && (
              <div style={{ marginBottom: 10 }}>
                <label style={{ fontSize: 11, color: '#667', display: 'block', marginBottom: 6 }}>
                  {block.file_url ? 'Replace File' : 'Upload File'}
                </label>
                {/* Drag-and-drop zone */}
                <div
                  onDragOver={e => { e.preventDefault(); setDragOver(true) }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleFileDrop}
                  onClick={() => fileInputRef.current?.click()}
                  style={{
                    border: `2px dashed ${dragOver ? '#087cff' : 'rgba(255,255,255,0.15)'}`,
                    borderRadius: 8, padding: '20px 16px',
                    textAlign: 'center', cursor: 'pointer',
                    background: dragOver ? 'rgba(8,124,255,0.06)' : 'rgba(255,255,255,0.02)',
                    transition: 'border-color 0.15s, background 0.15s',
                  }}
                >
                  {uploading ? (
                    <div style={{ color: '#087cff', fontSize: 13 }}>Uploading…</div>
                  ) : (
                    <>
                      <div style={{ fontSize: 22, marginBottom: 6 }}>
                        {block.block_type === 'pdf' ? '📄' : '🖼'}
                      </div>
                      <div style={{ fontSize: 13, color: '#aab', marginBottom: 4 }}>
                        {block.file_url
                          ? `Current: ${block.file_name || 'file uploaded'}`
                          : `Drop ${block.block_type === 'pdf' ? 'PDF' : 'image'} here, or click to browse`}
                      </div>
                      <div style={{ fontSize: 11, color: '#556' }}>
                        {block.block_type === 'pdf' ? 'PDF up to 20 MB' : 'PNG, JPG, GIF, WebP up to 20 MB'}
                      </div>
                    </>
                  )}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={block.block_type === 'pdf' ? 'application/pdf' : 'image/*'}
                    style={{ display: 'none' }}
                    onChange={handleFileDrop}
                  />
                </div>
              </div>
            )}
            {['video', 'cta', 'website_url'].includes(block.block_type) && (
              <div style={{ marginBottom: 10 }}>
                <label style={{ fontSize: 11, color: '#667', display: 'block', marginBottom: 4 }}>
                  {block.block_type === 'video' ? 'Video URL (YouTube / Vimeo / Loom)' :
                   block.block_type === 'cta' ? 'Button Link (URL)' :
                   'Website URL (must allow embedding)'}
                </label>
                <input
                  value={localUrl}
                  onChange={e => setLocalUrl(e.target.value)}
                  placeholder={block.block_type === 'video' ? 'https://www.loom.com/share/...' :
                               block.block_type === 'website_url' ? 'https://yourdomain.com' : 'https://...'}
                  style={{
                    width: '100%', boxSizing: 'border-box',
                    background: 'rgba(0,0,0,0.3)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: 6, padding: '8px 10px',
                    color: '#fff', fontSize: 13, outline: 'none',
                  }}
                />
                {block.block_type === 'website_url' && (
                  <div style={{ fontSize: 11, color: '#556', marginTop: 4 }}>
                    Note: some sites (e.g. Google) block iframe embedding. Vercel preview URLs, staging sites, and most custom domains work well.
                  </div>
                )}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={saveBlock}
                disabled={saving}
                style={{
                  background: 'var(--signal-blue, #087cff)', color: '#fff',
                  border: 'none', borderRadius: 6, padding: '6px 14px',
                  fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  opacity: saving ? 0.6 : 1,
                }}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button
                onClick={() => {
                  setLocalContent(block.content || '')
                  setLocalUrl(block.file_url || '')
                  setEditing(false)
                }}
                style={{
                  background: 'rgba(255,255,255,0.06)', color: '#aab',
                  border: 'none', borderRadius: 6, padding: '6px 14px',
                  fontSize: 12, cursor: 'pointer',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div>
            {block.block_type === 'text' && block.content && (
              <p style={{ margin: 0, fontSize: 13, color: '#aab', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                {block.content.length > 160 ? block.content.slice(0, 160) + '…' : block.content}
              </p>
            )}
            {block.block_type === 'image' && (block.file_url || block.content) && (
              <div style={{ fontSize: 13, color: '#668' }}>
                {block.file_url && <span>📎 {block.file_name || block.file_url.split('/').pop()}</span>}
                {block.content && <span style={{ color: '#667' }}> — {block.content}</span>}
              </div>
            )}
            {block.block_type === 'pdf' && (block.file_url || block.content) && (
              <div style={{ fontSize: 13, color: '#668' }}>
                📄 {block.file_name || block.content || block.file_url?.split('/').pop()}
              </div>
            )}
            {block.block_type === 'video' && block.file_url && (
              <div style={{ fontSize: 13, color: '#668' }}>🎬 {block.file_url}</div>
            )}
            {block.block_type === 'website_url' && (
              <div style={{ fontSize: 13, color: block.file_url ? '#19d67c' : '#556' }}>
                {block.file_url ? `🌐 ${block.file_url}` : 'No URL set — click Edit to add one'}
              </div>
            )}
            {block.block_type === 'cta' && block.content && (
              <div style={{ fontSize: 13, color: '#19d67c' }}>→ {block.content} {block.file_url && `(${block.file_url})`}</div>
            )}
            {block.block_type === 'divider' && (
              <div style={{ height: 1, background: 'rgba(255,255,255,0.1)', margin: '4px 0' }} />
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
        {block.block_type !== 'divider' && (
          <button
            onClick={() => setEditing(e => !e)}
            style={{
              background: 'rgba(255,255,255,0.06)', color: '#aab',
              border: 'none', borderRadius: 6, padding: '5px 10px',
              fontSize: 12, cursor: 'pointer',
            }}
          >
            {editing ? '✕' : 'Edit'}
          </button>
        )}
        <button
          onClick={() => onDelete(block.id)}
          style={{
            background: 'rgba(255,80,80,0.08)', color: '#e66',
            border: 'none', borderRadius: 6, padding: '5px 10px',
            fontSize: 12, cursor: 'pointer',
          }}
        >
          Delete
        </button>
      </div>
    </div>
  )
}

function SendModal({ proposal, onClose, onSent }) {
  const [form, setForm] = useState({
    recipient_email: proposal.client_email || '',
    recipient_name: proposal.client_name || '',
    personal_note: '',
    expires_hours: 72,
    protect_content: false,
  })
  const [sending, setSending] = useState(false)
  const [result, setResult] = useState(null)

  async function handleSend(e) {
    e.preventDefault()
    setSending(true)
    try {
      const r = await api.post(`/proposals/${proposal.id}/send`, form)
      setResult(r)
      onSent && onSent(r)
    } catch (err) {
      alert(err.message || 'Failed to send invite')
    } finally {
      setSending(false)
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--bg-card, #081224)',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 16, padding: 32,
          width: 500, maxWidth: '90vw',
        }}
      >
        {result ? (
          <div>
            <div style={{ fontSize: 32, marginBottom: 12 }}>✅</div>
            <h3 style={{ margin: '0 0 8px', color: '#fff', fontSize: 18 }}>Invite Sent!</h3>
            <p style={{ color: '#aab', fontSize: 14, margin: '0 0 16px' }}>
              Sent to <strong>{result.recipient_email}</strong>. The link expires in 72 hours.
            </p>
            <div
              style={{
                background: 'rgba(0,0,0,0.4)', borderRadius: 8, padding: '10px 14px',
                fontSize: 12, color: '#19d67c', wordBreak: 'break-all', marginBottom: 20,
                fontFamily: 'monospace',
              }}
            >
              {result.portal_url}
            </div>
            <button
              onClick={onClose}
              style={{
                background: 'var(--signal-blue, #087cff)', color: '#fff',
                border: 'none', borderRadius: 8, padding: '10px 24px',
                fontSize: 14, fontWeight: 600, cursor: 'pointer',
              }}
            >
              Done
            </button>
          </div>
        ) : (
          <form onSubmit={handleSend}>
            <h3 style={{ margin: '0 0 24px', color: '#fff', fontSize: 18, fontWeight: 700 }}>
              Send to Client
            </h3>
            {[
              { key: 'recipient_name', label: 'Client Name', placeholder: 'Sarah Johnson' },
              { key: 'recipient_email', label: 'Client Email', placeholder: 'sarah@acme.com', type: 'email', required: true },
            ].map(f => (
              <div key={f.key} style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 12, color: '#aab', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                  {f.label}
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
                    color: '#fff', fontSize: 14, outline: 'none',
                  }}
                />
              </div>
            ))}
            <div style={{ marginBottom: 20 }}>
              <label style={{ display: 'block', fontSize: 12, color: '#aab', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                Personal Note (optional)
              </label>
              <textarea
                placeholder="Add a personal message to include in the email…"
                value={form.personal_note}
                onChange={e => setForm(p => ({ ...p, personal_note: e.target.value }))}
                rows={3}
                style={{
                  width: '100%', boxSizing: 'border-box',
                  background: 'rgba(255,255,255,0.05)',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 8, padding: '10px 12px',
                  color: '#fff', fontSize: 14, outline: 'none',
                  resize: 'none', fontFamily: 'inherit',
                }}
              />
            </div>
            {/* Content protection toggle */}
            <div
              onClick={() => setForm(p => ({ ...p, protect_content: !p.protect_content }))}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                background: form.protect_content ? 'rgba(8,124,255,0.08)' : 'rgba(255,255,255,0.03)',
                border: `1px solid ${form.protect_content ? 'rgba(8,124,255,0.3)' : 'rgba(255,255,255,0.08)'}`,
                borderRadius: 10, padding: '12px 14px',
                cursor: 'pointer', marginBottom: 20,
                transition: 'background 0.15s, border-color 0.15s',
              }}
            >
              {/* Toggle pill */}
              <div style={{
                width: 36, height: 20, borderRadius: 10, flexShrink: 0,
                background: form.protect_content ? 'var(--signal-blue, #087cff)' : 'rgba(255,255,255,0.12)',
                position: 'relative', transition: 'background 0.2s',
              }}>
                <div style={{
                  position: 'absolute', top: 2,
                  left: form.protect_content ? 18 : 2,
                  width: 16, height: 16, borderRadius: '50%',
                  background: '#fff',
                  transition: 'left 0.2s',
                  boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
                }} />
              </div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: form.protect_content ? '#fff' : '#aab' }}>
                  🔒 Protect content
                </div>
                <div style={{ fontSize: 11, color: '#556', marginTop: 2 }}>
                  Disables right-click, download, drag, text copy, and print shortcuts
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12 }}>
              <button
                type="button"
                onClick={onClose}
                style={{
                  flex: 1, background: 'rgba(255,255,255,0.06)', color: '#aab',
                  border: 'none', borderRadius: 8, padding: '10px',
                  fontSize: 14, cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={sending}
                style={{
                  flex: 2, background: 'var(--signal-green, #19d67c)', color: '#000',
                  border: 'none', borderRadius: 8, padding: '10px',
                  fontSize: 14, fontWeight: 700, cursor: 'pointer',
                  opacity: sending ? 0.6 : 1,
                }}
              >
                {sending ? 'Sending…' : '✉ Send Magic Link'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

export default function ProposalEditor() {
  const { proposalId } = useParams()
  const navigate = useNavigate()
  const [proposal, setProposal] = useState(null)
  const [blocks, setBlocks] = useState([])
  const [loading, setLoading] = useState(true)
  const [publishing, setPublishing] = useState(false)
  const [showAddBlock, setShowAddBlock] = useState(false)
  const [showSend, setShowSend] = useState(false)
  const [showAnalytics, setShowAnalytics] = useState(false)
  const [analytics, setAnalytics] = useState(null)

  useEffect(() => {
    loadProposal()
  }, [proposalId])

  async function loadProposal() {
    try {
      const r = await api.get(`/proposals/${proposalId}`)
      setProposal(r)
      setBlocks(r.blocks || [])
    } catch {
      navigate('/proposals')
    } finally {
      setLoading(false)
    }
  }

  async function togglePublish() {
    setPublishing(true)
    try {
      const endpoint = proposal.status === 'published' ? 'unpublish' : 'publish'
      const r = await api.post(`/proposals/${proposalId}/${endpoint}`)
      setProposal(p => ({ ...p, status: r.status }))
    } catch (err) {
      alert(err.message || 'Failed to update status')
    } finally {
      setPublishing(false)
    }
  }

  async function addBlock(blockType) {
    try {
      const r = await api.post(`/proposals/${proposalId}/blocks`, { block_type: blockType })
      setBlocks(b => [...b, r])
      setShowAddBlock(false)
    } catch (err) {
      alert('Failed to add block')
    }
  }

  async function updateBlock(blockId, data) {
    const r = await api.patch(`/proposals/${proposalId}/blocks/${blockId}`, data)
    setBlocks(bs => bs.map(b => b.id === blockId ? { ...b, ...r } : b))
  }

  async function deleteBlock(blockId) {
    if (!confirm('Delete this block?')) return
    await api.delete(`/proposals/${proposalId}/blocks/${blockId}`)
    setBlocks(bs => bs.filter(b => b.id !== blockId))
  }

  async function moveBlock(blockId, direction) {
    const idx = blocks.findIndex(b => b.id === blockId)
    if (idx < 0) return
    const newBlocks = [...blocks]
    const swapIdx = direction === 'up' ? idx - 1 : idx + 1
    if (swapIdx < 0 || swapIdx >= newBlocks.length) return
    ;[newBlocks[idx], newBlocks[swapIdx]] = [newBlocks[swapIdx], newBlocks[idx]]
    const ordered = newBlocks.map((b, i) => ({ ...b, position: i }))
    setBlocks(ordered)
    await api.post(`/proposals/${proposalId}/blocks/reorder`, {
      block_ids: ordered.map(b => b.id),
    })
  }

  async function uploadFileForBlock(blockId, file) {
    const fd = new FormData()
    fd.append('file', file)
    const r = await api.upload(`/proposals/${proposalId}/upload`, fd)
    // r = { file_id, file_url, filename, content_type, file_size }
    await updateBlock(blockId, {
      file_url: r.file_url,
      file_name: r.filename,
      file_size: r.file_size,
    })
  }

  async function loadAnalytics() {
    const r = await api.get(`/proposals/${proposalId}/analytics`)
    setAnalytics(r)
    setShowAnalytics(true)
  }

  if (loading) return <div style={{ padding: 32, color: '#666' }}>Loading…</div>
  if (!proposal) return null

  const isPublished = proposal.status === 'published'

  return (
    <div style={{ padding: '32px', maxWidth: 800, margin: '0 auto' }}>
      {/* Back */}
      <button
        onClick={() => navigate('/proposals')}
        style={{ background: 'none', border: 'none', color: '#667', cursor: 'pointer', fontSize: 13, marginBottom: 24, padding: 0 }}
      >
        ← Back to Proposals
      </button>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 32, gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary, #fff)' }}>
              {proposal.title}
            </h1>
            <span style={{
              fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 20,
              background: isPublished ? 'rgba(25,214,124,0.15)' : 'rgba(255,255,255,0.06)',
              color: isPublished ? '#19d67c' : '#667',
              textTransform: 'uppercase', letterSpacing: '0.05em',
            }}>
              {proposal.status}
            </span>
          </div>
          {(proposal.client_name || proposal.client_company) && (
            <div style={{ fontSize: 13, color: '#667' }}>
              For {proposal.client_name}{proposal.client_company ? ` · ${proposal.client_company}` : ''}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 10, flexShrink: 0 }}>
          <button
            onClick={loadAnalytics}
            style={{
              background: 'rgba(255,255,255,0.06)', color: '#aab',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 8, padding: '8px 16px', fontSize: 13, cursor: 'pointer',
            }}
          >
            📊 Analytics
          </button>
          <button
            onClick={togglePublish}
            disabled={publishing}
            style={{
              background: isPublished ? 'rgba(255,255,255,0.06)' : 'rgba(25,214,124,0.15)',
              color: isPublished ? '#aab' : '#19d67c',
              border: `1px solid ${isPublished ? 'rgba(255,255,255,0.1)' : 'rgba(25,214,124,0.3)'}`,
              borderRadius: 8, padding: '8px 16px', fontSize: 13,
              fontWeight: 600, cursor: 'pointer',
              opacity: publishing ? 0.6 : 1,
            }}
          >
            {publishing ? '…' : isPublished ? 'Unpublish' : 'Publish'}
          </button>
          {isPublished && (
            <button
              onClick={() => setShowSend(true)}
              style={{
                background: 'var(--signal-blue, #087cff)', color: '#fff',
                border: 'none', borderRadius: 8, padding: '8px 16px',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
              }}
            >
              ✉ Send to Client
            </button>
          )}
        </div>
      </div>

      {/* Content blocks */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#556', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 14 }}>
          Content Blocks
        </div>

        {blocks.length === 0 ? (
          <div style={{
            border: '1px dashed rgba(255,255,255,0.1)', borderRadius: 10,
            padding: '40px 24px', textAlign: 'center', color: '#556',
          }}>
            <div style={{ fontSize: 13, marginBottom: 12 }}>No content yet</div>
            <button
              onClick={() => setShowAddBlock(true)}
              style={{
                background: 'var(--signal-blue, #087cff)', color: '#fff',
                border: 'none', borderRadius: 8, padding: '8px 20px',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
              }}
            >
              Add First Block
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {blocks.map((block, idx) => (
              <BlockCard
                key={block.id}
                block={block}
                onUpdate={updateBlock}
                onDelete={deleteBlock}
                onMoveUp={() => moveBlock(block.id, 'up')}
                onMoveDown={() => moveBlock(block.id, 'down')}
                isFirst={idx === 0}
                isLast={idx === blocks.length - 1}
                onUpload={(file) => uploadFileForBlock(block.id, file)}
              />
            ))}
          </div>
        )}

        {blocks.length > 0 && (
          <button
            onClick={() => setShowAddBlock(true)}
            style={{
              marginTop: 14, width: '100%',
              background: 'rgba(255,255,255,0.03)',
              border: '1px dashed rgba(255,255,255,0.1)',
              borderRadius: 10, padding: '12px',
              color: '#556', fontSize: 13, cursor: 'pointer',
            }}
          >
            + Add Block
          </button>
        )}
      </div>

      {/* Add block picker */}
      {showAddBlock && (
        <div
          onClick={() => setShowAddBlock(false)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: 'var(--bg-card, #081224)',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 16, padding: 28, width: 420, maxWidth: '90vw',
            }}
          >
            <h3 style={{ margin: '0 0 20px', color: '#fff', fontSize: 16, fontWeight: 700 }}>Add Content Block</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {Object.entries(BLOCK_LABELS).map(([type, label]) => (
                <button
                  key={type}
                  onClick={() => addBlock(type)}
                  style={{
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 10, padding: '14px 16px',
                    color: '#ccd', fontSize: 13, cursor: 'pointer',
                    textAlign: 'left',
                    display: 'flex', alignItems: 'center', gap: 10,
                  }}
                  onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(8,124,255,0.4)'}
                  onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'}
                >
                  <span style={{ fontSize: 18 }}>{BLOCK_ICONS[type]}</span>
                  <span style={{ fontWeight: 600 }}>{label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Send modal */}
      {showSend && (
        <SendModal
          proposal={proposal}
          onClose={() => setShowSend(false)}
          onSent={() => {}}
        />
      )}

      {/* Analytics panel */}
      {showAnalytics && analytics && (
        <div
          onClick={() => setShowAnalytics(false)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: 'var(--bg-card, #081224)',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 16, padding: 32,
              width: 600, maxWidth: '90vw', maxHeight: '80vh',
              overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
              <h3 style={{ margin: 0, color: '#fff', fontSize: 18, fontWeight: 700 }}>Analytics</h3>
              <button onClick={() => setShowAnalytics(false)} style={{ background: 'none', border: 'none', color: '#667', cursor: 'pointer', fontSize: 18 }}>✕</button>
            </div>

            {/* Summary stats */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 28 }}>
              {[
                { label: 'Total Opens', value: analytics.total_opens },
                { label: 'Unique Recipients', value: analytics.unique_recipients_opened },
                { label: 'Downloads', value: analytics.total_downloads },
                { label: 'Avg Duration', value: analytics.avg_duration_seconds > 0 ? `${Math.round(analytics.avg_duration_seconds / 60)}m` : '—' },
              ].map(s => (
                <div key={s.label} style={{
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: 10, padding: '14px 16px', textAlign: 'center',
                }}>
                  <div style={{ fontSize: 24, fontWeight: 700, color: '#fff', marginBottom: 4 }}>{s.value}</div>
                  <div style={{ fontSize: 11, color: '#556', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{s.label}</div>
                </div>
              ))}
            </div>

            {/* Per-recipient */}
            {analytics.token_activity.length > 0 && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: '#556', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 12 }}>
                  Recipients
                </div>
                {analytics.token_activity.map(t => (
                  <div key={t.token_id} style={{
                    display: 'flex', alignItems: 'center', gap: 14,
                    padding: '12px 0', borderBottom: '1px solid rgba(255,255,255,0.05)',
                  }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: '50%',
                      background: t.open_count > 0 ? 'rgba(25,214,124,0.15)' : 'rgba(255,255,255,0.06)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 14, flexShrink: 0,
                      color: t.open_count > 0 ? '#19d67c' : '#556',
                    }}>
                      {t.open_count > 0 ? '✓' : '—'}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: '#ccd', fontWeight: 600 }}>
                        {t.recipient_name || t.recipient_email}
                      </div>
                      {t.recipient_name && (
                        <div style={{ fontSize: 12, color: '#556' }}>{t.recipient_email}</div>
                      )}
                    </div>
                    <div style={{ fontSize: 12, color: '#556', textAlign: 'right' }}>
                      {t.open_count > 0 ? (
                        <>
                          <div style={{ color: '#aab' }}>{t.open_count} open{t.open_count !== 1 ? 's' : ''}</div>
                          <div>{t.last_scroll_pct}% scrolled{t.downloaded ? ' · downloaded' : ''}</div>
                        </>
                      ) : (
                        <div style={{ color: '#445' }}>Not opened</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {analytics.total_opens === 0 && (
              <div style={{ textAlign: 'center', color: '#445', padding: '32px 0', fontSize: 14 }}>
                No views yet. Send the proposal to a client to start tracking.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
