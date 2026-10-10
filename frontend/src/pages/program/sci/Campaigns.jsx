/* Campaigns: the catalog, a per-location preview of the real text and email
   (GET /program/campaigns/{id}/preview), and the on/off switch. Status shown:
   ACTIVE (switched on), OFF, or BLOCKED (the server cannot text: the line is
   not configured or not approved). Switching on still enrolls no one, and
   every send is checked again on the server (consent, STOP, DNC, suppression,
   the SCI switch and the approved sender). */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { LANDSCAPE, Banner, Chip, Empty, ErrorLine, Field, Loading, PageHead, errText } from './ui'

const PURPOSE = {
  cemetery_x_sell: 'Additional cemetery planning options', cremation: 'Cremation and memorial options',
  general_survey: 'Location-specific feedback', life_story: 'Life story and memorial planning',
  re_engagement: 'Follow-up on an earlier conversation', seminar: 'Seminar sign-ups: savings certificate and planning guide',
  veteran_official: 'Veteran benefits information', veteran_planning_guide: 'Veteran Benefits Guide requests',
  veteran_spanish: 'Veteran benefits, Spanish callouts', web_lead: 'Website information requests',
}

export default function Campaigns({ isManager, locationId, locations }) {
  const [rows, setRows] = useState(null)
  const [cur, setCur] = useState(null)
  const [loc, setLoc] = useState(locationId || locations.find(l => !l.is_review_bucket)?.location_id || '')
  const [touch, setTouch] = useState('first')
  const [preview, setPreview] = useState(null)
  const [sms, setSms] = useState(null)          // the server's texting state, from /program/health
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.get('/program/campaigns').then(d => { setRows(d); setCur(c => c || d[0] || null) }).catch(e => setErr(errText(e)))
    api.get('/program/health').then(h => setSms(h.sms || null)).catch(() => setSms(null))
  }, [])
  useEffect(() => { if (locationId) setLoc(locationId) }, [locationId])
  useEffect(() => {
    if (!cur || !loc) { setPreview(null); return }
    let alive = true
    api.get(`/program/campaigns/${cur.id}/preview?location_id=${encodeURIComponent(loc)}&touch=${touch}`)
      .then(p => { if (alive) { setPreview(p); setErr('') } }).catch(e => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [cur, loc, touch])

  const blocked = sms ? (sms.status === 'fail' || sms.approved === false) : false
  const statusOf = f => (blocked ? ['BLOCKED', 'bad'] : f.is_active ? ['ACTIVE', 'ok'] : ['OFF', ''])
  const replace = f => { setCur(f); setRows(rs => rs.map(r => (r.id === f.id ? f : r))) }
  const setMode = async (key, value) => {
    try { replace(await api.patch(`/program/campaigns/${cur.id}`, { [key]: value })) } catch (e) { setErr(errText(e)) }
  }
  const toggle = async () => {
    let confirm = null
    if (!cur.is_active) {
      confirm = window.prompt(`Type "${cur.name}" to switch this campaign on. Switching on lets automated messages go to contacts already enrolled in it; it enrolls no one by itself.`)
      if (confirm == null) return
    }
    setBusy(true)
    try { replace(await api.post(`/program/campaigns/${cur.id}/activation`, { active: !cur.is_active, confirm })) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  if (!rows && !err) return <><PageHead title="Outreach campaigns" /><Loading /></>
  const active = (rows || []).filter(r => r.is_active).length

  return (
    <>
      <PageHead title="Outreach campaigns" sub="Written once, personalized per location, with launch safeguards enforced on the server." />
      {blocked ? (
        <Banner tone="bad" title="Texting is blocked on the server">{sms.detail}. No campaign text can go out until this is fixed (see Health).</Banner>
      ) : active === 0 ? (
        <Banner title="All SCI campaigns are off">
          Nothing is sent automatically.{sms?.approved_scope ? ` The 844 line's approved scope is ${sms.approved_scope}.` : ''} Each text is still checked for consent, STOP and do-not-call before it goes out.
        </Banner>
      ) : (
        <Banner tone="ok" title={`${active} campaign(s) switched on`}>Automated messages go only to contacts already enrolled in those campaigns, and only after the server's consent, STOP and do-not-call checks.</Banner>
      )}
      <ErrorLine text={err} />

      <div className="sci-grid-2">
        <section className="sci-panel sci-pad" aria-labelledby="sci-catalog">
          <div className="sci-panel-head"><h2 id="sci-catalog">Campaign catalog</h2><Chip tone={active ? 'ok' : 'warn'}>{active} active</Chip></div>
          {(rows || []).length === 0 && <Empty>No campaigns set up yet.</Empty>}
          <div className="sci-rows">
            {(rows || []).map(f => {
              const [label, tone] = statusOf(f)
              return (
                <div className="sci-camp" key={f.id} style={cur?.id === f.id ? { background: '#f6f9f9', borderRadius: 12 } : undefined}>
                  <span className="sci-camp-img" style={{ backgroundImage: `url(${LANDSCAPE})` }} aria-hidden="true" />
                  <div className="sci-camp-body">
                    <b>{f.name}</b>
                    <div className="sci-row-gap" style={{ margin: '4px 0' }}><Chip tone={tone}>{label}</Chip>{f.language && f.language !== 'en' && <Chip plain>{f.language}</Chip>}</div>
                    <small>{PURPOSE[f.key] || f.asset_category || 'Outreach campaign'}</small>
                  </div>
                  <button type="button" className="sci-btn sm" aria-pressed={cur?.id === f.id} onClick={() => setCur(f)}>Preview</button>
                </div>
              )
            })}
          </div>
        </section>

        {cur && (
          <section className="sci-panel sci-pad" aria-labelledby="sci-preview">
            <div className="sci-panel-head"><h2 id="sci-preview">Message preview</h2><Chip tone={statusOf(cur)[1]}>{statusOf(cur)[0]}</Chip></div>
            <div className="sci-form" style={{ marginBottom: 14 }}>
              <Field label="Preview for location">
                <select value={loc} onChange={e => setLoc(e.target.value)}>
                  {locations.filter(l => !l.is_review_bucket).map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                </select>
              </Field>
              <Field label="Touch">
                <select value={touch} onChange={e => setTouch(e.target.value)}>
                  <option value="first">First touch</option><option value="followup">Engaged / follow-up</option>
                </select>
              </Field>
            </div>
            {preview && !preview.ok && <div className="sci-alert">{preview.reason}</div>}
            {!preview && <Loading label="Rendering preview…" />}
            {preview?.ok && (
              <>
                <p className="sci-eyebrow" style={{ color: 'var(--sub)' }}>{cur.name} · text message</p>
                <div className="sci-preview-sms">{preview.sms}</div>
                <p className="sci-micro sci-muted">From: {preview.from_display_name}</p>
                <p className="sci-eyebrow" style={{ color: 'var(--sub)', marginTop: 16 }}>Email · {preview.email_subject}</p>
                <div className="sci-preview-email">{preview.email_body}</div>
                <p className="sci-micro sci-muted">Flyer: {preview.flyer_available ? (preview.email_mode === 'attached' ? 'attached PDF' : preview.email_mode === 'hosted' ? 'hosted link' : 'not included') : 'none uploaded yet'}</p>
              </>
            )}
            <div className="sci-form" style={{ marginTop: 14 }}>
              <Field label="First-touch email">
                <select value={cur.first_touch_email_mode} disabled={!isManager} onChange={e => setMode('first_touch_email_mode', e.target.value)}>
                  <option value="none">No flyer</option><option value="hosted">Hosted / view flyer</option><option value="attached">Attached PDF</option>
                </select>
              </Field>
              <Field label="Follow-up email">
                <select value={cur.followup_email_mode} disabled={!isManager} onChange={e => setMode('followup_email_mode', e.target.value)}>
                  <option value="none">No flyer</option><option value="hosted">Hosted / view flyer</option><option value="attached">Attached PDF</option>
                </select>
              </Field>
            </div>
            <div className="sci-banner info" style={{ marginTop: 16 }}>
              <div>A preview does not establish anyone's opt-in. The server checks consent, STOP, do-not-call and suppression on every send.</div>
            </div>
            {isManager ? (
              <div className="sci-savebar" style={{ justifyContent: 'space-between' }}>
                <span className="sci-small">This campaign is <b>{cur.is_active ? 'ON' : 'OFF'}</b>.</span>
                <button type="button" className={`sci-btn ${cur.is_active ? 'danger' : 'primary'}`} disabled={busy || (blocked && !cur.is_active)} onClick={toggle}>
                  {busy ? 'Saving…' : cur.is_active ? 'Switch off' : 'Switch on…'}
                </button>
              </div>
            ) : <p className="sci-micro sci-muted">Only a workspace manager can switch campaigns on or off.</p>}
          </section>
        )}
      </div>
      {isManager && <EmailRunner />}
    </>
  )
}

function EmailRunner() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.get('/program/email-touches').then(setD).catch(e => setErr(errText(e))) }, [])
  if (err) return <div style={{ marginTop: 16 }}><ErrorLine text={err} /></div>
  if (!d) return null
  const c = d.counts || {}
  return (
    <section className="sci-panel sci-pad" style={{ marginTop: 16 }} aria-labelledby="sci-email-run">
      <div className="sci-panel-head"><h2 id="sci-email-run">Campaign email</h2><Chip tone={d.enabled ? 'ok' : ''}>{d.enabled ? 'On' : 'Off'}</Chip></div>
      <p className="sci-small sci-muted" style={{ marginTop: 0 }}>
        {d.enabled ? `At most ${d.daily_cap} a day (${d.used_today} so far today), ${d.batch} per pass, 9am–6pm local, follow-up after ${d.followup_days} days.` : 'Nothing is emailed automatically until it is switched on for this deployment.'}
        {' '}A reply on any channel ends a contact's sequence.
      </p>
      <p className="sci-small">Would go out now: <b>{d.would_send_total}</b> · held (reply, review, opt-out, no approved flyer): <b>{d.skipped}</b>{d.held_no_flyer ? ` (${d.held_no_flyer} waiting for a flyer)` : ''} · sent: <b>{c.sent || 0}</b> · blocked: <b>{c.blocked || 0}</b> · failed: <b>{c.failed || 0}</b>{c.unknown ? <> · <b>{c.unknown} outcome unknown — check before resending</b></> : null}</p>
      {d.would_send?.length > 0 && (
        <div className="sci-tablewrap">
          <table className="sci-table">
            <thead><tr><th>Lead ID</th><th>Location</th><th>Campaign</th><th>Touch</th><th>Email</th><th>Subject</th></tr></thead>
            <tbody>
              {d.would_send.slice(0, 25).map(w => (
                <tr key={`${w.lead_id}-${w.touch}`}>
                  <td>{w.source_lead_id}</td><td>{w.location}</td><td>{w.family}</td>
                  <td>{w.touch === 1 ? 'first' : 'follow-up'}</td>
                  <td>{w.email_mode === 'attached' ? 'attached PDF' : w.email_mode === 'hosted' ? 'hosted link' : 'no flyer'}</td>
                  <td>{w.subject}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
