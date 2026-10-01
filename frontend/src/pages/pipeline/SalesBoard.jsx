/**
 * SALES BOARD — the lead book by THIS organization's configured stages.
 *
 *   GET  /pipeline/board?owner=            columns (org lead tiers) + cards + lost lane
 *   POST /pipeline/board/{id}/stage        move a card to another configured stage
 *   POST /pipeline/board/{id}/lost         mark lost (reason required)
 *   POST /pipeline/board/{id}/reopen       clear a loss
 *
 * Every card field comes from a record (see the route header in
 * app/routers/pipeline_router.py). Age in stage reads stage_entered_at; a lead
 * that has not moved since that was recorded says "since <created>" instead.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import './SalesBoard.css'

function errText(e) { return e?.message || 'Request failed' }
function fmtDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
function fmtDateTime(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
function ageText(c) {
  if (c.age_in_stage_days == null) return 'Age not available'
  const n = c.age_in_stage_days
  const d = n === 0 ? 'today' : `${n} day${n === 1 ? '' : 's'}`
  if (c.age_basis === 'stage_entered') return n === 0 ? 'Entered stage today' : `${d} in stage`
  return `Since created ${fmtDate(c.created_at)} (${d}) · stage date not recorded`
}

function Card({ c, stages, reasons, onMove, onLost, onReopen, busy }) {
  const [losing, setLosing] = useState(false)
  return (
    <article className={`sb-card${c.lost ? ' is-lost' : ''}`} data-testid="sb-card">
      <div className="sb-card-head">
        <Link to={c.link} className="sb-name">{c.name}</Link>
        {c.dnc && <span className="sb-pill sb-pill--red">DNC</span>}
      </div>
      <div className="sb-meta">
        <span title="Owner">{c.owner || 'Unassigned'}</span>
        <span className={c.age_basis === 'created' ? 'sb-soft' : ''} title="Age in stage">{ageText(c)}</span>
      </div>
      <dl className="sb-facts">
        <dt>Next task</dt>
        <dd>{c.next_task
          ? <Link to={c.next_task.link} className={c.next_task.overdue ? 'sb-overdue' : ''}>
              {c.next_task.title}{c.next_task.due_at ? ` · ${fmtDate(c.next_task.due_at)}` : ''}{c.next_task.overdue ? ' (overdue)' : ''}
            </Link>
          : <span className="sb-soft">None open</span>}</dd>
        <dt>Appointment</dt>
        <dd>{c.appointment
          ? <span>{c.appointment.past ? 'Last: ' : ''}{fmtDateTime(c.appointment.at)}{c.appointment.label ? ` · ${c.appointment.label}` : ''}</span>
          : <span className="sb-soft">None booked</span>}</dd>
        {c.rate_request && <><dt>Rate request</dt><dd><Link to={c.rate_request.link}>Open request</Link></dd></>}
        {c.customer && <><dt>Customer</dt><dd className={c.customer.enrolled_at ? 'sb-good' : 'sb-soft'}>{c.customer.label}</dd></>}
        {c.lost && <><dt>Lost</dt><dd className="sb-bad">{c.lost.reason_label}{c.lost.reason?.includes(':') ? ` — ${c.lost.reason.split(':').slice(1).join(':').trim()}` : ''} · {fmtDate(c.lost.at)}</dd></>}
        {c.notes_preview && <><dt>Notes</dt><dd className="sb-notes">{c.notes_preview}</dd></>}
      </dl>
      <div className="sb-actions">
        <Link to={c.activity.link} className="sb-link">Activity{c.activity.last_at ? ` · ${fmtDate(c.activity.last_at)}` : ''}</Link>
        {!c.lost && (
          <select aria-label={`Move ${c.name}`} value={c.stage || ''} disabled={busy}
            onChange={e => onMove(c, e.target.value)} className="sb-move">
            {stages.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        )}
        {!c.lost && !losing && <button type="button" className="sb-btn" disabled={busy} onClick={() => setLosing(true)}>Mark lost</button>}
        {c.lost && <button type="button" className="sb-btn" disabled={busy} onClick={() => onReopen(c)}>Reopen</button>}
      </div>
      {losing && <LostForm c={c} reasons={reasons} busy={busy} onCancel={() => setLosing(false)} onSubmit={(r, d) => onLost(c, r, d).then(ok => ok && setLosing(false))} />}
    </article>
  )
}

function LostForm({ c, reasons, busy, onCancel, onSubmit }) {
  const [reason, setReason] = useState('')
  const [detail, setDetail] = useState('')
  const needDetail = reason === 'other'
  return (
    <form className="sb-lost" onSubmit={e => { e.preventDefault(); if (reason) onSubmit(reason, detail) }}>
      <select aria-label={`Loss reason for ${c.name}`} value={reason} onChange={e => setReason(e.target.value)} required>
        <option value="">Loss reason…</option>
        {reasons.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
      </select>
      <input aria-label="Loss detail" placeholder={needDetail ? 'Describe (required)' : 'Detail (optional)'}
        value={detail} maxLength={500} onChange={e => setDetail(e.target.value)} required={needDetail} />
      <div className="sb-lost-actions">
        <button type="submit" className="sb-btn sb-btn--danger" disabled={busy || !reason || (needDetail && !detail.trim())}>Mark lost</button>
        <button type="button" className="sb-btn" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

export default function SalesBoard() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [owner, setOwner] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [showLost, setShowLost] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const reload = useCallback(() => setReloadKey(k => k + 1), [])

  useEffect(() => {
    let alive = true
    setErr(null)
    api.get(`/pipeline/board${owner ? `?owner=${encodeURIComponent(owner)}` : ''}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) { setData(null); setErr(errText(e)) } })
    return () => { alive = false }
  }, [owner, reloadKey])

  const act = async (fn, msg) => {
    setBusy(true); setNotice(null)
    try { await fn(); setNotice({ ok: true, text: msg }); reload(); return true }
    catch (e) { setNotice({ ok: false, text: errText(e) }); return false }
    finally { setBusy(false) }
  }
  const stages = (data?.columns || []).map(c => ({ key: c.key, label: c.label }))
  const onMove = (c, stage) => act(() => api.post(`/pipeline/board/${c.id}/stage`, { stage }),
    `${c.name} moved to ${stages.find(s => s.key === stage)?.label || stage}.`)
  const onLost = (c, reason, detail) => act(() => api.post(`/pipeline/board/${c.id}/lost`, { reason, detail: detail || null }),
    `${c.name} marked lost.`)
  const onReopen = c => act(() => api.post(`/pipeline/board/${c.id}/reopen`, {}), `${c.name} reopened.`)

  if (err) return <section className="pl-panel"><p className="sb-bad">Could not load the board: {err}</p></section>
  if (!data) return <section className="pl-panel"><p className="pl-muted">Loading board…</p></section>

  const total = data.columns.reduce((n, c) => n + (c.count || 0), 0)
  return (
    <section className="sb">
      <div className="sb-bar">
        <p className="pl-muted sb-explain">
          {total.toLocaleString('en-US')} open in {data.columns.length} configured stages
          {data.unstaged_count ? ` · ${data.unstaged_count.toLocaleString('en-US')} not at a configured stage` : ''}
          {' '}· Stages come from your organization&apos;s lead tiers. Age in stage is recorded from each stage change.
        </p>
        <div className="sb-bar-controls">
          <select aria-label="Owner" value={owner} onChange={e => setOwner(e.target.value)} className="sb-move">
            <option value="">{data.is_manager ? 'All owners' : 'My leads'}</option>
            {data.is_manager && <option value="me">Mine</option>}
            {data.is_manager && <option value="unassigned">Unassigned</option>}
          </select>
          <button type="button" className="sb-btn" onClick={() => setShowLost(v => !v)} aria-pressed={showLost}>
            Lost ({data.lost.count.toLocaleString('en-US')})
          </button>
        </div>
      </div>
      {notice && <p role="status" className={notice.ok ? 'sb-good sb-notice' : 'sb-bad sb-notice'}>{notice.text}</p>}
      <div className="sb-columns">
        {data.columns.map(col => (
          <div key={col.key} className="sb-col" data-testid={`sb-col-${col.key}`}>
            <header className="sb-col-head">
              <span>{col.label}</span>
              <span className="sb-count">{col.count.toLocaleString('en-US')}</span>
            </header>
            {col.cards.length === 0 && <p className="sb-empty">No leads at this stage.</p>}
            {col.cards.map(c => <Card key={c.id} c={c} stages={stages} reasons={data.loss_reasons || []} busy={busy} onMove={onMove} onLost={onLost} onReopen={onReopen} />)}
            {col.count > col.cards.length && (
              <p className="sb-empty">Showing the {col.cards.length} longest in stage of {col.count.toLocaleString('en-US')}.</p>
            )}
          </div>
        ))}
        {showLost && (
          <div className="sb-col sb-col--lost" data-testid="sb-col-lost">
            <header className="sb-col-head"><span>Lost</span><span className="sb-count">{data.lost.count}</span></header>
            {data.lost.cards.length === 0 && <p className="sb-empty">Nothing marked lost.</p>}
            {data.lost.cards.map(c => <Card key={c.id} c={c} stages={stages} reasons={data.loss_reasons || []} busy={busy} onMove={onMove} onLost={onLost} onReopen={onReopen} />)}
          </div>
        )}
      </div>
    </section>
  )
}
