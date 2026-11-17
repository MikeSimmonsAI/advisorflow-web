import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { BOARD_URL, errorText } from '../../utils/launchBoard'

export default function ProjectChecklistChat({ projects, open, boardBusy, onReview }) {
  const [scope, setScope] = useState('')
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const log = useRef(null)
  const input = useRef(null)

  useEffect(() => {
    if (open) input.current?.focus()
  }, [open])

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight
  }, [messages, pending, error])

  async function send(event) {
    event.preventDefault()
    const text = message.trim()
    if (!text || pending) return
    const continuation = messages.at(-1)
    const previous = !continuation?.draft && !continuation?.failed && continuation?.scope === scope
      ? continuation?.message : ''
    setPending(true)
    setError('')
    setMessage('')
    setMessages(old => [...old.slice(-19), { message: text, scope }])
    try {
      const result = await api.post(BOARD_URL + '/checklist/chat', {
        message: text,
        previous_message: previous || '',
        project_id: scope ? Number(scope) : null,
      })
      if (!result?.reply) throw new Error('Project chat returned an empty response.')
      setMessages(old => [...old.slice(0, -1), { ...old.at(-1), ...result }])
    } catch (err) {
      setError(errorText(err))
      setMessages(old => [...old.slice(0, -1), { ...old.at(-1), failed: true }])
      setMessage(text)
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="pcc-chat" aria-label="Project Chat">
      <header>
        <h2>Project Chat</h2>
        <label>Scope
          <select value={scope} disabled={pending} onChange={e => setScope(e.target.value)}>
            <option value="">Entire board</option>
            {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
        </label>
      </header>
      <div className="pcc-chat-log" role="log" ref={log} aria-label="Conversation">
        {!messages.length && <p>Try “Add a Wish List card for a referral program” or “What’s left on Restland?”</p>}
        {messages.map((turn, index) => <div key={index} className="pcc-chat-turn">
          <p className="pcc-chat-message" aria-label="You">{turn.message}</p>
          {turn.reply && <div className="pcc-chat-reply">
            <p>{turn.reply}</p>
            {turn.draft && <button type="button" disabled={boardBusy}
                                   onClick={() => onReview(turn.draft)}>Review {turn.draft.id ? 'change' : 'new card'}</button>}
          </div>}
        </div>)}
        {pending && <p role="status">Working on it…</p>}
        {error && <p role="alert">{error}</p>}
      </div>
      <form onSubmit={send}>
        <textarea ref={input} aria-label="Message Project Chat" value={message} maxLength={1500} rows={2}
                  placeholder="Ask about a project or describe a change"
                  disabled={pending} onChange={event => setMessage(event.target.value)} />
        <button className="pcc-primary" disabled={pending || !message.trim()}>Send</button>
      </form>
    </section>
  )
}
