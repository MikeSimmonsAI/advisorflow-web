import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchMyContexts, getCurrentUser } from '../api/client'
import './ProgramSwitchLink.css'

/**
 * THE ONE "Switch program" BUTTON - top-right of every program's header
 * (Wholesale, SCI, Atlantis, Roofing, Restland, Insurance). Same label, size
 * and behaviour everywhere: it always goes to the program launcher
 * (/workspaces), which sets the next workspace before that program loads.
 *
 * Drawn only for a person the SERVER lists with more than one program
 * (/auth/my-contexts). It grants nothing; it is a way back to the launcher.
 * God Mode has its own workspace controls and does not get it.
 */
export function hasSeveralPrograms(ctx) {
  return ((ctx?.workspace_contexts?.length || 0) + (ctx?.product_contexts?.length || 0)) > 1
}

export default function ProgramSwitchLink({ className = '', tone = 'auto' }) {
  const [multi, setMulti] = useState(false)
  const role = getCurrentUser()?.role
  useEffect(() => {
    let live = true
    fetchMyContexts().then(c => { if (live) setMulti(hasSeveralPrograms(c)) })
      .catch(() => { if (live) setMulti(false) })
    return () => { live = false }
  }, [])
  if (!multi || role === 'god_admin') return null
  return (
    <Link to="/workspaces" className={`program-switch program-switch--${tone} ${className}`.trim()}
          aria-label="Switch program">
      <span className="program-switch__label">Switch program</span>
      <span aria-hidden="true" className="program-switch__icon">↗</span>
    </Link>
  )
}
