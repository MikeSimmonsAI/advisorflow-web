import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchMyContexts } from '../api/client'

/** Multi-program operator only. Nothing about the underlying platform is shown
 * in a customer's product navigation; this is solely a way to select a
 * different authorized program. */
export default function ProgramSwitchLink({ className = '' }) {
  const [multi, setMulti] = useState(false)
  useEffect(() => {
    let live = true
    fetchMyContexts().then(c => {
      if (live) setMulti((c?.workspace_contexts?.length || 0) + (c?.product_contexts?.length || 0) > 1)
    }).catch(() => { if (live) setMulti(false) })
    return () => { live = false }
  }, [])
  if (!multi) return null
  return (
    <Link to="/workspaces" className={className} style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      gap: 6, fontSize: 12, fontWeight: 700, color: 'inherit',
      border: '1px solid currentColor', opacity: .8, borderRadius: 9,
      textDecoration: 'none', padding: '8px 10px', whiteSpace: 'nowrap',
    }}>Switch program <span aria-hidden="true">↗</span></Link>
  )
}
