/**
 * GodReturnBar — the way back to the Command Center.
 *
 * God Mode can launch into the customer app and the Sales Workspace. Without
 * this, getting back meant retyping the URL or signing out and in again, which
 * is what made those a one-way trip.
 *
 * Rendered ONLY for god_admin, in both the tenant Layout and the Sales
 * Workspace shell. Styled inline on purpose: those two live in completely
 * different CSS systems (tenant theme variables vs the scoped `sw-` sheet), and
 * a strip that carries its own appearance is identical in both rather than
 * inheriting whichever one it landed in.
 *
 * It is a visual affordance, not a permission. `/god` is guarded by GodRoute in
 * the client and `require_god` on every god endpoint server-side.
 */
import { useNavigate } from 'react-router-dom'
import { getCurrentUser } from '../api/client'

export default function GodReturnBar({ context }) {
  const navigate = useNavigate()
  const user = getCurrentUser()

  // Everyone else must never see this, or it reads as a door they cannot open.
  if (user?.role !== 'god_admin') return null

  return (
    <div
      style={{
        background: 'rgba(245,185,66,0.10)',
        borderBottom: '1px solid rgba(245,185,66,0.30)',
        padding: '7px 20px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        flexShrink: 0,
        flexWrap: 'wrap',
        fontFamily: "'Inter', system-ui, sans-serif",
      }}
    >
      <span style={{ color: '#ffd968', fontSize: 11 }}>⚡</span>
      <span style={{ color: '#f5b942', fontWeight: 700, fontSize: 10, letterSpacing: '0.11em' }}>
        GOD ADMIN
      </span>
      <span style={{ color: '#a88030', fontSize: 11 }}>—</span>
      <span style={{ color: '#c09040', fontSize: 11 }}>
        viewing {context || 'the application'}
      </span>
      <div style={{ flex: 1, minWidth: 12 }} />
      <button
        type="button"
        onClick={() => navigate('/god')}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: 'rgba(245,185,66,0.15)',
          border: '1px solid rgba(245,185,66,0.40)',
          borderRadius: 3, color: '#f5b942', cursor: 'pointer',
          fontFamily: 'inherit', fontSize: 11, fontWeight: 600,
          letterSpacing: '0.05em', padding: '4px 11px', whiteSpace: 'nowrap',
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        BACK TO COMMAND CENTER
      </button>
    </div>
  )
}
