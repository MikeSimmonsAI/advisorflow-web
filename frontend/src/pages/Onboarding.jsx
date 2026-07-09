import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './Onboarding.css'

const INDUSTRIES = [
  { value: 'funeral', label: '⚰️ Funeral & Cemetery' },
  { value: 'roofing', label: '🏠 Roofing' },
  { value: 'insurance', label: '🛡 Insurance' },
  { value: 'real_estate', label: '🏡 Real Estate' },
  { value: 'dental', label: '🦷 Dental' },
  { value: 'legal', label: '⚖️ Legal' },
]

const STEPS = ['Business', 'Account', 'Done']

export default function Onboarding() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [form, setForm] = useState({
    business_name: '',
    industry: 'funeral',
    admin_full_name: '',
    admin_email: '',
    admin_password: '',
    confirm_password: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  function update(field, value) {
    setForm((p) => ({ ...p, [field]: value }))
  }

  async function handleRegister() {
    setError('')
    if (form.admin_password !== form.confirm_password) {
      setError('Passwords do not match.')
      return
    }
    if (form.admin_password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    setSaving(true)
    try {
      const res = await api.post('/onboarding/register', {
        business_name: form.business_name,
        industry: form.industry,
        admin_full_name: form.admin_full_name,
        admin_email: form.admin_email,
        admin_password: form.admin_password,
      })
      setResult(res)
      // Store token and log in
      localStorage.setItem('bookaboost_token', res.access_token)
      setStep(2)
    } catch (err) {
      setError(err.message || 'Could not create account. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  function canAdvanceStep0() {
    return form.business_name.trim().length >= 2 && form.industry
  }

  function canAdvanceStep1() {
    return form.admin_full_name.trim().length >= 2 &&
      form.admin_email.includes('@') &&
      form.admin_password.length >= 8 &&
      form.admin_password === form.confirm_password
  }

  return (
    <div className="onboarding-page">
      <div className="onboarding-card">
        <div className="onboarding-logo">
          <span className="onboarding-logo-text">Booka<strong>Boost</strong></span>
        </div>

        <div className="onboarding-steps">
          {STEPS.map((label, i) => (
            <div key={label} className={`onboarding-step ${i === step ? 'onboarding-step--active' : i < step ? 'onboarding-step--done' : ''}`}>
              <div className="onboarding-step-dot">{i < step ? '✓' : i + 1}</div>
              <span>{label}</span>
            </div>
          ))}
        </div>

        {step === 0 && (
          <div className="onboarding-form">
            <h2 className="onboarding-title">Tell us about your business</h2>
            <p className="onboarding-subtitle">We'll set up your account for your industry.</p>

            <label className="onboarding-label">
              Business name
              <input
                className="onboarding-input"
                value={form.business_name}
                onChange={(e) => update('business_name', e.target.value)}
                placeholder="Restland Cemetery & Funeral Home"
                autoFocus
              />
            </label>

            <label className="onboarding-label">
              Industry
              <div className="onboarding-industry-grid">
                {INDUSTRIES.map((ind) => (
                  <button
                    key={ind.value}
                    className={`onboarding-industry-btn ${form.industry === ind.value ? 'onboarding-industry-btn--active' : ''}`}
                    onClick={() => update('industry', ind.value)}
                  >
                    {ind.label}
                  </button>
                ))}
              </div>
            </label>

            <button
              className="btn btn--primary onboarding-next-btn"
              onClick={() => setStep(1)}
              disabled={!canAdvanceStep0()}
            >
              Continue →
            </button>
          </div>
        )}

        {step === 1 && (
          <div className="onboarding-form">
            <h2 className="onboarding-title">Create your admin account</h2>
            <p className="onboarding-subtitle">You'll use this to log in and manage your team.</p>

            <label className="onboarding-label">
              Full name
              <input className="onboarding-input" value={form.admin_full_name} onChange={(e) => update('admin_full_name', e.target.value)} placeholder="Mike Simmons" autoFocus />
            </label>
            <label className="onboarding-label">
              Email address
              <input className="onboarding-input" type="email" value={form.admin_email} onChange={(e) => update('admin_email', e.target.value)} placeholder="mike@yourbusiness.com" />
            </label>
            <label className="onboarding-label">
              Password
              <input className="onboarding-input" type="password" value={form.admin_password} onChange={(e) => update('admin_password', e.target.value)} placeholder="At least 8 characters" />
            </label>
            <label className="onboarding-label">
              Confirm password
              <input className="onboarding-input" type="password" value={form.confirm_password} onChange={(e) => update('confirm_password', e.target.value)} placeholder="Repeat password" />
            </label>

            {error && <div className="onboarding-error">{error}</div>}

            <div className="onboarding-btn-row">
              <button className="btn btn--secondary" onClick={() => setStep(0)}>← Back</button>
              <button
                className="btn btn--primary onboarding-next-btn"
                onClick={handleRegister}
                disabled={saving || !canAdvanceStep1()}
              >
                {saving ? 'Creating account…' : 'Create account'}
              </button>
            </div>
          </div>
        )}

        {step === 2 && result && (
          <div className="onboarding-form onboarding-success">
            <div className="onboarding-success-icon">🎉</div>
            <h2 className="onboarding-title">You're all set!</h2>
            <p className="onboarding-subtitle">
              <strong>{result.org_name}</strong> is ready to go. Your 14-day trial starts now.
            </p>
            <div className="onboarding-success-checklist">
              <div className="onboarding-check-item">✓ Organization created</div>
              <div className="onboarding-check-item">✓ Admin account ready</div>
              <div className="onboarding-check-item">✓ Trial plan active — 14 days free</div>
            </div>
            <p className="onboarding-next-steps">
              Next: import your leads, set up your Twilio number in Settings, and start your first cadence.
            </p>
            <button className="btn btn--primary onboarding-next-btn" onClick={() => navigate('/overview')}>
              Go to dashboard →
            </button>
          </div>
        )}

        <p className="onboarding-login-link">
          Already have an account? <span className="onboarding-link" onClick={() => navigate('/login')}>Sign in</span>
        </p>
      </div>
    </div>
  )
}
