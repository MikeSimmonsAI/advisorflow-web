import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { login, fetchAndStoreBranding, startKeepAlive, startRefreshLoop,
         fetchMyContexts, setWorkspaceContext, clearWorkspaceContext } from '../api/client'
import SignalPulse from '../components/SignalPulse'
import { detectTheme, THEMES, BRAND_CONFIG } from '../theme.js'
import './Login.css'

const theme = detectTheme()
const brand = BRAND_CONFIG[theme]

// ── Per-platform left-panel content ──────────────────────────────────────────

const PLATFORM_CONTENT = {
  [THEMES.BOOKABOOST]: {
    headline: 'BookaBoost',
    tagline: 'Pre-need outreach built for families who plan ahead — and the advisors who serve them.',
    accentColor: '#c9973d',
    accentGlow: 'rgba(201,151,61,0.15)',
    bgGradient: 'linear-gradient(135deg, #faf8f4 0%, #f5f0e8 100%)',
    panelGradient: 'linear-gradient(160deg, #2c1f10 0%, #3d2b14 100%)',
    lightRight: true,
    poweredBy: 'BookaBoost',
    stats: [
      { value: '4.8×', label: 'avg reply rate vs cold outreach' },
      { value: '72%', label: 'of pre-need leads go cold within 90 days' },
      { value: '3 min', label: 'to launch your first AI cadence' },
      { value: '100%', label: 'A2P 10DLC compliant messaging' },
    ],
    features: [
      { icon: '📱', text: 'Automated SMS & email cadences' },
      { icon: '🔥', text: 'Hot / Warm / Cold lead scoring' },
      { icon: '📋', text: 'Funeral-industry CRM built in' },
      { icon: '🔗', text: 'GoHighLevel & HubSpot sync' },
    ],
    badge: 'Pre-Need Edition',
    badgeStyle: { background: 'rgba(201,151,61,0.18)', color: '#c9973d', border: '1px solid rgba(201,151,61,0.4)' },
  },
  [THEMES.ADVISORFLOW]: {
    headline: '⚡ AdvisorFlow',
    tagline: 'God-level access. Authorized personnel only.',
    accentColor: '#f59e0b',
    accentGlow: 'rgba(245,158,11,0.15)',
    bgGradient: 'linear-gradient(135deg, #0f0a00 0%, #1a1200 50%, #110e00 100%)',
    panelGradient: 'linear-gradient(160deg, #1a1200 0%, #0f0a00 100%)',
    poweredBy: 'AdvisorFlow Platform',
    stats: [
      { value: '∞', label: 'organizations managed from one console' },
      { value: '100%', label: 'platform visibility and override access' },
      { value: 'Live', label: 'real-time org health and activity' },
      { value: 'Zero', label: 'tolerances for unauthorized access' },
    ],
    features: [
      { icon: '🏢', text: 'Full multi-org management' },
      { icon: '🔐', text: 'God-admin credential required' },
      { icon: '📊', text: 'Platform-wide analytics' },
      { icon: '⚙️', text: 'Billing, seats, and role control' },
    ],
    badge: 'Restricted Access',
    badgeStyle: { background: 'rgba(245,158,11,0.1)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)' },
  },
  [THEMES.EVOSYSPRO]: {
    headline: 'EvoSys Pro',
    tagline: 'Enterprise outreach console.',
    accentColor: '#087cff',
    accentGlow: 'rgba(8,124,255,0.15)',
    bgGradient: 'linear-gradient(135deg, #04101e 0%, #071828 50%, #040e1c 100%)',
    panelGradient: 'linear-gradient(160deg, #071828 0%, #04101e 100%)',
    poweredBy: 'EvoSys Pro',
    stats: [
      { value: '10×', label: 'outreach velocity vs manual' },
      { value: '24/7', label: 'automated cadence running' },
      { value: 'Multi', label: 'channel SMS, email, and voice' },
      { value: 'Live', label: 'pipeline and conversion tracking' },
    ],
    features: [
      { icon: '🚀', text: 'Enterprise-grade automation' },
      { icon: '📈', text: 'Real-time conversion analytics' },
      { icon: '🔗', text: 'Deep CRM integrations' },
      { icon: '🛡️', text: 'SOC-2 ready data practices' },
    ],
    badge: 'Enterprise',
    badgeStyle: { background: 'rgba(8,124,255,0.1)', color: '#087cff', border: '1px solid rgba(8,124,255,0.3)' },
  },
  [THEMES.HARMONYHUSTLE]: {
    headline: 'Harmony Hustle',
    tagline: 'Real estate outreach, reimagined.',
    accentColor: '#10b981',
    accentGlow: 'rgba(16,185,129,0.15)',
    bgGradient: 'linear-gradient(135deg, #020f08 0%, #051a0e 50%, #020f08 100%)',
    panelGradient: 'linear-gradient(160deg, #051a0e 0%, #020f08 100%)',
    poweredBy: 'Harmony Hustle',
    stats: [
      { value: '5×', label: 'more listings from past clients' },
      { value: '60%', label: 'of buyers search online first' },
      { value: '14 day', label: 'average to first response' },
      { value: '100%', label: 'automated follow-up cadences' },
    ],
    features: [
      { icon: '🏡', text: 'Real estate lead nurturing' },
      { icon: '📲', text: 'Automated SMS drip campaigns' },
      { icon: '📊', text: 'Pipeline and close tracking' },
      { icon: '🔗', text: 'MLS and CRM integrations' },
    ],
    badge: 'Real Estate Edition',
    badgeStyle: { background: 'rgba(16,185,129,0.1)', color: '#10b981', border: '1px solid rgba(16,185,129,0.3)' },
  },
}

