import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setMustChangePassword } from '../api/client'
import SignalPulse from '../components/SignalPulse'
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
      })
      setMustChangePassword(false)
      setSuccess(true)
      setTimeout(() => navigate('/'), 1200)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <SignalPulse color="blue" size={10} />
          <span className="login-brand-mark">Advisor<span className="login-brand-accent">Flow</span></span>
        </div>
        <p className="login-subtitle">
          {forced ? 'Set a new password to continue' : 'Change your password'}
        </p>
        {forced && (
          <p className="login-help-text">
            Enter the temporary password you were given below, then choose a new password of your own.
          </p>
        )}

        {success ? (
          <div style={{ textAlign: 'center', color: 'var(--signal-green)', fontSize: 14, padding: '20px 0' }}>
            Password updated. Taking you in…
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="login-form">
            <label className="login-label">
              {forced ? 'Temporary password' : 'Current password'}
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                required
                autoFocus
                className="login-input"
                placeholder={forced ? 'The temporary password you were given' : undefined}
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
            <p className="login-help-text login-help-text--rules">
              Must be at least 8 characters. No other requirements.
            </p>
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

            <button type="submit" className="login-submit" disabled={loading}>
              {loading ? 'Updating…' : 'Update password'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
