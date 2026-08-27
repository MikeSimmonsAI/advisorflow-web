/**
 * PortalViewer — the client-facing proposal document
 *
 * URL: /portal/view/:proposalId
 * Auth: sessionStorage portal_view_id + portal_proposal (set by PortalAccess)
 *
 * Design goals:
 *  - Reads like a printed proposal: white sheet, navy headings, blue keylines.
 *    A prospect is being asked for money; the page should look like a document
 *    from a firm, not like a product tour.
 *  - Nothing that looks like the internal app.
 *  - Scroll depth + time tracked via heartbeat pings every 15s
 *  - Final stats sent on beforeunload via sendBeacon
 *  - PDF/image blocks embedded inline, video blocks embedded via iframe
 *  - Download button per file block, marks download event on backend
 *
 * The tracking, permission and content-protection behaviour is unchanged from
 * the previous version of this file. Only the presentation was rebuilt.
 *
 * This page is deliberately single-theme. It is a document, and a document has
 * a paper colour; every colour below is stated explicitly so it never inherits
 * anything from the host.
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api/client'

// ── URL resolver: ensures /proposals/files/{id} hits the backend, not the SPA ─
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'https://advisorflow-backend.onrender.com'
function resolveFileUrl(url) {
  if (!url) return url
  if (url.startsWith('/')) return API_BASE + url
  return url
}

const C = {
  navy: '#0d2440',
  navy2: '#173b64',
  head: '#12304f',
  ink: '#16283c',
  body: '#3a4756',
  muted: '#6d7c8d',
  faint: '#93a1b1',
  blue: '#2f76c7',
  blueLt: '#8ab8ea',
  callBg: '#e9f1fb',
  callBar: '#1f4e8c',
  green: '#17794a',
  paper: '#ffffff',
  page: '#eef1f6',
  line: '#dfe5ec',
  zebra: '#f6f9fc',
}

const RULE = 'linear-gradient(90deg, #1b3f66 0%, #4a8ed4 55%, #9cc6ee 100%)'

// ── Inline formatting ────────────────────────────────────────────────────────
// Returns React nodes, never HTML. Proposal prose is typed by a salesperson,
// and rendering it as HTML would make every proposal an XSS vector aimed at
// the customer.

const URL_RE = /(https?:\/\/[^\s<>()]+[^\s<>().,;:!?])/g

function linkify(text, keyBase) {
  const out = []
  let last = 0
  let m
  URL_RE.lastIndex = 0
  while ((m = URL_RE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    out.push(
      <a key={keyBase + '-u' + m.index} href={m[0]} target="_blank" rel="noopener noreferrer"
         style={{ color: C.blue, textDecoration: 'underline', wordBreak: 'break-word' }}>
        {m[0]}
      </a>
    )
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

// Highlights that make the commercial sections readable at a glance: the word
// OPTIONAL becomes a badge, a stated saving becomes green. Both are only ever
// applied to words the author actually wrote.
function decorate(text, keyBase) {
  const parts = []
  const re = /(\bOPTIONAL\b|Save \$[\d,]+(?:\.\d\d)?(?:\s*(?:\/month|every month|per month|\/mo))?)/g
  let last = 0
  let m
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(...linkify(text.slice(last, m.index), keyBase + '-t' + last))
    if (m[0] === 'OPTIONAL') {
      parts.push(
        <span key={keyBase + '-o' + m.index} style={{
          display: 'inline-block', verticalAlign: 'baseline',
          background: C.callBg, color: C.callBar,
          border: '1px solid #c3d9f2', borderRadius: 4,
          fontSize: 10.5, fontWeight: 700, letterSpacing: '.10em',
          padding: '2px 7px', margin: '0 2px',
        }}>OPTIONAL</span>
      )
    } else {
      parts.push(
        <strong key={keyBase + '-s' + m.index} style={{ color: C.green, fontWeight: 600 }}>{m[0]}</strong>
      )
    }
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(...linkify(text.slice(last), keyBase + '-t' + last))
  return parts
}

function inline(text, keyBase) {
  // **bold** and *italic*, then decorate/linkify whatever is left.
  const nodes = []
  const re = /\*\*(.+?)\*\*|\*(.+?)\*/g
  let last = 0
  let m
  let i = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(...decorate(text.slice(last, m.index), keyBase + '-p' + (i++)))
    if (m[1] !== undefined) {
      nodes.push(<strong key={keyBase + '-b' + m.index} style={{ color: C.ink, fontWeight: 600 }}>
        {decorate(m[1], keyBase + '-bi' + m.index)}
      </strong>)
    } else {
      nodes.push(<em key={keyBase + '-i' + m.index}>{decorate(m[2], keyBase + '-ii' + m.index)}</em>)
    }
    last = m.index + m[0].length
  }
  if (last < text.length) nodes.push(...decorate(text.slice(last), keyBase + '-p' + (i++)))
  return nodes
}

