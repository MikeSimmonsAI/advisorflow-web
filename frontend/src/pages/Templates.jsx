import { useEffect, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './Templates.css'

// TRACK LABELS ARE A BUSINESS'S OWN, NOT THE PLATFORM'S.
//
// This was a lookup table naming a funeral home's tracks — Pre-Need
// (price-lock pitch), At-Need (support), Imminent, Contract Sold — as though
// every tenant had them, with `|| track` showing the raw key for anybody whose
// tracks were not on the list. So the businesses this platform actually
// serves saw `new_inquiry_intro` while a single vertical got prose.
//
// A track key is authored by the organization in its own tier definitions.
// Turning it back into words is grammar, and grammar works for every business;
// a dictionary of one vertical's tracks does not.
const TRACK_SUFFIXES = {
  intro: 'intro', nurture: 'nurture', support: 'support',
  followup: 'follow-up', follow_up: 'follow-up', upsell: 'upsell',
  fallback: 'fallback', reminder: 'reminder', review: 'review',
}

function trackLabel(track) {
  const key = String(track || '').trim()
  if (!key) return 'Untitled track'
  const parts = key.split(/[_\-\s]+/).filter(Boolean)
  const last = parts.length > 1 ? parts[parts.length - 1].toLowerCase() : null
  const suffix = last && TRACK_SUFFIXES[last] ? parts.pop() : null
  const name = parts
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
  return suffix ? `${name} (${TRACK_SUFFIXES[suffix.toLowerCase()]})` : name
}

export default function Templates() {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(null) // { message_track, channel }
  const [draftBody, setDraftBody] = useState('')
  const [draftSubject, setDraftSubject] = useState('')
  const [saving, setSaving] = useState(false)

  // AI writer — both a one-click "Generate" from scratch and a free-text
  // instruction box to rewrite whatever's currently in the editor.
  const [aiInstruction, setAiInstruction] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [aiError, setAiError] = useState('')

  function load() {
    setLoading(true)
    api.get('/templates/').then(setTemplates).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  function startEditing(t) {
    setEditing({ message_track: t.message_track, channel: t.channel })
    setDraftBody(t.body_template)
    setDraftSubject(t.email_subject_template || '')
    setAiInstruction('')
    setAiError('')
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

  async function handleAiGenerate() {
    setAiBusy(true)
    setAiError('')
    try {
      const result = await api.post('/templates/ai/generate', {
        message_track: editing.message_track,
        channel: editing.channel,
        instruction: aiInstruction.trim() || null,
      })
      setDraftBody(result.body_template)
      if (editing.channel === 'email') setDraftSubject(result.subject_template)
    } catch (err) {
      setAiError(err.message || 'AI generation failed.')
    } finally {
      setAiBusy(false)
    }
  }

  async function handleAiRewrite() {
    if (!aiInstruction.trim()) {
      setAiError('Type an instruction first (e.g. "make this warmer" or "shorter").')
      return
    }
    setAiBusy(true)
    setAiError('')
    try {
      const result = await api.post('/templates/ai/rewrite', {
        message_track: editing.message_track,
        channel: editing.channel,
        current_body: draftBody,
        current_subject: editing.channel === 'email' ? draftSubject : null,
        instruction: aiInstruction.trim(),
      })
      setDraftBody(result.body_template)
      if (editing.channel === 'email') setDraftSubject(result.subject_template)
    } catch (err) {
      setAiError(err.message || 'AI rewrite failed.')
    } finally {
      setAiBusy(false)
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
      ) : Object.keys(grouped).length === 0 ? (
        <div className="empty-state">
          <strong>No message templates yet.</strong>
          <p style={{ marginTop: 8, color: 'var(--text-secondary)', maxWidth: 440 }}>
            Templates are generated automatically from your tier definitions — go to <strong>Tier Definitions</strong> to create or seed your first templates, or import leads to trigger auto-generation.
          </p>
        </div>
      ) : (
        <div className="template-groups">
          {Object.entries(grouped).map(([track, channels]) => (
            <section key={track} className="panel template-group">
              <div className="panel-header">
                <h2 className="panel-title">{trackLabel(track)}</h2>
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

                          <div className="template-ai-bar">
                            <input
                              className="settings-input template-ai-instruction"
                              placeholder='Optional: "make this warmer", "shorter", "add urgency"…'
                              value={aiInstruction}
                              onChange={(e) => setAiInstruction(e.target.value)}
                              disabled={aiBusy}
                            />
                            <button
                              type="button"
                              className="btn btn--secondary"
                              onClick={handleAiGenerate}
                              disabled={aiBusy}
                              title="Generate a fresh draft from scratch for this track and channel"
                            >
                              {aiBusy ? 'Working…' : 'Generate with AI'}
                            </button>
                            <button
                              type="button"
                              className="btn btn--secondary"
                              onClick={handleAiRewrite}
                              disabled={aiBusy || !draftBody.trim()}
                              title="Rewrite the current draft above per your instruction"
                            >
                              {aiBusy ? 'Working…' : 'Rewrite with AI'}
                            </button>
                          </div>
                          {aiError && <div className="compose-error">{aiError}</div>}

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
