import { useEffect, useState } from 'react'
import { api } from '../api/client'
import './CertificationPanel.css'

const STEPS = [
  { key: 'solicited', label: 'Solicited' },
  { key: 'contacted', label: 'Contacted' },
  { key: 'booked', label: 'Booked' },
  { key: 'confirmed', label: 'Confirmed' },
]

/**
 * The Certified Appointment pipeline - per Mike's exact, direct
 * definition: "we've already solicited. We had to contact them. They
 * booked the appointment. We confirmed. Now we're just waiting for
 * them to come in." A real, auditable sequence of events, never an
 * AI-judged score - every step shown here reflects an actual
 * underlying fact (a message was sent, a reply came back, a real
 * booking exists, confirmation was explicitly recorded).
 */
export default function CertificationPanel({ leadId }) {
  const [status, setStatus] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState('')

  function load() {
    api.get(`/leads/${leadId}/certification`).then(setStatus).catch(() => setStatus(null))
  }

  useEffect(() => { load() }, [leadId])

  async function handleConfirm() {
    setConfirming(true)
    setError('')
    try {
      const updated = await api.post(`/leads/${leadId}/certification/confirm`, {})
      setStatus(updated)
    } catch (err) {
      setError(err.message || 'Could not confirm this appointment.')
    } finally {
      setConfirming(false)
    }
  }

  if (!status) return null

  const canConfirm = status.current_step === 'booked'

  return (
    <section className="panel certification-panel">
      <div className="panel-header">
        <h2 className="panel-title">Certified Appointment</h2>
        {status.is_certified && <span className="badge badge--green">Certified — Waiting</span>}
      </div>

      <div className="certification-steps">
        {STEPS.map((step, idx) => {
          const isDone = status.steps_completed[step.key]
          const isCurrent = status.current_step === step.key
          return (
            <div key={step.key} className="certification-step-wrap">
              <div
                className={`certification-step ${isDone ? 'certification-step--done' : ''} ${isCurrent ? 'certification-step--current' : ''}`}
              >
                <span className="certification-step-dot">{isDone ? '✓' : idx + 1}</span>
                <span className="certification-step-label">{step.label}</span>
              </div>
              {idx < STEPS.length - 1 && <div className={`certification-step-line ${isDone ? 'certification-step-line--done' : ''}`} />}
            </div>
          )
        })}
      </div>

      {canConfirm && (
        <div className="certification-confirm-row">
          <p className="certification-confirm-hint">
            Booked — confirm with them, then mark it confirmed here.
          </p>
          <button className="btn btn--primary" onClick={handleConfirm} disabled={confirming}>
            {confirming ? 'Confirming…' : 'Mark confirmed'}
          </button>
        </div>
      )}

      {status.current_step === null && (
        <p className="certification-empty-hint">Nothing sent to this lead yet.</p>
      )}

      {error && <div className="compose-error">{error}</div>}
    </section>
  )
}
