// CONVERSATION BRAIN - what this conversation has established, and what to do next.
//
// Everything shown is read from /conversation-intel/leads/{id}: facts the
// customer stated (with their words and the channel), inferences labelled as
// NOT confirmed, what is still unknown, open questions, objections, the
// follow-up they asked for (with any earlier answer struck through), who is
// driving (AI or a person), and the next best action.
//
// The Smart Composer drafts; it never sends. "Use" hands the draft to the
// page's own composer, which applies every send gate.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { asUtc } from '../pages/sales/calendarTime'
import './ConversationBrain.css'

const STATE_LABEL = {
  new: 'New', engaged: 'Engaged', question: 'Question', qualifying: 'Qualifying', interested: 'Interested',
  objection: 'Objection', follow_up_needed: 'Follow-up needed', appointment_ready: 'Appointment ready',
  appointment_set: 'Appointment set', waiting_on_customer: 'Waiting on customer', waiting_on_staff: 'Waiting on us',
  human_review: 'Human review', not_now: 'Not now', nurture: 'Nurture', stopped: 'Stopped', closed: 'Closed',
}
const MODE_LABEL = {
  ai_active: 'AI active', human_active: 'Human active', ai_paused: 'AI paused',
  waiting_on_customer: 'Waiting on customer', waiting_on_human: 'Waiting on a person',
}
const ACTION_LABEL = {
  reply: 'Reply', wait: 'Wait', call: 'Call', create_task: 'Create task', schedule: 'Schedule',
  assign: 'Assign', request_info: 'Request info', move_stage: 'Move stage', nurture: 'Nurture',
  escalate: 'Escalate', human_takeover: 'Human takeover', do_nothing: 'Do nothing',
}
const HEALTH_TONE = { healthy: 'ok', at_risk: 'warn', stale: 'warn', blocked: 'bad', stopped: 'bad' }

