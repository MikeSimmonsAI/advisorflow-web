/**
 * DemoSite — the prospect's view of a design mockup.
 *
 * URL: /demo/:token  ·  public, no account, no JWT. The token IS the
 * authorization, exactly as with the proposal portal.
 *
 * WHY THE PAGE IS RENDERED IN A SANDBOXED FRAME.
 * A demo is arbitrary HTML. Served directly into this document it would run
 * same-origin with the app, and the app keeps its session token in
 * localStorage — so any mistake in a mockup, now or in a year, would be a
 * token-theft bug. `sandbox="allow-scripts"` WITHOUT `allow-same-origin` puts
 * the frame on a unique opaque origin: its scripts still run, so the mockup
 * stays interactive, but it can read nothing of ours.
 *
 * Those two flags together are the whole protection. Adding allow-same-origin
 * back — for any reason — removes it.
 */

import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api/client'

export default function DemoSite() {
  const { token } = useParams()
  const [demo, setDemo] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!token) { setError('This link is missing its access key.'); return }
    api.get(`/public/demo/${token}`)
      .then(d => {
        setDemo(d)
        if (d && d.title) document.title = d.title
      })
      .catch(e => setError(
        (e && e.message) ||
        'This link is no longer available. Ask your contact for a new one.'
      ))
  }, [token])

  if (error) {
    return (
      <div style={S.center}>
        <div style={S.card}>
          <h1 style={S.h}>This link isn’t available</h1>
          <p style={S.p}>{error}</p>
        </div>
      </div>
    )
  }

  if (!demo) {
    return (
      <div style={S.center}>
        <div style={S.card}><p style={S.p}>Loading…</p></div>
      </div>
    )
  }

  return (
    <iframe
      title={demo.title || 'Design concept'}
      srcDoc={demo.html}
      sandbox="allow-scripts"
      style={{
        position: 'fixed', inset: 0, width: '100%', height: '100%',
        border: 'none', display: 'block',
      }}
    />
  )
}

const S = {
  center: {
    minHeight: '100vh', display: 'grid', placeItems: 'center',
    background: '#0f1720', padding: 24,
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif',
  },
  card: {
    maxWidth: 460, textAlign: 'center', background: '#16202b',
    border: '1px solid #24303d', borderRadius: 10, padding: '34px 30px',
  },
  h: { color: '#e8eef5', fontSize: 21, margin: '0 0 10px', fontWeight: 600 },
  p: { color: '#8fa3b6', fontSize: 15, margin: 0, lineHeight: 1.6 },
}
