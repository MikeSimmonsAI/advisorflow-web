/* Campaigns: the catalog, a live per-location preview of the real text and
   email (GET /program/campaigns/{id}/preview - sends nothing), and guarded
   controls. Status: ON (switched on), OFF, or BLOCKED (the server cannot text:
   line not configured or not approved).

   ACTIVATION IS DANGEROUS. Switching a campaign on lets automated messages go
   to contacts ALREADY enrolled in it, even though it enrolls no one new. It
   needs the campaign's exact name typed (enforced again on the server:
   POST /campaigns/{id}/activation) and is audit-logged. Nothing on this page
   switches anything on by itself. */
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import {
  Banner, Chip, Empty, ErrorLine, Field, Icon, Kpi, Loading, PageHead, Panel, SearchBox, Tabs, TypedConfirm,
  errText, num,
} from './ui'

const PURPOSE = {
  cemetery_x_sell: 'Additional cemetery planning options', cremation: 'Cremation and memorial options',
  general_survey: 'Location-specific feedback', life_story: 'Life story and memorial planning',
  re_engagement: 'Follow-up on an earlier conversation', seminar: 'Seminar sign-ups: savings certificate and planning guide',
  veteran_official: 'Veteran benefits information', veteran_planning_guide: 'Veteran Benefits Guide requests',
  veteran_spanish: 'Veteran benefits, Spanish callouts', web_lead: 'Website information requests',
}
const MODE_LABEL = { none: 'No flyer', hosted: 'Hosted / view flyer', attached: 'Attached PDF' }

