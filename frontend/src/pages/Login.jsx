import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { login, fetchAndStoreBranding, startKeepAlive } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import { detectTheme, THEMES, BRAND_CONFIG } from '../theme.js'
import './Login.css'

const theme = detectTheme()
const brand = BRAND_CONFIG[theme]

// Per-platform login page copy
const PLATFORM_COPY = {
  [THEMES.ADVISORFLOW]: {
    headline: '⚡ AdvisorFlow',
    tagline: 'God-level access. Authorized personnel only.',
    accentStyle: { color: '#f59e0b', letterSpacing: '0.02em' },
  },
  [THEMES.EVOSYSPRO]: {
    headline: 'EvoSys Pro',
    tagline: 'Enterprise outreach console. Sign in to continue.',
    accentStyle: { color: '#087cff' },
  },
  [THEMES.HARMONYHUSTLE]: {
    headline: 'Harmony Hustle',
    tagline: 'Sign in to your outreach console.',
    accentStyle: { color: '#10b981' },
  },
  [THEMES.BOOKABOOST]: {
    headline: 'BookaBoost',
    tagline: 'Sign in to your outreach console.',
    accentStyle: {},
  },
}

const copy = PLATFORM_COPY[theme] || PLATFORM_COPY[THEMES.BOOKABOOST]

export default function Login() {
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
      const user = await login(email, password)
      await fetchAndStoreBranding()
      startKeepAlive()
      navigate(user?.role === 'god_admin' ? '/god' : '/')
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
          <span className="login-brand-mark" style={copy.accentStyle}>{copy.headline}</span>
        </div>
        <p className="login-subtitle">{copy.tagline}</p>

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

        <div className="login-support">
          Need help? <a href={`mailto:${brand.supportEmail}`}>{brand.supportEmail}</a>
        </div>
      </div>
    </div>
  )
}
