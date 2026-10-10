/* Responses: one inbox for text and email replies, voicemails and replies from
   unknown senders. Left: the queue. Middle: the real conversation
   (GET /leads/{id}/timeline) and a reply box. Right: who, where, consent and
   the next step. A reply is sent with POST /sms/send; the server's gates (STOP,
   suppression, DNC, consent, the SCI switch) decide - never this page. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { CLASS_LABEL, CLASS_TONE, Chip, Empty, ErrorLine, PageHead, errText, initials, when } from './ui'

const OPTED_OUT_STATUSES = ['dnc', 'opted_out', 'do_not_contact', 'unsubscribed']

export default function Responses({ locationId, onChange, navigate }) {
  const [channel, setChannel] = useState('all')        // all | sms | email | calls | unknown
  const [status, setStatus] = useState('open')
  const [cls, setCls] = useState('')
  const [rows, setRows] = useState(null)
  const [calls, setCalls] = useState(null)
  const [unknown, setUnknown] = useState([])
  const [sel, setSel] = useState(null)                 // { kind: 'response'|'call'|'unknown', item }
  const [err, setErr] = useState('')
  const seq = useRef(0)

  const load = useCallback(() => {
    const q = new URLSearchParams()
    q.set('status', status)
    if (cls) q.set('response_class', cls)
    if (locationId) q.set('location_id', locationId)
    const mine = ++seq.current
    api.get(`/program/responses?${q}`)
      .then(d => { if (mine === seq.current) { setRows(d.items || []); setErr('') } })
      .catch(e => { if (mine === seq.current) { setErr(errText(e)); setRows([]) } })
  }, [status, cls, locationId])
  const loadSide = useCallback(() => {
    api.get('/voicemails').then(d => setCalls(Array.isArray(d) ? d : (d.items || d.voicemails || []))).catch(() => setCalls([]))
    api.get('/program/unmatched-replies').then(d => setUnknown(Array.isArray(d) ? d : [])).catch(() => setUnknown([]))
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => { loadSide() }, [loadSide])

  const view = r => {
    setSel({ kind: 'response', item: r })
    api.post(`/program/responses/${r.id}/viewed`, {}).catch(() => { /* viewing never fails the screen */ })
  }
  const mark = async (r, state) => {
    try {
      await api.post(`/program/responses/${r.id}/mark`, { state })
      setSel({ kind: 'response', item: { ...r, status: state } }); load(); onChange()
    } catch (e) { setErr(errText(e)) }
  }
  const handled = async u => {
    try { await api.post(`/program/unmatched-replies/${u.id}/handled`, {}); setSel(null); loadSide(); onChange() } catch (e) { setErr(errText(e)) }
  }

  const list = (rows || []).filter(r => channel === 'all' || r.channel === channel)
  const newCount = (rows || []).filter(r => r.status === 'new').length
  const chips = [
    ['all', 'All', (rows || []).length], ['sms', 'Text', (rows || []).filter(r => r.channel === 'sms').length],
    ['email', 'Email', (rows || []).filter(r => r.channel === 'email').length],
    ['calls', 'Calls', calls ? calls.length : null], ['unknown', 'Unknown senders', unknown.length],
  ]

  return (
    <>
      <PageHead title="Conversations" sub="One connected inbox for text replies, email replies and voicemails." />
      <ErrorLine text={err} />
      <section className="sci-panel sci-convo" aria-label="Conversations">
        <div className="sci-convo-col">
          <div className="sci-panel-head" style={{ marginBottom: 10 }}>
            <h2 className="sci-h2">Inbox</h2>
            {newCount > 0 && <Chip tone="bad">{newCount} new</Chip>}
          </div>
          <div className="sci-filterchips" role="group" aria-label="Channel">
            {chips.map(([k, label, n]) => (
              <button key={k} type="button" aria-pressed={channel === k} onClick={() => { setChannel(k); setSel(null) }}>
                {label}{n != null && <span className="n">{n}</span>}
              </button>
            ))}
          </div>
          {(channel === 'all' || channel === 'sms' || channel === 'email') && (
            <div className="sci-row-gap" style={{ margin: '12px 0' }}>
              <label className="sci-sr" htmlFor="sci-r-status">Status</label>
              <select id="sci-r-status" className="sci-input" style={{ flex: 1, minHeight: 36, padding: '6px 10px', fontSize: 13 }} value={status} onChange={e => setStatus(e.target.value)}>
                <option value="open">Open</option><option value="new">New</option><option value="opened">Opened</option>
                <option value="responded">Responded</option><option value="active">Active</option><option value="closed">Closed</option>
                <option value="">All statuses</option>
              </select>
              <label className="sci-sr" htmlFor="sci-r-class">Class</label>
              <select id="sci-r-class" className="sci-input" style={{ flex: 1, minHeight: 36, padding: '6px 10px', fontSize: 13 }} value={cls} onChange={e => setCls(e.target.value)}>
                <option value="">All classes</option>
                {Object.entries(CLASS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
          )}
          <div className="sci-list" aria-label="Messages" style={{ marginTop: 8 }}>
            {channel === 'calls' ? (
              calls === null ? <p className="sci-loading">Loading…</p> : calls.length === 0 ? <Empty>No voicemails.</Empty> : calls.map(c => (
                <button key={c.id} type="button" className="sci-listitem" aria-current={sel?.kind === 'call' && sel.item.id === c.id}
                  onClick={() => setSel({ kind: 'call', item: c })}>
                  <span className="sci-initials">VM</span>
                  <span className="sci-listitem-body">
                    <b>{c.lead_name || c.from || 'Unknown caller'}</b>
                    <small>{c.transcript ? c.transcript : c.has_recording ? 'Recording saved' : 'Voicemail'}</small>
                    <small>{when(c.received_at)}{c.duration_seconds ? ` · ${c.duration_seconds}s` : ''}</small>
                  </span>
                </button>
              ))
            ) : channel === 'unknown' ? (
              unknown.length === 0 ? <Empty>No replies from unknown senders.</Empty> : unknown.map(u => (
                <button key={u.id} type="button" className="sci-listitem" aria-current={sel?.kind === 'unknown' && sel.item.id === u.id}
                  onClick={() => setSel({ kind: 'unknown', item: u })}>
                  <span className="sci-initials">?</span>
                  <span className="sci-listitem-body">
                    <b>{u.from || 'Unknown sender'}</b>
                    <small>{u.subject || u.body}</small>
                    <small>to {u.alias} · {when(u.received_at)}</small>
                  </span>
                </button>
              ))
            ) : (
              rows === null ? <p className="sci-loading">Loading…</p> : list.length === 0 ? <Empty>Nothing here.</Empty> : list.map(r => (
                <button key={r.id} type="button" className="sci-listitem" aria-current={sel?.kind === 'response' && sel.item.id === r.id} onClick={() => view(r)}>
                  <span className={`sci-initials${r.class === 'hot' ? ' hot' : ''}`}>{initials(r.name)}</span>
                  <span className="sci-listitem-body">
                    <b>{r.name || 'Contact'}{r.location ? ` · ${r.location}` : ''}</b>
                    <small>{r.body || r.summary}</small>
                    <small>
                      {when(r.received_at)} · {r.channel === 'sms' ? 'Text' : 'Email'}
                      {r.status === 'new' && ' · new'}{r.sla_breached && ' · past SLA'}
                    </small>
                  </span>
                  <Chip tone={CLASS_TONE[r.class]} plain>{r.label || CLASS_LABEL[r.class]}</Chip>
                </button>
              ))
            )}
          </div>
        </div>

        {!sel ? (
          <div className="sci-convo-col sci-convo-empty" style={{ display: 'grid', placeItems: 'center' }}>
            <Empty title="Select a conversation">The full thread, the contact's location and the next step appear here.</Empty>
          </div>
        ) : sel.kind === 'response' ? (
          <ConversationPane r={sel.item} mark={mark} navigate={navigate} onSent={() => { load(); onChange() }} />
        ) : sel.kind === 'call' ? (
          <CallPane c={sel.item} navigate={navigate} />
        ) : (
          <UnknownPane u={sel.item} handled={handled} />
        )}
      </section>
    </>
  )
}

function ConversationPane({ r, mark, navigate, onSent }) {
  const [tl, setTl] = useState(null)
  const [tlErr, setTlErr] = useState('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState({ tone: '', text: '' })
  const threadRef = useRef(null)
  const loadThread = useCallback(() => {
    if (!r.lead_id) { setTl({ events: [], lead: null }); return }
    api.get(`/leads/${r.lead_id}/timeline`).then(d => { setTl(d); setTlErr('') }).catch(e => { setTlErr(errText(e)); setTl({ events: [], lead: null }) })
  }, [r.lead_id])
  useEffect(() => { setTl(null); setDraft(''); setNote({ tone: '', text: '' }); loadThread() }, [loadThread])
  useEffect(() => { if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight }, [tl])

  const lead = tl?.lead || null
  const leadStatus = String(lead?.status || '').toLowerCase()
  const optedOut = r.class === 'opt_out' || OPTED_OUT_STATUSES.includes(leadStatus) || !!(lead?.opted_out || lead?.sms_opted_out)
  const canText = !!lead?.phone && !optedOut
  const events = [...(tl?.events || [])]
    .filter(e => e && (e.body || e.type))
    .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))

  const send = async e => {
    e.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    setBusy(true); setNote({ tone: '', text: '' })
    try {
      await api.post('/sms/send', { lead_id: r.lead_id, template: text, include_booking_link: false })
      setDraft(''); setNote({ tone: 'ok', text: 'Sent. It appears in the thread once the carrier accepts it.' })
      loadThread(); onSent()
    } catch (er) {
      setNote({ tone: 'bad', text: `Not sent: ${errText(er)}` })
    } finally { setBusy(false) }
  }

  return (
    <>
      <div className="sci-convo-mid sci-convo-col">
        <div className="sci-convo-headbar">
          <div style={{ minWidth: 0 }}>
            <h2 className="sci-h2">{r.name || 'Contact'}{r.location ? ` · ${r.location}` : ''}</h2>
            <div className="sci-micro sci-muted">{r.location || 'Location review'} · {r.channel === 'sms' ? 'Text conversation' : 'Email conversation'}</div>
          </div>
          <div className="sci-row-gap">
            {lead?.is_test && <Chip tone="ok">Test record</Chip>}
            <Chip tone={CLASS_TONE[r.class]}>{r.label || CLASS_LABEL[r.class]}</Chip>
          </div>
        </div>
        <div className="sci-thread" ref={threadRef} aria-live="polite">
          {tl === null ? <p className="sci-loading">Loading conversation…</p> : (
            <>
              {tlErr && <ErrorLine text={tlErr} />}
              {events.length === 0 && (
                <div className="sci-bubble">{r.body}<small>{when(r.received_at)} · {r.channel === 'sms' ? 'text' : 'email'}</small></div>
              )}
              {events.map((ev, i) => {
                const dir = ev.type === 'outbound' ? 'out' : ev.type === 'inbound' ? '' : 'sys'
                return (
                  <div key={i} className={`sci-bubble ${dir}`}>
                    {ev.body || ev.type}
                    <small>{when(ev.timestamp)} · {ev.channel || ev.type}{ev.status ? ` · ${ev.status}` : ''}</small>
                  </div>
                )
              })}
              {(tl?.voice_calls || []).map((c, i) => (
                <div key={`v${i}`} className="sci-bubble sys">Call · {c.status || c.direction || 'voicemail'}<small>{when(c.started_at || c.created_at)}</small></div>
              ))}
            </>
          )}
        </div>
        <form className="sci-composer" onSubmit={send}>
          {note.text && <div className={note.tone === 'ok' ? 'sci-okmsg' : 'sci-alert'} role={note.tone === 'bad' ? 'alert' : 'status'}>{note.text}</div>}
          <label htmlFor="sci-reply" className="sci-sr">Reply by text</label>
          <textarea id="sci-reply" value={draft} onChange={e => setDraft(e.target.value)} disabled={!canText}
            placeholder={optedOut ? 'This contact opted out (STOP). No texts can be sent.' : !lead?.phone ? 'No mobile number on file — reply from the full record.' : 'Write a reply…'} />
          <div className="sci-row-gap" style={{ justifyContent: 'space-between', marginTop: 8 }}>
            <span className="sci-micro sci-muted">{optedOut ? 'STOP is always respected.' : 'Sent as a text. The server checks consent, STOP and do-not-call before anything goes out.'}</span>
            <button type="submit" className="sci-btn primary" disabled={!canText || !draft.trim() || busy}>{busy ? 'Sending…' : 'Send text'}</button>
          </div>
        </form>
      </div>

      <aside className="sci-convo-col sci-convo-ctx" aria-label="Contact context">
        <h2 className="sci-h2" style={{ marginBottom: 8 }}>Contact context</h2>
        <dl className="sci-ctx" style={{ margin: 0 }}>
          <dt>Location</dt><dd>{r.location || 'Location review — nothing is sent until assigned'}</dd>
          <dt>Channel</dt><dd>{r.channel === 'sms' ? 'Inbound text' : 'Inbound email'}{r.reply_to_alias ? ` · to ${r.reply_to_alias}` : ''}</dd>
          <dt>Consent</dt><dd>{optedOut ? <Chip tone="bad">Opted out — STOP</Chip> : <Chip tone="ok">Not opted out</Chip>}</dd>
          <dt>Status</dt>
          <dd>
            {r.status}{r.cadence_paused ? ' · cadence paused' : ''}
            {r.sla_due_at && <span className="sci-micro sci-muted" style={{ display: 'block' }}>Response due {when(r.sla_due_at)}{r.sla_breached ? ' (past SLA)' : ''}</span>}
          </dd>
          <dt>Campaign</dt><dd>{r.campaign_family || 'No campaign'}{r.source_lead_id ? ` · Lead ${r.source_lead_id}` : ''}</dd>
          <dt>Summary</dt><dd>{r.summary || '—'}</dd>
          <dt>Next step</dt><dd>{r.recommended_action || 'Human response'}</dd>
        </dl>
        {r.suggested_reply && (
          <div style={{ marginTop: 14 }}>
            <div className="sci-micro"><b>Suggested reply</b> <span className="sci-muted">— a draft; nothing is sent automatically</span></div>
            <div className="sci-preview-email" style={{ marginTop: 6, fontSize: 13 }}>{r.suggested_reply}</div>
            <div className="sci-row-gap" style={{ marginTop: 8 }}>
              <button type="button" className="sci-btn sm" disabled={!canText} onClick={() => setDraft(r.suggested_reply)}>Use as draft</button>
              <button type="button" className="sci-btn sm" onClick={() => { try { navigator.clipboard.writeText(r.suggested_reply) } catch (e) { /* clipboard unavailable */ } }}>Copy</button>
            </div>
          </div>
        )}
        <div className="sci-row-gap" style={{ marginTop: 16 }}>
          <button type="button" className="sci-btn sm" onClick={() => mark(r, 'opened')}>Mark opened</button>
          <button type="button" className="sci-btn sm" onClick={() => mark(r, 'responded')}>I responded</button>
          <button type="button" className="sci-btn sm" onClick={() => mark(r, 'active')}>Active</button>
          <button type="button" className="sci-btn sm" onClick={() => mark(r, 'closed')}>Close</button>
        </div>
        {r.lead_id && <button type="button" className="sci-btn primary" style={{ marginTop: 12, width: '100%' }} onClick={() => navigate(`/leads/${r.lead_id}`)}>Open full record</button>}
      </aside>
    </>
  )
}

