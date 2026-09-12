/**
 * SendOnboarding — the confirmation between an operator and a real person.
 *
 * ===========================================================================
 * WHY THERE IS A DIALOG AT ALL
 * ===========================================================================
 *
 * The failure this exists to prevent is not technical. Nothing here can crash
 * a server or corrupt a row. What it can do is invite a REAL PERSON into the
 * WRONG COMPANY'S workspace, and no amount of server-side validation catches
 * that, because every field would be individually valid.
 *
 * So the address and the brand are shown together, the address is typed twice,
 * and the Send button stays disabled until those two match. The second field
 * is not belt-and-braces — it is the whole control.
 *
 * ===========================================================================
 * THE LINK IS SHOWN ONCE
 * ===========================================================================
 *
 * The platform does not send the message; the operator does. So the link is
 * displayed after sending, with a copy button and a plain statement that it
 * will not be shown again. It is never stored by this component, never put in
 * the URL, and closing the dialog loses it — which is correct, because the way
 * to recover a lost link is to issue a new one, not to find the old one.
 */
import { useEffect, useState } from 'react'

import { api } from '../../api/client'

export default function SendOnboarding({ orgId, orgName, onClose, onSent }) {
  const [ctx, setCtx] = useState(null)
  const [loadErr, setLoadErr] = useState(null)

  const [email, setEmail] = useState('')
  const [confirm, setConfirm] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState('org_admin')

  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [sent, setSent] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let alive = true
    api.get('/god/launch/' + orgId + '/onboarding-recipient')
      .then(d => { if (alive) { setCtx(d); setRole(d.default_role || 'org_admin') } })
      .catch(e => { if (alive) setLoadErr(e?.detail || 'Could not load recipient details.') })
    return () => { alive = false }
  }, [orgId])

  // The exact rule the server enforces, mirrored here so the button is honest
  // about whether pressing it would work.
  const matches = email.trim().length > 0
    && email.trim().toLowerCase() === confirm.trim().toLowerCase()
  const valid = matches && email.includes('@')

  const send = async () => {
    if (!valid || busy) return
    setBusy(true)
    setErr(null)
    try {
      const res = await api.post('/god/launch/' + orgId + '/send-onboarding', {
        email: email.trim(),
        confirm_email: confirm.trim(),
        full_name: name.trim(),
        role,
        base_url: window.location.origin,
      })
      setSent(res)
      if (onSent) onSent()
    } catch (e) {
      setErr(e?.detail || 'Could not create the onboarding invitation.')
    } finally {
      setBusy(false)
    }
  }

  const wrap = {
    position: 'fixed', inset: 0, zIndex: 200, display: 'grid',
    placeItems: 'center', padding: 24, background: 'rgba(8,16,28,.6)',
  }
  const card = {
    width: '100%', maxWidth: 540, borderRadius: 14, padding: 22,
    background: 'var(--god-card, #fff)', color: 'var(--god-text, #0f172a)',
    border: '1px solid var(--god-border, #e5e7eb)',
    boxShadow: '0 24px 60px rgba(0,0,0,.35)', maxHeight: '90vh',
    overflowY: 'auto',
  }
  const field = {
    width: '100%', padding: '9px 11px', borderRadius: 8, fontSize: 13,
    border: '1px solid var(--god-border, #e5e7eb)', marginTop: 4,
    background: 'var(--god-bg, #fff)', color: 'inherit',
  }
  const label = { fontSize: 11, fontWeight: 700, letterSpacing: '.08em',
                  textTransform: 'uppercase', color: 'var(--gm-text, #64748b)' }
  const btn = {
    fontSize: 13, fontWeight: 700, padding: '9px 16px', borderRadius: 9,
    border: '1px solid var(--god-border, #e5e7eb)', cursor: 'pointer',
    background: 'transparent', color: 'inherit',
  }

  // ── after sending: the link, once ──
  if (sent) {
    return (
      <div style={wrap} role="dialog" aria-modal="true">
        <div style={card}>
          <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>Onboarding ready to send</h3>
          <p style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--gm-text, #475569)' }}>
            <b>{sent.recipient.name || sent.recipient.email}</b> now has access to{' '}
            <b>{orgName}</b>&rsquo;s onboarding as <b>{sent.recipient.role}</b>
            {sent.identity_created ? ' (new account)' : ' (existing account reused)'}.
          </p>

          {/* SAID PLAINLY. An operator who assumes the platform emailed it
              will wait for a reply that is never coming. */}
          <div style={{ border: '1px solid var(--gm-amber, #f59e0b)',
                        background: 'rgba(245,158,11,.08)', borderRadius: 10,
                        padding: '10px 12px', margin: '12px 0', fontSize: 12.5 }}>
            <b>Nothing has been sent.</b> Copy this link and send it to them
            yourself, from {sent.brand.name || 'your brand'}. It is shown once
            and cannot be retrieved afterwards — if it is lost, send onboarding
            again to issue a fresh one.
          </div>

          <label style={label}>One-time onboarding link</label>
          <textarea readOnly value={sent.onboarding_url} rows={3}
                    style={{ ...field, fontFamily: 'monospace', fontSize: 11.5 }}
                    onFocus={e => e.target.select()} />

          <div style={{ display: 'flex', gap: 8, marginTop: 12,
                        alignItems: 'center', flexWrap: 'wrap' }}>
            <button style={{ ...btn, borderColor: 'var(--gm-teal)',
                             color: 'var(--gm-teal)' }}
                    onClick={() => {
                      try {
                        navigator.clipboard.writeText(sent.onboarding_url)
                        setCopied(true)
                      } catch (e) { setCopied(false) }
                    }}>
              {copied ? 'Copied' : 'Copy link'}
            </button>
            <span style={{ flex: 1 }} />
            <button style={btn} onClick={onClose}>Done</button>
          </div>
        </div>
      </div>
    )
  }

  // ── before sending: confirm the recipient ──
  return (
    <div style={wrap} role="dialog" aria-modal="true">
      <div style={card}>
        <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>Send onboarding</h3>
        <p style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--gm-text, #475569)' }}>
          Give someone at <b>{orgName}</b> access to their own onboarding.
        </p>

        {loadErr ? (
          <p style={{ fontSize: 12.5, color: '#dc2626' }}>{loadErr}</p>
        ) : null}

        {ctx ? (
          <div style={{ fontSize: 12.5, border: '1px solid var(--god-border, #e5e7eb)',
                        borderRadius: 10, padding: '10px 12px', margin: '12px 0' }}>
            They will be invited as <b>{ctx.brand.name}</b>, into{' '}
            <b>{ctx.organization_name}</b>. Nothing is emailed or texted — you
            get a link to send yourself.
          </div>
        ) : null}

        {/* Reusing somebody who already exists, rather than typing an address
            that differs by a character and creating a second account. */}
        {ctx && ctx.existing_people.length ? (
          <div style={{ marginBottom: 12 }}>
            <label style={label}>Already at this customer</label>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
              {ctx.existing_people.map(p => (
                <button key={p.id} style={{ ...btn, fontSize: 12, padding: '5px 10px' }}
                        onClick={() => {
                          setEmail(p.email); setConfirm(p.email)
                          setName(p.name || '')
                        }}>
                  {p.name || p.email}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <div style={{ marginBottom: 10 }}>
          <label style={label}>Their name</label>
          <input style={field} value={name} onChange={e => setName(e.target.value)}
                 placeholder="Full name" />
        </div>

        <div style={{ marginBottom: 10 }}>
          <label style={label}>Their email</label>
          <input style={field} value={email} type="email" autoComplete="off"
                 onChange={e => setEmail(e.target.value)} />
        </div>

        <div style={{ marginBottom: 10 }}>
          <label style={label}>Confirm their email</label>
          <input style={{ ...field,
                          borderColor: confirm && !matches ? '#dc2626'
                            : 'var(--god-border, #e5e7eb)' }}
                 value={confirm} type="email" autoComplete="off"
                 onPaste={e => e.preventDefault()}
                 onChange={e => setConfirm(e.target.value)} />
          {confirm && !matches ? (
            <p style={{ fontSize: 11.5, color: '#dc2626', margin: '5px 0 0' }}>
              These do not match.
            </p>
          ) : null}
        </div>

        <div style={{ marginBottom: 14 }}>
          <label style={label}>Their access</label>
          <select style={field} value={role} onChange={e => setRole(e.target.value)}>
            {(ctx ? ctx.roles : ['org_admin']).map(r => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          <p style={{ fontSize: 11.5, color: 'var(--gm-text, #64748b)', margin: '5px 0 0' }}>
            Access to their own workspace only. No platform or back-office
            authority can be granted here.
          </p>
        </div>

        {err ? (
          <p style={{ fontSize: 12.5, color: '#dc2626' }}>{err}</p>
        ) : null}

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ flex: 1 }} />
          <button style={btn} onClick={onClose} disabled={busy}>Cancel</button>
          <button onClick={send} disabled={!valid || busy}
                  style={{ ...btn,
                           borderColor: valid ? 'var(--gm-teal)' : undefined,
                           background: valid ? 'var(--gm-pill-teal-bg)' : 'transparent',
                           color: valid ? 'var(--gm-teal)' : 'var(--gm-text, #94a3b8)',
                           cursor: valid && !busy ? 'pointer' : 'default',
                           opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Creating…' : 'Create onboarding link'}
          </button>
        </div>
      </div>
    </div>
  )
}
