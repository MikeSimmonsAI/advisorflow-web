import { useEffect, useState } from 'react'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './Settings.css'

export default function Settings() {
  const currentUser = getCurrentUser()
  const isAdmin = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'

  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(true)

  // Own Twilio settings
  const [sid, setSid] = useState('')
  const [authToken, setAuthToken] = useState('')
  const [phoneNumber, setPhoneNumber] = useState('')
  const [callerIdName, setCallerIdName] = useState('')
  const [savingTwilio, setSavingTwilio] = useState(false)
  const [twilioSaved, setTwilioSaved] = useState(false)

  // Notifications
  const [notifyEmail, setNotifyEmail] = useState('')
  const [notifyOnHot, setNotifyOnHot] = useState(true)
  const [savingNotif, setSavingNotif] = useState(false)
  const [notifSaved, setNotifSaved] = useState(false)

  // Calendar / Microsoft
  const [connectingCalendar, setConnectingCalendar] = useState(false)
  const [calendarMessage, setCalendarMessage] = useState(null)
  const [connectingMicrosoft, setConnectingMicrosoft] = useState(false)
  const [microsoftMessage, setMicrosoftMessage] = useState(null)

  // Admin — advisor Twilio assignment
  const [advisors, setAdvisors] = useState([])
  const [advisorsLoading, setAdvisorsLoading] = useState(false)
  const [assigningFor, setAssigningFor] = useState(null) // advisor id being edited
  const [assignForm, setAssignForm] = useState({ sid: '', authToken: '', phone: '', callerIdName: '' })
  const [assignSaving, setAssignSaving] = useState(false)
  const [assignResult, setAssignResult] = useState(null) // { advisorId, success, message }

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

    const params = new URLSearchParams(window.location.search)
    if (params.get('calendar_connected') === 'true') {
      setCalendarMessage({ type: 'success', text: 'Google Calendar connected successfully.' })
    } else if (params.get('calendar_error')) {
      setCalendarMessage({ type: 'error', text: `Calendar connection failed: ${params.get('calendar_error')}` })
    }
    if (params.get('microsoft_connected') === 'true') {
      setMicrosoftMessage({ type: 'success', text: 'Microsoft 365 connected successfully.' })
    } else if (params.get('microsoft_error')) {
      setMicrosoftMessage({ type: 'error', text: `Microsoft 365 connection failed: ${params.get('microsoft_error')}` })
    }
    if (params.has('calendar_connected') || params.has('calendar_error') || params.has('microsoft_connected') || params.has('microsoft_error')) {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [])

  useEffect(() => {
    if (!isAdmin) return
    setAdvisorsLoading(true)
    api.get('/admin/users')
      .then(users => setAdvisors(users.filter(u => u.is_active)))
      .catch(() => {})
      .finally(() => setAdvisorsLoading(false))
  }, [isAdmin])

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

  async function handleConnectMicrosoft() {
    setConnectingMicrosoft(true)
    try {
      const result = await api.get('/microsoft/connect')
      window.location.href = result.authorization_url
    } catch (err) {
      setMicrosoftMessage({ type: 'error', text: err.message })
      setConnectingMicrosoft(false)
    }
  }

  function startAssign(advisor) {
    setAssigningFor(advisor.id)
    setAssignForm({
      sid: advisor.twilio_account_sid || '',
      authToken: '',
      phone: advisor.twilio_phone_number || '',
      callerIdName: advisor.twilio_caller_id_name || '',
    })
    setAssignResult(null)
  }

  async function handleAssignSave(e) {
    e.preventDefault()
    if (!assignForm.phone.trim()) {
      alert('Phone number is required.')
      return
    }
    setAssignSaving(true)
    setAssignResult(null)
    try {
      await api.put(`/settings/admin/twilio/${assigningFor}`, {
        twilio_account_sid: assignForm.sid.trim() || null,
        twilio_auth_token: assignForm.authToken.trim() || null,
        twilio_phone_number: assignForm.phone.trim(),
        twilio_caller_id_name: assignForm.callerIdName.trim() || null,
      })
      setAssignResult({ advisorId: assigningFor, success: true, message: 'Saved successfully.' })
      // Refresh advisors list
      const users = await api.get('/admin/users')
      setAdvisors(users.filter(u => u.is_active))
      setAssigningFor(null)
    } catch (err) {
      setAssignResult({ advisorId: assigningFor, success: false, message: err.message || 'Save failed.' })
    } finally {
      setAssignSaving(false)
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

      {/* ── Admin: Advisor Twilio Assignment ── */}
      {isAdmin && (
        <section className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <h2 className="panel-title">📱 Advisor Twilio Numbers</h2>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Assign a Twilio number to each advisor so cadence fires from their number
            </span>
          </div>
          <p className="settings-help" style={{ marginBottom: 16 }}>
            Cadence messages skip advisors without a Twilio number configured. Assign numbers here to unblock sending.
            Each advisor can also set their own in their personal settings below.
          </p>

          {advisorsLoading ? (
            <div className="empty-state">Loading advisors…</div>
          ) : (
            <table className="data-table" style={{ marginBottom: 16 }}>
              <thead>
                <tr>
                  <th>Advisor</th>
                  <th>Role</th>
                  <th>Phone number</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {advisors.map(advisor => {
                  const configured = !!advisor.twilio_phone_number
                  const isEditing = assigningFor === advisor.id
                  return (
                    <>
                      <tr key={advisor.id}>
                        <td>{advisor.full_name}</td>
                        <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                          {advisor.role?.replace('_', ' ')}
                        </td>
                        <td className="mono" style={{ fontSize: 13 }}>
                          {advisor.twilio_phone_number || '–'}
                        </td>
                        <td>
                          {configured
                            ? <span className="badge badge--green">Ready</span>
                            : <span className="badge badge--neutral-dim">No number</span>}
                        </td>
                        <td>
                          <button
                            className="btn btn--secondary"
                            style={{ fontSize: 12, padding: '4px 12px' }}
                            onClick={() => isEditing ? setAssigningFor(null) : startAssign(advisor)}
                          >
                            {isEditing ? 'Cancel' : configured ? 'Edit' : 'Assign'}
                          </button>
                        </td>
                      </tr>
                      {isEditing && (
                        <tr key={`${advisor.id}-form`}>
                          <td colSpan={5} style={{ padding: '16px 12px', background: 'rgba(255,255,255,0.02)' }}>
                            <form onSubmit={handleAssignSave} className="settings-form" style={{ maxWidth: 600 }}>
                              <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 14 }}>
                                Assigning Twilio number for <strong style={{ color: 'var(--text-primary)' }}>{advisor.full_name}</strong>.
                                Leave SID/token blank to use the org-level account.
                              </p>
                              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                <label className="settings-label">
                                  Account SID <span className="settings-optional">optional</span>
                                  <input
                                    className="settings-input"
                                    value={assignForm.sid}
                                    onChange={e => setAssignForm(f => ({ ...f, sid: e.target.value }))}
                                    placeholder="ACxxxxxxxx or leave blank"
                                  />
                                </label>
                                <label className="settings-label">
                                  Auth token <span className="settings-optional">optional</span>
                                  <input
                                    className="settings-input"
                                    type="password"
                                    value={assignForm.authToken}
                                    onChange={e => setAssignForm(f => ({ ...f, authToken: e.target.value }))}
                                    placeholder="Leave blank to keep existing"
                                  />
                                </label>
                                <label className="settings-label">
                                  Phone number <span style={{ color: 'var(--signal-red)', fontSize: 11 }}>required</span>
                                  <input
                                    className="settings-input"
                                    value={assignForm.phone}
                                    onChange={e => setAssignForm(f => ({ ...f, phone: e.target.value }))}
                                    placeholder="+12145551234"
                                    required
                                  />
                                </label>
                                <label className="settings-label">
                                  Caller ID name <span className="settings-optional">optional</span>
                                  <input
                                    className="settings-input"
                                    value={assignForm.callerIdName}
                                    onChange={e => setAssignForm(f => ({ ...f, callerIdName: e.target.value }))}
                                    placeholder="Restland Cemetery"
                                  />
                                </label>
                              </div>
                              {assignResult && assignResult.advisorId === advisor.id && (
                                <div style={{
                                  marginTop: 10,
                                  padding: '8px 12px',
                                  borderRadius: 8,
                                  background: assignResult.success ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                                  color: assignResult.success ? 'var(--signal-green)' : 'var(--signal-red)',
                                  fontSize: 13,
                                }}>
                                  {assignResult.message}
                                </div>
                              )}
                              <div className="settings-actions" style={{ marginTop: 14 }}>
                                <button type="button" className="btn btn--secondary" onClick={() => setAssigningFor(null)}>
                                  Cancel
                                </button>
                                <button type="submit" className="btn btn--primary" disabled={assignSaving}>
                                  {assignSaving ? 'Saving…' : `Save for ${advisor.full_name.split(' ')[0]}`}
                                </button>
                              </div>
                            </form>
                          </td>
                        </tr>
                      )}
                    </>
                  )
                })}
              </tbody>
            </table>
          )}

          <p className="settings-help">
            Once a number is assigned, cadence will automatically send from that advisor's number.
            No other changes needed.
          </p>
        </section>
      )}

      {/* ── Own Twilio ── */}
      <section id="twilio" className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">Twilio</h2>
          {profile.twilio_configured && <span className="badge badge--green">Connected</span>}
        </div>
        <p className="settings-help">
          Each advisor connects their own Twilio account so your texts bill to your own number.
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

      {/* ── Google Calendar ── */}
      <section id="google" className="panel" style={{ marginBottom: 16 }}>
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

      {microsoftMessage && (
        <div className={microsoftMessage.type === 'success' ? 'settings-banner settings-banner--success' : 'settings-banner settings-banner--error'}>
          {microsoftMessage.text}
        </div>
      )}

      {/* ── Microsoft 365 ── */}
      <section id="microsoft" className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">Microsoft 365 Email</h2>
          {profile.microsoft_365_connected && <span className="badge badge--green">Connected</span>}
        </div>
        <p className="settings-help">
          Connect your Microsoft 365 mailbox so outbound email sends from your real Outlook address.
          This is separate from Google Calendar — connect either, both, or neither independently.
        </p>
        {profile.microsoft_365_connected && profile.microsoft_email_address && (
          <p className="settings-help mono">Connected as {profile.microsoft_email_address}</p>
        )}
        <div className="settings-actions" style={{ justifyContent: 'flex-start' }}>
          <button className="btn btn--primary" onClick={handleConnectMicrosoft} disabled={connectingMicrosoft}>
            {connectingMicrosoft ? 'Redirecting…' : profile.microsoft_365_connected ? 'Reconnect Microsoft 365' : 'Connect Microsoft 365'}
          </button>
        </div>
      </section>

      {/* ── Notifications ── */}
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