function when(iso) {
  const d = asUtc(iso)
  if (!d || Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function Chip({ children, tone }) {
  return <span className={'cb-chip' + (tone ? ' cb-chip--' + tone : '')}>{children}</span>
}

function Section({ title, count, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="cb-section">
      <button type="button" className="cb-section-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>{title}{count != null ? ` (${count})` : ''}</span><span aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open && <div className="cb-section-body">{children}</div>}
    </div>
  )
}

export default function ConversationBrain({ leadId, onUseDraft, defaultChannel = 'sms', compact = false }) {
  const [ctx, setCtx] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [channel, setChannel] = useState(defaultChannel)
  const [draft, setDraft] = useState(null)
  const [draftText, setDraftText] = useState('')
  const [note, setNote] = useState('')

  const load = useCallback(async () => {
    setError(null)
    try {
      const c = await api.get(`/conversation-intel/leads/${leadId}`)
      setCtx(c)
      // Draft on a channel we may actually use: no SMS consent means email.
      if (c?.consent && !c.consent.sms && c.consent.email) setChannel('email')
    }
    catch (e) { setError(e?.message || 'Could not load the conversation memory.') }
  }, [leadId])
  useEffect(() => { if (leadId) load() }, [leadId, load])

  async function setMode(mode) {
    setBusy(true)
    try { setCtx(await api.post(`/conversation-intel/leads/${leadId}/mode`, { mode })); setNote('') }
    catch (e) { setNote(e?.message || 'Could not change who is handling this conversation.') }
    finally { setBusy(false) }
  }
  async function compose(style = 'default') {
    setBusy(true); setNote('')
    try {
      const out = await api.post(`/conversation-intel/leads/${leadId}/compose`, { channel, style })
      setDraft(out); setDraftText(out.suggestion || '')
    } catch (e) { setNote(e?.message || 'Could not draft a reply.') }
    finally { setBusy(false) }
  }
  async function recheck() {
    setBusy(true)
    try {
      const q = await api.post(`/conversation-intel/leads/${leadId}/check`, { text: draftText, channel })
      setDraft(d => ({ ...(d || {}), quality: q }))
    } catch (e) { setNote(e?.message || 'Could not check the draft.') }
    finally { setBusy(false) }
  }
  async function resolveItem(id) {
    setBusy(true)
    try { setCtx(await api.post(`/conversation-intel/leads/${leadId}/memory/${id}/resolve`, {})) }
    catch (e) { setNote(e?.message || 'Could not update that item.') }
    finally { setBusy(false) }
  }
  async function createTask() {
    if (!ctx) return
    setBusy(true)
    try {
      const nba = ctx.next_best_action || {}
      const due = ctx.follow_up?.date ? new Date(ctx.follow_up.date + 'T15:00:00Z').toISOString() : null
      await api.post('/work/tasks', { title: `${ACTION_LABEL[nba.action] || 'Follow up'}: ${ctx.lead?.name || 'customer'}`,
        lead_id: leadId, details: nba.reason || null, due_at: due, source: 'manual' })
      setNote('Task created.')
    } catch (e) { setNote(e?.message || 'Could not create the task.') }
    finally { setBusy(false) }
  }

  if (error) return (
    <section className="panel cb-panel" aria-label="Conversation memory">
      <div className="cb-title">🧠 Conversation Brain</div>
      <div className="cb-error" role="alert">{error} <button type="button" className="cb-link" onClick={load}>Retry</button></div>
    </section>
  )
  if (!ctx) return (
    <section className="panel cb-panel" aria-label="Conversation memory" aria-busy="true">
      <div className="cb-title">🧠 Conversation Brain</div>
      <div className="cb-muted">Reading the conversation…</div>
    </section>
  )

  const st = ctx.state || {}
  const nba = ctx.next_best_action || {}
  const activeObjections = (ctx.objections || []).filter(o => o.status === 'active')
  const empty = !ctx.recent_messages?.length

  return (
    <section className="panel cb-panel" aria-label="Conversation memory">
      <div className="cb-head">
        <div className="cb-title">🧠 Conversation Brain</div>
        <div className="cb-chips">
          <Chip>{STATE_LABEL[st.state] || st.state}</Chip>
          <Chip tone={st.mode === 'human_active' ? 'info' : st.mode === 'ai_paused' ? 'warn' : null}>{MODE_LABEL[st.mode] || st.mode}</Chip>
          <Chip tone={HEALTH_TONE[st.health]}>{(st.health || '').replace('_', ' ')}</Chip>
        </div>
      </div>

      {empty ? (
        <div className="cb-muted">No conversation yet. Memory builds itself from replies, messages and emails as they happen.</div>
      ) : (
        <>
          <div className={'cb-nba' + (st.needs_human_reason ? ' cb-nba--human' : '')} data-testid="cb-next-action">
            <div className="cb-nba-label">Next best action</div>
            <div className="cb-nba-action">{ACTION_LABEL[nba.action] || nba.action}</div>
            <div className="cb-nba-reason">{nba.reason}</div>
          </div>

          <div className="cb-actions">
            {st.mode !== 'human_active'
              ? <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={() => setMode('human_active')}>Take over</button>
              : <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={() => setMode('ai_active')}>Resume AI</button>}
            {st.mode !== 'ai_paused' && st.mode !== 'human_active' &&
              <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={() => setMode('ai_paused')}>Pause AI</button>}
            <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={createTask}>Create task</button>
          </div>
          {st.mode_reason && st.mode === 'human_active' && <div className="cb-muted cb-small">{st.mode_reason}</div>}

          {ctx.summary?.what_they_want && (
            <div className="cb-want"><span className="cb-k">What they want</span> {ctx.summary.what_they_want}</div>
          )}

          {ctx.open_questions?.length > 0 && (
            <Section title="Open questions" count={ctx.open_questions.length}>
              {ctx.open_questions.map(q => (
                <div key={q.id} className="cb-item cb-item--q">
                  <div>“{q.value}”</div>
                  <div className="cb-meta">{q.channel} · {when(q.observed_at)}
                    <button type="button" className="cb-link" disabled={busy} onClick={() => resolveItem(q.id)}>Mark answered</button></div>
                </div>
              ))}
            </Section>
          )}

          {ctx.follow_up && (
            <Section title="Follow-up they asked for">
              <div className="cb-item"><strong>{ctx.follow_up.value}</strong>{ctx.follow_up.date ? ` — ${ctx.follow_up.date}` : ''}
                <div className="cb-meta">“{ctx.follow_up.quote}”</div></div>
              {(ctx.follow_up.history || []).map(h => (
                <div key={h.id} className="cb-item cb-item--old"><s>{h.value}{h.date ? ` — ${h.date}` : ''}</s> <span className="cb-meta">changed later</span></div>
              ))}
            </Section>
          )}

          <Section title="Known facts" count={ctx.known_facts?.length || 0}>
            {(ctx.known_facts || []).length === 0 && <div className="cb-muted">Nothing stated yet.</div>}
            {(ctx.known_facts || []).map(f => (
              <div key={f.id} className="cb-item">
                <span className="cb-k">{f.label}</span> {f.value}
                {f.quote && <div className="cb-meta">“{f.quote}” · {f.channel}</div>}
              </div>
            ))}
          </Section>

          {(ctx.inferences || []).length > 0 && (
            <Section title="Inferences — not confirmed" count={ctx.inferences.length} defaultOpen={!compact}>
              {ctx.inferences.map(f => <div key={f.id} className="cb-item cb-item--inf">{f.value}</div>)}
            </Section>
          )}

          {(ctx.unknowns || []).length > 0 && (
            <Section title="Still unknown" count={ctx.unknowns.length} defaultOpen={!compact}>
              {ctx.unknowns.map(u => <div key={u.slot} className="cb-item cb-muted">{u.slot}{u.already_asked ? ' — already asked' : ''}</div>)}
              {ctx.next_best_question && <div className="cb-item"><span className="cb-k">Next best question</span> {ctx.next_best_question}</div>}
            </Section>
          )}

          {(ctx.objections || []).length > 0 && (
            <Section title="Objections" count={activeObjections.length}>
              {ctx.objections.map(o => (
                <div key={o.id} className={'cb-item' + (o.status !== 'active' ? ' cb-item--old' : '')}>
                  <span className="cb-k">{o.value}</span> “{o.quote}”
                  <div className="cb-meta">{when(o.observed_at)} · {o.status === 'active' ? 'unresolved' : 'resolved'}
                    {o.status === 'active' && <button type="button" className="cb-link" disabled={busy} onClick={() => resolveItem(o.id)}>Mark resolved</button>}</div>
                </div>
              ))}
            </Section>
          )}

          {(ctx.commitments || []).length > 0 && (
            <Section title="What we promised" count={ctx.commitments.length} defaultOpen={!compact}>
              {ctx.commitments.map(c => <div key={c.id} className="cb-item">{c.value}</div>)}
            </Section>
          )}

          <Section title="Relationship timeline" count={ctx.timeline?.length || 0} defaultOpen={false}>
            <ol className="cb-timeline">
              {(ctx.timeline || []).slice(-25).map((t, i) => (
                <li key={i}><span className="cb-meta">{when(t.at)}</span> <strong>{t.label}</strong> {t.text}</li>
              ))}
            </ol>
          </Section>

          <div className="cb-composer" data-testid="cb-composer">
            <div className="cb-composer-head">
              <span className="cb-k">Smart Composer</span>
              <label className="cb-small">Channel{' '}
                <select value={channel} onChange={e => setChannel(e.target.value)} aria-label="Draft channel">
                  <option value="sms">Text</option><option value="email">Email</option>
                </select>
              </label>
            </div>
            <div className="cb-actions">
              <button type="button" className="btn btn--primary cb-btn" disabled={busy} onClick={() => compose('default')}>Suggest a reply</button>
              {draft && <>
                <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={() => compose('shorter')}>Shorter</button>
                <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={() => compose('warmer')}>Warmer</button>
                <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={() => compose('more_direct')}>More direct</button>
              </>}
            </div>
            {draft && (
              <>
                <textarea className="cb-draft" value={draftText} aria-label="Suggested reply"
                  onChange={e => setDraftText(e.target.value)} rows={4} />
                <div className="cb-small cb-muted">{draft.note}</div>
                {draft.quality && (draft.quality.passed
                  ? <div className="cb-ok" role="status">✓ Passes the conversation check</div>
                  : <ul className="cb-fails" role="alert">{draft.quality.failures.map((f, i) => <li key={i}>{f.message}</li>)}</ul>)}
                <div className="cb-actions">
                  <button type="button" className="btn btn--secondary cb-btn" disabled={busy} onClick={recheck}>Re-check</button>
                  {onUseDraft && <button type="button" className="btn btn--primary cb-btn" disabled={busy || !draftText.trim()}
                    onClick={() => onUseDraft(draftText, channel)}>Use this draft</button>}
                </div>
              </>
            )}
          </div>
        </>
      )}
      {note && <div className="cb-small cb-muted" role="status">{note}</div>}
    </section>
  )
}
