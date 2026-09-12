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

  // WHO THIS ADDRESS ALREADY IS, ASKED BEFORE THE OPERATOR COMMITS.
  //
  // An existing identity is the NORMAL case for a customer that came through
  // the pipeline — the person who signed is often the person who sold it, or
  // somebody who already administers another customer. It used to surface as
  // a red 409 after pressing the button, telling the operator to "use a
  // different address", which is how a second account for one human gets
  // created. The answer is shown up front and it is informational.
  const [look, setLook] = useState(null)
  const [looking, setLooking] = useState(false)

  useEffect(() => {
    let alive = true
    api.get('/god/launch/' + orgId + '/onboarding-recipient')
      .then(d => { if (alive) { setCtx(d); setRole(d.default_role || 'org_admin') } })
      .catch(e => { if (alive) setLoadErr(e?.detail || 'Could not load recipient details.') })
    return () => { alive = false }
  }, [orgId])

  // Debounced, and only once the address looks like one. Creates nothing —
  // the endpoint is a read.
  useEffect(() => {
    const addr = email.trim().toLowerCase()
    if (!addr || !addr.includes('@') || !addr.includes('.')) {
      setLook(null)
      return
    }
    let alive = true
    setLooking(true)
    const t = setTimeout(() => {
      api.get('/god/customers/' + orgId + '/identity-lookup?email='
              + encodeURIComponent(addr))
        .then(d => { if (alive) setLook(d) })
        .catch(() => { if (alive) setLook(null) })
        .finally(() => { if (alive) setLooking(false) })
    }, 400)
    return () => { alive = false; clearTimeout(t) }
  }, [email, orgId])

  // The exact rule the server enforces, mirrored here so the button is honest
  // about whether pressing it would work.
  const matches = email.trim().length > 0
    && email.trim().toLowerCase() === confirm.trim().toLowerCase()
  // `can_add === false` is now only ever a control-plane account, which the
  // server refuses. An existing ordinary identity is addable.
  const refused = !!(look && look.exists && look.can_add === false)
  // A new person needs a name; an existing identity already has one, and the
  // server does not ask for it again.
  const needsName = !!(look && !look.exists) && !name.trim()
  const valid = matches && email.includes('@') && !refused && !needsName

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

          {/* AN EXISTING SIGN-IN IS NOT A SETUP LINK, AND MUST NOT BE
              DESCRIBED AS ONE. Promising "shown once, cannot be retrieved"
              about a plain /launch URL would teach the operator to treat a
              recoverable address as a secret, and to re-issue onboarding to
              get it back. */}
          {sent.access_path === 'existing_login' ? (
            <div style={{ border: '1px solid var(--gm-teal, #0d9488)',
                          background: 'var(--gm-pill-teal-bg, rgba(13,148,136,.08))',
                          borderRadius: 10, padding: '10px 12px', margin: '12px 0',
                          fontSize: 12.5, lineHeight: 1.55 }}>
              <b>They already sign in to AdvisorFlow.</b> No password setup
              link was issued and nothing about their existing access was
              changed — their other roles are intact. Send them this address;
              they sign in as they always do and land on this customer&rsquo;s
              onboarding.
            </div>
          ) : (
            /* SAID PLAINLY. An operator who assumes the platform emailed it
               will wait for a reply that is never coming. */
            <div style={{ border: '1px solid var(--gm-amber, #f59e0b)',
                          background: 'rgba(245,158,11,.08)', borderRadius: 10,
                          padding: '10px 12px', margin: '12px 0', fontSize: 12.5 }}>
              <b>Nothing has been sent.</b> Copy this link and send it to them
              yourself, from {sent.brand.name || 'your brand'}. It is shown once
              and cannot be retrieved afterwards — if it is lost, send onboarding
              again to issue a fresh one.
            </div>
          )}

          <label style={label}>
            {sent.access_path === 'existing_login'
              ? 'Their onboarding address'
              : 'One-time onboarding link'}
          </label>
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

        {/* WHAT WILL HAPPEN TO THIS IDENTITY, IN THE OPERATOR'S LANGUAGE.
            Nobody using this screen should need to know that customer tenancy
            is a membership row rather than a column. */}
        {look && look.exists && look.can_add && look.action === 'add_context' ? (
          <div style={{ border: '1px solid var(--gm-teal, #0d9488)',
                        background: 'var(--gm-pill-teal-bg, rgba(13,148,136,.08))',
                        borderRadius: 10, padding: '10px 12px', marginBottom: 12,
                        fontSize: 12.5, lineHeight: 1.55 }}>
            <b>Existing AdvisorFlow identity found</b>
            {look.user && look.user.full_name ? <> — {look.user.full_name}</> : null}.
            Their current access stays exactly as it is;{' '}
            <b>{orgName}</b> <b>{role}</b> access will be added as a separate
            workspace membership. No second account is created.
            {look.user && look.user.has_usable_login ? (
              <div style={{ marginTop: 6 }}>
                They already sign in to AdvisorFlow, so no password setup link
                is issued and their password is not touched — they reach this
                customer&rsquo;s onboarding with the credentials they have.
              </div>
            ) : null}
          </div>
        ) : null}

        {look && look.exists && look.can_add && look.action === 'reuse' ? (
          <div style={{ border: '1px solid var(--god-border, #e5e7eb)',
                        borderRadius: 10, padding: '10px 12px', marginBottom: 12,
                        fontSize: 12.5 }}>
            Already at this customer. Their existing account is reused and
            their access is confirmed, not duplicated.
          </div>
        ) : null}

        {refused ? (
          <div style={{ border: '1px solid #dc2626',
                        background: 'rgba(220,38,38,.06)', borderRadius: 10,
                        padding: '10px 12px', marginBottom: 12, fontSize: 12.5 }}>
            {look.reason}
          </div>
        ) : null}

        {err ? (
          <p style={{ fontSize: 12.5, color: '#dc2626' }}>{err}</p>
        ) : null}

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {looking ? (
            <span style={{ fontSize: 11.5, color: 'var(--gm-text, #94a3b8)' }}>
              Checking this address…
            </span>
          ) : null}
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