export default function Campaigns({ isManager, locationId, locations, performance, goTab }) {
  const [searchParams] = useSearchParams()
  const targetCampaign = searchParams.get('campaign') || ''
  const [rows, setRows] = useState(null)
  const [cur, setCur] = useState(null)
  const [q, setQ] = useState('')
  const homes = locations.filter(l => !l.is_review_bucket)
  const [loc, setLoc] = useState(locationId && homes.some(l => l.location_id === locationId) ? locationId : homes[0]?.location_id || '')
  const [touch, setTouch] = useState('first')
  const [preview, setPreview] = useState(null)
  const [sms, setSms] = useState(null)          // the server's texting state, from /program/health
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmOn, setConfirmOn] = useState(false)
  const [previewVersion, setPreviewVersion] = useState(0)

  useEffect(() => {
    api.get('/program/campaigns').then(d => {
      setRows(d)
      setCur(c => d.find(f => f.key === targetCampaign) || c || d[0] || null)
    }).catch(e => setErr(errText(e)))
    api.get('/program/health').then(h => setSms(h.sms || null)).catch(() => setSms(null))
  }, []) // initial selection follows the deep link if provided
  useEffect(() => { if (locationId && homes.some(l => l.location_id === locationId)) setLoc(locationId) }, [locationId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (cur?.own_copy) setTouch('sms:1')
    else if (touch.includes(':')) setTouch('first')
  }, [cur?.id])
  useEffect(() => {
    if (!cur || !loc) { setPreview(null); return undefined }
    let alive = true
    setPreview(null)
    api.get(`/program/campaigns/${cur.id}/preview?location_id=${encodeURIComponent(loc)}&touch=${touch}`)
      .then(p => { if (alive) { setPreview(p); setErr('') } }).catch(e => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [cur, loc, touch, previewVersion])

  const perf = useMemo(() => Object.fromEntries((performance || []).map(p => [p.key, p])), [performance])
  const blocked = sms ? (sms.status === 'fail' || sms.approved === false) : false
  const statusOf = f => (blocked ? ['BLOCKED', 'bad'] : f.is_active ? ['ON', 'ok'] : ['OFF', ''])
  const replace = f => { setCur(f); setRows(rs => rs.map(r => (r.id === f.id ? f : r))) }
  const setMode = async (key, value) => {
    try { replace(await api.patch(`/program/campaigns/${cur.id}`, { [key]: value })) } catch (e) { setErr(errText(e)) }
  }
  const activate = async (active, confirm = null) => {
    setBusy(true)
    try { replace(await api.post(`/program/campaigns/${cur.id}/activation`, { active, confirm })); setConfirmOn(false) }
    catch (e) { setErr(errText(e)); setConfirmOn(false) } finally { setBusy(false) }
  }

  if (!rows && !err) return <><PageHead title="Campaigns" /><Loading /></>
  const active = (rows || []).filter(r => r.is_active).length
  const list = (rows || []).filter(f => !q.trim() || `${f.name} ${PURPOSE[f.key] || ''}`.toLowerCase().includes(q.trim().toLowerCase()))
  const totalLeads = (performance || []).filter(p => p.key !== 'unmapped').reduce((s, p) => s + p.leads, 0)
  const totalReplies = (performance || []).reduce((s, p) => s + p.responses, 0)
  const unmapped = perf.unmapped

  return (
    <>
      <PageHead title="Campaigns" sub="One set of campaign families, personalized for each family's own location.">
        <Chip tone={active ? 'ok' : 'bad'}>{active ? `${active} on` : 'All off'}</Chip>
        <button type="button" className="sci-btn gold" onClick={() => goTab('launch')}><Icon name="launch" />Launch readiness</button>
      </PageHead>

      <section className="sci-kpis four" aria-label="Campaign numbers">
        <Kpi icon="campaigns" tone="blue" value={(rows || []).length} label="Campaign families" />
        <Kpi icon="bolt" tone={active ? 'green' : 'red'} value={active} label="Campaigns switched on" note={active ? 'Automated messages may go' : 'Nothing sends automatically'} />
        <Kpi icon="users" tone="gold" value={totalLeads} label="Contacts in a campaign" note={unmapped ? `${num(unmapped.leads)} with no campaign` : undefined} />
        <Kpi icon="responses" tone="purple" value={totalReplies} label="Replies received" onClick={() => goTab('responses')} />
      </section>

      {blocked ? (
        <Banner tone="bad" icon="ban" title="Texting is blocked on the server">{sms.detail}. No campaign text can go out until this is fixed (see Health).</Banner>
      ) : active === 0 ? (
        <Banner icon="pause" title="All campaigns are off">
          Nothing is sent automatically.{sms?.approved_scope ? ` The texting line's approved scope is ${sms.approved_scope}.` : ''} Each text is still checked for consent, STOP and do-not-call before it goes out.
        </Banner>
      ) : (
        <Banner tone="ok" icon="bolt" title={`${active} campaign(s) switched on`}>Automated messages go only to contacts already enrolled in those campaigns, and only after the server's consent, STOP and do-not-call checks.</Banner>
      )}
      <ErrorLine text={err} />

      <div className="sci-grid-camp">
        <Panel title="Campaign catalog" labelledBy="sci-catalog" aside={<span className="sci-micro sci-muted">{(rows || []).length} families</span>}>
          <div style={{ marginBottom: 8 }}><SearchBox id="sci-camp-q" value={q} onChange={setQ} placeholder="Find a campaign" label="Find a campaign" width="100%" /></div>
          {list.length === 0 && <Empty icon="campaigns">{(rows || []).length ? 'No campaign matches.' : 'No campaigns set up yet.'}</Empty>}
          <div className="sci-list" style={{ maxHeight: 520 }}>
            {list.map(f => {
              const [label, tone] = statusOf(f)
              const p = perf[f.key]
              return (
                <button key={f.id} type="button" className="sci-camp" aria-current={cur?.id === f.id} onClick={() => setCur(f)}>
                  <span className="sci-camp-ico" aria-hidden="true"><Icon name="campaigns" /></span>
                  <span className="sci-camp-body">
                    <b>{f.name}</b>
                    <small>{p ? `${num(p.leads)} contacts · ${num(p.responses)} replies` : 'No contacts assigned'}{f.language && f.language !== 'en' ? ` · ${f.language}` : ''}</small>
                  </span>
                  <Chip tone={tone} plain>{label}</Chip>
                </button>
              )
            })}
          </div>
        </Panel>

        {cur ? (
          <Panel title="Campaign preview" labelledBy="sci-preview" aside={<span className="sci-micro sci-muted">Rendered by the server · sends nothing</span>}>
            <div className="sci-form" style={{ marginBottom: 6 }}>
              <Field label="Selected campaign"><input value={cur.name} disabled /></Field>
              <Field label="Location">
                <select value={loc} onChange={e => setLoc(e.target.value)}>
                  {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                </select>
              </Field>
            </div>
            {cur.own_copy ? (
              <p className="sci-micro sci-muted" style={{ margin: '0 0 8px' }}>
                Showing <b>{touch.startsWith('email') ? 'Email' : 'Text'} {touch.split(':')[1] || 1}</b> - pick a step in the editor below.
              </p>
            ) : (
              <Tabs items={[['first', 'First touch'], ['followup', 'Follow-up']]} value={touch} onChange={setTouch} label="Touch" />
            )}
            {preview && !preview.ok && <div className="sci-alert"><Icon name="alert" />{preview.reason}</div>}
            {!preview && <Loading label="Rendering preview…" />}
            {preview?.ok && cur.own_copy && (
              <div style={{ minWidth: 0 }}>
                {touch.startsWith('sms') ? (<>
                  <p className="sci-preview-label"><Icon name="sms" size={12} />Text {preview.step}{preview.sms_chars ? ` · ${preview.sms_chars} characters` : ''}</p>
                  <div className="sci-preview-sms">{preview.sms || 'This step has no wording yet - nothing will be sent for it.'}</div>
                  <p className="sci-micro sci-muted" style={{ marginTop: 6 }}>Location sender: <b>{preview.from_display_name}</b></p>
                </>) : (<>
                  <p className="sci-preview-label"><Icon name="mail" size={12} />Email {preview.step} · {preview.email_subject || '(no subject yet)'}</p>
                  <div className="sci-preview-email">{preview.email_body || 'This step has no wording yet - nothing will be sent for it.'}</div>
                </>)}
              </div>
            )}
            {preview?.ok && !cur.own_copy && (
              <div className="sci-preview-pair">
                <div style={{ minWidth: 0 }}>
                  <p className="sci-preview-label"><Icon name="sms" size={12} />Text message</p>
                  <div className="sci-preview-sms">{preview.sms}</div>
                  <p className="sci-micro sci-muted" style={{ marginTop: 6 }}>Location sender: <b>{preview.from_display_name}</b></p>
                </div>
                <div style={{ minWidth: 0 }}>
                  <p className="sci-preview-label"><Icon name="mail" size={12} />Email · {preview.email_subject}</p>
                  <div className="sci-preview-email">{preview.email_body}</div>
                  <p className="sci-micro sci-muted" style={{ marginTop: 6 }}>Flyer: {preview.flyer_available ? (preview.email_mode === 'attached' ? 'attached PDF' : preview.email_mode === 'hosted' ? 'hosted link' : 'not included') : 'none uploaded yet'}</p>
                </div>
              </div>
            )}
            <p className="sci-footnote"><Icon name="shield" />A preview does not establish anyone's opt-in. Every enrolled contact remains gated on the backend.</p>
          </Panel>
        ) : <Panel title="Campaign preview"><Empty icon="campaigns">Pick a campaign.</Empty></Panel>}

        {cur?.own_copy && (
          <SequenceEditor cur={cur} isManager={isManager} touch={touch} setTouch={setTouch}
            onSaved={f => { replace(f); setPreviewVersion(v => v + 1) }} onError={setErr} />
        )}
        {cur?.key === 'central_alabama_ms' && <Roster locations={homes} />}

        {cur && (
          <Panel title="Campaign controls" labelledBy="sci-controls">
            <div className="sci-ctrl"><span>Campaign</span><b style={{ textAlign: 'right' }}>{cur.name}</b></div>
            <div className="sci-ctrl"><span>Status</span><Chip tone={statusOf(cur)[1]}>{statusOf(cur)[0]}</Chip></div>
            <div className="sci-ctrl"><span>Purpose</span><span className="sci-muted" style={{ textAlign: 'right' }}>{PURPOSE[cur.key] || cur.asset_category || 'Outreach campaign'}</span></div>
            <div className="sci-form one" style={{ marginTop: 10 }}>
              <Field label="First-touch email">
                <select value={cur.first_touch_email_mode} disabled={!isManager} onChange={e => setMode('first_touch_email_mode', e.target.value)}>
                  {Object.entries(MODE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </Field>
              <Field label="Follow-up email">
                <select value={cur.followup_email_mode} disabled={!isManager} onChange={e => setMode('followup_email_mode', e.target.value)}>
                  {Object.entries(MODE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </Field>
            </div>
            <div className="sci-banner" style={{ margin: '12px 0' }}>
              <span className="sci-banner-icon" aria-hidden="true"><Icon name="alert" /></span>
              <div className="sci-banner-body sci-micro"><strong>Activation requires confirmation</strong>Switching on lets automated messages go to contacts already enrolled in this campaign. It enrolls no one by itself.</div>
            </div>
            {isManager ? (
              cur.is_active
                ? <button type="button" className="sci-btn danger block" disabled={busy} onClick={() => activate(false)}><Icon name="pause" />{busy ? 'Saving…' : 'Switch off'}</button>
                : <button type="button" className="sci-btn gold block" disabled={busy || blocked} onClick={() => setConfirmOn(true)}
                  title={blocked ? 'Texting is blocked on the server' : undefined}><Icon name="bolt" />Review activation…</button>
            ) : <p className="sci-micro sci-muted">Only a workspace manager can switch campaigns on or off.</p>}
            {isManager && <EmailRunner />}
          </Panel>
        )}
      </div>

      <TypedConfirm open={confirmOn && !!cur} title={`Switch on "${cur?.name}"?`} phrase={cur?.name} actionLabel="Switch campaign on"
        tone="dangerfill" busy={busy} onCancel={() => setConfirmOn(false)} onConfirm={typed => activate(true, typed)}>
        <p style={{ margin: 0 }}>Automated texts and emails may start going to contacts <b>already enrolled</b> in this campaign, after the server's consent, STOP, do-not-call and suppression checks. This is audit-logged.</p>
      </TypedConfirm>
    </>
  )
}

/** What the campaign email runner WOULD send now - a dry run on the server. */
function EmailRunner() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.get('/program/email-touches').then(setD).catch(e => setErr(errText(e))) }, [])
  if (err) return <div style={{ marginTop: 12 }}><ErrorLine text={err} /></div>
  if (!d) return <Loading label="Checking the email runner…" />
  const c = d.counts || {}
  return (
    <details className="sci-details" style={{ marginTop: 12, borderTop: '1px solid #edf1f4' }}>
      <summary>Campaign email runner <Chip tone={d.enabled ? 'ok' : ''} plain>{d.enabled ? 'On' : 'Off'}</Chip></summary>
      <div className="sci-details-body">
        <p style={{ margin: '0 0 8px' }}>
          {d.enabled ? `At most ${d.daily_cap} a day (${d.used_today} so far today), ${d.batch} per pass, 9am–6pm local, follow-up after ${d.followup_days} days.` : 'Nothing is emailed automatically until it is switched on for this deployment.'}
          {' '}A reply on any channel ends a contact's sequence.
        </p>
        <div className="sci-statstrip">
          <div className="sci-stat"><b>{num(d.would_send_total)}</b><span>Would go now</span></div>
          <div className="sci-stat"><b>{num(d.skipped)}</b><span>Held</span></div>
          <div className="sci-stat"><b>{num(c.sent || 0)}</b><span>Sent</span></div>
          <div className="sci-stat"><b>{num(c.failed || 0)}</b><span>Failed</span></div>
        </div>
        {d.held_no_flyer > 0 && <p className="sci-micro" style={{ marginTop: 6 }}>{d.held_no_flyer} waiting for an approved flyer.</p>}
        {c.unknown > 0 && <p className="sci-micro" style={{ color: 'var(--bad)', marginTop: 6 }}>{c.unknown} outcome unknown — check before resending.</p>}
      </div>
    </details>
  )
}


/* #441: every step of a campaign that speaks only its own words (Central
   Alabama). Text steps follow the SCI cadence days, email steps the email
   runner. Save keeps the campaign OFF and sends nothing. */
function SequenceEditor({ cur, isManager, touch, setTouch, onSaved, onError }) {
  const steps = cur.steps || { sms: [], email: [] }
  const [draft, setDraft] = useState({})          // 'sms:2' -> text | 'email:3' -> {subject, body}
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  useEffect(() => { setDraft({}); setNote('') }, [cur.id])
  const [ch, n] = touch.includes(':') ? touch.split(':') : ['sms', '1']
  const step = (ch === 'email' ? steps.email : steps.sms).find(x => String(x.touch) === n) || {}
  const key = `${ch}:${n}`
  const val = draft[key] ?? (ch === 'email' ? { subject: step.subject || '', body: step.body || '' } : (step.body || ''))
  const original = k => {
    const [c, t] = k.split(':')
    const x = (c === 'email' ? steps.email : steps.sms).find(y => String(y.touch) === t) || {}
    return c === 'email' ? { subject: x.subject || '', body: x.body || '' } : (x.body || '')
  }
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
  const set = v => setDraft(d => {
    const next = { ...d }
    if (same(v, original(key))) delete next[key]; else next[key] = v     // typed back = unchanged
    return next
  })
  const changed = Object.keys(draft).length > 0
  const blank = s => (s.startsWith('email') ? !(draft[s]?.subject ?? '').trim() || !(draft[s]?.body ?? '').trim() : !(draft[s] ?? '').trim())
  const isBlank = (c, x) => {
    const k = `${c}:${x.touch}`
    if (k in draft) return blank(k)
    return c === 'email' ? !(x.subject && x.body) : !x.body
  }
  const save = async () => {
    if (!changed || saving) return
    setSaving(true); setNote('')
    const sms = {}, email = {}
    for (const [k, v] of Object.entries(draft)) {
      const [c, t] = k.split(':')
      if (c === 'sms') sms[t] = v; else email[t] = v
    }
    try {
      const f = await api.patch(`/program/campaigns/${cur.id}`, { steps: { sms, email } })
      setDraft({}); onSaved(f)
      setNote('Saved. The campaign is still off - no family was contacted.')
    } catch (e) { onError(errText(e)) } finally { setSaving(false) }
  }
  const pill = (c, x, label) => {
    const k = `${c}:${x.touch}`
    const on = touch === k
    return (
      <button key={k} type="button" onClick={() => setTouch(k)} aria-pressed={on}
        className={`sci-btn sm${on ? ' primary' : ''}`} style={{ justifyContent: 'space-between', minWidth: 132 }}>
        <span>{label} {x.touch}</span>
        <span className="sci-micro" style={{ opacity: 0.8 }}>Day {x.day_offset}{isBlank(c, x) ? ' · blank' : ''}{k in draft ? ' · edited' : ''}</span>
      </button>
    )
  }
  return (
    <Panel title={`${cur.name} — message sequence`} labelledBy="sci-seq" style={{ gridColumn: '1 / -1' }}>
      <p className="sci-micro sci-muted" style={{ marginTop: 0 }}>
        Every step belongs only to this campaign and has its own wording. Texts follow the SCI cadence days; emails follow
        the email runner. A blank step sends nothing, and the campaign can't be switched on while any step is blank.
        Saving never switches the campaign on.
      </p>
      <div style={{ display: 'grid', gap: 10 }}>
        <div><b className="sci-micro">Texts</b><div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>{steps.sms.map(x => pill('sms', x, 'Text'))}</div></div>
        <div><b className="sci-micro">Emails</b><div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>{steps.email.map(x => pill('email', x, 'Email'))}</div></div>
      </div>
      <div className="sci-form one" style={{ marginTop: 12 }}>
        {ch === 'sms' ? (
          <Field label={`Text ${n} · day ${step.day_offset ?? ''}${step.send_hour != null ? `, about ${step.send_hour > 12 ? step.send_hour - 12 : step.send_hour}${step.send_hour >= 12 ? ' PM' : ' AM'}` : ''}`}
            hint={`${val.length} characters before the sign-off and "Reply STOP to opt out."`}>
            <textarea rows={5} value={val} disabled={!isManager || saving} onChange={e => set(e.target.value)} />
          </Field>
        ) : (<>
          <Field label={`Email ${n} subject · day ${step.day_offset ?? ''}`}>
            <input value={val.subject} disabled={!isManager || saving} onChange={e => set({ ...val, subject: e.target.value })} />
          </Field>
          <Field label={`Email ${n} body`}>
            <textarea rows={11} value={val.body} disabled={!isManager || saving} onChange={e => set({ ...val, body: e.target.value })} />
          </Field>
        </>)}
      </div>
      <p className="sci-micro sci-muted">
        Personalization: <code>{'{first_name}'}</code> <code>{'{location_name}'}</code> <code>{'{primary_contact_name}'}</code>{' '}
        <code>{'{appointment_options}'}</code>{ch === 'email' ? <> <code>{'{location_phone}'}</code> <code>{'{booking_line}'}</code></> : null}.
        Don't add a certificate value, expiration date or guarantee - none is on file.
      </p>
      {cur.missing_steps?.length > 0 && <p className="sci-micro" style={{ color: '#8a5a00' }}>Blank steps: {cur.missing_steps.join(', ')}</p>}
      {isManager && (
        <button type="button" className="sci-btn gold" disabled={!changed || saving} onClick={save}>
          <Icon name="check" />{saving ? 'Saving…' : 'Save draft'}
        </button>
      )}
      {note && <p className="sci-micro" role="status">{note}</p>}
    </Panel>
  )
}

/* #441: who is in Central Alabama and how each family can be reached. Counts
   come from real message records - approved is not the same as sent. */
function Roster({ locations }) {
  const [loc, setLoc] = useState('')
  const [q, setQ] = useState('')
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    setD(null); setErr('')
    api.get(`/program/central-alabama/roster${loc ? `?location_id=${encodeURIComponent(loc)}` : ''}`)
      .then(setD).catch(e => setErr(errText(e)))
  }, [loc])
  const rows = (d?.rows || []).filter(r => !q.trim() || `${r.name} ${r.location}`.toLowerCase().includes(q.trim().toLowerCase()))
  return (
    <Panel title="Central Alabama families" labelledBy="sci-ca-roster" style={{ gridColumn: '1 / -1' }}
      aside={d ? <span className="sci-micro sci-muted">{num(d.people)} people · {d.campaign_active ? 'campaign on' : 'campaign off'}</span> : null}>
      <ErrorLine text={err} />
      {!d ? <Loading /> : (<>
        <p className="sci-micro" style={{ marginTop: 0 }}>
          <b>{num(d.with_phone)}</b> can get texts and email · <b>{num(d.email_only)}</b> email only (no phone on file)
          {d.unreachable ? <> · <b>{num(d.unreachable)}</b> no phone or email</> : null} · sent so far: {num(d.texts_sent)} texts, {num(d.emails_sent)} emails ·
          {' '}{num(d.replied)} replied · {num(d.booked)} booked
        </p>
        <div className="sci-form" style={{ marginBottom: 8 }}>
          <Field label="Location">
            <select value={loc} onChange={e => setLoc(e.target.value)}>
              <option value="">All locations</option>
              {locations.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
          </Field>
          <Field label="Find a family"><input value={q} onChange={e => setQ(e.target.value)} placeholder="Name or location" /></Field>
        </div>
        <div className="sci-tablewrap" style={{ maxHeight: 420, overflow: 'auto' }}>
          <table className="sci-table compact">
            <thead><tr><th>Family</th><th>Location</th><th>Reached by</th><th>Sent</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.lead_id}>
                  <td><a href={`/program?tab=leads&person=${r.lead_id}`}>{r.name || 'Contact'}</a></td>
                  <td className="sci-small">{r.location || '—'}</td>
                  <td className="sci-small">{r.path}</td>
                  <td className="sci-small">{r.texts_sent} texts · {r.emails_sent} emails</td>
                  <td className="sci-small">{r.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>)}
    </Panel>
  )
}
