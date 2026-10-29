/**
 * GrantedPreview - a demo this login was given permission to view by God
 * (deal_preview_grants). URL: /previews/:grantId, signed in (App.jsx sends a
 * signed-out visitor to /login before this renders).
 *
 * The server re-checks the grant on every open, records the open in the audit
 * log, does not count it as the prospect's view, and returns the stored page
 * exactly as it was built ({title, html}). It never sends the demo's own link.
 *
 * The page is shown in the same sandboxed frame as the public demo page
 * (DemoFrame: allow-scripts, never allow-same-origin), untouched: the demo
 * keeps its own look, buttons and behaviour. Only the thin bar above it - the
 * way back to Program Access - belongs to this app.
 */
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { DemoFrame } from '../portal/DemoSite'

const BAR = 44

export default function GrantedPreview() {
  const { grantId } = useParams()
  const navigate = useNavigate()
  const [demo, setDemo] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!grantId) return
    let live = true
    api.get('/program-access/previews/' + encodeURIComponent(grantId))
      .then(d => { if (live) { setDemo(d); if (d && d.title) document.title = d.title } })
      .catch(() => { if (live) setError('This preview is not available.') })
    return () => { live = false }
  }, [grantId])

  const bar = (
    <div style={S.bar} role="navigation" aria-label="Preview">
      <button type="button" style={S.back} onClick={() => navigate('/workspaces')}>← Program Access</button>
      <span style={S.label}>{(demo && demo.label) || 'Authorized preview'}{demo && demo.title ? ' · ' + demo.title : ''}</span>
    </div>
  )

  if (error || !demo) {
    return (
      <div style={S.center}>
        {bar}
        <div style={S.card} role={error ? 'alert' : 'status'}>
          <p style={S.p}>{error || 'Loading preview…'}</p>
        </div>
      </div>
    )
  }
  return (
    <>
      {bar}
      <DemoFrame title={demo.title} html={demo.html} top={BAR} />
    </>
  )
}

const S = {
  bar: {
    position: 'fixed', top: 0, left: 0, right: 0, height: BAR, zIndex: 2,
    display: 'flex', alignItems: 'center', gap: 14, padding: '0 14px',
    background: '#111111', borderBottom: '1px solid #2a2a2a', color: '#e9e4d8',
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif', fontSize: 14,
  },
  back: {
    background: 'transparent', color: '#e9e4d8', border: '1px solid #3a3a3a', borderRadius: 6,
    padding: '6px 10px', font: 'inherit', cursor: 'pointer',
  },
  label: { color: '#bdb5a3', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  center: {
    minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#141414', padding: 24,
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif',
  },
  card: { maxWidth: 460, textAlign: 'center', background: '#1c1c1c', border: '1px solid #2a2a2a', borderRadius: 10, padding: '28px 26px' },
  p: { color: '#bdb5a3', fontSize: 15, margin: 0, lineHeight: 1.6 },
}
