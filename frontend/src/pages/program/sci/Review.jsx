/* Review: records that need a person before outreach - location issues,
   source data notes, possible duplicates, holds. Queue on the left, one record
   inspected on the right. Reads GET /program/records; every change uses the
   existing audited endpoints:
     POST /records/{id}/location   assign a location (source name kept)
     POST /records/{id}/review     "a person has looked" (flags stay on record)
     POST /records/{id}/hold       hold / release (audited)
     GET  /records/{id}/verification  original vs verified, side by side
   "Reviewed" and "held" are different things and stay different here: a held
   record is excluded from outreach until released; clearing a review never
   releases a hold. Nothing here overrides STOP, DNC, consent or suppression.
   The sidebar badge counts Location + Data + Duplicate review; holds are a
   separate queue. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import {
  Chip, Empty, ErrorLine, FilterChips, Icon, KV, Kpi, Loading, Notice, PageHead, Panel, SearchBox,
  TypedConfirm, errText, num, when,
} from './ui'

const QUEUES = [
  ['all', 'All staged'], ['location_review', 'Location issues'], ['data_review', 'Source data notes'],
  ['duplicate_review', 'Duplicates'], ['on_hold', 'On hold'], ['linked', 'Linked'],
]

function issueOf(r) {
  if (r.on_hold) return { text: `On hold${r.hold_reason ? ` — ${r.hold_reason}` : ''}`, pri: 'High', tone: 'bad', status: ['Held', 'bad'] }
  if (r.location_status !== 'mapped') return { text: 'No location assigned', pri: 'High', tone: 'bad', status: ['Blocked', 'bad'] }
  if (r.data_note_flags?.length) return { text: `Source data note: ${r.data_note_flags.join(', ')}`, pri: 'Medium', tone: 'warn', status: ['Review', 'warn'] }
  if (r.duplicate_review_reason) return { text: `Possible duplicate: ${r.duplicate_review_reason}`, pri: 'Medium', tone: 'warn', status: ['Review', 'warn'] }
  if (r.link_reason) return { text: r.link_reason, pri: 'Low', tone: '', status: ['Linked', 'info'] }
  return { text: 'No open issue', pri: 'Low', tone: 'ok', status: ['Clear', 'ok'] }
}

export default function Review({ locationId, locations, isManager, attention, initialQueue, initialSearch, onChange, navigate }) {
  const [queue, setQueue] = useState(initialQueue)
  const [search, setSearch] = useState(initialSearch || '')
  const [data, setData] = useState(null)
  const [cur, setCur] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [holdAllOpen, setHoldAllOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setQueue(initialQueue) }, [initialQueue])
  useEffect(() => { setSearch(initialSearch || '') }, [initialSearch])

  const load = useCallback(() => {
    const q = new URLSearchParams({ limit: '300' })
    if (queue && queue !== 'all') q.set('queue', queue)
    if (locationId) q.set('location_id', locationId)
    if (search.trim()) q.set('search', search.trim())
    api.get(`/program/records?${q}`).then(d => {
      setData(d); setErr('')
      setCur(c => (c ? d.items.find(x => x.id === c.id) || c : null))
    }).catch(e => { setErr(errText(e)); setData({ total: 0, items: [] }) })
  }, [queue, locationId, search])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])

  const after = text => { setMsg(text); load(); onChange() }
  const homes = locations.filter(l => !l.is_review_bucket)
  const holdAll = async () => {
    setBusy(true)
    try {
      const r = await api.post('/program/records/hold-open-reviews', {})
      setHoldAllOpen(false); setQueue('on_hold'); after(r.held ? `${num(r.held)} record(s) held.` : 'Nothing new to hold.')
    } catch (e) { setErr(errText(e)); setHoldAllOpen(false) } finally { setBusy(false) }
  }

  const counts = {
    data_review: attention.data_review || 0, location_review: attention.location_review || 0,
    duplicate_review: attention.duplicate_review || 0, on_hold: attention.on_hold || 0,
  }
  const pick = k => { setQueue(k); setCur(null); setMsg('') }
  const reviewTotal = counts.data_review + counts.location_review + counts.duplicate_review

  return (
    <>
      <PageHead title="Review Center" sub="Resolve records that need attention before anyone is contacted.">
        <Chip tone={reviewTotal ? 'warn' : 'ok'}>{reviewTotal ? `${num(reviewTotal)} need review` : 'Nothing to review'}</Chip>
        <button type="button" className="sci-btn" onClick={() => pick('on_hold')}><Icon name="pause" />Review holds</button>
        {isManager && <button type="button" className="sci-btn" onClick={() => setHoldAllOpen(true)}>Hold all open reviews…</button>}
      </PageHead>

      <section className="sci-kpis four" aria-label="Review counts">
        <Kpi icon="pin" tone="red" value={counts.location_review} label="Missing locations" note="Nothing is sent until assigned" onClick={() => pick('location_review')} />
        <Kpi icon="database" tone="gold" value={counts.data_review} label="Source data notes" note="Review before outreach" onClick={() => pick('data_review')} />
        <Kpi icon="users" tone="purple" value={counts.duplicate_review} label="Possible duplicates" note="Confirm or link" onClick={() => pick('duplicate_review')} />
        <Kpi icon="pause" tone="slate" value={counts.on_hold} label="Records on hold" note="Separate from the review badge" onClick={() => pick('on_hold')} />
      </section>

      <ErrorLine text={err} />
      <Notice text={msg} onClose={() => setMsg('')} />

      <div className="sci-grid-review">
        <Panel title="Review queue" labelledBy="sci-exc" aside={<span className="sci-micro sci-muted">{data ? `${num(data.total)} record(s)` : 'Loading…'}</span>}>
          <div className="sci-toolbar">
            <div className="sci-grow"><SearchBox id="sci-rev-search" value={search} onChange={v => { setSearch(v); setMsg('') }} placeholder="Name, email or Lead ID" label="Search records" width="100%" /></div>
          </div>
          <div style={{ marginBottom: 10 }}>
            <FilterChips items={QUEUES.map(([k, l]) => [k, l, counts[k]])} value={queue} onChange={pick} label="Queue" />
          </div>
          {!data ? <Loading /> : data.items.length === 0 ? (
            <Empty icon="check" title="Nothing in this queue.">Records that need a person show up here.</Empty>
          ) : (
            <div className="sci-tablewrap scroll">
              <table className="sci-table compact">
                <thead><tr><th>Priority</th><th>Contact</th><th>Issue</th><th>Location</th><th>Status</th><th><span className="sci-sr">Inspect</span></th></tr></thead>
                <tbody>
                  {data.items.map(r => {
                    const is = issueOf(r)
                    const open = cur?.id === r.id
                    return (
                      <tr key={r.id} className={`sci-click${open ? ' sci-open' : ''}`} tabIndex={0} aria-selected={open}
                        onClick={() => setCur(r)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCur(r) } }}>
                        <td><Chip tone={is.tone} plain>{is.pri}</Chip></td>
                        <td className="sci-cell-main"><b>{r.first_name} {r.last_name}</b><small>Lead {r.source_lead_id}</small></td>
                        <td className="sci-small" style={{ maxWidth: 260 }}>{is.text}</td>
                        <td className="sci-small">{r.location_status === 'mapped' ? r.location : <span className="sci-muted">{r.source_location_name || 'Not supplied'}</span>}</td>
                        <td><Chip tone={is.status[1]} plain>{is.status[0]}</Chip></td>
                        <td className="sci-chev"><Icon name="chevron" /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="sci-footnote"><Icon name="shield" />Safety gate: resolving a review never overrides STOP, do-not-call, consent or suppression. Every change is recorded.</p>
        </Panel>

        <Panel title="Resolve selected item" labelledBy="sci-resolve" className="sci-sticky">
          {!cur ? (
            <Empty icon="review" title="Select a record">Pick a row to see the source details, assign a location, mark it reviewed or hold it.</Empty>
          ) : (
            <Inspector key={cur.id} r={cur} homes={homes} isManager={isManager} navigate={navigate}
              onDone={after} onError={setErr} />
          )}
        </Panel>
      </div>

      <TypedConfirm open={holdAllOpen} title="Hold every open review?" phrase={null} actionLabel="Hold all open reviews" busy={busy}
        onCancel={() => setHoldAllOpen(false)} onConfirm={holdAll}>
        Every record still in Location, Duplicate or Data Review is put on hold. Nothing is deleted or changed; they are excluded from outreach until released.
      </TypedConfirm>
    </>
  )
}

function Inspector({ r, homes, isManager, navigate, onDone, onError }) {
  const is = issueOf(r)
  const [loc, setLoc] = useState('')
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [ver, setVer] = useState(undefined)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    api.get(`/program/records/${r.id}/verification`).then(v => { if (alive) setVer(v) }).catch(() => { if (alive) setVer(null) })
    return () => { alive = false }
  }, [r.id])
  const run = async (fn, okText) => {
    setBusy(true)
    try { await fn(); onDone(okText) } catch (e) { onError(errText(e)) } finally { setBusy(false) }
  }
  const assign = () => run(() => api.post(`/program/records/${r.id}/location`, { location_id: loc }), 'Location assigned. The source name is kept as supplied.')
  const clear = review => run(() => api.post(`/program/records/${r.id}/review`, { review, note: note.trim() || null }),
    'Marked reviewed. The original flags stay on the record.')
  const hold = on => run(() => api.post(`/program/records/${r.id}/hold`, { on_hold: on, reason: reason.trim() || null }),
    on ? 'Record held — excluded from outreach until released.' : 'Hold released.')

  return (
    <div className="sci-stack" style={{ gap: 12 }}>
      <div>
        <div className="sci-row-gap" style={{ marginBottom: 4 }}><Chip tone={is.tone} plain>{is.pri} priority</Chip><Chip tone={is.status[1]} plain>{is.status[0]}</Chip></div>
        <h3 style={{ fontSize: 18, fontWeight: 800, letterSpacing: '-.3px' }}>{r.first_name} {r.last_name}</h3>
        <div className="sci-micro sci-muted">Lead {r.source_lead_id}{r.lead_id ? ' · promoted to a lead' : ' · staged only'}</div>
      </div>
      <div className={`sci-banner ${is.tone === 'bad' ? 'bad' : is.tone === 'warn' ? '' : 'info'}`} style={{ margin: 0 }}>
        <div className="sci-banner-body sci-micro"><strong>Issue</strong>{is.text}</div>
      </div>

      <KV rows={[
        ['Email', r.email], ['Phone', r.phone],
        ['Status (as supplied)', r.source_status], ['Campaign', r.campaign_family],
        ['Source location', r.source_location_name], ['Mapped location', r.location_status === 'mapped' ? r.location : null],
        ['Link', ['primary', 'linked'].includes(r.link_status) ? `${r.link_status}${r.link_reason ? ` — ${r.link_reason}` : ''}` : null],
        r.on_hold ? ['Held', `${when(r.held_at)}${r.hold_reason ? ` — ${r.hold_reason}` : ''}`] : null,
      ]} />

      {ver?.verified && (
        <details className="sci-details" open>
          <summary>Verification (stored beside the original)</summary>
          <div className="sci-details-body">
            <KV rows={[['Verified phone', ver.verified.verified_phone], ['Verified email', ver.verified.verified_email],
              ['Provider', ver.verified.provider], ['Verified', when(ver.verified.verified_at)]]} />
            <p className="sci-micro" style={{ marginTop: 6 }}>A verification never grants outreach on its own.</p>
          </div>
        </details>
      )}

      {isManager ? (
        <>
          {r.location_status !== 'mapped' && (
            <div className="sci-field">
              <label className="sci-field-label" htmlFor={`sci-assign-${r.id}`}>Assign location</label>
              <div className="sci-row-gap" style={{ flexWrap: 'nowrap' }}>
                <select id={`sci-assign-${r.id}`} className="sci-input" value={loc} onChange={e => setLoc(e.target.value)} style={{ flex: 1, width: 0 }}>
                  <option value="">Select a verified location…</option>
                  {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                </select>
                <button type="button" className="sci-btn primary" disabled={!loc || busy} onClick={assign}>Assign</button>
              </div>
            </div>
          )}
          {(r.data_note_flags?.length > 0 || r.duplicate_review_reason) && (
            <div className="sci-field">
              <label className="sci-field-label" htmlFor={`sci-note-${r.id}`}>Resolution note (kept in the audit log)</label>
              <textarea id={`sci-note-${r.id}`} className="sci-input" rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="What you checked" />
              <div className="sci-row-gap" style={{ marginTop: 6 }}>
                {r.data_note_flags?.length > 0 && <button type="button" className="sci-btn" disabled={busy} onClick={() => clear('data_review')}><Icon name="check" />Mark data note reviewed</button>}
                {r.duplicate_review_reason && <button type="button" className="sci-btn" disabled={busy} onClick={() => clear('duplicate_review')}><Icon name="check" />Mark duplicate reviewed</button>}
              </div>
            </div>
          )}
          <div className="sci-field">
            <label className="sci-field-label" htmlFor={`sci-reason-${r.id}`}>{r.on_hold ? 'Hold' : 'Hold reason (optional)'}</label>
            {!r.on_hold && <input id={`sci-reason-${r.id}`} className="sci-input" value={reason} onChange={e => setReason(e.target.value)} placeholder="Why this should wait" />}
            <div className="sci-row-gap" style={{ marginTop: 6 }}>
              {r.on_hold
                ? <button type="button" className="sci-btn gold" disabled={busy} onClick={() => hold(false)}>Release hold</button>
                : <button type="button" className="sci-btn" disabled={busy} onClick={() => hold(true)}><Icon name="pause" />Keep on hold</button>}
            </div>
          </div>
        </>
      ) : <p className="sci-micro sci-muted">Only a workspace manager can assign, clear or hold records.</p>}

      {r.lead_id && <button type="button" className="sci-btn block" onClick={() => navigate(`/program?tab=leads&person=${r.lead_id}`)}><Icon name="external" />Open full record</button>}
      <div className="sci-banner" style={{ margin: 0 }}>
        <span className="sci-banner-icon" aria-hidden="true"><Icon name="lock" /></span>
        <div className="sci-banner-body sci-micro">Resolving a data issue never bypasses consent, STOP, do-not-call or suppression.</div>
      </div>
    </div>
  )
}
