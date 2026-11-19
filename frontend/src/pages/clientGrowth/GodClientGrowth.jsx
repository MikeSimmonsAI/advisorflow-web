/* GOD MODE › Revenue & Billing › Client Growth (/god/client-growth).
 *
 * Owner-only (GodRoute here, require_god on every /god/client-growth call):
 * who can open Client Growth, template switches, the complete invitation and
 * submission history, and removal of synthetic test records. Counts are the
 * server's real rows; nothing here is sample data.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { Badge, SubmissionView, fmt } from './ClientGrowthWorkspace'
import './clientGrowth.css'

function errText (e) {
  const d = e && e.detail
  return typeof d === 'string' ? d : (d && d.message) || (e && e.message) || 'Something went wrong.'
}

export default function GodClientGrowth () {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [email, setEmail] = useState('')
  const [msg, setMsg] = useState('')
  const [sub, setSub] = useState(null)
  const [confirmClean, setConfirmClean] = useState(false)
  const load = useCallback(() => api.get('/god/client-growth/overview').then(setData).catch(e => setErr(errText(e))), [])
  useEffect(() => { load() }, [load])

  const grant = async (addr, on) => {
    setMsg('')
    try { const r = await api.post('/god/client-growth/access', { email: addr, grant: on }); setData(d => ({ ...d, access: r.access })); setEmail(''); setMsg(on ? 'Access granted.' : 'Access removed.') } catch (e) { setMsg(errText(e)) }
  }
  const switchTpl = async (t) => {
    try { await api.post('/god/client-growth/templates/' + t.id, { active: !t.is_active }); load() } catch (e) { setMsg(errText(e)) }
  }
  const clean = async () => {
    try { const r = await api.post('/god/client-growth/test-records/cleanup', { confirm: true }); setMsg('Removed synthetic test records: ' + JSON.stringify(r.removed)); setConfirmClean(false); load() } catch (e) { setMsg(errText(e)) }
  }

  if (err) return <div className="cgg"><div className="cg-alert" role="alert">{err}</div></div>
  if (!data) return <div className="cgg"><p className="cg-muted">Loading…</p></div>
  const c = data.counts
  const t = data.test_counts
  return (
    <div className="cgg" style={{ padding: '4px 0 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 className="cg-h2" style={{ marginTop: 0 }}>Client Growth</h1>
          <p className="cg-muted" style={{ margin: 0 }}>Discovery • Questionnaires • Proposals — EvoSys Pro platform workspace on {data.brand} prospects.</p>
        </div>
        <Link className="cg-btn cg-btn--primary" to={data.workspace_path}>Open workspace</Link>
      </div>

      <div className="cg-tiles" style={{ marginTop: 14 }}>
        {[['Pending invitations', c.pending_invitations], ['Submitted questionnaires', c.submitted],
          ['Awaiting review', c.awaiting_review], ['Proposals prepared', c.proposals_prepared]].map(([l, n]) => (
          <div className="cg-tile" key={l}><div className="cg-tile-n">{n}</div><div className="cg-tile-l">{l}</div></div>
        ))}
      </div>
      <p className="cg-sub">Real records only. Including synthetic test records: {t.pending_invitations} pending, {t.submitted} submitted. Revoked links: {c.revoked}.</p>
      {msg ? <div className="cg-alert cg-alert--warn" role="status">{msg}</div> : null}

      <h2 className="cg-h2">Who can open Client Growth</h2>
      <table className="cg-table"><thead><tr><th>Person</th><th>Email</th><th>Why</th><th /></tr></thead><tbody>
        {data.access.map(p => (
          <tr key={p.user_id}><td>{p.name || '—'}</td><td>{p.email}</td><td>{p.via}{p.grant && p.via !== 'Client Growth access' ? ' + Client Growth access' : ''}</td>
            <td>{p.removable ? <button className="cg-btn cg-btn--small cg-btn--danger" onClick={() => grant(p.email, false)}>Remove Client Growth access</button> : null}</td></tr>
        ))}
      </tbody></table>
      <p className="cg-sub">God Mode and the EvoSys Pro sales program owners have access through their existing roles. Owner appointments stay in Pricing &amp; Compensation. Client Growth staff never get God Mode controls.</p>
      <div className="cg-panel" style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label className="cg-search" style={{ margin: 0, flex: '1 1 260px' }}>Grant Client Growth access to an existing login (email)
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="name@company.com" /></label>
        <button className="cg-btn" disabled={!email.trim()} onClick={() => grant(email.trim(), true)}>Grant access</button>
      </div>

      <h2 className="cg-h2">Templates</h2>
      <table className="cg-table"><thead><tr><th>Template</th><th>Vertical</th><th>Version</th><th>Links</th><th>Status</th><th /></tr></thead><tbody>
        {data.templates.map(tp => (
          <tr key={tp.id}><td>{tp.name}</td><td>{tp.vertical || 'Any'}</td><td>v{tp.version}</td><td>{tp.invitations}</td>
            <td><Badge tone={tp.is_active ? 'ok' : 'muted'}>{tp.is_active ? 'Active' : 'Off'}</Badge></td>
            <td><button className="cg-btn cg-btn--small" onClick={() => switchTpl(tp)}>{tp.is_active ? 'Turn off for new links' : 'Turn on'}</button></td></tr>
        ))}
      </tbody></table>

      <h2 className="cg-h2">Submission history</h2>
      {data.submissions.length === 0 ? <p className="cg-muted">No submissions yet.</p> : (
        <table className="cg-table"><thead><tr><th>Prospect</th><th>Submitted</th><th>Status</th><th>Task</th><th /></tr></thead><tbody>
          {data.submissions.map(s => (
            <tr key={s.id}><td>{s.company}{s.is_test ? <> <Badge tone="muted">Test</Badge></> : null}</td><td>{fmt(s.submitted_at)}</td>
              <td>{s.status_label}</td><td>{s.task ? s.task.title + (s.task.status === 'done' ? ' (done)' : '') : '—'}</td>
              <td><button className="cg-btn cg-btn--small" onClick={() => setSub(s.id)}>Open answers</button></td></tr>
          ))}
        </tbody></table>
      )}

      <h2 className="cg-h2">Invitation history</h2>
      {data.invitations.length === 0 ? <p className="cg-muted">No links yet.</p> : (
        <table className="cg-table"><thead><tr><th>Prospect</th><th>Status</th><th>Created</th><th>Sent (recorded)</th><th>Opened</th><th>Revoked</th></tr></thead><tbody>
          {data.invitations.map(i => (
            <tr key={i.id}><td>{i.company}{i.is_test ? <> <Badge tone="muted">Test</Badge></> : null}<div className="cg-sub">for {i.prepared_for || '—'}</div></td>
              <td>{i.state_label}</td><td>{fmt(i.created_at)}</td><td>{i.sent_at ? fmt(i.sent_at) : '—'}</td><td>{i.opened_at ? fmt(i.opened_at) : '—'}</td>
              <td>{i.revoked_at ? fmt(i.revoked_at) + (i.revoke_reason ? ' — ' + i.revoke_reason : '') : '—'}</td></tr>
          ))}
        </tbody></table>
      )}

      <h2 className="cg-h2">Synthetic test records</h2>
      <p className="cg-muted">Removes only records marked as synthetic tests (test prospects, their links, answers, tasks and notifications). Real prospects are never touched.</p>
      {confirmClean ? (
        <div className="cg-confirm"><p><strong>Remove all synthetic Client Growth test records?</strong> This is recorded in the audit log.</p>
          <button className="cg-btn cg-btn--danger" onClick={clean}>Remove test records</button>{' '}
          <button className="cg-btn" onClick={() => setConfirmClean(false)}>Cancel</button></div>
      ) : <button className="cg-btn" onClick={() => setConfirmClean(true)}>Remove synthetic test records…</button>}

      {sub ? <div className="cg-page" style={{ minHeight: 0 }}><SubmissionView id={sub} onClose={() => { setSub(null); load() }} onOpenProspect={(pid) => { window.location.href = '/client-growth?tab=prospects&prospect=' + encodeURIComponent(pid) }} /></div> : null}
    </div>
  )
}
