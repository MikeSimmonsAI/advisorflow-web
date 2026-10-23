/**
 * Conversations - the priority inbox with AI / human handoff.
 *
 * List: /roofing-ops/inbox (server-ordered: needs you, ready to book, new...).
 * Thread: the shared /communications/thread; sending is the shared
 * /communications/send, which applies every consent / DNC / quiet-hour gate.
 * The AI draft and the inspection offer only FILL the composer - a person
 * reads it and presses Send. Nothing here sends on its own.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { useRoofing } from './RoofingShell'
import { Avatar, Empty, ErrorNote, Icon, Loading, Pill, ago, errText, localWhen, useApi, useToast } from './ui'
import './roofing-conv.css'

const BUCKET_LABEL = {
  needs_human: 'Needs you', ready_to_book: 'Ready to book', new_reply: 'New reply',
  booked: 'Booked', in_progress: 'In progress', opted_out: 'Opted out',
}
const QUAL = { READY_TO_SEND: ['green', 'Ready'], REVIEW_REQUIRED: ['amber', 'Review'], EXCLUDED: ['slate', 'Excluded'] }

function timeOf(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function Conversations() {
  const [params, setParams] = useSearchParams()
  const bucket = params.get('bucket') || ''
  const leadId = params.get('lead') || ''
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 300); return () => clearTimeout(t) }, [q])

  const qs = new URLSearchParams()
  if (bucket) qs.set('bucket', bucket)
  if (query) qs.set('q', query)
  const inbox = useApi(`/roofing-ops/inbox${qs.toString() ? `?${qs}` : ''}`)
  const [toast, say] = useToast()

  const set = (k, v) => {
    const p = new URLSearchParams(params)
    if (v) p.set(k, v); else p.delete(k)
    setParams(p, { replace: k !== 'lead' })
  }

  const items = inbox.data?.items || []
  const current = items.find((i) => i.lead_id === leadId)

  return (
    <div className="rf-page rf-conv-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Conversations</h1>
          <p className="rf-lede">Newest replies and anyone who needs a person come first. Read what the AI said, answer, or take over.</p>
        </div>
      </div>
      <div className={`rf-conv ${leadId ? 'has-lead' : ''}`}>
        <section className="rf-panel rf-conv__list" aria-label="Inbox">
          <div className="rf-conv__listhead">
            <div className="rf-search">
              <Icon name="search" size={18} />
              <input className="rf-input" placeholder="Search name, phone, address" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search conversations" />
            </div>
            <div className="rf-tabs rf-conv__tabs" role="tablist">
              <button className="rf-tab" role="tab" aria-selected={!bucket} onClick={() => set('bucket', '')}>
                All <span className="rf-tab__n">{(inbox.data?.buckets || []).reduce((a, b) => a + b.count, 0)}</span>
              </button>
              {(inbox.data?.buckets || []).map((b) => (
                <button key={b.key} className="rf-tab" role="tab" aria-selected={bucket === b.key} onClick={() => set('bucket', b.key)} title={b.hint}>
                  {b.label} <span className="rf-tab__n">{b.count}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="rf-conv__items">
            {inbox.loading && !inbox.data && <Loading />}
            {inbox.error && <ErrorNote onRetry={inbox.reload}>{inbox.error}</ErrorNote>}
            {inbox.data && !items.length && (
              <Empty icon="inbox" title={query || bucket ? 'Nothing matches' : 'No replies yet'}>
                {query || bucket ? 'Try another filter or search.' : 'When homeowners answer your follow-up, their replies land here - newest and most urgent first.'}
              </Empty>
            )}
            {items.map((it) => (
              <button key={it.lead_id} className={`rf-conv__item ${it.lead_id === leadId ? 'active' : ''} ${it.unread ? 'unread' : ''}`}
                onClick={() => set('lead', it.lead_id)}>
                <Avatar name={it.name} size={38} />
                <span className="rf-conv__itemmain">
                  <span className="rf-conv__itemtop">
                    <b>{it.name}</b>
                    <span className="rf-muted">{ago(it.latest_reply_at)}</span>
                  </span>
                  <span className="rf-conv__snippet">{it.latest_reply || it.needs_human_reason || 'No reply text'}</span>
                  <span className="rf-conv__itemmeta">
                    <Pill tone={it.bucket}>{BUCKET_LABEL[it.bucket] || it.bucket}</Pill>
                    {it.roof_issue && <span className="rf-muted rf-conv__issue">{it.roof_issue}</span>}
                    {it.unread > 0 && <span className="rf-conv__badge" aria-label={`${it.unread} unread`}>{it.unread}</span>}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>

        {leadId ? (
          <Workspace key={leadId} leadId={leadId} listItem={current} onBack={() => set('lead', '')}
            onChanged={() => inbox.reload()} say={say} />
        ) : (
          <section className="rf-panel rf-conv__placeholder">
            <Empty icon="chat" title="Pick a conversation">
              Choose a homeowner on the left to see what the AI said, what they said, and what comes next.
            </Empty>
          </section>
        )}
      </div>
      {toast}
    </div>
  )
}

function Workspace({ leadId, listItem, onBack, onChanged, say }) {
  const { status } = useRoofing()
  const thread = useApi(`/communications/thread/${leadId}`)
  const ws = useApi(`/roofing-ops/leads/${leadId}`)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState('')
  const [offer, setOffer] = useState(null)       // openings response
  const [offerUsed, setOfferUsed] = useState(false)
  const endRef = useRef(null)

  const events = useMemo(() => (thread.data?.events || []).filter((e) => e.type !== 'note'), [thread.data])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [events.length])

  const lead = thread.data?.lead
  const gate = thread.data?.compose
  const optedOut = !!(lead?.is_dnc || ws.data?.lead?.status === 'dnc' || listItem?.bucket === 'opted_out')
  const unreadIds = listItem?.unread_reply_ids || []
  const mode = ws.data?.conversation?.mode
  const human = mode === 'human_active'

  const refresh = () => { thread.reload(); ws.reload(); onChanged() }

  const markRead = async (silent) => {
    if (!unreadIds.length) return
    try {
      await Promise.all(unreadIds.map((id) => api.post(`/communications/replies/${id}/review`, {})))
      if (!silent) say('Marked as read')
      onChanged()
    } catch (e) { say(errText(e), 'err') }
  }

  const send = async () => {
    const body = draft.trim()
    if (!body) return
    setBusy('send')
    try {
      await api.post('/communications/send', { lead_id: leadId, body, reply_id: unreadIds[unreadIds.length - 1] || null })
      if (offerUsed) {
        try {
          await api.post(`/roofing-ops/leads/${leadId}/stage`, { stage: 'inspection_offered', reason: 'Offered inspection times by text' })
        } catch { /* stage is bookkeeping; the text already went */ }
      }
      await markRead(true)
      setDraft(''); setOfferUsed(false); setOffer(null)
      say('Sent')
      refresh()
    } catch (e) {
      say(errText(e), 'err')
    } finally { setBusy('') }
  }

  const aiDraft = async () => {
    setBusy('ai')
    try {
      const r = await api.post(`/conversation-intel/leads/${leadId}/compose`, { channel: 'sms' })
      if (r?.suggestion) { setDraft(r.suggestion); setOfferUsed(false) }
      say(r?.source === 'template' ? 'Draft ready (from template) - review before sending' : 'AI draft ready - review before sending')
    } catch (e) { say(errText(e), 'err') } finally { setBusy('') }
  }

  const offerTimes = async () => {
    setBusy('offer')
    try {
      const r = await api.get(`/roofing-ops/leads/${leadId}/openings?days=7`)
      setOffer(r)
      if (r.availability_status === 'ok' && r.offer_text) { setDraft(r.offer_text); setOfferUsed(true) }
    } catch (e) { say(errText(e), 'err') } finally { setBusy('') }
  }

  const takeover = async (on) => {
    setBusy('mode')
    try {
      const r = await api.post(`/roofing-ops/leads/${leadId}/takeover`, { human: on })
      say(on ? `You took over${r.follow_up === 'paused' ? ' - follow-up paused' : ''}` : `Handed back to AI${r.follow_up === 'active' ? ' - follow-up resumed' : ''}`)
      ws.reload(); onChanged()
    } catch (e) { say(errText(e), 'err') } finally { setBusy('') }
  }

  const gateBlocked = gate && !gate.allowed
  const canType = !optedOut && !gateBlocked

  return (
    <>
      <section className="rf-panel rf-conv__thread" aria-label="Conversation">
        <header className="rf-conv__threadhead">
          <button className="rf-btn rf-btn--ghost rf-btn--sm rf-conv__back" onClick={onBack} aria-label="Back to inbox"><Icon name="back" size={16} /></button>
          <Avatar name={lead?.name || listItem?.name} size={44} />
          <div className="rf-conv__who">
            <b>{lead?.name || listItem?.name || 'Homeowner'}</b>
            <span className="rf-muted">
              {lead?.phone && <><Icon name="phone" size={14} /> {lead.phone}</>}
              {ws.data?.lead?.address && <><Icon name="pin" size={14} /> {ws.data.lead.address}</>}
            </span>
          </div>
          <div className="rf-conv__threadactions">
            {unreadIds.length > 0 && <button className="rf-btn rf-btn--navy rf-btn--sm" onClick={() => markRead(false)}>Mark as read</button>}
            {!optedOut && (human
              ? <button className="rf-btn rf-btn--ghost rf-btn--sm" disabled={busy === 'mode'} onClick={() => takeover(false)}><Icon name="spark" size={16} />Hand back to AI</button>
              : <button className="rf-btn rf-btn--ghost rf-btn--sm" disabled={busy === 'mode'} onClick={() => takeover(true)}><Icon name="hand" size={16} />Take over</button>)}
            {!optedOut && <Link className="rf-btn rf-btn--primary rf-btn--sm" to={`/program/roofing/inspections?lead=${leadId}`}><Icon name="calendar" size={16} />Book inspection</Link>}
          </div>
        </header>
        {human && <div className="rf-conv__modebar"><Icon name="hand" size={16} />You're handling this conversation. The AI and the follow-up sequence are paused for this homeowner.</div>}

        <div className="rf-conv__msgs">
          {thread.loading && !thread.data && <Loading />}
          {thread.error && <ErrorNote onRetry={thread.reload}>{thread.error}</ErrorNote>}
          {thread.data && !events.length && <Empty icon="chat" title="No messages yet">Nothing has been sent to or received from this homeowner.</Empty>}
          {events.map((e) => <Bubble key={`${e.type}-${e.id}`} e={e} />)}
          <div ref={endRef} />
        </div>

        {offer && offer.availability_status !== 'ok' && (
          <div className="rf-note rf-note--warn rf-conv__offernote"><Icon name="calendar" size={16} />{offer.reason || 'No open inspection times found.'}</div>
        )}
        {offer && offer.availability_status === 'ok' && (
          <div className="rf-conv__slots" aria-label="Open inspection times">
            <span className="rf-muted">Real openings for {offer.salesperson_name}:</span>
            {(offer.slots || []).slice(0, 6).map((s) => <span key={s.starts_at} className="rf-conv__slot">{s.label}</span>)}
          </div>
        )}

        <div className="rf-conv__composer">
          {optedOut ? (
            <div className="rf-note rf-note--warn"><Icon name="alert" size={16} />This homeowner opted out. Messaging is off for them - do not contact.</div>
          ) : gateBlocked ? (
            <div className="rf-note rf-note--warn"><Icon name="alert" size={16} />
              <span>Texting isn't available for this homeowner right now: {(gate.reasons || []).map((r) => r.label).join(' ')}</span>
            </div>
          ) : null}
          <div className="rf-conv__compose">
            <textarea className="rf-textarea" rows={3} placeholder={canType ? 'Type a message…' : 'Messaging unavailable'}
              value={draft} disabled={!canType} onChange={(e) => { setDraft(e.target.value); if (!e.target.value) setOfferUsed(false) }}
              aria-label="Message" />
            <div className="rf-conv__composebar">
              <button className="rf-btn rf-btn--ghost rf-btn--sm" disabled={optedOut || busy === 'ai'} onClick={aiDraft}><Icon name="spark" size={16} />{busy === 'ai' ? 'Drafting…' : 'AI draft'}</button>
              <button className="rf-btn rf-btn--ghost rf-btn--sm" disabled={optedOut || busy === 'offer'} onClick={offerTimes}><Icon name="calendar" size={16} />{busy === 'offer' ? 'Checking calendar…' : 'Offer inspection times'}</button>
              <span className="rf-conv__count rf-muted">{draft.length}/1600</span>
              <button className="rf-btn rf-btn--primary rf-btn--sm" disabled={!canType || !draft.trim() || busy === 'send'} onClick={send}>
                <Icon name="send" size={16} />{busy === 'send' ? 'Sending…' : 'Send'}
              </button>
            </div>
          </div>
        </div>
      </section>

      <Details ws={ws} leadId={leadId} fields={status?.fields || []} say={say} onChanged={() => { ws.reload(); onChanged() }} />
    </>
  )
}

