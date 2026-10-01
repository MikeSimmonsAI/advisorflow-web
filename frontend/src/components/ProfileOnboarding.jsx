/**
 * ProfileOnboarding — Mandatory profile completion checklist.
 *
 * Appears as a persistent floating card for any user whose profile is
 * incomplete. It cannot be fully dismissed until every required item is
 * checked off. Users can minimize it (collapses to a small tab) but the
 * card reappears expanded on every page load while items remain incomplete.
 *
 * Required items:
 *   1. Password changed (must_change_password === false)
 *   2. Full name set (not just an email prefix)
 *   3. Phone number
 *   4. Profile photo (headshot)
 */

import { useState, useEffect, useRef } from 'react'
import { api, getCurrentUser } from '../api/client'

// Fields we consider "placeholder" names (created by admin, not the user's real name)
const PLACEHOLDER_NAME_RE = /^(advisor|user|new user|temp|test)$/i

function checkItems(profile, user) {
  return [
    {
      id: 'password',
      label: 'Set your password',
      hint: 'Change the temporary password you were given',
      done: user ? !user.must_change_password : true,
      action: null, // handled by must_change_password modal
    },
    {
      id: 'name',
      label: 'Add your full name',
      hint: 'First and last name so your team knows who you are',
      done: profile
        ? !!(profile.full_name && profile.full_name.trim().length > 2 && !PLACEHOLDER_NAME_RE.test(profile.full_name.trim()))
        : false,
      field: 'full_name',
    },
    {
      id: 'phone',
      label: 'Add your phone number',
      hint: 'Your direct contact number',
      done: profile ? !!(profile.phone && profile.phone.trim()) : false,
      field: 'phone',
    },
    {
      id: 'photo',
      label: 'Upload a profile photo',
      hint: 'A headshot so clients and teammates recognize you',
      done: profile ? !!profile.profile_photo_url : false,
      field: 'photo',
    },
  ]
}

