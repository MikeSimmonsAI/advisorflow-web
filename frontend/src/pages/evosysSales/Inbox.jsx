// EvoSys BookIt Inbox (design pages 03-05, 27-31): who needs you, the
// conversation, the AI draft, and Call / Text / Book a call in one place.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import CallPanel from './CallPanel'
import { ReplyTranslation, useReplyTranslation } from './ReplyTranslation'

const API = '/sales/evosys'
const BASE = '/program/evosys-sales'
const CT = { timeZone: 'America/Chicago' }

export function errText(e) { return (typeof e?.detail === 'string' && e.detail) || e?.message || 'Something went wrong. Please try again.' }
export const initials = s => (s || '').replace(/\(.*?\)/g, '').split(/\s+/).filter(w => /[A-Za-z]/.test(w)).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '·'
export const ctTime = iso => iso ? new Date(iso).toLocaleTimeString('en-US', { ...CT, hour: 'numeric', minute: '2-digit' }) : ''
export const ctDay = iso => iso ? new Date(iso).toLocaleDateString('en-US', { ...CT, weekday: 'short', month: 'short', day: 'numeric' }) : ''
export function ctWhen(iso) {
  if (!iso) return ''
  const d = new Date(iso), now = new Date()
  const same = d.toLocaleDateString('en-US', CT) === now.toLocaleDateString('en-US', CT)
  return same ? ctTime(iso) : ctDay(iso)
}
function greeting() {
  const h = Number(new Date().toLocaleString('en-US', { ...CT, hour: 'numeric', hour12: false }))
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

const SENT_BY = { ai: 'Sent by AI', person: 'Sent by Mike', booking: 'Booking confirmation', outside: 'Marked done', system: 'Sent' }
const TAG = {
  pricing: 'Pricing question', question: 'Question', interested: 'Interested', not_interested: 'Not interested',
  wrong_person: 'Wrong person', unclear: 'Unclear', unsubscribe: 'Opted out',
}
function tagsFor(m, why) {
  const t = []
  const b = (m.body || '').toLowerCase()
  if (/website|sitio web/.test(b)) t.push('Asked for website')
  if (/spanish|español|espanol/.test(b)) t.push('Mentions Spanish')
  if (/price|cost|how much|cuánto|precio/.test(b)) t.push('Pricing question')
  if (/availab|schedule|time|horario|disponib/.test(b)) t.push('Scheduling question')
  if (!t.length && why) t.push(why.length > 40 ? why.slice(0, 38) + '…' : why)
  if (!t.length && TAG[m.label]) t.push(TAG[m.label])
  return t.slice(0, 2)
}

export function Toast({ text, onDone }) {
  useEffect(() => { if (!text) return; const t = setTimeout(onDone, 3500); return () => clearTimeout(t) }, [text, onDone])
  return text ? <div className="v2-toast" role="status">{text}</div> : null
}

function Bubble({ m }) {
  const mine = m.direction === 'out'
  if (m.channel === 'call') {
    const st = m.status === 'completed' ? 'Call' : m.status === 'missed' ? 'Missed call' : m.status === 'no-answer' ? 'No answer' : 'Call'
    return <div className="v2-callline">{mine ? '↗' : '↙'} {st}{m.body?.includes('·') ? ' · ' + m.body.split('·').pop().trim() : ''}{m.label ? ' · ' + m.label.replace('_', ' ') : ''} · {ctWhen(m.at)}</div>
  }
  const who = mine ? (m.label === 'ai_answer' ? 'Sent by AI' : m.label === 'booking_confirmation' ? 'Booking confirmation'
    : (m.label || '').startsWith('rd:') || (m.label || '').startsWith('r1:') ? 'Reminder' : m.step ? 'Campaign text ' + m.step : 'Sent by Mike') : null
  return (
    <div className={'v2-bubble-wrap ' + (mine ? 'mine' : 'theirs')}>
      <div className={'v2-bubble ' + (mine ? 'mine' : 'theirs')}>
        {m.translation && !mine ? <>{m.translation}<details className="v2-orig"><summary>Show original</summary>{m.body}</details></> : m.body}
      </div>
      <div className="v2-bubble-meta">{who ? who + ' · ' : ''}{ctWhen(m.at)}{m.status === 'failed' ? ' · did not send' : ''}</div>
    </div>
  )
}

function Draft({ lead, lastIn, saved, onSent, onDone }) {
  const [t, setT] = useState(saved && !saved.outcome ? saved : null)
  const [body, setBody] = useState(saved && !saved.outcome ? (saved.output?.draft || '') : '')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  useEffect(() => { setT(saved && !saved.outcome ? saved : null); setBody(saved && !saved.outcome ? (saved.output?.draft || '') : ''); setEditing(false); setErr(null) }, [saved, lastIn?.id])
  const ask = async again => {
    if (!lastIn) return
    setBusy(true); setErr(null)
    try { const r = await api.post(`${API}/messages/${lastIn.id}/assist${again ? '?again=true' : ''}`, {}); setT(r); setBody(r.output?.draft || '') }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const out = t?.output || {}
  const reply = useReplyTranslation({ leadId: lead.id, message: lastIn, body, spanishDraft: out.draft_english ? out.draft : undefined, englishDraft: out.draft_english })
  const send = async () => {
    setBusy(true); setErr(null)
    try { const outgoing = await reply.prepare(); if (outgoing === null) return; await api.post(`${API}/leads/${lead.id}/send-reply`, { body: outgoing, ai_task_id: t?.id, channel: 'text' }); onSent(`Sent to ${lead.company}`) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const lang = out.draft_english ? 'Spanish' : null
  return (
    <div className="v2-draft">
      <div className="v2-draft-head"><b>{t ? 'AI draft' : 'Your reply'}</b>{lang && <span className="v2-tag">{lang}</span>}</div>
      {!t && lastIn && !body && <button className="v2-btn" disabled={busy} onClick={() => ask(false)}>{busy ? 'Drafting…' : 'Draft a reply'}</button>}
      {editing || !t || !out.draft
        ? <textarea className="v2-input" rows={3} disabled={busy} value={body} onChange={e => setBody(e.target.value)} placeholder="Write a text…" aria-label="Your reply" />
        : <div className="v2-draft-text">{body}</div>}
      {out.draft_english && body === out.draft && <div className="v2-english">English · {out.draft_english}</div>}
      <ReplyTranslation reply={reply} disabled={busy} />
      <div className="v2-row">
        <button className="v2-btn v2-primary" disabled={busy || !body.trim()} onClick={send}>{busy ? 'Working…' : reply.needsPreview ? 'Preview Spanish' : reply.language === 'Spanish' ? 'Send Spanish text' : 'Send'}</button>
        {t && out.draft && <button className="v2-btn" onClick={() => setEditing(!editing)}>{editing ? 'Done editing' : 'Edit'}</button>}
        {lastIn && <button className="v2-btn" disabled={busy} onClick={() => ask(true)}>Rewrite</button>}
        {lastIn && <button className="v2-btn" disabled={busy} onClick={onDone}>Mark done</button>}
      </div>
      {(out.needs_person || []).length > 0 && <div className="v2-why">AI paused for Mike · {out.needs_person[0]}</div>}
      <div className="v2-hint">{body.length} characters · sends from the EvoSys BookIt number</div>
      {err && <p className="v2-err" role="alert">{err}</p>}
    </div>
  )
}

export function Thread({ id, onBack, onChanged, toast }) {
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [calling, setCalling] = useState(false)
  const [dnc, setDnc] = useState(false)
  const load = useCallback(() => api.get(`${API}/leads/${id}`).then(setD).catch(e => setErr(errText(e))), [id])
  useEffect(() => { setD(null); load() }, [load])
  if (err) return <div className="v2-pane"><p className="v2-err">{err}</p></div>
  if (!d) return <div className="v2-pane"><div className="v2-skel" /><div className="v2-skel" /><div className="v2-skel short" /></div>
  const lead = d.lead
  const msgs = (d.messages || []).filter(m => m.channel === 'text' || m.channel === 'call')
  const lastIn = [...msgs].reverse().find(m => m.direction === 'in' && m.channel === 'text')
  const stopped = lead.outreach?.key === 'opted_out' || /do not contact/i.test(lead.outreach?.label || '')
  const markDone = async () => { try { await api.post(`${API}/contacts/${lead.id}/handled`, {}); toast(`${lead.company} marked done`); onChanged() } catch (e) { setErr(errText(e)) } }
  const doDnc = async () => {
    try { await api.post(`/sales/opportunities/${lead.id}/do-not-contact`, { reason: 'Marked Do Not Contact from the inbox' }); setDnc(false); toast(`${lead.company} is on Do Not Contact`); onChanged(); load() }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <div className={'v2-pane v2-thread' + (stopped ? ' v2-readonly' : '')}>
      <div className="v2-thread-head">
        {onBack && <button className="v2-back" onClick={onBack} aria-label="Back to conversations">‹</button>}
        <span className="v2-avatar">{initials(lead.company)}</span>
        <div className="v2-grow">
          <Link to={`${BASE}/leads/${lead.id}`} className="v2-thread-name">{lead.company}</Link>
          <div className="v2-sub">{[lead.contact, msgs.some(m => m.translation) ? 'Translated messages' : null].filter(Boolean).join(' · ') || lead.outreach?.label}</div>
        </div>
        {!stopped && (
          <div className="v2-row v2-thread-acts">
            <button className="v2-btn" onClick={() => nav(`${BASE}/appointments/new?lead=${lead.id}`)}>Book a call</button>
            <button className="v2-btn" disabled={!lead.phone} onClick={() => setCalling(true)}>Call</button>
            <button className="v2-btn" onClick={() => setDnc(true)}>Do Not Contact</button>
          </div>
        )}
      </div>
      {dnc && (
        <div className="v2-confirm" role="alertdialog">
          <b>Put {lead.company} on Do Not Contact?</b> They won't get another text or call from EvoSys BookIt.
          <div className="v2-row"><button className="v2-btn v2-primary" onClick={doDnc}>Yes, Do Not Contact</button><button className="v2-btn" onClick={() => setDnc(false)}>Cancel</button></div>
        </div>
      )}
      <div className="v2-msgs">
        {msgs.length ? msgs.map(m => <Bubble key={m.id} m={m} />) : <p className="v2-muted">No texts with {lead.company} yet.</p>}
      </div>
      {stopped
        ? <div className="v2-stopnote">On Do Not Contact · this conversation is read-only.</div>
        : <Draft lead={lead} lastIn={lastIn} saved={d.reply_ai} onSent={t => { toast(t); onChanged(); load() }} onDone={markDone} />}
      {calling && <div className="v2-modal" onClick={e => e.target === e.currentTarget && setCalling(false)}><CallPanel lead={lead} onClose={() => { setCalling(false); load() }} onLogged={load} /></div>}
    </div>
  )
}

export default function Inbox() {
  const [sp, setSp] = useSearchParams()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [why, setWhy] = useState({})
  const [toastText, setToastText] = useState(null)
  const tab = sp.get('tab') || 'needs'
  const open = sp.get('c')
  const load = useCallback(() => {
    api.get(`${API}/overview`).then(setData).catch(e => setErr(errText(e)))
    api.get(`${API}/attention`).then(a => {
      const w = {}; (a.items || []).filter(i => i.kind === 'reply_needs_person' && i.lead_id).forEach(i => { w[i.lead_id] = w[i.lead_id] || i.text }); setWhy(w)
    }).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])
  const lists = useMemo(() => ({ needs: data?.replies || [], handled: data?.handled || [], opted: data?.opted_out || [] }), [data])
  const rows = lists[tab] || []
  const user = getCurrentUser() || {}
  const first = (user.full_name || 'there').split(' ')[0]
  const next = (data?.appointments || []).find(a => a.status === 'scheduled' && new Date(a.starts_at) > new Date())
  const pick = id => { const p = new URLSearchParams(sp); id ? p.set('c', id) : p.delete('c'); setSp(p) }
  const setTab = t => { const p = new URLSearchParams(sp); p.set('tab', t); p.delete('c'); setSp(p) }

  if (err) return <div className="v2-page"><p className="v2-err">{err}</p></div>
  return (
    <div className="v2-page v2-inbox-page">
      <header className="v2-pagehead">
        <h1>{greeting()}, {first}</h1>
        <p>{data ? `${lists.needs.length} ${lists.needs.length === 1 ? 'person needs' : 'people need'} you` : 'Loading…'}{next ? ` · next call ${ctDay(next.starts_at)} ${ctTime(next.starts_at)} CT` : ''}</p>
      </header>
      <div className={'v2-inbox' + (open ? ' has-open' : '')}>
        <section className="v2-list" aria-label="Conversations">
          <div className="v2-filters" role="tablist">
            {[['needs', 'Needs you'], ['handled', 'Handled'], ['opted', 'Opted out']].map(([k, l]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={'v2-filter' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l} <b>{lists[k].length}</b></button>
            ))}
          </div>
          {!data && [0, 1, 2, 3].map(i => <div key={i} className="v2-skel row" />)}
          {data && !rows.length && (
            <div className="v2-empty">
              <div className="v2-empty-mark">✓</div>
              <b>{tab === 'needs' ? "You're all caught up" : tab === 'handled' ? 'Nothing handled this week yet' : 'Nobody has opted out'}</b>
              <span>{tab === 'needs' ? 'New replies that need you land here.' : ''}</span>
            </div>
          )}
          {rows.map(m => (
            <button key={m.lead.id} className={'v2-conv' + (open === m.lead.id ? ' on' : '') + (tab === 'needs' ? ' unread' : '') + (tab === 'opted' ? ' muted' : '')} onClick={() => pick(m.lead.id)}>
              <span className="v2-avatar">{initials(m.lead.company)}</span>
              <span className="v2-conv-main">
                <span className="v2-conv-top"><b>{m.lead.company}</b><small>{ctWhen(m.at)}</small></span>
                <span className="v2-conv-prev">{tab === 'handled' && m.answer ? (SENT_BY[m.answer.sent_by] || 'Answered') + ': ' + (m.answer.body || '') : (m.translation || m.body)}</span>
                {tab === 'needs' && <span className="v2-tags">{tagsFor(m, why[m.lead.id]).map(t => <span key={t} className="v2-tag">{t}</span>)}</span>}
                {tab === 'opted' && <span className="v2-tags"><span className="v2-tag grey">Opted out</span></span>}
              </span>
              {tab === 'needs' && <span className="v2-dot" aria-label="Needs you" />}
            </button>
          ))}
        </section>
        {open
          ? <Thread id={open} onBack={() => pick(null)} onChanged={load} toast={setToastText} />
          : <div className="v2-pane v2-pane-empty"><p>Pick a conversation.</p></div>}
      </div>
      <Toast text={toastText} onDone={() => setToastText(null)} />
    </div>
  )
}
