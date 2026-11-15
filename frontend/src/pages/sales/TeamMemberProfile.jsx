import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import ContractorOnboardingPanel from './ContractorOnboardingPanel'

/**
 * One salesperson inside My Team: deals, activity and demo work. Read from
 * /sales-team/members/<id>/profile, which applies the same team scope as the
 * roster. Onboarding documents are never shown here.
 */
const box = { marginTop: 10, padding: 12, background: '#f7fbfa', border: '1px solid #dce5ea', borderRadius: 10, fontSize: 13 }
const when = iso => { try { return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) } catch { return '' } }

export default function TeamMemberProfile({ userId }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api.get('/sales-team/members/' + userId + '/profile', { noOrgContext: true })
    .then(r => setD(r)).catch(e => setErr((e && e.detail) || 'Could not load this profile.'))
  useEffect(() => { load() }, [userId]) // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <div style={box}>{err}</div>
  if (!d) return <div style={box}>Loading…</div>
  const s = d.summary
  return (
    <div style={box} aria-label={'Profile for ' + d.person.name}>
      <div style={{ marginBottom: 8 }}>
        <b>{s.open_deals}</b> open deals · <b>{s.follow_ups_due}</b> follow-ups due · <b>{s.demos_building}</b> demos building
        {d.person.manager && <> · reports to {d.person.manager}</>}
      </div>
      {d.getting_started && (
        <div style={{ marginBottom: 8 }}>
          <b>From approval to first sale</b>: {d.getting_started.steps.filter(x => x.done && !x.optional).length} of {d.getting_started.steps.filter(x => !x.optional).length} steps
          {d.getting_started.next ? <> · next: {d.getting_started.next.label}</> : ' · complete'}
          <div style={{ display: 'flex', gap: 3, marginTop: 4 }} aria-hidden="true">
            {d.getting_started.steps.filter(x => !x.optional).map(x => <span key={x.key} title={x.label} style={{ flex: 1, height: 6, borderRadius: 3, background: x.done ? '#087f83' : '#dce5ea' }} />)}
          </div>
        </div>
      )}
      <ContractorOnboardingPanel ob={d.onboarding} userId={userId} canResend={d.can_resend_invite} onChanged={load} />
      <div style={{ marginTop: 12 }} />
      <b>Deals</b>
      {d.deals.length === 0 ? <p style={{ margin: '4px 0 10px' }}>No open deals.</p> : (
        <table style={{ width: '100%', borderCollapse: 'collapse', margin: '4px 0 10px' }}>
          <thead><tr style={{ textAlign: 'left', color: '#607486' }}><th>Customer</th><th>Stage</th><th>Next</th><th>Quote</th><th /></tr></thead>
          <tbody>
            {d.deals.map(x => (
              <tr key={x.opportunity_id} style={{ borderTop: '1px solid #e6edf1' }}>
                <td><Link to={x.href}>{x.company_name}</Link></td>
                <td>{x.stage}</td>
                <td style={x.follow_up_due ? { color: '#a1340c', fontWeight: 700 } : null}>{x.next_action || '—'}{x.next_action_due_at ? ' · ' + when(x.next_action_due_at) : ''}</td>
                <td>{x.quote_revision ? 'Rev ' + x.quote_revision : '—'}</td>
                <td><Link to={x.quote_href}>Build Quote</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <b>Demo work</b>
      <ul style={{ margin: '4px 0 10px', paddingLeft: 18 }}>
        {d.demo_work.building.map((x, i) => <li key={'b' + i}>Building for {x.company_name}{x.target_at ? ' · target ' + when(x.target_at) : ''}{x.overdue ? ' · overdue' : ''}</li>)}
        {d.demo_work.requested.map((x, i) => <li key={'r' + i}>{x.company_name}: {x.waiting_for_decision ? 'custom demo waiting for a decision' : 'demo ' + String(x.status).replace('_', ' ')}</li>)}
        {!d.demo_work.building.length && !d.demo_work.requested.length && <li>None.</li>}
      </ul>
      <b>Recent activity</b>
      <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
        {d.activity.length === 0 && <li>Nothing recorded yet.</li>}
        {d.activity.slice(0, 10).map((a, i) => <li key={i}><Link to={a.href}>{a.company_name}</Link>: {a.summary} · {when(a.at)}</li>)}
      </ul>
    </div>
  )
}
