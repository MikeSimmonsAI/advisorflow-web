/**
 * HARVEY AI WORKFORCE panel (#452, corrected #506). Lives inside the existing
 * Acquisition Control Center (Control Center card + Worker Health page), in
 * its purple/navy design. Compact: one status strip, one control row, one
 * even metric grid. Every value is the server's; every button is a real
 * server action. Scheduler, AI model, email and calendar are shown apart —
 * a running scheduler does not mean the AI is working.
 */
import { Fragment, useState } from 'react'
import { api } from '../../api/client'
import { Confirm, Empty, Icon, Loading, errText, useLoad, useToast } from './ui'

const TONE = { Permitted: 'green', Off: 'gray', 'Not connected': 'amber', 'None set': 'amber', Running: 'green', Connected: 'green', Ready: 'green', Available: 'green', Live: 'violet', Practice: 'blue',
               Paused: 'amber', Disabled: 'gray', 'Not configured': 'gray', Configured: 'amber', Stopped: 'gray',
               Blocked: 'amber', Degraded: 'amber', Unavailable: 'rose', Error: 'rose', 'Stopped (emergency)': 'rose', Enabled: 'violet' }

// "11:12 PM" — the full date goes in the hover title, never in the card.
export function shortTime(iso) {
  if (!iso) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return '—'
  const t = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return new Date().toDateString() === d.toDateString() ? t : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${t}`
}
const fullTime = iso => (iso ? new Date(/[zZ]$/.test(iso) ? iso : iso + 'Z').toLocaleString() : '')

function Chip({ label, value, title }) {
  return <span className={`hw-chip hw-chip--${TONE[value] || 'gray'}`} title={title || ''}><small>{label}</small><b>{value}</b></span>
}

function Metric({ label, value, sub, tone = 'violet', href, title }) {
  const body = <><span className="hw-metric__label">{label}</span><b>{value ?? '—'}</b>{sub && <small>{sub}</small>}</>
  return href ? <a className={`hw-metric hw-metric--${tone}`} href={href} title={title}>{body}</a>
    : <div className={`hw-metric hw-metric--${tone}`} title={title}>{body}</div>
}

export function HarveyWorkforcePanel({ compact = false }) {
  const [h, err, reload] = useLoad('/god/acquisition/workforce')
  const [busy, setBusy] = useState(null)
  const [fail, setFail] = useState(null)
  const [sure, setSure] = useState(null)
  const [show, setShow] = useState(null)       // research bucket/reason drill-down
  const [toast, say] = useToast()
  if (err) return <section className="aq-card hw"><h2>Harvey AI Workforce</h2><div className="aq-err">{errText(err)}</div></section>
  if (!h) return <section className="aq-card hw"><Loading label="Loading the AI workforce…" /></section>
  const act = async (action, body = {}, ok) => {
    setBusy(action); setFail(null); setSure(null)
    try { const r = await api.post(`/god/acquisition/workforce/${action}`, body); say(ok || 'Done'); reload(); return r }
    catch (e) { setFail(errText(e)) } finally { setBusy(null) }
  }
  const cap = h.capabilities, rc = h.research.counts, real = h.results.real, prac = h.results.practice
  const running = h.status === 'running', paused = h.status === 'paused', estop = h.status === 'emergency_stopped'
  const stoppedish = h.status === 'stopped' || estop
  const sched = cap.scheduler.state, campaign = cap.campaign.state
  return (
    <section className="aq-card hw" style={{ marginBottom: 14 }}>{toast}
      <div className="hw-head">
        <h2><Icon name="spark" size={17} /> Harvey Workforce</h2>
        <span className="hw-scope" title="Counts cover every service company; TEST companies and practice records are kept out of real results">{h.scope}</span>
      </div>
      <div className="hw-chips">
        <Chip label="Scheduler" value={sched} title={h.reason || ''} />
        <Chip label="Mode" value={h.mode === 'live' ? 'Live' : 'Practice'} title={cap.campaign.detail} />
        <Chip label="AI model" value={cap.ai_model.state} title={cap.ai_model.detail} />
        <Chip label="Email sending" value={!cap.server_sending ? (cap.email.state === 'Ready' ? 'Enabled' : 'Disabled')
          : cap.server_sending.state === 'Permitted' && cap.sending_provider?.state === 'Connected' ? 'Permitted' : 'Off'}
          title={cap.server_sending ? `Server switch: ${cap.server_sending.state} · Mailbox: ${cap.sending_provider?.state} · Practice inboxes: ${cap.practice_recipients?.state}. A person approves every send.` : cap.email.detail} />
        <span className="hw-chip hw-chip--plain" title={fullTime(h.last_pass_at)}><small>Last pass</small><b>{shortTime(h.last_pass_at)}</b>
          {running && h.next_pass_at && <em>Next: {shortTime(h.next_pass_at)}</em>}</span>
        <span className="hw-chip hw-chip--plain" title={fullTime(h.last_success_at)}><small>Last successful job</small><b>{shortTime(h.last_success_at)}</b></span>
      </div>
      {cap.ai_model.state !== 'Connected' && <p className="hw-note">{cap.ai_model.state === 'Disabled' ? cap.ai_model.detail : `AI model unavailable — using deterministic template fallback. ${cap.ai_model.detail}`}</p>}
      <div className="hw-controls">
        {stoppedish && <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!!busy} onClick={() => act('start', { mode: 'practice' }, 'Started in practice mode')}>Start</button>}
        {stoppedish && <button className="aq-btn aq-btn--sm" disabled={!!busy} onClick={() => setSure('live')}>Start live</button>}
        {running && <button className="aq-btn aq-btn--sm" disabled={!!busy} onClick={() => act('pause', {}, 'Paused')}>Pause</button>}
        {paused && <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!!busy} onClick={() => act('resume', {}, 'Resumed')}>Resume</button>}
        {(running || paused) && <button className="aq-btn aq-btn--sm" disabled={!!busy} onClick={() => act('stop', {}, 'Stopped')}>Stop</button>}
        {!estop && <button className="aq-btn aq-btn--sm aq-btn--danger" disabled={!!busy} onClick={() => setSure('estop')}>Emergency stop</button>}
        {!estop && <button className="aq-btn aq-btn--sm" disabled={!!busy} onClick={async () => { const r = await act('practice-test', {}, 'Practice test ran'); if (r) say(`Practice test: ${r.summary.ran} jobs ran`) }}>{busy === 'practice-test' ? 'Running…' : 'Run practice test'}</button>}
        <button className="aq-btn aq-btn--sm" onClick={reload}>Refresh</button>
      </div>
      {sure === 'estop' && <Confirm danger text="Emergency stop: every queued job is cancelled and nothing runs until someone presses Start." confirmLabel="Emergency stop" busy={busy === 'emergency-stop'} onCancel={() => setSure(null)} onConfirm={() => act('emergency-stop', { reason: 'Emergency stop from the Control Center' }, 'Emergency stopped')} />}
      {sure === 'live' && <Confirm text="Live mode lets the workforce research and draft for real prospects. Nothing is sent without a person pressing Send, and the server's sending switch still applies." confirmLabel="Start live" busy={busy === 'start'} onCancel={() => setSure(null)} onConfirm={() => act('start', { mode: 'live' }, 'Started in live mode')} />}
      {fail && <div className="aq-err">{fail}</div>}

      <div className="hw-grid">
        <Metric label="Total prospects" value={rc.imported} sub="In the master pool" href="/acquisition-ops/master" />
        <Metric tone="green" label="Website research completed" value={rc.website_read} sub={`of ${rc.research_attempted} attempted`} title="A research job read the business's own website" />
        <Metric tone={rc.research_incomplete + rc.research_blocked ? 'amber' : 'gray'} label="Website research blocked" value={rc.research_incomplete + rc.research_blocked + rc.research_failed}
          sub={(h.research.blocked_reasons[0] && `${h.research.blocked_reasons[0].count} · ${h.research.blocked_reasons[0].label}`) || 'None'} />
        <Metric tone={rc.email_verified ? 'green' : 'gray'} label="Verified contact emails" value={rc.email_verified} sub={`${rc.outreach_ready} ready to email`} />
        <Metric tone={real.drafts_pending + prac.drafts_pending ? 'amber' : 'gray'} label="Drafts pending approval" value={real.drafts_pending + prac.drafts_pending} sub={`${prac.drafts_pending} practice`} />
        <Metric tone="blue" label="Messages actually sent" value={real.messages_sent} sub={`${prac.messages_sent} practice`} />
        <Metric tone="blue" label="Replies received" value={real.replies} sub={`${prac.replies} practice`} />
        <Metric tone={real.appointments_confirmed ? 'green' : 'gray'} label="Confirmed appointments" value={real.appointments_confirmed} sub={`${prac.appointments_confirmed} practice`} />
      </div>

      {!compact && <>
        <h3 className="hw-h3">Research — what actually happened</h3>
        <div className="hw-research">
          {[['Imported', rc.imported], ['Queued', rc.research_queued], ['Attempted', rc.research_attempted], ['Website read', rc.website_read, 'website_read'],
            ['Incomplete', rc.research_incomplete, 'incomplete'], ['Blocked', rc.research_blocked, 'blocked'], ['Failed', rc.research_failed, 'failed'],
            ['Contact identified', rc.contact_identified], ['Email verified', rc.email_verified], ['Ready to email', rc.outreach_ready]]
            .map(([l, v, b]) => <button key={l} type="button" className={`hw-pill ${show === b ? 'is-on' : ''}`} disabled={!b || !v} onClick={() => setShow(show === b ? null : b)}><b>{v}</b>{l}</button>)}
        </div>
        {h.research.blocked_reasons.length > 0 && <div className="hw-reasons">
          {h.research.blocked_reasons.map(r => <button key={r.code} type="button" className={`hw-reason ${show === 'r:' + r.code ? 'is-on' : ''}`} onClick={() => setShow(show === 'r:' + r.code ? null : 'r:' + r.code)}>{r.count} · {r.label}</button>)}
        </div>}
        {show && <ResearchRows filter={show} onChanged={reload} />}

        <h3 className="hw-h3">Capabilities</h3>
        <div className="hw-caps">
          {[['Scheduler', cap.scheduler], ['Website research', cap.website_research], ['AI model', cap.ai_model],
            ...(cap.server_sending ? [['Drafting', cap.outreach_drafting], ['Server sending', cap.server_sending], ['Mailbox', cap.sending_provider], ['Practice inboxes', cap.practice_recipients]] : [['Email', cap.email]]),
            ['Calendar', cap.calendar], ['Campaign', cap.campaign], ['Paid discovery', cap.paid_discovery]].map(([l, c]) =>
            <div className="hw-cap" key={l}><span>{l}</span><Chip label="" value={c.state} /><small>{c.detail || ''}</small></div>)}
        </div>

        <h3 className="hw-h3">Costs — {h.costs.period}</h3>
        <div className="hw-costs">
          <div><b>{h.costs.harvey_software.label}</b><span className="hw-cost">{h.costs.harvey_software.value}</span><small>{h.costs.harvey_software.detail}</small></div>
          <div><b>{h.costs.ai_model.label}</b><span className="hw-cost">{h.costs.ai_model.spend}</span>
            <small>{h.costs.ai_model.provider}/{h.costs.ai_model.model || '—'} · {h.costs.ai_model.calls_used} of {h.costs.ai_model.calls_allowed} calls · {h.costs.ai_model.tokens} tokens. {h.costs.ai_model.detail}</small></div>
          <div><b>{h.costs.discovery.label}</b><span className="hw-cost">{h.costs.discovery.enabled ? h.costs.discovery.spend : 'Disabled'}</span><small>{h.costs.discovery.search_calls} search calls. {h.costs.discovery.detail}</small></div>
          <div><b>{h.costs.website_research.label}</b><span className="hw-cost">{h.costs.website_research.value}</span><small>{h.costs.website_research.detail}</small></div>
          <div><b>{h.costs.other_operating_costs.label}</b><span className="hw-cost">{h.costs.other_operating_costs.value}</span><small>{h.costs.other_operating_costs.detail}</small></div>
        </div>
        <AIBudget h={h} onSaved={reload} />

        <h3 className="hw-h3">Workers</h3>
        {h.workers.map(w => <div className="aq-row" key={w.key}><div className="aq-row__main"><b>{w.label}</b>
          <span>{w.counts.done} done · {w.counts.queued} queued · {w.counts.blocked} blocked · {w.counts.dead} failed{w.last_success ? ` · last success ${shortTime(w.last_success)}` : ''}</span>
          {w.last_error && <span className="aq-err" style={{ display: 'block', marginTop: 4 }}>{w.last_error}</span>}
          {w.blockers.length > 0 && <small className="hw-blockers">{w.blockers.join(' · ')}</small>}</div></div>)}

        <h3 className="hw-h3">Service companies the workforce works for</h3>
        {h.clients.length ? h.clients.map(c => <div className="aq-row" key={c.organization_id}><div className="aq-row__main"><b>{c.name}{c.is_test && <span className="hw-test">TEST</span>}</b>
          <span>{c.category || 'service business'} · up to {c.daily_job_limit} jobs/day{c.client_paused ? ' · client outreach paused' : ''}</span></div>
          <button className={`aq-btn aq-btn--sm ${c.enabled ? '' : 'aq-btn--primary'}`} onClick={async () => {
            setFail(null); try { await api.put(`/god/acquisition/workforce/clients/${c.organization_id}`, { enabled: !c.enabled }); say(c.enabled ? 'Switched off' : 'Switched on'); reload() } catch (e) { setFail(errText(e)) } }}>{c.enabled ? 'Switch off' : 'Switch on'}</button></div>)
          : <Empty icon="store" title="No service companies yet" />}

        <h3 className="hw-h3">Ready for a real launch?</h3>
        <LaunchReadiness />
      </>}
    </section>
  )
}

// Per company: YES/NO for a REAL launch with every reason. Authorizing real
// outreach is offered only when every check is YES, and it is time-boxed.
export function LaunchReadiness() {
  const [d, err, reload] = useLoad('/god/acquisition/workforce/launch-readiness')
  const [msg, setMsg] = useState(null)
  const [sure, setSure] = useState(null)
  if (err) return <div className="aq-err">{errText(err)}</div>
  if (!d) return <Loading />
  const go = async (id, what) => {
    setMsg(null); setSure(null)
    try {
      if (what === 'authorize') await api.post(`/god/acquisition/workforce/campaigns/${id}/authorize`, { confirm: true, days: 30, max_sends_per_day: 10 })
      else await api.post(`/god/acquisition/workforce/campaigns/${id}/revoke`, {})
      setMsg(what === 'authorize' ? 'Real outreach authorized for 30 days, up to 10 emails a day.' : 'Authorization revoked — no real emails will be sent.'); reload()
    } catch (e) { setMsg(errText(e)) }
  }
  return <div className="hw-rows hw-launch">
    {msg && <div className="aq-note">{msg}</div>}
    {d.companies.map(c => <div className="aq-row" key={c.organization_id}><div className="aq-row__main">
      <b>{c.company}{c.is_test && <span className="hw-test">TEST</span>} <span className={`hw-yn hw-yn--${c.ready_to_launch === 'YES' ? 'yes' : 'no'}`}>{c.ready_to_launch}</span>
        {c.live && <span className="hw-yn hw-yn--yes">AUTHORIZED</span>}</b>
      {c.reasons.length > 0 ? <ul className="hw-reasons">{c.reasons.map(r => <li key={r}>{r}</li>)}</ul>
        : <span>Every launch check passes{c.authorized ? '' : ' — waiting for your go-ahead'}.</span>}
    </div>
      {c.ready_to_launch === 'YES' && !c.authorized && <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={() => setSure(c)}>Authorize real outreach</button>}
      {c.authorized && <button className="aq-btn aq-btn--sm" onClick={() => go(c.organization_id, 'revoke')}>Revoke</button>}
    </div>)}
    {sure && <Confirm text={`Authorize real outreach for ${sure.company}?`} confirmLabel="Authorize"
      onConfirm={() => go(sure.organization_id, 'authorize')} onCancel={() => setSure(null)}>
      <span> Harvey may email verified prospects for this company for 30 days, at most 10 a day. You can revoke it any time.</span></Confirm>}
  </div>
}

function ResearchRows({ filter, onChanged }) {
  const q = filter.startsWith('r:') ? `reason=${filter.slice(2)}` : `bucket=${filter}`
  const [d, err, reload] = useLoad(`/god/acquisition/workforce/research?${q}`)
  const [site, setSite] = useState({})
  const [msg, setMsg] = useState(null)
  if (err) return <div className="aq-err">{errText(err)}</div>
  if (!d) return <Loading />
  const save = async id => {
    setMsg(null)
    try { const r = await api.post(`/god/acquisition/workforce/research/${id}/website`, { website: site[id] || '' }); setMsg(`Saved ${r.website} — ${r.match || 'homepage did not name the business; check it'}. Research queued.`); reload(); onChanged() }
    catch (e) { setMsg(errText(e)) }
  }
  return <div className="hw-rows">
    {msg && <div className="aq-note">{msg}</div>}
    {d.rows.length ? d.rows.map(r => <div className="aq-row" key={r.id}><div className="aq-row__main"><b>{r.name}</b>
      <span>{r.city || ''}{r.website ? ` · ${r.website}` : ''}{r.reason ? ` · ${r.reason}` : ''}</span>
      {r.detail && <small className="hw-blockers">{r.detail}</small>}</div>
      {r.reason_code === 'no_website' && <span className="hw-site"><input placeholder="business website" value={site[r.id] || ''} onChange={e => setSite({ ...site, [r.id]: e.target.value })} />
        <button className="aq-btn aq-btn--sm" disabled={!site[r.id]} onClick={() => save(r.id)}>Add &amp; research</button></span>}
    </div>) : <div className="aq-empty">No businesses in this group.</div>}
  </div>
}

function AIBudget({ h, onSaved }) {
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ daily_ai_call_limit: h.limits.daily_ai_call_limit, per_client_daily_calls: h.ai_policy.per_client_daily_calls,
    max_tokens_per_call: h.ai_policy.max_tokens_per_call, max_calls_per_pass: h.ai_policy.max_calls_per_pass, daily_cost_ceiling_usd: h.ai_policy.daily_cost_ceiling_usd ?? '' })
  const [fail, setFail] = useState(null)
  const save = async () => {
    setFail(null)
    try {
      await api.post('/god/acquisition/workforce/limits', { daily_ai_call_limit: Number(f.daily_ai_call_limit) })
      await api.post('/god/acquisition/workforce/ai-policy', { per_client_daily_calls: Number(f.per_client_daily_calls), max_tokens_per_call: Number(f.max_tokens_per_call),
        max_calls_per_pass: Number(f.max_calls_per_pass), daily_cost_ceiling_usd: f.daily_cost_ceiling_usd === '' ? null : Number(f.daily_cost_ceiling_usd) })
      setOpen(false); onSaved()
    } catch (e) { setFail(errText(e)) }
  }
  if (!open) return <button className="aq-btn aq-btn--sm" style={{ marginTop: 8 }} onClick={() => setOpen(true)}>AI budget &amp; limits</button>
  return <div className="hw-budget">
    <p className="hw-note">AI calls are billed by OpenAI per token on the existing EvoSys account. 0 calls per day keeps AI off (no charge). Raising it is a spending decision.</p>
    <div className="hw-budget__grid">
      {[['daily_ai_call_limit', 'AI calls per day (all companies)'], ['per_client_daily_calls', 'AI calls per company per day'], ['max_calls_per_pass', 'AI calls per 5-minute pass'],
        ['max_tokens_per_call', 'Max tokens per call'], ['daily_cost_ceiling_usd', 'Daily cost ceiling (USD, optional)']].map(([k, l]) =>
        <Fragment key={k}><label className="aq-field">{l}<input type="number" min="0" value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} /></label></Fragment>)}
    </div>
    {fail && <div className="aq-err">{fail}</div>}
    <div className="aq-actions"><button className="aq-btn aq-btn--sm aq-btn--primary" onClick={save}>Save limits</button><button className="aq-btn aq-btn--sm" onClick={() => setOpen(false)}>Cancel</button></div>
  </div>
}
