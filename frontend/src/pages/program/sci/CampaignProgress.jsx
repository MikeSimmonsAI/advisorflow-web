/* CAMPAIGN PROGRESS (owner, Nov 7): one campaign's whole roster - every family
   in exactly one group - what went out each day, what is due in the next
   seven days, whether the senders are keeping up, and the sending days and
   hours. GET /program/command/campaign-progress, read-only, counts only.
   "Attempted" texts are provider accepts; "delivered" is carrier-confirmed. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, Kpi, Loading, Panel, ago, errText, num } from './ui'

const GROUPS = [
  ['source_records', 'Total roster', 'users', 'slate', 'Source rows'],
  ['approved_families', 'Approved', 'check', 'blue', 'Families'],
  ['excluded', 'Excluded', 'pause', 'slate', 'Held out of this campaign'],
  ['not_started', 'Not started', 'clock', 'gold', 'Approved, waiting'],
  ['active', 'Active', 'bolt', 'teal', 'Still moving'],
  ['stopped', 'Stopped', 'alert', 'red', 'STOP, reply, person, booked'],
  ['completed', 'Completed', 'check', 'green', 'All touches done'],
  ['held', 'Held', 'pause', 'gold', 'Texts on recovery hold'],
]
const DAY_COLS = [
  ['sms_attempted', 'Texts attempted'], ['sms_delivered', 'Delivered'], ['sms_failed', 'Failed'],
  ['email_sent', 'Emails sent'], ['email_bounced', 'Bounced'], ['email_blocked', 'Blocked'],
  ['replies', 'Replies'], ['handoffs', 'Handoffs'], ['appointments', 'Booked'],
]
const dayName = d => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
const dateOnly = iso => (iso ? new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/Chicago' }) : '—')

export default function CampaignProgress() {
  const [campaign, setCampaign] = useState('')
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    api.get(`/program/command/campaign-progress${campaign ? `?campaign=${encodeURIComponent(campaign)}` : ''}`)
      .then(x => { setD(x); setErr('') }).catch(e => setErr(errText(e)))
  }, [campaign])
  useEffect(() => { load(); const t = setInterval(load, 120000); return () => clearInterval(t) }, [load])
  const r = d?.report
  const ro = r?.roster || {}
  const sched = r ? Object.values(r.scheduler || {}) : []
  const healthy = sched.length && sched.every(s => s.healthy)
  return (
    <Panel title="Campaign progress" labelledBy="sci-campprog"
      aside={d?.campaigns?.length ? (
        <select className="sci-input sm" aria-label="Campaign" value={campaign || r?.campaign?.key || ''} onChange={e => setCampaign(e.target.value)}>
          {d.campaigns.map(c => <option key={c.key} value={c.key}>{c.name}{c.active ? '' : ' (off)'}</option>)}
        </select>) : null}>
      <ErrorLine text={err} />
      {!d && !err ? <Loading label="Counting the roster…" /> : !r ? (!err && <Empty>No campaign to show.</Empty>) : (
        <>
          <p className="sci-micro sci-muted" style={{ margin: '0 0 10px' }}>
            Sending: <b>{r.window.label}</b>. Daily batch: {r.daily_batch.on ? `${r.daily_batch.count} a day, ${r.daily_batch.days_label.toLowerCase()} from ${r.daily_batch.start}` : 'off'}.
            {' '}Texts: {r.schedule.sms_steps.length} (days {r.schedule.sms_steps.map(s => s.day).join(', ')}). Emails: {r.schedule.email_touches}, every {r.schedule.email_every_days} days.
          </p>
          <section className="sci-kpis four" aria-label="Roster">
            {GROUPS.map(([k, label, icon, tone, note]) => <Kpi key={k} icon={icon} tone={tone} value={ro[k] ?? 0} label={label} note={note} />)}
          </section>
          {ro.not_started > 0 && r.not_started_why && (
            <p className="sci-micro" style={{ marginTop: 8 }}>Not started: {Object.entries(r.not_started_why).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(' · ')}</p>
          )}
          {ro.stopped > 0 && (
            <p className="sci-micro sci-muted">Stopped: {num(r.stopped_why.stop_dnc)} STOP/DNC · {num(r.stopped_why.replied)} replied · {num(r.stopped_why.person_has_it)} with a person · {num(r.stopped_why.appointment)} booked</p>
          )}
          <div className="sci-row-gap" style={{ margin: '10px 0', flexWrap: 'wrap' }}>
            <Chip tone={healthy ? 'ok' : 'bad'}>{healthy ? 'Senders running' : 'Check the senders'}</Chip>
            {sched.length > 0 && <span className="sci-micro">Text sender last ran {ago(r.scheduler.cadence_loop?.last_run) || '—'}</span>}
            <Chip tone={r.overdue.count ? 'warn' : 'ok'} plain>{r.overdue.count ? `${r.overdue.count} touches overdue (oldest ${ago(r.overdue.oldest_due)})` : 'Nothing overdue'}</Chip>
            <span className="sci-micro">Still to go: {num(r.remaining.sms_touches)} texts, {num(r.remaining.email_touches)} emails · last one expected {dateOnly(r.remaining.expected_last_touch)}</span>
          </div>
          <div className="sci-tablewrap">
            <table className="sci-table compact">
              <caption className="sci-micro sci-muted" style={{ textAlign: 'left' }}>Last 7 days (Central)</caption>
              <thead><tr><th scope="col">Day</th>{DAY_COLS.map(([k, l]) => <th key={k} scope="col">{l}</th>)}</tr></thead>
              <tbody>
                {r.daily.map(row => <tr key={row.day}><th scope="row">{dayName(row.day)}</th>{DAY_COLS.map(([k]) => <td key={k}>{num(row[k])}</td>)}</tr>)}
                <tr><th scope="row">7 days</th>{DAY_COLS.map(([k]) => <td key={k}><b>{num(r.week[k])}</b></td>)}</tr>
              </tbody>
            </table>
          </div>
          <div className="sci-tablewrap" style={{ marginTop: 10 }}>
            <table className="sci-table compact">
              <caption className="sci-micro sci-muted" style={{ textAlign: 'left' }}>Next 7 days - scheduled, if nobody replies or stops</caption>
              <thead><tr><th scope="col">Day</th><th scope="col">Texts scheduled</th><th scope="col">Emails scheduled</th></tr></thead>
              <tbody>{r.next_7_days.map(x => <tr key={x.day}><th scope="row">{dayName(x.day)}</th><td>{num(x.sms)}</td><td>{num(x.email)}</td></tr>)}</tbody>
            </table>
          </div>
          <p className="sci-micro sci-muted" style={{ marginTop: 6 }}>Updated {ago(r.generated_at)}. Texts "attempted" were accepted by the provider; "delivered" means the carrier confirmed it.</p>
        </>
      )}
    </Panel>
  )
}
