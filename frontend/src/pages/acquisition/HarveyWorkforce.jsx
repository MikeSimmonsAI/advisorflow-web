/**
 * HARVEY AI WORKFORCE panel (#452). Lives inside the existing Acquisition
 * Control Center and Worker Health pages — same cards, same styling.
 * Every control calls the server and every number is the server's: no
 * animated "online" lights. The workforce researches, verifies, drafts and
 * reads replies; it never sends — a person approves each message.
 */
import { Fragment, useState } from 'react'
import { api } from '../../api/client'
import { Confirm, Dot, Empty, Icon, Loading, Stat, errText, useLoad, useToast, when } from './ui'

const STATUS = { running: ['green', 'Running'], paused: ['amber', 'Paused'], stopped: ['gray', 'Stopped'],
                 emergency_stopped: ['rose', 'Emergency stopped'] }
const WSTATE = { configured: ['gray', 'Configured'], ready: ['blue', 'Ready'], running: ['green', 'Running'],
                 paused: ['amber', 'Paused'], blocked: ['amber', 'Blocked'], failed: ['rose', 'Failed'],
                 recovering: ['amber', 'Recovering'], completed: ['green', 'Completed'] }

export function HarveyWorkforcePanel({ compact = false }) {
  const [h, err, reload] = useLoad('/god/acquisition/workforce')
  const [busy, setBusy] = useState(null)
  const [fail, setFail] = useState(null)
  const [sure, setSure] = useState(null)
  const [toast, say] = useToast()
  if (err) return <section className="aq-card"><h2>Harvey AI Workforce</h2><div className="aq-err">{errText(err)}</div></section>
  if (!h) return <section className="aq-card"><Loading label="Loading the AI workforce…" /></section>
  const act = async (action, body = {}, ok) => {
    setBusy(action); setFail(null); setSure(null)
    try { const r = await api.post(`/god/acquisition/workforce/${action}`, body); say(ok || 'Done'); reload(); return r }
    catch (e) { setFail(errText(e)) } finally { setBusy(null) }
  }
  const toggle = async (c) => {
    setFail(null)
    try { await api.put(`/god/acquisition/workforce/clients/${c.organization_id}`, { enabled: !c.enabled }); say(c.enabled ? 'Switched off' : 'Switched on'); reload() }
    catch (e) { setFail(errText(e)) }
  }
  const [tone, label] = STATUS[h.status] || ['gray', h.status]
  const running = h.status === 'running', paused = h.status === 'paused', stopped = h.status === 'stopped', estop = h.status === 'emergency_stopped'
  const real = h.results.real, prac = h.results.practice, pool = h.results.master_pool
  return (
    <section className="aq-card" style={{ marginBottom: 14 }}>{toast}
      <div className="aq-card__head"><h2><Icon name="spark" size={18} /> Harvey AI Workforce</h2>
        <span><Dot tone={tone}>{label}</Dot> <Dot tone={h.mode === 'live' ? 'violet' : 'blue'}>{h.mode === 'live' ? 'Live mode' : 'Practice mode'}</Dot></span></div>
      <p className="hb-muted" style={{ marginTop: 0 }}>Finds, researches and verifies businesses, drafts outreach, reads replies and sets up bookings on each client's real calendar. It never sends — every message waits for a person. {h.reason ? `Last change: ${h.reason}.` : ''}</p>
      <div className="aq-actions" style={{ marginTop: 0 }}>
        {(stopped || estop) && <button className="aq-btn aq-btn--primary" disabled={!!busy} onClick={() => act('start', { mode: 'practice' }, 'Started in practice mode')}><Icon name="check" size={16} />Start (practice)</button>}
        {(stopped || estop) && <button className="aq-btn" disabled={!!busy} onClick={() => setSure('live')}>Start (live)</button>}
        {running && <button className="aq-btn" disabled={!!busy} onClick={() => act('pause', {}, 'Paused')}><Icon name="pause" size={16} />Pause</button>}
        {paused && <button className="aq-btn aq-btn--primary" disabled={!!busy} onClick={() => act('resume', {}, 'Resumed')}>Resume</button>}
        {(running || paused) && <button className="aq-btn" disabled={!!busy} onClick={() => act('stop', {}, 'Stopped')}>Stop</button>}
        {!estop && <button className="aq-btn aq-btn--danger" disabled={!!busy} onClick={() => setSure('estop')}>Emergency stop</button>}
        <button className="aq-btn" disabled={!!busy || estop} onClick={async () => { const r = await act('practice-test', {}, 'Practice test ran'); if (r) say(`Practice test: ${r.summary.ran} jobs ran`) }}>{busy === 'practice-test' ? 'Running…' : 'Run practice test'}</button>
        <button className="aq-btn" onClick={reload}>Refresh</button>
      </div>
      {sure === 'estop' && <Confirm danger text="Emergency stop: every queued job is cancelled and nothing runs until someone presses Start." confirmLabel="Emergency stop" busy={busy === 'emergency-stop'} onCancel={() => setSure(null)} onConfirm={() => act('emergency-stop', { reason: 'Emergency stop from the Control Center' }, 'Emergency stopped')} />}
      {sure === 'live' && <Confirm text="Live mode lets the workforce research and draft for real prospects. Nothing is sent without a person pressing Send, and the server's sending switch still applies." confirmLabel="Start live" busy={busy === 'start'} onCancel={() => setSure(null)} onConfirm={() => act('start', { mode: 'live' }, 'Started in live mode')} />}
      {fail && <div className="aq-err">{fail}</div>}
      <div className="hb-stats" style={{ marginTop: 12 }}>
        <Stat icon="clock" tone="blue" label="Last pass" value={h.last_pass_at ? when(h.last_pass_at) : 'Never'} sub={running && h.next_pass_at ? `Next ${when(h.next_pass_at)}` : 'Runs every 5 min while Running'} />
        <Stat icon="list" tone="violet" label="Queue" value={h.queue_depth} sub={`${h.jobs_last_24h} jobs in 24h · limit ${h.limits.daily_job_limit}`} />
        <Stat icon="mail" tone={h.pending_approvals ? 'amber' : 'gray'} label="Waiting for approval" value={h.pending_approvals} sub="Drafts — nothing sent" />
        <Stat icon="shield" tone={h.failed_jobs ? 'rose' : 'green'} label="Failed jobs" value={h.failed_jobs} sub="Moved to the failed queue" />
        <Stat icon="database" tone="green" label="Researched (pool)" value={`${pool.researched} / ${pool.businesses}`} sub={`${pool.verified_emails} verified emails`} />
        <Stat icon="calendar" tone="amber" label="Appointments confirmed" value={real.appointments_confirmed} sub={`Practice: ${prac.appointments_confirmed}`} />
        <Stat icon="dollar" tone="gray" label="Metered spend (24h)" value={'$' + Number(h.usage.metered_cost_usd_today || 0).toFixed(2)} sub={h.ai.available ? h.ai.reason : 'AI calls: 0 allowed'} />
      </div>
      {!compact && <>
        <h3 style={{ margin: '14px 0 6px' }}>Workers</h3>
        {h.workers.map(w => { const [t, l] = WSTATE[w.state] || ['gray', w.state]
          return <div className="aq-row" key={w.key}><div className="aq-row__main"><b>{w.label}</b>
            <span>{w.counts.done} done · {w.counts.queued} queued · {w.counts.blocked} blocked · {w.counts.dead} failed{w.last_success ? ` · last success ${when(w.last_success)}` : ''}{w.last_failure ? ` · last failure ${when(w.last_failure)}` : ''}</span>
            {w.last_error && <span className="aq-err" style={{ display: 'block', marginTop: 4 }}>{w.last_error}</span>}
            {w.blockers.length > 0 && <ul className="hb-muted" style={{ margin: '4px 0 0 18px', fontSize: 12 }}>{w.blockers.map(b => <li key={b}>{b}</li>)}</ul>}</div>
            <Dot tone={t}>{l}</Dot></div> })}
        <h3 style={{ margin: '14px 0 6px' }}>Service companies the workforce works for</h3>
        {h.clients.length ? h.clients.map(c => <div className="aq-row" key={c.organization_id}><div className="aq-row__main"><b>{c.name}</b>
          <span>{c.category || 'service business'} · up to {c.daily_job_limit} jobs/day{c.client_paused ? ' · client outreach paused' : ''}</span></div>
          <button className={`aq-btn aq-btn--sm ${c.enabled ? '' : 'aq-btn--primary'}`} onClick={() => toggle(c)}>{c.enabled ? 'Switch off' : 'Switch on'}</button></div>)
          : <Empty icon="store" title="No service companies yet" />}
        <h3 style={{ margin: '14px 0 6px' }}>Results (real vs practice)</h3>
        <dl className="aq-dl">
          {[['Verified contacts', 'verified_contacts'], ['Drafts prepared', 'drafts_prepared'], ['Messages actually sent', 'messages_sent'], ['Replies received', 'replies'],
            ['Interested', 'interested'], ['Appointments confirmed', 'appointments_confirmed'], ['Appointments held', 'appointments_held'], ['Estimates', 'estimates'], ['Contracts won', 'won']]
            .map(([l, k]) => <Fragment key={k}><dt>{l}</dt><dd>{real[k]} real · {prac[k]} practice</dd></Fragment>)}
          <dt>Monthly contract value</dt><dd>${Number(real.monthly_value || 0).toLocaleString()}</dd>
        </dl>
        {h.recent_jobs?.length > 0 && <details style={{ marginTop: 10 }}><summary>Recent jobs ({h.recent_jobs.length})</summary>
          {h.recent_jobs.map(j => <div className="aq-row" key={j.id}><div className="aq-row__main"><b>{j.worker} · {j.status}</b>
            <span>{j.result?.reason || j.result?.status || j.result?.note || j.result?.classification || j.error || ''}{j.is_practice ? ' · practice' : ''} · {when(j.finished_at || j.created_at)}</span></div></div>)}
        </details>}
      </>}
    </section>
  )
}
