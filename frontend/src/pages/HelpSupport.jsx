/*
 * HELP & SUPPORT — the customer's whole support experience, wearing their
 * brand's name.
 *
 * WHERE THE NAMES COME FROM. Nothing here hardcodes "Ask Evo" or any brand
 * string. GET /support/me returns assistant_name, help_center_name and
 * support_display_name from the brand's own configuration, and every label on
 * this page is rendered from that. A new brand needs no change to this file.
 *
 * NO DEAD BUTTONS. Every control on this page calls an endpoint that exists
 * and renders what comes back. Where an action is not available — a closed
 * request cannot be replied to, a repair needs approval — the control is
 * absent or disabled with the reason shown, rather than present and lying.
 *
 * WHAT IT DELIBERATELY NEVER SHOWS. Stack traces, internal notes, provider
 * error strings, other organizations, or the platform underneath. All of that
 * is filtered on the SERVER (support_tickets.customer_view,
 * CheckResult.customer_view) — this page could not render it if it tried,
 * which is the point.
 */
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'
import '../styles/shared.css'
import './HelpSupport.css'

const TABS = [
  { key: 'ask', label: 'Ask' },
  { key: 'tickets', label: 'My requests' },
  { key: 'knowledge', label: 'Help centre' },
  { key: 'status', label: 'System status' },
  { key: 'plan', label: 'Support plan' },
]

const SEVERITY_CLASS = {
  healthy: 'badge--green',
  attention: 'badge--amber',
  action_required: 'badge--red',
  unavailable: 'badge--neutral',
  no_data: 'badge--neutral',
}

const SLA_CLASS = {
  within: 'badge--green',
  met: 'badge--green',
  at_risk: 'badge--amber',
  breached: 'badge--red',
  paused: 'badge--blue',
  not_applicable: 'badge--neutral',
}

const STATUS_CLASS = {
  ai_triage: 'badge--blue',
  new: 'badge--blue',
  assigned: 'badge--blue',
  in_progress: 'badge--blue',
  waiting_on_customer: 'badge--amber',
  resolved: 'badge--green',
  closed: 'badge--neutral',
}

