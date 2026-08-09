/**
 * Public page — no login required.
 * Advisors open this via a time-limited link an admin sends them.
 * They can connect their Google Calendar and/or Microsoft 365 account
 * so BookaBoost can book appointments and send email on their behalf.
 *
 * URL params:
 *   ?token=<JWT>              — the advisor's setup token (required for interactive state)
 *   ?calendar_connected=true  — landing back here after successful Google OAuth
 *   ?microsoft_connected=true — landing back here after successful Microsoft OAuth
 *   ?calendar_error=<msg>     — OAuth failed or was denied
 *   ?microsoft_error=<msg>    — OAuth failed or was denied
 */

import { useEffect, useState } from 'react'
import { api } from '../api/client'

const BG = 'var(--bg-app, #0f1117)'
const CARD = 'var(--bg-card, #1a1f2e)'
const BORDER = 'var(--border, #2d3748)'
const TEXT_SEC = 'var(--text-secondary, #94a3b8)'
const TEXT_TER = 'var(--text-tertiary, #64748b)'

export default function SetupIntegrations() {
  const params = new URLSearchParams(window.location.search)
  const token = params.get('token')
  const calendarConnected = params.get('calendar_connected') === 'true'
  const microsoftConnected = params.get('microsoft_connected') === 'true'
  const calendarError = params.get('calendar_error')
  const microsoftError = params.get('microsoft_error')

  const [advisor, setAdvisor] = useState(null)
  const [loading, setLoading] = useState(!!token)
  const [error, setError] = useState(null)
  const [connectingGoogle, setConnectingGoogle] = useState(false)
  const [connectingMicrosoft, setConnectingMicrosoft] = useState(false)

  useEffect(() => {
    if (!token) { setLoading(false); return }
    api.get(`/setup/verify?token=${encodeURIComponent(token)}`)
      .then(data => { setAdvisor(data); setLoading(false) })
      .catch(err => { setError(err.message); setLoading(false) })
  }, [token])

  async function handleGoogleConnect() {
    setConnectingGoogle(true)
    try {
      const result = await api.get(`/setup/google-connect?token=${encodeURIComponent(token)}`)
      window.location.href = result.authorization_url
    } catch (err) {
      alert(`Could not start Google connection: ${err.message}`)
      setConnectingGoogle(false)
    }
  }

  async function handleMicrosoftConnect() {
    setConnectingMicrosoft(true)
    try {
      const result = await api.get(`/setup/microsoft-connect?token=${encodeURIComponent(token)}`)
      window.location.href = result.authorization_url
    } catch (err) {
      alert(`Could not start Microsoft connection: ${err.message}`)
      setConnectingMicrosoft(false)
    }
  }

  // ── Post-OAuth success landing ─────────────────────────────────────────────
  if (calendarConnected || microsoftConnected) {
    const service = calendarConnected ? 'Google Calendar' : 'Microsoft 365'
    const detail = calendarConnected
      ? 'Appointments booked through BookaBoost will land directly on your calendar.'
      : 'Outbound emails will send from your real Outlook address and replies arrive in your inbox.'
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: BG, padding: 24 }}>
        <div style={{ textAlign: 'center', maxWidth: 460 }}>
          <div style={{ fontSize: 64, marginBottom: 20 }}>✅</div>
          <h1 style={{ fontSize: 26, fontWeight: 800, marginBottom: 10, color: '#fff' }}>{service} connected!</h1>
          <p style={{ fontSize: 15, color: TEXT_SEC, lineHeight: 1.6 }}>{detail}</p>
          <p style={{ marginTop: 28, fontSize: 13, color: TEXT_TER }}>You can close this window.</p>
        </div>
      </div>
    )
  }

  // ── OAuth error landing ────────────────────────────────────────────────────
  if ((calendarError || microsoftError) && !token) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: BG, padding: 24 }}>
        <div style={{ textAlign: 'center', maxWidth: 460 }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>⚠️</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 8, color: '#fff' }}>Connection failed</h1>
          <p style={{ fontSize: 14, color: TEXT_SEC }}>{calendarError || microsoftError}</p>
          <p style={{ marginTop: 16, fontSize: 13, color: TEXT_TER }}>Ask your admin to send you the setup link again and try once more.</p>
        </div>
      </div>
    )
  }

  // ── Loading ────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: BG }}>
        <p style={{ color: TEXT_SEC }}>Verifying your setup link…</p>
      </div>
    )
  }

  // ── Bad / missing token ────────────────────────────────────────────────────
  if (!token || error) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: BG, padding: 24 }}>
        <div style={{ textAlign: 'center', maxWidth: 460 }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🔗</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 8, color: '#fff' }}>
            {error ? 'Link not valid' : 'No setup link found'}
          </h1>
          <p style={{ fontSize: 14, color: TEXT_SEC }}>
            {error || 'This page needs a valid setup link. Ask your admin to send you one.'}
          </p>
        </div>
      </div>
    )
  }

  // ── Main setup UI ─────────────────────────────────────────────────────────
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: BG, padding: 24 }}>
      <div style={{ width: '100%', maxWidth: 500 }}>

        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{ fontSize: 44, marginBottom: 14 }}>⚡</div>
          <h1 style={{ fontSize: 26, fontWeight: 800, color: '#fff', marginBottom: 8 }}>Connect your accounts</h1>
          {advisor && (
            <p style={{ fontSize: 15, color: TEXT_SEC, lineHeight: 1.6 }}>
              Hi <strong style={{ color: '#fff' }}>{advisor.full_name}</strong> — link your Google Calendar
              and/or Microsoft 365 so BookaBoost can schedule appointments and send emails on your behalf.
            </p>
          )}
          {(calendarError || microsoftError) && (
            <div style={{ marginTop: 12, padding: '10px 14px', borderRadius: 8, background: 'rgba(239,68,68,0.12)', color: '#f87171', fontSize: 13 }}>
              Connection failed: {calendarError || microsoftError}. Please try again.
            </div>
          )}
        </div>

        {/* Google Calendar card */}
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 14, padding: 24, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16 }}>
            <span style={{ fontSize: 32, flexShrink: 0 }}>📅</span>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, fontSize: 16, color: '#fff' }}>Google Calendar</span>
                {advisor?.google_calendar_connected && (
                  <span style={{ background: '#16a34a22', color: '#22c55e', borderRadius: 999, padding: '2px 10px', fontSize: 11, fontWeight: 700 }}>
                    Connected
                  </span>
                )}
              </div>
              <p style={{ fontSize: 13, color: TEXT_SEC, marginTop: 4, lineHeight: 1.5 }}>
                Appointments booked through your BookaBoost link land directly on your Google Calendar — no manual entry needed.
              </p>
            </div>
          </div>
          <button
            onClick={handleGoogleConnect}
            disabled={connectingGoogle}
            style={{
              width: '100%', padding: '13px 0', borderRadius: 10, fontWeight: 700,
              fontSize: 14, cursor: connectingGoogle ? 'not-allowed' : 'pointer',
              background: '#4285f4', color: '#fff', border: 'none', opacity: connectingGoogle ? 0.7 : 1,
              transition: 'opacity 0.15s',
            }}
          >
            {connectingGoogle ? 'Redirecting to Google…' : advisor?.google_calendar_connected ? 'Reconnect Google Calendar' : 'Connect Google Calendar'}
          </button>
        </div>

        {/* Microsoft 365 card */}
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 14, padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16 }}>
            <span style={{ fontSize: 32, flexShrink: 0 }}>📧</span>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, fontSize: 16, color: '#fff' }}>Microsoft 365</span>
                {advisor?.microsoft_365_connected && (
                  <span style={{ background: '#16a34a22', color: '#22c55e', borderRadius: 999, padding: '2px 10px', fontSize: 11, fontWeight: 700 }}>
                    Connected
                  </span>
                )}
              </div>
              <p style={{ fontSize: 13, color: TEXT_SEC, marginTop: 4, lineHeight: 1.5 }}>
                Outbound emails send from your real Outlook address so recipients see your name and replies come back to your inbox.
              </p>
            </div>
          </div>
          <button
            onClick={handleMicrosoftConnect}
            disabled={connectingMicrosoft}
            style={{
              width: '100%', padding: '13px 0', borderRadius: 10, fontWeight: 700,
              fontSize: 14, cursor: connectingMicrosoft ? 'not-allowed' : 'pointer',
              background: '#0078d4', color: '#fff', border: 'none', opacity: connectingMicrosoft ? 0.7 : 1,
              transition: 'opacity 0.15s',
            }}
          >
            {connectingMicrosoft ? 'Redirecting to Microsoft…' : advisor?.microsoft_365_connected ? 'Reconnect Microsoft 365' : 'Connect Microsoft 365'}
          </button>
        </div>

        <p style={{ textAlign: 'center', marginTop: 24, fontSize: 12, color: TEXT_TER }}>
          Powered by BookaBoost · This setup link expires in 48 hours
        </p>
      </div>
    </div>
  )
}
