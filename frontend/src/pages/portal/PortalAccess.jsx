/**
 * PortalAccess — magic-link resolver
 *
 * URL: /portal/access/:token
 *
 * Hits the backend to validate the token, gets back the proposal + view_id,
 * stores both in sessionStorage (not localStorage — intentionally session-scoped),
 * then navigates to the full-screen portal viewer.
 *
 * No internal JWT is involved. The token IS the authentication.
 */

import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'

export default function PortalAccess() {
  const { token } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!token) {
      setError('Invalid link.')
      return
    }

    api.get(`/proposals/portal/resolve/${token}`)
      .then(r => {
        const { view_id, proposal, branding, permissions } = r.data
        // Store in sessionStorage — cleared when tab is closed
        sessionStorage.setItem('portal_view_id', view_id)
        sessionStorage.setItem('portal_proposal', JSON.stringify(proposal))
        sessionStorage.setItem('portal_branding', JSON.stringify(branding || {}))
        sessionStorage.setItem('portal_permissions', JSON.stringify(permissions || {}))
        navigate(`/portal/view/${proposal.id}`, { replace: true })
      })
      .catch(err => {
        const msg = err.response?.data?.detail || 'This link is invalid or has expired.'
        setError(msg)
      })
  }, [token])

  return (
    <div style={{
      minHeight: '100vh',
      background: '#040812',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontFamily: '"Inter", system-ui, sans-serif',
    }}>
      {error ? (
        <div style={{ textAlign: 'center', maxWidth: 420, padding: 32 }}>
          <div style={{
            width: 64, height: 64, borderRadius: '50%',
            background: 'rgba(255,80,80,0.1)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 28, margin: '0 auto 24px',
          }}>
            🔒
          </div>
          <h2 style={{ color: '#fff', fontSize: 20, fontWeight: 700, margin: '0 0 12px' }}>
            Link Unavailable
          </h2>
          <p style={{ color: '#667', fontSize: 15, lineHeight: 1.6, margin: 0 }}>
            {error}
          </p>
          <p style={{ color: '#445', fontSize: 13, marginTop: 24 }}>
            Contact your advisor if you believe this is an error.
          </p>
        </div>
      ) : (
        <div style={{ textAlign: 'center' }}>
          {/* Animated logo mark */}
          <div style={{
            width: 48, height: 48, borderRadius: 12,
            background: 'linear-gradient(135deg, #087cff, #0a56b0)',
            margin: '0 auto 24px',
            animation: 'pulse 1.8s ease-in-out infinite',
            boxShadow: '0 0 32px rgba(8,124,255,0.3)',
          }} />
          <p style={{ color: '#445', fontSize: 14 }}>Accessing your proposal…</p>
          <style>{`
            @keyframes pulse {
              0%, 100% { opacity: 1; transform: scale(1); }
              50% { opacity: 0.7; transform: scale(0.95); }
            }
          `}</style>
        </div>
      )}
    </div>
  )
}
