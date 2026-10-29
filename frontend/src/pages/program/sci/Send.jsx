/* Choose & Send - a controlled, per-person approval flow (not a bulk campaign
   builder). 01 tick contacts -> 02 read exactly what each will receive ->
   03 approve with the typed confirmation "SEND <n>". Only approved contacts
   ever get an automated text or email; the server enforces it
   (programs/send_approval.py + identity.send_refusal) and re-checks hold,
   review, STOP, DNC, suppression, consent and location on every send.
   The per-approval maximum is the server's (max_per_approval), never a
   number baked into this page. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import {
  Chip, Empty, ErrorLine, FilterChips, Icon, Kpi, Loading, Notice, PageHead, Panel, SearchBox,
  errText, num, when,
} from './ui'

const SHOW = [['not_approved', 'Not sent yet'], ['approved', 'Approved'], ['all', 'Everyone']]
const PAGE = 50

export default function Send({ locations, isManager, locationId, goTab, automation, openPerson }) {
  const [searchParams] = useSearchParams()
  const requestedCampaign = searchParams.get('campaign') || ''
  const [show, setShow] = useState(requestedCampaign ? 'approved' : 'not_approved')
  const [search, setSearch] = useState('')
  const [loc, setLoc] = useState(locationId || '')
  const [fam, setFam] = useState(requestedCampaign)
  const [fams, setFams] = useState([])
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [totals, setTotals] = useState({ all: null, approved: null })
  const [max, setMax] = useState(null)
  const [picked, setPicked] = useState({})          // lead_id -> name
  const [preview, setPreview] = useState(null)      // preview of the ticked set
  const [focus, setFocus] = useState(null)          // { row, item } single-contact preview
  const [confirming, setConfirming] = useState(false)
  const [mode, setMode] = useState('text_email')
  const [drip, setDrip] = useState('today')
  // Slow drip: tomorrow 9:00 AM (this computer's clock), one contact every 20 minutes.
  // "Today": starts in 5 minutes - or right after the last first message
  // already scheduled today, so batches line up instead of piling on - then
  // one every 8 minutes.
  const dripBody = async () => {
    if (drip === 'tomorrow') {
      const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0)
      return { start_at: d.toISOString(), spacing_minutes: 20 }
    }
    if (drip === 'today') {
      let start = Date.now() + 5 * 60000
      try {
        const ob = await api.get('/program/outbox')
        for (const u of ob.upcoming || []) {
          if (u.what === 'First message' && u.at) start = Math.max(start, new Date(u.at).getTime() + 8 * 60000)
        }
      } catch { /* no schedule read - start in 5 minutes */ }
      return { start_at: new Date(start).toISOString(), spacing_minutes: 8 }
    }
    return {}
  }
  // "Pick 25 for me": the next people never contacted, on a confirmed cell
  // phone, in a campaign that is switched on - one per location in turn so a
  // batch is spread across locations. Only ticks them; nothing is sent until
  // Good to go is pressed.
  const autoPick = async () => {
    setBusy(true); setErr(''); setMsg(''); setPreview(null)
    try {
      const active = new Set((fams || []).filter(f => f.is_active).map(f => f.key))
      let rows = []
      for (let off = 0; off < 3000; off += 300) {
        const d = await api.get(`/program/send/contacts?show=not_approved&limit=300&offset=${off}`)
        rows = rows.concat(d.items || [])
        if ((d.items || []).length < 300) break
      }
      const pool = rows.filter(r => !r.approved && !r.blocked && r.line_type === 'mobile' && active.has(r.campaign_key)
        && !r.last_text_at && !r.last_email_at && r.status === 'new' && !picked[r.lead_id])
      const byLoc = {}
      pool.forEach(r => { (byLoc[r.location_id] = byLoc[r.location_id] || []).push(r) })
      const want = Math.max(0, (max || 25) - ids.length)
      const out = []
      for (let i = 0; out.length < want; i++) {
        let any = false
        for (const k of Object.keys(byLoc)) {
          if (byLoc[k][i]) { out.push(byLoc[k][i]); any = true; if (out.length >= want) break }
        }
        if (!any) break
      }
      setPicked(p => { const n = { ...p }; out.forEach(r => { n[r.lead_id] = r.name }); return n })
      setMsg(out.length ? `Ticked ${out.length} people across ${new Set(out.map(r => r.location_id)).size} locations. Nothing is sent until you press Good to go.`
        : 'Nobody left who has not been contacted on a cell phone in an active campaign.')
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api.get('/program/campaigns').then(d => setFams(Array.isArray(d) ? d : [])).catch(() => {})
    // Read-only: an empty preview returns the server's per-approval maximum and sends nothing.
    api.post('/program/send/preview', { lead_ids: [] }).then(p => setMax(p.max_per_approval ?? null)).catch(() => {})
  }, [])
  useEffect(() => { setLoc(locationId || '') }, [locationId])

  const loadTotals = useCallback(() => {
    Promise.all(['all', 'approved'].map(s => api.get(`/program/send/contacts?show=${s}&limit=1`).then(d => d.total).catch(() => null)))
      .then(([all, approved]) => setTotals({ all, approved }))
  }, [])
  useEffect(() => { loadTotals() }, [loadTotals])

  const load = useCallback(() => {
    const q = new URLSearchParams({ show, limit: String(PAGE), offset: String(offset) })
    if (search.trim()) q.set('search', search.trim())
    if (loc) q.set('location_id', loc)
    if (fam) q.set('family', fam)
    api.get(`/program/send/contacts?${q}`).then(d => { setData(d); setErr('') }).catch(e => setErr(errText(e)))
  }, [show, search, loc, fam, offset])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])
  useEffect(() => { setOffset(0) }, [show, search, loc, fam])

  const ids = Object.keys(picked)
  const overMax = max != null && ids.length > max
  const toggle = row => {
    setPreview(null)
    setPicked(p => {
      const n = { ...p }
      if (n[row.lead_id]) delete n[row.lead_id]; else n[row.lead_id] = row.name
      return n
    })
  }
  const pickable = (data?.items || []).filter(r => !r.approved && !r.blocked)
  const allOnPage = pickable.length > 0 && pickable.every(r => picked[r.lead_id])
  const togglePage = () => {
    setPreview(null)
    setPicked(p => {
      const n = { ...p }
      if (allOnPage) pickable.forEach(r => { delete n[r.lead_id] })
      else pickable.forEach(r => { n[r.lead_id] = r.name })
      return n
    })
  }

  const inspect = async row => {
    setFocus({ row, item: null })
    try {
      const p = await api.post('/program/send/preview', { lead_ids: [row.lead_id] })
      setFocus({ row, item: p.items?.[0] || null, emailOn: p.email_sending_on })
    } catch (e) { setFocus({ row, item: null, error: errText(e) }) }
  }
  const openPreview = async () => {
    setBusy(true); setErr(''); setMsg('')
    try { const p = await api.post('/program/send/preview', { lead_ids: ids }); setPreview(p); if (p.max_per_approval != null) setMax(p.max_per_approval) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const approve = async typed => {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/program/send/approve', { lead_ids: ids, confirm: typed, mode, ...(await dripBody()) })
      const parts = [`${num(r.approved)} approved.`]
      if (r.start_at) parts.push(`Slow drip: the first one starts ${when(r.start_at)}, then one every ${r.spacing_minutes} minutes. Nothing goes out before then.`)
      else if (r.texting) parts.push(`${num(r.texting)} will get their first text at the next allowed hour in their own time zone (never at night).`)
      if (r.email_only) parts.push(`${num(r.email_only)} have email only.`)
      parts.push(r.email_sending_on ? 'Emails go out on the email schedule.' : 'Emails are switched off on the server right now, so only texts go for now.')
      if (r.skipped?.length) parts.push(`${num(r.skipped.length)} skipped: ${r.skipped.map(s => s.reason).join(', ')}.`)
      setMsg(parts.join(' ')); setPicked({}); setPreview(null); setConfirming(false); load(); loadTotals()
    } catch (e) { setErr(errText(e)); setConfirming(false) } finally { setBusy(false) }
  }
  const approveOne = async row => {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/program/send/approve', { lead_ids: [row.lead_id], confirm: 'SEND 1', mode, ...(await dripBody()) })
      setMsg(r.approved ? `${row.name} is good to go — first text at the next allowed hour in their time zone.`
        : `${row.name} was not sent: ${(r.skipped || []).map(s => s.reason).join(', ') || 'blocked by a send check'}.`)
      load(); loadTotals()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const unapprove = async row => {
    try {
      await api.post('/program/send/unapprove', { lead_ids: [row.lead_id] })
      setMsg(`${row.name}: approval taken back — no more automated messages.`); load(); loadTotals()
    } catch (e) { setErr(errText(e)) }
  }

  const homes = (locations || []).filter(l => !l.is_review_bucket)
  const ready = (preview?.items || []).filter(i => !i.blocked)
  const blockedInPreview = (preview?.items || []).filter(i => i.blocked)
  const step = ids.length ? 2 : 1
  const sendingOn = (automation?.active_campaign_families || 0) > 0
  const showLabel = useMemo(() => SHOW.find(s => s[0] === show)?.[1], [show])

  return (
    <>
      <PageHead title="Choose & Send" sub="Select individual contacts, review exactly what each will receive, then approve.">
        <Chip tone={sendingOn ? 'ok' : 'bad'}>{sendingOn ? `${automation.active_campaign_families} campaign(s) on` : 'All campaigns off'}</Chip>
        <button type="button" className="sci-btn" onClick={() => goTab('launch')}><Icon name="launch" />Launch readiness</button>
      </PageHead>

      <ol className="sci-steps" aria-label="Approval steps">
        {[['Choose contacts', 1], ['Good to go', 2]].map(([label, n]) => (
          <li key={n} className={step === n ? 'on' : step > n ? 'done' : ''} aria-current={step === n ? 'step' : undefined}>
            <span>{step > n ? <Icon name="check" size={13} /> : `0${n}`}</span>{label}
          </li>
        ))}
      </ol>

      <section className="sci-kpis four" aria-label="Send numbers">
        <Kpi icon="users" tone="blue" value={totals.all} label="Contacts in program" note="One row per person" onClick={() => setShow('all')} />
        <Kpi icon="check" tone="green" value={totals.approved} label="Approved for outreach" note="Getting automated messages" onClick={() => setShow('approved')} />
        <Kpi icon="review" tone="gold" value={ids.length} label="Selected in this session" note={ids.length ? 'Not sent until you press Good to go' : 'Tick contacts below'} />
        <Kpi icon="shield" tone="purple" value={max ?? '—'} label="Max per approval" note="Set by the server" />
      </section>

      <ErrorLine text={err} />
      <Notice text={msg} onClose={() => setMsg('')} />

      <div className="sci-grid-send">
        <Panel title="Eligible contacts" labelledBy="sci-send"
          aside={<span className="sci-micro sci-muted">{data ? `${num(data.total)} · ${showLabel}` : 'Loading…'}</span>}>
          <div className="sci-toolbar">
            <div className="sci-grow"><SearchBox id="sci-send-search" value={search} onChange={setSearch} placeholder="Name, phone, email or Lead ID" label="Search contacts" width="100%" /></div>
            <label className="sci-sr" htmlFor="sci-send-loc">Location</label>
            <select id="sci-send-loc" className="sci-input sm" value={loc} onChange={e => setLoc(e.target.value)}>
              <option value="">All locations</option>
              {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
            <label className="sci-sr" htmlFor="sci-send-fam">Campaign</label>
            <select id="sci-send-fam" className="sci-input sm" value={fam} onChange={e => setFam(e.target.value)}>
              <option value="">All campaigns</option>
              {fams.map(f => <option key={f.key} value={f.key}>{f.name}</option>)}
            </select>
          </div>
          <div style={{ marginBottom: 10 }}><FilterChips items={SHOW} value={show} onChange={setShow} label="Show" /></div>

          {!data ? <Loading /> : !data.items.length ? (
            <Empty icon="users" title="No contacts here">{show === 'not_approved' ? 'Everyone in this view is already approved, or nothing has been loaded yet.' : 'Nothing matches these filters.'}</Empty>
          ) : (
            <div className="sci-tablewrap scroll">
              <table className="sci-table compact">
                <thead>
                  <tr>
                    <th style={{ width: 34 }}>{isManager && pickable.length > 0 && (
                      <input type="checkbox" aria-label="Tick everyone selectable on this page" checked={allOnPage} onChange={togglePage} />)}</th>
                    <th>Contact</th><th>Location</th><th>Campaign</th><th>Status</th><th><span className="sci-sr">Action</span></th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map(r => (
                    <tr key={r.lead_id} className={`${picked[r.lead_id] ? 'sci-sel' : ''}${focus?.row.lead_id === r.lead_id ? ' sci-open' : ''}`}>
                      <td>{isManager && !r.approved && !r.blocked && (
                        <input type="checkbox" aria-label={`Tick ${r.name}`} checked={!!picked[r.lead_id]} onChange={() => toggle(r)} />)}</td>
                      <td className="sci-cell-main">
                        <button type="button" className="sci-namebtn" onClick={() => inspect(r)} title="See exactly what this person would receive">{r.name}</button>
                        <small>{[r.phone, r.email].filter(Boolean).join(' · ') || 'No contact details'} · Lead {r.source_lead_id}</small>
                        {r.line_type === 'landline' && <Chip tone="warn" plain>Landline — no texts, call instead</Chip>}
                      </td>
                      <td className="sci-small">{r.location || <Chip tone="warn" plain>No location</Chip>}</td>
                      <td className="sci-small">{r.campaign || '—'}
                        <span className="sci-micro sci-muted" style={{ display: 'block' }}>
                          {r.reaches}{r.starts_at ? ` · Starts ${when(r.starts_at)}` : ''}{r.last_text_at ? ` · Last text ${when(r.last_text_at)}` : ''}{r.last_email_at ? ` · Last email ${when(r.last_email_at)}` : ''}
                        </span>
                      </td>
                      <td>
                        {r.approved ? <Chip tone="ok" plain>Good to go ✓ {when(r.approved_at)}</Chip>
                          : r.blocked ? <Chip tone="bad" plain title="The server will not send to this contact">{r.blocked}</Chip>
                            : isManager
                              ? <button type="button" className="sci-btn sm gold" disabled={busy} onClick={() => approveOne(r)}>✓ Good to go</button>
                              : <span className="sci-micro sci-muted">Waiting on Mike</span>}
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <button type="button" className="sci-btn sm" onClick={() => openPerson && openPerson(r.lead_id)} style={{ marginRight: 6 }}>Open</button>
                        {isManager && r.approved && <button type="button" className="sci-btn sm danger" onClick={() => unapprove(r)}>Stop</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data && data.total > PAGE && (
            <div className="sci-pager">
              <button type="button" className="sci-btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
              <span className="sci-micro sci-muted">{num(offset + 1)}–{num(Math.min(offset + PAGE, data.total))} of {num(data.total)}</span>
              <button type="button" className="sci-btn sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
            </div>
          )}
          <p className="sci-footnote"><Icon name="lock" />Blocked contacts can't be ticked. Hold, review, STOP, do-not-call and missing location always win.</p>
        </Panel>

        <Panel title="Approval workspace" labelledBy="sci-approve" className="sci-sticky">
          <div className="sci-bignum"><b>{num(ids.length)}</b><span>Selected recipients{max != null ? ` · at most ${max} per approval` : ''}</span></div>
          {!isManager ? (
            <div className="sci-banner info"><div className="sci-banner-body sci-micro">Only a workspace manager can approve contacts for outreach. You can still read each contact's message.</div></div>
          ) : (
            null
          )}
          {overMax && <div className="sci-alert" role="alert"><Icon name="alert" />Approve at most {max} at a time — untick {ids.length - max}.</div>}

          {ids.length > 0 && (
            <div className="sci-picked" aria-label="Selected contacts">
              {ids.slice(0, 12).map(id => (
                <span key={id} className="sci-pickchip">{picked[id]}
                  <button type="button" aria-label={`Untick ${picked[id]}`} onClick={() => toggle({ lead_id: id, name: picked[id] })}><Icon name="x" size={11} /></button>
                </span>
              ))}
              {ids.length > 12 && <span className="sci-micro sci-muted">+{ids.length - 12} more</span>}
            </div>
          )}

          <h3 className="sci-h3" style={{ marginTop: 12 }}>Message preview</h3>
          {preview ? (
            <div aria-live="polite">
              <p className="sci-micro sci-muted" style={{ marginBottom: 6 }}>
                {num(ready.length)} will get outreach{blockedInPreview.length ? ` · ${blockedInPreview.length} won't send` : ''}.
                {!preview.email_sending_on && ' Email is switched off on the server, so only texts go.'}
              </p>
              <div className="sci-previewlist">
                {preview.items.map(i => (
                  <div key={i.lead_id} className="sci-previewitem">
                    <div className="sci-row-gap" style={{ justifyContent: 'space-between' }}>
                      <b>{i.name || '—'}</b>
                      <span className="sci-micro sci-muted">{i.location || 'No location'}</span>
                    </div>
                    {i.blocked ? <Chip tone="bad" plain>Won't send: {i.blocked}</Chip> : (
                      <>
                        <div className="sci-preview-sms sm">{i.gets_text ? (i.text || 'No text template for this campaign') : 'No mobile number — no text'}</div>
                        <div className="sci-micro sci-muted">Email: {i.gets_email ? 'yes, on the email schedule' : 'no'}</div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : focus ? (
            <FocusPreview f={focus} onClose={() => setFocus(null)} />
          ) : (
            <div className="sci-identity" style={{ alignItems: 'flex-start' }}>
              <span className="sci-identity-mark" aria-hidden="true"><Icon name="pin" /></span>
              <div className="sci-micro sci-muted">Click a contact's name to load their exact message, or tick contacts and press <b>Preview selected</b>. Nothing is sent from a preview.</div>
            </div>
          )}

          {isManager && (
            <label className="sci-micro" style={{ display: 'block', marginTop: 12 }}>Campaign type{' '}
              <select value={mode} onChange={e => setMode(e.target.value)} style={{ marginLeft: 6 }}>
                <option value="text_email">Text + Email (10-day)</option>
                <option value="email_only">Email only</option>
                <option value="call">Call list (no automated messages)</option>
              </select>
            </label>
          )}
          {isManager && (
            <label className="sci-micro" style={{ display: 'block', marginTop: 8 }}>When{' '}
              <select value={drip} onChange={e => setDrip(e.target.value)} style={{ marginLeft: 6 }}>
                <option value="now">Right away</option>
                <option value="today">Today, spread out — one every 8 min</option>
                <option value="tomorrow">Slow drip — tomorrow 9 AM, one every 20 min</option>
              </select>
            </label>
          )}
          {isManager && (
            <div className="sci-row-gap" style={{ marginTop: 14 }}>
              <button type="button" className="sci-btn sm" disabled={busy || (max != null && ids.length >= max)} onClick={autoPick}>
                Pick {max || 25} for me
              </button>
              <button type="button" className="sci-btn gold" disabled={!ids.length || overMax || busy}
                onClick={() => approve(`SEND ${ids.length}`)}>
                <Icon name="check" />{busy ? 'Sending…' : `Good to go — ${mode === 'email_only' ? 'email' : mode === 'call' ? 'call list' : 'send'} (${num(ids.length)})`}
              </button>
              {!preview
                ? <button type="button" className="sci-btn sm" disabled={!ids.length || overMax || busy} onClick={openPreview}>Preview first</button>
                : <button type="button" className="sci-btn sm" onClick={() => setPreview(null)} disabled={busy}>Hide preview</button>}
              {ids.length > 0 && <button type="button" className="sci-btn sm" onClick={() => { setPicked({}); setPreview(null) }}>Clear</button>}
            </div>
          )}
          <p className="sci-footnote">Good to go starts their texts right away (first text at the next allowed hour in their time zone). A reply or STOP ends them.</p>
          {isManager && <DailyBatch />}
          {isManager && <CentralAlabamaPrepare />}
        </Panel>
      </div>

    </>
  )
}

// CENTRAL ALABAMA 2025-2026 MS: one click re-processes the list under the
// owner's rules, adds only new people, and records them Approved to Send for
// that campaign. The campaign stays OFF; nothing is texted, emailed or
// scheduled (POST /program/central-alabama/prepare).
function CentralAlabamaPrepare() {
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.post('/program/central-alabama/prepare', { apply: false }).then(setRes).catch(() => {})
  }, [])
  const run = async () => {
    if (busy) return
    setBusy(true); setErr('')
    try { setRes(await api.post('/program/central-alabama/prepare', { apply: true })) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const a = res?.after || res?.before
  return (
    <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
      <div className="sci-row-gap" style={{ justifyContent: 'space-between' }}>
        <b>Central Alabama 2025–2026 MS</b>
        <Chip tone="">Campaign off</Chip>
      </div>
      <p className="sci-micro sci-muted" style={{ margin: '6px 0 10px' }}>
        Gets the list ready: people already in SCI stay in their own campaign, only new people are added, and they are marked Approved to Send. Nothing is sent - the campaign stays off.
      </p>
      {a && (
        <p className="sci-micro" style={{ margin: '0 0 10px' }}>
          {num(a.source_records)} records · <b>{num(a.approved_people)}</b> people approved to send · {num(a.excluded_existing_people)} already in SCI · {num(a.location_review_records)} in location review
        </p>
      )}
      <button type="button" className="sci-btn gold sm" disabled={busy} onClick={run}>
        {busy ? 'Preparing…' : 'Prepare Central Alabama'}
      </button>
      {res?.applied && (
        <p className="sci-micro" role="status" style={{ marginTop: 8 }}>
          Done: {num(res.approved_to_send_people)} people approved to send ({num(res.newly_approved_people)} new this time). Nothing was sent.
        </p>
      )}
      {err && <p className="sci-micro" role="alert" style={{ marginTop: 8, color: '#b42318' }}>Not prepared: {err}</p>}
    </div>
  )
}

// EVERY DAY, AUTOMATICALLY: a standing approval. Once a manager switches it
// on, each sending day the program approves the next N people itself (same
// people "Pick 25 for me" would tick), spread out from the start time.
function DailyBatch() {
  const [d, setD] = useState(null)
  const [count, setCount] = useState(66)
  const [spacing, setSpacing] = useState(8)
  const [start, setStart] = useState('09:00')
  const [sat, setSat] = useState(false)
  const [sure, setSure] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  useEffect(() => {
    api.get('/program/daily-batch').then(x => {
      setD(x)
      if (x.count) setCount(x.count)
      if (x.spacing_minutes) setSpacing(x.spacing_minutes)
      if (x.start) setStart(x.start)
      setSat((x.days || '').includes('6'))
    }).catch(() => {})
  }, [])
  const save = async on => {
    setBusy(true); setNote('')
    try {
      const n = on ? Math.max(1, Math.min(200, Number(count) || 0)) : 0
      const x = await api.post('/program/daily-batch', { count: n, spacing_minutes: Number(spacing) || 8, start,
        days: sat ? '123456' : '12345', confirm: on ? `DAILY ${n}` : undefined })
      setD(x); setSure(false)
      setNote(on ? `On: ${n} new people every ${sat ? 'Mon–Sat' : 'weekday'} from ${start}, one every ${x.spacing_minutes} min. If today's batch hasn't gone yet, it starts within a few minutes.`
        : 'Off. No new daily batches; people already approved keep their schedule.')
    } catch (e) { setNote(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="sci-daily" style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
      <div className="sci-row-gap" style={{ justifyContent: 'space-between' }}>
        <b>Every day, automatically</b>
        <Chip tone={d?.on ? 'ok' : ''}>{d?.on ? `On · ${d.count}/day` : 'Off'}</Chip>
      </div>
      <p className="sci-micro sci-muted" style={{ margin: '6px 0 10px' }}>
        Sends the next new people (cell phones, active campaigns, never contacted) every sending day, spread across locations. No taps needed after this.
      </p>
      <div className="sci-row-gap" style={{ flexWrap: 'wrap' }}>
        <label className="sci-micro">People a day <input type="number" min="1" max="200" value={count} onChange={e => setCount(e.target.value)} className="sci-input sm" style={{ width: 70 }} /></label>
        <label className="sci-micro">Start <input type="time" value={start} onChange={e => setStart(e.target.value)} className="sci-input sm" /></label>
        <label className="sci-micro">Every <input type="number" min="1" max="240" value={spacing} onChange={e => setSpacing(e.target.value)} className="sci-input sm" style={{ width: 56 }} /> min</label>
        <label className="sci-micro"><input type="checkbox" checked={sat} onChange={e => setSat(e.target.checked)} /> Saturdays too</label>
      </div>
      <label className="sci-micro" style={{ display: 'block', marginTop: 10 }}>
        <input type="checkbox" checked={sure} onChange={e => setSure(e.target.checked)} /> I approve texting and emailing {Number(count) || 0} new people every {sat ? 'Mon–Sat' : 'weekday'}.
      </label>
      <div className="sci-row-gap" style={{ marginTop: 10 }}>
        <button type="button" className="sci-btn gold sm" disabled={!sure || busy} onClick={() => save(true)}>{d?.on ? 'Save' : 'Turn on'}</button>
        {d?.on && <button type="button" className="sci-btn sm danger" disabled={busy} onClick={() => save(false)}>Turn off</button>}
      </div>
      {note && <p className="sci-micro" style={{ marginTop: 8 }}>{note}</p>}
      {d?.on && d.set_by_name && <p className="sci-micro sci-muted" style={{ marginTop: 4 }}>Switched on by {d.set_by_name}{d.last_date ? ` · last batch ${d.last_date}` : ''}.</p>}
    </div>
  )
}

function FocusPreview({ f, onClose }) {
  const i = f.item
  return (
    <div className="sci-previewitem" aria-live="polite">
      <div className="sci-row-gap" style={{ justifyContent: 'space-between' }}>
        <b>{f.row.name}</b>
        <button type="button" className="sci-x" aria-label="Close preview" onClick={onClose}><Icon name="x" /></button>
      </div>
      <div className="sci-micro sci-muted">{f.row.location || 'No location'} · {f.row.campaign || 'No campaign'} · Lead {f.row.source_lead_id}</div>
      {f.error ? <ErrorLine text={f.error} /> : !i ? <Loading label="Rendering their message…" /> : i.blocked ? (
        <Chip tone="bad" plain>Won't send: {i.blocked}</Chip>
      ) : (
        <>
          <p className="sci-preview-label"><Icon name="sms" size={12} />First text</p>
          <div className="sci-preview-sms sm">{i.gets_text ? (i.text || 'No text template for this campaign') : 'No mobile number — no text'}</div>
          <p className="sci-preview-label"><Icon name="mail" size={12} />Email</p>
          <div className="sci-micro">{i.gets_email ? (f.emailOn ? 'Yes — on the email schedule' : 'Yes, once email is switched on for this deployment') : 'No email address or email not allowed'}</div>
        </>
      )}
    </div>
  )
}
