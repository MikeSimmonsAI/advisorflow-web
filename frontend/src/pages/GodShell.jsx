import { useState } from 'react'
import { api } from '../api/client'

export default function GodShell({ children, orgSession, onExitOrgSession }) {
  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg, #0d0d0d)' }}>
      {orgSession && (
        <div style={{
          background: 'rgba(245,158,11,0.12)', borderBottom: '1px solid rgba(245,158,11,0.3)',
          padding: '8px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: 13, color: '#fbbf24',
        }}>
          <span>👁 Viewing org: <strong>{orgSession.name || orgSession.org_id}</strong></span>
          <button onClick={onExitOrgSession} style={{
            background: 'rgba(245,158,11,0.2)', border: '1px solid rgba(245,158,11,0.4)',
            color: '#fbbf24', borderRadius: 4, padding: '3px 10px', cursor: 'pointer', fontSize: 12,
          }}>Exit Org View</button>
        </div>
      )}
      {children}
    </div>
  )
}
