import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import '../styles/shared.css'
import './SystemHealth.css'

const statusCards = [
  {
    key: 'twilio_connected',
    title: 'Twilio SMS',
    description: 'Advisor SMS account SID is configured.',
    connected: 'Ready to send advisor-owned SMS.',
    disconnected: 'Twilio account SID is not configured.',
  },
  {
    key: 'google_calendar_connected',
    title: 'Google Calendar',
    description: 'Bookings can sync onto the advisor calendar.',
    connected: 'Calendar OAuth connection is active.',
    disconnected: 'Google Calendar is not connected.',
  },
  {
    key: 'microsoft_365_connected',
    title: 'Microsoft 365 Email',
    description: 'Outbound email can send from the advisor mailbox.',
    connected: 'Microsoft 365 mailbox is connected.',
    disconnected: 'Microsoft 365 is not connected.',
  },
]

function formatDate(value) {
  if (!value) return 'Not tracked yet'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function StatusCard({ card, connected }) {
  return (
    <article className={`panel health-card ${connected ? 'health-card--online' : 'health-card--offline'}`}>
      <div className="health-card-topline">
        <div className={`health-icon ${connected ? 'health-icon--online' : 'health-icon--offline'}`} aria-hidden="true">
          {connected ? '✓' : '×'}
        </div>
        <span className={`health-status-pill ${connected ? 'health-status-pill--online' : 'health-status-pill--offline'}`}>
          {connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>
      <h2>{card.title}</h2>
      <p>{card.description}</p>
      <small>{connected ? card.connected : card.disconnected}</small>
    </article>
  )
}

export default function SystemHealth() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const connectedCount = useMemo(() => {
    if (!status) return 0
    return statusCards.reduce((count, card) => count + (status[card.key] ? 1 : 0), 0)
  }, [status])

  async function loadStatus() {
    setLoading(true)
    setError('')
    try {
      const data = await api.get('/health/advisor-status')
      setStatus(data)
    } catch (err) {
      setError(err.message || 'Could not load system health.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadStatus()
  }, [])

  return (
    <div className="system-health-page">
      <header className="page-header system-health-header">
        <div>
          <p className="system-health-eyebrow">Advisor System Monitor</p>
          <h1 className="page-title">System Health</h1>
          <p className="page-subtitle">Read-only connection status for this advisor account.</p>
        </div>
        <div className="panel system-health-summary">
          <SignalPulse color={connectedCount === statusCards.length ? 'green' : 'red'} label={connectedCount === statusCards.length ? 'All systems connected' : 'Needs setup'} />
          <strong>{loading ? '—' : `${connectedCount}/${statusCards.length}`}</strong>
          <span>integrations connected</span>
        </div>
      </header>

      {error ? <div className="system-health-alert">{error}</div> : null}

      <section className="system-health-grid">
        {statusCards.map((card) => (
          <StatusCard key={card.key} card={card} connected={Boolean(status?.[card.key])} />
        ))}
      </section>

      <section className="panel cadence-health-panel">
        <div className="panel-header">
          <div>
            <h2 className="panel-title">Cadence Job</h2>
            <p className="cadence-health-subtitle">Last scheduler run timestamp.</p>
          </div>
          <span className="health-status-pill health-status-pill--neutral">Read only</span>
        </div>
        <div className="cadence-health-value">
          <span className="mono">{loading ? 'Loading...' : formatDate(status?.last_cadence_run)}</span>
          <p>
            AdvisorFlow currently tracks per-lead cadence timestamps. A dedicated cadence job-run timestamp has not been added yet.
          </p>
        </div>
      </section>
    </div>
  )
}
