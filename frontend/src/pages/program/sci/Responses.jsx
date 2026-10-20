/* Responses: one inbox for text and email replies, voicemails and replies from
   unknown senders. Left: the queue. Middle: the real conversation
   (GET /leads/{id}/timeline) and a reply box. Right: who, where, consent and
   the next step. A reply is sent with POST /program/command/reply (as SCI Live
   does), which also marks the family's reply answered; the server's gates (STOP,
   suppression, DNC, consent, the SCI switch) decide - never this page. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { CLASS_LABEL, CLASS_TONE, Chip, Empty, ErrorLine, FilterChips, Icon, Kpi, PageHead, SearchBox, ago, errText, initials, when } from './ui'
import { queueMatches, sortInbox, statusMatches } from './inboxModel'
import OutreachPulse from './OutreachPulse'

const OPTED_OUT_STATUSES = ['dnc', 'opted_out', 'do_not_contact', 'unsubscribed']

export default function Responses({ locationId, onChange, navigate }) {
  const [q, setQ] = useState('')
  const [channel, setChannel] = useState('all')        // all | sms | email | calls | unknown
  const [status, setStatus] = useState('open')
  const [queue, setQueue] = useState('attention')
  const [sortBy, setSortBy] = useState('attention')
  const [total, setTotal] = useState(null)
  const [cls, setCls] = useState('')
  const [rows, setRows] = useState(null)
  const [calls, setCalls] = useState(null)
  const [unknown, setUnknown] = useState([])
  const [sel, setSel] = useState(null)                 // { kind: 'response'|'call'|'unknown', item }
  const [err, setErr] = useState('')
  const seq = useRef(0)

  const load = useCallback(() => {
    const q = new URLSearchParams()
    q.set('limit', '500') // all statuses, server-capped; metrics disclose truncation
    if (locationId) q.set('location_id', locationId)
    const mine = ++seq.current
    api.get(`/program/responses?${q}`)
      .then(d => { if (mine === seq.current) { setRows(d.items || []); setTotal(d.total ?? null); setErr('') } })
      .catch(e => { if (mine === seq.current) { setErr(errText(e)); setRows([]); setTotal(null) } })
  }, [locationId])
  const loadSide = useCallback(() => {
    api.get('/voicemails').then(d => setCalls(Array.isArray(d) ? d : (d.items || d.voicemails || []))).catch(() => setCalls([]))
    api.get('/program/unmatched-replies').then(d => setUnknown(Array.isArray(d) ? d : [])).catch(() => setUnknown([]))
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => { loadSide() }, [loadSide])
  // Poll visible inbox only; do not mark messages as viewed on a refresh.
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === 'visible') { load(); loadSide() } }, 30000)
    return () => clearInterval(t)
  }, [load, loadSide])

  const view = r => {
    setSel({ kind: 'response', item: r })
    api.post(`/program/responses/${r.id}/viewed`, {}).then(() => { load(); onChange() }).catch(() => { /* keep inbox usable on failed viewed receipt */ })
  }
  const mark = async (r, state) => {
    try {
      const d = await api.post(`/program/responses/${r.id}/mark`, { state })
      setSel({ kind: 'response', item: { ...r, status: d?.status || r.status } }); load(); onChange()
    } catch (e) { setErr(errText(e)) }
  }
  const handled = async u => {
    try { await api.post(`/program/unmatched-replies/${u.id}/handled`, {}); setSel(null); loadSide(); onChange() } catch (e) { setErr(errText(e)) }
  }

  const term = q.trim().toLowerCase()
  const hit = (...xs) => !term || xs.some(x => String(x || '').toLowerCase().includes(term))
  const list = sortInbox((rows || []).filter(r =>
    statusMatches(r, status) && queueMatches(r, queue) &&
    (!cls || r.class === cls) && (channel === 'all' || r.channel === channel) &&
    hit(r.name, r.body, r.summary, r.location, r.source_lead_id)), sortBy)
  const callList = (calls || []).filter(c => hit(c.lead_name, c.from, c.transcript))
  const unkList = unknown.filter(u => hit(u.from, u.subject, u.body, u.alias))
  const all = rows || []
  const newCount = all.filter(r => r.status === 'new').length
  const hotCount = all.filter(r => r.class === 'hot' && ['new', 'opened', 'active'].includes(r.status)).length
  const activeCount = all.filter(r => r.status === 'active').length
  const slaCount = all.filter(r => r.sla_breached).length
  const followCount = all.filter(r => ['opened', 'active'].includes(r.status)).length
  const handledCount = all.filter(r => ['responded', 'closed'].includes(r.status)).length
  const queueOptions = [
    ['attention', 'Needs attention', all.filter(r => ['new', 'opened', 'active'].includes(r.status)).length, 'open', 'attention'],
    ['new', 'New / unread', newCount, 'open', 'attention'],
    ['hot', 'HOT', hotCount, 'open', 'attention'],
    ['followup', 'Follow-up', followCount, 'open', 'attention'],
    ['recent', 'All recent', total ?? all.length, '', 'recent'],
    ['handled', 'Handled', handledCount, '', 'recent'],
  ]
  const chooseQueue = (key, st, order) => {
    setQueue(key); setStatus(st); setCls(''); setSortBy(order); setChannel('all'); setSel(null)
  }
  const chips = [
    ['all', 'All', all.length], ['sms', 'Text', all.filter(r => r.channel === 'sms').length],
    ['email', 'Email', all.filter(r => r.channel === 'email').length],
    ['calls', 'Voicemail', calls ? calls.length : null], ['unknown', 'Unknown', unknown.length],
  ]

  return (
    <>
      <PageHead title="Responses" sub="Live family replies, organized by urgency and next action.">
        <Chip tone={newCount ? 'info' : ''}>{newCount} new {newCount === 1 ? 'reply' : 'replies'}</Chip>
        <button type="button" className="sci-btn" onClick={() => { load(); loadSide() }}><Icon name="refresh" />Refresh</button>
        <button type="button" className="sci-btn" onClick={() => { setChannel('unknown'); setSel(null) }}><Icon name="alert" />View unmatched{unknown.length ? ` (${unknown.length})` : ''}</button>
      </PageHead>
      <OutreachPulse locationId={locationId} navigate={navigate} />
      <section className="sci-kpis four" aria-label="Response numbers">
        <Kpi icon="mail" tone="blue" value={rows ? newCount : null} label="New / unread" note="Open latest unhandled replies" onClick={() => chooseQueue('new', 'open', 'attention')} />
        <Kpi icon="alert" tone="red" value={rows ? hotCount : null} label="HOT priority" note="Urgent responses" onClick={() => chooseQueue('hot', 'open', 'attention')} />
        <Kpi icon="responses" tone="purple" value={rows ? followCount : null} label="Needs follow-up" note="Opened or active" onClick={() => chooseQueue('followup', 'open', 'attention')} />
        <Kpi icon="clock" tone="gold" value={rows ? slaCount : null} label="Past response SLA" note="Overdue first" onClick={() => chooseQueue('attention', 'open', 'attention')} />
      </section>
      <ErrorLine text={err} />
      <section className="sci-panel sci-convo" aria-label="Conversations">
        <div className="sci-convo-col">
          <div className="sci-panel-head" style={{ marginBottom: 8 }}>
            <div><h2 className="sci-h2">Action inbox</h2><p className="sci-micro sci-muted">Newest actionable messages first, then follow-ups.</p></div>
            {newCount > 0 && <Chip tone="bad">{newCount} new</Chip>}
          </div>
          <div className="sci-queue-tabs" role="group" aria-label="Inbox queues">
            {queueOptions.map(([key, title, count, st, order]) => (
              <button type="button" key={key} className={queue === key ? 'on' : ''} aria-pressed={queue === key}
                onClick={() => chooseQueue(key, st, order)}>{title}<b>{rows ? count : '—'}</b></button>
            ))}
          </div>
          <div style={{ marginBottom: 8 }}><SearchBox id="sci-r-q" value={q} onChange={setQ} placeholder="Search conversations" label="Search conversations" width="100%" /></div>
          <FilterChips items={chips} value={channel} onChange={k => { setChannel(k); setSel(null) }} label="Channel" />
          {(channel === 'all' || channel === 'sms' || channel === 'email') && (
            <div className="sci-row-gap" style={{ margin: '8px 0 4px', flexWrap: 'nowrap' }}>
              <label className="sci-sr" htmlFor="sci-r-status">Status</label>
              <select id="sci-r-status" className="sci-input sm" style={{ flex: 1, width: 0 }} value={status} onChange={e => setStatus(e.target.value)}>
                <option value="open">Open</option><option value="new">New</option><option value="opened">Opened (follow-up)</option>
                <option value="responded">Responded</option><option value="active">Active</option><option value="closed">Closed</option>
                <option value="">All statuses</option>
              </select>
              <label className="sci-sr" htmlFor="sci-r-class">Class</label>
              <select id="sci-r-class" className="sci-input sm" style={{ flex: 1, width: 0 }} value={cls} onChange={e => setCls(e.target.value)}>
                <option value="">All classes</option>
                {Object.entries(CLASS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
          )}
          <div className="sci-inbox-toolbar">
            <span>{rows ? `${list.length} shown` : 'Loading inbox'}{total !== null && total > all.length ? ` · latest ${all.length} of ${total}` : ''}</span>
            <label htmlFor="sci-inbox-sort">Order</label>
            <select id="sci-inbox-sort" className="sci-input sm" value={sortBy} onChange={e => setSortBy(e.target.value)}>
              <option value="attention">Priority first</option><option value="recent">Newest first</option>
            </select>
          </div>
          <div className="sci-list" aria-label="Messages" style={{ marginTop: 6 }}>
            {channel === 'calls' ? (
              calls === null ? <p className="sci-loading">Loading…</p> : callList.length === 0 ? <Empty icon="voicemail">No voicemails.</Empty> : callList.map(c => (
                <button key={c.id} type="button" className="sci-listitem" aria-current={sel?.kind === 'call' && sel.item.id === c.id}
                  onClick={() => setSel({ kind: 'call', item: c })}>
                  <span className="sci-initials vm"><Icon name="voicemail" /></span>
                  <span className="sci-listitem-body">
                    <b>{c.lead_name || c.from || 'Unknown caller'}</b>
                    <small>{c.transcript ? c.transcript : c.has_recording ? 'Recording saved' : 'Voicemail'}</small>
                    <small>{ago(c.received_at)}{c.duration_seconds ? ` · ${c.duration_seconds}s` : ''}</small>
                  </span>
                </button>
              ))
            ) : channel === 'unknown' ? (
              unkList.length === 0 ? <Empty icon="inbox">No replies from unknown senders.</Empty> : unkList.map(u => (
                <button key={u.id} type="button" className="sci-listitem" aria-current={sel?.kind === 'unknown' && sel.item.id === u.id}
                  onClick={() => setSel({ kind: 'unknown', item: u })}>
                  <span className="sci-initials unk">?</span>
                  <span className="sci-listitem-body">
                    <b>{u.from || 'Unknown sender'}</b>
                    <small>{u.subject || u.body}</small>
                    <small>to {u.alias} · {ago(u.received_at)}</small>
                  </span>
                </button>
              ))
            ) : (
              rows === null ? <p className="sci-loading">Loading…</p> : list.length === 0 ? (
                <Empty icon="responses" title={all.length ? 'Nothing matches.' : 'No replies received yet.'}>
                  {all.length ? 'Try another filter or search.' : 'New conversations will appear here the moment a family replies.'}
                </Empty>
              ) : list.map(r => (
                <button key={r.id} type="button" className={`sci-listitem sci-inbox-item ${r.status === 'new' ? 'unread' : ''} ${r.sla_breached ? 'overdue' : ''}`}
                  aria-current={sel?.kind === 'response' && sel.item.id === r.id} onClick={() => view(r)}>
                  <span className={`sci-initials${r.class === 'hot' ? ' hot' : ''}`}>{initials(r.name)}</span>
                  <span className="sci-listitem-body">
                    <b>{r.name || 'Contact'}{r.status === 'new' && <span className="sci-newdot" aria-label="new" />}</b>
                    <small>{r.body || r.summary}</small>
                    <small>
                      <Chip tone={CLASS_TONE[r.class]} plain>{r.label || CLASS_LABEL[r.class]}</Chip>{' '}
                      {r.channel === 'sms' ? 'Text' : 'Email'} · {ago(r.received_at)}{r.sla_breached && ' · PAST SLA'}
                      {r.status === 'new' && <strong className="sci-inbox-new"> · NEW</strong>}
                      {r.status === 'opened' && ' · Follow-up'}
                    </small>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>

        {!sel ? (
          <div className="sci-convo-col sci-convo-empty" style={{ display: 'grid', placeItems: 'center' }}>
            <Empty icon="responses" title="Every family deserves a timely reply.">Select a conversation to see the full timeline, the family's location, consent and the next step.</Empty>
            <p className="sci-footnote"><Icon name="lock" />STOP and suppression rules always apply.</p>
          </div>
        ) : sel.kind === 'response' ? (
          <ConversationPane r={sel.item} mark={mark} navigate={navigate} onSent={st => {
            if (st) setSel(cur => (cur?.kind === 'response' && cur.item.id === sel.item.id ? { ...cur, item: { ...cur.item, status: st } } : cur))
            load(); onChange()
          }} />
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
  const sendKey = useRef(null)
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
  // Messages and calls in one timeline, oldest first.
  const events = [
    ...(tl?.events || []).filter(e => e && (e.body || e.type)),
    ...(tl?.voice_calls || []).map(c => ({
      type: 'call', channel: 'call', timestamp: c.started_at || c.created_at,
      body: `Call · ${c.status || c.direction || 'voicemail'}`,
    })),
  ].sort((a, b) => (+new Date(a.timestamp) || 0) - (+new Date(b.timestamp) || 0))

  const send = async e => {
    e.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    setBusy(true); setNote({ tone: '', text: '' })
    try {
      if (!sendKey.current) sendKey.current = window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`
      const d = await api.post('/program/command/reply', {
        lead_id: r.lead_id, response_id: r.id, channel: 'sms', text, idempotency_key: sendKey.current,
      })
      sendKey.current = null
      setDraft(''); setNote({ tone: 'ok', text: 'Sent. It appears in the thread once the carrier accepts it.' })
      loadThread(); onSent(d?.response_status)
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
                    <small>{when(ev.timestamp)}{ev.type !== 'call' ? ` · ${ev.channel || ev.type}` : ''}{ev.status ? ` · ${ev.status}` : ''}</small>
                  </div>
                )
              })}
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
        <h2 className="sci-h2" style={{ marginBottom: 4 }}>Family context</h2>
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
        <h3 className="sci-h3" style={{ marginTop: 14 }}>Mark this conversation</h3>
        <div className="sci-seg" role="group" aria-label="Conversation status">
          {[['opened', 'Opened'], ['responded', 'I responded'], ['active', 'Active'], ['closed', 'Closed']].map(([k, label]) => (
            <button key={k} type="button" aria-pressed={r.status === k} onClick={() => mark(r, k)}>{label}</button>
          ))}
        </div>
        {r.lead_id && <button type="button" className="sci-btn primary" style={{ marginTop: 12, width: '100%' }} onClick={() => navigate(`/program?tab=leads&person=${r.lead_id}`)}>Open full record</button>}
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
        {c.lead_id && <button type="button" className="sci-btn primary" style={{ marginTop: 14, width: '100%' }} onClick={() => navigate(`/program?tab=leads&person=${c.lead_id}`)}>Open full record</button>}
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
