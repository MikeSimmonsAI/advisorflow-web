import { useEffect, useState } from 'react'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'

const STEPS = [
  { key: 'service', num: 1, label: 'Create Messaging Service' },
  { key: 'brand', num: 2, label: 'Register Brand (EIN)' },
  { key: 'campaign', num: 3, label: 'Register Campaign' },
  { key: 'numbers', num: 4, label: 'Add Phone Numbers' },
]

const USE_CASES = [
  { value: 'MIXED', label: 'Mixed — Scheduling + Follow-up (recommended)' },
  { value: 'APPOINTMENT_REMINDER', label: 'Appointment Reminders' },
  { value: 'MARKETING', label: 'Marketing' },
  { value: 'CUSTOMER_CARE', label: 'Customer Care' },
]

const VERTICALS = [
  { value: 'REAL_ESTATE', label: 'Real Estate / Funeral Home Services' },
  { value: 'INSURANCE', label: 'Insurance' },
  { value: 'HEALTHCARE', label: 'Healthcare' },
  { value: 'PROFESSIONAL_SERVICES', label: 'Professional Services' },
  { value: 'FINANCIAL', label: 'Financial Services' },
]

function StatusBadge({ status }) {
  if (!status) return <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>—</span>
  const s = status.toUpperCase()
  const color = s === 'APPROVED' || s === 'ACTIVE' ? 'var(--signal-green)'
    : s === 'PENDING' || s === 'IN_PROGRESS' ? '#b8892a'
    : s === 'FAILED' || s === 'REJECTED' ? 'var(--signal-red)'
    : 'var(--text-secondary)'
  return (
    <span style={{
      background: `${color}22`,
      color,
      border: `1px solid ${color}44`,
      borderRadius: 6,
      padding: '2px 10px',
      fontSize: 12,
      fontWeight: 700,
      letterSpacing: '0.04em',
    }}>
      {status}
    </span>
  )
}

