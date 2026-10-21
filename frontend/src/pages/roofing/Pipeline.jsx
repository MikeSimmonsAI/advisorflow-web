/**
 * Roofing sales pipeline - GET /roofing-ops/pipeline, moves via
 * POST /roofing-ops/leads/{id}/stage. Activity moves homeowners forward on its
 * own; a person moves them into Inspected, Estimate Sent, Won or Lost here.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useRoofing } from './RoofingShell'
import { Avatar, Empty, ErrorNote, Icon, Loading, Panel, SampleTag, errText, localWhen, money, n, useApi, useToast } from './ui'
import './roofing-ops.css'

const COL_COLORS = {
  imported: '#8fa3c4', qualified: '#2f6fed', contacted: '#2f6fed', engaged: '#12925e', inspection_offered: '#f5a524',
  inspection_booked: '#f26a1b', inspected: '#6d4fd8', estimate_sent: '#6d4fd8', won: '#12925e', lost: '#a8b4c6',
}

export default function Pipeline() {
  const { status } = useRoofing()
  const [owner, setOwner] = useState('')
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const qs = new URLSearchParams({ ...(owner ? { owner } : {}), ...(query ? { q: query } : {}) }).toString()
  const { data, error, loading, reload } = useApi(`/roofing-ops/pipeline${qs ? `?${qs}` : ''}`)
  const [toast, say] = useToast()
  const sample = status.is_demo

  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Roofing sales pipeline</h1>
          <p className="rf-lede">Every homeowner from import to a closed job. Replies and bookings move cards forward automatically; you move them after the inspection.</p>
        </div>
        <form className="rf-filters" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()) }}>
          <div className="rf-search">
            <Icon name="search" size={16} />
            <input className="rf-input" placeholder="Search name, phone, address" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search homeowners" />
          </div>
          {status.can_manage && (
            <select className="rf-select" value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Salesperson">
              <option value="">All salespeople</option>
              <option value="me">My homeowners</option>
              <option value="unassigned">Unassigned</option>
              {(status.team || []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          )}
        </form>
      </div>

      {error && <ErrorNote onRetry={reload}>{error}</ErrorNote>}
      {!data && loading && <Loading label="Loading pipeline" />}
      {data && (
        <div className="rf-stack">
          <div className="rf-totals">
            {sample && <SampleTag />}
            <span><b>{n(data.total)}</b> homeowners</span>
            <span><b>{money(data.open_estimate_value, false)}</b> in open estimates (from estimates entered)</span>
            <span><b>{money(data.won_value, false)}</b> won (from amounts entered)</span>
          </div>
          {data.total === 0 ? (
            <Panel>
              <Empty icon="pipeline" title={query || owner ? 'No homeowners match these filters' : 'No homeowners yet'}
                action={!query && !owner ? <Link className="rf-btn rf-btn--primary" to={`${BASE}/leads`}>Import your leads</Link> : null}>
                {query || owner ? 'Clear the search or pick another salesperson.' : 'Import the leads you already have and the AI starts working them.'}
              </Empty>
            </Panel>
          ) : (
            <div className="rf-board" role="list" aria-label="Pipeline stages">
              {data.columns.map((c) => (
                <section className="rf-col" key={c.key} role="listitem" aria-label={c.label}>
                  <header className="rf-col__head" style={{ borderTopColor: COL_COLORS[c.key] }}>
                    <h3>{c.label}</h3>
                    <div className="rf-col__count">{n(c.count)}</div>
                    <div className="rf-col__hint">{data.total ? `${Math.round((c.count / data.total) * 1000) / 10}% of ${n(data.total)} homeowners` : c.hint}</div>
                  </header>
                  <div className="rf-col__cards">
                    {c.cards.map((card) => (
                      <Card key={card.lead_id} card={card} stage={c.key} stages={status.stages}
                        onMoved={(label) => { say(`Moved ${card.name} to ${label}`); reload() }}
                        onError={(m) => say(m, 'err')} />
                    ))}
                    {c.count > c.cards.length && <p className="rf-muted" style={{ fontSize: 12.5, padding: '0 4px' }}>Showing {c.cards.length} of {n(c.count)}. Search to find others.</p>}
                    {c.count === 0 && <p className="rf-muted" style={{ fontSize: 12.5, padding: '6px 4px' }}>No one here right now.</p>}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      )}
      {toast}
    </div>
  )
}

function Card({ card, stage, stages, onMoved, onError }) {
  const [moving, setMoving] = useState(false)
  const [to, setTo] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const target = stages.find((s) => s.key === to)

  const submit = async (e) => {
    e.preventDefault()
    if (!to) return
    if (to === 'lost' && !reason.trim()) { onError('Say why the homeowner was lost.'); return }
    setBusy(true)
    try {
      await api.post(`/roofing-ops/leads/${card.lead_id}/stage`, { stage: to, reason: reason.trim() || undefined })
      setMoving(false)
      onMoved(target?.label || to)
    } catch (err) { onError(errText(err)) } finally { setBusy(false) }
  }

  return (
    <article className="rf-card">
      <div className="rf-card__top">
        <Avatar name={card.name} size={30} />
        <div style={{ minWidth: 0 }}>
          <b>{card.name}</b>
          {card.is_test && <span className="rf-muted" style={{ fontSize: 11.5 }}> sample</span>}
        </div>
        {card.unread > 0 && <span className="rf-card__unread" title="Unread replies">{card.unread}</span>}
      </div>
      <div className="rf-card__meta">
        {card.address && <div>{card.address}</div>}
        {card.roof_issue && <div>{card.roof_issue}{card.service_type ? ` (${card.service_type.replace('_', ' ')})` : ''}</div>}
        <div>{card.owner_name || 'Unassigned'}</div>
        {card.days_in_stage !== null && <div>{card.days_in_stage === 0 ? 'Entered this stage today' : `${card.days_in_stage} days in this stage`}</div>}
        {card.inspection_at && <div><b>Inspection {localWhen(card.inspection_at)}</b></div>}
        {card.estimate_amount ? <div>Estimate {money(card.estimate_amount, false)}</div> : null}
        {card.won_amount ? <div>Won {money(card.won_amount, false)}</div> : null}
        {stage === 'lost' && card.lost_reason && <div>Lost: {card.lost_reason}</div>}
      </div>
      {card.next_action && <div className="rf-card__next">{card.next_action}</div>}
      {moving ? (
        <form className="rf-move" onSubmit={submit}>
          <select className="rf-select" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Move to stage" autoFocus>
            <option value="">Move to…</option>
            {stages.filter((s) => s.key !== stage).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          {to && (
            <input className="rf-input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300}
              placeholder={to === 'lost' ? 'Why was it lost? (required)' : 'Note (optional)'} aria-label="Reason" />
          )}
          <div className="rf-card__actions">
            <button className="rf-btn rf-btn--primary rf-btn--sm" type="submit" disabled={!to || busy}>{busy ? 'Moving…' : 'Move'}</button>
            <button className="rf-btn rf-btn--ghost rf-btn--sm" type="button" onClick={() => { setMoving(false); setTo(''); setReason('') }}>Cancel</button>
          </div>
        </form>
      ) : (
        <div className="rf-card__actions">
          <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${BASE}/conversations?lead=${card.lead_id}`}>Open</Link>
          <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => setMoving(true)}>Move to…</button>
        </div>
      )}
    </article>
  )
}
