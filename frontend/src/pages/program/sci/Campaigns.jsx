/* Campaigns: the catalog, a live per-location preview of the real text and
   email (GET /program/campaigns/{id}/preview - sends nothing), and guarded
   controls. Status: ON (switched on), OFF, or BLOCKED (the server cannot text:
   line not configured or not approved).

   ACTIVATION IS DANGEROUS. Switching a campaign on lets automated messages go
   to contacts ALREADY enrolled in it, even though it enrolls no one new. It
   needs the campaign's exact name typed (enforced again on the server:
   POST /campaigns/{id}/activation) and is audit-logged. Nothing on this page
   switches anything on by itself. */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import {
  Banner, Chip, Empty, ErrorLine, Field, FilterChips, Icon, Kpi, Loading, PageHead, Panel, SearchBox, Tabs, TypedConfirm,
  errText, num, when,
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
  // #441: unsaved step wording, kept here so the preview can show it and a
  // campaign switch can warn before throwing it away.
  const [draft, setDraft] = useState({})          // 'sms:2' -> text | 'email:3' -> {subject, body}
  const [sample, setSample] = useState('Pat')     // made-up first name for previews
  const [roster, setRoster] = useState(null)
  const [rosterErr, setRosterErr] = useState('')
  const previewRef = useRef(null)
  const [journeyVersion, setJourneyVersion] = useState(0)
  const dirty = Object.keys(draft).length > 0

  useEffect(() => {
    api.get('/program/campaigns').then(d => {
      setRows(d)
      setCur(c => d.find(f => f.key === targetCampaign) || c || d[0] || null)
    }).catch(e => setErr(errText(e)))
    api.get('/program/health').then(h => setSms(h.sms || null)).catch(() => setSms(null))
  }, []) // initial selection follows the deep link if provided
  useEffect(() => { if (locationId && homes.some(l => l.location_id === locationId)) setLoc(locationId) }, [locationId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setDraft({})
    if (cur?.own_copy) setTouch('sms:1')
    else if (touch.includes(':')) setTouch('first')
  }, [cur?.id])
  const loadRoster = () => {
    setRosterErr('')
    api.get('/program/central-alabama/roster').then(setRoster).catch(e => setRosterErr(errText(e)))
  }
  useEffect(() => { if (cur?.key === 'central_alabama_ms' && !roster) loadRoster() }, [cur?.key]) // eslint-disable-line react-hooks/exhaustive-deps
  // Leaving the page with unsaved wording asks first.
  useEffect(() => {
    if (!dirty) return undefined
    const warn = e => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const stepDraft = cur?.own_copy ? draft[touch] : undefined
  useEffect(() => {
    if (!cur || !loc) { setPreview(null); return undefined }
    let alive = true
    const t = setTimeout(() => {
      const name = (sample || '').trim()
      const req = stepDraft !== undefined
        // unsaved wording: rendered by the same server code, saved nowhere
        ? api.post(`/program/campaigns/${cur.id}/preview-draft`, {
          location_id: loc, touch, first_name: name || null,
          ...(touch.startsWith('email') ? { subject: stepDraft.subject, body: stepDraft.body } : { sms: stepDraft }),
        })
        : api.get(`/program/campaigns/${cur.id}/preview?location_id=${encodeURIComponent(loc)}&touch=${touch}${cur.own_copy && name ? `&first_name=${encodeURIComponent(name)}` : ''}`)
      req.then(p => { if (alive) { setPreview(p); setErr('') } }).catch(e => { if (alive) setErr(errText(e)) })
    }, stepDraft !== undefined ? 450 : 0)
    return () => { alive = false; clearTimeout(t) }
  }, [cur, loc, touch, previewVersion, sample, stepDraft])
  const pick = f => {
    if (f.id === cur?.id) return
    if (dirty && !window.confirm('You have unsaved message changes. Leave them without saving?')) return
    setCur(f)
  }
  const showPreview = () => {
    setPreviewVersion(v => v + 1)
    previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  // Quick location picks for previews: Southern Heritage, Kilgroe, then the next home.
  const quickHomes = useMemo(() => {
    const find = (...words) => {
      for (const w of words) {
        const hit = homes.find(l => (l.name || '').toLowerCase().includes(w))
        if (hit) return hit
      }
      return undefined
    }
    const picks = [find('southern heritage funeral home', 'southern heritage'), find('kilgroe')].filter(Boolean)
    const other = homes.find(l => !picks.includes(l) && /funeral home/i.test(l.name || '')) || homes.find(l => !picks.includes(l))
    return other ? [...picks, other] : picks
  }, [locations]) // eslint-disable-line react-hooks/exhaustive-deps

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

      {cur?.own_copy && <CampaignHeader cur={cur} roster={roster} dirty={dirty} />}

      <div className="sci-grid-camp">
        <Panel title="Campaign catalog" labelledBy="sci-catalog" aside={<span className="sci-micro sci-muted">{(rows || []).length} families</span>}>
          <div style={{ marginBottom: 8 }}><SearchBox id="sci-camp-q" value={q} onChange={setQ} placeholder="Find a campaign" label="Find a campaign" width="100%" /></div>
          {list.length === 0 && <Empty icon="campaigns">{(rows || []).length ? 'No campaign matches.' : 'No campaigns set up yet.'}</Empty>}
          <div className="sci-list" style={{ maxHeight: 520 }}>
            {list.map(f => {
              const [label, tone] = statusOf(f)
              const p = perf[f.key]
              return (
                <button key={f.id} type="button" className="sci-camp" aria-current={cur?.id === f.id} onClick={() => pick(f)}>
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
          <div ref={previewRef} style={{ minWidth: 0 }}>
          <Panel title="Campaign preview" labelledBy="sci-preview" aside={<span className="sci-micro sci-muted">Rendered by the server · sends nothing</span>}>
            <div className="sci-form" style={{ marginBottom: 6 }}>
              {cur.own_copy
                ? <Field label="Sample first name" hint="A made-up family, for the preview only"><input value={sample} maxLength={30} onChange={e => setSample(e.target.value)} /></Field>
                : <Field label="Selected campaign"><input value={cur.name} disabled /></Field>}
              <Field label="Location">
                <select value={loc} onChange={e => setLoc(e.target.value)}>
                  {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                </select>
              </Field>
            </div>
            {cur.own_copy ? (<>
              {quickHomes.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '0 0 8px' }} role="group" aria-label="Quick location">
                  {quickHomes.map(l => (
                    <button key={l.location_id} type="button" className={`sci-btn sm${loc === l.location_id ? ' primary' : ''}`}
                      aria-pressed={loc === l.location_id} onClick={() => setLoc(l.location_id)}>{l.name}</button>
                  ))}
                </div>
              )}
              <p className="sci-micro sci-muted" style={{ margin: '0 0 8px' }}>
                Showing <b>{stepName(cur, touch)}</b>{stepDraft !== undefined ? <> · <b style={{ color: '#8a5a00' }}>unsaved draft</b></> : ' · saved wording'} - pick a step in the editor below.
              </p>
            </>) : (
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
                <p className="sci-micro sci-muted" style={{ marginTop: 6 }}>
                  From <b>{preview.sender || '—'}</b> at <b>{preview.location}</b>
                  {preview.appointment_options ? <> · meeting options on file: {preview.appointment_options}</> : null}
                </p>
                {preview.problems?.length > 0 && <div className="sci-alert" role="alert"><Icon name="alert" />{preview.problems.join('; ')}</div>}
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
          </div>
        ) : <Panel title="Campaign preview"><Empty icon="campaigns">Pick a campaign.</Empty></Panel>}

        {cur?.own_copy && (
          <SequenceEditor cur={cur} isManager={isManager} touch={touch} setTouch={setTouch}
            draft={draft} setDraft={setDraft} onPreview={showPreview}
            onSaved={f => { replace(f); setPreviewVersion(v => v + 1) }} onError={setErr} />
        )}
        {cur?.key === 'central_alabama_ms' && <DeliveryPanel key={`d-${cur.is_active}-${journeyVersion}`} />}
        {cur?.key === 'central_alabama_ms' && (
          <JourneyPanel cur={cur} isManager={isManager} version={journeyVersion}
            onDone={() => { setJourneyVersion(v => v + 1); loadRoster() }} />
        )}
        {cur?.key === 'central_alabama_ms' && <Roster locations={homes} d={roster} err={rosterErr} onReload={loadRoster} isManager={isManager} />}

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
        {cur?.own_copy
          ? <p style={{ margin: 0 }}>Switching on <b>sends nothing by itself</b>. Messages go only to families whose journey you start next (Start / Resume Journey), after the server's consent, STOP, do-not-call and suppression checks. This is audit-logged.</p>
          : <p style={{ margin: 0 }}>Automated texts and emails may start going to contacts <b>already enrolled</b> in this campaign, after the server's consent, STOP, do-not-call and suppression checks. This is audit-logged.</p>}
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


/* #441: plain step names - "First contact" / "Follow-up 2", with the channel. */
function stepName(cur, touch) {
  const [c, n] = (touch || 'sms:1').split(':')
  const list = (c === 'email' ? cur?.steps?.email : cur?.steps?.sms) || []
  const x = list.find(y => String(y.touch) === String(n))
  return `${c === 'email' ? 'Email' : 'Text'} · ${x?.label || (n === '1' ? 'First contact' : `Follow-up ${Number(n) - 1}`)}`
}

const hourText = h => (h == null ? '' : `${h > 12 ? h - 12 : h || 12}${h >= 12 ? ' PM' : ' AM'}`)

/* #441: the campaign at a glance - on/off, who is in it, how they can be
   reached, whether every step has its wording, and when it was last saved. */
function CampaignHeader({ cur, roster, dirty }) {
  const steps = cur.steps || { sms: [], email: [] }
  const total = steps.sms.length + steps.email.length
  const missing = cur.missing_steps || []
  const problems = cur.copy_problems || []
  const ready = !missing.length && !problems.length
  return (
    <Panel title={cur.name} labelledBy="sci-ca-head" style={{ marginBottom: 14 }}
      aside={<Chip tone={cur.is_active ? 'ok' : ''}>{cur.is_active ? 'ON' : 'OFF'}</Chip>}>
      <section className="sci-kpis four" aria-label="Campaign summary" style={{ margin: 0 }}>
        <Kpi icon="users" tone="blue" value={roster ? roster.people : null} label="Approved families"
          note={roster?.held?.location_review?.length ? `${roster.held.location_review.length} held for location review` : undefined} />
        <Kpi icon="sms" tone="green" value={roster ? roster.with_phone : null} label="Text + email"
          note={roster ? `${num(roster.email_only)} email only · ${num(roster.no_phone_on_file)} with no phone on file` : undefined} />
        <Kpi icon="check" tone={ready ? 'green' : 'gold'} value={`${total - missing.length}/${total}`} label="Steps with wording"
          note={dirty ? 'Unsaved changes' : ready ? 'Draft complete' : problems.length ? 'Needs a fix' : `Blank: ${missing.join(', ')}`} />
        <Kpi icon="clock" tone="purple" value={cur.last_saved_at ? when(cur.last_saved_at) : '—'} label="Last saved"
          note={cur.last_saved_by ? `by ${cur.last_saved_by}` : undefined} />
      </section>
      <p className="sci-micro sci-muted" style={{ margin: '10px 0 0' }}>
        {cur.is_active ? 'Switched on: messages go only to families whose journey has started, after the server’s consent, STOP and do-not-call checks.'
          : 'Off - nothing automated goes out.'}
        {' '}Switching it on sends nothing by itself: each family's journey is started below (Start / Resume Journey). Saving or previewing never switches it on.
      </p>
    </Panel>
  )
}

/* #441: every step of a campaign that speaks only its own words (Central
   Alabama). Text steps follow the SCI cadence days, email steps the email
   runner. Save keeps the campaign OFF and sends nothing. */
function SequenceEditor({ cur, isManager, touch, setTouch, draft, setDraft, onPreview, onSaved, onError }) {
  const steps = cur.steps || { sms: [], email: [] }
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  useEffect(() => { setNote('') }, [cur.id])
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
    return c === 'email' ? !(x.subject?.trim() && x.body?.trim()) : !x.body?.trim()
  }
  const problemsFor = (c, x) => (cur.copy_problems || []).filter(p => p.startsWith(`${c === 'email' ? 'Email' : 'Text'} ${x.touch} `))
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
  const discard = () => { setDraft({}); setNote('Changes discarded.') }
  const status = (c, x) => {
    const k = `${c}:${x.touch}`
    if (isBlank(c, x)) return ['Blank', 'bad']
    if (k in draft) return ['Unsaved', 'warn']
    if (problemsFor(c, x).length) return ['Needs a fix', 'bad']
    return ['Ready', 'ok']
  }
  const row = (c, x) => {
    const k = `${c}:${x.touch}`
    const on = touch === k
    const [label, tone] = status(c, x)
    return (
      <button key={k} type="button" onClick={() => setTouch(k)} aria-pressed={on}
        className={`sci-btn sm${on ? ' primary' : ''}`}
        style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 2, textAlign: 'left', minWidth: 0, width: '100%', padding: '6px 10px' }}>
        <span><Icon name={c === 'email' ? 'mail' : 'sms'} size={12} /> <b>{x.label}</b></span>
        <Chip tone={tone} plain>{label}</Chip>
        <span className="sci-micro" style={{ opacity: 0.85, gridColumn: '1 / -1' }}>
          {c === 'email' ? 'Email' : 'Text'} · day {x.day_offset}{c === 'sms' && x.send_hour != null ? `, about ${hourText(x.send_hour)}` : ''}{x.theme ? ` · ${x.theme}` : ''}
        </span>
      </button>
    )
  }
  const mine = problemsFor(ch, step)
  return (
    <Panel title={`${cur.name} — message sequence`} labelledBy="sci-seq" style={{ gridColumn: '1 / -1' }}
      aside={changed ? <Chip tone="warn">{Object.keys(draft).length} unsaved</Chip> : <span className="sci-micro sci-muted">All changes saved</span>}>
      <p className="sci-micro sci-muted" style={{ marginTop: 0 }}>
        Every step belongs only to this campaign and has its own wording. Texts follow the SCI cadence days; emails follow
        the email runner. A blank step sends nothing, and the campaign can't be switched on while any step is blank.
        Saving never switches the campaign on.
      </p>
      {changed && (
        <div className="sci-alert" role="status" style={{ marginBottom: 10 }}>
          <Icon name="alert" />You have unsaved changes in {Object.keys(draft).map(k => stepName(cur, k)).join(', ')}. Save draft to keep them.
        </div>
      )}
      <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
        <div>
          <b className="sci-micro">Texts ({steps.sms.length})</b>
          <div style={{ display: 'grid', gap: 6, marginTop: 4 }}>{steps.sms.map(x => row('sms', x))}</div>
        </div>
        <div>
          <b className="sci-micro">Emails ({steps.email.length})</b>
          <div style={{ display: 'grid', gap: 6, marginTop: 4 }}>{steps.email.map(x => row('email', x))}</div>
        </div>
      </div>
      <div className="sci-form one" style={{ marginTop: 12 }}>
        {ch === 'sms' ? (
          <Field label={`Text · ${step.label || ''} · day ${step.day_offset ?? ''}${step.send_hour != null ? `, about ${hourText(step.send_hour)}` : ''}`}
            error={!val.trim() ? 'This step is blank - nothing will be sent for it.' : mine.length ? mine.join('; ') : undefined}
            hint={`${val.length} characters before the sign-off and "Reply STOP to opt out."`}>
            <textarea rows={5} value={val} disabled={!isManager || saving} onChange={e => set(e.target.value)} />
          </Field>
        ) : (<>
          <Field label={`Email · ${step.label || ''} · day ${step.day_offset ?? ''} · subject`}
            error={!val.subject.trim() ? 'A subject is needed.' : undefined}>
            <input value={val.subject} disabled={!isManager || saving} onChange={e => set({ ...val, subject: e.target.value })} />
          </Field>
          <Field label="Email body" error={!val.body.trim() ? 'This step is blank - nothing will be sent for it.' : mine.length ? mine.join('; ') : undefined}>
            <textarea rows={11} value={val.body} disabled={!isManager || saving} onChange={e => set({ ...val, body: e.target.value })} />
          </Field>
        </>)}
      </div>
      <p className="sci-micro sci-muted">
        Personalization: <code>{'{first_name}'}</code> <code>{'{location_name}'}</code> <code>{'{primary_contact_name}'}</code>{' '}
        <code>{'{appointment_options}'}</code>{ch === 'email' ? <> <code>{'{location_phone}'}</code> <code>{'{booking_line}'}</code></> : null}.
        Don't add a certificate value, expiration date or guarantee - none is on file.
        {step.revision ? <> Last saved {when(step.updated_at)}{step.updated_by ? ` by ${step.updated_by}` : ''} (version {step.revision}).</> : null}
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {isManager && (
          <button type="button" className="sci-btn gold" disabled={!changed || saving} onClick={save}>
            <Icon name="check" />{saving ? 'Saving…' : 'Save draft'}
          </button>
        )}
        <button type="button" className="sci-btn" onClick={onPreview}><Icon name="play" />Preview this step</button>
        {isManager && changed && <button type="button" className="sci-btn" disabled={saving} onClick={discard}>Discard changes</button>}
      </div>
      {note && <p className="sci-micro" role="status">{note}</p>}
    </Panel>
  )
}

/* #441: who is in Central Alabama and how each family can be reached. Every
   count comes from real message records - approved is not the same as sent. */
const ROSTER_FILTERS = [
  ['all', 'All approved', () => true],
  ['phone', 'With phone', r => r.has_phone],
  ['email_only', 'Email only', r => !r.has_phone && r.has_email],
  ['not_started', 'Journey not started', r => r.journey === 'Not started'],
  ['awaiting', 'Awaiting contact', r => !r.contacted && !r.replied && !r.booked && !r.stop_dnc && !r.on_hold],
  ['contacted', 'Contacted', r => r.contacted],
  ['replied', 'Replied', r => r.replied],
  ['booked', 'Appointment booked', r => r.booked],
  ['hold', 'On hold', r => r.on_hold],
  ['stop', 'STOP / DNC', r => !!r.stop_dnc],
  ['bounced', 'Email bounced', r => !!r.email_failure || r.email_flagged],
]

function Roster({ locations, d, err, onReload, isManager }) {
  const [open, setOpen] = useState('')
  const [loc, setLoc] = useState('')
  const [q, setQ] = useState('')
  const [show, setShow] = useState('all')
  const base = (d?.rows || []).filter(r => !loc || r.location_id === loc)
  const counts = Object.fromEntries(ROSTER_FILTERS.map(([k, , f]) => [k, base.filter(f).length]))
  const test = (ROSTER_FILTERS.find(x => x[0] === show) || ROSTER_FILTERS[0])[2]
  const rows = base.filter(test).filter(r => !q.trim() || `${r.name} ${r.location}`.toLowerCase().includes(q.trim().toLowerCase()))
  const held = d?.held
  return (
    <Panel title="Central Alabama families" labelledBy="sci-ca-roster" style={{ gridColumn: '1 / -1' }}
      aside={d ? <span className="sci-micro sci-muted">{num(d.people)} approved · {d.campaign_active ? 'campaign on' : 'campaign off'} · <button type="button" className="sci-btn sm" onClick={onReload}><Icon name="refresh" size={12} />Refresh</button></span> : null}>
      <ErrorLine text={err} />
      {!d ? (!err && <Loading />) : (<>
        <p className="sci-micro" style={{ marginTop: 0 }}>
          <b>{num(d.with_phone)}</b> can get texts and email · <b>{num(d.email_only)}</b> email only
          {d.no_phone_on_file ? <> ({num(d.no_phone_on_file)} have no phone on file)</> : null}
          {d.unreachable ? <> · <b>{num(d.unreachable)}</b> no phone or email</> : null} · contacted: {num(d.contacted)} ·
          reached: {num(d.texts_sent)} texts, {num(d.emails_sent)} emails{d.emails_bounced ? <> (plus {num(d.emails_bounced)} {d.emails_bounced === 1 ? 'family' : 'families'} whose email bounced)</> : null} · journeys started: {num(d.journey_started ?? 0)} ·
          {' '}{num(d.replied)} replied · {num(d.booked)} booked
          {d.stop_dnc ? <> · {num(d.stop_dnc)} STOP/DNC</> : null}
        </p>
        <FilterChips label="Show" value={show} onChange={setShow} items={ROSTER_FILTERS.map(([k, l]) => [k, l, counts[k]])} />
        <div className="sci-form" style={{ margin: '8px 0' }}>
          <Field label="Location">
            <select value={loc} onChange={e => setLoc(e.target.value)}>
              <option value="">All locations</option>
              {locations.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
          </Field>
          <Field label="Find a family"><input value={q} onChange={e => setQ(e.target.value)} placeholder="Name or location" /></Field>
        </div>
        <div className="sci-tablewrap" style={{ maxHeight: 460, overflow: 'auto' }}>
          <table className="sci-table compact">
            <thead><tr>
              <th>Family</th><th>Location</th><th>Journey</th><th>Phone</th><th>Email</th><th>Reached by</th><th>Status</th><th>Email outcome</th>
              <th>Last touch</th><th>Next touch</th><th>Reply</th><th>Appointment</th><th>STOP / DNC</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (<Fragment key={r.lead_id}>
                <tr>
                  <td>
                    <button type="button" className="sci-btn sm" aria-expanded={open === r.lead_id}
                      onClick={() => setOpen(o => (o === r.lead_id ? '' : r.lead_id))}>{r.name || 'Contact'}</button>
                  </td>
                  <td className="sci-small">{r.location || '—'}</td>
                  <td className="sci-small">{r.journey}{r.journey_starts_at && r.journey === 'Scheduled' ? ` · ${when(r.journey_starts_at)}` : ''}</td>
                  <td className="sci-small">{r.phone_on_file ? (r.has_phone ? 'Yes' : 'On file, can’t text') : 'None on file'}</td>
                  <td className="sci-small">{r.email_on_file ? 'Yes' : 'None'}</td>
                  <td className="sci-small">{r.path}</td>
                  <td className="sci-small">{r.status}</td>
                  <td className="sci-small" title={r.email_failure || undefined}>{r.email_outcome || '—'}{r.email_flagged ? ' · address flagged' : ''}</td>
                  <td className="sci-small">{r.last_touch ? `${r.last_touch} · ${when(r.last_touch_at)}` : 'None yet'}</td>
                  <td className="sci-small">{r.next_touch ? `${r.next_touch.label} · ${when(r.next_touch.at)}` : '—'}</td>
                  <td className="sci-small">{r.replied ? 'Replied' : '—'}</td>
                  <td className="sci-small">{r.booked ? 'Booked' : '—'}</td>
                  <td className="sci-small">{r.stop_dnc || '—'}</td>
                </tr>
                {open === r.lead_id && (
                  <tr><td colSpan={14}><FamilyHistory leadId={r.lead_id} isManager={isManager} onChanged={onReload} /></td></tr>
                )}
              </Fragment>))}
              {rows.length === 0 && <tr><td colSpan={14} className="sci-small sci-muted">No family matches.</td></tr>}
            </tbody>
          </table>
        </div>
        {held && (held.location_review?.length > 0 || held.excluded_people > 0) && (
          <details className="sci-details" style={{ marginTop: 10 }}>
            <summary>Held out of this campaign ({num((held.location_review || []).length + (held.excluded_people || 0))})</summary>
            <div className="sci-details-body sci-micro">
              {(held.location_review || []).map((h, i) => (
                <p key={i} style={{ margin: '0 0 4px' }}><b>{h.name || 'Contact'}</b> - {h.reason}{h.source_location ? ` (file says "${h.source_location}")` : ''}. Not contacted.</p>
              ))}
              {held.excluded_people > 0 && <p style={{ margin: 0 }}><b>{num(held.excluded_people)}</b> families ({num(held.excluded_records)} records): {held.excluded_reason}</p>}
            </div>
          </details>
        )}
      </>)}
    </Panel>
  )
}

/* #449: what really happened in this campaign, per channel, from the
   message records. Microsoft reports failures only - an email with no
   failure notice is "accepted", never "delivered". */
function DeliveryPanel() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const load = () => { setErr(''); api.get('/program/central-alabama/delivery').then(setD).catch(e => setErr(errText(e))) }
  useEffect(load, [])
  const stat = (v, label, tone) => (
    <div className="sci-stat"><b style={tone ? { color: tone } : undefined}>{v == null ? '—' : num(v)}</b><span>{label}</span></div>
  )
  const e = d?.email || {}
  const s = d?.sms || {}
  const j = d?.journey || {}
  return (
    <Panel title="What actually happened" labelledBy="sci-ca-delivery" style={{ gridColumn: '1 / -1' }}
      aside={<button type="button" className="sci-btn sm" onClick={load}><Icon name="refresh" size={12} />Refresh</button>}>
      <ErrorLine text={err} />
      {!d ? (!err && <Loading />) : (<>
        <p className="sci-micro" style={{ marginTop: 0 }}>
          Campaign is <b>{d.campaign_active ? 'ON' : 'OFF'}</b>{d.owner_paused ? ' (paused by a person)' : ''}.
          {d.last_on ? <> Last switched on {when(d.last_on.at)}{d.last_on.by ? ` by ${d.last_on.by}` : ''}.</> : null}
          {d.last_off ? <> Last paused {when(d.last_off.at)}{d.last_off.by ? ` by ${d.last_off.by}` : ''}.</> : null}
        </p>
        <b className="sci-micro">Families</b>
        <div className="sci-statstrip" style={{ margin: '4px 0 10px', flexWrap: 'wrap' }}>
          {stat(j.approved, 'Approved')}{stat(j.awaiting_start, 'Journey not started')}{stat(j.scheduled, 'Scheduled')}
          {stat(j.started, 'Started')}{stat(j.contacted, 'Reached at least once')}{stat(j.sms_in_progress, 'Texts in progress')}
          {stat(j.email_in_progress, 'Emails in progress')}{stat(j.replied, 'Replied')}{stat(j.booked, 'Booked')}
          {stat(j.on_hold, 'On hold')}{stat(j.completed, 'Completed')}
        </div>
        <b className="sci-micro">Email</b>
        <div className="sci-statstrip" style={{ margin: '4px 0 4px', flexWrap: 'wrap' }}>
          {stat(e.attempted, 'Attempted')}{stat(e.provider_accepted, 'Accepted by Microsoft')}
          {stat(e.accepted_no_failure_reported, 'No failure reported')}
          {stat(e.permanent_bounce, 'Address does not exist', 'var(--bad)')}{stat(e.rejected, 'Refused by their server', 'var(--bad)')}
          {stat(e.expired, 'Never accepted (expired)', 'var(--bad)')}{stat(e.temporary_failure, 'Delayed')}
          {stat(e.blocked, 'Blocked by their spam filter')}{stat(e.sender_config, 'Our setup refused')}{stat(e.unknown, 'Outcome unknown')}{stat(e.held, 'Held')}
          {stat(e.not_attempted, 'Not attempted')}
        </div>
        <p className="sci-micro sci-muted" style={{ margin: '0 0 10px' }}>Delivered: not reported. {e.delivered_note}</p>
        <b className="sci-micro">Text</b>
        <div className="sci-statstrip" style={{ margin: '4px 0 10px', flexWrap: 'wrap' }}>
          {stat(s.scheduled, 'Scheduled (first text)')}{stat(s.attempted, 'Attempted')}{stat(s.accepted, 'Accepted by carrier')}
          {stat(s.delivered, 'Delivered')}{stat(s.failed, 'Failed', 'var(--bad)')}{stat(s.blocked, 'Blocked by checks')}
          {stat(s.remaining, 'Texts remaining')}
        </div>
        {d.history?.length > 0 && (
          <details className="sci-details">
            <summary>On / off and journey history ({d.history.length})</summary>
            <div className="sci-details-body sci-micro">
              {d.history.map((h, i) => <p key={i} style={{ margin: '0 0 4px' }}>{when(h.at)} - {h.action}{h.by ? ` - ${h.by}` : ''}</p>)}
            </div>
          </details>
        )}
      </>)}
    </Panel>
  )
}

/* #449: Start / Resume Journey. A read-only preview first; starting only
   SCHEDULES each family's first message, spaced out, and never starts anyone
   twice. Typed confirmation. The runners send at the scheduled times, through
   every existing check. */
function JourneyPanel({ cur, isManager, version, onDone }) {
  const [imp, setImp] = useState(null)
  const [err, setErr] = useState('')
  const [count, setCount] = useState('')
  const [spacing, setSpacing] = useState(5)
  const [startAt, setStartAt] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const load = () => {
    setErr('')
    api.get(`/program/campaigns/${cur.id}/journey`).then(setImp).catch(e => setErr(errText(e)))
  }
  useEffect(load, [cur.id, cur.is_active, version]) // eslint-disable-line react-hooks/exhaustive-deps
  const n = imp ? (count === '' ? imp.will_start : Math.max(0, Math.min(imp.will_start, parseInt(count, 10) || 0))) : 0
  const phrase = n > 0 ? `START ${n}` : (imp?.would_resume ? 'RESUME' : '')
  const go = async typed => {
    setBusy(true); setErr('')
    try {
      const body = { confirm: typed, spacing_minutes: Number(spacing) || 0 }
      if (count !== '') body.count = n
      if (startAt) body.start_at = new Date(startAt).toISOString()
      setResult(await api.post(`/program/campaigns/${cur.id}/journey/start`, body))
      setConfirmOpen(false); onDone()
    } catch (e) { setErr(errText(e)); setConfirmOpen(false) } finally { setBusy(false) }
  }
  const lastAt = imp && n > 0 ? new Date((startAt ? new Date(startAt) : new Date()).getTime() + (n - 1) * (Number(spacing) || 0) * 60000) : null
  return (
    <Panel title="Start / Resume Journey" labelledBy="sci-ca-journey" style={{ gridColumn: '1 / -1' }}>
      <p className="sci-micro sci-muted" style={{ marginTop: 0 }}>
        Approval and switching the campaign on send nothing. Starting a family's journey schedules their first message;
        texts come first, emails follow. Nobody is started twice, a message that already went is never repeated,
        and families who replied, booked, are on hold or on STOP/DNC are left alone.
      </p>
      <ErrorLine text={err} />
      {!imp ? (!err && <Loading />) : (<>
        <div className="sci-statstrip" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
          <div className="sci-stat"><b>{num(imp.approved)}</b><span>Approved</span></div>
          <div className="sci-stat"><b>{num(imp.already_started)}</b><span>Already started (keep their place)</span></div>
          <div className="sci-stat"><b>{num(imp.will_start)}</b><span>Ready to start</span></div>
          <div className="sci-stat"><b>{num(imp.will_start_text)}</b><span>…with texts</span></div>
          <div className="sci-stat"><b>{num(imp.will_start_email_only)}</b><span>…email only</span></div>
          <div className="sci-stat"><b>{num(imp.would_resume)}</b><span>To resume after a pause</span></div>
        </div>
        {imp.email_1_already_sent > 0 && (
          <p className="sci-micro" style={{ margin: '0 0 6px' }}>
            {num(imp.email_1_already_sent)} of them already got Email 1 - it is not repeated; their emails continue at Email 2, and their first text is Text 1.
          </p>
        )}
        {Object.keys(imp.held || {}).length > 0 && (
          <p className="sci-micro" style={{ margin: '0 0 8px' }}>
            Not started: {Object.entries(imp.held).map(([k, v]) => `${num(v)} ${k.toLowerCase()}`).join(' · ')}
          </p>
        )}
        <div className="sci-form" style={{ marginBottom: 8 }}>
          <Field label="How many to start now" hint={`Leave blank for all ${num(imp.will_start)}`}>
            <input type="number" min={0} max={imp.will_start} value={count} onChange={e => setCount(e.target.value)} placeholder="All" />
          </Field>
          <Field label="Minutes between families" hint="Spreads the first messages out">
            <input type="number" min={0} max={1440} value={spacing} onChange={e => setSpacing(e.target.value)} />
          </Field>
          <Field label="First one at" hint="Blank = now. Contact hours still apply.">
            <input type="datetime-local" value={startAt} onChange={e => setStartAt(e.target.value)} />
          </Field>
        </div>
        {lastAt && <p className="sci-micro sci-muted" style={{ margin: '0 0 8px' }}>The last of these {num(n)} would be scheduled around {when(lastAt.toISOString())}.</p>}
        {isManager ? (
          <button type="button" className="sci-btn gold" disabled={!cur.is_active || !phrase || busy}
            title={!cur.is_active ? 'Switch the campaign on first - that alone sends nothing' : undefined}
            onClick={() => setConfirmOpen(true)}>
            <Icon name="play" />{n > 0 ? `Start ${num(n)} journeys…` : imp.would_resume ? 'Resume journeys…' : 'Nothing to start'}
          </button>
        ) : <p className="sci-micro sci-muted">Only a workspace manager can start journeys.</p>}
        {!cur.is_active && <p className="sci-micro" style={{ marginTop: 6 }}>The campaign is off. Switch it on in Campaign controls first - that alone sends nothing.</p>}
        {result && (
          <p className="sci-micro" role="status" style={{ marginTop: 8 }}>
            Started {num(result.started)} ({num(result.texting)} with texts, {num(result.email_only)} email only)
            {result.resumed ? `, resumed ${num(result.resumed)}` : ''}. First at {when(result.first_at)}, last around {when(result.last_at)}. Nothing was sent by this button.
          </p>
        )}
      </>)}
      <TypedConfirm open={confirmOpen} title="Start these journeys?" phrase={phrase} actionLabel="Start journeys"
        tone="dangerfill" busy={busy} onCancel={() => setConfirmOpen(false)} onConfirm={go}>
        <p style={{ margin: 0 }}>
          {n > 0 ? <>{num(n)} families get their first message scheduled, {Number(spacing) || 0} minutes apart. </> : null}
          {imp?.would_resume ? <>{num(imp.would_resume)} paused families pick up where they left off, re-spaced. </> : null}
          Messages go at the scheduled times through every check (contact hours, STOP, do-not-call, replies). This is audit-logged.
        </p>
      </TypedConfirm>
    </Panel>
  )
}

/* #449: one family's real history in this campaign, and - after a bounce -
   a controlled correction. Correcting never resends anything. */
function FamilyHistory({ leadId, isManager, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [email, setEmail] = useState('')
  const [checked, setChecked] = useState(false)
  const [note, setNote] = useState('')
  const load = () => api.get(`/program/central-alabama/family/${leadId}/events`).then(setD).catch(e => setErr(errText(e)))
  useEffect(() => { load() }, [leadId]) // eslint-disable-line react-hooks/exhaustive-deps
  const recover = async () => {
    setErr(''); setNote('')
    try {
      const r = await api.post(`/program/people/${leadId}/email-recovery`, email.trim() ? { email: email.trim() } : { confirm_address_is_right: checked })
      setNote(r.note); setEmail(''); setChecked(false); load(); onChanged()
    } catch (e) { setErr(errText(e)) }
  }
  if (err && !d) return <ErrorLine text={err} />
  if (!d) return <Loading />
  return (
    <div className="sci-micro" style={{ padding: '6px 4px' }}>
      <ul style={{ margin: '0 0 8px', paddingLeft: 18 }}>
        {d.events.map((e, i) => (
          <li key={i}>{when(e.at)} · {e.channel !== '-' ? `${e.channel} · ` : ''}<b>{e.what}</b>{e.detail ? ` - ${e.detail}` : ''}</li>
        ))}
        {d.events.length === 0 && <li>Nothing yet.</li>}
      </ul>
      {d.email_flagged && (
        <div className="sci-alert" style={{ display: 'block' }}>
          <p style={{ margin: '0 0 6px' }}><Icon name="alert" /> Email address flagged: {d.email_flag_reason || 'it bounced'}. No more email goes to it. Texts are not affected.</p>
          {isManager && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <input value={email} onChange={e => setEmail(e.target.value)} placeholder="Corrected email address" style={{ minWidth: 220 }} />
              <label style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                <input type="checkbox" checked={checked} disabled={!!email.trim()} onChange={e => setChecked(e.target.checked)} />I checked this address and it is right
              </label>
              <button type="button" className="sci-btn sm" disabled={!email.trim() && !checked} onClick={recover}>Save</button>
            </div>
          )}
        </div>
      )}
      <ErrorLine text={err} />
      {note && <p role="status">{note}</p>}
      <a href={`/program?tab=leads&person=${leadId}`}>Open the full record</a>
    </div>
  )
}
