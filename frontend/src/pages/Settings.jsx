import { useEffect, useState } from 'react'
import { api, getCurrentUser, getBranding } from '../api/client'
import { getMemberLabel } from '../utils/labels'
import '../styles/shared.css'
import './Settings.css'

// Styles for the scheduling-calendar panel. Inline because Settings.css has no
// row/pill vocabulary and one more global class name is worse than a local
// object that cannot collide with anything.
const CAL = {
  row: {
    display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap',
    border: '1px solid var(--border-subtle)', borderRadius: 10,
    padding: '12px 14px', background: 'var(--surface-card, rgba(255,255,255,0.03))',
  },
  title: {
    fontSize: 14, fontWeight: 650, color: 'var(--text-primary)',
    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
  },
  activePill: {
    fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em',
    textTransform: 'uppercase', borderRadius: 20, padding: '2px 8px',
    background: 'rgba(30,240,168,0.14)', color: 'var(--signal-green, #1ef0a8)',
    border: '1px solid rgba(30,240,168,0.32)',
  },
  warnPill: {
    fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em',
    textTransform: 'uppercase', borderRadius: 20, padding: '2px 8px',
    background: 'rgba(255,180,30,0.14)', color: 'var(--signal-amber, #ffb41e)',
    border: '1px solid rgba(255,180,30,0.32)',
  },
  meta: { fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4, lineHeight: 1.5 },
  detail: { fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.5 },
  ok: { color: 'var(--signal-green, #1ef0a8)', fontWeight: 600 },
  warn: { color: 'var(--signal-amber, #ffb41e)', fontWeight: 600 },
  off: { color: 'var(--text-tertiary)', fontWeight: 600 },
  actions: { display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0, flexWrap: 'wrap' },
  btn: { fontSize: 12, padding: '7px 12px' },
}

