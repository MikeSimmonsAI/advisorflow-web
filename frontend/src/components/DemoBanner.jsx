/**
 * DEMO MODE — SIMULATED ENVIRONMENT.
 *
 * Rendered at the very top of the application, above every shell, in the demo
 * environment and nowhere else. Nobody should ever have to wonder whether the
 * pipeline they are looking at belongs to a real customer.
 *
 * IT ASKS THE BACKEND, NOT THE URL. `GET /demo/environment` is answered by the
 * process that installed the egress firewall. A hostname check or a query
 * parameter could be wrong in the dangerous direction — a real environment
 * that looks like a demo, or worse, a demo that looks real to the person
 * clicking Reset.
 *
 * STYLED INLINE, deliberately, for the same reason GodReturnBar is: the tenant
 * app and the Sales Workspace live in two different CSS systems, and a strip
 * that carries its own appearance looks identical in both instead of
 * inheriting whichever one it landed in.
 *
 * VISIBLE, NOT OBNOXIOUS. One line, fixed height, muted amber. It has to
 * survive being on screen for an hour-long presentation without becoming the
 * thing people look at.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchEnvironment } from '../api/demo'

export default function DemoBanner() {
  const [envInfo, setEnvInfo] = useState(null)
  const [narrow, setNarrow] = useState(
    typeof window !== 'undefined' && window.innerWidth < 700
  )
  const navigate = useNavigate()

  useEffect(() => {
    let alive = true
    fetchEnvironment().then((d) => { if (alive) setEnvInfo(d) })
    const onResize = () => setNarrow(window.innerWidth < 700)
    window.addEventListener('resize', onResize)
    return () => { alive = false; window.removeEventListener('resize', onResize) }
  }, [])

  // Production renders nothing at all — not a hidden element, not an empty
  // div. There is no demo affordance in the DOM to find.
  if (!envInfo || !envInfo.demo_mode) return null

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="demo-banner"
      style={{
        background: 'linear-gradient(90deg, rgba(245,185,66,0.16), rgba(245,185,66,0.07))',
        borderBottom: '1px solid rgba(245,185,66,0.38)',
        padding: narrow ? '8px 14px' : '9px 22px',
        display: 'flex',
        alignItems: 'center',
        gap: narrow ? 8 : 12,
        flexWrap: 'wrap',
        flexShrink: 0,
        fontFamily: "'Inter', system-ui, sans-serif",
        position: 'relative',
        zIndex: 60,
      }}
    >
      <span
        style={{
          color: '#f5b942',
          fontWeight: 800,
          fontSize: narrow ? 10 : 11,
          letterSpacing: '0.13em',
          whiteSpace: 'nowrap',
        }}
      >
        ● DEMO MODE
      </span>

      {/* The reassurance is the point of the banner, so it stays visible on a
          phone. Only the long-form list of providers collapses. */}
      <span style={{ color: '#c9a24a', fontSize: narrow ? 11 : 12, lineHeight: 1.35 }}>
        {narrow
          ? 'Simulated environment — nothing is sent.'
          : 'Simulated environment. No real calls, texts, emails, calendar events, Zoom meetings, or charges will occur.'}
      </span>

      <button
        type="button"
        onClick={() => navigate('/demo')}
        style={{
          marginLeft: 'auto',
          background: 'rgba(245,185,66,0.14)',
          border: '1px solid rgba(245,185,66,0.42)',
          color: '#f5c96b',
          borderRadius: 6,
          padding: narrow ? '4px 9px' : '4px 12px',
          fontSize: narrow ? 10 : 11,
          fontWeight: 700,
          letterSpacing: '0.04em',
          cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        Demo Console
      </button>
    </div>
  )
}
