/**
 * STAGING / QA — visible on every screen of the staging deploy, nowhere else.
 *
 * Asks the backend (same probe as DemoBanner), not the page URL, so a production
 * bundle can never wear it and staging can never miss it. Inline styles so it
 * looks identical in every shell. Renders nothing at all outside staging.
 */
import { useEffect, useState } from 'react'
import { fetchEnvironment } from '../api/demo'
import { stagingBanner } from '../pages/god/controlRoomView'

export default function StagingBanner() {
  const [env, setEnv] = useState(null)
  useEffect(() => {
    let alive = true
    fetchEnvironment().then((d) => { if (alive) setEnv(d) })
    return () => { alive = false }
  }, [])
  const b = stagingBanner(env)
  if (!b) return null
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
