/**
 * The field inspection workspace - /program/roofing/inspections/:id
 *
 * Built for a phone on a roof: one column, big tap targets, everything saves
 * as a draft as you go (debounced PATCH), photos straight from the camera.
 * "Complete report" is refused by the server until the findings and at least
 * one photo are there, and the missing items are listed here, not guessed.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'
import { Avatar, ErrorNote, Icon, Loading, Pill, SampleTag, errText, localWhen, money, useApi, useToast } from './ui'
import './roofing-inspect.css'
import { createDraftSaver } from './draftSaver'

const BASE = '/program/roofing/inspections'

export function PhotoImg({ url, alt, className }) {
  const [src, setSrc] = useState(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let live = true; let made = null
    setSrc(null); setErr(false)
    fetchObjectUrl(url).then((u) => { made = u; if (live) setSrc(u) }).catch(() => { if (live) setErr(true) })
    return () => { live = false; if (made) URL.revokeObjectURL(made) }
  }, [url])
  if (err) return <div className={`ri-photo-missing ${className || ''}`}><Icon name="image" size={22} /></div>
  if (!src) return <div className={`ri-photo-loading ${className || ''}`} />
  return <img src={src} alt={alt || ''} className={className} />
}

function Chips({ options, value, onChange, multi = false, disabled }) {
  const has = (k) => (multi ? (value || []).includes(k) : value === k)
  const toggle = (k) => {
    if (disabled) return
    if (!multi) return onChange(value === k ? null : k)
    const v = value || []
    onChange(v.includes(k) ? v.filter((x) => x !== k) : [...v, k])
  }
  return (
    <div className="ri-chips" role={multi ? 'group' : 'radiogroup'}>
      {options.map((o) => {
        const k = typeof o === 'string' ? o : o.key
        const l = typeof o === 'string' ? o : o.label
        return (
          <button key={k} type="button" disabled={disabled} className={`ri-chip ${has(k) ? 'on' : ''}`}
            aria-pressed={has(k)} onClick={() => toggle(k)}>
            {has(k) && multi && <Icon name="check" size={14} />}{l}
          </button>
        )
      })}
    </div>
  )
}

function Section({ n, title, done, children }) {
  return (
    <section className="ri-sec">
      <h2 className="ri-sec__h"><span className={`ri-sec__n ${done ? 'done' : ''}`}>{done ? <Icon name="check" size={14} /> : n}</span>{title}</h2>
      {children}
    </section>
  )
}

export default function InspectionWorkspace() {
  const { id } = useParams()
  const nav = useNavigate()
  const res = useApi(`/roofing-ops/inspections/${id}`)
  const [ins, setIns] = useState(null)
  const [saving, setSaving] = useState('saved')
  const [toast, say] = useToast()
  const saver = useRef(null)
  const [busy, setBusy] = useState(false)
  const [missing, setMissing] = useState(null)
  const [restored, setRestored] = useState(0)

  // One saver per inspection. Server answers update the computed fields
  // (missing items, status) without overwriting anything typed since.
  useEffect(() => {
    let storage = null
    try { storage = window.localStorage } catch { storage = null }
    const s = createDraftSaver({
      id,
      patch: (body) => api.patch(`/roofing-ops/inspections/${id}`, body),
      storage,
      onState: (st) => setSaving(st),
      onSaved: (doc, sentKeys, stillPending) => setIns((cur) => {
        if (!cur) return doc
        const keep = Object.fromEntries(Object.keys(stillPending).map((k) => [k, cur[k]]))
        return { ...doc, photos: cur.photos, ...keep }
      }),
    })
    saver.current = s
    const online = () => s.flush()
    const hide = () => { if (document.visibilityState === 'hidden') s.flush() }
    window.addEventListener('online', online)
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('pagehide', online)
    return () => {
      window.removeEventListener('online', online)
      document.removeEventListener('visibilitychange', hide)
      window.removeEventListener('pagehide', online)
      s.flush(); s.dispose()
    }
  }, [id])

  useEffect(() => {
    if (!res.data) return
    const s = saver.current
    if (res.data.status === 'completed') { s?.discard(); setIns(res.data); return }
    const back = s?.restore()
    setIns(back ? { ...res.data, ...back } : res.data)
    if (back) setRestored(Object.keys(back).length)
  }, [res.data])

  const set = (field, value) => {
    setIns((cur) => ({ ...cur, [field]: value }))
    saver.current?.set(field, value)
  }

  if (res.error) return <div className="rf-page"><ErrorNote onRetry={res.reload}>{res.error}</ErrorNote></div>
  if (!ins) return <div className="rf-page"><Loading label="Opening the inspection" /></div>
  const ch = ins.choices || CHOICES_FALLBACK
  const locked = ins.status === 'completed' || !ins.can_edit
  const H = ins.homeowner || {}

  // Complete is only offered when the server holds every change. The server
  // still refuses an incomplete report on its own (second layer).
  const unsynced = saving !== 'saved'
  const completeLabel = busy ? 'Completing\u2026' : saving === 'error' ? 'Save pending \u2014 reconnect to complete'
    : unsynced ? 'Saving\u2026' : 'Complete report'
  const complete = async () => {
    setBusy(true); setMissing(null)
    // Never complete on top of changes the server hasn't got yet.
    const ok = await saver.current.settle()
    if (!ok) {
      setBusy(false)
      say('Your latest changes haven\u2019t reached the server yet, so the report can\u2019t be completed. They\u2019re saved on this device and will sync as soon as the connection is back.', 'err')
      return
    }
    try {
      const d = await api.post(`/roofing-ops/inspections/${id}/complete`)
      setIns(d); say(`Report ${d.report_number} completed`)
      nav(`${BASE}/${id}/report`)
    } catch (e) {
      const m = e.detail?.missing || e.data?.detail?.missing || e.body?.detail?.missing
      if (m) setMissing(m); else say(errText(e), 'err')
    } finally { setBusy(false) }
  }
  const reopen = async () => {
    try { setIns(await api.post(`/roofing-ops/inspections/${id}/reopen`)); say('Report reopened for changes') } catch (e) { say(errText(e), 'err') }
  }

  return (
    <div className="rf-page ri">
      <div className="ri-top">
        <Link to={BASE} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="back" size={16} />Inspections</Link>
        <span className={`ri-save ri-save--${saving}`}>
          {locked ? (ins.status === 'completed' ? `Report ${ins.report_number}` : 'View only')
            : saving === 'saving' ? 'Saving…' : saving === 'pending' ? 'Unsaved changes' : saving === 'error' ? 'Not synced yet - saved on this device, retrying' : 'Draft saved'}
        </span>
      </div>

      {restored > 0 && (
        <div className="rf-note" style={{ marginBottom: 12 }}><Icon name="refresh" size={16} />
          <span>Restored {restored} change{restored === 1 ? '' : 's'} saved on this device before the page closed - sending now.</span></div>
      )}
      {saving === 'error' && !locked && (
        <div className="rf-note rf-note--warn" style={{ marginBottom: 12 }}><Icon name="alert" size={16} />
          <span>No connection to the server. Keep working - your changes are saved on this device and will sync when the connection returns. They are not on the server yet.</span></div>
      )}
      <header className="ri-head rf-panel">
        <div className="ri-head__who">
          <Avatar name={H.name} size={52} />
          <div>
            <h1 className="ri-h1">{H.name}</h1>
            <p className="ri-addr"><Icon name="pin" size={15} />{H.address || 'No address on file'}</p>
          </div>
        </div>
        <div className="ri-head__tags">
          <Pill tone={ins.status === 'completed' ? 'green' : 'orange'}>{ins.status_label}</Pill>
          {ins.sample_data && <SampleTag />}
        </div>
        <dl className="ri-facts">
          <div><dt>When</dt><dd>{ins.scheduled_at ? localWhen(ins.scheduled_at) : 'Not scheduled'}</dd></div>
          <div><dt>Type</dt><dd>{ins.inspection_type}</dd></div>
          <div><dt>Inspector</dt><dd>{ins.inspector_name || '—'}</dd></div>
          <div><dt>Stage</dt><dd>{ins.opportunity?.stage_label}</dd></div>
        </dl>
        <div className="ri-contact">
          {H.phone && <a className="rf-btn rf-btn--ghost rf-btn--sm" href={`tel:${H.phone}`}><Icon name="phone" size={16} />Call</a>}
          {H.address && <a className="rf-btn rf-btn--ghost rf-btn--sm" target="_blank" rel="noreferrer"
            href={`https://maps.google.com/?q=${encodeURIComponent(H.address)}`}><Icon name="pin" size={16} />Directions</a>}
          {H.email && <a className="rf-btn rf-btn--ghost rf-btn--sm" href={`mailto:${H.email}`}><Icon name="mail" size={16} />Email</a>}
        </div>
        {H.roof_issue && <p className="ri-issue"><b>Homeowner said:</b> {H.roof_issue}</p>}
      </header>

      {ins.status === 'completed' && (
        <div className="ri-done rf-panel">
          <Icon name="check" size={20} />
          <div><b>Report {ins.report_number} is complete.</b><span>Findings and photos are locked so the report stays exactly as delivered.</span></div>
          <Link className="rf-btn rf-btn--primary rf-btn--sm" to={`${BASE}/${id}/report`}>View report</Link>
          {ins.can_reopen && <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={reopen}>Reopen</button>}
        </div>
      )}

      <Section n={1} title="The roof" done={!!(ins.roof_type && ins.roof_condition)}>
        <label className="ri-lbl">Roof type</label>
        <Chips options={ch.roof_types} value={ins.roof_type} onChange={(v) => set('roof_type', v)} disabled={locked} />
        <div className="ri-row2">
          <label className="ri-lbl">Roof age (years)
            <input className="rf-input" inputMode="numeric" disabled={locked} value={ins.roof_age_years ?? ''}
              onChange={(e) => set('roof_age_years', e.target.value.replace(/\D/g, '') || null)} />
          </label>
          <label className="ri-lbl">Stories
            <input className="rf-input" inputMode="numeric" disabled={locked} value={ins.stories ?? ''}
              onChange={(e) => set('stories', e.target.value.replace(/\D/g, '') || null)} />
          </label>
        </div>
        <label className="ri-lbl">Overall condition</label>
        <Chips options={ch.conditions} value={ins.roof_condition} onChange={(v) => set('roof_condition', v)} disabled={locked} />
      </Section>

      <Section n={2} title="Damage found" done={!!ins.severity}>
        <label className="ri-lbl">Check everything you see</label>
        <Chips multi options={ch.damage} value={ins.damage} onChange={(v) => set('damage', v)} disabled={locked} />
        <label className="ri-lbl">Severity</label>
        <Chips options={ch.severities} value={ins.severity} onChange={(v) => set('severity', v)} disabled={locked} />
        <label className="ri-lbl">Affected areas</label>
        <Chips multi options={ch.areas} value={ins.affected_areas} onChange={(v) => set('affected_areas', v)} disabled={locked} />
      </Section>

      <Photos ins={ins} setIns={setIns} locked={locked} say={say} labels={ch.photo_labels} />

      <Section n={4} title="Inspector's notes" done={!!(ins.notes || '').trim()}>
        <textarea className="rf-textarea ri-notes" rows={5} disabled={locked} value={ins.notes || ''}
          placeholder="What you found, where, and what the homeowner should know."
          onChange={(e) => set('notes', e.target.value)} />
      </Section>

      <Section n={5} title="Recommendation" done={!!ins.recommendation}>
        <Chips options={ch.recommendations} value={ins.recommendation} onChange={(v) => set('recommendation', v)} disabled={locked} />
        <textarea className="rf-textarea" rows={3} disabled={locked} value={ins.recommendation_notes || ''}
          placeholder="Scope of work, materials, anything the estimate should include."
          onChange={(e) => set('recommendation_notes', e.target.value)} />
        <label className="ri-lbl">Insurance - as reported by the homeowner</label>
        <Chips options={ch.insurance_claim} value={ins.insurance_claim} onChange={(v) => set('insurance_claim', v)} disabled={locked} />
      </Section>

      {ins.status === 'completed'
        ? <Handoff ins={ins} setIns={setIns} say={say} />
        : !locked && (
          <div className="ri-finish rf-panel">
            {missing && (
              <div className="rf-note rf-note--warn"><Icon name="alert" size={16} />
                <span>Still needed: {missing.join(', ')}.</span></div>
            )}
            {!missing && ins.missing?.length > 0 && <p className="rf-muted">Still needed before you can complete: {ins.missing.join(', ')}.</p>}
            <div className="ri-finish__btns">
              <button className="rf-btn rf-btn--ghost" onClick={async () => {
                const ok = await saver.current.settle()
                say(ok ? 'Draft saved - you can come back to it any time' : 'Not synced yet - your changes are saved on this device and will sync when the connection is back', ok ? undefined : 'err')
              }}>Save draft</button>
              <button className="rf-btn rf-btn--primary" disabled={busy || unsynced} aria-disabled={busy || unsynced}
                title={unsynced ? 'Your latest changes are still saving' : undefined} onClick={complete}>{completeLabel}</button>
            </div>
          </div>
        )}
      <div className="ri-sticky">
        {ins.status !== 'completed' && !locked
          ? <><span className={`ri-save--${saving}`}>{saving === 'saved' ? 'Draft saved' : saving === 'error' ? 'Saved on this device only' : 'Saving\u2026'}</span>
            <button className={`rf-btn rf-btn--primary ${saving === 'error' ? 'ri-btn-wait' : ''}`} disabled={busy || unsynced}
              aria-disabled={busy || unsynced} onClick={complete}>{completeLabel}</button></>
          : <Link className="rf-btn rf-btn--primary" to={`${BASE}/${id}/report`}>View report</Link>}
      </div>
      {toast}
    </div>
  )
}

function Photos({ ins, setIns, locked, say, labels }) {
  const cam = useRef(null)
  const lib = useRef(null)
  const [label, setLabel] = useState('damage')
  const [uploading, setUploading] = useState(0)
  const [view, setView] = useState(null)
  const id = ins.id
  const upload = async (files) => {
    const list = Array.from(files || [])
    if (!list.length) return
    setUploading(list.length)
    let ok = 0
    for (const f of list) {
      const fd = new FormData()
      fd.append('file', f); fd.append('label', label)
      try {
        const p = await api.upload(`/roofing-ops/inspections/${id}/photos`, fd)
        setIns((cur) => ({ ...cur, photos: [...(cur.photos || []).filter((x) => x.id !== p.id), p],
          missing: (cur.missing || []).filter((m) => m !== 'At least one photo') }))
        ok += 1
      } catch (e) { say(`${f.name}: ${errText(e)}`, 'err') }
      setUploading((n) => n - 1)
    }
    if (ok) say(`${ok} photo${ok === 1 ? '' : 's'} saved to this inspection`)
  }
  const patch = async (p, body) => {
    try {
      const d = await api.patch(`/roofing-ops/inspections/${id}/photos/${p.id}`, body)
      setIns((cur) => ({ ...cur, photos: cur.photos.map((x) => (x.id === p.id ? d : x)) }))
      if (view?.id === p.id) setView(d)
    } catch (e) { say(errText(e), 'err') }
  }
  const remove = async (p) => {
    try {
      await api.delete(`/roofing-ops/inspections/${id}/photos/${p.id}`)
      setIns((cur) => ({ ...cur, photos: cur.photos.filter((x) => x.id !== p.id) })); setView(null)
    } catch (e) { say(errText(e), 'err') }
  }
  const photos = ins.photos || []
  return (
    <Section n={3} title={`Photos (${photos.length})`} done={photos.length > 0}>
      {!locked && (
        <>
          <label className="ri-lbl">What are you photographing?</label>
          <Chips options={labels} value={label} onChange={(v) => setLabel(v || 'other')} />
          <div className="ri-photo-btns">
            <button className="rf-btn rf-btn--primary ri-big" onClick={() => cam.current?.click()}><Icon name="camera" size={20} />Take photo</button>
            <button className="rf-btn rf-btn--ghost ri-big" onClick={() => lib.current?.click()}><Icon name="upload" size={20} />Upload photos</button>
          </div>
          <input ref={cam} type="file" accept="image/*" capture="environment" hidden
            onChange={(e) => { upload(e.target.files); e.target.value = '' }} aria-label="Take photo with camera" />
          <input ref={lib} type="file" accept="image/*" multiple hidden
            onChange={(e) => { upload(e.target.files); e.target.value = '' }} aria-label="Upload photos" />
          {uploading > 0 && <p className="rf-muted"><Icon name="upload" size={14} /> Uploading {uploading}…</p>}
        </>
      )}
      {!photos.length && <p className="rf-muted ri-empty">No photos yet.{locked ? '' : ' Take at least one before completing the report.'}</p>}
      <div className="ri-gallery">
        {photos.map((p) => (
          <figure key={p.id} className="ri-tile">
            <button type="button" className="ri-tile__img" onClick={() => setView(p)} aria-label={`Open photo ${p.caption || p.label_text || ''}`}>
              <PhotoImg url={p.url} alt={p.caption || p.label_text} />
              {p.label_text && <span className="ri-tile__tag">{p.label_text}</span>}
            </button>
            {locked
              ? <figcaption>{p.caption}</figcaption>
              : <input className="ri-cap" placeholder="Add a description" defaultValue={p.caption || ''}
                  onBlur={(e) => { if ((e.target.value || '') !== (p.caption || '')) patch(p, { caption: e.target.value }) }} />}
          </figure>
        ))}
      </div>
      {view && (
        <div className="ri-viewer" role="dialog" aria-label="Photo" onClick={() => setView(null)}>
          <div className="ri-viewer__card" onClick={(e) => e.stopPropagation()}>
            <PhotoImg url={view.url} alt={view.caption} className="ri-viewer__img" />
            <div className="ri-viewer__meta">
              {!locked ? (
                <>
                  <Chips options={labels} value={view.label} onChange={(v) => patch(view, { label: v })} />
                  <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => remove(view)}><Icon name="trash" size={16} />Delete photo</button>
                </>
              ) : <span>{view.label_text} {view.caption ? `- ${view.caption}` : ''}</span>}
              <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => setView(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </Section>
  )
}

/** What sales should do next, from the homeowner's REAL stage - never the
 *  step already done. Recording an estimate as given never claims a document
 *  was produced or sent; a follow-up is a reminder on the record, not a
 *  calendar event. */