function Bubble({ e }) {
  if (e.type === 'call' || e.type === 'voicemail') {
    return (
      <div className="rf-conv__sys"><Icon name="phone" size={14} />
        {e.type === 'voicemail' ? 'Voicemail' : `${e.direction === 'inbound' ? 'Inbound' : 'Outbound'} call`}
        {e.outcome ? ` - ${e.outcome}` : ''}{e.summary || e.body ? `: ${e.summary || e.body}` : ''} <span>{timeOf(e.at)}</span>
      </div>
    )
  }
  const inbound = e.direction === 'inbound'
  const ai = e.actor === 'ai'
  const cls = inbound ? 'in' : ai ? 'ai' : 'team'
  const failed = ['failed', 'blocked', 'undelivered'].includes((e.state || '').toLowerCase())
  return (
    <div className={`rf-msg rf-msg--${cls}`}>
      {!inbound && (ai ? <Avatar ai size={30} /> : <Avatar name={e.sender || 'Team'} size={30} />)}
      <div className="rf-msg__col">
        <div className="rf-msg__bubble">
          {e.channel === 'email' && e.subject && <b className="rf-msg__subj">Email: {e.subject}</b>}
          {e.body || (e.channel === 'email' ? '(email body not shown)' : '')}
        </div>
        <span className="rf-msg__meta">
          {inbound ? (e.classification ? `${e.classification.replace('_', ' ')} · ` : '') : `${ai ? 'AI' : (e.sender || 'Team')} · `}
          {timeOf(e.at)}{failed && <b className="rf-msg__fail"> · {e.state}</b>}
        </span>
      </div>
      {inbound && <Avatar name={e.sender} size={30} />}
    </div>
  )
}

