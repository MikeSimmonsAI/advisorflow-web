/**
 * TEAM CHAT screens. Every list, message and file comes from /team-app/chat,
 * which re-checks room membership on each call. New messages arrive by a
 * short poll (no new service): the open conversation asks for messages after
 * the newest one it has; the list and the tab badge refresh more slowly.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { badge, bodyPieces, fileSize, fmtListTime, fmtTime, insertMention, LABELS, mentionedIds,
         mentionMatches, mentionQuery, mergeMessages } from './teamHelpers'
import { Avatar, ErrorNote, Icon, Loading, ScreenTitle, useTeam, useTeamApi } from './TeamApp'

const OPTS = { skipRedirect: true, noOrgContext: true }

function roomIcon(room) {
  if (room.kind === 'announcements') return <span className="ta-avatar ta-avatar--icon" aria-hidden><Icon name="megaphone" size={20} /></span>
  if (room.kind === 'room') return <span className="ta-avatar ta-avatar--icon" aria-hidden><Icon name="team" size={20} /></span>
  return <Avatar text={room.initials} />
}

// ── Chat list ───────────────────────────────────────────────────────────────
export function ChatList() {
  const navigate = useNavigate()
  const { tz, me, setUnread } = useTeam()
  const { data, error, loading, reload } = useTeamApi('/team-app/chat/rooms', { pollMs: 5000 })
  useEffect(() => { if (data) setUnread(data.unread_total || 0) }, [data, setUnread])
  if (me && me.access && !me.access.chat) return <div className="ta-screen"><ErrorNote error={{ status: 403, message: 'Chat is not turned on for your account.' }} /></div>
  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.teamChat} action={
        <button type="button" className="ta-icon-btn ta-icon-btn--accent" aria-label="New message" onClick={() => navigate('/app/chat/new')}>
          <Icon name="compose" size={24} /></button>} />
      {loading && !data && <Loading />}
      {error && !data && <ErrorNote error={error} onRetry={reload} />}
      {data && (
        <ul className="ta-list ta-list--chat">
          {data.rooms.map(r => (
            <li key={r.id}>
              <button type="button" className="ta-chatrow" onClick={() => navigate('/app/chat/' + r.id)}>
                {roomIcon(r)}
                <span className="ta-chatrow-main">
                  <span className="ta-chatrow-top">
                    <span className={'ta-chatrow-name' + (r.unread ? ' is-unread' : '')}>{r.name}</span>
                    <span className="ta-chatrow-time">{r.last ? fmtListTime(r.last.at, tz) : ''}</span>
                  </span>
                  <span className="ta-chatrow-bottom">
                    <span className="ta-chatrow-preview">
                      {r.last ? (r.last.mine ? 'You: ' : (r.kind !== 'dm' && r.last.sender_name ? r.last.sender_name.split(' ')[0] + ': ' : '')) + r.last.text
                        : (r.kind === 'announcements' ? 'Company-wide news' : 'No messages yet')}
                    </span>
                    {r.unread > 0 && <span className="ta-dot" aria-label={r.unread + ' unread'}>{r.unread > 1 ? badge(r.unread) : ''}</span>}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── New DM ──────────────────────────────────────────────────────────────────
export function NewChat() {
  const navigate = useNavigate()
  const { data, error, loading, reload } = useTeamApi('/team-app/chat/people')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)
  const people = ((data && data.people) || []).filter(p => !q || p.name.toLowerCase().includes(q.toLowerCase()))
  async function start(p) {
    setBusy(p.id); setErr(null)
    try {
      const room = await api.post('/team-app/chat/dm', { user_id: p.id })
      navigate('/app/chat/' + room.id, { replace: true })
    } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }
  return (
    <div className="ta-screen">
      <div className="ta-subhead"><button type="button" className="ta-icon-btn" onClick={() => navigate('/app/chat')} aria-label="Back"><Icon name="back" /></button><h1 className="ta-title">New message</h1></div>
      <input className="ta-search" type="search" placeholder="Search coworkers" value={q} onChange={e => setQ(e.target.value)} aria-label="Search coworkers" />
      {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
      {loading && <Loading />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {data && people.length === 0 && <div className="ta-state">No coworkers found.</div>}
      <ul className="ta-list">
        {people.map(p => (
          <li key={p.id}><button type="button" className="ta-row" disabled={busy === p.id} onClick={() => start(p)}>
            <Avatar text={p.initials} size={36} /><span className="ta-row-main"><span className="ta-row-name">{p.name}</span></span>
            <Icon name="chev" size={20} /></button></li>
        ))}
      </ul>
    </div>
  )
}

// ── Attachment chip in a message ────────────────────────────────────────────
function Attachment({ a }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  if (a.kind === 'drive_link') {
    return <a className="ta-file" href={a.url} target="_blank" rel="noopener noreferrer"><span className="ta-file-icon ta-file-icon--drive"><Icon name="link" size={18} /></span><span className="ta-file-name">{a.filename}</span></a>
  }
  async function download() {
    setBusy(true); setErr(null)
    try {
      const blob = await api.get(a.download_path, { ...OPTS, asBlob: true })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url; link.download = a.filename || 'file'
      document.body.appendChild(link); link.click(); link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 30000)
    } catch (e) { setErr(e.message || 'Could not download') } finally { setBusy(false) }
  }
  const isPdf = (a.content_type || '').includes('pdf')
  return (
    <button type="button" className="ta-file" onClick={download} disabled={busy}>
      <span className={'ta-file-icon' + (isPdf ? ' ta-file-icon--pdf' : '')}>{isPdf ? 'PDF' : <Icon name="file" size={18} />}</span>
      <span className="ta-file-name">{a.filename}</span>
      <span className="ta-file-size">{busy ? 'Downloading…' : err || fileSize(a.byte_size)}</span>
    </button>
  )
}

// ── Conversation ────────────────────────────────────────────────────────────
export function Conversation() {
  const { roomId } = useParams()
  const navigate = useNavigate()
  const { tz, setUnread } = useTeam()
  const room = useTeamApi('/team-app/chat/rooms/' + roomId)
  const [msgs, setMsgs] = useState(null)
  const [loadErr, setLoadErr] = useState(null)
  const [text, setText] = useState('')
  const [caret, setCaret] = useState(0)
  const [picked, setPicked] = useState([])
  const [replyTo, setReplyTo] = useState(null)
  const [pending, setPending] = useState([])        // uploaded, not yet sent
  const [drive, setDrive] = useState(null)          // null = closed; string = open
  const [selected, setSelected] = useState(null)
  const [sending, setSending] = useState(false)
  const [sendErr, setSendErr] = useState(null)
  const [uploading, setUploading] = useState(false)
  const listRef = useRef(null)
  const inputRef = useRef(null)
  const fileRef = useRef(null)
  const msgsRef = useRef(null)
  msgsRef.current = msgs
  const atBottom = useRef(true)

  // First page, then poll for newer messages.
  useEffect(() => {
    let live = true
    setMsgs(null); setLoadErr(null)
    api.get('/team-app/chat/rooms/' + roomId + '/messages?limit=60', OPTS)
      .then(d => { if (live) setMsgs(d.messages) })
      .catch(e => { if (live) setLoadErr(e) })
    const t = setInterval(() => {
      const cur = msgsRef.current
      if (!cur || document.visibilityState === 'hidden') return
      const last = cur.length ? cur[cur.length - 1].created_at : null
      const q = last ? '?after=' + encodeURIComponent(last) : '?limit=60'
      api.get('/team-app/chat/rooms/' + roomId + '/messages' + q, OPTS)
        .then(d => { if (live && d.messages.length) setMsgs(m => mergeMessages(m, d.messages)) })
        .catch(() => {})
    }, 3000)
    return () => { live = false; clearInterval(t) }
  }, [roomId])

  // Read position: whenever we have messages on screen.
  const newestId = msgs && msgs.length ? msgs[msgs.length - 1].id : null
  useEffect(() => {
    if (!msgs) return
    api.post('/team-app/chat/rooms/' + roomId + '/read', {}).then(d => setUnread(d.unread_total || 0)).catch(() => {})
  }, [roomId, newestId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Stay pinned to the newest message unless the person scrolled up.
  useLayoutEffect(() => {
    const el = listRef.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [msgs])

  // Keep the composer above the on-screen keyboard (iOS Safari resizes the
  // visual viewport, not the layout viewport).
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return undefined
    const root = document.documentElement
    const onResize = () => {
      const kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop)
      root.style.setProperty('--ta-kb', kb + 'px')
      const el = listRef.current
      if (el && atBottom.current) el.scrollTop = el.scrollHeight
    }
    vv.addEventListener('resize', onResize); vv.addEventListener('scroll', onResize)
    onResize()
    return () => { vv.removeEventListener('resize', onResize); vv.removeEventListener('scroll', onResize); root.style.setProperty('--ta-kb', '0px') }
  }, [])

  const info = room.data
  const mentionable = (info && info.mentionable) || []
  const mq = mentionQuery(text, caret)
  const suggestions = mq ? mentionMatches(mentionable, mq.query) : []
  const names = useMemo(() => [...new Set([...(mentionable.map(p => p.name)), ...((msgs || []).map(m => m.sender_name))])], [mentionable, msgs])

  function choose(p) {
    setText(t => insertMention(t, mq, p))
    setPicked(list => list.some(x => x.id === p.id) ? list : [...list, p])
    setTimeout(() => inputRef.current && inputRef.current.focus(), 0)
  }

  async function upload(file) {
    if (!file) return
    setUploading(true); setSendErr(null)
    try {
      const fd = new FormData(); fd.append('file', file)
      const att = await api.upload('/team-app/chat/rooms/' + roomId + '/attachments', fd)
      setPending(p => [...p, att])
    } catch (e) { setSendErr(e.message || 'Upload failed') } finally {
      setUploading(false); if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function send(e) {
    if (e) e.preventDefault()
    const body = text.trim()
    const driveUrl = (drive || '').trim()
    if (!body && !pending.length && !driveUrl) return
    setSending(true); setSendErr(null)
    const key = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : String(Date.now()) + Math.random()
    const payload = { body, client_key: key, reply_to_id: replyTo ? replyTo.id : null,
                      mentions: mentionedIds(body, picked), attachment_ids: pending.map(a => a.id),
                      drive_url: driveUrl || null }
    // One automatic retry with the SAME key: a lost response never posts twice.
    let saved = null; let lastErr = null
    for (let i = 0; i < 2 && !saved; i++) {
      try { saved = await api.post('/team-app/chat/rooms/' + roomId + '/messages', payload) } catch (ex) {
        lastErr = ex
        if (ex.status && ex.status < 500) break
      }
    }
    setSending(false)
    if (!saved) { setSendErr((lastErr && lastErr.message) || 'Not sent. Try again.'); return }
    atBottom.current = true
    setMsgs(m => mergeMessages(m, [saved]))
    setText(''); setPicked([]); setReplyTo(null); setPending([]); setDrive(null)
  }

  if (room.error) {
    return (
      <div className="ta-convo">
        <header className="ta-convo-top"><button type="button" className="ta-icon-btn ta-icon-btn--light" onClick={() => navigate('/app/chat')} aria-label="Back"><Icon name="back" /></button><h1>Chat</h1></header>
        <ErrorNote error={room.error} />
      </div>
    )
  }

  const canPost = info ? info.can_post : false
  return (
    <div className="ta-convo">
      <header className="ta-convo-top">
        <button type="button" className="ta-icon-btn ta-icon-btn--light" onClick={() => navigate('/app/chat')} aria-label="Back"><Icon name="back" /></button>
        <h1>{info ? info.name : ''}</h1>
        {info && info.can_manage
          ? <Link className="ta-convo-manage" to={'/app/chat/' + roomId + '/members'}>Members</Link>
          : info && <Avatar text={info.initials} size={36} />}
      </header>
      <div className="ta-msgs" ref={listRef} onScroll={e => { const el = e.currentTarget; atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80 }}>
        {loadErr && <ErrorNote error={loadErr} />}
        {!msgs && !loadErr && <Loading />}
        {msgs && msgs.length === 0 && <div className="ta-state">No messages yet. Say hello.</div>}
        {msgs && msgs.map((m, i) => {
          const prev = msgs[i - 1]
          const showHead = !m.mine && (!prev || prev.sender_id !== m.sender_id)
          return (
            <div key={m.id} className={'ta-msg' + (m.mine ? ' is-mine' : '') + (m.mentions_me ? ' is-mention' : '')}>
              {!m.mine && <div className="ta-msg-avatar">{showHead ? <Avatar text={m.sender_initials} size={34} /> : null}</div>}
              <div className="ta-msg-col">
                {showHead && <div className="ta-msg-head"><strong>{m.sender_name}</strong><span>{fmtTime(m.created_at, tz)}</span></div>}
                <button type="button" className="ta-bubble" onClick={() => setSelected(s => s === m.id ? null : m.id)}>
                  {m.reply_to && <span className="ta-quote"><strong>{m.reply_to.sender_name}</strong> {m.reply_to.body}</span>}
                  {m.body && <span className="ta-body">{bodyPieces(m.body, names).map((p, k) => p.mention
                    ? <span key={k} className="ta-mention">@{p.mention}</span> : <span key={k}>{p.text}</span>)}</span>}
                </button>
                {m.attachments.map(a => <Attachment key={a.id} a={a} />)}
                {m.mine && <div className="ta-msg-time">{fmtTime(m.created_at, tz)}</div>}
                {selected === m.id && canPost && (
                  <div className="ta-msg-actions">
                    <button type="button" className="ta-chip" onClick={() => { setReplyTo(m); setSelected(null); inputRef.current && inputRef.current.focus() }}>
                      <Icon name="reply" size={16} /> Reply</button>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
      {canPost ? (
        <form className="ta-composer" onSubmit={send}>
          {suggestions.length > 0 && (
            <ul className="ta-suggest" role="listbox" aria-label="Mention someone">
              {suggestions.map(p => <li key={p.id}><button type="button" onMouseDown={e => e.preventDefault()} onClick={() => choose(p)}>@{p.name}</button></li>)}
            </ul>
          )}
          {replyTo && <div className="ta-replying"><span>Replying to <strong>{replyTo.sender_name}</strong>: {(replyTo.body || 'a file').slice(0, 60)}</span>
            <button type="button" className="ta-icon-btn" onClick={() => setReplyTo(null)} aria-label="Cancel reply"><Icon name="x" size={18} /></button></div>}
          {(pending.length > 0 || uploading) && (
            <div className="ta-pending">
              {pending.map(a => <span key={a.id} className="ta-chip">{a.filename}<button type="button" aria-label={'Remove ' + a.filename} onClick={() => setPending(p => p.filter(x => x.id !== a.id))}><Icon name="x" size={14} /></button></span>)}
              {uploading && <span className="ta-chip">Uploading…</span>}
            </div>
          )}
          {drive !== null && (
            <div className="ta-drive">
              <input type="url" inputMode="url" placeholder="Paste a Google Drive link" value={drive} onChange={e => setDrive(e.target.value)} aria-label="Google Drive link" />
              <button type="button" className="ta-icon-btn" onClick={() => setDrive(null)} aria-label="Remove Drive link"><Icon name="x" size={18} /></button>
            </div>
          )}
          {sendErr && <div className="ta-note ta-note--err" role="alert">{sendErr}</div>}
          <div className="ta-composer-row">
            <details className="ta-attach">
              <summary className="ta-icon-btn" aria-label="Attach"><Icon name="clip" /></summary>
              <div className="ta-attach-menu">
                <button type="button" onClick={e => { e.currentTarget.closest('details').open = false; fileRef.current && fileRef.current.click() }}><Icon name="file" size={18} /> File or photo</button>
                <button type="button" onClick={e => { e.currentTarget.closest('details').open = false; setDrive('') }}><Icon name="link" size={18} /> Google Drive link</button>
              </div>
            </details>
            <input ref={fileRef} type="file" hidden onChange={e => upload(e.target.files && e.target.files[0])}
                   accept="image/*,application/pdf,.doc,.docx,.xlsx" />
            <textarea ref={inputRef} className="ta-input" rows={1} placeholder="Message…" value={text} aria-label="Message"
                      onChange={e => { setText(e.target.value); setCaret(e.target.selectionStart) }}
                      onKeyUp={e => setCaret(e.currentTarget.selectionStart)} onClick={e => setCaret(e.currentTarget.selectionStart)}
                      onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && window.matchMedia('(pointer: fine)').matches) { e.preventDefault(); send() } }} />
            <button type="submit" className="ta-send" disabled={sending || uploading || (!text.trim() && !pending.length && !(drive || '').trim())} aria-label="Send"><Icon name="send" /></button>
          </div>
        </form>
      ) : info && (
        <div className="ta-readonly">{info.kind === 'announcements' ? 'Only announcement publishers can post here.' : 'You can read this conversation.'}</div>
      )}
    </div>
  )
}

// ── Room members (chat managers) ────────────────────────────────────────────
export function RoomMembers() {
  const { roomId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const path = '/team-app/chat/rooms/' + roomId + '/members'
  useEffect(() => { api.get(path, OPTS).then(setData).catch(setError) }, [path])
  async function change(id, add) {
    setBusy(id)
    try { setData(add ? await api.post(path, { user_id: id }) : await api.delete(path + '/' + id)) } catch (e) { setError(e) } finally { setBusy(null) }
  }
  return (
    <div className="ta-convo">
      <header className="ta-convo-top">
        <button type="button" className="ta-icon-btn ta-icon-btn--light" onClick={() => navigate('/app/chat/' + roomId)} aria-label="Back"><Icon name="back" /></button>
        <h1>{data ? data.room.name : 'Members'}</h1><span />
      </header>
      <div className="ta-screen ta-screen--scroll">
        {error && <ErrorNote error={error} />}
        {!data && !error && <Loading />}
        {data && <>
          <h2 className="ta-h2">In this room ({data.members.length})</h2>
          <ul className="ta-list">{data.members.map(p => (
            <li key={p.id} className="ta-row ta-row--static"><Avatar text={p.initials} size={34} /><span className="ta-row-main"><span className="ta-row-name">{p.name}</span></span>
              <button type="button" className="ta-btn ta-btn--ghost ta-btn--sm" disabled={busy === p.id} onClick={() => change(p.id, false)}>Remove</button></li>))}
          </ul>
          <h2 className="ta-h2">Add people</h2>
          {data.can_add.length === 0 && <div className="ta-state">Everyone with Chat is already in this room.</div>}
          <ul className="ta-list">{data.can_add.map(p => (
            <li key={p.id} className="ta-row ta-row--static"><Avatar text={p.initials} size={34} /><span className="ta-row-main"><span className="ta-row-name">{p.name}</span></span>
              <button type="button" className="ta-btn ta-btn--primary ta-btn--sm" disabled={busy === p.id} onClick={() => change(p.id, true)}>Add</button></li>))}
          </ul>
        </>}
      </div>
    </div>
  )
}
