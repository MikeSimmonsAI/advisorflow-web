/**
 * HARVEY AI WORKFORCE page (#452). Its own menu item in the Acquisition
 * Control Center: the full workforce panel, the viability groups (A/B/C/D,
 * with each business's reasons), the attack queue and the daily count.
 * Master logins only (God or an EvoSys Acquisition admin, e.g. platform@);
 * the server refuses everyone else. Every number is the server's.
 */
import { Fragment, useState } from 'react'
import { api } from '../../api/client'
import { useOps } from './opsContext'
import { HarveyWorkforcePanel, shortTime } from './HarveyWorkforce'
import { Dot, Empty, Loading, PageHeader, Stat, errText, useLoad, useToast } from './ui'

const GROUP_TONE = { A: 'green', B: 'blue', C: 'amber', D: 'gray' }
const GROUP_NAME = { A: 'Start outreach', B: 'Needs contact info', C: 'Parked 30 days', D: 'Dropped' }
const QUEUE_TONE = { done: 'green', next: 'violet', waiting: 'gray', needs_google_places: 'amber' }
const QUEUE_LABEL = { done: 'Done', next: 'Next', waiting: 'Waiting', needs_google_places: 'Needs Google listings' }

function Viability() {
  const [group, setGroup] = useState('A')
  const [v, err, reload] = useLoad(`/god/acquisition/workforce/viability?group=${group}&limit=200`)
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState(false)
  const [toast, say] = useToast()
  const rescore = async () => {
    setBusy(true)
    try { const r = await api.post('/god/acquisition/workforce/viability/rescore', {}); say(`Scored ${r.scored} businesses`); reload() }
    catch (e) { say(errText(e)) } finally { setBusy(false) }
  }
  if (err) return <section className="aq-card"><h2>Viability groups</h2><div className="aq-err">{errText(err)}</div></section>
  if (!v) return <section className="aq-card"><Loading label="Loading the scores…" /></section>
  const g = v.groups
  return (
    <section className="aq-card" style={{ marginBottom: 14 }}>{toast}
      <div className="aq-card__head"><h2>Viability groups</h2>
        <button className="aq-btn aq-btn--sm" disabled={busy} onClick={rescore}>{busy ? 'Scoring…' : 'Score everything again'}</button></div>
      <p className="aq-lede" style={{ marginTop: 0 }}>Every business scored 0–100: fit 30, reachable 25, job size 20, easy to get in 15, timing 10.
        {g.unscored ? ` ${g.unscored} not scored yet — Harvey scores 100 every 5 minutes.` : ''}</p>
      <div className="hb-stats">
        {['A', 'B', 'C', 'D'].map(k => (
          <button key={k} type="button" className={`hw-pill ${group === k ? 'is-on' : ''}`} onClick={() => { setGroup(k); setOpen(null) }}
            style={{ textAlign: 'left' }}>
            <b>{g[k] || 0}</b> Group {k} · {GROUP_NAME[k]}</button>))}
      </div>
      {v.rows.length === 0 ? <Empty title={`No businesses in group ${group}`} text={v.actions[group]} /> :
        <div className="aq-scroll"><table className="aq-table">
          <thead><tr><th>Business</th><th>Type</th><th>City</th><th>Score</th><th>Phone</th><th>Email</th><th>Contact</th></tr></thead>
          <tbody>{v.rows.map(r => (<Fragment key={r.id}>
            <tr onClick={() => setOpen(open === r.id ? null : r.id)} style={{ cursor: 'pointer' }}>
              <td><b>{r.name}</b></td><td>{r.segment_label || r.segment || '—'}</td><td>{r.city || '—'}</td>
              <td><Dot tone={GROUP_TONE[r.group]}>{r.score}</Dot></td><td>{r.phone || '—'}</td>
              <td>{r.email ? `${r.email}${r.email_verified ? ' ✓' : ' (unverified)'}` : '—'}</td><td>{r.contact_name || '—'}</td></tr>
            {open === r.id && <tr><td colSpan="7"><ul style={{ margin: '4px 0 8px 18px' }}>
              {r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul></td></tr>}
          </Fragment>))}</tbody></table></div>}
    </section>
  )
}

function SearchQueue() {
  const [q, err] = useLoad('/god/acquisition/workforce/search-queue')
  if (err) return <section className="aq-card"><h2>Search order</h2><div className="aq-err">{errText(err)}</div></section>
  if (!q) return <section className="aq-card"><Loading label="Loading the search order…" /></section>
  const d = q.daily_report
  return (<>
    <section className="aq-card" style={{ marginBottom: 14 }}>
      <div className="aq-card__head"><h2>Last 24 hours</h2><small>as of {shortTime(d.as_of)}</small></div>
      <div className="hb-stats">
        <Stat icon="search" label="Searches run" value={d.searches_run} sub={`${d.searches_failed} failed`} />
        <Stat icon="database" label="Businesses found" value={d.businesses_found} sub={`${d.businesses_added} new · ${d.pool_total} in pool`} />
        <Stat icon="mail" tone="green" label="Verified emails" value={d.verified_emails} sub={`${d.with_phone} with a phone`} />
        <Stat icon="calendar" tone="amber" label="Booked" value={d.booked_24h} sub={`${d.interested_24h} interested`} />
      </div>
    </section>
    <section className="aq-card" style={{ marginBottom: 14 }}>
      <div className="aq-card__head"><h2>Search order</h2>
        <small>{q.counts.done} done · {q.counts.waiting + q.counts.next} to go · {q.counts.needs_google_places} need Google listings</small></div>
      <p className="aq-lede" style={{ marginTop: 0 }}>{q.next ? `Next: ${q.rows.find(r => r.status === 'next')?.segment_label} in ${q.next.city}. ` : 'Nothing waiting. '}{q.note}</p>
      <div className="aq-scroll" style={{ maxHeight: 420 }}><table className="aq-table">
        <thead><tr><th>#</th><th>Business type</th><th>City</th><th>Status</th><th>Last run</th></tr></thead>
        <tbody>{q.rows.map((r, i) => (
          <tr key={r.segment + r.city}><td>{i + 1}</td><td>{r.segment_label}</td><td>{r.city}</td>
            <td><Dot tone={QUEUE_TONE[r.status]}>{QUEUE_LABEL[r.status]}</Dot></td>
            <td>{r.last_run ? `${shortTime(r.last_run.at)} · ${r.last_run.added} added` : '—'}</td></tr>))}</tbody></table></div>
    </section>
  </>)
}

export default function HarveyPage() {
  const { ov } = useOps()
  if (!ov.is_god) return <div className="aq-page"><Empty icon="shield" title="Owner only" text="Harvey belongs to the EvoSys owner logins." /></div>
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Program Access · Acquisition" title="Harvey AI Workforce"
        sub="Finds businesses, scores them, researches contacts and works the lists in order" />
      <HarveyWorkforcePanel />
      <Viability />
      <SearchQueue />
    </div>
  )
}
