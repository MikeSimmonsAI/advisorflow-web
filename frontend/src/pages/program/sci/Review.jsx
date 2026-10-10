/* Review: records that need a person before outreach - source data notes,
   missing locations, possible duplicates, holds. Reads GET /program/records;
   assigning a location, holding and releasing use the existing audited
   endpoints. Resolving here never overrides STOP, DNC, consent or suppression. */
import { Fragment, useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, Metric, PageHead, errText, num } from './ui'

const QUEUES = [
  ['on_hold', 'On hold'], ['data_review', 'Source data notes'], ['location_review', 'Location issues'],
  ['duplicate_review', 'Duplicates'], ['linked', 'Linked under one contact'], ['all', 'All staged'],
]

function issueOf(r) {
  if (r.on_hold) return { text: `On hold${r.hold_reason ? ` — ${r.hold_reason}` : ''}`, pri: 'High', tone: 'bad' }
  if (r.location_status !== 'mapped') return { text: 'No location resolved — nothing is sent', pri: 'High', tone: 'bad' }
  if (r.data_note_flags?.length) return { text: `Source data note: ${r.data_note_flags.join(', ')}`, pri: 'Medium', tone: 'warn' }
  if (r.duplicate_review_reason) return { text: `Possible duplicate: ${r.duplicate_review_reason}`, pri: 'Medium', tone: 'warn' }
  if (r.link_reason) return { text: r.link_reason, pri: 'Low', tone: '' }
  return { text: 'No open issue', pri: 'Low', tone: 'ok' }
}

