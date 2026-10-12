/* Choose & Send - a controlled, per-person approval flow (not a bulk campaign
   builder). 01 tick contacts -> 02 read exactly what each will receive ->
   03 approve with the typed confirmation "SEND <n>". Only approved contacts
   ever get an automated text or email; the server enforces it
   (programs/send_approval.py + identity.send_refusal) and re-checks hold,
   review, STOP, DNC, suppression, consent and location on every send.
   The per-approval maximum is the server's (max_per_approval), never a
   number baked into this page. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import {
  Chip, Empty, ErrorLine, FilterChips, Icon, Kpi, Loading, Notice, PageHead, Panel, SearchBox,
  TypedConfirm, errText, num, when,
} from './ui'

const SHOW = [['not_approved', 'Not sent yet'], ['approved', 'Approved'], ['all', 'Everyone']]
const PAGE = 50

export default function Send({ locations, isManager, locationId, goTab, automation }) {
  const [show, setShow] = useState('not_approved')
  const [search, setSearch] = useState('')
  const [loc, setLoc] = useState(locationId || '')
  const [fam, setFam] = useState('')
  const [fams, setFams] = useState([])
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [totals, setTotals] = useState({ all: null, approved: null })
  const [max, setMax] = useState(null)
  const [picked, setPicked] = useState({})          // lead_id -> name
  const [preview, setPreview] = useState(null)      // preview of the ticked set
  const [focus, setFocus] = useState(null)          // { row, item } single-contact preview
  const [confirming, setConfirming] = useState(false)
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
      const r = await api.post('/program/send/approve', { lead_ids: ids, confirm: typed })
      const parts = [`${num(r.approved)} approved.`]
      if (r.texting) parts.push(`${num(r.texting)} will get their first text at the next allowed hour in their own time zone (never at night).`)
      if (r.email_only) parts.push(`${num(r.email_only)} have email only.`)
      parts.push(r.email_sending_on ? 'Emails go out on the email schedule.' : 'Emails are switched off on the server right now, so only texts go for now.')
      if (r.skipped?.length) parts.push(`${num(r.skipped.length)} skipped: ${r.skipped.map(s => s.reason).join(', ')}.`)
      setMsg(parts.join(' ')); setPicked({}); setPreview(null); setConfirming(false); load(); loadTotals()
    } catch (e) { setErr(errText(e)); setConfirming(false) } finally { setBusy(false) }
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
  const step = confirming ? 3 : preview ? 2 : 1
  const sendingOn = (automation?.active_campaign_families || 0) > 0
  const showLabel = useMemo(() => SHOW.find(s => s[0] === show)?.[1], [show])

  return (
    <>
      <PageHead title="Choose & Send" sub="Select individual contacts, review exactly what each will receive, then approve.">
        <Chip tone={sendingOn ? 'ok' : 'bad'}>{sendingOn ? `${automation.active_campaign_families} campaign(s) on` : 'All campaigns off'}</Chip>
        <button type="button" className="sci-btn" onClick={() => goTab('launch')}><Icon name="launch" />Launch readiness</button>
      </PageHead>

      <ol className="sci-steps" aria-label="Approval steps">
        {[['Choose contacts', 1], ['Preview messages', 2], ['Approve recipients', 3]].map(([label, n]) => (
          <li key={n} className={step === n ? 'on' : step > n ? 'done' : ''} aria-current={step === n ? 'step' : undefined}>
            <span>{step > n ? <Icon name="check" size={13} /> : `0${n}`}</span>{label}
          </li>
        ))}
      </ol>

      <section className="sci-kpis four" aria-label="Send numbers">
        <Kpi icon="users" tone="blue" value={totals.all} label="Contacts in program" note="One row per person" onClick={() => setShow('all')} />
        <Kpi icon="check" tone="green" value={totals.approved} label="Approved for outreach" note="Getting automated messages" onClick={() => setShow('approved')} />
        <Kpi icon="review" tone="gold" value={ids.length} label="Selected in this session" note={ids.length ? 'Not approved until step 03' : 'Tick contacts below'} />
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
                      </td>
                      <td className="sci-small">{r.location || <Chip tone="warn" plain>No location</Chip>}</td>
                      <td className="sci-small">{r.campaign || '—'}</td>
                      <td>
                        {r.approved ? <Chip tone="ok" plain>Approved {when(r.approved_at)}</Chip>
                          : r.blocked ? <Chip tone="bad" plain title="The server will not send to this contact">{r.blocked}</Chip>
                            : <Chip tone="info" plain>Not approved</Chip>}
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
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
            <div className="sci-banner"><span className="sci-banner-icon" aria-hidden="true"><Icon name="shield" /></span>
              <div className="sci-banner-body sci-micro">Each selected contact must pass the server's consent, suppression, location and send-gate checks.</div></div>
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
              <span className="sci-identity-mark">SCI</span>
              <div className="sci-micro sci-muted">Click a contact's name to load their exact message, or tick contacts and press <b>Preview selected</b>. Nothing is sent from a preview.</div>
            </div>
          )}

          {isManager && (
            <div className="sci-row-gap" style={{ marginTop: 14 }}>
              {!preview ? (
                <button type="button" className="sci-btn gold" disabled={!ids.length || overMax || busy} onClick={openPreview}>
                  <Icon name="doc" />{busy ? 'Loading…' : `Preview selected (${num(ids.length)})`}
                </button>
              ) : (
                <>
                  <button type="button" className="sci-btn" onClick={() => setPreview(null)} disabled={busy}>Back</button>
                  <button type="button" className="sci-btn gold" disabled={busy || !ready.length || overMax} onClick={() => setConfirming(true)}>
                    <Icon name="check" />Approve selected…
                  </button>
                </>
              )}
              {ids.length > 0 && <button type="button" className="sci-btn sm" onClick={() => { setPicked({}); setPreview(null) }}>Clear</button>}
            </div>
          )}
          <p className="sci-footnote">Approval is confirmation-gated. Follow-up texts continue on the SCI cadence; a reply or STOP ends them.</p>
        </Panel>
      </div>

      <TypedConfirm open={confirming} title={`Approve ${num(ids.length)} contact${ids.length === 1 ? '' : 's'} for outreach?`}
        phrase={`SEND ${ids.length}`} actionLabel={`Approve & send to ${num(ready.length)}`} tone="gold" busy={busy}
        onCancel={() => setConfirming(false)} onConfirm={approve}>
        <p style={{ margin: 0 }}>
          {num(ready.length)} {ready.length === 1 ? 'person' : 'people'} will start getting automated outreach: their first text at the next allowed hour in their own time zone.
          {blockedInPreview.length ? ` ${blockedInPreview.length} blocked contact(s) will be skipped by the server.` : ''}
        </p>
      </TypedConfirm>
    </>
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
