/* OPPORTUNITY READINESS — where a property stands on the road to a deal, and
 * the one thing a person should do next (app/services/evosense/readiness.py).
 *
 *   ReadinessPanel  one property: the six steps, what is missing, the next action
 *   ActFirst        the "act first" list for the EvoSense home
 *   FunnelPanel     where opportunities stop, counted from records
 *
 * Every word comes from the server. A step with no evidence says "not yet";
 * nothing here fills in a value. Reads only — no button here sends anything.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Empty, Panel, Tag } from '../ds/ds'

const STATE = {
  done: ['✓', 'Done'], waiting: ['…', 'Waiting'], todo: ['○', 'To do'],
  blocked: ['!', 'Blocked'], stop: ['–', 'Stops here'],
}
const STEP_LABEL = {
  evidence: 'Evidence', owner: 'Owner', contact: 'Reach the owner', conversation: 'First conversation',
  qualified: 'Qualified', deal: 'Deal',
}

function StepRow({ s }) {
  const [mark, word] = STATE[s.state] || ['?', s.state]
  return (
    <li className={`evo-ready__step is-${s.state}`} style={{ display: 'grid', gridTemplateColumns: '28px 1fr', gap: 8, padding: '8px 0', borderTop: '1px solid var(--evo-line, #e6e8ec)' }}>
      <span aria-label={word} title={word} style={{ fontWeight: 700, textAlign: 'center' }}>{mark}</span>
      <div>
        <div><strong>{s.label}</strong> <span className="evo-muted">· {word}</span></div>
        {s.detail ? <div className="evo-muted">{s.detail}</div> : null}
        {s.key === 'evidence' && (s.signals || []).length ? (
          <div className="evo-muted" style={{ marginTop: 4 }}>
            {s.signals.map((g) => `${g.label}${g.scored ? '' : ' (shown, not scored)'} — ${(g.sources || []).join(', ')}`).join(' · ')}
          </div>
        ) : null}
        {s.key === 'evidence' && (s.missing || []).length ? (
          <div className="evo-muted" style={{ marginTop: 4 }}>Not known: {s.missing.join(' · ')}</div>
        ) : null}
        {s.key === 'owner' && s.owner ? (
          <div className="evo-muted" style={{ marginTop: 4 }}>
            {s.owner.type}{s.owner.mailing ? ` · mailing ${s.owner.mailing}` : ''}
            {(s.owner.flags || []).length ? ` · flags: ${s.owner.flags.join(', ').toLowerCase().replace(/_/g, ' ')}` : ''}
          </div>
        ) : null}
        {s.key === 'contact' && (s.ways || []).length ? (
          <ul className="evo-muted" style={{ margin: '4px 0 0 16px' }}>{s.ways.map((w) => <li key={w}>{w}</li>)}</ul>
        ) : null}
        {s.key === 'qualified' && (s.facts || []).length ? (
          <ul style={{ margin: '4px 0 0 16px' }}>
            {s.facts.map((f) => (
              <li key={f.fact}><strong>{f.fact.replace(/_/g, ' ')}:</strong> {f.value}
                {f.quote ? <span className="evo-muted"> — “{f.quote}” ({f.basis})</span> : null}</li>
            ))}
          </ul>
        ) : null}
        {s.key === 'deal' && (s.missing || []).length ? (
          <div className="evo-muted" style={{ marginTop: 4 }}>Before any offer: {s.missing.join(' · ')}</div>
        ) : null}
      </div>
    </li>
  )
}

export function ReadinessPanel({ propertyId, refreshKey }) {
  const [r, setR] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let live = true
    api.get(`/wholesale/evosense/properties/${propertyId}/readiness`)
      .then((x) => { if (live) { setR(x); setErr(null) } })
      .catch((e) => { if (live) setErr(errText(e)) })
    return () => { live = false }
  }, [propertyId, refreshKey])
  if (err) return <Panel title="Road to a deal" id="readiness"><Alert>{err}</Alert></Panel>
  if (!r) return null
  return (
    <Panel title="Road to a deal" id="readiness" hint="From records on file · nothing filled in">
      {r.next ? (
        <p style={{ margin: '0 0 8px' }}>
          <Tag kind={r.next.stopped ? 'danger' : 'info'}>Next: {STEP_LABEL[r.next.step] || r.next.label}</Tag>{' '}
          <strong>{r.next.action}</strong>
        </p>
      ) : null}
      {(r.work || []).length ? (
        <p className="evo-muted" style={{ margin: '0 0 8px' }}>
          In the work queue: {r.work.map((w) => w.title).join(' · ')} — <Link className="evo-link" to="/wholesale/exceptions">open queue</Link>
        </p>
      ) : null}
      <ol style={{ listStyle: 'none', padding: 0, margin: 0 }}>
        {r.steps.map((s) => <StepRow key={s.key} s={s} />)}
      </ol>
    </Panel>
  )
}

export function ActFirst({ limit = 8 }) {
  const [q, setQ] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.get(`/wholesale/evosense/opportunity-queue?limit=${limit}`)
      .then((x) => { setQ(x); setErr(null) }).catch((e) => setErr(errText(e)))
  }, [limit])
  if (err) return <Panel title="Act first"><Alert>{err}</Alert></Panel>
  if (!q) return null
  const counts = Object.entries(q.by_step || {}).map(([k, n]) => `${n} ${(STEP_LABEL[k] || k).toLowerCase()}`).join(' · ')
  return (
    <Panel title="Act first" count={q.total} className="evo-gapb"
           hint={counts ? `Waiting on: ${counts}` : 'Most advanced first, then by score'}>
      {!q.items.length ? (
        <Empty title="Nothing to act on">No property has passed its search's threshold without being worked.</Empty>
      ) : (
        <ul className="evo-needlist">
          {q.items.map((r) => (
            <li key={r.property_id}>
              <Link className="evo-needrow" to={`/wholesale/evosense/property/${r.property_id}`}>
                <span className="evo-needrow__main">
                  <span className="evo-prop__addr">{r.address}</span>
                  <span className="evo-prop__sub">
                    Score {r.score ?? '—'} · {r.county || '—'} · waiting on {(STEP_LABEL[r.next.step] || r.next.label).toLowerCase()}
                    {(r.work || []).length ? ' · in the work queue' : ''}
                  </span>
                </span>
                <span className="evo-next">{r.next.action} →</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

export function FunnelPanel() {
  const [f, setF] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.get('/wholesale/evosense/funnel').then((x) => { setF(x); setErr(null) }).catch((e) => setErr(errText(e)))
  }, [])
  if (err) return <Panel title="Where opportunities stop"><Alert>{err}</Alert></Panel>
  if (!f) return null
  return (
    <Panel title="Where opportunities stop" hint="Counted from records · '—' means the system does not track it here">
      {f.stuck_at ? (
        <p style={{ margin: '0 0 8px' }}><Tag kind="danger">Biggest drop</Tag>{' '}
          {f.stuck_at.from} ({f.stuck_at.from_count}) → {f.stuck_at.to} ({f.stuck_at.to_count})</p>
      ) : null}
      <table className="evo-table">
        <tbody>
          {f.stages.map((s) => (
            <tr key={s.key}>
              <td>{s.label}</td>
              <td className="is-num" title={s.unavailable || undefined}>{s.count == null ? '—' : s.count.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
