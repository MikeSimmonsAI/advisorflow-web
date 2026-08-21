/**
 * PortalViewer — the immersive client-facing proposal experience
 *
 * URL: /portal/view/:proposalId
 * Auth: sessionStorage portal_view_id + portal_proposal (set by PortalAccess)
 *
 * Design goals:
 *  - Full-screen, dark, premium — nothing that looks like the internal app
 *  - Client sees their name, the proposal title, and content blocks
 *  - Scroll depth + time tracked via heartbeat pings every 15s
 *  - Final stats sent on beforeunload via sendBeacon
 *  - PDF/image blocks embedded inline, video blocks embedded via iframe
 *  - Download button per file block, marks download event on backend
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'

// ── Markdown renderer (lightweight, no external lib) ─────────────────────────
function renderMarkdown(text) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^#{3}\s+(.+)$/gm, '<h3>$1</h3>')
    .replace(/^#{2}\s+(.+)$/gm, '<h2>$1</h2>')
    .replace(/^#{1}\s+(.+)$/gm, '<h1>$1</h1>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br/>')
}

// ── Block renderers ───────────────────────────────────────────────────────────
function TextBlock({ block }) {
  return (
    <div style={{
      color: '#c8d4e8',
      fontSize: 16,
      lineHeight: 1.85,
      maxWidth: 680,
    }}>
      <div
        dangerouslySetInnerHTML={{ __html: '<p>' + renderMarkdown(block.content) + '</p>' }}
        style={{ margin: 0 }}
      />
    </div>
  )
}

function ImageBlock({ block, onDownload, canDownload }) {
  const [loaded, setLoaded] = useState(false)
  if (!block.file_url) return null
  return (
    <div>
      {block.content && (
        <p style={{ color: '#7a92b4', fontSize: 13, margin: '0 0 12px', fontStyle: 'italic' }}>
          {block.content}
        </p>
      )}
      <div style={{
        borderRadius: 12, overflow: 'hidden',
        border: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(0,0,0,0.3)',
        opacity: loaded ? 1 : 0,
        transition: 'opacity 0.4s ease',
      }}>
        <img
          src={block.file_url}
          alt={block.content || 'Image'}
          onLoad={() => setLoaded(true)}
          style={{ width: '100%', display: 'block', maxHeight: 600, objectFit: 'contain' }}
        />
      </div>
      {canDownload && block.file_url && (
        <button
          onClick={() => { window.open(block.file_url, '_blank'); onDownload() }}
          style={{
            marginTop: 12, background: 'none',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: 8, padding: '7px 16px',
            color: '#7a92b4', fontSize: 12, cursor: 'pointer',
          }}
        >
          ↓ {block.file_name || 'Download Image'}
          {block.file_size ? ` (${(block.file_size / 1024).toFixed(0)} KB)` : ''}
        </button>
      )}
    </div>
  )
}

function PdfBlock({ block, onDownload, canDownload }) {
  const [expanded, setExpanded] = useState(false)
  if (!block.file_url) return null
  return (
    <div style={{
      border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 12,
      overflow: 'hidden',
      background: 'rgba(8,12,30,0.6)',
    }}>
      {/* Preview bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14,
        padding: '16px 20px',
        borderBottom: expanded ? '1px solid rgba(255,255,255,0.08)' : 'none',
      }}>
        <div style={{
          width: 40, height: 40, borderRadius: 8,
          background: 'rgba(8,124,255,0.15)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 18, flexShrink: 0,
        }}>
          📄
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#ddeeff' }}>
            {block.content || block.file_name || 'Document'}
          </div>
          {block.file_size && (
            <div style={{ fontSize: 12, color: '#4a6080' }}>
              {block.file_name} · {(block.file_size / 1024).toFixed(0)} KB
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => setExpanded(e => !e)}
            style={{
              background: 'rgba(8,124,255,0.15)',
              border: '1px solid rgba(8,124,255,0.3)',
              borderRadius: 8, padding: '7px 14px',
              color: '#087cff', fontSize: 12, fontWeight: 600, cursor: 'pointer',
            }}
          >
            {expanded ? 'Collapse' : 'Preview'}
          </button>
          {canDownload && (
            <button
              onClick={() => { window.open(block.file_url, '_blank'); onDownload() }}
              style={{
                background: 'rgba(25,214,124,0.1)',
                border: '1px solid rgba(25,214,124,0.3)',
                borderRadius: 8, padding: '7px 14px',
                color: '#19d67c', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              }}
            >
              ↓ Download
            </button>
          )}
        </div>
      </div>
      {/* Inline PDF viewer */}
      {expanded && (
        <iframe
          src={`${block.file_url}#toolbar=0&navpanes=0`}
          title={block.content || 'Document'}
          style={{ width: '100%', height: 600, border: 'none', display: 'block' }}
        />
      )}
    </div>
  )
}

