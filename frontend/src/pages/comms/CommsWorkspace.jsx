// Right pane of the Communications command center: one contact's workspace.
// Every action writes to a real backend model (WS5 work_router + existing
// /sms reclassify). The composer is disabled, with the server's reasons,
// whenever the compose gate refuses — consent, DNC/STOP, suppression, quiet
// hours and sender readiness are decided by the server, never here.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { CLASSIFICATIONS, ClassTag, StatusTag, Tag, Icon, channelIcon, fmtDateTime, fmtPhone, timeAgo } from './commsShared'

const TABS = [
  { key: 'conversation', label: 'Conversation' },
  { key: 'notes', label: 'Notes' },
  { key: 'tasks', label: 'Tasks' },
  { key: 'details', label: 'Contact info' },
]

function initials(name) {
  return (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0].toUpperCase()).join('') || '?'
}

export default function CommsWorkspace({ leadId, reply, assignees, canWrite, onChanged, onBack }) {
  const navigate = useNavigate()
  const [thread, setThread] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('conversation')
  const [mode, setMode] = useState('reply')           // reply | note
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState('')
  const [flash, setFlash] = useState('')
  const [notes, setNotes] = useState(null)
  const [tasks, setTasks] = useState(null)
  const [taskForm, setTaskForm] = useState(null)       // {title, due}
  const endRef = useRef(null)

  const loadThread = useCallback(() => {
    if (!leadId) return
    setLoading(true)
    setError('')
    api.get(`/communications/thread/${leadId}`)
      .then(setThread)
      .catch(e => setError(e.message || 'Could not load this conversation.'))
      .finally(() => setLoading(false))
  }, [leadId])

  const loadNotes = useCallback(() => {
    api.get(`/work/leads/${leadId}/notes`).then(setNotes).catch(e => setNotes({ error: e.message }))
  }, [leadId])

  const loadTasks = useCallback(() => {
    api.get('/work/tasks', { params: { lead_id: leadId, status: 'all', page_size: 100 } })
      .then(setTasks).catch(e => setTasks({ error: e.message }))
  }, [leadId])

  useEffect(() => {
    setThread(null); setNotes(null); setTasks(null); setDraft(''); setFlash(''); setTaskForm(null)
    setTab('conversation'); setMode('reply')
    loadThread()
  }, [leadId, loadThread])

  useEffect(() => {
    if (tab === 'notes' && notes === null) loadNotes()
    if (tab === 'tasks' && tasks === null) loadTasks()
  }, [tab, notes, tasks, loadNotes, loadTasks])

  useEffect(() => {
    if (tab === 'conversation' && endRef.current) endRef.current.scrollIntoView({ block: 'end' })
  }, [thread, tab])

  async function run(label, fn, success) {
    setBusy(label); setError(''); setFlash('')
    try {
      await fn()
      if (success) setFlash(success)
      onChanged && onChanged()
    } catch (e) {
      setError(e.message || 'That did not work.')
    } finally {
      setBusy('')
    }
  }

  const lead = thread?.lead
  const gate = thread?.compose
  const replyBlocked = mode === 'reply' && gate && !gate.allowed

  async function submitComposer(e) {
    e.preventDefault()
    const body = draft.trim()
    if (!body) return
    if (mode === 'note') {
      await run('note', () => api.post(`/work/leads/${leadId}/notes`,
        { body, kind: 'internal', reply_id: reply?.id || null }), 'Internal note saved.')
      setDraft(''); setNotes(null); loadThread()
    } else {
      await run('send', () => api.post('/communications/send',
        { lead_id: leadId, body, reply_id: reply?.id || null }), 'Message sent.')
      setDraft(''); loadThread()
    }
  }

  async function aiDraft() {
    await run('draft', async () => {
      const d = await api.post(`/sms/draft-reply/${leadId}`, { tone: 'warm' })
      if (d?.suggested_reply) setDraft(d.suggested_reply)
    })
  }

  async function createTask(e) {
    e.preventDefault()
    if (!taskForm?.title?.trim()) return
    const due = taskForm.due ? new Date(taskForm.due).toISOString().slice(0, 19) : null
    await run('task', () => api.post('/work/tasks', {
      title: taskForm.title.trim(), lead_id: leadId, due_at: due,
      reply_id: reply?.id || null, source: reply ? 'reply' : 'manual',
    }), 'Task created.')
    setTaskForm(null); setTasks(null); if (tab !== 'tasks') setTab('tasks')
  }

  if (!leadId) {
    return (
      <section className="cc-pane cc-pane--empty">
        <Icon name="msg" size={28} />
        <p>Select a reply to see the conversation, notes and tasks.</p>
      </section>
    )
  }

  return (
    <section className="cc-pane" aria-label="Contact workspace">
      <header className="cc-contact">
        <button type="button" className="cc-iconbtn cc-back" onClick={onBack} aria-label="Back to queue">
          <Icon name="back" />
        </button>
        <div className="cc-avatar">{initials(lead?.name)}</div>
        <div className="cc-contact-main">
          <div className="cc-contact-name">{lead?.name || (loading ? 'Loading…' : '—')}</div>
          <div className="cc-contact-meta">
            {lead?.phone && <span>{fmtPhone(lead.phone)}</span>}
            {lead?.email && <span>{lead.email}</span>}
            {lead?.assigned_to_name && <span>Owner: {lead.assigned_to_name}</span>}
          </div>
          <div className="cc-contact-tags">
            {reply && <ClassTag value={reply.classification} />}
            {reply && <StatusTag value={reply.status} />}
            {lead?.is_dnc && <Tag tone="red"><Icon name="ban" size={12} /> Do Not Contact</Tag>}
            {!lead?.is_dnc && thread?.compose?.reasons?.some(r => r.code === 'STOP_REPLY') &&
              <Tag tone="red"><Icon name="ban" size={12} /> Replied STOP</Tag>}
            {lead?.suppressed && <Tag tone="red">Suppressed</Tag>}
            {lead && (lead.sms_consent
              ? <Tag tone="green" title={lead.sms_consent_source || ''}>SMS consent on record</Tag>
              : <Tag tone="amber">No SMS consent</Tag>)}
            {reply?.assigned_to_name && <Tag tone="blue"><Icon name="user" size={12} /> {reply.assigned_to_name}</Tag>}
          </div>
        </div>
        <button type="button" className="cc-btn cc-btn--ghost cc-open" onClick={() => navigate(`/leads/${leadId}`)}>
          Open record <Icon name="ext" size={13} />
        </button>
      </header>

      {canWrite && (
        <div className="cc-actions">
          {reply && (reply.reviewed_at
            ? <button type="button" className="cc-btn" disabled={!!busy}
                onClick={() => run('review', () => api.post(`/communications/replies/${reply.id}/review`, { reviewed: false }), 'Reopened.')}>
                Reopen
              </button>
            : <button type="button" className="cc-btn cc-btn--primary" disabled={!!busy}
                onClick={() => run('review', () => api.post(`/communications/replies/${reply.id}/review`, { reviewed: true }), 'Marked reviewed.')}>
                <Icon name="check" size={14} /> Mark reviewed
              </button>)}
          {reply && (
            <label className="cc-inline-select">
              <span>Assign</span>
              <select value={reply.assigned_to_id || ''} disabled={!!busy}
                onChange={e => run('assign', () => api.post(`/communications/replies/${reply.id}/assign`,
                  { assigned_to_id: e.target.value || null }), 'Assignment saved.')}>
                <option value="">Unassigned</option>
                {(assignees || []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
          )}
          {reply && (
            <label className="cc-inline-select">
              <span>Classify</span>
              <select value={reply.classification || ''} disabled={!!busy}
                onChange={e => run('cls', () => api.patch(`/sms/replies/${reply.id}/reclassify`,
                  { classification: e.target.value }), 'Classification updated.')}>
                {Object.entries(CLASSIFICATIONS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            </label>
          )}
          <button type="button" className="cc-btn" disabled={!!busy}
            onClick={() => setTaskForm({ title: reply ? `Follow up with ${lead?.name || 'contact'}` : '', due: '' })}>
            <Icon name="task" size={14} /> Create task
          </button>
          {reply && (
            <button type="button" className="cc-btn" disabled={!!busy}
              onClick={() => run('cb', () => api.post(`/communications/replies/${reply.id}/callback-task`, {}),
                'Callback task created.').then(() => setTasks(null))}>
              <Icon name="phone" size={14} /> Callback
            </button>
          )}
          <button type="button" className="cc-btn" disabled={!!busy}
            onClick={() => { setTab('conversation'); setMode('note') }}>
            <Icon name="note" size={14} /> Internal note
          </button>
        </div>
      )}

      {taskForm && (
        <form className="cc-taskform" onSubmit={createTask}>
          <input autoFocus placeholder="Task title" value={taskForm.title} maxLength={300}
            onChange={e => setTaskForm(f => ({ ...f, title: e.target.value }))} />
          <input type="datetime-local" value={taskForm.due} aria-label="Due"
            onChange={e => setTaskForm(f => ({ ...f, due: e.target.value }))} />
          <button type="submit" className="cc-btn cc-btn--primary" disabled={busy === 'task'}>Save task</button>
          <button type="button" className="cc-btn cc-btn--ghost" onClick={() => setTaskForm(null)}>Cancel</button>
        </form>
      )}

      {(error || flash) && <div className={`cc-flash ${error ? 'is-error' : ''}`}>{error || flash}</div>}

      <nav className="cc-subtabs" role="tablist">
        {TABS.map(t => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={`cc-subtab ${tab === t.key ? 'is-active' : ''}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </nav>

      <div className="cc-pane-body">
        {tab === 'conversation' && (
          <div className="cc-thread">
            {loading && !thread && <div className="cc-muted cc-pad">Loading conversation…</div>}
            {thread && thread.events.length === 0 && <div className="cc-muted cc-pad">No messages yet.</div>}
            {thread?.events.map(ev => <ThreadItem key={`${ev.type}-${ev.id}`} ev={ev} />)}
            <div ref={endRef} />
          </div>
        )}
        {tab === 'notes' && <NotesTab notes={notes} leadId={leadId} canWrite={canWrite}
          onChange={() => { setNotes(null); loadThread() }} />}
        {tab === 'tasks' && <TasksTab tasks={tasks} canWrite={canWrite} onChange={() => { setTasks(null); onChanged && onChanged() }} />}
        {tab === 'details' && <DetailsTab lead={lead} gate={gate} />}
      </div>

      {tab === 'conversation' && canWrite && (
        <form className="cc-composer" onSubmit={submitComposer}>
          <div className="cc-composer-modes">
            <button type="button" className={`cc-mode ${mode === 'reply' ? 'is-active' : ''}`} onClick={() => setMode('reply')}>
              <Icon name="send" size={13} /> Reply (SMS)
            </button>
            <button type="button" className={`cc-mode ${mode === 'note' ? 'is-active' : ''}`} onClick={() => setMode('note')}>
              <Icon name="lock" size={13} /> Internal note
            </button>
            {mode === 'reply' && gate?.allowed && gate.from_number &&
              <span className="cc-muted cc-from">From {fmtPhone(gate.from_number)}</span>}
          </div>
          {replyBlocked && (
            <div className="cc-blocked" role="status">
              <strong>Sending is blocked.</strong>
              <ul>{gate.reasons.map(r => <li key={r.code}>{r.label}</li>)}</ul>
            </div>
          )}
          <textarea rows={3} value={draft} maxLength={1600} disabled={replyBlocked || !!busy}
            placeholder={mode === 'note' ? 'Write an internal note — never sent to the customer'
              : replyBlocked ? 'Composer disabled' : 'Type your reply…'}
            onChange={e => setDraft(e.target.value)} />
          <div className="cc-composer-foot">
            <span className="cc-muted">{draft.length}/1600</span>
            {mode === 'reply' && !replyBlocked && (
              <button type="button" className="cc-btn cc-btn--ghost" disabled={!!busy} onClick={aiDraft}>
                <Icon name="bot" size={14} /> Draft with AI
              </button>
            )}
            <button type="submit" className="cc-btn cc-btn--primary" disabled={!draft.trim() || replyBlocked || !!busy}>
              {mode === 'note' ? 'Save note' : busy === 'send' ? 'Sending…' : 'Send'}
            </button>
          </div>
        </form>
      )}
    </section>
  )
}

function ThreadItem({ ev }) {
  if (ev.type === 'note') {
    return (
      <div className="cc-ev cc-ev--note">
        <div className="cc-ev-head"><Icon name="lock" size={12} /> Internal note · {ev.sender || 'Unknown'} · {fmtDateTime(ev.at)}</div>
        <div className="cc-ev-body">{ev.body}</div>
      </div>
    )
  }
  if (ev.type === 'call') {
    return (
      <div className="cc-ev cc-ev--call">
        <div className="cc-ev-head"><Icon name="phone" size={12} /> {ev.direction === 'inbound' ? 'Inbound call' : 'Call'}
          {ev.voicemail_left ? ' · voicemail left' : ''} · {ev.status || '—'}
          {ev.duration_seconds ? ` · ${Math.round(ev.duration_seconds / 60)}m` : ''} · {fmtDateTime(ev.at)}</div>
        {ev.summary && <div className="cc-ev-body">{ev.summary}</div>}
      </div>
    )
  }
  const inbound = ev.direction === 'inbound'
  return (
    <div className={`cc-ev ${inbound ? 'cc-ev--in' : 'cc-ev--out'} ${ev.actor === 'ai' ? 'cc-ev--ai' : ''}`}>
      <div className="cc-bubble">
        {ev.subject && <div className="cc-ev-subject">{ev.subject}</div>}
        {ev.body && <div className="cc-ev-body">{ev.body}</div>}
      </div>
      <div className="cc-ev-meta">
        <Icon name={channelIcon(ev.channel)} size={11} />
        {inbound ? (ev.sender || 'Customer') : (ev.actor === 'ai' ? 'AI' : ev.sender || 'Staff')}
        {' · '}{fmtDateTime(ev.at)}
        {!inbound && ev.state ? ` · ${ev.state}` : ''}
        {inbound && ev.classification && <> · <ClassTag value={ev.classification} /></>}
      </div>
    </div>
  )
}

function NotesTab({ notes, leadId, canWrite, onChange }) {
  const [body, setBody] = useState('')
  const [err, setErr] = useState('')
  if (notes === null) return <div className="cc-muted cc-pad">Loading notes…</div>
  if (notes.error) return <div className="cc-flash is-error">{notes.error}</div>
  async function add(e) {
    e.preventDefault()
    if (!body.trim()) return
    try { await api.post(`/work/leads/${leadId}/notes`, { body: body.trim() }); setBody(''); onChange() }
    catch (e2) { setErr(e2.message) }
  }
  async function act(fn) { try { await fn(); onChange() } catch (e2) { setErr(e2.message) } }
  return (
    <div className="cc-list">
      {canWrite && (
        <form className="cc-noteform" onSubmit={add}>
          <textarea rows={2} value={body} placeholder="Add a note…" maxLength={10000} onChange={e => setBody(e.target.value)} />
          <button type="submit" className="cc-btn cc-btn--primary" disabled={!body.trim()}>Add note</button>
        </form>
      )}
      {err && <div className="cc-flash is-error">{err}</div>}
      {notes.items.length === 0 && !notes.legacy_notes && <div className="cc-muted cc-pad">No notes yet.</div>}
      {notes.items.map(n => (
        <div key={n.id} className={`cc-card ${n.pinned ? 'is-pinned' : ''}`}>
          <div className="cc-card-head">
            <span>{n.author_name || 'Unknown'} · {fmtDateTime(n.created_at)}</span>
            {n.kind === 'internal' && <Tag tone="violet">Internal</Tag>}
            {n.pinned && <Tag tone="blue">Pinned</Tag>}
            {canWrite && (
              <span className="cc-card-actions">
                <button type="button" className="cc-link" onClick={() => act(() => api.patch(`/work/leads/${leadId}/notes/${n.id}`, { pinned: !n.pinned }))}>
                  {n.pinned ? 'Unpin' : 'Pin'}
                </button>
                {n.mine && <button type="button" className="cc-link cc-link--danger"
                  onClick={() => act(() => api.delete(`/work/leads/${leadId}/notes/${n.id}`))}>Delete</button>}
              </span>
            )}
          </div>
          <div className="cc-card-body">{n.body}</div>
        </div>
      ))}
      {notes.legacy_notes && (
        <div className="cc-card">
          <div className="cc-card-head"><span>Record notes (from the lead page)</span></div>
          <div className="cc-card-body">{notes.legacy_notes}</div>
        </div>
      )}
    </div>
  )
}

function TasksTab({ tasks, canWrite, onChange }) {
  const [err, setErr] = useState('')
  if (tasks === null) return <div className="cc-muted cc-pad">Loading tasks…</div>
  if (tasks.error) return <div className="cc-flash is-error">{tasks.error}</div>
  async function setStatus(t, status) {
    try { await api.patch(`/work/tasks/${t.id}`, { status }); onChange() } catch (e) { setErr(e.message) }
  }
  return (
    <div className="cc-list">
      {err && <div className="cc-flash is-error">{err}</div>}
      {tasks.items.length === 0 && <div className="cc-muted cc-pad">No tasks for this contact. Use “Create task” above.</div>}
      {tasks.items.map(t => (
        <div key={t.id} className={`cc-card cc-task ${t.status !== 'open' ? 'is-done' : ''}`}>
          <label className="cc-task-check">
            <input type="checkbox" checked={t.status === 'done'} disabled={!canWrite || t.status === 'cancelled'}
              onChange={e => setStatus(t, e.target.checked ? 'done' : 'open')} />
            <span className="cc-task-title">{t.title}</span>
          </label>
          <div className="cc-task-meta">
            {t.due_at ? <span className={t.overdue ? 'cc-overdue' : ''}>Due {fmtDateTime(t.due_at)}</span> : <span>No due date</span>}
            {t.assigned_to_name && <span>· {t.assigned_to_name}</span>}
            {t.source && t.source !== 'manual' && <span>· from {t.source}</span>}
            {t.status === 'cancelled' && <Tag tone="grey">Cancelled</Tag>}
            {canWrite && t.status === 'open' &&
              <button type="button" className="cc-link" onClick={() => setStatus(t, 'cancelled')}>Cancel</button>}
          </div>
        </div>
      ))}
    </div>
  )
}

function Row({ k, v }) {
  return <div className="cc-kv"><span>{k}</span><span>{v ?? '—'}</span></div>
}

function DetailsTab({ lead, gate }) {
  if (!lead) return <div className="cc-muted cc-pad">Loading…</div>
  return (
    <div className="cc-list">
      <div className="cc-card">
        <div className="cc-card-head"><span>Contact</span></div>
        <Row k="Name" v={lead.name} />
        <Row k="Phone" v={fmtPhone(lead.phone)} />
        <Row k="Email" v={lead.email} />
        <Row k="Location" v={[lead.city, lead.state].filter(Boolean).join(', ') || null} />
        <Row k="Record status" v={lead.status} />
        <Row k="Owner" v={lead.assigned_to_name} />
        <Row k="Source" v={lead.source} />
        <Row k="Created" v={lead.created_at ? timeAgo(lead.created_at) : null} />
      </div>
      <div className="cc-card">
        <div className="cc-card-head"><span>Consent &amp; compliance</span></div>
        <Row k="SMS consent" v={lead.sms_consent ? 'On record' : 'Not on record'} />
        <Row k="Consent source" v={lead.sms_consent_source} />
        <Row k="Consent recorded" v={lead.sms_consent_at ? fmtDateTime(lead.sms_consent_at) : null} />
        <Row k="Do Not Contact" v={lead.is_dnc ? 'Yes' : 'No'} />
        <Row k="Suppression list" v={lead.suppressed == null ? 'Unknown' : lead.suppressed ? 'Suppressed' : 'Not suppressed'} />
        <Row k="Email permission" v={lead.allow_email == null ? 'Not stated' : lead.allow_email ? 'Allowed' : 'Opted out'} />
        <p className="cc-muted cc-small">
          Consent and Do Not Contact are shown here, never changed here. They are managed in the lead record
          and the Compliance Center.
        </p>
        {gate && !gate.allowed && (
          <div className="cc-blocked"><strong>SMS currently blocked</strong>
            <ul>{gate.reasons.map(r => <li key={r.code}>{r.label}</li>)}</ul></div>
        )}
      </div>
    </div>
  )
}
