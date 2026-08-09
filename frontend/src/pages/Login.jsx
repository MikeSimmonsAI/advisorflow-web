import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { login, fetchAndStoreBranding } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import './Login.css'

const PLATFORMS = {
  evosyspro: { brand: 'EVO', accent: 'Syspro', tagline: 'Sign in to your outreach console' },
  default:    { brand: 'Booka', accent: 'Boost',  tagline: 'Sign in to your outreach console' },
}

function getPlatform() {
  const host = window.location.hostname
  if (host.includes('evosyspro')) return PLATFORMS.evosyspro
  return PLATFORMS.default
}

export default function Login() {
  const platform = getPlatform()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email, password)
      await fetchAndStoreBranding()
      navigate('/')
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
          <span className="login-brand-mark">{platform.brand}<span className="login-brand-accent">{platform.accent}</span></span>
        </div>
        <p className="login-subtitle">{platform.tagline}</p>

        <form onSubmit={handleSubmit} className="login-form">
          <label className="login-label">
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              className="login-input"
              placeholder="you@company.com"
            />
          </label>
          <label className="login-label">
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="login-input"
              placeholder="••••••••"
            />
          </label>

          {error && <div className="login-error">{error}</div>}

          <button type="submit" className="login-submit" disabled={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