// ── Block-level parser ───────────────────────────────────────────────────────
// Headings, bullets, numbered lists, pipe tables, > callouts, paragraphs.

function isTableRow(line) {
  const t = line.trim()
  return t.startsWith('|') && t.endsWith('|') && t.length > 2
}
function isTableDivider(line) {
  return /^\|[\s:|-]+\|$/.test(line.trim())
}
function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim())
}

function parseBlocks(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const t = line.trim()

    if (!t) { i++; continue }

    if (/^###\s+/.test(t)) { out.push({ k: 'h3', text: t.replace(/^###\s+/, '') }); i++; continue }
    if (/^##\s+/.test(t))  { out.push({ k: 'h2', text: t.replace(/^##\s+/, '') });  i++; continue }
    if (/^#\s+/.test(t))   { out.push({ k: 'h2', text: t.replace(/^#\s+/, '') });   i++; continue }
    if (/^(-{3,}|_{3,}|\*{3,})$/.test(t)) { out.push({ k: 'hr' }); i++; continue }

    if (isTableRow(t)) {
      const rows = []
      while (i < lines.length && isTableRow(lines[i])) {
        if (!isTableDivider(lines[i])) rows.push(splitRow(lines[i]))
        i++
      }
      if (rows.length) out.push({ k: 'table', head: rows[0], rows: rows.slice(1) })
      continue
    }

    if (t.startsWith('>')) {
      const buf = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      out.push({ k: 'callout', text: buf.join(' ').trim() })
      continue
    }

    if (/^([-*•]|•)\s+/.test(t)) {
      const items = []
      while (i < lines.length && /^([-*•]|•)\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^([-*•]|•)\s+/, ''))
        i++
      }
      out.push({ k: 'ul', items })
      continue
    }

    if (/^\d+[.)]\s+/.test(t)) {
      const items = []
      while (i < lines.length && /^\d+[.)]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+[.)]\s+/, ''))
        i++
      }
      out.push({ k: 'ol', items })
      continue
    }

    // Paragraph: consume until a blank line or the start of another construct.
    const buf = []
    while (i < lines.length) {
      const l = lines[i]
      const lt = l.trim()
      if (!lt) break
      if (/^#{1,3}\s+/.test(lt) || lt.startsWith('>') || isTableRow(lt) ||
          /^([-*•]|•)\s+/.test(lt) || /^\d+[.)]\s+/.test(lt)) break
      buf.push(lt)
      i++
    }
    if (buf.length) out.push({ k: 'p', text: buf.join(' ') })
  }
  return out
}

// ── Document primitives ──────────────────────────────────────────────────────

function SectionRule() {
  return <div style={{ height: 3, background: RULE, borderRadius: 2, margin: '10px 0 22px' }} />
}

function H2({ children }) {
  return (
    <>
      <h2 style={{
        fontFamily: 'Archivo, "Helvetica Neue", Arial, sans-serif',
        fontSize: 'clamp(20px, 2.4vw, 25px)', fontWeight: 700, color: C.head,
        letterSpacing: '-.01em', lineHeight: 1.2, margin: '38px 0 0',
        textWrap: 'balance',
      }}>{children}</h2>
      <SectionRule />
    </>
  )
}

function Callout({ children }) {
  return (
    <div style={{
      background: C.callBg, borderLeft: '4px solid ' + C.callBar,
      padding: '16px 20px', margin: '18px 0', color: C.ink,
      fontSize: 16, lineHeight: 1.65,
    }}>{children}</div>
  )
}

