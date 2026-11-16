/**
 * Acquisition Control Center → a service company → SERVICE OPERATIONS.
 * The owner and authorized Acquisition program admins switch the company's
 * Service Operations capabilities on or off here. Every switch is a real,
 * audited server change (PUT /acquisition-ops/{org}/service-ops); turning
 * one off deletes nothing.
 */
import { useState } from 'react'
import { api } from '../../api/client'
import { useOps } from './opsContext'
import { Confirm, Dot, Loading, errText, useLoad, useToast, when } from './ui'

export function ServiceOpsAdmin() {
  const { orgId } = useOps()
  const [d, err, reload] = useLoad(orgId ? `/acquisition-ops/${orgId}/service-ops` : null)
  const [ask, setAsk] = useState(null)
  const [busy, setBusy] = useState(false)
  const [toast, say] = useToast()
  const flip = async (key, on, extra = {}) => {
    setBusy(true)
    try {
      await api.put(`/acquisition-ops/${orgId}/service-ops`, { [key]: on, ...extra })
      setAsk(null); reload(); say(on ? 'Turned on' : 'Turned off — nothing was deleted')
    } catch (e) {
      const msg = errText(e)
      if (e.status === 409 && /upcoming/.test(msg)) setAsk({ key, on, msg })
      else say(msg)
    } finally { setBusy(false) }
  }
  if (!orgId) return <div className="aq-page"><div className="aq-err">Choose a service company first.</div></div>
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!d) return <Loading />
  const c = d.counts
  return (
    <div className="aq-page">{toast}
      <h1 className="aq-h1">Service Operations — {d.company}{d.is_test && <> <Dot tone="amber">TEST</Dot></>}</h1>
      <p className="aq-lede">What this company can use after it wins a customer. These are access switches only — no prices, packages or billing change here. Time zone: {d.timezone}.</p>
      <div className="hb-stats">
        {[['Customers', c.customers], ['Locations', c.locations], ['Upcoming jobs', c.upcoming_jobs], ['Recurring', c.active_schedules],
          ['Invoices', c.invoices], ['Team', c.team_members], ['Buyer logins', c.buyer_logins], ['Company admins', d.company_admins]].map(([l, v]) =>
          <div className="hb-stat" key={l}><div><div className="hb-stat__label">{l}</div><div className="hb-stat__value">{v}</div></div></div>)}
      </div>
      <div className="aq-grid aq-two">
        {d.capabilities.map(cap => (
          <section className="aq-card so-cap" key={cap.key}>
            <div className="so-cap__head"><h2>{cap.label}</h2>
              <Dot tone={cap.enabled ? 'green' : 'gray'}>{cap.enabled ? 'Enabled' : 'Disabled'}</Dot></div>
            <div className="so-cap__state">{cap.enabled ? (cap.ready ? <Dot tone="green">Ready</Dot> : <Dot tone="amber">On — setup incomplete</Dot>)
              : cap.blocked_reason ? <Dot tone="red">Can’t turn on: {cap.blocked_reason}</Dot> : cap.ready ? <Dot tone="blue">Ready to turn on</Dot> : <Dot tone="amber">Can turn on — setup incomplete</Dot>}</div>
            {cap.missing.length > 0 && <ul className="hw-reasons">{cap.missing.map(m => <li key={m}>{m}</li>)}</ul>}
            <div className="hb-muted">Gives: {cap.functions.join(' · ')}</div>
            <div className="hb-muted">{cap.last_change ? `Last turned ${cap.last_change.to ? 'on' : 'off'} by ${cap.last_change.by || 'unknown'} · ${when(cap.last_change.at)}` : 'Never changed'}</div>
            <div className="aq-actions">
              {cap.enabled
                ? <button className="aq-btn aq-btn--sm" disabled={busy} onClick={() => flip(cap.key, false)}>Disable</button>
                : <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={busy || !!cap.blocked_reason} onClick={() => flip(cap.key, true)}>Enable</button>}
            </div>
          </section>))}
      </div>
      <section className="aq-card"><h2>When it’s off</h2><p className="aq-lede">{d.while_off}</p></section>
      {ask && <Confirm text="Turn Service Operations off?" danger confirmLabel="Turn off, keep everything"
        onCancel={() => setAsk(null)} onConfirm={() => flip(ask.key, false, { confirm_upcoming: true })}>
        <span> {ask.msg}</span></Confirm>}
    </div>
  )
}
