import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, clearToken, setMustChangePassword } from '../api/client'
import './Login.css'

export default function ChangePassword({ forced = false }) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')

    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters.')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('New password and confirmation do not match.')
      return
    }

    setLoading(true)
    try {
      await api.post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
        // Sent so the SERVER can enforce the match too. The check above is for
        // the person's benefit; this one is the guarantee.
        confirm_password: confirmPassword,
      })
      // The server clears this account's session on success, so every token
      // ever issued for it — including the one sitting in this browser — is
      // now refused. Keeping it would only produce a confusing 401 on the next
      // click, so it is dropped here and the person signs in again with the
      // password they just set. That fresh sign-in IS the confirmation the
      // change took effect.
      setMustChangePassword(false)
      clearToken()
      // Neither password is left in component state after this point.
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('')
      setSuccess(true)
      setTimeout(() => navigate('/login', { replace: true }), 1400)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    // Login.css is written for the dark backdrop the Login page paints with
    // inline styles. This page borrowed its classes without the backdrop, so
    // every label, field and the button were white on near-white (1.04:1) -
    // on the screen every person with a temporary password is sent to first.
    // The backdrop and button colour are set here, and the old internal
    // product name ("AdvisorFlow") is no longer shown to customers.
    <div className="login-page" style={{ background: '#0b1220', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div className="login-card">
        <h1 className="login-card-title" style={{ color: '#f4f7fb' }}>
          {forced ? 'Set a new password to continue' : 'Change your password'}
        </h1>
        <p className="login-card-sub" style={{ color: 'rgba(255, 255, 255, 0.72)' }}>
          At least 8 characters. Every session for this account is signed out when it changes.
        </p>

        {success ? (
          <div style={{ textAlign: 'center', color: 'var(--signal-green)', fontSize: 14, padding: '20px 0' }}>
            Password updated. Every session for this account has been signed
            out — sign in again with your new password.
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="login-form">
            <label className="login-label">
              Current password
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                required
                autoFocus
                className="login-input"
              />
            </label>
            <label className="login-label">
              New password
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
                className="login-input"
                placeholder="At least 8 characters"
              />
            </label>
            <label className="login-label">
              Confirm new password
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                className="login-input"
              />
            </label>

            {error && <div className="login-error">{error}</div>}

            <button type="submit" className="login-submit" disabled={loading} style={{ background: '#2563eb' }}>
              {loading ? 'Updating…' : 'Update password'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