export default function DLCRegistration() {
  const currentUser = getCurrentUser()
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [activeStep, setActiveStep] = useState(null)
  const [working, setWorking] = useState(false)
  const [result, setResult] = useState(null)

  // Brand form
  const [brand, setBrand] = useState({
    company_name: '',
    ein: '',
    website: '',
    address_street: '',
    address_city: '',
    address_state: '',
    address_zip: '',
    contact_first_name: '',
    contact_last_name: '',
    contact_email: '',
    contact_phone: '',
    business_type: 'PRIVATE_PROFIT',
    vertical: 'REAL_ESTATE',
  })

  // Campaign form
  const [campaign, setCampaign] = useState({
    description: 'We send appointment scheduling messages, reminders, and follow-up texts to customers who have provided verbal consent during in-person consultations with our advisors.',
    message_flow: 'Customers provide verbal consent during in-person consultations or phone calls with our Family Service Advisors. Advisors log consent in BookaBoost at time of collection.',
    sample_message_1: "Hi {first_name}, this is {advisor_name} from our office. I wanted to follow up about your appointment. Would you like to schedule a time to meet? Reply STOP to opt out.",
    sample_message_2: "Reminder: Your appointment is tomorrow. Reply STOP to opt out at any time.",
    use_case: 'MIXED',
    has_embedded_links: true,
    has_embedded_phone: false,
  })

  useEffect(() => {
    loadStatus()
  }, [])

  async function loadStatus() {
    setLoading(true)
    try {
      const s = await api.get('/10dlc/status')
      setStatus(s)
    } catch (err) {
      setResult({ type: 'error', text: `Failed to load status: ${err.message}` })
    } finally {
      setLoading(false)
    }
  }

  async function runStep1() {
    setWorking(true)
    setResult(null)
    try {
      const r = await api.post('/10dlc/create-messaging-service', {})
      setResult({ type: 'success', text: r.created ? `Messaging Service created: ${r.messaging_service_sid}` : `Existing Messaging Service: ${r.messaging_service_sid}` })
      await loadStatus()
    } catch (err) {
      setResult({ type: 'error', text: err.message })
    } finally {
      setWorking(false)
    }
  }

  async function runStep2() {
    if (!brand.company_name || !brand.ein || !brand.website || !brand.address_street || !brand.contact_email) {
      setResult({ type: 'error', text: 'Please fill in all required brand fields.' })
      return
    }
    setWorking(true)
    setResult(null)
    try {
      const r = await api.post('/10dlc/register-brand', brand)
      setResult({ type: 'success', text: `Brand submitted — SID: ${r.brand_sid} · Status: ${r.brand_status}` })
      await loadStatus()
    } catch (err) {
      setResult({ type: 'error', text: err.message })
    } finally {
      setWorking(false)
    }
  }

  async function runStep3() {
    if (!campaign.description || !campaign.message_flow || !campaign.sample_message_1) {
      setResult({ type: 'error', text: 'Please fill in all required campaign fields.' })
      return
    }
    setWorking(true)
    setResult(null)
    try {
      const r = await api.post('/10dlc/register-campaign', campaign)
      setResult({ type: 'success', text: `Campaign submitted — SID: ${r.campaign_sid} · Status: ${r.campaign_status}` })
      await loadStatus()
    } catch (err) {
      setResult({ type: 'error', text: err.message })
    } finally {
      setWorking(false)
    }
  }

  async function runStep4() {
    setWorking(true)
    setResult(null)
    try {
      const r = await api.post('/10dlc/add-phone-number', {})
      const successes = r.results.filter(x => x.success).length
      const failures = r.results.filter(x => !x.success).length
      setResult({
        type: failures === 0 ? 'success' : successes > 0 ? 'warn' : 'error',
        text: `${successes} number(s) added successfully.${failures > 0 ? ` ${failures} failed — check that the numbers exist in your Twilio account.` : ''}`,
      })
    } catch (err) {
      setResult({ type: 'error', text: err.message })
    } finally {
      setWorking(false)
    }
  }

  async function refreshStatus() {
    setWorking(true)
    setResult(null)
    try {
      const r = await api.post('/10dlc/refresh-status', {})
      setStatus(r)
      setResult({ type: 'success', text: 'Status refreshed from Twilio.' })
    } catch (err) {
      setResult({ type: 'error', text: err.message })
    } finally {
      setWorking(false)
    }
  }

  const hasService = !!status?.messaging_service_sid
  const hasBrand = !!status?.brand_sid
  const hasCampaign = !!status?.campaign_sid

  return (
    <div style={{ maxWidth: 860, margin: '0 auto' }}>
      <header className="page-header">
        <div>
          <h1 className="page-title">A2P 10DLC Registration</h1>
          <p className="page-subtitle">Register your organization's SMS number for US carrier compliance.</p>
        </div>
        <button className="btn btn--secondary" onClick={refreshStatus} disabled={working || loading} style={{ fontSize: 13 }}>
          ↻ Refresh Status
        </button>
      </header>

      {/* Why this matters */}
      <section className="panel" style={{ marginBottom: 16, borderLeft: '3px solid #b8892a' }}>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          <strong style={{ color: 'var(--text-primary)' }}>Why A2P 10DLC?</strong> US carriers (AT&T, T-Mobile, Verizon) require
          all business SMS traffic on 10-digit numbers to be registered. Unregistered numbers get filtered or blocked entirely.
          Registration links your company's EIN and message use-case to your Twilio phone number so messages reach recipients reliably.
          Complete Steps 1–4 below. Brand and campaign approval typically takes 1–5 business days after submission.
        </p>
      </section>

      {/* Compliance resources */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header" style={{ marginBottom: 8 }}>
          <h2 className="panel-title" style={{ fontSize: 14 }}>📋 Required Public Pages</h2>
          <span style={{ fontSize: 12, color: 'var(--signal-green)' }}>✓ All live</span>
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {[
            { label: 'Privacy Policy', url: '/privacy-policy' },
            { label: 'Terms & Conditions', url: '/terms' },
            { label: 'SMS Consent Evidence', url: '/sms-consent-evidence' },
          ].map(({ label, url }) => (
            <a
              key={url}
              href={`https://advisorflow-backend.onrender.com${url}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '6px 14px', borderRadius: 8,
                background: 'rgba(34,197,94,0.08)',
                border: '1px solid rgba(34,197,94,0.3)',
                color: 'var(--signal-green)',
                textDecoration: 'none', fontSize: 13, fontWeight: 600,
              }}
            >
              ✓ {label} ↗
            </a>
          ))}
        </div>
      </section>

      {/* Status overview */}
      {!loading && status && (
        <section className="panel" style={{ marginBottom: 16 }}>
          <h2 className="panel-title" style={{ marginBottom: 14 }}>Registration Status</h2>
          <table className="data-table">
            <tbody>
              <tr>
                <td style={{ width: 200, color: 'var(--text-secondary)', fontSize: 13 }}>Messaging Service SID</td>
                <td><span className="mono" style={{ fontSize: 12 }}>{status.messaging_service_sid || '—'}</span></td>
                <td><StatusBadge status={status.messaging_service_sid ? 'ACTIVE' : null} /></td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-secondary)', fontSize: 13 }}>Brand SID</td>
                <td><span className="mono" style={{ fontSize: 12 }}>{status.brand_sid || '—'}</span></td>
                <td><StatusBadge status={status.brand_status} /></td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-secondary)', fontSize: 13 }}>Campaign SID</td>
                <td><span className="mono" style={{ fontSize: 12 }}>{status.campaign_sid || '—'}</span></td>
                <td><StatusBadge status={status.campaign_status} /></td>
              </tr>
            </tbody>
          </table>
        </section>
      )}

      {result && (
        <div style={{
          marginBottom: 16, padding: '12px 16px', borderRadius: 8, fontSize: 14,
          background: result.type === 'success' ? 'rgba(34,197,94,0.1)' : result.type === 'warn' ? 'rgba(184,137,42,0.1)' : 'rgba(239,68,68,0.1)',
          border: `1px solid ${result.type === 'success' ? 'rgba(34,197,94,0.3)' : result.type === 'warn' ? 'rgba(184,137,42,0.3)' : 'rgba(239,68,68,0.3)'}`,
          color: result.type === 'success' ? 'var(--signal-green)' : result.type === 'warn' ? '#b8892a' : 'var(--signal-red)',
        }}>
          {result.text}
        </div>
      )}

      {/* Step 1 — Messaging Service */}
      <section className="panel" style={{ marginBottom: 12 }}>
        <div
          className="panel-header"
          style={{ cursor: 'pointer', userSelect: 'none' }}
          onClick={() => setActiveStep(activeStep === 'service' ? null : 'service')}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: hasService ? 'rgba(34,197,94,0.15)' : 'rgba(184,137,42,0.15)',
              border: `2px solid ${hasService ? 'rgba(34,197,94,0.4)' : 'rgba(184,137,42,0.4)'}`,
              color: hasService ? 'var(--signal-green)' : '#b8892a',
              fontWeight: 800, fontSize: 13, flexShrink: 0,
            }}>
              {hasService ? '✓' : '1'}
            </div>
            <h2 className="panel-title" style={{ margin: 0 }}>Create Messaging Service</h2>
          </div>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{activeStep === 'service' ? '▲' : '▼'}</span>
        </div>
        {activeStep === 'service' && (
          <div style={{ paddingTop: 12 }}>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
              A Messaging Service is a Twilio resource that groups your phone numbers and links them to a registered A2P campaign.
              {hasService && <span style={{ color: 'var(--signal-green)' }}> ✓ Already created.</span>}
            </p>
            <button className="btn btn--primary" onClick={runStep1} disabled={working || hasService}>
              {working ? 'Working…' : hasService ? '✓ Done' : 'Create Messaging Service'}
            </button>
          </div>
        )}
      </section>

      {/* Step 2 — Brand */}
      <section className="panel" style={{ marginBottom: 12 }}>
        <div
          className="panel-header"
          style={{ cursor: 'pointer', userSelect: 'none' }}
          onClick={() => setActiveStep(activeStep === 'brand' ? null : 'brand')}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: hasBrand ? 'rgba(34,197,94,0.15)' : 'rgba(184,137,42,0.15)',
              border: `2px solid ${hasBrand ? 'rgba(34,197,94,0.4)' : 'rgba(184,137,42,0.4)'}`,
              color: hasBrand ? 'var(--signal-green)' : '#b8892a',
              fontWeight: 800, fontSize: 13, flexShrink: 0,
            }}>
              {hasBrand ? '✓' : '2'}
            </div>
            <h2 className="panel-title" style={{ margin: 0 }}>Register Brand (EIN)</h2>
          </div>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{activeStep === 'brand' ? '▲' : '▼'}</span>
        </div>
        {activeStep === 'brand' && (
          <div style={{ paddingTop: 12 }}>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
              Brand registration submits your company's legal identity to The Campaign Registry (TCR) via Twilio.
              Your EIN is required — it's used to verify your business is legitimate.
              {hasBrand && <span style={{ color: 'var(--signal-green)' }}> ✓ Brand already submitted (Status: {status?.brand_status}).</span>}
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
              {[
                { field: 'company_name', label: 'Legal Company Name', placeholder: 'North Star Memorial Group, Inc.' },
                { field: 'ein', label: 'EIN (Tax ID)', placeholder: '12-3456789' },
                { field: 'website', label: 'Website', placeholder: 'https://bookaboost.com' },
                { field: 'address_street', label: 'Street Address', placeholder: '13005 Greenville Ave' },
                { field: 'address_city', label: 'City', placeholder: 'Dallas' },
                { field: 'address_state', label: 'State (2-letter)', placeholder: 'TX' },
                { field: 'address_zip', label: 'ZIP', placeholder: '75243' },
                { field: 'contact_first_name', label: 'Contact First Name', placeholder: 'Mike' },
                { field: 'contact_last_name', label: 'Contact Last Name', placeholder: 'Simmons' },
                { field: 'contact_email', label: 'Contact Email', placeholder: 'mike@bookaboost.com' },
                { field: 'contact_phone', label: 'Contact Phone (E.164)', placeholder: '+14695537417' },
              ].map(({ field, label, placeholder }) => (
                <label key={field} className="settings-label" style={{ margin: 0 }}>
                  {label}
                  <input
                    className="settings-input"
                    value={brand[field]}
                    onChange={e => setBrand(b => ({ ...b, [field]: e.target.value }))}
                    placeholder={placeholder}
                  />
                </label>
              ))}
              <label className="settings-label" style={{ margin: 0 }}>
                Business Vertical
                <select className="settings-input" value={brand.vertical} onChange={e => setBrand(b => ({ ...b, vertical: e.target.value }))}>
                  {VERTICALS.map(v => <option key={v.value} value={v.value}>{v.label}</option>)}
                </select>
              </label>
            </div>

            <div style={{
              background: 'rgba(184,137,42,0.08)', border: '1px solid rgba(184,137,42,0.3)',
              borderRadius: 8, padding: '12px 16px', marginBottom: 16, fontSize: 13, color: '#b8892a',
            }}>
              <strong>Note:</strong> Twilio's A2P brand registration requires a completed CustomerProfile bundle in the Twilio Console
              (Console → Messaging → Regulatory Compliance → Customer Profiles). If registration fails here,
              complete the CustomerProfile first in the Console, then return to register your campaign.
            </div>

            <button className="btn btn--primary" onClick={runStep2} disabled={working || hasBrand}>
              {working ? 'Submitting…' : hasBrand ? '✓ Submitted' : 'Submit Brand Registration'}
            </button>
          </div>
        )}
      </section>

      {/* Step 3 — Campaign */}
      <section className="panel" style={{ marginBottom: 12 }}>
        <div
          className="panel-header"
          style={{ cursor: 'pointer', userSelect: 'none' }}
          onClick={() => setActiveStep(activeStep === 'campaign' ? null : 'campaign')}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: hasCampaign ? 'rgba(34,197,94,0.15)' : 'rgba(184,137,42,0.15)',
              border: `2px solid ${hasCampaign ? 'rgba(34,197,94,0.4)' : 'rgba(184,137,42,0.4)'}`,
              color: hasCampaign ? 'var(--signal-green)' : '#b8892a',
              fontWeight: 800, fontSize: 13, flexShrink: 0,
            }}>
              {hasCampaign ? '✓' : '3'}
            </div>
            <h2 className="panel-title" style={{ margin: 0 }}>Register Campaign</h2>
          </div>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{activeStep === 'campaign' ? '▲' : '▼'}</span>
        </div>
        {activeStep === 'campaign' && (
          <div style={{ paddingTop: 12 }}>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
              The campaign registration tells carriers what types of messages you send and how recipients opted in.
              Use specific, accurate descriptions — vague descriptions are the #1 reason campaigns get rejected.
              {hasCampaign && <span style={{ color: 'var(--signal-green)' }}> ✓ Campaign submitted (Status: {status?.campaign_status}).</span>}
            </p>

            <label className="settings-label">
              Use Case
              <select className="settings-input" value={campaign.use_case} onChange={e => setCampaign(c => ({ ...c, use_case: e.target.value }))}>
                {USE_CASES.map(u => <option key={u.value} value={u.value}>{u.label}</option>)}
              </select>
            </label>

            <label className="settings-label">
              Campaign Description <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>Be specific — what messages do you send?</span>
              <textarea
                className="settings-input"
                rows={3}
                value={campaign.description}
                onChange={e => setCampaign(c => ({ ...c, description: e.target.value }))}
                style={{ resize: 'vertical' }}
              />
            </label>

            <label className="settings-label">
              How Customers Opt In <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>Describe the consent flow exactly</span>
              <textarea
                className="settings-input"
                rows={3}
                value={campaign.message_flow}
                onChange={e => setCampaign(c => ({ ...c, message_flow: e.target.value }))}
                style={{ resize: 'vertical' }}
              />
            </label>

            <label className="settings-label">
              Sample Message 1 <span style={{ color: 'var(--signal-red)', fontSize: 11 }}>required — must include STOP opt-out</span>
              <textarea
                className="settings-input"
                rows={2}
                value={campaign.sample_message_1}
                onChange={e => setCampaign(c => ({ ...c, sample_message_1: e.target.value }))}
              />
            </label>

            <label className="settings-label">
              Sample Message 2 <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>optional but recommended</span>
              <textarea
                className="settings-input"
                rows={2}
                value={campaign.sample_message_2}
                onChange={e => setCampaign(c => ({ ...c, sample_message_2: e.target.value }))}
              />
            </label>

            <div style={{ display: 'flex', gap: 20, marginBottom: 16 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={campaign.has_embedded_links}
                  onChange={e => setCampaign(c => ({ ...c, has_embedded_links: e.target.checked }))} />
                Messages contain links (URLs)
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={campaign.has_embedded_phone}
                  onChange={e => setCampaign(c => ({ ...c, has_embedded_phone: e.target.checked }))} />
                Messages contain phone numbers
              </label>
            </div>

            <button className="btn btn--primary" onClick={runStep3} disabled={working || hasCampaign}>
              {working ? 'Submitting…' : hasCampaign ? '✓ Submitted' : 'Submit Campaign Registration'}
            </button>
          </div>
        )}
      </section>

      {/* Step 4 — Add Phone Numbers */}
      <section className="panel" style={{ marginBottom: 12 }}>
        <div
          className="panel-header"
          style={{ cursor: 'pointer', userSelect: 'none' }}
          onClick={() => setActiveStep(activeStep === 'numbers' ? null : 'numbers')}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'rgba(184,137,42,0.15)',
              border: '2px solid rgba(184,137,42,0.4)',
              color: '#b8892a',
              fontWeight: 800, fontSize: 13, flexShrink: 0,
            }}>
              4
            </div>
            <h2 className="panel-title" style={{ margin: 0 }}>Add Phone Numbers to Messaging Service</h2>
          </div>
          <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{activeStep === 'numbers' ? '▲' : '▼'}</span>
        </div>
        {activeStep === 'numbers' && (
          <div style={{ paddingTop: 12 }}>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
              This links all advisor phone numbers configured in BookaBoost to the Messaging Service,
              so messages sent through those numbers are covered by your A2P registration.
              Run this after your Messaging Service is created (Step 1).
            </p>
            <button className="btn btn--primary" onClick={runStep4} disabled={working || !hasService}>
              {working ? 'Adding numbers…' : 'Add All Advisor Phone Numbers'}
            </button>
          </div>
        )}
      </section>

      {/* Help note */}
      <section className="panel" style={{ marginBottom: 24 }}>
        <h2 className="panel-title" style={{ fontSize: 14, marginBottom: 8 }}>Need Help?</h2>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.7 }}>
          A2P 10DLC registration is managed by The Campaign Registry (TCR) through Twilio.
          Brand approval typically takes 1–5 business days. Campaign approval typically takes 3–7 business days after brand approval.
          Check the <strong>Refresh Status</strong> button above after submitting to see the latest state.
          For questions, see{' '}
          <a href="https://help.twilio.com/articles/1260800720410-What-is-A2P-10DLC-" target="_blank" rel="noopener noreferrer"
            style={{ color: 'var(--accent-blue)' }}>
            Twilio's A2P 10DLC guide ↗
          </a>
        </p>
      </section>
    </div>
  )
}
