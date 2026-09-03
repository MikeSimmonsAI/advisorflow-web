/**
 * ExecObserveShell — wrapper for executive observation mode.
 *
 * Renders a red "EXECUTIVE VIEW — {ORG NAME} — READ ONLY" banner above
 * the observation content. Fetches org identity from
 *   GET /executive/organizations/{orgId}
 * which require_brand_executive + platform isolation guards.
 *
 * SECURITY: org_id comes from the URL params only. Nothing here injects
 * organization_id into the user context or grants any workspace access.
 *
 * EXIT: navigates back to /executive/organizations.
 */

import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import ExecObservationOverview from './ExecObservationOverview'

function useOrgIdentity(orgId) {
  const [state, setState] = useState({ loading: true, org: null, error: null })
  useEffect(() => {
    if (!orgId) { setState({ loading: false, org: null, error: 'No organization ID.' }); return }
    api.get(`/executive/organizations/${orgId}`)
      .then(r => setState({ loading: false, org: r, error: null }))
      .catch(err => {
        const status = err?.status
        setState({
          loading: false,
          org: null,
          error: status === 404
            ? 'Organization not found or not accessible under your brand.'
            : status === 403
            ? 'You do not have executive access for this organization.'
            : 'Could not load organization details. Please try again.',
        })
      })
  }, [orgId])
  return state
}

export default function ExecObserveShell() {
  const { orgId } = useParams()
  const navigate = useNavigate()
  const { loading, org, error } = useOrgIdentity(orgId)

  if (loading) {
    return (
      <div style={s.loading}>
        <p style={s.loadingText}>Loading organization…</p>
      </div>
    )
  }

  if (error || !org) {
    return (
      <div style={s.loading}>
        <div style={s.errorCard}>
          <h2 style={s.errorTitle}>Cannot Load Observation</h2>
          <p style={s.errorMsg}>{error || 'Organization not found.'}</p>
          <button style={s.exitBtn} onClick={() => navigate('/executive/organizations')}>
            ← Back to Organizations
          </button>
        </div>
      </div>
    )
  }

  return (
    <div style={s.wrap}>
      {/* RED READ-ONLY BANNER */}
      <div style={s.banner}>
        <div style={s.bannerLeft}>
          <span style={s.bannerIcon}>👁</span>
          <span style={s.bannerText}>
            EXECUTIVE VIEW — {(org.name || '').toUpperCase()} — READ ONLY
          </span>
        </div>
        <button style={s.exitBtn} onClick={() => navigate('/executive/organizations')}>
          ← Exit Observation
        </button>
      </div>

      {/* OBSERVATION CONTENT */}
      <ExecObservationOverview orgId={orgId} orgName={org.name} />
    </div>
  )
}

const s = {
  wrap: { display: 'flex', flexDirection: 'column', minHeight: '100%' },
  loading: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    minHeight: 300, padding: 40,
  },
  loadingText: { color: '#9ca3af', fontSize: 14 },
  errorCard: {
    background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12,
    padding: '40px 48px', textAlign: 'center', maxWidth: 440,
  },
  errorTitle: { margin: '0 0 12px', fontSize: 20, color: '#1a1f36' },
  errorMsg: { color: '#6b7280', margin: '0 0 24px', lineHeight: 1.5 },
  banner: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    background: '#7f1d1d',
    borderBottom: '2px solid #ef4444',
    padding: '10px 24px',
    flexShrink: 0,
    gap: 12,
  },
  bannerLeft: { display: 'flex', alignItems: 'center', gap: 10 },
  bannerIcon: { fontSize: 16 },
  bannerText: {
    fontSize: 13, fontWeight: 700, color: '#fca5a5',
    letterSpacing: '0.06em', fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  exitBtn: {
    background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.25)',
    color: '#fca5a5', borderRadius: 6, padding: '6px 16px', cursor: 'pointer',
    fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap',
  },
}