const content = PLATFORM_CONTENT[theme] || PLATFORM_CONTENT[THEMES.BOOKABOOST]

// Animated ticker — cycles through stats
function StatTicker({ stats, accentColor }) {
  const [idx, setIdx] = useState(0)
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const interval = setInterval(() => {
      setVisible(false)
      setTimeout(() => {
        setIdx(i => (i + 1) % stats.length)
        setVisible(true)
      }, 300)
    }, 3000)
    return () => clearInterval(interval)
  }, [stats.length])

  const stat = stats[idx]
  return (
    <div className="login-stat-ticker" style={{ opacity: visible ? 1 : 0, transition: 'opacity 0.3s' }}>
      <span className="login-stat-value" style={{ color: accentColor }}>{stat.value}</span>
      <span className="login-stat-desc">{stat.label}</span>
    </div>
  )
}

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
      startRefreshLoop()
      // WHERE LOGIN LANDS — the SERVER decides, not a role label.
      //
      // This used to be `role === 'god_admin' ? '/god' : '/'`, one branch for
      // one column. It sent a workspace-only customer and a platform-only
      // salesperson to the same tenant home, and the salesperson's version of
      // that home is a screen belonging to the other domain.
      //
      // /auth/my-contexts returns a default_context built from real
      // memberships: the back office when they have one, their single
      // workspace when that is all they hold, a selector when they hold
      // several. A failure falls back to the old behaviour rather than
      // stranding somebody at a blank page.
      let dest = user?.role === 'god_admin' ? '/god' : '/'
      try {
        const ctx = await fetchMyContexts()
        const def = ctx && ctx.default_context
        if (def && def.path) {
          dest = def.path
          if (def.type === 'workspace' && def.organization_id) {
            setWorkspaceContext(def.organization_id)
          } else {
            clearWorkspaceContext()
          }
        }
      } catch (ctxErr) {
        // Keep the legacy destination. A context lookup that fails must not
        // turn a successful sign-in into an error screen.
      }
      navigate(dest)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const isAdvisorFlow = theme === THEMES.ADVISORFLOW

  return (
    <div className={`login-page${content.lightRight ? ' login-page--light' : ''}`} style={{ background: content.bgGradient }}>

      {/* Ambient orbs */}
      <div className="login-orb login-orb--1" style={{ background: content.accentGlow }} />
      <div className="login-orb login-orb--2" style={{ background: content.accentGlow }} />

      {/* LEFT PANEL — marketing content */}
      <div className="login-left" style={{ background: content.panelGradient }}>

        {/* Badge */}
        <div className="login-lp-badge" style={content.badgeStyle}>{content.badge}</div>

        {/* Brand */}
        <div className="login-lp-brand">
          <SignalPulse color={theme === THEMES.EVOSYSPRO || theme === THEMES.HARMONYHUSTLE ? 'blue' : 'amber'} size={12} />
          <span className="login-lp-name" style={{ color: content.accentColor }}>{content.headline}</span>
        </div>

        <h2 className="login-lp-headline">{content.tagline}</h2>

        {/* Animated stat ticker */}
        <StatTicker stats={content.stats} accentColor={content.accentColor} />

        {/* All stats grid */}
        <div className="login-stats-grid">
          {content.stats.map((s, i) => (
            <div key={i} className="login-stat-card">
              <div className="login-stat-card-val" style={{ color: content.accentColor }}>{s.value}</div>
              <div className="login-stat-card-lbl">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Feature list */}
        <div className="login-features">
          {content.features.map((f, i) => (
            <div key={i} className="login-feature">
              <span className="login-feature-icon">{f.icon}</span>
              <span className="login-feature-text">{f.text}</span>
            </div>
          ))}
        </div>

        {/* Bottom watermark */}
        <div className="login-lp-footer">
          Powered by <span style={{ color: content.accentColor }}>{content.poweredBy}</span>
        </div>
      </div>

      {/* RIGHT PANEL — login form */}
      <div className="login-right">
        <div className="login-card">

          {/* Mobile-only brand (hidden on desktop) */}
          <div className="login-mobile-brand">
            <SignalPulse color={isAdvisorFlow ? 'amber' : 'blue'} size={9} />
            <span style={{ color: content.accentColor, fontWeight: 700, fontSize: 18 }}>{content.headline}</span>
          </div>

          <h3 className="login-card-title">Sign in</h3>
          <p className="login-card-sub">Enter your credentials to continue</p>

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
                style={{ '--focus-color': content.accentColor }}
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
                style={{ '--focus-color': content.accentColor }}
              />
            </label>

            {error && <div className="login-error">{error}</div>}

            <button
              type="submit"
              className="login-submit"
              disabled={loading}
              style={{ background: content.accentColor }}
            >
              {loading ? 'Signing in…' : 'Sign in →'}
            </button>
          </form>

          {isAdvisorFlow && (
            <div className="login-restricted-notice">
              🔐 Restricted to authorized personnel only
            </div>
          )}

          <div className="login-support">
            Need help? <a href={`mailto:${brand.supportEmail}`}>{brand.supportEmail}</a>
          </div>
        </div>
      </div>
    </div>
  )
}
