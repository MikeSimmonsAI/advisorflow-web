import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Templates.css'

const TRACK_LABELS = {
  pre_need_lock_price: 'Pre-Need (price-lock pitch)',
  at_need_support: 'At-Need (support)',
  imminent_support: 'Imminent (support)',
  upsell_existing: 'Contract Sold (upsell)',
  email_only_nurture: 'Email-only (nurture)',
  needs_review: 'Needs review (fallback)',
}

export default function Templates() {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(null) // { message_track, channel }
  const [draftBody, setDraftBody] = useState('')
  const [draftSubject, setDraftSubject] = useState('')
  const [saving, setSaving] = useState(false)

  function load() {
    setLoading(true)
    api.get('/templates/').then(setTemplates).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  function startEditing(t) {
    setEditing({ message_track: t.message_track, channel: t.channel })
    setDraftBody(t.body_template)
    setDraftSubject(t.email_subject_template || '')
  }

  async function handleSave() {
    setSaving(true)
    try {
      await api.put('/templates/', {
        message_track: editing.message_track,
        channel: editing.channel,
        body_template: draftBody,
        email_subject_template: editing.channel === 'email' ? draftSubject : null,
      })
      setEditing(null)
      load()
    } catch (err) {
      alert(`Failed to save: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  async function handleReset(t) {
    if (!confirm('Reset this template back to the default wording?')) return
    try {
      await api.delete(`/templates/${t.message_track}/${t.channel}`)
      load()
    } catch (err) {
      alert(`Failed to reset: ${err.message}`)
    }
  }

  // Group by track so SMS and email show side by side per tier
  const grouped = templates.reduce((acc, t) => {
    if (!acc[t.message_track]) acc[t.message_track] = {}
    acc[t.message_track][t.channel] = t
    return acc
  }, {})

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Message templates</h1>
          <p className="page-subtitle">Customize the wording for each lead type, for SMS and email.</p>
        </div>
      </header>

      {loading ? (
        <div className="empty-state">Loading templates…</div>
      ) : (
        <div className="template-groups">
          {Object.entries(grouped).map(([track, channels]) => (
            <section key={track} className="panel template-group">
              <div className="panel-header">
                <h2 className="panel-title">{TRACK_LABELS[track] || track}</h2>
              </div>
              <div className="template-channels">
                {['sms', 'email'].map((channel) => {
                  const t = channels[channel]
                  if (!t) return null
                  const isEditing = editing?.message_track === track && editing?.channel === channel
                  return (
                    <div key={channel} className="template-card">
                      <div className="template-card-header">
                        <span className="template-channel-label">{channel === 'sms' ? 'SMS' : 'Email'}</span>
                        {t.is_customized && <span className="badge badge--blue">Customized</span>}
                      </div>

                      {isEditing ? (
                        <div className="template-edit-form">
                          {channel === 'email' && (
                            <input
                              className="settings-input"
                              placeholder="Subject line"
                              value={draftSubject}
                              onChange={(e) => setDraftSubject(e.target.value)}
                            />
                          )}
                          <textarea
                            className="compose-textarea"
                            rows={5}
                            value={draftBody}
                            onChange={(e) => setDraftBody(e.target.value)}
                          />
                          <div className="template-edit-actions">
                            <button className="btn btn--secondary" onClick={() => setEditing(null)}>Cancel</button>
                            <button className="btn btn--primary" onClick={handleSave} disabled={saving}>
                              {saving ? 'Saving…' : 'Save'}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          {channel === 'email' && t.email_subject_template && (
                            <p className="template-subject-preview">{t.email_subject_template}</p>
                          )}
                          <p className="template-body-preview">{t.body_template}</p>
                          <div className="template-card-actions">
                            <button className="btn btn--secondary" onClick={() => startEditing(t)}>Edit</button>
                            {t.is_customized && (
                              <button className="btn btn--danger" onClick={() => handleReset(t)}>Reset to default</button>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