function when(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

export default function HelpSupport() {
  const toast = useToast()
  const [tab, setTab] = useState('ask')
  const [home, setHome] = useState(null)

  useEffect(() => {
    api.get('/support/me')
      .then(setHome)
      .catch((e) => toast.error(e.detail || 'Could not load Help & Support.'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const assistant = home?.assistant_name || 'Support'

  return (
    <div className="help-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{home?.help_center_name || 'Help & Support'}</h1>
          <p className="page-subtitle">
            {home
              ? `${assistant} can look at your account and fix a lot of things on the spot.`
              : 'Loading…'}
          </p>
        </div>
        {home?.open_tickets > 0 && (
          <span className="badge badge--blue">
            {home.open_tickets} open request{home.open_tickets === 1 ? '' : 's'}
          </span>
        )}
      </div>

      <div className="pill-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`pill-tab ${tab === t.key ? 'pill-tab--active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'ask' && <AskPanel home={home} onRaised={() => setTab('tickets')} />}
      {tab === 'tickets' && <TicketsPanel />}
      {tab === 'knowledge' && <KnowledgePanel />}
      {tab === 'status' && <StatusPanel />}
      {tab === 'plan' && <PlanPanel />}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────
 * ASK [BRAND]
 * ───────────────────────────────────────────────────────────────────────── */

function AskPanel({ home, onRaised }) {
  const toast = useToast()
  const [turns, setTurns] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [conversationId, setConversationId] = useState(null)
  const [last, setLast] = useState(null)

  const assistant = home?.assistant_name || 'Support'

  async function send(e) {
    e.preventDefault()
    const message = draft.trim()
    if (!message || busy) return
    setDraft('')
    setTurns((t) => [...t, { role: 'user', content: message }])
    setBusy(true)
    try {
      const result = await api.post('/support/ask', {
        message,
        conversation_id: conversationId,
      })
      setConversationId(result.conversation_id)
      setLast(result)
      setTurns((t) => [...t, { role: 'assistant', content: result.reply, meta: result }])
    } catch (err) {
      toast.error(err.detail || 'Could not reach support just now.')
      setTurns((t) => [...t, {
        role: 'assistant',
        content: "I couldn't reach your account details just then. You can still "
          + 'raise a request and a person will pick it up.',
      }])
    } finally {
      setBusy(false)
    }
  }

  async function raiseTicket() {
    const suggested = last?.suggested_ticket
    if (!suggested) return
    try {
      await api.post('/support/tickets', {
        subject: suggested.subject,
        body: turns.filter((t) => t.role === 'user').map((t) => t.content).join('\n\n'),
        category: suggested.category,
        conversation_id: conversationId,
        diagnostic_run_id: suggested.diagnostic_run_id,
      })
      toast.success('Request raised — everything we checked is attached to it.')
      onRaised()
    } catch (err) {
      toast.error(err.detail || 'Could not raise the request.')
    }
  }

  return (
    <div className="help-grid">
      <div className="panel help-chat">
        <div className="panel-header">
          <span className="panel-title">{assistant}</span>
          {home && !home.ai_available && (
            <span className="badge badge--neutral" title="Answers come from your
              account's live diagnostics rather than from the assistant.">
              Diagnostics only
            </span>
          )}
        </div>

        <div className="help-chat__log">
          {turns.length === 0 && (
            <div className="help-bubble help-bubble--assistant">
              {home?.greeting || 'Tell me what is happening and I will take a look.'}
            </div>
          )}
          {turns.map((turn, i) => (
            <div
              key={i}
              className={`help-bubble help-bubble--${turn.role === 'user' ? 'user' : 'assistant'}`}
            >
              {turn.content.split('\n\n').map((para, j) => <p key={j}>{para}</p>)}
              {turn.meta && <TurnEvidence meta={turn.meta} />}
            </div>
          ))}
          {busy && <div className="help-bubble help-bubble--assistant">Checking your account…</div>}
        </div>

        <form className="help-chat__composer" onSubmit={send}>
          <input
            className="help-input"
            placeholder={`Ask ${assistant}…`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy}
          />
          <button className="btn btn--primary" type="submit" disabled={busy || !draft.trim()}>
            Send
          </button>
        </form>
      </div>

      <div className="panel">
        <div className="panel-header"><span className="panel-title">What I checked</span></div>
        {!last && <div className="empty-state">Nothing checked yet.</div>}
        {last && (
          <div className="help-checks">
            {last.diagnostics.map((d) => (
              <div key={d.key} className="help-check">
                <span className={`badge ${SEVERITY_CLASS[d.severity] || 'badge--neutral'}`}>
                  {d.severity_label}
                </span>
                <div>
                  <div className="help-check__label">{d.label}</div>
                  <div className="help-check__headline">{d.headline}</div>
                </div>
              </div>
            ))}
            {last.escalation_recommended && (
              <button type="button" className="btn btn--primary help-raise" onClick={raiseTicket}>
                Raise this with a person
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function TurnEvidence({ meta }) {
  const repairs = meta.repairs || []
  const articles = meta.articles || []
  if (!repairs.length && !articles.length) return null
  return (
    <div className="help-evidence">
      {repairs.map((r) => (
        <div key={r.id} className="help-evidence__row">
          <span className={`badge ${r.fixed ? 'badge--green' : 'badge--amber'}`}>
            {/* `fixed` is the VERIFIED flag, never the status. A repair that
                ran and was not verified is reported as still being checked. */}
            {r.fixed ? 'Fixed' : 'Checking'}
          </span>
          <span>{r.explanation || r.message}</span>
        </div>
      ))}
      {articles.map((a) => (
        <div key={a.id} className="help-evidence__row">
          <span className="badge badge--neutral">Help centre</span>
          <span>{a.title}</span>
        </div>
      ))}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────
 * TICKETS
 * ───────────────────────────────────────────────────────────────────────── */

function TicketsPanel() {
  const toast = useToast()
  const [tickets, setTickets] = useState(null)
  const [open, setOpen] = useState(null)
  const [creating, setCreating] = useState(false)

  async function load() {
    try {
      const data = await api.get('/support/tickets')
      setTickets(data.tickets)
    } catch (e) {
      toast.error(e.detail || 'Could not load your requests.')
      setTickets([])
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (open) {
    return <TicketDetail ticketId={open} onBack={() => { setOpen(null); load() }} />
  }
  if (creating) {
    return <CreateTicket onDone={() => { setCreating(false); load() }} onCancel={() => setCreating(false)} />
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">My requests</span>
        <button className="btn btn--primary btn--sm" type="button" onClick={() => setCreating(true)}>
          New request
        </button>
      </div>
      {tickets === null && <div className="empty-state">Loading…</div>}
      {tickets && tickets.length === 0 && (
        <div className="empty-state">You haven&apos;t raised any requests.</div>
      )}
      {tickets && tickets.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Reference</th><th>Subject</th><th>Status</th><th>Priority</th>
              <th>Response</th><th>Raised</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((t) => (
              <tr key={t.id} className="help-row" onClick={() => setOpen(t.id)}>
                <td data-label="Reference"><span className="mono">{t.ticket_number}</span></td>
                <td data-label="Subject">{t.subject}</td>
                <td data-label="Status">
                  <span className={`badge ${STATUS_CLASS[t.status] || 'badge--neutral'}`}>
                    {t.status_label}
                  </span>
                </td>
                <td data-label="Priority">{t.severity_label}</td>
                <td data-label="Response">
                  <span className={`badge ${SLA_CLASS[t.sla_state] || 'badge--neutral'}`}>
                    {t.sla_label}
                  </span>
                </td>
                <td data-label="Raised">{when(t.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function CreateTicket({ onDone, onCancel }) {
  const toast = useToast()
  const [form, setForm] = useState({
    subject: '', body: '', category: 'technical_product_support', urgency: '',
  })
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    try {
      await api.post('/support/tickets', {
        subject: form.subject,
        body: form.body,
        category: form.category,
        urgency: form.urgency || null,
      })
      toast.success('Request raised. We ran the checks for you and attached them.')
      onDone()
    } catch (err) {
      toast.error(err.detail || 'Could not raise the request.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="panel help-form" onSubmit={submit}>
      <div className="panel-header"><span className="panel-title">New request</span></div>

      <label className="help-label">
        What is this about?
        <select
          className="help-input"
          value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value })}
        >
          <option value="technical_product_support">Something isn&apos;t working</option>
          <option value="customer_assistance">Help using the product</option>
          <option value="professional_services">Work you&apos;d like us to do</option>
        </select>
      </label>

      <label className="help-label">
        Summary
        <input
          className="help-input"
          required
          maxLength={300}
          value={form.subject}
          onChange={(e) => setForm({ ...form, subject: e.target.value })}
        />
      </label>

      <label className="help-label">
        What happened?
        <textarea
          className="help-input help-textarea"
          required
          rows={6}
          value={form.body}
          onChange={(e) => setForm({ ...form, body: e.target.value })}
        />
      </label>

      <label className="help-label">
        How urgent does this feel?
        <select
          className="help-input"
          value={form.urgency}
          onChange={(e) => setForm({ ...form, urgency: e.target.value })}
        >
          <option value="">Not sure</option>
          <option value="P1">We&apos;re stopped — nothing works</option>
          <option value="P2">A big part of our day is affected</option>
          <option value="P3">Annoying, but we can work around it</option>
          <option value="P4">A question or a request</option>
        </select>
        {/* Said plainly, because a field whose value is quietly ignored is
            worse than no field: we use it, and we also look for ourselves. */}
        <span className="help-hint">
          We use this alongside what our own checks find, so you never have to
          argue about how serious something is.
        </span>
      </label>

      <div className="help-actions">
        <button className="btn btn--primary" type="submit" disabled={busy}>
          {busy ? 'Raising…' : 'Raise request'}
        </button>
        <button className="btn btn--secondary" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

function TicketDetail({ ticketId, onBack }) {
  const toast = useToast()
  const [ticket, setTicket] = useState(null)
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setTicket(await api.get(`/support/tickets/${ticketId}`))
    } catch (e) {
      toast.error(e.detail || 'Could not load that request.')
    }
  }

  useEffect(() => { load() }, [ticketId]) // eslint-disable-line react-hooks/exhaustive-deps

  async function act(fn, successMessage) {
    setBusy(true)
    try {
      setTicket(await fn())
      if (successMessage) toast.success(successMessage)
    } catch (err) {
      toast.error(err.detail || 'That did not work.')
    } finally {
      setBusy(false)
    }
  }

  async function upload(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const data = new FormData()
    data.append('file', file)
    try {
      await api.upload(`/support/tickets/${ticketId}/attachments`, data)
      toast.success('Attached.')
      load()
    } catch (err) {
      toast.error(err.detail || 'Could not attach that file.')
    }
  }

  if (!ticket) return <div className="panel"><div className="empty-state">Loading…</div></div>

  return (
    <div className="panel help-detail">
      <div className="panel-header">
        <button className="btn btn--sm btn--secondary" type="button" onClick={onBack}>
          ← All requests
        </button>
        <span className="panel-title">{ticket.subject}</span>
      </div>

      <div className="help-detail__facts">
        <Fact label="Reference" value={ticket.ticket_number} mono />
        <Fact label="Status" value={ticket.status_label} />
        <Fact label="Priority" value={ticket.severity_label} />
        <Fact label="Support plan" value={ticket.support_plan || '—'} />
        <Fact
          label="First response"
          value={ticket.first_response_at
            ? when(ticket.first_response_at)
            : `Target ${when(ticket.first_response_due_at)}`}
        />
      </div>

      <div className={`help-sla badge ${SLA_CLASS[ticket.sla.state] || 'badge--neutral'}`}>
        {ticket.sla.label} — {ticket.sla.reason}
      </div>

      <div className="help-thread">
        {ticket.messages.map((m) => (
          <div key={m.id} className={`help-msg help-msg--${m.author_kind}`}>
            <div className="help-msg__who">{m.author} · {when(m.created_at)}</div>
            <div className="help-msg__body">{m.body}</div>
          </div>
        ))}
      </div>

      {ticket.attachments.length > 0 && (
        /* CHIPS, NOT LINKS, AND DELIBERATELY SO.
         *
         * The download endpoint is authenticated, so a plain <a href> would
         * send no Authorization header and 401 — a link that looks like a
         * link and never works. Until there is a signed one-time URL, these
         * are a receipt that the file reached us, which is the thing the
         * customer actually needs to know. A dead link would be worse than
         * no link. */
        <div className="help-attachments">
          <span className="help-fact__label">Attached</span>
          {ticket.attachments.map((a) => (
            <span key={a.id} className="badge badge--neutral"
                  title={`${Math.max(Math.round(a.size / 1024), 1)} KB`}>
              {a.filename}
            </span>
          ))}
        </div>
      )}

      {ticket.can_reply && (
        <div className="help-reply">
          <textarea
            className="help-input help-textarea"
            rows={3}
            placeholder="Add a reply…"
            value={reply}
            onChange={(e) => setReply(e.target.value)}
          />
          <div className="help-actions">
            <button
              className="btn btn--primary"
              type="button"
              disabled={busy || !reply.trim()}
              onClick={() => act(
                () => api.post(`/support/tickets/${ticketId}/reply`, { body: reply })
                  .then((t) => { setReply(''); return t }),
                'Reply sent.',
              )}
            >
              Send reply
            </button>
            <label className="btn btn--secondary help-file">
              Attach a file
              <input type="file" onChange={upload} hidden />
            </label>
          </div>
        </div>
      )}

      <div className="help-actions">
        {ticket.can_reopen && (
          <button
            className="btn btn--secondary"
            type="button"
            disabled={busy}
            onClick={() => act(
              () => api.post(`/support/tickets/${ticketId}/reopen`, { reason: reply || null }),
              'Reopened.',
            )}
          >
            Reopen
          </button>
        )}
        {ticket.status !== 'closed' && (
          <button
            className="btn btn--secondary"
            type="button"
            disabled={busy}
            onClick={() => act(
              () => api.post(`/support/tickets/${ticketId}/close`, {}),
              'Closed. Thanks for letting us know.',
            )}
          >
            This is sorted — close it
          </button>
        )}
      </div>
    </div>
  )
}

function Fact({ label, value, mono }) {
  return (
    <div className="help-fact">
      <span className="help-fact__label">{label}</span>
      <span className={`help-fact__value ${mono ? 'mono' : ''}`}>{value}</span>
    </div>
  )
}


/* ─────────────────────────────────────────────────────────────────────────
 * HELP CENTRE
 * ───────────────────────────────────────────────────────────────────────── */

function KnowledgePanel() {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [articles, setArticles] = useState(null)
  const [open, setOpen] = useState(null)

  async function load(q) {
    try {
      const data = await api.get('/support/knowledge', { params: { q: q || undefined } })
      setArticles(data.articles)
    } catch (e) {
      toast.error(e.detail || 'Could not load the help centre.')
      setArticles([])
    }
  }

  useEffect(() => { load('') }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function openArticle(slug) {
    try {
      setOpen(await api.get(`/support/knowledge/${slug}`))
    } catch (e) {
      toast.error(e.detail || 'Could not open that article.')
    }
  }

  async function rate(helpful) {
    if (!open) return
    try {
      await api.post(`/support/knowledge/${open.slug}/feedback`, { helpful })
      toast.success('Thanks — that tells us what to fix.')
    } catch (e) {
      toast.error(e.detail || 'Could not record that.')
    }
  }

  if (open) {
    return (
      <div className="panel help-article">
        <div className="panel-header">
          <button className="btn btn--sm btn--secondary" type="button" onClick={() => setOpen(null)}>
            ← Help centre
          </button>
          <span className="panel-title">{open.title}</span>
        </div>
        <div className="help-article__body">
          {(open.body || '').split('\n\n').map((p, i) => <p key={i}>{p}</p>)}
        </div>
        <div className="help-actions">
          <span className="help-fact__label">Did this help?</span>
          <button className="btn btn--sm btn--secondary" type="button" onClick={() => rate(true)}>
            Yes
          </button>
          <button className="btn btn--sm btn--secondary" type="button" onClick={() => rate(false)}>
            Not really
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="panel-header"><span className="panel-title">Help centre</span></div>
      <form
        className="help-search"
        onSubmit={(e) => { e.preventDefault(); load(query) }}
      >
        <input
          className="help-input"
          placeholder="Search the help centre…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="btn btn--secondary" type="submit">Search</button>
      </form>

      {articles === null && <div className="empty-state">Loading…</div>}
      {articles && articles.length === 0 && (
        <div className="empty-state">
          Nothing matches that yet. Ask the assistant — it can look at your
          account directly.
        </div>
      )}
      {articles && articles.map((a) => (
        <button
          key={a.id}
          type="button"
          className="help-article-row"
          onClick={() => openArticle(a.slug)}
        >
          <span className="help-article-row__title">{a.title}</span>
          <span className="help-article-row__summary">{a.summary}</span>
        </button>
      ))}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────
 * SYSTEM STATUS
 * ───────────────────────────────────────────────────────────────────────── */

function StatusPanel() {
  const toast = useToast()
  const [status, setStatus] = useState(null)

  useEffect(() => {
    api.get('/support/status')
      .then(setStatus)
      .catch((e) => toast.error(e.detail || 'Could not read system status.'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!status) return <div className="panel"><div className="empty-state">Loading…</div></div>

  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">System status</span>
        <span className={`badge ${SEVERITY_CLASS[status.overall_severity] || 'badge--neutral'}`}>
          {status.overall_label}
        </span>
      </div>

      {status.incident_note && (
        /* Written or approved by a person. Never a count, never another
           customer, never another brand — see support_incidents
           .customer_statement. */
        <div className="help-incident">{status.incident_note}</div>
      )}

      <div className="stat-grid">
        {status.services.map((s) => (
          <div key={s.key} className="help-service">
            <div className="help-service__head">
              <span className="help-service__name">{s.label}</span>
              <span className={`badge ${SEVERITY_CLASS[s.severity] || 'badge--neutral'}`}>
                {s.severity_label}
              </span>
            </div>
            <div className="help-service__headline">{s.headline}</div>
            {s.settings_path && s.severity !== 'healthy' && (
              <a className="help-service__link" href={s.settings_path}>Open settings</a>
            )}
          </div>
        ))}
      </div>
      <p className="help-hint">Checked {when(status.checked_at)}.</p>
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────
 * SUPPORT PLAN & SLA
 * ───────────────────────────────────────────────────────────────────────── */

function PlanPanel() {
  const toast = useToast()
  const [plan, setPlan] = useState(null)
  const [services, setServices] = useState(null)
  const [request, setRequest] = useState({ topic: '', minutes: 30, category: 'customer_assistance' })
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const [p, s] = await Promise.all([
        api.get('/support/plan'),
        api.get('/support/services'),
      ])
      setPlan(p)
      setServices(s)
    } catch (e) {
      toast.error(e.detail || 'Could not load your support plan.')
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function bookAssistance(e) {
    e.preventDefault()
    setBusy(true)
    try {
      const result = await api.post('/support/assistance', request)
      toast.success(
        result.minutes_billable > 0
          ? `Requested. ${result.minutes_covered_by_plan} minutes are included; `
            + `${result.minutes_billable} would be billable and we'll confirm before starting.`
          : 'Requested. This is covered by your plan.',
      )
      setRequest({ ...request, topic: '' })
      load()
    } catch (err) {
      toast.error(err.detail || 'Could not send that request.')
    } finally {
      setBusy(false)
    }
  }

  if (!plan) return <div className="panel"><div className="empty-state">Loading…</div></div>

  const a = plan.assistance
  const used = a.allowance_minutes ? Math.min(100, Math.round((a.used_minutes / a.allowance_minutes) * 100)) : 0

  return (
    <div className="help-grid">
      <div className="panel">
        <div className="panel-header">
          <span className="panel-title">{plan.plan.name}</span>
          <span className="badge badge--blue">{plan.plan.queue_label} queue</span>
        </div>

        <table className="data-table">
          <thead>
            <tr><th>Priority</th><th>What it means</th><th>First response</th></tr>
          </thead>
          <tbody>
            {plan.response_targets.map((t) => (
              <tr key={t.severity}>
                <td data-label="Priority">{t.label}</td>
                <td data-label="What it means">{t.meaning}</td>
                <td data-label="First response">{t.first_response_text}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <p className="help-hint">{plan.commitment_note}</p>
        <p className="help-hint">{plan.emergency_note}</p>
        <p className="help-hint">Support hours: {plan.hours.text}</p>

        <div className="divider" />

        <div className="help-fact__label">Included live assistance this period</div>
        <div className="help-meter">
          <div className="help-meter__fill" style={{ width: `${used}%` }} />
        </div>
        <p className="help-hint">
          {a.used_minutes} of {a.allowance_minutes} minutes used ·{' '}
          {a.remaining_minutes} remaining
          {a.overage_minutes > 0 && ` · ${a.overage_minutes} minutes over`}
          {' '}· resets {when(a.period_end)} · unused minutes do not roll over
        </p>

        <div className="help-fact__label">What&apos;s included</div>
        <div className="help-features">
          {plan.plan.features.map((f) => (
            <span key={f.key} className="badge badge--neutral">{f.label}</span>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-header"><span className="panel-title">Book live assistance</span></div>
        <form className="help-form" onSubmit={bookAssistance}>
          <label className="help-label">
            What would you like help with?
            <textarea
              className="help-input help-textarea"
              rows={3}
              required
              value={request.topic}
              onChange={(e) => setRequest({ ...request, topic: e.target.value })}
            />
          </label>
          <label className="help-label">
            How long?
            <select
              className="help-input"
              value={request.minutes}
              onChange={(e) => setRequest({ ...request, minutes: Number(e.target.value) })}
            >
              <option value={15}>15 minutes</option>
              <option value={30}>30 minutes</option>
              <option value={60}>60 minutes</option>
              <option value={120}>2 hours</option>
            </select>
          </label>
          <label className="help-label">
            What kind of help?
            <select
              className="help-input"
              value={request.category}
              onChange={(e) => setRequest({ ...request, category: e.target.value })}
            >
              <option value="customer_assistance">Help using the product</option>
              <option value="professional_services">Work for our business</option>
              <option value="technical_product_support">Something is broken</option>
            </select>
            <span className="help-hint">
              Time spent on a problem with the product is always included and
              never uses your assistance minutes.
            </span>
          </label>
          <button className="btn btn--primary" type="submit" disabled={busy || !request.topic.trim()}>
            {busy ? 'Sending…' : 'Request assistance'}
          </button>
        </form>

        {services && services.offerings.length > 0 && (
          <>
            <div className="divider" />
            <div className="help-fact__label">Also available</div>
            {services.offerings.map((o) => (
              <div key={o.key} className="help-offering">
                <div>
                  <div className="help-offering__name">{o.name}</div>
                  <div className="help-hint">{o.description}</div>
                </div>
                {/* A price appears only where somebody configured one.
                    Everything else says "quoted" rather than showing a number
                    nobody agreed to. */}
                <span className="badge badge--neutral">
                  {o.price_text || 'Quoted'}
                </span>
              </div>
            ))}
            <p className="help-hint">{services.note}</p>
          </>
        )}
      </div>
    </div>
  )
}
