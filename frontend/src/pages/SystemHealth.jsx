import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import '../styles/shared.css'
import './SystemHealth.css'

function formatDate(value) {
  if (!value) return 'Not tracked yet'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function IntegrationCard({ integration, onFix }) {
  const connected = integration.connected
  return (
    <article className={`panel health-card ${connected ? 'health-card--online' : 'health-card--offline'}`}>
      <div className="health-card-topline">
        <div className={`health-icon ${connected ? 'health-icon--online' : 'health-icon--offline'}`} aria-hidden="true">
          {connected ? '✓' : '×'}
        </div>
        <span className={`health-status-pill ${connected ? 'health-status-pill--online' : 'health-status-pill--offline'}`}>
          {connected ? 'Connected' : 'Needs attention'}
        </span>
      </div>
      <h2>{integration.title}</h2>
      {connected ? (
        <p>Working normally.</p>
      ) : (
        <p className="health-card-reason">{integration.reason}</p>
      )}
      {!connected && integration.settings_path && (
        <button className="btn btn--secondary health-card-fix-btn" onClick={() => onFix(integration.settings_path)}>
          {integration.key === 'ai_features' ? 'More info' : 'Fix this'}
        </button>
      )}
    </article>
  )
}

export default function SystemHealth() {
  const navigate = useNavigate()
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const integrations = status?.integrations || []
  const connectedCount = useMemo(
    () => integrations.filter((i) => i.connected).length,
    [integrations]
  )

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

  function handleFix(settingsPath) {
    navigate(settingsPath)
  }

  return (
    <div className="system-health-page">
      <header className="page-header system-health-header">
        <div>
          <p className="system-health-eyebrow">Advisor System Monitor</p>
          <h1 className="page-title">System Health</h1>
          <p className="page-subtitle">What's connected, what isn't, and how to fix it.</p>
        </div>
        <div className="panel system-health-summary">
          <SignalPulse color={connectedCount === integrations.length && integrations.length > 0 ? 'green' : 'red'} label={connectedCount === integrations.length && integrations.length > 0 ? 'All systems connected' : 'Needs setup'} />
          <strong>{loading ? '—' : `${connectedCount}/${integrations.length}`}</strong>
          <span>integrations connected</span>
        </div>
      </header>

      {error ? <div className="system-health-alert">{error}</div> : null}

      <section className="system-health-grid">
        {integrations.map((integration) => (
          <IntegrationCard key={integration.key} integration={integration} onFix={handleFix} />
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
            BookaBoost currently tracks per-lead cadence timestamps. A dedicated cadence job-run timestamp has not been added yet.
          </p>
        </div>
      </section>
    </div>
  )
}
