/**
 * SYSTEM HEALTH — what is connected, what is not, and what that costs you.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHY THIS PAGE WAS REWRITTEN
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * It read as a developer's status board. A tile could only be "Connected" or
 * "Needs attention", the summary was a bare "3/4 integrations connected", and
 * nothing on the page said what any of it meant for the business — whether an
 * unconnected integration stops texts going out today, or is a feature nobody
 * here uses.
 *
 * It now speaks the platform's severity vocabulary (`src/severity.js`, mirroring
 * `app/services/severity.py`), which distinguishes the two states a
 * connected/not-connected boolean cannot:
 *
 *   ACTION REQUIRED  this is stopping work right now
 *   CAN'T CHECK      we cannot see this — NOT a report that it is fine
 *
 * THE CADENCE PANEL IS THE CLEAREST CASE. It used to print a blank timestamp
 * with a paragraph explaining that blank was "by design". A blank field with a
 * footnote is not a status. It is now explicitly CAN'T CHECK, and the sentence
 * says what that means for the reader: follow-ups are running, we just cannot
 * yet prove each run finished.
 *
 * NOTHING ABOUT WHAT IS MEASURED CHANGED. Every fact on this page comes from
 * GET /health/advisor-status exactly as before. This pass changed how those
 * facts are worded and ranked, not which facts they are — and in particular it
 * did not turn any unknown into a green tick.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import {
  ACTION_REQUIRED, HEALTHY, LABELS, MEANINGS, UNAVAILABLE, rank, worst,
} from '../severity'
import '../styles/shared.css'
import './SystemHealth.css'

function formatDate(value) {
  if (!value) return null
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
      .format(new Date(value))
  } catch {
    return value
  }
}

/* A CONNECTED BOOLEAN IS NOT A SEVERITY, so the mapping is made here, once,
 * and explained.
 *
 * Messaging and calendar are ACTION REQUIRED when disconnected: an advisor
 * whose texting is not configured cannot do the job today, and softening that
 * to "attention" would let it sit unfixed.
 *
 * AI features are different. The tile reports whether the DEPLOYMENT has an AI
 * key, which is a platform fact identical for every customer and not something
 * this organization can act on. Unconfigured there is NEEDS ATTENTION — worth
 * knowing, not the reader's emergency — and its button says "More info"
 * rather than "Fix this" for exactly that reason. */
function severityOf(integration) {
  if (integration.connected) return HEALTHY
  return integration.key === 'ai_features' ? 'attention' : ACTION_REQUIRED
}

function Pill({ severity }) {
  return (
    <span className={`health-sev sv-${severity}`} title={MEANINGS[severity]}>
      {LABELS[severity]}
    </span>
  )
}

function IntegrationCard({ integration, onFix }) {
  const sv = severityOf(integration)
  const ok = sv === HEALTHY
  return (
    <article className={`panel health-card sv-${sv}`}>
      <div className="health-card-topline">
        <div className={`health-icon ${ok ? 'health-icon--online' : 'health-icon--offline'}`}
             aria-hidden="true">
          {ok ? '✓' : '!'}
        </div>
        <Pill severity={sv} />
      </div>
      <h2>{integration.title}</h2>
      {ok ? (
        <p>Working normally.</p>
      ) : (
        <p className="health-card-reason">{integration.reason}</p>
      )}
      {!ok && integration.settings_path && (
        <button className="btn btn--secondary health-card-fix-btn"
                onClick={() => onFix(integration.settings_path)}>
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

  /* THE PAGE'S OWN HEADLINE, rolled up the same way God Mode rolls up platform
     health: worst wins. "3 of 4 connected" was arithmetic; what an owner needs
     is whether anything is stopping work. */
  const overall = useMemo(() => {
    if (loading || error) return UNAVAILABLE
    if (!integrations.length) return UNAVAILABLE
    return worst(integrations.map(severityOf))
  }, [integrations, loading, error])

  const sorted = useMemo(
    () => [...integrations].sort((a, b) => rank(severityOf(a)) - rank(severityOf(b))),
    [integrations]
  )

  const needsWork = integrations.filter(i => severityOf(i) !== HEALTHY).length

  async function loadStatus() {
    setLoading(true)
    setError('')
    try {
      setStatus(await api.get('/health/advisor-status'))
    } catch (err) {
      setError(err.message || 'Could not load system health.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadStatus() }, [])

  function handleFix(settingsPath) {
    navigate(settingsPath)
  }

  const cadenceStamp = formatDate(status?.last_cadence_run)

  return (
    <div className="system-health-page">
      <header className="page-header system-health-header">
        <div>
          <p className="system-health-eyebrow">System Monitor</p>
          <h1 className="page-title">System Health</h1>
          <p className="page-subtitle">
            What's connected, what isn't, and what each one costs you while it
            stays that way.
          </p>
        </div>
        <div className={`panel system-health-summary sv-${overall}`}>
          <Pill severity={overall} />
          <strong>
            {loading ? '—'
              : needsWork === 0 ? 'All clear'
                : `${needsWork} to fix`}
          </strong>
          <span>{MEANINGS[overall]}</span>
        </div>
      </header>

      {error ? <div className="system-health-alert">{error}</div> : null}

      {/* WORST FIRST. A reader should not have to scan a grid to find the one
          thing that is broken. */}
      <section className="system-health-grid">
        {sorted.map((integration) => (
          <IntegrationCard key={integration.key} integration={integration}
                           onFix={handleFix} />
        ))}
      </section>

      <section className="panel cadence-health-panel">
        <div className="panel-header">
          <div>
            <h2 className="panel-title">Automated follow-ups</h2>
            <p className="cadence-health-subtitle">
              Reminders and follow-up messages that go out on a schedule.
            </p>
          </div>
          <Pill severity={cadenceStamp ? HEALTHY : UNAVAILABLE} />
        </div>
        <div className="cadence-health-value">
          {/* WAS: a blank timestamp with a paragraph explaining that blank was
              "by design". Blank plus a footnote is not a status — it reads as
              a broken field, which is the opposite of what it means.

              THE UNDERLYING FACT IS UNCHANGED AND STILL TRUE. The scheduler
              runs (`_cadence_loop` in app/main.py, hourly, with double-send
              protection) and `_get_last_cadence_run` returns None because no
              job-run record is kept. So the honest answer is "we cannot check
              this", stated as such, with what it does and does not mean. */}
          {cadenceStamp ? (
            <>
              <span className="mono">{cadenceStamp}</span>
              <p>Last completed run.</p>
            </>
          ) : (
            <p>
              Follow-ups run on a schedule in the background, and cadence
              progress is tracked on each individual lead. What is not recorded
              anywhere is whether each scheduled run finished — so we cannot
              show you a last-run time. <strong>This is not a report that
              follow-ups have stopped.</strong> It is a gap in what the platform
              can see, and closing it needs a record of each run and its outcome.
            </p>
          )}
        </div>
      </section>
    </div>
  )
}
