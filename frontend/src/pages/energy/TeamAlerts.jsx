/**
 * SETTINGS → TEAM & ALERTS (energy workspaces, managers).
 *
 *   GET   /energy-ops/team
 *   PATCH /energy-ops/team/members/{user_id}     { in_app?, email?, assignable? }
 *   PUT   /energy-ops/team/default-advisor        { user_id | null }
 *   PUT   /energy-ops/team/mailbox                { address }
 *   POST  /energy-ops/team/mailbox/send-test
 *   POST  /energy-ops/team/mailbox/confirm        { code }
 *   POST  /admin/users  (Add teammate — the existing invitation; org admins)
 *
 * Email shows "Email not configured" with the reasons until the shared mailbox
 * is verified; nothing claims a delivery that did not happen.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

const ROLE = { org_admin: 'Admin', manager: 'Manager', advisor: 'Advisor' }

export default function TeamAlerts() {
  const [t, setT] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = useCallback(() => {
    api.get('/energy-ops/team').then(d => { setErr(''); setT(d) }).catch(e => setErr(e.message || 'Could not load the team'))
  }, [])
  useEffect(() => { load() }, [load])

  // Optimistic: the box flips at once; the server's answer then replaces the
  // row (or the old value comes back with the reason if the save failed).
  const toggle = (m, key) => {
    setMsg('')
    const flip = val => setT(prev => prev && ({
      ...prev, members: prev.members.map(x => (x.user_id === m.user_id ? { ...x, [key]: val } : x)),
    }))
    flip(!m[key])
    api.patch(`/energy-ops/team/members/${m.user_id}`, { [key]: !m[key] })
      .then(r => { setMsg(r.note || `Saved: ${m.name} ${key === 'in_app' ? 'in-app alerts' : key === 'email' ? 'email alerts' : 'can be assigned'} ${!m[key] ? 'on' : 'off'}.`); load() })
      .catch(e => { flip(m[key]); setMsg(e.message || 'Could not save') })
  }
  const setDefault = uid => {
    setMsg('')
    api.put('/energy-ops/team/default-advisor', { user_id: uid || null })
      .then(() => { setMsg(uid ? 'Default advisor saved. New requests go straight to them.' : 'No default advisor: new requests wait in New Requests to be claimed.'); load() })
      .catch(e => setMsg(e.message || 'Could not save'))
  }

  if (err) return <div className="eo-banner eo-banner--error" data-testid="team-error">{err}</div>
  if (!t) return <div className="eo-empty">Loading…</div>
  const assignable = t.members.filter(m => m.assignable)
  return (
    <div data-testid="team-alerts">
      {msg && <div className="eo-note" role="status" data-testid="team-msg">{msg}</div>}

      <section className="eo-panel eo-intake">
        <h2 className="eo-sec" style={{ marginTop: 0 }}>Who gets alerted, and who can take requests</h2>
        <table className="eo-table" data-testid="team-table">
          <thead><tr><th>Person</th><th>In-app alert</th><th>Email alert</th><th>Can be assigned</th></tr></thead>
          <tbody>
            {t.members.map(m => (
              <tr key={m.user_id} data-member={m.user_id}>
                <td><strong>{m.name}</strong><div className="eo-sub">{ROLE[m.role] || m.role}{m.setup_pending ? ' · setup link not used yet' : ''}</div></td>
                {['in_app', 'email', 'assignable'].map(k => (
                  <td key={k}>
                    <label className="eo-check">
                      <input type="checkbox" checked={!!m[k]} onChange={() => toggle(m, k)} data-flag={k}
                        aria-label={`${m.name}: ${k === 'in_app' ? 'in-app alerts' : k === 'email' ? 'email alerts' : 'can be assigned'}`} />
                      <span className="eo-hide-sm">{m[k] ? 'On' : 'Off'}</span>
                    </label>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <ul className="eo-list eo-sub">{t.rules.map(r => <li key={r}>{r}</li>)}</ul>
      </section>

      <section className="eo-panel eo-intake">
        <h2 className="eo-sec" style={{ marginTop: 0 }}>Default advisor</h2>
        <label>New requests go to
          <select value={t.default_advisor_id || ''} onChange={e => setDefault(e.target.value)} data-testid="default-advisor">
            <option value="">Nobody — the team claims them</option>
            {assignable.map(m => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
          </select>
        </label>
        <p className="eo-note">{t.default_advisor_note} This does not change the website.</p>
      </section>

      {t.can_invite ? <AddTeammate onDone={load} /> : (
        <section className="eo-panel eo-intake"><p className="eo-note">To add a teammate, ask a workspace admin.</p></section>
      )}

      <EmailPanel email={t.email} onChange={load} />

      {t.recent_alerts.length > 0 && (
        <section className="eo-panel eo-intake">
          <h2 className="eo-sec" style={{ marginTop: 0 }}>Recent alerts</h2>
          <ul className="eo-list" data-testid="recent-alerts">
            {t.recent_alerts.map((a, i) => (
              <li key={i}>{(a.at || '').replace('T', ' ').slice(0, 16)} · {a.origin === 'field' ? 'Field referral' : 'Website request'} ·
                {' '}{a.in_app} in-app · email: {a.email_state === 'attempted' ? `${a.email_sent} sent${a.email_failed ? `, ${a.email_failed} failed` : ''}`
                  : a.email_state === 'skipped' ? 'skipped (test record)' : 'not configured'}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function AddTeammate({ onDone }) {
  const [f, setF] = useState({ full_name: '', email: '', role: 'advisor' })
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const submit = e => {
    e.preventDefault()
    setBusy(true); setRes(null)
    api.post('/admin/users', f)
      .then(u => {
        const link = u.setup_url ? new URL(u.setup_url, window.location.origin).toString() : null
        setRes({ ok: true, text: `${u.full_name} was added.`, link })
        setF({ full_name: '', email: '', role: 'advisor' }); onDone()
      })
      .catch(e2 => setRes({ ok: false, text: e2.message || 'Could not add this person' }))
      .finally(() => setBusy(false))
  }
  return (
    <section className="eo-panel eo-intake">
      <h2 className="eo-sec" style={{ marginTop: 0 }}>Add teammate</h2>
      <form className="eo-form" onSubmit={submit} data-testid="add-teammate">
        <label>Full name<input value={f.full_name} onChange={e => setF(p => ({ ...p, full_name: e.target.value }))} required /></label>
        <label>Email<input type="email" value={f.email} onChange={e => setF(p => ({ ...p, email: e.target.value }))} required /></label>
        <label>Role
          <select value={f.role} onChange={e => setF(p => ({ ...p, role: e.target.value }))}>
            <option value="advisor">Advisor</option><option value="org_admin">Admin</option>
          </select>
        </label>
        <button type="submit" className="eo-btn eo-btn--primary" disabled={busy}>{busy ? 'Adding…' : 'Add teammate'}</button>
      </form>
      {res && (
        <div className={res.ok ? 'eo-note' : 'eo-banner eo-banner--error'} role="status" data-testid="add-result">
          {res.text}
          {res.link && <> Send them this one-time setup link yourself (no email is sent): <input readOnly value={res.link} onFocus={e => e.target.select()} aria-label="Setup link" style={{ width: '100%' }} /></>}
          {res.ok && <> They start with alerts off and can be assigned requests — change that above.</>}
        </div>
      )}
    </section>
  )
}

function EmailPanel({ email, onChange }) {
  const [address, setAddress] = useState(email.mailbox || '')
  const [code, setCode] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setAddress(email.mailbox || '') }, [email.mailbox])
  const run = (p, ok) => {
    setBusy(true); setMsg('')
    p.then(() => { setMsg(ok); onChange() }).catch(e => setMsg(e.message || 'Not done')).finally(() => setBusy(false))
  }
  const ready = email.state === 'ready'
  return (
    <section className="eo-panel eo-intake" data-testid="email-panel">
      <div className="eo-head" style={{ marginBottom: 6 }}>
        <h2 className="eo-sec" style={{ margin: 0 }}>Shared alert mailbox</h2>
        <span className={`eo-pill${ready ? ' eo-pill--green' : ''}`} data-testid="email-state">{email.label}</span>
      </div>
      {!ready && <ul className="eo-list" data-testid="email-reasons">{email.reasons.map(r => <li key={r}>{r}</li>)}</ul>}
      {ready && <p className="eo-note">New-request emails go to {email.mailbox} and to teammates with email alerts on, from {email.sender}. Only this team is ever emailed.</p>}
      <form className="eo-inline" onSubmit={e => { e.preventDefault(); run(api.put('/energy-ops/team/mailbox', { address }), 'Mailbox saved. Verify it to turn email on.') }}>
        <input type="email" aria-label="Shared mailbox" placeholder="alerts@yourcompany.com" value={address} onChange={e => setAddress(e.target.value)} />
        <button type="submit" className="eo-btn" disabled={busy}>Save mailbox</button>
      </form>
      {email.mailbox && !email.mailbox_verified && (
        <div className="eo-inline" style={{ marginTop: 8 }}>
          <button type="button" className="eo-btn" disabled={busy}
            onClick={() => run(api.post('/energy-ops/team/mailbox/send-test', {}), `A code was sent to ${email.mailbox}. Type it below.`)}>Send test code</button>
          <input aria-label="Code from the test email" placeholder="6-digit code" value={code} onChange={e => setCode(e.target.value)} inputMode="numeric" maxLength={6} />
          <button type="button" className="eo-btn eo-btn--primary" disabled={busy || code.length < 6}
            onClick={() => run(api.post('/energy-ops/team/mailbox/confirm', { code }), 'Mailbox verified.')}>Confirm</button>
        </div>
      )}
      {msg && <div className="eo-note" role="status" data-testid="email-msg">{msg}</div>}
      <p className="eo-sub">In-app alerts work without email.</p>
    </section>
  )
}
