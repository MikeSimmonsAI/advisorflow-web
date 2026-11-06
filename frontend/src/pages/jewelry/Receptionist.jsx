import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useJewelry } from './JewelryShell'
import { CustomerPicker, Empty, ErrorNote, Loading, Modal, Pill, ago, errText, n, useApi, useToast } from './jwui'

const SAMPLES = ['Do you buy gold jewelry?', 'Can I bring in coins?', 'How do I schedule an evaluation?', 'Are you open seven days a week?', 'How much will you pay for my ring?']

export default function Receptionist() {
  const { status } = useJewelry()
  const { data, error, loading, reload } = useApi('/jewelry-ops/ai')
  const [q, setQ] = useState('')
  const [lead, setLead] = useState(null)
  const [chat, setChat] = useState([])
  const [busy, setBusy] = useState(false)
  const [override, setOverride] = useState(null)
  const [err, setErr] = useState(null)
  const sid = useRef(Math.random().toString(36).slice(2, 14))
  const [toast, toastNode] = useToast()
  const ask = async (text) => {
    const question = (text ?? q).trim()
    if (!question) return
    setBusy(true); setErr(null); setQ('')
    setChat(c => [...c, { who: 'you', text: question }])
    try {
      const r = await api.post('/jewelry-ops/ai/ask', { question, session_id: sid.current, lead_id: lead })
      setChat(c => [...c, { who: 'ai', ...r }]); reload()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const controls = async (body, ok) => { try { await api.post('/jewelry-ops/ai/controls', body); reload(); toast(ok) } catch (e) { toast(errText(e), 'err') } }
  return (
    <div>
      <div className="jw-head">
        <div><div className="jw-eyebrow">AI Receptionist</div><h1>Test the assistant</h1>
          <p><b>Test mode:</b> only your team can ask it questions here. It is not connected to any customer phone line, text number or website chat yet.</p></div>
        {data && <div className="jw-row"><Pill tone={data.paused ? 'err' : 'ok'}>{data.paused ? 'Stopped' : data.mode === 'off' ? 'Off' : 'Test mode'}</Pill></div>}
      </div>
      <ErrorNote onRetry={reload}>{error}</ErrorNote>
      {loading && !data && <Loading />}
      {data && (
        <div className="jw-grid jw-grid--main">
          <div className="jw-card">
            <div className="jw-card__head"><h3>Conversation</h3><button className="jw-btn jw-btn--sm" onClick={() => { setChat([]); sid.current = Math.random().toString(36).slice(2, 14) }}>New conversation</button></div>
            <div className="jw-chat" aria-live="polite">
              {chat.length === 0 && <Empty title="Ask what customers ask">Try one of the questions below. The assistant answers only from the business information in Settings, and hands prices, valuations and offers to a person.</Empty>}
              {chat.map((m, i) => m.who === 'you' ? <div key={i} className="jw-msg jw-msg--you">{m.text}</div> : (
                <div key={i} className="jw-msg jw-msg--ai">
                  <small>ASSISTANT · TEST{m.needs_human ? ' · handed to a person' : ''}{m.source ? ' · ' + m.source.replace('_', ' ') : ''}</small>
                  <div>{m.answer}</div>
                  {m.answered_by !== 'operator' && <button className="jw-link" onClick={() => setOverride(m)}>Answer as a person instead</button>}
                </div>
              ))}
              {busy && <div className="jw-msg jw-msg--ai"><span className="jw-spin" /></div>}
            </div>
            <div className="jw-chips jw-chips--wrap">{SAMPLES.map(s => <button key={s} className="jw-chip" disabled={busy} onClick={() => ask(s)}>{s}</button>)}</div>
            <form className="jw-row" onSubmit={e => { e.preventDefault(); ask() }}>
              <input className="jw-input jw-grow" value={q} onChange={e => setQ(e.target.value)} placeholder="Type a customer question…" maxLength={1000} />
              <button className="jw-btn jw-btn--navy" disabled={busy || !q.trim()}>Ask</button>
            </form>
            <details className="jw-details"><summary>Ask as a specific customer (looks up their record)</summary><CustomerPicker value={lead} onChange={setLead} /></details>
            <ErrorNote>{err}</ErrorNote>
          </div>
          <div>
            <div className="jw-card">
              <div className="jw-card__head"><h3>Controls</h3></div>
              <ul className="jw-list">
                <li><span>Questions (last 7 days)</span><b>{n(data.summary.last_7_days)}</b></li>
                <li><span>Handed to a person</span><b>{n(data.summary.handed_to_person)}</b></li>
                <li><span>Answered by staff</span><b>{n(data.summary.operator_answers)}</b></li>
                <li><span>Blocked while stopped</span><b>{n(data.summary.blocked)}</b></li>
              </ul>
              {status.can_manage && (data.paused
                ? <button className="jw-btn jw-btn--gold" onClick={() => controls({ paused: false }, 'AI receptionist restarted (test mode).')}>Restart assistant</button>
                : <button className="jw-btn jw-btn--danger" onClick={() => controls({ paused: true }, 'Emergency stop: the assistant will not answer.')}>Emergency stop</button>)}
              {status.is_owner && <div className="jw-row" style={{ marginTop: 10 }}>
                <span className="jw-small">Mode:</span>
                <div className="jw-seg"><button className={data.mode === 'off' ? 'on' : ''} onClick={() => controls({ mode: 'off' }, 'Assistant turned off.')}>Off</button>
                  <button className={data.mode === 'test' ? 'on' : ''} onClick={() => controls({ mode: 'test' }, 'Assistant in test mode.')}>Test</button>
                  <button disabled title="Live mode needs an approved channel and business information">Live</button></div></div>}
              <p className="jw-note">Live mode isn’t available yet. It needs an approved phone or text channel and owner-approved business information. <Link to={`${BASE}/settings`}>Business information →</Link></p>
            </div>
            <div className="jw-card">
              <div className="jw-card__head"><h3>Recent questions</h3></div>
              {data.history.length === 0 ? <Empty title="No questions yet" /> : (
                <ul className="jw-list">{data.history.slice(0, 12).map(h => <li key={h.id}><div><b>{h.question}</b><small>{h.answered_by}{h.needs_human ? ' · needs a person' : ''} · {ago(h.at)}</small></div></li>)}</ul>
              )}
            </div>
          </div>
        </div>
      )}
      {override && (
        <Modal title="Answer as a person" onClose={() => setOverride(null)}>
          <p className="jw-small"><b>Question:</b> {override.question}</p>
          <form onSubmit={async e => {
            e.preventDefault()
            const text = e.target.answer.value
            try { const r = await api.post(`/jewelry-ops/ai/exchanges/${override.id}/override`, { answer: text }); setChat(c => [...c, { who: 'ai', ...r, source: 'staff answer' }]); setOverride(null); reload() }
            catch (x) { toast(errText(x), 'err') }
          }}>
            <textarea name="answer" className="jw-input" rows={4} maxLength={2000} required />
            <div className="jw-row jw-row--end"><button type="button" className="jw-btn" onClick={() => setOverride(null)}>Cancel</button><button className="jw-btn jw-btn--gold">Use this answer</button></div>
          </form>
        </Modal>
      )}
      {toastNode}
    </div>
  )
}