export default function Settings() {
  const currentUser = getCurrentUser()
  const isAdmin = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin' || currentUser?.role === 'god_admin'

  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(true)

  // Own Twilio settings
  const [sid, setSid] = useState('')
  const [authToken, setAuthToken] = useState('')
  const [phoneNumber, setPhoneNumber] = useState('')
  const [callerIdName, setCallerIdName] = useState('')
  const [savingTwilio, setSavingTwilio] = useState(false)
  const [twilioSaved, setTwilioSaved] = useState(false)

  // Booking page URL
  const [bookingUrl, setBookingUrl] = useState('')
  const [savingBooking, setSavingBooking] = useState(false)
  const [bookingSaved, setBookingSaved] = useState(false)
  const [bookingCopied, setBookingCopied] = useState(false)

  // Booking / availability settings
  const [apptDuration, setApptDuration] = useState(30)
  const [bufferMin, setBufferMin] = useState(0)
  const [maxBookings, setMaxBookings] = useState(8)
  const [startTime, setStartTime] = useState('09:00')
  const [endTime, setEndTime] = useState('17:00')
  const [availDays, setAvailDays] = useState([0, 1, 2, 3, 4])
  const [bookingTz, setBookingTz] = useState('America/Chicago')
  const [bookingConfirmMsg, setBookingConfirmMsg] = useState('')
  const [savingBookingSettings, setSavingBookingSettings] = useState(false)
  const [bookingSettingsSaved, setBookingSettingsSaved] = useState(false)

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
  // Scheduling calendars: the backend's own view of both providers.
  const [calendarState, setCalendarState] = useState(null)
  const [calendarLoading, setCalendarLoading] = useState(false)
  const [calendarBusy, setCalendarBusy] = useState('')   // provider key in flight
  const [calendarError, setCalendarError] = useState('')
  const [calendarNotice, setCalendarNotice] = useState('')
  const [calendarWarning, setCalendarWarning] = useState('')

  // Profile photo
  const [photoPreview, setPhotoPreview] = useState(null)
  const [photoSaving, setPhotoSaving] = useState(false)
  const [photoSaved, setPhotoSaved] = useState(false)

  // Name change
  const [displayName, setDisplayName]   = useState('')
  const [nameSaving, setNameSaving]     = useState(false)
  const [nameSaved, setNameSaved]       = useState(false)
  const [nameError, setNameError]       = useState('')

  // Password change
  const [currentPw, setCurrentPw]       = useState('')
  const [newPw, setNewPw]               = useState('')
  const [confirmPw, setConfirmPw]       = useState('')
  const [pwSaving, setPwSaving]         = useState(false)
  const [pwSaved, setPwSaved]           = useState(false)
  const [pwError, setPwError]           = useState('')

  // Admin — advisor Twilio assignment
  const [advisors, setAdvisors] = useState([])
  const [advisorsLoading, setAdvisorsLoading] = useState(false)
  const [assigningFor, setAssigningFor] = useState(null) // advisor id being edited
  const [assignForm, setAssignForm] = useState({ sid: '', authToken: '', phone: '', callerIdName: '' })
  const [assignSaving, setAssignSaving] = useState(false)
  const [assignResult, setAssignResult] = useState(null) // { advisorId, success, message }
  const [advisorSearch, setAdvisorSearch] = useState('')

  useEffect(() => {
    api.get('/settings/profile').then((p) => {
      setProfile(p)
      setSid(p.twilio_account_sid || '')
      setPhoneNumber(p.twilio_phone_number || '')
      setCallerIdName(p.twilio_caller_id_name || '')
      setNotifyEmail(p.notification_email || '')
      setNotifyOnHot(p.notify_on_hot_reply)
      setBookingUrl(p.booking_page_url || '')
      setApptDuration(p.appt_duration_minutes || 30)
      setBufferMin(p.buffer_minutes || 0)
      setMaxBookings(p.max_bookings_per_day || 8)
      setStartTime(p.available_start_time || '09:00')
      setEndTime(p.available_end_time || '17:00')
      setAvailDays((p.available_days || '0,1,2,3,4').split(',').map(Number))
      setBookingTz(p.booking_timezone || 'America/Chicago')
      setBookingConfirmMsg(p.booking_confirmation_message || '')
      setPhotoPreview(p.profile_photo_url || null)
      setDisplayName(p.full_name || '')
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

  // Calendar state is read once on mount, and again after any change, so the
  // panel never shows a state the backend has since moved on from.
  useEffect(() => { loadCalendars() }, [])

  useEffect(() => {
    if (!isAdmin) return
    setAdvisorsLoading(true)
    api.get('/admin/users')
      .then(users => setAdvisors(users.filter(u => u.is_active)))
      .catch(() => {})
      .finally(() => setAdvisorsLoading(false))
  }, [isAdmin])

  async function saveBookingPage(e) {
    e.preventDefault()
    setSavingBooking(true)
    setBookingSaved(false)
    try {
      await api.put('/settings/booking-page', { booking_page_url: bookingUrl || null })
      setBookingSaved(true)
    } catch (err) {
      alert(`Failed to save: ${err.message}`)
    } finally {
      setSavingBooking(false)
    }
  }

  function copyBookingUrl() {
    if (!bookingUrl) return
    navigator.clipboard.writeText(bookingUrl).then(() => {
      setBookingCopied(true)
      setTimeout(() => setBookingCopied(false), 2000)
    })
  }

  async function saveBookingSettings(e) {
    e.preventDefault()
    setSavingBookingSettings(true)
    setBookingSettingsSaved(false)
    try {
      await api.patch('/settings/booking', {
        appt_duration_minutes: apptDuration,
        buffer_minutes: bufferMin,
        max_bookings_per_day: maxBookings,
        available_start_time: startTime,
        available_end_time: endTime,
        available_days: availDays.join(','),
        booking_timezone: bookingTz,
        booking_confirmation_message: bookingConfirmMsg || null,
      })
      setBookingSettingsSaved(true)
    } catch (err) {
      alert(`Failed to save: ${err.message}`)
    } finally {
      setSavingBookingSettings(false)
    }
  }

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

  // ── scheduling calendars ────────────────────────────────────────────────
  //
  // One source of truth for both providers: /me/calendar/connections, which is
  // the same router the Sales Workspace uses. Nothing here infers a state from
  // a token being present - the backend distinguishes "has a token" from "can
  // read the calendar", which is the distinction that let an advisor show
  // CONNECTED while every read failed.

  async function loadCalendars() {
    setCalendarLoading(true)
    setCalendarError('')
    try {
      setCalendarState(await api.get('/me/calendar/connections'))
    } catch (err) {
      setCalendarError(err.message || 'We could not read your calendar connections.')
    } finally {
      setCalendarLoading(false)
    }
  }

  async function connectCalendarProvider(c) {
    if (calendarBusy) return
    setCalendarBusy(c.provider)
    setCalendarError('')
    try {
      // The EXISTING consent flows - this panel reports state and sends people
      // to them; it does not start a second OAuth flow of its own.
      const path = c.provider === 'google' ? '/calendar/connect' : '/microsoft/connect'
      const result = await api.get(path)
      window.location.href = result.authorization_url
    } catch (err) {
      setCalendarError(err.message || 'We could not start that connection.')
      setCalendarBusy('')
    }
  }

  async function testCalendar(provider) {
    if (calendarBusy) return
    setCalendarBusy(provider)
    setCalendarError('')
    setCalendarNotice('')
    try {
      const r = await api.post(`/me/calendar/connections/${provider}/test`, {})
      if (r.ok) setCalendarNotice(r.message)
      else setCalendarError(r.message)
      await loadCalendars()
    } catch (err) {
      setCalendarError(err.message || 'The test could not be run.')
    } finally {
      setCalendarBusy('')
    }
  }

  async function disconnectCalendar(c) {
    if (calendarBusy) return
    // Confirmation, because this is not undoable without going back through
    // the provider's consent screen.
    const isActive = calendarState?.active_provider === c.provider
    const extra = isActive
      ? '\n\nThis is the calendar scheduling currently uses. Until you reconnect it or ' +
        'choose another, availability will be reported as unavailable rather than ' +
        'falling back to a different calendar.'
      : ''
    if (!window.confirm(
      `Disconnect ${c.label}?\n\nAppointments already on that calendar are left in place.` + extra
    )) return

    setCalendarBusy(c.provider)
    setCalendarError('')
    setCalendarNotice('')
    setCalendarWarning('')
    try {
      const r = await api.post(`/me/calendar/connections/${c.provider}/disconnect`, {})
      setCalendarNotice(r.note || 'Disconnected.')
      if (r.warning) setCalendarWarning(r.warning)
      await loadCalendars()
      // The profile badge elsewhere on this page reads from /settings/profile.
      api.get('/settings/profile').then(setProfile).catch(() => {})
    } catch (err) {
      setCalendarError(err.message || 'We could not disconnect that calendar.')
    } finally {
      setCalendarBusy('')
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

  function handlePhotoChange(e) {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.size > 10 * 1024 * 1024) {
      alert('Photo must be under 10MB.')
      e.target.value = ''
      return
    }
    // Auto-compress: resize to max 900×900 and re-encode at 85% quality so
    // even high-res headshots stay small enough to store as a data URL.
    const reader = new FileReader()
    reader.onload = (ev) => {
      const img = new Image()
      img.onload = () => {
        const MAX = 900
        let { width: w, height: h } = img
        if (w > MAX || h > MAX) {
          if (w > h) { h = Math.round(h * MAX / w); w = MAX }
          else { w = Math.round(w * MAX / h); h = MAX }
        }
        const canvas = document.createElement('canvas')
        canvas.width = w; canvas.height = h
        canvas.getContext('2d').drawImage(img, 0, 0, w, h)
        setPhotoPreview(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = ev.target.result
    }
    reader.readAsDataURL(file)
  }

  async function savePhoto() {
    if (!photoPreview) return
    setPhotoSaving(true)
    setPhotoSaved(false)
    try {
      await api.patch('/settings/profile-photo', { photo_data_url: photoPreview })
      setPhotoSaved(true)
      setTimeout(() => setPhotoSaved(false), 2500)
    } catch (err) {
      alert(`Failed to save photo: ${err.message}`)
    } finally {
      setPhotoSaving(false)
    }
  }

  async function removePhoto() {
    if (!window.confirm('Remove your profile photo?')) return
    try {
      await api.delete('/settings/profile-photo')
      setPhotoPreview(null)
    } catch (err) {
      alert(`Failed to remove photo: ${err.message}`)
    }
  }

  async function saveName(e) {
    e.preventDefault()
    const name = displayName.trim()
    if (!name) { setNameError('Name cannot be empty.'); return }
    setNameSaving(true); setNameSaved(false); setNameError('')
    try {
      await api.patch('/settings/profile', { full_name: name })
      setNameSaved(true)
      setTimeout(() => setNameSaved(false), 2500)
    } catch (err) {
      setNameError(err.message || 'Failed to save name.')
    } finally {
      setNameSaving(false)
    }
  }

  async function savePassword(e) {
    e.preventDefault()
    if (newPw !== confirmPw) { setPwError('Passwords do not match.'); return }
    if (newPw.length < 8)    { setPwError('Password must be at least 8 characters.'); return }
    setPwSaving(true); setPwSaved(false); setPwError('')
    try {
      await api.post('/auth/change-password', { current_password: currentPw, new_password: newPw })
      setCurrentPw(''); setNewPw(''); setConfirmPw('')
      setPwSaved(true)
      setTimeout(() => setPwSaved(false), 2500)
    } catch (err) {
      setPwError(err.message || 'Failed to change password.')
    } finally {
      setPwSaving(false)
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

      {/* ── Profile Photo ── */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">🖼️ Profile Photo</h2>
        </div>
        <p className="settings-help">
          Your headshot appears in your sidebar and on your booking page. Upload a square photo under 2MB.
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginBottom: 16 }}>
          {/* Avatar preview */}
          <div style={{
            width: 80, height: 80, borderRadius: '50%', overflow: 'hidden', flexShrink: 0,
            background: 'var(--bg-card)', border: '2px solid var(--border)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 28, color: 'var(--text-secondary)', fontWeight: 700,
          }}>
            {photoPreview
              ? <img src={photoPreview} alt="Profile" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              : (profile?.full_name || currentUser?.full_name || '?')[0].toUpperCase()
            }
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <label
              style={{
                display: 'inline-block', padding: '8px 16px', borderRadius: 8, cursor: 'pointer',
                background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600,
              }}
            >
              Choose photo
              <input
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={handlePhotoChange}
              />
            </label>
            {photoPreview && (
              <button
                className="btn btn--secondary"
                style={{ fontSize: 12, padding: '6px 14px' }}
                onClick={removePhoto}
              >
                Remove photo
              </button>
            )}
          </div>
        </div>
        <div className="settings-actions" style={{ justifyContent: 'flex-start' }}>
          {photoSaved && <span className="settings-saved">Photo saved ✓</span>}
          <button
            className="btn btn--primary"
            disabled={!photoPreview || photoSaving}
            onClick={savePhoto}
          >
            {photoSaving ? 'Saving…' : 'Save photo'}
          </button>
        </div>
      </section>

      {/* ── Display Name ── */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">✏️ Display Name</h2>
        </div>
        <form onSubmit={saveName} style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>Full name</label>
            <input
              value={displayName}
              onChange={e => { setDisplayName(e.target.value); setNameError('') }}
              placeholder="Your full name"
              style={{ width: '100%' }}
            />
          </div>
          <button type="submit" className="btn btn--primary" disabled={nameSaving} style={{ whiteSpace: 'nowrap' }}>
            {nameSaving ? 'Saving…' : nameSaved ? '✓ Saved' : 'Save name'}
          </button>
        </form>
        {nameError && <div className="settings-error" style={{ marginTop: 8 }}>{nameError}</div>}
      </section>

      {/* ── Change Password ── */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">🔐 Change Password</h2>
        </div>
        <form onSubmit={savePassword} style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 400 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>Current password</label>
            <input type="password" value={currentPw} onChange={e => { setCurrentPw(e.target.value); setPwError('') }} required placeholder="Your current password" />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>New password</label>
            <input type="password" value={newPw} onChange={e => { setNewPw(e.target.value); setPwError('') }} required placeholder="At least 8 characters" />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>Confirm new password</label>
            <input type="password" value={confirmPw} onChange={e => { setConfirmPw(e.target.value); setPwError('') }} required placeholder="Repeat new password" />
          </div>
          {pwError  && <div className="settings-error">{pwError}</div>}
          {pwSaved  && <div style={{ color: 'var(--success)', fontSize: 13 }}>✓ Password changed successfully.</div>}
          <button type="submit" className="btn btn--primary" disabled={pwSaving} style={{ alignSelf: 'flex-start' }}>
            {pwSaving ? 'Saving…' : 'Change password'}
          </button>
        </form>
      </section>

      {calendarMessage && (
        <div className={calendarMessage.type === 'success' ? 'settings-banner settings-banner--success' : 'settings-banner settings-banner--error'}>
          {calendarMessage.text}
        </div>
      )}

      {/* ── Admin: Advisor Twilio Assignment ── */}
      {isAdmin && (
        <section className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <h2 className="panel-title">📱 {getMemberLabel(getBranding(), true)} Twilio Numbers</h2>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Assign a Twilio number to each {getMemberLabel(getBranding(), false).toLowerCase()} so cadence fires from their number
            </span>
          </div>
          <p className="settings-help" style={{ marginBottom: 16 }}>
            Cadence messages skip {getMemberLabel(getBranding(), true).toLowerCase()} without a Twilio number configured. Assign numbers here to unblock sending.
            Each {getMemberLabel(getBranding(), false).toLowerCase()} can also set their own in their personal settings below.
          </p>

          {advisorsLoading ? (
            <div className="empty-state">Loading advisors…</div>
          ) : (
            <>
              {/* Search / filter */}
              <div style={{ marginBottom: 12 }}>
                <input
                  className="settings-input"
                  placeholder="Search by name or phone…"
                  value={advisorSearch}
                  onChange={e => setAdvisorSearch(e.target.value)}
                  style={{ maxWidth: 320 }}
                />
              </div>
            <table className="data-table" style={{ marginBottom: 16 }}>
              <thead>
                <tr>
                  <th>{getMemberLabel(getBranding(), false)}</th>
                  <th>Role</th>
                  <th>Phone number</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {advisors
                  .filter(a => {
                    if (!advisorSearch.trim()) return true
                    const q = advisorSearch.toLowerCase()
                    return (a.full_name || '').toLowerCase().includes(q) ||
                           (a.twilio_phone_number || '').includes(q) ||
                           (a.email || '').toLowerCase().includes(q)
                  })
                  .map(advisor => {
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
                                    placeholder="Your Organization Name"
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
            </>
          )}

          <p className="settings-help">
            Once a number is assigned, cadence will automatically send from that advisor's number.
            No other changes needed.
          </p>
        </section>
      )}

      {/* ── Own Twilio — org admin and above only ── */}
      {isAdmin && (
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
              <input className="settings-input" value={callerIdName} onChange={(e) => setCallerIdName(e.target.value)} placeholder="Your Organization Name" />
            </label>
            <div className="settings-actions">
              {twilioSaved && <span className="settings-saved">Saved</span>}
              <button className="btn btn--primary" type="submit" disabled={savingTwilio}>
                {savingTwilio ? 'Saving…' : 'Save Twilio settings'}
              </button>
            </div>
          </form>
        </section>
      )}

      {/* ── Scheduling calendars ──
          One panel for both providers, driven by the backend's own view of
          each connection. The two panels this replaced each showed a green
          "Connected" badge derived from a token being present, which is not
          the same question as "can we read this calendar" — that is how an
          advisor came to be connected to Outlook without meaning to be, with
          no account shown and no way to undo it. */}
      <section id="google" className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">📅 Scheduling calendars</h2>
          {calendarState?.active_label && (
            <span className="badge badge--green">Using {calendarState.active_label}</span>
          )}
        </div>
        <p className="settings-help">
          Appointments booked through your link are written to the calendar marked
          <strong> scheduling calendar</strong>, and that calendar is also read to work out
          which times to offer. Connecting a second provider does not change which one is used.
        </p>

        {calendarError && <div className="settings-banner settings-banner--error">{calendarError}</div>}
        {calendarNotice && <div className="settings-banner settings-banner--success">{calendarNotice}</div>}
        {calendarWarning && <div className="settings-banner settings-banner--error">{calendarWarning}</div>}

        {calendarLoading && !calendarState ? (
          <p className="settings-help">Checking your calendars…</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {(calendarState?.connections || []).map((c) => {
              const isActive = calendarState.active_provider === c.provider
              const isConfigured = calendarState.configured_provider === c.provider
              return (
                <div key={c.provider} style={CAL.row}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={CAL.title}>
                      {c.label}
                      {isActive && <span style={CAL.activePill}>scheduling calendar</span>}
                      {isConfigured && !isActive && <span style={CAL.warnPill}>configured, not readable</span>}
                    </div>
                    <div style={CAL.meta}>
                      <span style={c.state === 'connected' ? CAL.ok : c.state === 'not_connected' ? CAL.off : CAL.warn}>
                        {c.state === 'connected' ? 'Connected'
                          : c.state === 'not_connected' ? 'Not connected'
                          : 'Reconnect required'}
                      </span>
                      {c.account_email ? <> · <span className="mono">{c.account_email}</span></> : null}
                      {c.last_sync_at ? <> · last read {new Date(c.last_sync_at).toLocaleString()}</> : null}
                    </div>
                    {c.detail && <div style={CAL.detail}>{c.detail}</div>}
                    {c.last_error && <div style={CAL.detail}>Last error: {c.last_error}</div>}
                  </div>
                  <div style={CAL.actions}>
                    <button
                      className="btn btn--secondary"
                      style={CAL.btn}
                      disabled={calendarBusy === c.provider || !c.has_token}
                      onClick={() => testCalendar(c.provider)}
                    >
                      {calendarBusy === c.provider ? '…' : 'Test'}
                    </button>
                    <button
                      className="btn btn--primary"
                      style={CAL.btn}
                      disabled={calendarBusy === c.provider}
                      onClick={() => connectCalendarProvider(c)}
                    >
                      {c.has_token ? 'Reconnect' : 'Connect'}
                    </button>
                    {c.has_token && (
                      <button
                        className="btn btn--ghost"
                        style={CAL.btn}
                        disabled={calendarBusy === c.provider}
                        onClick={() => disconnectCalendar(c)}
                      >
                        Disconnect
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
            {calendarState?.uses_email_fallback && (
              <p className="settings-help">{calendarState.fallback_explainer}</p>
            )}
          </div>
        )}
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

      {/* ── Booking Link ── */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">🔗 Your Booking Link</h2>
          {profile.booking_page_url && <span className="badge badge--green">Set</span>}
        </div>
        <p className="settings-help">
          Your personal booking page URL — paste this into SMS/email templates so leads can book directly with you.
          Can be a Calendly link, Google booking page, or your BookaBoost scheduling URL.
        </p>
        <form onSubmit={saveBookingPage} className="settings-form">
          <label className="settings-label">
            Booking page URL
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                className="settings-input"
                type="url"
                value={bookingUrl}
                onChange={(e) => setBookingUrl(e.target.value)}
                placeholder="https://calendly.com/yourname or https://book.bookaboost.com/..."
                style={{ flex: 1 }}
              />
              {bookingUrl && (
                <button
                  type="button"
                  className="btn btn--secondary"
                  style={{ fontSize: 12, padding: '8px 14px', flexShrink: 0 }}
                  onClick={copyBookingUrl}
                >
                  {bookingCopied ? '✓ Copied' : 'Copy'}
                </button>
              )}
            </div>
          </label>
          <div className="settings-actions">
            {bookingSaved && <span className="settings-saved">Saved</span>}
            <button className="btn btn--primary" type="submit" disabled={savingBooking}>
              {savingBooking ? 'Saving…' : 'Save booking link'}
            </button>
          </div>
        </form>
      </section>

      {/* ── Booking Settings ── */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <h2 className="panel-title">📅 Booking Settings</h2>
        </div>
        <p className="settings-help">
          Control your availability and how appointments are booked through your BookaBoost scheduling link.
        </p>
        <form onSubmit={saveBookingSettings} className="settings-form">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14 }}>
            <label className="settings-label">
              Appointment duration (min)
              <input type="number" className="settings-input" min={5} max={480} value={apptDuration}
                onChange={e => setApptDuration(Number(e.target.value))} />
            </label>
            <label className="settings-label">
              Buffer between appts (min)
              <input type="number" className="settings-input" min={0} max={120} value={bufferMin}
                onChange={e => setBufferMin(Number(e.target.value))} />
            </label>
            <label className="settings-label">
              Max bookings per day
              <input type="number" className="settings-input" min={1} max={50} value={maxBookings}
                onChange={e => setMaxBookings(Number(e.target.value))} />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 14, marginTop: 4 }}>
            <label className="settings-label">
              Available from
              <input type="time" className="settings-input" value={startTime}
                onChange={e => setStartTime(e.target.value)} />
            </label>
            <label className="settings-label">
              Available until
              <input type="time" className="settings-input" value={endTime}
                onChange={e => setEndTime(e.target.value)} />
            </label>
            <label className="settings-label">
              Timezone
              <select className="settings-input" value={bookingTz} onChange={e => setBookingTz(e.target.value)}>
                <option value="America/Chicago">Central (CT)</option>
                <option value="America/New_York">Eastern (ET)</option>
                <option value="America/Denver">Mountain (MT)</option>
                <option value="America/Los_Angeles">Pacific (PT)</option>
                <option value="America/Phoenix">Arizona (MST)</option>
                <option value="America/Anchorage">Alaska (AKT)</option>
                <option value="Pacific/Honolulu">Hawaii (HT)</option>
              </select>
            </label>
          </div>
          <div style={{ marginTop: 4 }}>
            <label className="settings-label" style={{ marginBottom: 6 }}>Available days</label>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map((d, i) => {
                const checked = availDays.includes(i)
                return (
                  <label key={d} style={{
                    display: 'flex', alignItems: 'center', gap: 5, fontSize: 13, cursor: 'pointer',
                    background: checked ? 'var(--accent)' : 'var(--bg-secondary)',
                    color: checked ? '#fff' : 'var(--text-primary)',
                    padding: '5px 14px', borderRadius: 20, userSelect: 'none',
                    border: '1px solid var(--border)', transition: 'background 0.15s',
                  }}>
                    <input type="checkbox" style={{ display: 'none' }} checked={checked}
                      onChange={() => setAvailDays(prev =>
                        checked ? prev.filter(x => x !== i) : [...prev, i].sort((a, b) => a - b)
                      )} />
                    {d}
                  </label>
                )
              })}
            </div>
          </div>
          <label className="settings-label" style={{ marginTop: 4 }}>
            Booking confirmation message <span className="settings-optional">sent to lead after booking</span>
            <textarea className="settings-input" rows={3} value={bookingConfirmMsg}
              onChange={e => setBookingConfirmMsg(e.target.value)}
              placeholder="e.g. Thank you for scheduling with us! We look forward to seeing you." />
          </label>
          <div className="settings-actions">
            {bookingSettingsSaved && <span className="settings-saved">Saved ✓</span>}
            <button className="btn btn--primary" type="submit" disabled={savingBookingSettings}>
              {savingBookingSettings ? 'Saving…' : 'Save booking settings'}
            </button>
          </div>
        </form>
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
