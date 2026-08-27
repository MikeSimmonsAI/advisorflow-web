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
 *
 * Presentation matches the proposal document it hands off to: paper, navy,
 * one blue keyline. Nothing here is themed by the host.
 */

import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'

const NAVY = '#12304f'
const BLUE = '#2f76c7'
const PAGE = '#eef1f6'
const LINE = '#dfe5ec'
const BODY = '#3a4756'
const MUTED = '#6d7c8d'

function Wordmark() {
  return (
    <span style={{
      fontFamily: 'Archivo, "Helvetica Neue", Arial, sans-serif',
      fontWeight: 700, letterSpacing: '-.02em', color: NAVY, fontSize: 18,
    }}>
      EvoSys <span style={{ color: BLUE }}>Pro</span>
    </span>
  )
}

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
        const { view_id, proposal, branding, permissions } = r
        // Store in sessionStorage — cleared when tab is closed
        sessionStorage.setItem('portal_view_id', view_id)
        sessionStorage.setItem('portal_proposal', JSON.stringify(proposal))
        sessionStorage.setItem('portal_branding', JSON.stringify(branding || {}))
        sessionStorage.setItem('portal_permissions', JSON.stringify(permissions || {}))
        navigate(`/portal/view/${proposal.id}`, { replace: true })
      })
      .catch(err => {
        const msg = err.message || 'This link is invalid or has expired.'
        setError(msg)
      })
  }, [token])

  return (
    <div style={{
      minHeight: '100vh',
      background: PAGE,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24,
      fontFamily: '"Source Sans 3", system-ui, -apple-system, Arial, sans-serif',
      color: BODY,
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=Source+Sans+3:wght@400;600&display=swap');
        @keyframes paSlide { 0% { transform: translateX(-100%); } 100% { transform: translateX(300%); } }
      `}</style>

      {error ? (
        <div style={{
          textAlign: 'center', maxWidth: 460, padding: '40px 32px',
          background: '#fff', border: '1px solid ' + LINE,
          borderTop: '4px solid ' + NAVY,
        }}>
          <div style={{ marginBottom: 16 }}><Wordmark /></div>
          <h2 style={{
            fontFamily: 'Archivo, Arial, sans-serif', color: NAVY,
            fontSize: 20, fontWeight: 700, margin: '0 0 10px',
          }}>
            This link is no longer active
          </h2>
          <p style={{ fontSize: 15, lineHeight: 1.6, margin: 0 }}>{error}</p>
          <p style={{ color: MUTED, fontSize: 13.5, marginTop: 22, marginBottom: 0 }}>
            Contact your advisor if you believe this is an error.
          </p>
        </div>
      ) : (
        <div style={{ textAlign: 'center' }}>
          <div style={{ marginBottom: 16 }}><Wordmark /></div>
          <div style={{ width: 170, height: 3, background: '#dbe3ec', overflow: 'hidden', margin: '0 auto' }}>
            <div style={{
              width: '40%', height: '100%',
              background: 'linear-gradient(90deg, #1b3f66 0%, #4a8ed4 55%, #9cc6ee 100%)',
              animation: 'paSlide 1.2s ease-in-out infinite',
            }} />
          </div>
          <p style={{ color: MUTED, fontSize: 14, marginTop: 18 }}>Opening your proposal…</p>
        </div>
      )}
    </div>
  )
}
