/**
 * RESET PASSWORD — the owner's administrative override.
 *
 * Separate from ConfirmDialog on purpose. ConfirmDialog's whole job is "you are
 * about to do X, yes or no" and it says so in its own docstring; this asks for
 * input, and a form whose Confirm button can be pressed while the two fields
 * disagree is a different failure mode entirely.
 *
 * ── WHAT THIS SCREEN REFUSES TO DO ────────────────────────────────────────
 * It never displays, stores, echoes or logs the password. The value lives in
 * component state for exactly as long as the dialog is open and leaves in one
 * request body. Nothing is written to localStorage, nothing lands in a URL, and
 * the fields are type="password" with autoComplete="new-password" so the
 * browser does not offer to remember somebody else's credential.
 *
 * The confirmation is enforced here AND on the server. The server check is the
 * real one; this exists so the operator finds out before they lock a live
 * account, not after.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'

const MIN_LENGTH = 8

export default function ResetPasswordDialog({ user, onCancel, onDone }) {
  const [pw, setPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [forceChange, setForceChange] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape' && !busy) onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onCancel])

  const tooShort = pw.length > 0 && pw.length < MIN_LENGTH
  const mismatch = confirmPw.length > 0 && confirmPw !== pw
  const ready = pw.length >= MIN_LENGTH && confirmPw === pw && !busy

  async function submit(e) {
    e.preventDefault()
    if (!ready) return
    setBusy(true); setErr('')
    try {
      await api.post(`/admin/users/${user.id}/reset-password`, {
        new_password: pw,
        confirm_password: confirmPw,
        must_change_password: forceChange,
      })
      // Cleared before the parent re-renders, so the value does not sit in a
      // mounted component while the list reloads behind the dialog.
      setPw(''); setConfirmPw('')
      onDone(forceChange)
    } catch (e2) {
      setErr(e2?.message || 'The reset was refused.')
      setBusy(false)
    }
  }

  const line = 'rgba(255,217,104,.42)'

  return (
    <div
      role="dialog" aria-modal="true" aria-label="Reset password"
      onClick={() => { if (!busy) onCancel() }}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(1,4,9,.72)', zIndex: 400,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 18,
      }}
    >
      <form
        className="gm-card"
        onClick={e => e.stopPropagation()}
        onSubmit={submit}
        style={{ borderColor: line, padding: 24, maxWidth: 460, width: '100%' }}
      >
        <div style={{ color: '#ffd968', fontSize: 9, fontWeight: 800, letterSpacing: '.12em', marginBottom: 11 }}>
          ⚿ RESET PASSWORD
        </div>
        <div style={{ color: '#f1f7ff', fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
          {user.full_name || user.email}
        </div>
        <div style={{ color: '#758ba4', fontSize: 11, marginBottom: 14 }}>{user.email}</div>

        <div style={{ color: '#7f96ae', fontSize: 11.5, lineHeight: 1.65, marginBottom: 18 }}>
          Sets this account&rsquo;s password immediately. It is never shown back to
          you and never written to the audit log &mdash; only the fact that you
          reset it. Any session this person currently has signed in is ended, so
          they will sign in again with what you set here.
        </div>

        <label style={{ display: 'block', color: '#8fb6cf', fontSize: 9.5, fontWeight: 800,
                        letterSpacing: '.1em', marginBottom: 6 }}>
          NEW PASSWORD
        </label>
        <input
          className="gm-input" type="password" autoComplete="new-password"
          value={pw} onChange={e => setPw(e.target.value)} disabled={busy}
          style={{ width: '100%', marginBottom: tooShort ? 5 : 14 }}
          placeholder={`at least ${MIN_LENGTH} characters`}
        />
        {tooShort && (
          <div style={{ color: '#ff8299', fontSize: 10, marginBottom: 12 }}>
            Must be at least {MIN_LENGTH} characters.
          </div>
        )}

        <label style={{ display: 'block', color: '#8fb6cf', fontSize: 9.5, fontWeight: 800,
                        letterSpacing: '.1em', marginBottom: 6 }}>
          CONFIRM NEW PASSWORD
        </label>
        <input
          className="gm-input" type="password" autoComplete="new-password"
          value={confirmPw} onChange={e => setConfirmPw(e.target.value)} disabled={busy}
          style={{ width: '100%', marginBottom: mismatch ? 5 : 16 }}
          placeholder="type it again"
        />
        {mismatch && (
          <div style={{ color: '#ff8299', fontSize: 10, marginBottom: 14 }}>
            The two passwords do not match.
          </div>
        )}

        <label style={{ display: 'flex', alignItems: 'flex-start', gap: 9, cursor: 'pointer',
                        marginBottom: 18, color: '#8fb6cf', fontSize: 11, lineHeight: 1.55 }}>
          <input
            type="checkbox" checked={forceChange} disabled={busy}
            onChange={e => setForceChange(e.target.checked)}
            style={{ marginTop: 2 }}
          />
          <div>
            Require them to choose their own password at next sign-in.
            <div style={{ color: '#5f768e', fontSize: 10, marginTop: 3 }}>
              Leave this on when you are handing over a temporary password.
              Turn it off only when this password is meant to be permanent.
            </div>
          </div>
        </label>

        {err && (
          <div style={{ color: '#ff8299', fontSize: 11, marginBottom: 14, lineHeight: 1.5 }}>
            {err}
          </div>
        )}

        <div style={{ display: 'flex', gap: 9 }}>
          <button type="button" className="gm-btn" style={{ flex: 1, padding: '9px 0' }}
                  onClick={onCancel} disabled={busy}>
            CANCEL
          </button>
          <button
            type="submit"
            className="gm-btn"
            disabled={!ready}
            style={{
              flex: 1, padding: '9px 0', fontWeight: 800,
              color: ready ? '#ffd968' : '#5f768e',
              borderColor: ready ? line : 'rgba(120,150,180,.22)',
              background: ready ? '#1b1505' : 'transparent',
              cursor: ready ? 'pointer' : 'not-allowed',
            }}
          >
            {busy ? 'WORKING…' : 'RESET PASSWORD'}
          </button>
        </div>
      </form>
    </div>
  )
}