export function nextStep(ins) {
  const st = ins.opportunity?.stage
  const amt = ins.opportunity?.estimate_amount
  if (st === 'won' || st === 'lost') return { key: 'closed' }
  if (st === 'estimate_sent') return { key: 'follow_estimate', label: 'Follow up on estimate', icon: 'calendar' }
  if (ins.recommendation === 'none') return { key: 'close', label: 'Close - no work needed', icon: 'close' }
  if (amt != null) return { key: 'record_given', label: 'Record estimate given', icon: 'check' }
  return { key: 'estimate', label: 'Prepare estimate', icon: 'dollar' }
}

export function Handoff({ ins, setIns, say }) {
  const [open, setOpen] = useState(null)
  const [amt, setAmt] = useState(ins.opportunity?.estimate_amount ?? '')
  const [won, setWon] = useState(ins.opportunity?.estimate_amount ?? '')
  const [scope, setScope] = useState(ins.recommendation_notes || '')
  const [when, setWhen] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const st = ins.opportunity?.stage
  const next = nextStep(ins)
  const inFlight = useRef(false)
  const go = async (action, extra = {}) => {
    if (inFlight.current) return            // a second tap before the first answers records nothing twice
    inFlight.current = true
    setBusy(true)
    try {
      const d = await api.post(`/roofing-ops/inspections/${ins.id}/handoff`, { action, ...extra })
      setIns((cur) => ({ ...cur, ...d, photos: cur.photos })); setOpen(null)
      say({
        prepare_estimate: 'Estimate amount saved on the homeowner. Nothing was sent.',
        record_estimate_given: 'Recorded as given - homeowner moved to Estimate Sent. Nothing was emailed from here.',
        schedule_follow_up: 'Follow-up reminder saved on the homeowner (not added to a calendar).',
        recommend_replacement: 'Replacement recommended', recommend_repair: 'Repair recommended',
        close_no_work: 'Closed - no work needed', mark_won: 'Job won', mark_lost: 'Marked as declined',
      }[action])
    } catch (e) { say(errText(e), 'err') } finally { inFlight.current = false; setBusy(false) }
  }
  const primary = () => setOpen({ estimate: 'estimate', record_given: 'given', follow_estimate: 'follow', close: 'close' }[next.key])
  const h = ins.handoff
  return (
    <section className="ri-sec ri-handoff">
      <h2 className="ri-sec__h"><span className="ri-sec__n done"><Icon name="arrow" size={14} /></span>Next step for sales</h2>
      <p className="rf-muted">The homeowner is at <b>{ins.opportunity?.stage_label}</b>
        {ins.opportunity?.estimate_amount != null ? <> · estimate {money(ins.opportunity.estimate_amount, false)}</> : null}
        {ins.opportunity?.won_amount != null && st === 'won' ? <> · job {money(ins.opportunity.won_amount, false)}</> : null}.
        {h ? <> Last recorded: <b>{h.label}</b>{h.follow_up_at ? ` (${localWhen(h.follow_up_at, { hour: undefined, minute: undefined })})` : ''}.</> : null}</p>
      {next.key === 'closed' ? <p className="rf-muted">This job is closed. The report stays on the homeowner's record.</p> : (
        <div className="ri-actions">
          <button className="rf-btn rf-btn--primary" onClick={primary}><Icon name={next.icon} size={18} />{next.label}</button>
          {st === 'estimate_sent' && <>
            <button className="rf-btn rf-btn--ghost" onClick={() => setOpen('won')}><Icon name="check" size={18} />Homeowner accepted</button>
            <button className="rf-btn rf-btn--ghost" onClick={() => setOpen('lost')}><Icon name="close" size={18} />Homeowner declined</button>
          </>}
          {st !== 'estimate_sent' && <>
            {next.key !== 'estimate' && <button className="rf-btn rf-btn--ghost" onClick={() => setOpen('estimate')}><Icon name="dollar" size={18} />{ins.opportunity?.estimate_amount != null ? 'Change estimate' : 'Prepare estimate'}</button>}
            <button className="rf-btn rf-btn--ghost" onClick={() => setOpen('follow')}><Icon name="calendar" size={18} />Schedule follow-up</button>
            {ins.recommendation !== 'replacement' && <button className="rf-btn rf-btn--ghost" disabled={busy} onClick={() => go('recommend_replacement')}><Icon name="roof" size={18} />Recommend replacement</button>}
            {ins.recommendation !== 'repair' && <button className="rf-btn rf-btn--ghost" disabled={busy} onClick={() => go('recommend_repair')}><Icon name="settings" size={18} />Recommend repair</button>}
            {next.key !== 'close' && <button className="rf-btn rf-btn--ghost" onClick={() => setOpen('close')}><Icon name="close" size={18} />Close - no work needed</button>}
          </>}
        </div>
      )}
      {open === 'estimate' && (
        <div className="ri-form">
          <label className="ri-lbl">Estimate amount ($)
            <input className="rf-input" inputMode="decimal" value={amt} onChange={(e) => setAmt(e.target.value.replace(/[^\d.]/g, ''))} />
          </label>
          <label className="ri-lbl">Scope of work
            <textarea className="rf-textarea" rows={3} value={scope} onChange={(e) => setScope(e.target.value)} />
          </label>
          <p className="rf-muted ri-small">Saves the amount and scope on the homeowner. No estimate document is created or sent.</p>
          <div className="ri-finish__btns"><button className="rf-btn rf-btn--ghost" onClick={() => setOpen(null)}>Cancel</button>
            <button className="rf-btn rf-btn--primary" disabled={busy || amt === ''} onClick={() => go('prepare_estimate', { estimate_amount: Number(amt), scope })}>Save estimate</button></div>
        </div>
      )}
      {open === 'given' && (
        <div className="ri-form">
          <p>Record that you gave the homeowner the <b>{money(ins.opportunity?.estimate_amount, false)}</b> estimate (in person, by email or by text from your own phone)?
            They move to <b>Estimate Sent</b>. Nothing is emailed from here.</p>
          <div className="ri-finish__btns"><button className="rf-btn rf-btn--ghost" onClick={() => setOpen(null)}>Cancel</button>
            <button className="rf-btn rf-btn--primary" disabled={busy} onClick={() => go('record_estimate_given')}>Record estimate given</button></div>
        </div>
      )}
      {open === 'follow' && (
        <div className="ri-form">
          <label className="ri-lbl">Follow up on<input className="rf-input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
          <label className="ri-lbl">Note<input className="rf-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder={st === 'estimate_sent' ? 'e.g. Call to answer questions on the estimate' : 'e.g. Waiting on the adjuster'} /></label>
          <p className="rf-muted ri-small">Saved as a reminder on this homeowner and on the Inspections list. It is not added to a calendar and nothing is sent to the homeowner.</p>
          <div className="ri-finish__btns"><button className="rf-btn rf-btn--ghost" onClick={() => setOpen(null)}>Cancel</button>
            <button className="rf-btn rf-btn--primary" disabled={busy || !when} onClick={() => go('schedule_follow_up', { follow_up_at: when, note })}>Save reminder</button></div>
        </div>
      )}
      {open === 'won' && (
        <div className="ri-form">
          <label className="ri-lbl">Job amount ($)<input className="rf-input" inputMode="decimal" value={won} onChange={(e) => setWon(e.target.value.replace(/[^\d.]/g, ''))} /></label>
          <div className="ri-finish__btns"><button className="rf-btn rf-btn--ghost" onClick={() => setOpen(null)}>Cancel</button>
            <button className="rf-btn rf-btn--primary" disabled={busy} onClick={() => go('mark_won', { won_amount: won === '' ? null : Number(won) })}>Mark job won</button></div>
        </div>
      )}
      {open === 'lost' && (
        <div className="ri-form">
          <label className="ri-lbl">Why did they decline?<input className="rf-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Went with a cheaper bid" /></label>
          <div className="ri-finish__btns"><button className="rf-btn rf-btn--ghost" onClick={() => setOpen(null)}>Cancel</button>
            <button className="rf-btn rf-btn--navy" disabled={busy || !note.trim()} onClick={() => go('mark_lost', { note })}>Mark declined</button></div>
        </div>
      )}
      {open === 'close' && (
        <div className="ri-form">
          <p>Close this homeowner as <b>no work needed</b>? They move to Lost with that reason. The report stays on file.</p>
          <div className="ri-finish__btns"><button className="rf-btn rf-btn--ghost" onClick={() => setOpen(null)}>Cancel</button>
            <button className="rf-btn rf-btn--navy" disabled={busy} onClick={() => go('close_no_work')}>Close - no work needed</button></div>
        </div>
      )}
    </section>
  )
}

const CHOICES_FALLBACK = {
  roof_types: [], conditions: [], damage: [], severities: [], areas: [], recommendations: [], insurance_claim: [], photo_labels: [],
}
