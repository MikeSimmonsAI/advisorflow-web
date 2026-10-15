/* Forgot password: ask for a one-time reset link sent to the account's own
   inbox (POST /auth/forgot-password). Same answer whether or not the account
   exists. Route: /forgot-password */
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'

export default function ForgotPassword() {
  const [params] = useSearchParams()
  const [email, setEmail] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async e => {
    e.preventDefault()
    setBusy(true)
    try {
      const r = await api.post('/auth/forgot-password', { email, next: params.get('next') || undefined })
      setMsg(r.message)
    } catch (ex) { setMsg(ex.message || 'Please try again in a few minutes.') } finally { setBusy(false) }
  }
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f3f6f9', padding: 16,
                  font: '15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', color: '#0f1b2a' }}>
      <form onSubmit={submit} style={{ background: '#fff', border: '1px solid #e2e8ef', borderRadius: 16, padding: 24,
                                       width: '100%', maxWidth: 400 }}>
        <h1 style={{ margin: '0 0 6px', fontSize: 22 }}>Reset your password</h1>
        <p style={{ margin: '0 0 16px', color: '#5b6878' }}>Enter your sign-in email. We'll send a one-time link to that inbox.</p>
        {msg ? <p role="status" style={{ background: '#eef8f1', padding: 12, borderRadius: 10 }}>{msg}</p> : <>
          <input type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@company.com"
                 autoFocus style={{ width: '100%', boxSizing: 'border-box', padding: '11px 12px', borderRadius: 10,
                                    border: '1px solid #cfd8e3', fontSize: 16, marginBottom: 12 }} />
          <button disabled={busy} style={{ width: '100%', padding: 12, borderRadius: 10, border: 0, background: '#0b3a5b',
                                           color: '#fff', fontWeight: 700, fontSize: 15 }}>{busy ? 'Sending…' : 'Send reset link'}</button>
        </>}
        <p style={{ margin: '16px 0 0', textAlign: 'center' }}><a href="/login">Back to sign in</a></p>
      </form>
    </div>
  )
}
