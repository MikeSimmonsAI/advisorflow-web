/* One family's record, inside the Family Service Center.
   Only what applies to the program: who they are (editable), how they are
   reached, their messages, appointments, survey, notes and files. No platform
   tools. Uses the lead's own endpoints (GET/PATCH /leads/{id}, timeline, notes)
   plus the program's reach, appointments and files endpoints. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, Field, Icon, Loading, Notice, PageHead, Panel, errText, when, apptWhen } from './ui'

const EDIT_FIELDS = [
  ['first_name', 'First name'], ['last_name', 'Last name'], ['phone', 'Phone'], ['email', 'Email'],
  ['street_address', 'Street address'], ['city', 'City'], ['state', 'State'], ['zip_code', 'ZIP'],
]
const STATUS_LABEL = {
  new: 'New', sent: 'Contacted', replied: 'Replied', hot: 'HOT', booked: 'Booked', dnc: 'Do not contact',
  completed: 'Completed', lost: 'Closed',
}
const kb = n => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)

export default function Person({ leadId, onBack, isManager }) {
  const [lead, setLead] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState(null)
  const [saving, setSaving] = useState(false)
  const [reach, setReach] = useState(null)
  const [tl, setTl] = useState(null)
  const [appts, setAppts] = useState(null)
  const [survey, setSurvey] = useState(null)
  const [notes, setNotes] = useState(null)
  const [noteText, setNoteText] = useState('')
  const [files, setFiles] = useState(null)
  const [fileNote, setFileNote] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef(null)
  const [dnc, setDnc] = useState(null)          // null = closed; string = the reason being typed
  const [dncBusy, setDncBusy] = useState(false)

  const loadLead = useCallback(() => {
    setErr('')
    return api.get(`/leads/${leadId}`).then(setLead).catch(e => setErr(errText(e)))
  }, [leadId])
  const loadNotes = useCallback(() => api.get(`/work/leads/${leadId}/notes`).then(d => setNotes(d.items || [])).catch(() => setNotes([])), [leadId])
  const loadFiles = useCallback(() => api.get(`/program/leads/${leadId}/files`).then(d => setFiles(d.items || [])).catch(() => setFiles([])), [leadId])

  useEffect(() => {
    setLead(null); setForm(null); setReach(null); setTl(null); setAppts(null); setSurvey(null); setNotes(null); setFiles(null); setMsg('')
    loadLead(); loadNotes(); loadFiles()
    api.get(`/program/reach/${leadId}`).then(setReach).catch(() => setReach(false))
    api.get(`/leads/${leadId}/timeline?limit=100`).then(setTl).catch(() => setTl({ events: [] }))
    api.get(`/program/appointments?when=all&lead_id=${encodeURIComponent(leadId)}`).then(d => setAppts(d.items || [])).catch(() => setAppts([]))
    api.get(`/survey/results/${leadId}`).then(d => setSurvey(d?.responses || [])).catch(() => setSurvey([]))
    window.scrollTo?.({ top: 0 })
  }, [leadId, loadLead, loadNotes, loadFiles])

  const startEdit = () => setForm(Object.fromEntries(EDIT_FIELDS.map(([k]) => [k, lead?.[k] || ''])))
  const save = async e => {
    e.preventDefault()
    setSaving(true); setErr('')
    const changes = {}
    EDIT_FIELDS.forEach(([k]) => { if ((form[k] || '') !== (lead?.[k] || '')) changes[k] = form[k].trim() })
    try {
      if (Object.keys(changes).length) await api.patch(`/leads/${leadId}`, changes)
      await loadLead(); setForm(null); setMsg(Object.keys(changes).length ? 'Record updated.' : 'Nothing changed.')
    } catch (er) { setErr(errText(er)) } finally { setSaving(false) }
  }

  const addNote = async e => {
    e.preventDefault()
    const body = noteText.trim()
    if (!body) return
    try { await api.post(`/work/leads/${leadId}/notes`, { body }); setNoteText(''); loadNotes() } catch (er) { setErr(errText(er)) }
  }

  const upload = async f => {
    if (!f) return
    if (f.size > 15 * 1024 * 1024) { setErr('Files up to 15 MB.'); return }
    const fd = new FormData(); fd.append('file', f); if (fileNote.trim()) fd.append('note', fileNote.trim())
    setUploading(true); setErr('')
    try { await api.upload(`/program/leads/${leadId}/files`, fd); setFileNote(''); setMsg(`${f.name} added.`); loadFiles() }
    catch (er) { setErr(errText(er)) }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = '' }
  }
  const openFile = async f => {
    try {
      const blob = await api.get(`/program/leads/${leadId}/files/${f.id}`, { asBlob: true })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = f.filename; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 10000)
    } catch (er) { setErr(errText(er)) }
  }

  if (!lead && !err) return <Loading label="Opening the record…" />
  const name = lead ? [lead.first_name, lead.last_name].filter(Boolean).join(' ') || '(no name)' : 'Record'
  const events = [
    ...(tl?.events || []).filter(e => e && (e.body || e.type)),
    ...(tl?.voice_calls || []).map(c => ({ type: 'call', timestamp: c.started_at || c.created_at, body: `Call · ${c.status || c.direction || ''}` })),
  ].sort((a, b) => (+new Date(a.timestamp) || 0) - (+new Date(b.timestamp) || 0))

  const markDnc = async () => {
    setDncBusy(true); setErr('')
    try {
      const r = await api.post('/program/command/dnc', { lead_id: leadId, reason: dnc || '' })
      setDnc(null)
      setMsg(`${name} is on Do Not Contact. No texts, emails, AI replies or calls will go to them${r.phone_suppressed ? ', and their number is on the suppression list' : ''}.`)
      loadLead()
      api.get(`/program/reach/${leadId}`).then(setReach).catch(() => {})
    } catch (er) { setErr(errText(er)) } finally { setDncBusy(false) }
  }
  const isDnc = (lead?.status || '').toLowerCase() === 'dnc'

  return (
    <>
      <div className="sci-row-gap" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <button type="button" className="sci-btn sm" onClick={onBack}><Icon name="back" />Back</button>
        <div className="sci-row-gap">
          {lead?.status && <Chip tone={lead.status === 'hot' ? 'bad' : lead.status === 'booked' ? 'ok' : ''}>{STATUS_LABEL[lead.status] || lead.status}</Chip>}
          {reach && <Chip tone={reach.reaches === 'Text + Email' ? 'ok' : 'warn'}>{reach.reaches}</Chip>}
          {lead && !isDnc && dnc === null &&
            <button type="button" className="sci-btn sm danger" onClick={() => setDnc('')}>Do not contact</button>}
        </div>
      </div>
      {isDnc && <p className="sci-dnc-banner" role="status"><b>DO NOT CONTACT</b> No texts, emails, AI replies or calls go to this family.</p>}
      {dnc !== null && (
        <div className="sci-dnc-confirm" role="group" aria-label="Confirm Do Not Contact">
          <p><b>Put {name} on Do Not Contact?</b> Every text, email, AI reply and follow-up stops for good, and their number goes on the suppression list.</p>
          <input value={dnc} onChange={e => setDnc(e.target.value)} placeholder="Reason (optional) - e.g. asked by phone not to be contacted" maxLength={200} />
          <div className="sci-row-gap">
            <button type="button" className="sci-btn sm" onClick={() => setDnc(null)} disabled={dncBusy}>Cancel</button>
            <button type="button" className="sci-btn sm dangerfill" onClick={markDnc} disabled={dncBusy}>{dncBusy ? 'Saving…' : 'Yes, do not contact'}</button>
          </div>
        </div>
      )}
      <PageHead title={name} sub={[lead?.phone, lead?.email].filter(Boolean).join(' · ') || 'No contact details'} />
      <ErrorLine text={err} />
      <Notice text={msg} onClose={() => setMsg('')} />

      <div className="sci-grid-even">
        <div className="sci-stack" style={{ gap: 14 }}>
          <Panel title="Details" labelledBy="sci-p-details"
            aside={lead && !form && <button type="button" className="sci-btn sm" onClick={startEdit}><Icon name="edit" />Edit</button>}>
            {!lead ? <Loading /> : form ? (
              <form className="sci-form" onSubmit={save}>
                {EDIT_FIELDS.map(([k, label]) => (
                  <Field key={k} label={label}><input value={form[k]} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} /></Field>
                ))}
                <div className="sci-row-gap" style={{ gridColumn: '1 / -1' }}>
                  <button type="submit" className="sci-btn gold" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                  <button type="button" className="sci-btn" onClick={() => setForm(null)} disabled={saving}>Cancel</button>
                </div>
              </form>
            ) : (
              <dl className="sci-kvlist">
                {EDIT_FIELDS.map(([k, label]) => (
                  <div key={k}><dt>{label}</dt><dd className={lead[k] ? '' : 'missing'}>{lead[k] || 'Not provided'}</dd></div>
                ))}
              </dl>
            )}
          </Panel>

          <Panel title="How we reach them" labelledBy="sci-p-reach">
            {reach === null ? <Loading /> : !reach ? <p className="sci-micro sci-muted">Not on the program list.</p> : (
              <dl className="sci-kvlist">
                <div><dt>Reaches by</dt><dd>{reach.reaches}</dd></div>
                <div><dt>Last text</dt><dd>{reach.last_text_at ? when(reach.last_text_at) : 'Not yet'}</dd></div>
                <div><dt>Last email</dt><dd>{reach.last_email_at ? when(reach.last_email_at) : 'Not yet'}</dd></div>
                {reach.line_type === 'landline' && <div><dt>Phone</dt><dd>Landline — call, don't text</dd></div>}
              </dl>
            )}
          </Panel>

          <Panel title="Appointments" labelledBy="sci-p-appts">
            {appts === null ? <Loading /> : !appts.length ? <p className="sci-micro sci-muted">No visit booked.</p> : appts.map(a => (
              <div key={a.id} className="sci-kv"><span>{apptWhen(a.when)} · {a.location || 'Visit'}</span><span><Chip tone={a.status === 'cancelled' ? 'warn' : 'ok'} plain>{a.status}</Chip></span></div>
            ))}
          </Panel>

          <Panel title="Survey" labelledBy="sci-p-survey">
            {survey === null ? <Loading /> : !survey.length ? <p className="sci-micro sci-muted">No survey answered yet.</p> : survey.map(s => (
              <div key={s.id} style={{ marginBottom: 8 }}>
                <b>{s.rating ? `${s.rating} of 5` : '—'}</b> <span className="sci-micro sci-muted">{when(s.submitted_at)}{s.share_ok ? ' · OK to share' : ''}</span>
                {s.feedback && <p className="sci-small" style={{ margin: '2px 0 0', whiteSpace: 'pre-wrap' }}>{s.feedback}</p>}
              </div>
            ))}
          </Panel>
        </div>

        <div className="sci-stack" style={{ gap: 14 }}>
          <Panel title="Messages" labelledBy="sci-p-msgs">
            {tl === null ? <Loading /> : !events.length ? <p className="sci-micro sci-muted">No messages yet.</p> : (
              <div className="sci-thread" style={{ maxHeight: 420, overflow: 'auto' }}>
                {events.map((ev, i) => (
                  <div key={i} className={`sci-bubble ${ev.type === 'outbound' ? 'out' : ev.type === 'inbound' ? '' : 'sys'}`}>
                    {ev.body || ev.type}
                    <small>{when(ev.timestamp)}{ev.channel ? ` · ${ev.channel}` : ''}{ev.status ? ` · ${ev.status}` : ''}</small>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Notes" labelledBy="sci-p-notes">
            <form onSubmit={addNote} style={{ marginBottom: 10 }}>
              <label htmlFor="sci-p-note" className="sci-sr">Add a note</label>
              <textarea id="sci-p-note" className="sci-input" rows={3} style={{ width: '100%' }} value={noteText}
                onChange={e => setNoteText(e.target.value)} placeholder="Add a note about this family…" />
              <button type="submit" className="sci-btn sm" style={{ marginTop: 6 }} disabled={!noteText.trim()}>Add note</button>
            </form>
            {notes === null ? <Loading /> : !notes.length ? <p className="sci-micro sci-muted">No notes yet.</p> : notes.map(n => (
              <div key={n.id} style={{ borderTop: '1px solid rgba(0,0,0,.06)', padding: '8px 0' }}>
                <p className="sci-small" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{n.body}</p>
                <span className="sci-micro sci-muted">{n.author_name || 'Someone'} · {when(n.created_at)}</span>
              </div>
            ))}
          </Panel>

          <Panel title="Files" labelledBy="sci-p-files">
            <div className="sci-row-gap" style={{ marginBottom: 10, alignItems: 'flex-end' }}>
              <Field label="What is it? (optional)"><input value={fileNote} onChange={e => setFileNote(e.target.value)} placeholder="e.g. Signed planning form" /></Field>
              <input ref={fileRef} type="file" className="sci-sr" aria-label="Choose a file"
                accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.txt,.doc,.docx" onChange={e => upload(e.target.files?.[0])} />
              <button type="button" className="sci-btn sm gold" disabled={uploading} onClick={() => fileRef.current?.click()}>
                <Icon name="upload" />{uploading ? 'Adding…' : 'Add a file'}
              </button>
            </div>
            {files === null ? <Loading /> : !files.length ? <Empty icon="doc" title="No files yet">PDF, Word, text or pictures, up to 15 MB.</Empty> : files.map(f => (
              <div key={f.id} className="sci-kv">
                <span><button type="button" className="sci-namebtn" onClick={() => openFile(f)}>{f.filename}</button>
                  <span className="sci-micro sci-muted" style={{ display: 'block' }}>{f.note ? `${f.note} · ` : ''}{kb(f.size_bytes)} · {when(f.created_at)}</span></span>
                <button type="button" className="sci-btn sm" onClick={() => openFile(f)}>Download</button>
              </div>
            ))}
          </Panel>
        </div>
      </div>
    </>
  )
}