function Table({ head, rows }) {
  return (
    <div style={{ overflowX: 'auto', margin: '18px 0', border: '1px solid ' + C.line }}>
      <table style={{
        width: '100%', minWidth: 480, borderCollapse: 'collapse',
        fontSize: 14.5, fontVariantNumeric: 'tabular-nums',
      }}>
        <thead>
          <tr>
            {head.map((c, n) => (
              <th key={n} style={{
                background: C.head, color: '#fff', textAlign: 'left',
                fontWeight: 600, padding: '11px 14px',
                fontSize: 13, letterSpacing: '.01em',
                borderRight: n < head.length - 1 ? '1px solid rgba(255,255,255,.14)' : 'none',
              }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, n) => (
            <tr key={n} style={{ background: n % 2 ? C.zebra : '#fff' }}>
              {r.map((c, m) => (
                <td key={m} style={{
                  padding: '12px 14px', color: C.body, verticalAlign: 'top',
                  borderTop: '1px solid ' + C.line, lineHeight: 1.55,
                }}>{inline(c, 'tc' + n + '-' + m)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// A headline commercial figure: "**Total contract value: $7,997** — $1,497
// implementation plus $6,500 platform." Split into label / number / note so the
// number carries the weight it carries in the conversation.
const FIGURE_RE = /^\*\*\s*([^:*]+?)\s*:\s*([^*]+?)\s*\*\*\s*(.*)$/

function Figure({ text }) {
  const m = text.match(FIGURE_RE)
  const label = m ? m[1] : 'Total'
  const value = m ? m[2] : text.replace(/\*\*/g, '')
  const note = m ? (m[3] || '').replace(/^[\s—–-]+/, '').trim() : ''
  return (
    <div style={{
      border: '1px solid ' + C.line, borderTop: '3px solid ' + C.head,
      background: '#fbfcfe', padding: '20px 22px', height: '100%',
    }}>
      <div style={{
        fontSize: 11.5, fontWeight: 700, letterSpacing: '.13em',
        textTransform: 'uppercase', color: C.muted, marginBottom: 8,
      }}>{label}</div>
      <div style={{
        fontFamily: 'Archivo, "Helvetica Neue", Arial, sans-serif',
        fontSize: 'clamp(26px, 3.4vw, 34px)', fontWeight: 700, color: C.head,
        letterSpacing: '-.02em', fontVariantNumeric: 'tabular-nums', lineHeight: 1.1,
      }}>{value}</div>
      {note && (
        <div style={{ fontSize: 14, color: C.body, marginTop: 8, lineHeight: 1.5 }}>{note}</div>
      )}
    </div>
  )
}

function Prose({ nodes, keyBase }) {
  // Consecutive figures sit side by side, the way a summary panel reads.
  const grouped = []
  for (let n = 0; n < nodes.length; n++) {
    if (nodes[n].k === 'figure') {
      const run = []
      while (n < nodes.length && nodes[n].k === 'figure') { run.push(nodes[n]); n++ }
      n--
      grouped.push({ k: 'figures', run })
    } else {
      grouped.push(nodes[n])
    }
  }
  return grouped.map((b, n) => {
    const k = keyBase + '-' + n
    if (b.k === 'figures') return (
      <div key={k} style={{
        display: 'grid', gap: 16, margin: '18px 0',
        gridTemplateColumns: b.run.length > 1 ? 'repeat(auto-fit, minmax(240px, 1fr))' : '1fr',
      }}>
        {b.run.map((f, m) => <Figure key={m} text={f.text} />)}
      </div>
    )
    if (b.k === 'h2') return <H2 key={k}>{b.text}</H2>
    if (b.k === 'h3') return (
      <h3 key={k} style={{
        fontFamily: 'Archivo, "Helvetica Neue", Arial, sans-serif',
        fontSize: 17, fontWeight: 600, color: C.head, margin: '26px 0 8px',
      }}>{inline(b.text, k)}</h3>
    )
    if (b.k === 'hr') return <hr key={k} style={{ border: 0, borderTop: '1px solid ' + C.line, margin: '28px 0' }} />
    if (b.k === 'callout') return <Callout key={k}>{inline(b.text, k)}</Callout>
    if (b.k === 'table') return <Table key={k} head={b.head} rows={b.rows} />
    if (b.k === 'ul') return (
      <ul key={k} style={{ margin: '12px 0 16px', padding: 0, listStyle: 'none' }}>
        {b.items.map((it, m) => (
          <li key={m} style={{
            position: 'relative', paddingLeft: 22, margin: '0 0 9px',
            fontSize: 16.5, lineHeight: 1.7, color: C.body,
          }}>
            <span style={{
              position: 'absolute', left: 2, top: '.62em', width: 6, height: 6,
              background: C.blue, borderRadius: 1, transform: 'rotate(45deg)',
            }} />
            {inline(it, k + '-' + m)}
          </li>
        ))}
      </ul>
    )
    if (b.k === 'ol') return (
      <ol key={k} style={{ margin: '12px 0 16px', paddingLeft: 22 }}>
        {b.items.map((it, m) => (
          <li key={m} style={{ fontSize: 16.5, lineHeight: 1.7, color: C.body, margin: '0 0 9px' }}>
            {inline(it, k + '-' + m)}
          </li>
        ))}
      </ol>
    )
    return (
      <p key={k} style={{ fontSize: 16.5, lineHeight: 1.75, color: C.body, margin: '0 0 14px' }}>
        {inline(b.text, k)}
      </p>
    )
  })
}

// ── Block renderers ──────────────────────────────────────────────────────────

function TextBlock({ block }) {
  const nodes = parseBlocks(block.content)
  const first = nodes[0]
  const heading = first && first.k === 'h2' ? first.text : null
  const rest = heading ? nodes.slice(1) : nodes

  // In the commercial section, a paragraph written as "**Label: value** — note"
  // is a headline figure rather than prose. Promote those and leave the rest of
  // the section — the options table, the named selection — exactly as written.
  if (heading && /^investment$/i.test(heading.trim())) {
    const promoted = rest.map(b =>
      (b.k === 'p' && FIGURE_RE.test(b.text)) ? { ...b, k: 'figure' } : b)
    return (
      <section>
        <H2>{heading}</H2>
        <Prose nodes={promoted} keyBase={'inv-' + block.id} />
      </section>
    )
  }

  // A "next steps" section is the one place the document should raise its
  // voice, so it gets the dark card.
  if (heading && /next step/i.test(heading)) {
    return (
      <section>
        <H2>{heading}</H2>
        <div style={{
          background: 'linear-gradient(135deg, ' + C.navy + ' 0%, #16375c 100%)',
          color: '#fff', padding: '26px 28px', margin: '4px 0 8px',
        }}>
          <div className="pv-oncard">
            <Prose nodes={rest} keyBase={'ns-' + block.id} />
          </div>
        </div>
      </section>
    )
  }

  return (
    <section>
      {heading && <H2>{heading}</H2>}
      <Prose nodes={rest} keyBase={'tb-' + block.id} />
    </section>
  )
}

function FileChrome({ title, meta, actions, children }) {
  return (
    <div style={{ border: '1px solid ' + C.line, background: '#fff', margin: '22px 0' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14,
        padding: '14px 18px', background: '#f8fafc',
        borderBottom: '1px solid ' + C.line, flexWrap: 'wrap',
      }}>
        <div style={{ flex: 1, minWidth: 180 }}>
          <div style={{ fontSize: 14.5, fontWeight: 600, color: C.ink }}>{title}</div>
          {meta && <div style={{ fontSize: 12.5, color: C.muted, marginTop: 2 }}>{meta}</div>}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>{actions}</div>
      </div>
      {children}
    </div>
  )
}

const BTN = {
  background: '#fff', border: '1px solid ' + C.line, borderRadius: 6,
  padding: '7px 14px', color: C.head, fontSize: 12.5, fontWeight: 600,
  cursor: 'pointer', font: 'inherit', fontFamily: 'inherit',
  textDecoration: 'none', display: 'inline-flex', alignItems: 'center',
}
const BTN_PRIMARY = { ...BTN, background: C.head, borderColor: C.head, color: '#fff' }

function ImageBlock({ block, onDownload, canDownload, protected: isProtected }) {
  const [loaded, setLoaded] = useState(false)
  if (!block.file_url) return null
  const src = resolveFileUrl(block.file_url)
  return (
    <div style={{ margin: '22px 0' }}>
      {block.content && (
        <p style={{ color: C.muted, fontSize: 13.5, margin: '0 0 10px' }}>{block.content}</p>
      )}
      <div style={{
        border: '1px solid ' + C.line, background: '#f8fafc',
        opacity: loaded ? 1 : 0, transition: 'opacity .4s ease', position: 'relative',
      }}>
        <img
          src={src}
          alt={block.content || 'Image'}
          onLoad={() => setLoaded(true)}
          draggable={false}
          style={{ width: '100%', display: 'block', maxHeight: 600, objectFit: 'contain' }}
        />
        {isProtected && (
          <div style={{ position: 'absolute', inset: 0, background: 'transparent', zIndex: 1 }}
               onContextMenu={e => e.preventDefault()} />
        )}
      </div>
      {canDownload && block.file_url && (
        <button onClick={() => { window.open(src, '_blank'); onDownload() }}
                style={{ ...BTN, marginTop: 10 }}>
          ↓ {block.file_name || 'Download image'}
          {block.file_size ? ` (${(block.file_size / 1024).toFixed(0)} KB)` : ''}
        </button>
      )}
    </div>
  )
}

function PdfBlock({ block, onDownload, canDownload }) {
  const [expanded, setExpanded] = useState(false)
  if (!block.file_url) return null
  const src = resolveFileUrl(block.file_url)
  return (
    <FileChrome
      title={block.content || block.file_name || 'Document'}
      meta={block.file_size ? `${block.file_name} · ${(block.file_size / 1024).toFixed(0)} KB` : block.file_name}
      actions={
        <>
          <button style={BTN} onClick={() => setExpanded(e => !e)}>
            {expanded ? 'Collapse' : 'Preview'}
          </button>
          {canDownload && (
            <button style={BTN_PRIMARY} onClick={() => { window.open(src, '_blank'); onDownload() }}>
              ↓ Download
            </button>
          )}
        </>
      }
    >
      {expanded && (
        <iframe src={`${src}#toolbar=0&navpanes=0`} title={block.content || 'Document'}
                style={{ width: '100%', height: 600, border: 'none', display: 'block' }} />
      )}
    </FileChrome>
  )
}

function VideoBlock({ block }) {
  if (!block.file_url) return null
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
    <div style={{ margin: '22px 0' }}>
      {block.content && (
        <p style={{ color: C.muted, fontSize: 13.5, margin: '0 0 10px' }}>{block.content}</p>
      )}
      <div style={{ border: '1px solid ' + C.line, background: '#000', aspectRatio: '16/9' }}>
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
  return <div style={{ height: 2, background: RULE, opacity: .5, margin: '34px 0', borderRadius: 2 }} />
}

function WebsiteUrlBlock({ block }) {
  const [expanded, setExpanded] = useState(true)
  const [loadError, setLoadError] = useState(false)
  if (!block.file_url) return null
  const url = block.file_url.startsWith('http') ? block.file_url : `https://${block.file_url}`
  return (
    <FileChrome
      title={block.content || 'Website'}
      meta={url}
      actions={
        <>
          <button style={BTN} onClick={() => setExpanded(e => !e)}>
            {expanded ? 'Collapse' : 'Expand'}
          </button>
          <a style={BTN_PRIMARY} href={url} target="_blank" rel="noopener noreferrer">Open ↗</a>
        </>
      }
    >
      {expanded && (
        loadError ? (
          <div style={{
            height: 220, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 12,
            color: C.muted, fontSize: 14,
          }}>
            <div>This site doesn’t allow embedding.</div>
            <a style={BTN} href={url} target="_blank" rel="noopener noreferrer">Open in a new tab ↗</a>
          </div>
        ) : (
          <iframe
            src={url}
            title={block.content || 'Site preview'}
            style={{ width: '100%', height: 560, border: 'none', display: 'block' }}
            onError={() => setLoadError(true)}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          />
        )
      )}
    </FileChrome>
  )
}

function CtaBlock({ block }) {
  if (!block.content) return null
  return (
    <div style={{ margin: '26px 0' }}>
      <a
        href={block.file_url || '#'}
        target={block.file_url ? '_blank' : '_self'}
        rel="noopener noreferrer"
        style={{
          display: 'inline-block', background: C.head, color: '#fff',
          textDecoration: 'none', padding: '14px 30px',
          fontSize: 15, fontWeight: 600, letterSpacing: '.01em',
        }}
      >
        {block.content}
      </a>
    </div>
  )
}

// ── Cover ────────────────────────────────────────────────────────────────────

function MetaCell({ label, children }) {
  if (!children) return null
  return (
    <div style={{ borderTop: '1px solid rgba(255,255,255,.28)', paddingTop: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: '#fff', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 14.5, color: '#c9dcf0', lineHeight: 1.55 }}>{children}</div>
    </div>
  )
}

function Wordmark({ light }) {
  return (
    <span style={{
      fontFamily: 'Archivo, "Helvetica Neue", Arial, sans-serif',
      fontWeight: 700, letterSpacing: '-.02em',
      color: light ? '#fff' : C.head,
    }}>
      EvoSys <span style={{ color: light ? C.blueLt : C.blue }}>Pro</span>
    </span>
  )
}

function fmtDate(iso) {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  if (isNaN(d.getTime())) return null
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}

// ── Main component ───────────────────────────────────────────────────────────

export default function PortalViewer() {
  const { proposalId } = useParams()
  const [proposal, setProposal] = useState(null)
  const [branding, setBranding] = useState({})
  const [error, setError] = useState(null)
  const [revealed, setRevealed] = useState(false)
  const [progress, setProgress] = useState(0)
  const viewIdRef = useRef(null)
  const startRef = useRef(Date.now())
  const scrollPctRef = useRef(0)
  const pingRef = useRef(null)
  const containerRef = useRef(null)
  const canDownload = useRef(true)
  const protectContent = useRef(false)

  // Load from sessionStorage (set by PortalAccess)
  useEffect(() => {
    const raw = sessionStorage.getItem('portal_proposal')
    const viewId = sessionStorage.getItem('portal_view_id')
    const perms = JSON.parse(sessionStorage.getItem('portal_permissions') || '{}')
    let brand = {}
    try { brand = JSON.parse(sessionStorage.getItem('portal_branding') || '{}') || {} } catch { brand = {} }

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
      setBranding(brand)
      viewIdRef.current = viewId
      canDownload.current = perms.can_download !== false
      protectContent.current = !!perms.protect_content
      setTimeout(() => setRevealed(true), 80)
    } catch {
      setError('Failed to load proposal. Please use the link from your email.')
    }
  }, [proposalId])

  // Scroll tracking — unchanged semantics, plus a reading-progress bar.
  const handleScroll = useCallback(() => {
    if (!containerRef.current) return
    const el = containerRef.current
    const scrolled = el.scrollTop + el.clientHeight
    const total = el.scrollHeight
    const pct = Math.round((scrolled / total) * 100)
    if (pct > scrollPctRef.current) scrollPctRef.current = pct
    const range = total - el.clientHeight
    setProgress(range > 0 ? Math.min(100, Math.max(0, (el.scrollTop / range) * 100)) : 0)
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

  // Content protection
  useEffect(() => {
    if (!proposal || !protectContent.current) return
    const blockContext = e => e.preventDefault()
    document.addEventListener('contextmenu', blockContext)
    const blockDrag = e => e.preventDefault()
    document.addEventListener('dragstart', blockDrag)
    const blockKeys = e => {
      if (e.ctrlKey || e.metaKey) {
        if (['s', 'p', 'u', 'a'].includes(e.key.toLowerCase())) {
          e.preventDefault()
          return false
        }
      }
    }
    document.addEventListener('keydown', blockKeys)
    document.body.style.userSelect = 'none'
    document.body.style.webkitUserSelect = 'none'
    return () => {
      document.removeEventListener('contextmenu', blockContext)
      document.removeEventListener('dragstart', blockDrag)
      document.removeEventListener('keydown', blockKeys)
      document.body.style.userSelect = ''
      document.body.style.webkitUserSelect = ''
    }
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

  const FONTS = (
    <style>{`
      @import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=Source+Sans+3:wght@300;400;500;600&display=swap');
      .pv-root, .pv-root * { box-sizing: border-box; }
      .pv-root ::-webkit-scrollbar { width: 10px; }
      .pv-root ::-webkit-scrollbar-track { background: ${C.page}; }
      .pv-root ::-webkit-scrollbar-thumb { background: #c4cfdb; border-radius: 6px; }
      @keyframes pvUp { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:translateY(0); } }
      .pv-block { animation: pvUp .45s ease both; }
      .pv-oncard p, .pv-oncard li { color: #d8e6f5 !important; }
      .pv-oncard h2, .pv-oncard h3, .pv-oncard strong { color: #fff !important; }
      @media (prefers-reduced-motion: reduce) {
        .pv-block { animation: none !important; }
        .pv-root { scroll-behavior: auto !important; }
      }
      @media print {
        .pv-chrome { display: none !important; }
        .pv-sheet { box-shadow: none !important; border: 0 !important; }
      }
    `}</style>
  )

  // ── Error state ─────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="pv-root" style={{
        minHeight: '100vh', background: C.page,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: '"Source Sans 3", system-ui, Arial, sans-serif', padding: 24,
      }}>
        {FONTS}
        <div style={{
          textAlign: 'center', maxWidth: 460, padding: '40px 32px',
          background: '#fff', border: '1px solid ' + C.line,
          borderTop: '4px solid ' + C.head,
        }}>
          <div style={{ marginBottom: 14, fontSize: 15 }}><Wordmark /></div>
          <h2 style={{
            fontFamily: 'Archivo, Arial, sans-serif', color: C.head,
            fontSize: 20, fontWeight: 700, margin: '0 0 10px',
          }}>This link is no longer active</h2>
          <p style={{ color: C.body, fontSize: 15, lineHeight: 1.6, margin: 0 }}>{error}</p>
        </div>
      </div>
    )
  }

  if (!proposal) {
    return (
      <div className="pv-root" style={{
        minHeight: '100vh', background: C.page,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: '"Source Sans 3", system-ui, Arial, sans-serif',
      }}>
        {FONTS}
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 17, marginBottom: 14 }}><Wordmark /></div>
          <div style={{ width: 160, height: 3, background: '#dbe3ec', overflow: 'hidden' }}>
            <div style={{
              width: '40%', height: '100%', background: RULE,
              animation: 'pvSlide 1.2s ease-in-out infinite',
            }} />
          </div>
          <style>{`@keyframes pvSlide { 0%{transform:translateX(-100%)} 100%{transform:translateX(300%)} }`}</style>
        </div>
      </div>
    )
  }

  const brandName = branding.name || 'EvoSys Pro'
  const brandPhone = branding.support_phone || branding.phone || null
  const brandEmail = branding.support_email || branding.email || null
  const brandSite = branding.website || null
  const preparedDate = fmtDate(proposal.created_at) || fmtDate(proposal.updated_at)
  const docRef = proposal.proposal_number
    ? proposal.proposal_number + (proposal.version > 1 ? ' · v' + proposal.version : '')
    : null

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="pv-root"
      style={{
        minHeight: '100vh', height: '100vh', overflowY: 'auto',
        background: C.page, color: C.body,
        fontFamily: '"Source Sans 3", system-ui, -apple-system, Arial, sans-serif',
        opacity: revealed ? 1 : 0,
        transition: 'opacity .5s ease',
        scrollBehavior: 'smooth',
      }}
    >
      {FONTS}

      {/* Top bar */}
      <div className="pv-chrome" style={{
        position: 'sticky', top: 0, zIndex: 100,
        background: 'rgba(255,255,255,.94)',
        backdropFilter: 'blur(10px)',
        borderBottom: '1px solid ' + C.line,
        padding: '0 clamp(16px, 4vw, 40px)',
        height: 54, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: 15 }}><Wordmark /></span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {protectContent.current && (
            <span style={{
              fontSize: 11, color: C.callBar, background: C.callBg,
              border: '1px solid #c3d9f2', borderRadius: 4,
              padding: '3px 9px', letterSpacing: '.06em', fontWeight: 600,
            }}>PROTECTED</span>
          )}
          <span style={{ fontSize: 12, color: C.muted, letterSpacing: '.06em' }}>
            CONFIDENTIAL PROPOSAL
          </span>
        </div>
        <div style={{
          position: 'absolute', left: 0, bottom: -1, height: 2,
          width: progress + '%', background: RULE, transition: 'width .12s linear',
        }} />
      </div>

      {/* Cover */}
      <div style={{
        background: 'linear-gradient(135deg, ' + C.navy + ' 0%, ' + C.navy2 + ' 100%)',
        padding: 'clamp(48px, 8vw, 88px) clamp(20px, 5vw, 56px) clamp(40px, 6vw, 64px)',
      }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div style={{ fontSize: 22, marginBottom: 'clamp(32px, 6vw, 64px)' }}><Wordmark light /></div>

          <div style={{
            fontSize: 12.5, fontWeight: 700, letterSpacing: '.16em',
            textTransform: 'uppercase', color: C.blueLt, marginBottom: 14,
          }}>
            Proposal{docRef ? ' · ' + docRef : ''}
          </div>

          <h1 style={{
            fontFamily: 'Archivo, "Helvetica Neue", Arial, sans-serif',
            fontSize: 'clamp(30px, 5.2vw, 50px)', fontWeight: 700, color: '#fff',
            lineHeight: 1.12, letterSpacing: '-.025em', margin: '0 0 18px',
            maxWidth: 18 + 'em', textWrap: 'balance',
          }}>
            {proposal.title}
          </h1>

          {proposal.subtitle && (
            <p style={{
              fontSize: 'clamp(16px, 1.8vw, 18.5px)', color: '#c9dcf0',
              lineHeight: 1.6, margin: 0, maxWidth: '38em',
            }}>
              {proposal.subtitle}
            </p>
          )}

          <div style={{
            display: 'grid', gap: 'clamp(18px, 3vw, 32px)',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            marginTop: 'clamp(36px, 6vw, 60px)',
          }}>
            <MetaCell label="Prepared for">
              {proposal.client_name}
              {proposal.client_company && <><br />{proposal.client_company}</>}
            </MetaCell>
            <MetaCell label="Prepared by">{brandName}</MetaCell>
            {preparedDate && <MetaCell label="Date">{preparedDate}</MetaCell>}
            {(brandPhone || brandEmail || brandSite) && (
              <MetaCell label="Contact">
                {brandPhone && <>{brandPhone}<br /></>}
                {brandEmail && <>{brandEmail}<br /></>}
                {brandSite}
              </MetaCell>
            )}
          </div>
        </div>
      </div>

      {/* Document sheet */}
      <div style={{ padding: 'clamp(24px, 4vw, 44px) clamp(12px, 4vw, 40px) 64px' }}>
        <div className="pv-sheet" style={{
          maxWidth: 900, margin: '0 auto', background: C.paper,
          border: '1px solid ' + C.line,
          boxShadow: '0 12px 40px rgba(16,36,60,.08)',
          padding: 'clamp(28px, 5vw, 60px) clamp(20px, 5vw, 64px) clamp(36px, 5vw, 56px)',
        }}>
          {proposal.blocks.map((block, idx) => (
            <div key={block.id} className="pv-block" style={{ animationDelay: `${Math.min(0.08 * idx, 0.5)}s` }}>
              {block.block_type === 'text'        && <TextBlock block={block} />}
              {block.block_type === 'image'       && <ImageBlock block={block} onDownload={handleDownload} canDownload={canDownload.current} protected={protectContent.current} />}
              {block.block_type === 'pdf'         && <PdfBlock block={block} onDownload={handleDownload} canDownload={canDownload.current} />}
              {block.block_type === 'video'       && <VideoBlock block={block} />}
              {block.block_type === 'divider'     && <DividerBlock />}
              {block.block_type === 'cta'         && <CtaBlock block={block} />}
              {block.block_type === 'website_url' && <WebsiteUrlBlock block={block} />}
            </div>
          ))}

          {proposal.blocks.length === 0 && (
            <div style={{ textAlign: 'center', color: C.muted, padding: '80px 0', fontSize: 15 }}>
              Content coming soon.
            </div>
          )}

          {/* Signature */}
          <div style={{ marginTop: 'clamp(40px, 6vw, 64px)' }}>
            <H2>Prepared by</H2>
            <div style={{ fontSize: 16.5, color: C.ink, fontWeight: 600 }}>{brandName}</div>
            {brandPhone && <div style={{ fontSize: 15.5, color: C.body, marginTop: 6 }}>{brandPhone}</div>}
            {brandEmail && <div style={{ fontSize: 15.5, color: C.body, marginTop: 2 }}>{brandEmail}</div>}
            {brandSite && <div style={{ fontSize: 15.5, color: C.body, marginTop: 2 }}>{brandSite}</div>}

            <div style={{
              display: 'grid', gap: 'clamp(20px, 4vw, 44px)',
              gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
              marginTop: 'clamp(36px, 6vw, 56px)',
            }}>
              <div style={{ borderTop: '1px solid #9fb0c2', paddingTop: 8 }}>
                <div style={{ fontSize: 12.5, color: C.muted }}>
                  {(proposal.client_company || proposal.client_name || 'Client')} representative
                </div>
              </div>
              <div style={{ borderTop: '1px solid #9fb0c2', paddingTop: 8 }}>
                <div style={{ fontSize: 12.5, color: C.muted }}>Date</div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div style={{
          maxWidth: 900, margin: '26px auto 0', textAlign: 'center',
          color: C.muted, fontSize: 12.5, lineHeight: 1.6,
        }}>
          <div style={{ marginBottom: 6, fontSize: 13 }}><Wordmark /></div>
          This proposal is private and intended only for its recipient.
          For questions, contact your advisor directly.
        </div>
      </div>
    </div>
  )
}
