/* /auth/google/complete - the last hop of Sign in with Google (#430).
 * The backend callback sent the browser here with a single-use ticket in the
 * URL FRAGMENT (never sent to any server). It is removed from the address bar
 * immediately, exchanged with a POST for the normal session, and the person is
 * routed exactly as a password sign-in routes them. Any failure goes back to
 * the login page with a plain message - never a half-signed-in screen. */
import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { exchangeGoogleTicket, fetchAndStoreBranding, startKeepAlive, startRefreshLoop, safeNextPath,
         getWorkspaceContext } from '../api/client'
import { signInDestination } from '../auth/signInDestination'

export default function GoogleSignInComplete() {
  const navigate = useNavigate()
  const ran = useRef(false)
  useEffect(() => {
    if (ran.current) return
    ran.current = true
    const frag = new URLSearchParams((window.location.hash || '').replace(/^#/, ''))
    const ticket = frag.get('ticket')
    const nextPath = safeNextPath(frag.get('next'))
    try { window.history.replaceState(null, '', window.location.pathname) } catch { /* old browsers */ }
    if (!ticket) { navigate('/login?google_error=failed', { replace: true }); return }
    ;(async () => {
      try {
        const priorWorkspace = getWorkspaceContext()
        const user = await exchangeGoogleTicket(ticket)
        await fetchAndStoreBranding()
        startKeepAlive()
        startRefreshLoop()
        navigate(await signInDestination(user, nextPath, priorWorkspace), { replace: true })
      } catch {
        navigate('/login?google_error=failed', { replace: true })
      }
    })()
  }, [navigate])
  return (
    <div role="status" aria-live="polite" style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#040a16', color: '#cfe2ff', fontFamily: 'Inter, sans-serif' }}>
      Signing you in…
    </div>
  )
}