function CallPane({ c, navigate }) {
  return (
    <>
      <div className="sci-convo-mid sci-convo-col">
        <div className="sci-convo-headbar">
          <div><h2 className="sci-h2">{c.lead_name || c.from || 'Unknown caller'}</h2><div className="sci-micro sci-muted">Voicemail · {when(c.received_at)}</div></div>
          {c.task_id && <Chip tone="info">Callback task created</Chip>}
        </div>
        <div className="sci-thread">
          <div className="sci-bubble">{c.transcript || (c.transcript_status ? `Transcript ${c.transcript_status}` : 'No transcript.')}
            <small>{c.duration_seconds ? `${c.duration_seconds} seconds` : ''}{c.has_recording ? ' · recording saved' : ''}</small>
          </div>
        </div>
      </div>
      <aside className="sci-convo-col sci-convo-ctx" aria-label="Caller context">
        <h2 className="sci-h2" style={{ marginBottom: 8 }}>Caller</h2>
        <dl className="sci-ctx" style={{ margin: 0 }}>
          <dt>From</dt><dd>{c.from || '—'}</dd>
          <dt>Matched contact</dt><dd>{c.lead_name || 'Not matched'}</dd>
          <dt>Status</dt><dd>{c.status || '—'}{c.reviewed_at ? ` · reviewed ${when(c.reviewed_at)}` : ''}</dd>
        </dl>
        {c.lead_id && <button type="button" className="sci-btn primary" style={{ marginTop: 14, width: '100%' }} onClick={() => navigate(`/leads/${c.lead_id}`)}>Open full record</button>}
      </aside>
    </>
  )
}

function UnknownPane({ u, handled }) {
  return (
    <>
      <div className="sci-convo-mid sci-convo-col">
        <div className="sci-convo-headbar">
          <div><h2 className="sci-h2">{u.from || 'Unknown sender'}</h2><div className="sci-micro sci-muted">Email to {u.alias} · {when(u.received_at)}</div></div>
          <Chip tone="warn">No matching contact</Chip>
        </div>
        <div className="sci-thread">
          <div className="sci-bubble"><b>{u.subject}</b>{u.subject ? '\n\n' : ''}{u.body}<small>{when(u.received_at)}</small></div>
        </div>
      </div>
      <aside className="sci-convo-col sci-convo-ctx" aria-label="What to do">
        <h2 className="sci-h2" style={{ marginBottom: 8 }}>What to do</h2>
        <p className="sci-small sci-muted">Someone wrote to {u.location || 'a location'}'s address from an email we don't have. Find the family (for example under a spouse's address), answer them, then mark this handled.</p>
        <button type="button" className="sci-btn primary" style={{ width: '100%' }} onClick={() => handled(u)}>Mark handled</button>
      </aside>
    </>
  )
}
