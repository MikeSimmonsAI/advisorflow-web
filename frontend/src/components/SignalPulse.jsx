import './SignalPulse.css'

/**
 * The signature element: a small radar-style pulse used anywhere
 * something is "live" or "hot" - a hot lead reply, an active cadence,
 * a connected phone line. Ties back to the "Tesla dashboard" brief:
 * telemetry, not decoration.
 */
export default function SignalPulse({ color = 'green', size = 8, label }) {
  return (
    <span className="signal-pulse-wrap" role="status" aria-label={label || 'active'}>
      <span className={`signal-pulse signal-pulse--${color}`} style={{ width: size, height: size }}>
        <span className="signal-pulse-ring" />
      </span>
      {label && <span className="signal-pulse-label">{label}</span>}
    </span>
  )
}
