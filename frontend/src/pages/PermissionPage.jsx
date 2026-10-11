/* The OWNER's permission page (public, no account). The private link is the
 * whole authorization; see app/routers/permission_public_router.py.
 * Shows the business name, the owner's street and the last four digits of the
 * number - nothing else. The box starts unticked; only the owner's own tick +
 * Confirm records permission. "No thanks" records only the answer. */
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE } from '../api/client'

const wrap = { minHeight: '100vh', background: '#f4f6f9', fontFamily: 'Arial, Helvetica, sans-serif', color: '#1d2433', padding: '24px 16px' }
const card = { maxWidth: 520, margin: '0 auto', background: '#fff', borderRadius: 12, padding: '28px 24px', boxShadow: '0 2px 12px rgba(0,0,0,.08)' }
const btn = { fontSize: 17, padding: '12px 18px', borderRadius: 8, border: 0, cursor: 'pointer', width: '100%', marginTop: 10 }

export default function PermissionPage() {
  const { token } = useParams()
  const [d, setD] = useState(null)
  const [agree, setAgree] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    fetch(`${API_BASE}/permission/${encodeURIComponent(token)}/data`)
      .then((r) => r.json()).then(setD).catch(() => setD({ status: 'error' }))
  }, [token])

  async function send(answer) {
    setBusy(true); setErr('')
    try {
      const r = await fetch(`${API_BASE}/permission/${encodeURIComponent(token)}/answer`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer, agree: answer === 'confirm' ? agree : false }),
      })
      const j = await r.json()
      if (!r.ok) { setErr(j.detail || 'Please tick the box to confirm.'); return }
      setD(j)
    } catch { setErr('Something went wrong. Please try again.') } finally { setBusy(false) }
  }

  let body
  if (!d) body = <p>Loading…</p>
  else if (d.status === 'sent') {
    body = (
      <>
        <h1 style={{ fontSize: 22, margin: '0 0 12px' }}>{d.first_name ? `Hi ${d.first_name},` : 'Hello,'}</h1>
        <p style={{ lineHeight: 1.5 }}>
          {d.business} would like to follow up with you about your property{d.street ? ` at ${d.street}` : ''}.
          Please confirm whether that's okay.
        </p>
        <label style={{ display: 'flex', gap: 12, alignItems: 'flex-start', background: '#f8fafc', border: '1px solid #d6dde8', borderRadius: 8, padding: 14, margin: '18px 0', fontSize: 15, lineHeight: 1.5 }}>
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)}
                 style={{ width: 22, height: 22, flex: '0 0 auto', marginTop: 2 }} />
          <span>{d.disclosure}</span>
        </label>
        {err ? <p style={{ color: '#b42318' }}>{err}</p> : null}
        <button type="button" style={{ ...btn, background: '#1f3864', color: '#fff', opacity: agree ? 1 : 0.6 }}
                disabled={busy || !agree} onClick={() => send('confirm')}>Confirm</button>
        <button type="button" style={{ ...btn, background: '#e9edf3', color: '#1d2433' }}
                disabled={busy} onClick={() => send('decline')}>No thanks</button>
        <p style={{ fontSize: 13, color: '#5b6577', marginTop: 14 }}>Confirming is optional. Number on file ends in {d.phone_last4}.</p>
      </>
    )
  } else if (d.status === 'confirmed') {
    body = <><h1 style={{ fontSize: 22 }}>Thank you!</h1><p>You're all set. {d.business} will follow up with you soon. You can reply STOP to any text to opt out.</p></>
  } else if (d.status === 'declined') {
    body = <><h1 style={{ fontSize: 22 }}>All set</h1><p>You chose not to receive follow-up. Nothing else is needed.</p></>
  } else if (d.status === 'expired') {
    body = <><h1 style={{ fontSize: 22 }}>This link has expired</h1><p>If you'd still like us to follow up, just let us know.</p></>
  } else {
    body = <><h1 style={{ fontSize: 22 }}>This link isn't valid</h1><p>Please contact the person who sent it to you.</p></>
  }
  return <main style={wrap}><div style={card}>{body}</div></main>
}
