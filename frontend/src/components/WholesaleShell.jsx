/* The focused Wholesale shell (Phase 7.3).
 *
 * Inside /wholesale the platform rail becomes the product the approved board
 * shows: a light sidebar with the two operating worlds only, a light top bar
 * with ONE search, the environment indicator and the signed-in person. Every
 * other platform screen is still one click away under "EvoSys Platform" —
 * nothing is removed, it is simply out of the way of the wholesale work.
 *
 * Layout.jsx decides WHEN this applies (a /wholesale path outside a configured
 * vertical); this file only draws the pieces. */
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { fetchEnvironment } from '../api/demo'
import './wholesale-shell.css'

export function isWholesalePath(pathname) {
  return pathname === '/wholesale' || pathname.startsWith('/wholesale/')
}

/** The board's top-bar search. Searches the acquisition inbox (address, city,
 *  ZIP, owner) — the one list that holds every property EvoSense knows about. */
export function WholesaleSearch() {
  const navigate = useNavigate()
  const location = useLocation()
  const current = new URLSearchParams(location.search).get('q') || ''
  const [q, setQ] = useState(current)
  useEffect(() => { setQ(current) }, [current])
  return (
    <form className="wsx-search" role="search" onSubmit={(e) => {
      e.preventDefault()
      const v = q.trim()
      navigate('/wholesale/evosense/inbox' + (v ? '?q=' + encodeURIComponent(v) : ''))
    }}>
      <label className="wsx-sr" htmlFor="wsx-search-input">Search properties and opportunities</label>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" />
      </svg>
      <input id="wsx-search-input" type="search" value={q} onChange={(e) => setQ(e.target.value)}
             placeholder="Search address, city, ZIP or opportunity…" autoComplete="off" />
    </form>
  )
}

/** "Am I looking at production?" — answered by the backend, never guessed. */
export function WholesaleEnvironment() {
  const [env, setEnv] = useState(null)
  useEffect(() => {
    let alive = true
    fetchEnvironment().then((d) => { if (alive) setEnv(d) })
    return () => { alive = false }
  }, [])
  if (!env || !env.local_review) return null
  return (
    <span className="wsx-env" role="status"
          title="Local review environment: a local database with sandbox data. Nothing here is production, and no real message, call or charge can leave this computer.">
      <span className="wsx-env__dot" aria-hidden="true" />
      <span className="wsx-sr">Local review environment · Sandbox data</span>
      <span className="wsx-env__long" aria-hidden="true">Local review · Sandbox data</span>
      <span className="wsx-env__short" aria-hidden="true">Local review</span>
    </span>
  )
}

export function WholesaleUser({ user, photo }) {
  const name = user?.full_name || 'Signed in'
  const role = (user?.role || '').replace(/_/g, ' ')
  return (
    <span className="wsx-user" title={name}>
      <span className="wsx-user__avatar" aria-hidden="true">
        {photo ? <img src={photo} alt="" /> : (name[0] || '?')}
      </span>
      <span className="wsx-user__text">
        <span className="wsx-user__name">{name}</span>
        {role ? <span className="wsx-user__role">{role}</span> : null}
      </span>
    </span>
  )
}

/** Paints the page canvas light while a wholesale screen is showing. */
export function useWholesaleCanvas(active) {
  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('wsx-canvas', !!active)
    return () => root.classList.remove('wsx-canvas')
  }, [active])
}