function VideoBlock({ block }) {
  const [ready, setReady] = useState(false)
  if (!block.file_url) return null

  // Transform share URLs to embed URLs
  let embedUrl = block.file_url
  if (embedUrl.includes('youtube.com/watch')) {
    const id = new URL(embedUrl).searchParams.get('v')
    if (id) embedUrl = `https://www.youtube.com/embed/${id}`
  } else if (embedUrl.includes('youtu.be/')) {
    const id = embedUrl.split('youtu.be/')[1]?.split('?')[0]
    if (id) embedUrl = `https://www.youtube.com/embed/${id}`
  } else if (embedUrl.includes('vimeo.com/')) {
    const id = embedUrl.split('vimeo.com/')[1]?.split('?')[0]
    if (id) embedUrl = `https://player.vimeo.com/video/${id}`
  } else if (embedUrl.includes('loom.com/share/')) {
    const id = embedUrl.split('loom.com/share/')[1]?.split('?')[0]
    if (id) embedUrl = `https://www.loom.com/embed/${id}`
  }

  return (
    <div>
      {block.content && (
        <p style={{ color: '#7a92b4', fontSize: 13, margin: '0 0 14px', fontStyle: 'italic' }}>
          {block.content}
        </p>
      )}
      <div style={{
        borderRadius: 12, overflow: 'hidden',
        border: '1px solid rgba(255,255,255,0.08)',
        background: '#000',
        aspectRatio: '16/9',
        position: 'relative',
      }}>
        <iframe
          src={embedUrl}
          title={block.content || 'Video'}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
          allowFullScreen
          style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
        />
      </div>
    </div>
  )
}

function DividerBlock() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 16, padding: '8px 0',
    }}>
      <div style={{ flex: 1, height: 1, background: 'linear-gradient(to right, transparent, rgba(8,124,255,0.3), transparent)' }} />
      <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'rgba(8,124,255,0.5)' }} />
      <div style={{ flex: 1, height: 1, background: 'linear-gradient(to left, transparent, rgba(8,124,255,0.3), transparent)' }} />
    </div>
  )
}

