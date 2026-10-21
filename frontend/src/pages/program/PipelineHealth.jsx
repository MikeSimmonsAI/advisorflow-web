/* Pipeline health (Phase Nine): where a family's reply is in the pipeline
   and where it is stuck. Stage counts for the last 24 hours, then each
   problem in plain words with how many, since when, and what to do.
   Read-only, no family details - GET /program/command/pipeline-health,
   refreshed every minute. Uses the SelfCheck look so it sits beside it. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import './SelfCheck.css'

const STAGES = [
  ['received', 'Received'], ['processed', 'Processed'], ['ai_answered', 'AI answered'],
  ['human_action_required', 'Need a person'], ['notification_attempted', 'Alerts sent'],
  ['notification_delivered', 'Alerts delivered'], ['notification_failed', 'Alerts failed'], ['completed', 'Completed'],
]

const since = iso => {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function PipelineHealth({ hours = 24 }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [open, setOpen] = useState(null)
  const load = useCallback(() => api.get(`/program/command/pipeline-health?hours=${hours}`)
    .then(r => { setD(r); setErr(null) })
    // 403 = this login may not see it: show nothing. Anything else is itself a problem worth showing.
    .catch(e => setErr(e?.response?.status === 403 ? null : 'Pipeline check could not run - try again in a minute.')), [hours])
  useEffect(() => {
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, 60000)
    return () => clearInterval(t)
  }, [load])
  if (!d) return err ? <div className="sc bad" role="status"><div className="sc-head"><span className="sc-dot" aria-hidden="true" /><b>{err}</b></div></div> : null
  const serious = d.problems.filter(p => p.severity !== 'low')
  return (
    <div className={`sc ${d.ok ? 'ok' : 'bad'}`} role="status" aria-label="Pipeline health">
      <div className="sc-head">
        <span className="sc-dot" aria-hidden="true" />
        <b>{d.ok ? 'Pipeline: nothing is stuck' : `Pipeline: ${serious.length} place${serious.length === 1 ? '' : 's'} need${serious.length === 1 ? 's' : ''} attention`}</b>
        <span className="sc-at">last {d.window_hours} h · checked {since(d.generated_at)}</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: 8, fontSize: 13 }}>
        {STAGES.map(([k, label]) => (
          <span key={k}><b>{d.funnel[k] ?? 0}</b> {label}</span>
        ))}
      </div>
      {d.problems.length > 0 && (
        <ul className="sc-list">
          {d.problems.map((p, i) => (
            <li key={`${p.key}-${i}`}>
              <b>{p.count > 1 || p.stage !== 'configuration' ? `${p.count} · ` : ''}{p.what}</b>
              {p.oldest_at ? <span className="sc-people"> (oldest {since(p.oldest_at)})</span> : null}
              <div style={{ fontSize: 13 }}>What to do: {p.action}</div>
              {(p.reasons?.length || p.example_ids?.length) ? (
                <button type="button" className="sc-link" style={{ fontSize: 12 }} onClick={() => setOpen(open === `${p.key}-${i}` ? null : `${p.key}-${i}`)}>
                  {open === `${p.key}-${i}` ? 'Hide details' : 'Details'}
                </button>
              ) : null}
              {open === `${p.key}-${i}` && (
                <div style={{ fontSize: 12, opacity: 0.85 }}>
                  {p.reasons?.length ? <div>Reason: {p.reasons.join('; ')}</div> : null}
                  {p.example_ids?.length ? <div>{p.id_type} ids: <code>{p.example_ids.join(', ')}</code></div> : null}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