export default function Review({ locationId, locations, isManager, attention, initialQueue, initialSearch, onChange }) {
  const [queue, setQueue] = useState(initialQueue)
  const [search, setSearch] = useState(initialSearch || '')
  const [data, setData] = useState(null)
  const [openId, setOpenId] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  useEffect(() => { setQueue(initialQueue) }, [initialQueue])
  useEffect(() => { setSearch(initialSearch || '') }, [initialSearch])

  const load = useCallback(() => {
    const q = new URLSearchParams({ limit: '300' })
    if (queue && queue !== 'all') q.set('queue', queue)
    if (locationId) q.set('location_id', locationId)
    if (search.trim()) q.set('search', search.trim())
    api.get(`/program/records?${q}`).then(d => { setData(d); setErr('') }).catch(e => { setErr(errText(e)); setData({ total: 0, items: [] }) })
  }, [queue, locationId, search])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])

  const homes = locations.filter(l => !l.is_review_bucket)
  const assign = async (rec, loc) => {
    if (!loc) return
    try { await api.post(`/program/records/${rec.id}/location`, { location_id: loc }); setMsg('Location assigned.'); load(); onChange() } catch (e) { setErr(errText(e)) }
  }
  const setHold = async (rec, on) => {
    try { await api.post(`/program/records/${rec.id}/hold`, { on_hold: on }); setMsg(on ? 'Record held.' : 'Record released.'); load(); onChange() } catch (e) { setErr(errText(e)) }
  }
  const holdAll = async () => {
    if (!window.confirm('Put every record still in Location, Duplicate or Data Review on hold? Nothing is deleted or changed; they are excluded from outreach until released.')) return
    try {
      const r = await api.post('/program/records/hold-open-reviews', {})
      setQueue('on_hold'); load(); onChange(); setMsg(r.held ? `${num(r.held)} record(s) held.` : 'Nothing new to hold.')
    } catch (e) { setErr(errText(e)) }
  }

  const counts = {
    data_review: attention.data_review || 0, location_review: attention.location_review || 0,
    duplicate_review: attention.duplicate_review || 0, on_hold: attention.on_hold || 0,
  }
  const pick = k => { setQueue(k); setOpenId(null) }

  return (
    <>
      <PageHead title="Items needing review" sub="Resolve record conflicts without losing audit history.">
        {isManager && <button type="button" className="sci-btn" onClick={holdAll}>Hold all open reviews</button>}
      </PageHead>
      <section className="sci-metrics" aria-label="Review counts">
        <Metric label="Source data notes" value={counts.data_review} tone={counts.data_review ? 'warn' : ''} note="Review before outreach" onClick={() => pick('data_review')} />
        <Metric label="Location issues" value={counts.location_review} tone={counts.location_review ? 'bad' : ''} note="Need a cemetery assigned" onClick={() => pick('location_review')} />
        <Metric label="Possible duplicates" value={counts.duplicate_review} tone={counts.duplicate_review ? 'warn' : ''} note="Confirm or link" onClick={() => pick('duplicate_review')} />
        <Metric label="Held records" value={counts.on_hold} note="Excluded from outreach" onClick={() => pick('on_hold')} />
      </section>

      <section className="sci-panel sci-pad" aria-labelledby="sci-exc">
        <div className="sci-panel-head">
          <div>
            <h2 id="sci-exc">Exception management</h2>
            <div className="sci-filterchips" role="group" aria-label="Queue" style={{ marginTop: 10 }}>
              {QUEUES.map(([k, label]) => (
                <button key={k} type="button" aria-pressed={queue === k} onClick={() => pick(k)}>
                  {label}{counts[k] != null && <span className="n">{counts[k]}</span>}
                </button>
              ))}
            </div>
          </div>
          <div className="sci-search" style={{ width: 280 }}>
            <label htmlFor="sci-rev-search" className="sci-sr">Search records</label>
            <input id="sci-rev-search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name, email, phone or Lead ID" />
          </div>
        </div>
        <ErrorLine text={err} />
        {msg && <div className="sci-okmsg" role="status">{msg}</div>}
        <div className="sci-micro sci-muted" style={{ marginBottom: 8 }}>{data ? `${num(data.total)} record(s)` : 'Loading…'}</div>
        <div className="sci-tablewrap">
          <table className="sci-table">
            <thead><tr><th>Contact</th><th>Issue</th><th>Location</th><th>Priority</th><th><span className="sci-sr">Action</span></th></tr></thead>
            <tbody>
              {(data?.items || []).map(r => {
                const is = issueOf(r)
                const open = openId === r.id
                return (
                  <Fragment key={r.id}>
                    <tr className={open ? 'sci-open' : ''}>
                      <td>
                        <b style={{ fontWeight: 600 }}>{r.first_name} {r.last_name}</b>
                        <div className="sci-micro sci-muted">{[r.email, r.phone].filter(Boolean).join(' · ') || 'No contact details'} · Lead {r.source_lead_id}</div>
                      </td>
                      <td className="sci-small">{is.text}</td>
                      <td>
                        {r.location_status === 'mapped' ? r.location : (
                          isManager ? (
                            <select className="sci-input" style={{ minHeight: 36, fontSize: 13 }} aria-label={`Assign a location for ${r.source_lead_id}`} defaultValue="" onChange={e => assign(r, e.target.value)}>
                              <option value="">Assign location…</option>
                              {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                            </select>
                          ) : <Chip tone="warn">Location review</Chip>
                        )}
                        {r.source_location_name && r.location_status !== 'mapped' && <div className="sci-micro sci-muted">source: {r.source_location_name}</div>}
                      </td>
                      <td><Chip tone={is.tone} plain>{is.pri}</Chip></td>
                      <td style={{ textAlign: 'right' }}>
                        <button type="button" className="sci-btn sm" aria-expanded={open} onClick={() => setOpenId(open ? null : r.id)}>{open ? 'Close' : 'Review'}</button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="sci-open">
                        <td colSpan={5}>
                          <div className="sci-cards">
                            <div className="sci-card"><dt>Status (as supplied)</dt><dd>{r.source_status || '—'}</dd></div>
                            <div className="sci-card"><dt>Campaign</dt><dd>{r.campaign_family || '—'}</dd></div>
                            <div className="sci-card"><dt>Source location</dt><dd>{r.source_location_name || '—'}</dd></div>
                          </div>
                          <ul className="sci-small" style={{ margin: '12px 0', paddingLeft: 18 }}>
                            {r.on_hold && <li>On hold{r.hold_reason ? `: ${r.hold_reason}` : ''}</li>}
                            {r.data_note_flags?.length > 0 && <li>Source data note detected — review: {r.data_note_flags.join(', ')}</li>}
                            {r.duplicate_review_reason && <li>Duplicate review: {r.duplicate_review_reason}</li>}
                            {r.link_reason && <li>{r.link_reason}</li>}
                            {r.location_status !== 'mapped' && <li>No location resolved — nothing is sent.</li>}
                          </ul>
                          {isManager && (
                            <div className="sci-row-gap">
                              {r.on_hold
                                ? <button type="button" className="sci-btn" onClick={() => setHold(r, false)}>Release hold</button>
                                : <button type="button" className="sci-btn" onClick={() => setHold(r, true)}>Put on hold</button>}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
          {data && data.items.length === 0 && <Empty title="Nothing in this queue.">Records that need a person show up here.</Empty>}
        </div>
        <p className="sci-micro sci-muted" style={{ marginBottom: 0, marginTop: 14 }}>Safety gate: resolving a review never overrides STOP, do-not-call, consent or suppression. Every change is recorded.</p>
      </section>
    </>
  )
}