function CtaBlock({ block }) {
  if (!block.content) return null
  return (
    <div style={{ textAlign: 'center', padding: '8px 0' }}>
      <a
        href={block.file_url || '#'}
        target={block.file_url ? '_blank' : '_self'}
        rel="noopener noreferrer"
        style={{
          display: 'inline-block',
          background: 'linear-gradient(135deg, #087cff, #0557c4)',
          color: '#fff',
          textDecoration: 'none',
          borderRadius: 12,
          padding: '14px 36px',
          fontSize: 15,
          fontWeight: 700,
          boxShadow: '0 4px 20px rgba(8,124,255,0.35)',
          transition: 'transform 0.1s, box-shadow 0.1s',
        }}
        onMouseEnter={e => {
          e.currentTarget.style.transform = 'translateY(-1px)'
          e.currentTarget.style.boxShadow = '0 6px 28px rgba(8,124,255,0.5)'
        }}
        onMouseLeave={e => {
          e.currentTarget.style.transform = 'translateY(0)'
          e.currentTarget.style.boxShadow = '0 4px 20px rgba(8,124,255,0.35)'
        }}
      >
        {block.content}
      </a>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function PortalViewer() {
  const { proposalId } = useParams()
  const navigate = useNavigate()
  const [proposal, setProposal] = useState(null)
  const [error, setError] = useState(null)
  const [revealed, setRevealed] = useState(false)
  const viewIdRef = useRef(null)
  const startRef = useRef(Date.now())
  const scrollPctRef = useRef(0)
  const pingRef = useRef(null)
  const containerRef = useRef(null)
  const canDownload = useRef(true)

  // Load from sessionStorage (set by PortalAccess)
  useEffect(() => {
    const raw = sessionStorage.getItem('portal_proposal')
    const viewId = sessionStorage.getItem('portal_view_id')
    const perms = JSON.parse(sessionStorage.getItem('portal_permissions') || '{}')

    if (!raw || !viewId) {
      setError('Your session has expired. Please use the link from your email to re-enter.')
      return
    }

    try {
      const p = JSON.parse(raw)
      if (p.id !== proposalId) {
        setError('Proposal not found. Please use the link from your email.')
        return
      }
      setProposal(p)
      viewIdRef.current = viewId
      canDownload.current = perms.can_download !== false

      // Fade in
      setTimeout(() => setRevealed(true), 80)
    } catch {
      setError('Failed to load proposal. Please use the link from your email.')
    }
  }, [proposalId])

  // Scroll tracking
  const handleScroll = useCallback(() => {
    if (!containerRef.current) return
    const el = containerRef.current
    const scrolled = el.scrollTop + el.clientHeight
    const total = el.scrollHeight
    const pct = Math.round((scrolled / total) * 100)
    if (pct > scrollPctRef.current) scrollPctRef.current = pct
  }, [])

  // Heartbeat ping every 15s
  useEffect(() => {
    if (!viewIdRef.current) return
    pingRef.current = setInterval(() => {
      const elapsed = Math.round((Date.now() - startRef.current) / 1000)
      api.post(`/proposals/portal/view/${viewIdRef.current}/ping`, {
        scroll_pct: scrollPctRef.current,
        elapsed_seconds: elapsed,
      }).catch(() => {})
    }, 15000)
    return () => clearInterval(pingRef.current)
  }, [proposal])

  // Final close event on unload
  useEffect(() => {
    function handleUnload() {
      if (!viewIdRef.current) return
      const elapsed = Math.round((Date.now() - startRef.current) / 1000)
      const body = JSON.stringify({ scroll_pct: scrollPctRef.current, elapsed_seconds: elapsed })
      const beaconUrl = (import.meta.env.VITE_API_BASE_URL || 'https://advisorflow-backend.onrender.com') + `/proposals/portal/view/${viewIdRef.current}/close`
      navigator.sendBeacon(beaconUrl, new Blob([body], { type: 'application/json' }))
    }
    window.addEventListener('beforeunload', handleUnload)
    return () => window.removeEventListener('beforeunload', handleUnload)
  }, [])

  async function handleDownload() {
    if (!viewIdRef.current) return
    canDownload.current = false
    try {
      await api.post(`/proposals/portal/view/${viewIdRef.current}/download`)
    } catch {}
  }

  // ── Error state ─────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div style={{
        minHeight: '100vh', background: '#040812',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: '"Inter", system-ui, sans-serif',
      }}>
        <div style={{ textAlign: 'center', maxWidth: 400, padding: 32 }}>
          <div style={{ fontSize: 48, marginBottom: 20 }}>🔒</div>
          <h2 style={{ color: '#fff', fontSize: 20, fontWeight: 700, margin: '0 0 12px' }}>Session Expired</h2>
          <p style={{ color: '#556', fontSize: 14, lineHeight: 1.6 }}>{error}</p>
        </div>
      </div>
    )
  }

  if (!proposal) {
    return (
      <div style={{
        minHeight: '100vh', background: '#040812',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          width: 40, height: 40, borderRadius: 10,
          background: 'linear-gradient(135deg, #087cff, #0a56b0)',
          animation: 'pulse 1.8s ease-in-out infinite',
          boxShadow: '0 0 24px rgba(8,124,255,0.3)',
        }} />
        <style>{`@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.6;transform:scale(0.93)} }`}</style>
      </div>
    )
  }

  // ── The proposal ────────────────────────────────────────────────────────────
  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      style={{
        minHeight: '100vh',
        height: '100vh',
        overflowY: 'auto',
        background: '#040812',
        fontFamily: '"Inter", system-ui, sans-serif',
        opacity: revealed ? 1 : 0,
        transform: revealed ? 'translateY(0)' : 'translateY(16px)',
        transition: 'opacity 0.6s ease, transform 0.6s ease',
        scrollBehavior: 'smooth',
      }}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Playfair+Display:wght@700&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(8,124,255,0.25); border-radius: 4px; }
        @keyframes fadeInUp { from { opacity:0; transform:translateY(24px); } to { opacity:1; transform:translateY(0); } }
        .portal-block { animation: fadeInUp 0.5s ease both; }
      `}</style>

      {/* Top bar */}
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
        background: 'rgba(4,8,18,0.85)',
        backdropFilter: 'blur(12px)',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
        padding: '0 32px',
        height: 56,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 7,
            background: 'linear-gradient(135deg, #087cff, #0a56b0)',
            boxShadow: '0 0 12px rgba(8,124,255,0.4)',
          }} />
          <span style={{ fontSize: 14, fontWeight: 700, color: '#fff', letterSpacing: '-0.01em' }}>
            EvoSys Pro
          </span>
        </div>
        <div style={{ fontSize: 12, color: '#445' }}>
          Secure Proposal Portal
        </div>
      </div>

      {/* Cover / Hero */}
      <div style={{
        minHeight: '55vh',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        textAlign: 'center', padding: '100px 32px 64px',
        position: 'relative', overflow: 'hidden',
      }}>
        {/* Background glow */}
        <div style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: 'radial-gradient(ellipse 800px 400px at 50% 30%, rgba(8,124,255,0.08) 0%, transparent 70%)',
        }} />
        {/* Grid texture */}
        <div style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          backgroundImage: 'linear-gradient(rgba(8,124,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(8,124,255,0.04) 1px, transparent 1px)',
          backgroundSize: '48px 48px',
          maskImage: 'radial-gradient(ellipse 80% 60% at 50% 40%, black, transparent)',
          WebkitMaskImage: 'radial-gradient(ellipse 80% 60% at 50% 40%, black, transparent)',
        }} />

        <div style={{ position: 'relative', zIndex: 1 }}>
          {proposal.client_name && (
            <div style={{
              fontSize: 13, color: '#087cff', fontWeight: 600,
              letterSpacing: '0.12em', textTransform: 'uppercase',
              marginBottom: 20,
              animation: 'fadeInUp 0.5s 0.1s ease both',
            }}>
              Prepared for {proposal.client_name}
              {proposal.client_company ? ` · ${proposal.client_company}` : ''}
            </div>
          )}
          <h1 style={{
            fontSize: 'clamp(28px, 5vw, 52px)',
            fontWeight: 700,
            fontFamily: '"Playfair Display", Georgia, serif',
            color: '#fff',
            lineHeight: 1.15,
            margin: '0 0 20px',
            maxWidth: 740,
            animation: 'fadeInUp 0.5s 0.2s ease both',
          }}>
            {proposal.title}
          </h1>
          {proposal.subtitle && (
            <p style={{
              fontSize: 18, color: '#7a92b4', lineHeight: 1.6,
              margin: 0, maxWidth: 560,
              animation: 'fadeInUp 0.5s 0.3s ease both',
            }}>
              {proposal.subtitle}
            </p>
          )}

          {/* Scroll cue */}
          <div style={{
            marginTop: 48, color: '#2a4060',
            animation: 'fadeInUp 0.5s 0.6s ease both',
          }}>
            <div style={{ fontSize: 12, letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 8 }}>Scroll to review</div>
            <div style={{ fontSize: 20, animation: 'bounce 2s ease-in-out infinite' }}>↓</div>
            <style>{`@keyframes bounce { 0%,100%{transform:translateY(0)} 50%{transform:translateY(6px)} }`}</style>
          </div>
        </div>
      </div>

      {/* Content blocks */}
      <div style={{
        maxWidth: 760, margin: '0 auto', padding: '0 32px 120px',
      }}>
        {proposal.blocks.map((block, idx) => (
          <div
            key={block.id}
            className="portal-block"
            style={{ animationDelay: `${0.15 * idx}s`, marginBottom: block.block_type === 'divider' ? 40 : 56 }}
          >
            {block.block_type === 'text'    && <TextBlock block={block} />}
            {block.block_type === 'image'   && <ImageBlock block={block} onDownload={handleDownload} canDownload={canDownload.current} />}
            {block.block_type === 'pdf'     && <PdfBlock block={block} onDownload={handleDownload} canDownload={canDownload.current} />}
            {block.block_type === 'video'   && <VideoBlock block={block} />}
            {block.block_type === 'divider' && <DividerBlock />}
            {block.block_type === 'cta'     && <CtaBlock block={block} />}
          </div>
        ))}

        {proposal.blocks.length === 0 && (
          <div style={{ textAlign: 'center', color: '#334', padding: '80px 0', fontSize: 14 }}>
            Content coming soon.
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{
        borderTop: '1px solid rgba(255,255,255,0.05)',
        padding: '28px 32px',
        textAlign: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginBottom: 8 }}>
          <div style={{
            width: 20, height: 20, borderRadius: 5,
            background: 'linear-gradient(135deg, #087cff, #0a56b0)',
          }} />
          <span style={{ fontSize: 13, fontWeight: 700, color: '#334' }}>EvoSys Pro</span>
        </div>
        <p style={{ fontSize: 12, color: '#2a3a50', margin: 0 }}>
          This proposal is private and intended only for its recipient.
          For questions, contact your advisor directly.
        </p>
      </div>
    </div>
  )
}