function Details({ ws, leadId, fields, say, onChanged }) {
  const d = ws.data
  const [edit, setEdit] = useState(false)
  const [form, setForm] = useState({})
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => { if (d) setForm({ ...d.roofing }) }, [d])

  if (ws.loading && !d) return <aside className="rf-panel rf-conv__details"><Loading /></aside>
  if (ws.error) return <aside className="rf-panel rf-conv__details"><ErrorNote onRetry={ws.reload}>{ws.error}</ErrorNote></aside>
  if (!d) return null

  const save = async () => {
    setSaving(true)
    try {
      const body = {}
      fields.forEach((f) => {
        let v = form[f.key]
        if (v === '' || v === undefined) v = null
        if (f.type === 'number' && v !== null) v = Number(v)
        body[f.key] = v
      })
      await api.patch(`/roofing-ops/leads/${leadId}`, body)
      say('Roofing details saved'); setEdit(false); onChanged()
    } catch (e) { say(errText(e), 'err') } finally { setSaving(false) }
  }
  const addNote = async () => {
    if (!note.trim()) return
    try { await api.post(`/roofing-ops/leads/${leadId}/notes`, { body: note.trim() }); setNote(''); say('Note added'); ws.reload() } catch (e) { say(errText(e), 'err') }
  }

  const L = d.lead
  return (
    <aside className="rf-panel rf-conv__details" aria-label="Homeowner details">
      <div className="rf-conv__next">
        <span className="rf-muted">Next action</span>
        <b>{d.next_action?.label}</b>
        <Pill tone="blue">{d.opportunity?.stage_label}</Pill>
      </div>

      <div className="rf-conv__sect">
        <div className="rf-conv__secthead"><h3>Homeowner</h3><Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`/program/roofing/homeowners/${leadId}`}>Open record</Link></div>
        <dl className="rf-dl">
          <dt>Phone</dt><dd>{L.phone || '—'}</dd>
          <dt>Email</dt><dd>{L.email || '—'}</dd>
          <dt>Address</dt><dd>{L.address || '—'}</dd>
          <dt>Source</dt><dd>{L.source_detail || L.source || '—'}</dd>
          <dt>Owner</dt><dd>{L.owner_name || 'Unassigned'}</dd>
          {L.is_test && <><dt>Record</dt><dd><Pill tone="amber">Test record</Pill></dd></>}
        </dl>
      </div>

      <div className="rf-conv__sect">
        <div className="rf-conv__secthead">
          <h3>Roof</h3>
          {!edit ? <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => setEdit(true)}>Edit</button>
            : <span style={{ display: 'flex', gap: 6 }}>
              <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => { setEdit(false); setForm({ ...d.roofing }) }}>Cancel</button>
              <button className="rf-btn rf-btn--primary rf-btn--sm" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
            </span>}
        </div>
        {!edit ? (
          <dl className="rf-dl">
            {fields.map((f) => (
              <span key={f.key} style={{ display: 'contents' }}>
                <dt>{f.label}</dt>
                <dd>{d.roofing?.[f.key] !== null && d.roofing?.[f.key] !== undefined && d.roofing?.[f.key] !== ''
                  ? String(d.roofing[f.key]).replace('_', ' ') : <span className="rf-muted">—</span>}</dd>
              </span>
            ))}
          </dl>
        ) : (
          <div className="rf-conv__form">
            {fields.map((f) => (
              <label key={f.key} className="rf-label">{f.label}
                {f.options ? (
                  <select className="rf-select" value={form[f.key] ?? ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                    <option value="">—</option>
                    {f.options.map((o) => <option key={o} value={o}>{o.replace('_', ' ')}</option>)}
                  </select>
                ) : (
                  <input className="rf-input" type={f.type === 'number' ? 'number' : 'text'} value={form[f.key] ?? ''}
                    onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
                )}
              </label>
            ))}
          </div>
        )}
      </div>

      {d.inspection && (
        <div className="rf-conv__sect">
          <div className="rf-conv__secthead"><h3>Inspection</h3></div>
          <p className="rf-conv__insp"><Icon name="calendar" size={16} /><b>{localWhen(d.inspection.at_local)}</b></p>
          <p className="rf-muted">{d.inspection.label} with {d.inspection.salesperson_name || 'team'} · {d.inspection.status}</p>
        </div>
      )}

      <div className="rf-conv__sect">
        <div className="rf-conv__secthead"><h3>AI follow-up</h3></div>
        {d.follow_up ? (
          <p className="rf-muted">Sequence {d.follow_up.status} · touch {d.follow_up.touch} sent
            {d.follow_up.next_due_at && d.follow_up.status === 'active' ? ` · next ${timeOf(d.follow_up.next_due_at)}` : ''}</p>
        ) : <p className="rf-muted">Not in a follow-up sequence. {d.touch_count ? `${d.touch_count} message(s) sent.` : ''}</p>}
        {d.conversation && <p className="rf-muted">Mode: {d.conversation.mode?.replace('_', ' ')}{d.conversation.needs_human_reason ? ` - ${d.conversation.needs_human_reason}` : ''}</p>}
        {d.touches?.length > 0 && (
          <ul className="rf-conv__touches">
            {d.touches.slice(0, 6).map((t, i) => (
              <li key={i}><span className={`rf-conv__dot rf-conv__dot--${t.outcome}`} />Touch {t.touch} · {t.channel} · {t.outcome}
                <span className="rf-muted"> {timeOf(t.at)}</span></li>
            ))}
          </ul>
        )}
      </div>

      <div className="rf-conv__sect">
        <div className="rf-conv__secthead"><h3>Can we contact them?</h3></div>
        {Object.entries(d.qualification || {}).map(([ch, q]) => {
          const [tone, label] = QUAL[q.bucket] || ['slate', q.bucket]
          return (
            <div key={ch} className="rf-conv__qual">
              <span className="rf-conv__qualch">{ch === 'sms' ? 'Text' : 'Email'}</span>
              <Pill tone={tone}>{label}</Pill>
              {q.reasons?.[0] && <span className="rf-muted">{q.reasons[0].detail || q.reasons[0].label || q.reasons[0].code}</span>}
            </div>
          )
        })}
      </div>

      <div className="rf-conv__sect">
        <div className="rf-conv__secthead"><h3>Notes</h3></div>
        <div className="rf-conv__noteadd">
          <input className="rf-input" placeholder="Add a note" value={note} onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addNote() }} aria-label="New note" />
          <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={addNote} disabled={!note.trim()}>Add</button>
        </div>
        {(d.notes || []).slice(0, 8).map((nt) => (
          <div key={nt.id} className="rf-conv__note"><p>{nt.body}</p><span className="rf-muted">{nt.author || 'Team'} · {ago(nt.at)}</span></div>
        ))}
      </div>
    </aside>
  )
}
