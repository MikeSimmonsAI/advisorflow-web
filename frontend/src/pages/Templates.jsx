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

  // AI writer — both a one-click "Generate" from scratch and a free-text
  // instruction box to rewrite whatever's currently in the editor.
  const [aiInstruction, setAiInstruction] = useState('')
  const [aiTone, setAiTone] = useState('standard')
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
    setAiTone('standard')
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
        tone: aiTone,
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
        tone: aiTone,
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

                          <div className="template-ai-bar">
                            <select
                              className="settings-input template-ai-tone"
                              value={aiTone}
                              onChange={(e) => setAiTone(e.target.value)}
                              disabled={aiBusy}
                              title="How strongly should the generated copy push for a follow-up?"
                            >
                              <option value="soft">Soft</option>
                              <option value="standard">Standard</option>
                              <option value="urgent">Urgent</option>
                              <option value="direct">Direct</option>
                            </select>
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
