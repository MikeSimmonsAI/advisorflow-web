import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Settings.css'

export default function Settings() {
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(true)

  const [sid, setSid] = useState('')
  const [authToken, setAuthToken] = useState('')
  const [phoneNumber, setPhoneNumber] = useState('')
  const [callerIdName, setCallerIdName] = useState('')
  const [savingTwilio, setSavingTwilio] = useState(false)
  const [twilioSaved, setTwilioSaved] = useState(false)

  const [notifyEmail, setNotifyEmail] = useState('')
  const [notifyOnHot, setNotifyOnHot] = useState(true)
  const [savingNotif, setSavingNotif] = useState(false)
  const [notifSaved, setNotifSaved] = useState(false)

  const [connectingCalendar, setConnectingCalendar] = useState(false)
  const [calendarMessage, setCalendarMessage] = useState(null) // { type: 'success'|'error', text }

  useEffect(() => {
    api.get('/settings/profile').then((p) => {
      setProfile(p)
      setSid(p.twilio_account_sid || '')
      setPhoneNumber(p.twilio_phone_number || '')
      setCallerIdName(p.twilio_caller_id_name || '')
      setNotifyEmail(p.notification_email || '')
      setNotifyOnHot(p.notify_on_hot_reply)
      setLoading(false)
    })

    // After the Google OAuth redirect, the backend sends the advisor back
    // here with ?calendar_connected=true or ?calendar_error=... in the URL -
    // read those once on load so the result of the OAuth flow is visible.
    const params = new URLSearchParams(window.location.search)
    if (params.get('calendar_connected') === 'true') {
      setCalendarMessage({ type: 'success', text: 'Google Calendar connected successfully.' })
    } else if (params.get('calendar_error')) {
      setCalendarMessage({ type: 'error', text: `Calendar connection failed: ${params.get('calendar_error')}` })
    }
    // Same pattern for the separate Microsoft 365 email connection -
    // independent of the Google Calendar flow above, per Mike's
    // explicit instruction that these are two separate integrations.
    if (params.get('microsoft_connected') === 'true') {
      setMicrosoftMessage({ type: 'success', text: 'Microsoft 365 connected successfully.' })
    } else if (params.get('microsoft_error')) {
      setMicrosoftMessage({ type: 'error', text: `Microsoft 365 connection failed: ${params.get('microsoft_error')}` })
    }
    if (params.has('calendar_connected') || params.has('calendar_error') || params.has('microsoft_connected') || params.has('microsoft_error')) {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [])

  async function saveTwilio(e) {
    e.preventDefault()
    setSavingTwilio(true)
    setTwilioSaved(false)
    try {
      await api.put('/settings/twilio', {
        twilio_account_sid: sid,
        twilio_auth_token: authToken,
        twilio_phone_number: phoneNumber,
        twilio_caller_id_name: callerIdName || null,
      })
      setAuthToken('')
      setTwilioSaved(true)
    } catch (err) {
      alert(`Failed to save: ${err.message}`)
    } finally {
      setSavingTwilio(false)
    }
  }

  async function saveNotifications(e) {
    e.preventDefault()
    setSavingNotif(true)
    setNotifSaved(false)
    try {
      await api.put('/settings/notifications', {
        notification_email: notifyEmail || null,
        notify_on_hot_reply: notifyOnHot,
      })
      setNotifSaved(true)
    } catch (err) {
      alert(`Failed to save: ${err.message}`)
    } finally {
      setSavingNotif(false)
    }
  }

  async function handleConnectCalendar() {
    setConnectingCalendar(true)
    try {
      const result = await api.get('/calendar/connect')
      window.location.href = result.authorization_url
    } catch (err) {
      setCalendarMessage({ type: 'error', text: err.message })
      setConnectingCalendar(false)
    }
  }

  if (loading) return <div className="empty-state">Loading settings…</div>

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">Your Twilio connection, calendar, and notification preferences.</p>
        </div>
      </header>

      {calendarMessage && (
        <div className={calendarMessage.type === 'success' ? 'settings-banner settings-banner--success' : 'settings-banner settings-banner--error'}>
          {calendarMessage.text}
        </div>
      )}

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">Twilio</h2>
          {profile.twilio_configured && <span className="badge badge--green">Connected</span>}
        </div>
        <p className="settings-help">
          Each advisor connects their own Twilio account, so your texts bill to your own number.
          Find these values in your Twilio console.
        </p>
        <form onSubmit={saveTwilio} className="settings-form">
          <label className="settings-label">
            Account SID
            <input className="settings-input" value={sid} onChange={(e) => setSid(e.target.value)} placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" required />
          </label>
          <label className="settings-label">
            Auth token
            <input
              className="settings-input"
              type="password"
              value={authToken}
              onChange={(e) => setAuthToken(e.target.value)}
              placeholder={profile.twilio_configured ? 'Leave blank to keep current token' : 'Your Twilio auth token'}
              required={!profile.twilio_configured}
            />
          </label>
          <label className="settings-label">
            Phone number
            <input className="settings-input" value={phoneNumber} onChange={(e) => setPhoneNumber(e.target.value)} placeholder="+12145551234" required />
          </label>
          <label className="settings-label">
            Caller ID name <span className="settings-optional">optional</span>
            <input className="settings-input" value={callerIdName} onChange={(e) => setCallerIdName(e.target.value)} placeholder="Restland Cemetery" />
          </label>
          <div className="settings-actions">
            {twilioSaved && <span className="settings-saved">Saved</span>}
            <button className="btn btn--primary" type="submit" disabled={savingTwilio}>
              {savingTwilio ? 'Saving…' : 'Save Twilio settings'}
            </button>
          </div>
        </form>
      </section>

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">Google Calendar</h2>
          {profile.google_calendar_connected && <span className="badge badge--green">Connected</span>}
        </div>
        <p className="settings-help">
          Connect your Google Calendar so appointments booked through your booking link land
          directly on your real calendar.
        </p>
        <div className="settings-actions" style={{ justifyContent: 'flex-start' }}>
          <button className="btn btn--primary" onClick={handleConnectCalendar} disabled={connectingCalendar}>
            {connectingCalendar ? 'Redirecting…' : profile.google_calendar_connected ? 'Reconnect Google Calendar' : 'Connect Google Calendar'}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Notifications</h2>
        </div>
        <form onSubmit={saveNotifications} className="settings-form">
          <label className="settings-label">
            Notification email <span className="settings-optional">where hot reply alerts go</span>
            <input className="settings-input" type="email" value={notifyEmail} onChange={(e) => setNotifyEmail(e.target.value)} placeholder={profile.email} />
          </label>
          <label className="settings-checkbox-row">
            <input type="checkbox" checked={notifyOnHot} onChange={(e) => setNotifyOnHot(e.target.checked)} />
            Email me immediately when a lead replies hot
          </label>
          <div className="settings-actions">
            {notifSaved && <span className="settings-saved">Saved</span>}
            <button className="btn btn--primary" type="submit" disabled={savingNotif}>
              {savingNotif ? 'Saving…' : 'Save notification settings'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
