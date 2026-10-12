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

  useEffect(() => {
    api.get('/program/campaigns').then(d => { setRows(d); setCur(c => c || d[0] || null) }).catch(e => setErr(errText(e)))
    api.get('/program/health').then(h => setSms(h.sms || null)).catch(() => setSms(null))
  }, [])
  useEffect(() => { if (locationId && homes.some(l => l.location_id === locationId)) setLoc(locationId) }, [locationId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!cur || !loc) { setPreview(null); return undefined }
    let alive = true
    setPreview(null)
    api.get(`/program/campaigns/${cur.id}/preview?location_id=${encodeURIComponent(loc)}&touch=${touch}`)
      .then(p => { if (alive) { setPreview(p); setErr('') } }).catch(e => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [cur, loc, touch])

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
        <Banner icon="pause" title="All SCI campaigns are off">
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
            <Tabs items={[['first', 'First touch'], ['followup', 'Follow-up']]} value={touch} onChange={setTouch} label="Touch" />
            {preview && !preview.ok && <div className="sci-alert"><Icon name="alert" />{preview.reason}</div>}
            {!preview && <Loading label="Rendering preview…" />}
            {preview?.ok && (
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