export default function ProfileOnboarding() {
  const user = getCurrentUser()
  const [profile, setProfile] = useState(null)
  // On a phone the full card would cover most of the page; start as the tab.
  // Starts as the compact pill at every width: expanded by default it sat over
  // the bottom-right action buttons of full pages (import wizard, drawers).
  const [minimized, setMinimized] = useState(true)
  const [saving, setSaving] = useState(false)
  const [activeField, setActiveField] = useState(null)
  const [fieldValue, setFieldValue] = useState('')
  const fileRef = useRef(null)

  useEffect(() => {
    api.get('/settings/profile').then(setProfile).catch(() => {})
  }, [])

  // god_admin and super_admin don't need to complete a profile —
  // they're platform operators, not advisors with clients.
  if (user?.role === 'god_admin' || user?.role === 'super_admin') return null

  if (!profile) return null

  const items = checkItems(profile, user)
  const allDone = items.every(i => i.done)
  if (allDone) return null

  const doneCount = items.filter(i => i.done).length
  const pct = Math.round((doneCount / items.length) * 100)

  async function saveField() {
    if (!activeField || !fieldValue.trim()) return
    setSaving(true)
    try {
      if (activeField === 'photo') {
        // already handled by file input — fieldValue is base64
        await api.patch('/settings/profile-photo', { photo_data_url: fieldValue })
      } else {
        await api.patch('/settings/profile', { [activeField]: fieldValue.trim() })
      }
      const updated = await api.get('/settings/profile')
      setProfile(updated)
      setActiveField(null)
      setFieldValue('')
    } catch (e) {
      console.error('Profile save error:', e)
    } finally {
      setSaving(false)
    }
  }

  function handlePhotoFile(file) {
    if (!file) return
    if (file.size > 500 * 1024) { alert('Photo must be under 500KB'); return }
    const reader = new FileReader()
    reader.onload = async (e) => {
      const b64 = e.target.result
      setSaving(true)
      try {
        await api.patch('/settings/profile-photo', { photo_data_url: b64 })
        const updated = await api.get('/settings/profile')
        setProfile(updated)
        setActiveField(null)
      } catch (err) { console.error(err) }
      finally { setSaving(false) }
    }
    reader.readAsDataURL(file)
  }

  // ── Minimized tab ──────────────────────────────────────────────────────
  if (minimized) {
    // On a phone the full pill sat over page content (filters, card titles) on
    // every screen, and on /m over the bottom tab bar. Narrow screens get a
    // compact icon + count chip, lifted above the /m tab bar.
    const narrow = typeof window !== 'undefined' && window.innerWidth <= 640
    const onMobileShell = typeof window !== 'undefined' && /^\/m(\/|$)/.test(window.location.pathname)
    return (
      <button
        onClick={() => setMinimized(false)}
        aria-label={`Complete your profile (${doneCount} of ${items.length} done)`}
        title="Complete your profile"
        style={{
          position: 'fixed', bottom: onMobileShell ? 84 : 16, right: narrow ? 12 : 16, zIndex: 60,
          background: '#ffffff', border: '1px solid #d6e2f3',
          borderRadius: narrow ? 999 : 12, padding: narrow ? '6px 10px' : '10px 16px', cursor: 'pointer', color: '#0f1b2d',
          display: 'flex', alignItems: 'center', gap: narrow ? 6 : 10, boxShadow: '0 6px 24px rgba(15,27,45,0.14)',
          fontFamily: 'Inter, sans-serif', fontSize: 13,
        }}
      >
        <span style={{ fontSize: narrow ? 16 : 18 }}>📋</span>
        {!narrow && <span>Complete your profile</span>}
        <span style={{
          background: '#eaf2fe', border: '1px solid #c7dbfa',
          borderRadius: 20, padding: '2px 8px', fontSize: 11, fontWeight: 700, color: '#1d63d1'
        }}>{doneCount}/{items.length}</span>
      </button>
    )
  }

  // ── Full card ──────────────────────────────────────────────────────────
  return (
    <div style={{
      position: 'fixed', bottom: 16, right: 16, zIndex: 60, width: 'min(360px, calc(100vw - 32px))',
      background: '#ffffff',
      border: '1px solid #d6e2f3',
      borderRadius: 16, overflow: 'hidden',
      boxShadow: '0 12px 40px rgba(15,27,45,0.18)', color: '#0f1b2d',
      fontFamily: 'Inter, sans-serif',
    }}>
      {/* Header */}
      <div style={{
        background: '#f4f8fe',
        borderBottom: '1px solid #e4e9f1',
        padding: '14px 16px 12px',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#0f1b2d', marginBottom: 2 }}>
            Complete your profile
          </div>
          <div style={{ fontSize: 11, color: '#5b6b82' }}>
            {doneCount} of {items.length} done — required to continue
          </div>
        </div>
        <button
          onClick={() => setMinimized(true)}
          title="Minimize"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#5b6b82', fontSize: 18, lineHeight: 1, padding: 2 }}
        >–</button>
      </div>

      {/* Progress bar */}
      <div style={{ height: 3, background: '#e4e9f1' }}>
        <div style={{ height: '100%', width: pct + '%', background: 'linear-gradient(90deg, #1d63d1, #067a55)', transition: 'width .4s' }} />
      </div>

      {/* Checklist */}
      <div style={{ padding: '10px 0' }}>
        {items.map(item => (
          <div key={item.id}>
            <div
              onClick={() => {
                if (item.done || !item.field) return
                if (item.field === 'photo') { fileRef.current?.click(); return }
                setActiveField(item.field)
                setFieldValue('')
              }}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '10px 16px', cursor: item.done || !item.field ? 'default' : 'pointer',
                transition: 'background .12s',
                background: activeField === item.field ? '#eef4fd' : 'transparent',
              }}
              onMouseEnter={e => { if (!item.done && item.field) e.currentTarget.style.background = '#f4f8fe' }}
              onMouseLeave={e => { e.currentTarget.style.background = activeField === item.field ? '#eef4fd' : 'transparent' }}
            >
              {/* Checkbox */}
              <div style={{
                width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: item.done ? '#e3f4ec' : '#ffffff',
                border: `2px solid ${item.done ? '#7cc9a6' : '#c9d3e1'}`,
                transition: 'all .2s',
              }}>
                {item.done && <span style={{ color: '#067a55', fontSize: 13, lineHeight: 1 }}>✓</span>}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: item.done ? '#7a889c' : '#0f1b2d', textDecoration: item.done ? 'line-through' : 'none' }}>
                  {item.label}
                </div>
                {!item.done && (
                  <div style={{ fontSize: 11, color: '#5b6b82', marginTop: 1 }}>{item.hint}</div>
                )}
              </div>
              {!item.done && item.field && (
                <span style={{ fontSize: 11, fontWeight: 600, color: '#1d63d1' }}>Edit →</span>
              )}
            </div>

            {/* Inline edit form */}
            {activeField === item.field && item.field !== 'photo' && (
              <div style={{ padding: '4px 16px 12px 50px', display: 'flex', gap: 8 }}>
                <input
                  autoFocus
                  type={item.field === 'phone' ? 'tel' : 'text'}
                  placeholder={item.field === 'phone' ? '+1 (555) 000-0000' : 'Your full name'}
                  value={fieldValue}
                  onChange={e => setFieldValue(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveField(); if (e.key === 'Escape') { setActiveField(null); setFieldValue('') } }}
                  style={{
                    flex: 1, padding: '7px 10px', borderRadius: 8, fontSize: 13,
                    background: '#ffffff', border: '1px solid #c9d3e1',
                    color: '#0f1b2d', outline: 'none',
                  }}
                />
                <button
                  onClick={saveField}
                  disabled={saving || !fieldValue.trim()}
                  style={{
                    padding: '7px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600,
                    background: saving ? '#9dbbe8' : '#1d63d1',
                    border: 'none', color: '#fff', cursor: saving ? 'default' : 'pointer',
                  }}
                >{saving ? '…' : 'Save'}</button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div style={{ padding: '10px 16px', borderTop: '1px solid #e4e9f1', fontSize: 11, color: '#5b6b82', textAlign: 'center' }}>
        This checklist stays until your profile is complete
      </div>

      {/* Hidden file input for photo */}
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        style={{ display: 'none' }}
        onChange={e => handlePhotoFile(e.target.files[0])}
      />
    </div>
  )
}
