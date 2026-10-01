/**
 * CONVERSATIONS — GET /agency/conversations?q=&awaiting_reply=&page=
 *
 * The prospects in THIS workspace that have a conversation (real messages,
 * inbound replies, or recorded SIMULATED copilot sends), newest first. It is
 * a read-only index: each row opens the prospect record, where the full
 * thread and the copilot (Approve → Simulate Send) live. Nothing is sent here.
 */
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiQuery, humanize, relTime } from './agencyFormat'
import { AgencyPage, DemoBadge, FilterBar, ListState, Notice, Pill, Section, useAgency } from './agencyUi'
import { Pager } from './AgencyForms'

const FILTERS = ['q', 'awaiting_reply', 'page']

export default function Conversations() {
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const [q, setQ] = useState(params.get('q') || '')
  const list = useAgency(`/agency/conversations${apiQuery(params, FILTERS, { per_page: 50 })}`)
  const awaiting = params.get('awaiting_reply') === 'true'
  const set = (k, v) => { const p = new URLSearchParams(params); p.delete('page'); if (v) p.set(k, v); else p.delete(k); setParams(p) }
  return (
    <AgencyPage title="Conversations" eyebrow="Client Acquisition" testid="agency-conversations">
      <div className="ag-toolbar">
        <form onSubmit={e => { e.preventDefault(); set('q', q.trim()) }} className="ag-toolbar__search">
          <input className="ag-input" value={q} onChange={e => setQ(e.target.value)} placeholder="Search prospect name, email, phone" aria-label="Search conversations" />
        </form>
        <label className="ag-check"><input type="checkbox" checked={awaiting} onChange={e => set('awaiting_reply', e.target.checked ? 'true' : '')} /> Awaiting our reply</label>
      </div>
      <FilterBar label={[params.get('q') ? `Search: ${params.get('q')}` : '', awaiting ? 'Awaiting our reply' : ''].filter(Boolean).join(' · ')} clearTo="/agency/conversations" />
      <Section>
        <ListState q={list} empty={awaiting ? 'No prospect is waiting on a reply.' : 'No conversations yet. Replies from prospects and copilot activity appear here.'}>
          <ul className="ag-convlist">
            {(list.data?.items || []).map(c => (
              <li key={c.prospect.id} className={`ag-convrow${c.awaiting_reply ? ' ag-convrow--await' : ''}`} onClick={() => nav(`/agency/prospects/${c.prospect.id}`)} data-testid="conversation-row">
                <div className="ag-convrow__who">
                  <Link to={`/agency/prospects/${c.prospect.id}`} onClick={e => e.stopPropagation()}><strong>{c.prospect.name || 'Unnamed'}</strong></Link> <DemoBadge on={c.is_demo} />
                  <div className="ag-muted ag-small">{c.assigned_agent?.name ? `Agent: ${c.assigned_agent.name}` : 'No agent'} · {c.inbound_count} inbound</div>
                </div>
                <div className="ag-convrow__last">
                  {c.last_message ? <>
                    <span className="ag-small ag-muted">{c.last_message.direction === 'inbound' ? 'Prospect' : 'Agency'} · {c.last_message.simulated ? 'SIMULATED (not sent)' : humanize(c.last_message.channel)}</span>
                    <div className="ag-convrow__body">{c.last_message.body}</div>
                  </> : <span className="ag-muted">—</span>}
                </div>
                <div className="ag-convrow__meta">
                  {c.awaiting_reply ? <Pill value="pending" label="Awaiting reply" tone="amber" /> : null}
                  <Pill value={c.intent_level} label={c.intent_level ? `${humanize(c.intent_level)} intent` : null} />
                  <span className="ag-small ag-muted">{relTime(c.last_activity_at)}</span>
                  <Link className="ag-btn ag-btn--sm ag-btn--ghost" to={`/agency/prospects/${c.prospect.id}#copilot`} onClick={e => e.stopPropagation()}>Open copilot</Link>
                </div>
              </li>))}
          </ul>
          <Pager data={list.data} onPage={n => { const p = new URLSearchParams(params); p.set('page', String(n)); setParams(p) }} />
        </ListState>
      </Section>
      <Notice>Read-only index for this workspace. Replies are drafted with the copilot on the prospect record; Simulate Send records a simulated event only — no message is delivered from here.</Notice>
    </AgencyPage>
  )
}
