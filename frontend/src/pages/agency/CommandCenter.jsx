/**
 * COMMAND CENTER — what needs attention, the counts behind it, and Ask EvoAI.
 *
 *   GET  /agency/summary    counts; each carries the exact list path it equals
 *   GET  /agency/attention  items, each linking to one record
 *   POST /agency/ask        intent-parsed answer over this workspace's records
 *
 * No decorative KPIs: every number is a link to the list it counts, every
 * attention row opens its record.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { APP_STATUS_LABELS, askState, humanize, routeForLink, sortAttention } from './agencyFormat'
import { readableTimestamps, replaceKeys } from '../../utils/humanize'
import { AgencyPage, DemoBadge, Empty, ErrorState, Loading, Pill, Section, useAction, useAgency } from './agencyUi'

const SUMMARY_ORDER = [
  'attention', 'high_intent', 'unassigned', 'offers_pending', 'escalated', 'appointments_need_confirmation',
  'appointments_today', 'applications_open', 'applications_stalled', 'policies_review_due', 'recruits', 'agents', 'prospects',
]

export function SummaryStrip({ q }) {
  if (q.loading && !q.data) return <Loading label="Loading counts" />
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />
  const counts = q.data?.counts || {}
  const keys = [...SUMMARY_ORDER.filter(k => counts[k]), ...Object.keys(counts).filter(k => !SUMMARY_ORDER.includes(k))]
  return (
    <nav className="ag-counts" aria-label="Agency counts">
      {keys.map(k => {
        const c = counts[k]
        const to = routeForLink(c.link) || '/agency'
        return (
          <Link key={k} to={to} className={`ag-count${c.count && ['attention', 'escalated', 'applications_stalled'].includes(k) ? ' ag-count--hot' : ''}`} data-testid={`count-${k}`}>
            <span className="ag-count__n">{c.count}</span>
            <span className="ag-count__l">{c.label}</span>
          </Link>
        )
      })}
    </nav>
  )
}

export function AttentionList({ q, limit }) {
  if (q.loading && !q.data) return <Loading label="Checking what needs attention" />
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />
  const items = sortAttention(q.data?.items)
  if (!items.length) return <Empty>Nothing needs attention right now. New prospects, stalled cases and overdue follow-ups will surface here.</Empty>
  const shown = limit ? items.slice(0, limit) : items
  return (
    <ul className="ag-attn">
      {shown.map((it, i) => {
        const to = routeForLink(it.link)
        const body = (
          <>
            <span className={`ag-attn__sev ag-attn__sev--${it.severity || 'low'}`} aria-label={`${it.severity} severity`} />
            <span className="ag-attn__main">
              <span className="ag-attn__title">{it.title}</span>
              <span className="ag-attn__detail">{readableTimestamps(replaceKeys(it.detail, APP_STATUS_LABELS))}</span>
            </span>
            <span className="ag-attn__meta"><Pill value={it.kind} label={humanize(it.link?.type || it.kind)} tone="muted" /><DemoBadge on={it.is_demo} /></span>
          </>
        )
        return <li key={`${it.kind}-${it.link?.id || i}`}>{to ? <Link to={to} className="ag-attn__row">{body}</Link> : <div className="ag-attn__row">{body}</div>}</li>
      })}
      {limit && items.length > limit ? <li className="ag-attn__more"><Link to="/agency/intelligence">All {items.length} items →</Link></li> : null}
    </ul>
  )
}

const ASK_STARTERS = [
  'What needs my attention today?', 'Which agents have capacity?', 'What applications are stalled?',
  'Which offers are awaiting acceptance?', 'Which tasks are overdue?', 'Which annual reviews are due this month?',
]

function AskFilter({ filter }) {
  if (!filter) return null
  const apis = filter.apis || (filter.api ? [filter.api] : [])
  return (
    <div className="ag-ask__filter" data-testid="ask-filter">
      <span className="ag-k">Filter used</span> <span>{filter.description}</span>
      {apis.length ? <div className="ag-ask__api">{apis.map(a => <code key={a}>{a}</code>)}</div> : null}
    </div>
  )
}

export function AskEvoAI({ compact }) {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState(null)
  const [run, busy, error] = useAction()
  const ask = async (text) => {
    const qtext = (text ?? question).trim()
    if (!qtext) return
    setQuestion(qtext)
    const r = await run(() => api.post('/agency/ask', { question: qtext }))
    if (r) setAnswer(r)
  }
  const state = askState(answer)
  const chips = state === 'unsupported' || state === 'insufficient' ? (answer.suggestions || []) : []
  return (
    <Section title="Ask EvoAI" className="ag-ask" aside={<span className="ag-muted">Answers only from this workspace's records</span>}>
      <form className="ag-ask__form" onSubmit={e => { e.preventDefault(); ask() }}>
        <input className="ag-input" value={question} onChange={e => setQuestion(e.target.value)}
          placeholder="e.g. What does Maya have open? · retirement prospects in TX" aria-label="Ask EvoAI a question" maxLength={500} />
        <button className="ag-btn" type="submit" disabled={busy || !question.trim()}>{busy ? 'Asking…' : 'Ask'}</button>
      </form>
      {error ? <ErrorState error={error} /> : null}
      {answer ? (
        <div className={`ag-ask__answer ag-ask__answer--${state}`} data-testid="ask-answer" data-state={state}>
          {state === 'insufficient' ? <div className="ag-ask__insuff" role="status"><Pill value="insufficient" label="Insufficient information" tone="amber" /></div> : null}
          {state === 'unsupported' ? <div className="ag-ask__insuff" role="status"><Pill value="unsupported" label="Not a supported question" tone="muted" /></div> : null}
          <p className="ag-ask__text">{answer.answer} {answer.demo_data ? <DemoBadge on /> : null}</p>
          {answer.corrections?.length ? <p className="ag-muted ag-small">Read as: “{answer.normalized}”</p> : null}
          {state !== 'unsupported' ? <AskFilter filter={answer.filter} /> : null}
          {answer.items?.length ? (
            <ul className="ag-linklist ag-ask__items">
              {answer.items.map((it, i) => {
                const to = routeForLink(it.link)
                return <li key={i}>{to ? <Link to={to}>{it.label}</Link> : it.label} <DemoBadge on={it.is_demo} /></li>
              })}
            </ul>
          ) : null}
          {answer.insufficient?.length ? (
            <div className="ag-ask__gaps"><span className="ag-k">{state === 'insufficient' ? 'Why' : 'Insufficient information — not used or not recorded'}</span>
              <ul>{answer.insufficient.map(x => <li key={x}>{x}</li>)}</ul></div>
          ) : null}
          {chips.length ? (
            <div className="ag-chips" data-testid="ask-suggestions">
              <span className="ag-muted">Closest questions I can answer:</span>
              {chips.map(s => <button key={s} type="button" className="ag-chip" onClick={() => ask(s)}>{s}</button>)}
            </div>
          ) : null}
          {state === 'unsupported' && answer.supported_questions?.length ? (
            <details className="ag-ask__all"><summary className="ag-small">All supported questions ({answer.supported_questions.length})</summary>
              <div className="ag-chips">{answer.supported_questions.map(s => <button key={s} type="button" className="ag-chip" onClick={() => ask(s)}>{s}</button>)}</div>
            </details>
          ) : null}
          {answer.note && state !== 'unsupported' ? <p className="ag-muted ag-small">{answer.note}</p> : null}
        </div>
      ) : !compact ? (
        <div className="ag-chips">
          {ASK_STARTERS.map(s => <button key={s} type="button" className="ag-chip" onClick={() => ask(s)}>{s}</button>)}
        </div>
      ) : null}
    </Section>
  )
}

export default function CommandCenter() {
  const summary = useAgency('/agency/summary')
  const attention = useAgency('/agency/attention')
  return (
    <AgencyPage title="Command Center" testid="agency-command">
      <SummaryStrip q={summary} />
      <div className="ag-grid ag-grid--main">
        <Section title="Needs attention" aside={<Link to="/agency/intelligence" className="ag-link">Agency Intelligence →</Link>}>
          <AttentionList q={attention} limit={8} />
        </Section>
        <AskEvoAI />
      </div>
    </AgencyPage>
  )
}
