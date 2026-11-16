/**
 * STAGING / QA — visible on every screen of the staging deploy, nowhere else.
 *
 * Asks the backend (same probe as DemoBanner), not the page URL, so a production
 * bundle can never wear it and staging can never miss it. Inline styles so it
 * looks identical in every shell. Renders nothing at all outside staging.
 */
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { fetchEnvironment } from '../api/demo'
import { stagingBanner } from '../pages/god/controlRoomView'

// EvoSys Pro Mobile (/app) draws this strip INSIDE its own shell (inShell), so
// the page-level copy stays out of the way there and the phone layout never shifts.
export default function StagingBanner({ inShell = false }) {
  const { pathname } = useLocation()
  const [env, setEnv] = useState(null)
  useEffect(() => {
    let alive = true
    fetchEnvironment().then((d) => { if (alive) setEnv(d) })
    return () => { alive = false }
  }, [])
  const b = stagingBanner(env)
  if (!b) return null
  const inApp = pathname === '/app' || pathname.startsWith('/app/')
  if (inApp !== inShell) return null
  if (inShell) {
    return (
      <div role="status" data-testid="staging-banner" className="ta-envbanner">
        <strong>● {b.label}</strong> <span>{b.text}</span>
      </div>
    )
  }
  return (
    <div role="status" data-testid="staging-banner" style={{
      background: '#7c2d12', color: '#fff', padding: '6px 14px', display: 'flex', gap: 10, flexWrap: 'wrap',
      alignItems: 'center', fontFamily: "'Inter', system-ui, sans-serif", fontSize: 12, position: 'relative', zIndex: 61,
    }}>
      <strong style={{ letterSpacing: '0.12em' }}>● {b.label}</strong>
      <span>{b.text}</span>
    </div>
  )
}
