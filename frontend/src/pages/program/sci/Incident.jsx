/* SYSTEM-CHECK INCIDENT (Nov 27) - the page the alert text and email open.

   GET /program/incidents/{id}   one incident, for a manager of ITS workspace.

   What happened, who it affects, every family's planned and current time,
   the text and email outcome exactly as recorded (scheduled / accepted /
   delivered / failed), the recorded reasons, the cause or "Cause
   unconfirmed", what the system will do next, every notice sent about it
   (per channel) and the check history. Nothing on this page sends to a
   family; the next action opens an existing screen. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText, Icon, Loading } from './ui'

const STATE_TONE = { delivered: 'ok', accepted: 'warn', attempted: 'warn', scheduled: 'warn', failed: 'bad', none: 'bad',
  stopped: 'warn' }

function Outcome({ o }) {
  if (!o) return null
  return <span className={`sci-chip ${STATE_TONE[o.state] || ''}`}>{o.label}</span>
}

export default function Incident({ incidentId, openPerson, goTab, onBack }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    setErr('')
    const load = () => api.get(`/program/incidents/${encodeURIComponent(incidentId)}`)
      .then(x => { if (alive) setD(x) }).catch(e => { if (alive) setErr(errText(e)) })
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, 60000)
    return () => { alive = false; clearInterval(t) }
  }, [incidentId])

  if (err) return <div className="sci-alert" role="alert">{err}</div>
  if (!d) return <Loading label="Loading incident…" />

  const goNext = () => {
    const to = d.next_action?.to || ''
    const person = /[?&]person=([^&]+)/.exec(to)
    const tab = /[?&]tab=([^&]+)/.exec(to)
    if (person) openPerson(decodeURIComponent(person[1]))
    else if (tab) goTab(decodeURIComponent(tab[1]))
  }

  return (
    <section className="sci-panel" aria-labelledby="inc-title">
      <button type="button" className="sci-btn" onClick={onBack}><Icon name="back" />Back</button>
      <h2 id="inc-title" style={{ margin: '12px 0 4px' }}>{d.title}</h2>
      <p style={{ margin: '0 0 12px' }}>
        <span className={`sci-chip ${d.status === 'resolved' ? 'ok' : d.severity === 'outage' ? 'bad' : 'warn'}`}>
          {d.status === 'resolved' ? 'Resolved' : d.severity === 'action_needed' ? 'Action needed' : 'Open'}</span>{' '}
        <b>{d.number}</b> · {d.affected} {d.affected === 1 ? 'family' : 'families'} · started {d.started}
        {d.resolved && <> · resolved {d.resolved}</>}
      </p>

      <div className={`sci-alert ${d.act.needed ? '' : 'ok'}`} role="status" style={{ marginBottom: 12 }}>
        <b>Do you need to act?</b> {d.act.words}
        {d.next_action && <> <button type="button" className="sci-btn" onClick={goNext}>{d.next_action.label}</button></>}
      </div>

      <dl className="sci-dl">
        <dt>What happened</dt><dd>{d.happened}</dd>
        {d.resolution && <><dt>Outcome</dt><dd>{d.resolution}</dd></>}
        <dt>Cause</dt><dd>{d.cause.text}</dd>
        <dt>What the system will do next</dt><dd>{d.next}</dd>
        <dt>Actions already taken</dt>
        <dd>{d.actions.length ? <ul>{d.actions.map((a, i) => <li key={i}>{a}</li>)}</ul> : 'None recorded.'}</dd>
        <dt>Who follows up</dt><dd>{d.owner}</dd>
        <dt>Next update</dt><dd>{d.next_update}</dd>
      </dl>
      <p className="sci-muted">{d.controls_note}</p>

      {d.people.length > 0 && <>
        <h3>Affected families</h3>
        <div className="sci-tablewrap">
          <table className="sci-table">
            <thead><tr><th>Family</th><th>Planned first text</th><th>Now set for</th><th>Text</th><th>Email</th></tr></thead>
            <tbody>
              {d.people.map(p => (
                <tr key={p.lead_id}>
                  <td><button type="button" className="sci-link" onClick={() => openPerson(p.lead_id)}>{p.name}</button></td>
                  <td>{p.original || '-'}</td>
                  <td>{p.current || '-'}{p.reserved ? ' (slot reserved)' : ''}</td>
                  <td><Outcome o={p.sms} /></td>
                  <td><Outcome o={p.email} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>Recorded reasons (send log)</h3>
        {d.people.slice(0, 10).map(p => (
          <div key={p.lead_id} style={{ marginBottom: 8 }}>
            <b>{p.name}</b>
            <ul>{p.reasons.map((r, i) => (
              <li key={i}>{r.at} · {r.channel} {r.outcome}: {r.plain} <code style={{ fontSize: 11 }}>{r.code}</code></li>
            ))}</ul>
          </div>
        ))}
      </>}

      <h3>Notices sent about this incident</h3>
      {d.notices.length === 0 ? <p>None.</p> : (
        <table className="sci-table">
          <thead><tr><th>When</th><th>Event</th><th>Channel</th><th>To</th><th>Result</th></tr></thead>
          <tbody>{d.notices.map((n, i) => (
            <tr key={i}><td>{n.at}</td><td>{n.event}</td><td>{n.channel === 'sms' ? 'Text' : 'Email'}</td><td>{n.to}</td>
              <td>{n.state}{n.provider_status ? ` · ${n.provider_status}` : ''}{n.reason ? ` · ${n.reason}` : ''}</td></tr>
          ))}</tbody>
        </table>
      )}

      <h3>History</h3>
      <ul>{d.history.map((h, i) => (
        <li key={i}>{h.at_ct} · {h.type === 'check' ? `${h.title}: ${h.detail}` : h.type === 'notice'
          ? `Notice (${h.event}) · ${(h.channels || []).join(' + ')} · ${(h.states || []).join(', ')}` : (h.note || h.event)}</li>
      ))}</ul>
    </section>
  )
}
