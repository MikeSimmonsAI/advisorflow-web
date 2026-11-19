/**
 * CLIENT SETUP (frame A, 2026-11-18). The client fills in her own business
 * details in 7 short steps; each one saves straight into the same business
 * record EvoSys sees in Client Configuration. The offer step is read-only:
 * she can only agree to what EvoSys set. Finishing tells EvoSys she is ready
 * for review - outreach stays paused until EvoSys turns it on.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useAcq } from './AcquisitionShell'
import { Icon, Loading, errText, useLoad } from './ui'
import { useT } from './portalText'

export const STEPS = ['business', 'services', 'customers', 'area', 'never', 'calendar', 'offer']
const ICON = { business: 'building', services: 'list', customers: 'users', area: 'pin', never: 'x', calendar: 'calendar', offer: 'doc' }
const list = v => (Array.isArray(v) ? v : []).join(', ')
const split = s => String(s || '').split(/[,\n]/).map(x => x.trim()).filter(Boolean)

// Progress comes from what is actually saved, never from button clicks.
export function stepDone(k, profile, cal) {
  const p = profile || {}
  const s = p.setup || {}
  const has = v => Array.isArray(v) ? v.length > 0 : !!(v && String(v).trim())
  if (k === 'business') return has(p.business_name) && has(p.contact_phone) && has(p.contact_email) && has(p.mailing_address)
  if (k === 'services') return has(p.services)
  if (k === 'customers') return has(p.segments)
  if (k === 'area') return (has(p.service_area) || has(p.service_zips)) && has(p.service_states)
  // "None to add" is a real answer: the client saved this step on purpose.
  if (k === 'never') return (s.never_contact_count || 0) > 0 || (s.steps || []).includes('never')
  if (k === 'calendar') return !!cal?.ready
  if (k === 'offer') return !!s.offer_accepted_at
  return false
}

export function SetupCard({ profile, cal }) {
  const { t } = useT()
  const done = STEPS.filter(k => stepDone(k, profile, cal)).length
  const next = STEPS.find(k => !stepDone(k, profile, cal))
  return (
    <section className="cp-card cp-setup">
      <div className="cp-card__head"><h2>{t('setup_title')}</h2><span className="cp-setup__count">{t('setup_count', { n: done })}</span></div>
      <div className="cp-bar"><i style={{ width: `${(done / STEPS.length) * 100}%` }} /></div>
      <ol className="cp-steps">{STEPS.map(k => (
        <li key={k} className={stepDone(k, profile, cal) ? 'done' : k === next ? 'next' : ''}>
          <span className="cp-steps__tick">{stepDone(k, profile, cal) ? '✓' : ''}</span><Icon name={ICON[k]} size={17} />
          <b>{t('st_' + k)}</b><small>{t('st_' + k + '_sub')}</small></li>))}</ol>
      <Link className="cp-btn" to={`${BASE}/setup${next ? `?step=${next}` : ''}`}>{done ? t('setup_continue') : t('setup_start')}</Link>
    </section>
  )
}

export function Setup() {
  const { status, reloadStatus } = useAcq()
  const { t } = useT()
  const [cal] = useLoad('/acquisition/calendar')
  const q = new URLSearchParams(window.location.search).get('step')
  const [profile, setProfile] = useState(status?.profile)
  const [step, setStep] = useState(STEPS.includes(q) ? q : (STEPS.find(k => !stepDone(k, status?.profile, null)) || 'business'))
  const [f, setF] = useState({})
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  useEffect(() => {
    const p = profile || {}
    // Keep what the person typed if a save fails; only fill from the record
    // for fields they haven't touched yet.
    setF(cur => ({ business_name: p.business_name || '', owner_name: p.owner_name || '', contact_phone: p.contact_phone || '',
           contact_email: p.contact_email || '', website: p.website || '', mailing_address: p.mailing_address || '',
           services: list(p.services), segments: p.segments || [], service_area: list(p.service_area),
           service_zips: list(p.service_zips), service_states: list(p.service_states), never_contact: '', ...cur.__dirty }))
  }, [profile])
  if (!status) return <Loading />
  if (!status.can_manage) return <div className="aq-page cp"><div className="aq-note">{t('admin_only_setup')}</div></div>
  const set = k => e => setF({ ...f, [k]: e.target.value, __dirty: { ...(f.__dirty || {}), [k]: e.target.value } })
  const go = k => { setStep(k); setFail(null); window.scrollTo({ top: 0, behavior: 'smooth' }) }
  const nextOf = k => STEPS[STEPS.indexOf(k) + 1]
  const save = async (body) => {
    setBusy(true); setFail(null)
    try {
      const p = await api.put('/acquisition/profile', { ...body, setup_step: step })
      setF(cur => ({ ...cur, __dirty: {} })); setProfile(p); reloadStatus && reloadStatus()
      const n = nextOf(step); if (n) go(n)
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const accept = async () => {
    setBusy(true); setFail(null)
    try { setProfile(await api.post('/acquisition/setup/accept-offer', {})); reloadStatus && reloadStatus() } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const finish = async () => {
    setBusy(true); setFail(null)
    try { setProfile(await api.post('/acquisition/setup/complete', {})); reloadStatus && reloadStatus() } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  // The client's own calendar: if EvoSys hasn't chosen one, appointments go to
  // theirs. "Connected" is shown only when the server says it is.
  const connectMine = async path => {
    setBusy(true); setFail(null)
    try {
      if (!cal?.booking_user) await api.put('/acquisition/profile', { use_my_calendar: true })
      const r = await api.get(path); window.location.href = r.authorization_url
    } catch (e) { setFail(errText(e)); setBusy(false) }
  }
  const segs = status.segments || []
  const allDone = STEPS.every(k => stepDone(k, profile, cal))
  const completed = !!profile?.setup?.completed_at
  const doneCount = STEPS.filter(k => stepDone(k, profile, cal)).length
  return (
    <div className="aq-page cp">
      <h1 className="cp-h1">{t('setup_title')}</h1>
      <p className="cp-hello">{t('setup_lede')}</p>
      {completed && <div className="cp-done"><Icon name="check" size={20} />{t('setup_done')}</div>}
      <div className="cp-card cp-setupwrap">
        <div className="cp-setupwrap__head"><span>{t('setup_count', { n: doneCount })}</span><div className="cp-bar"><i style={{ width: `${(doneCount / STEPS.length) * 100}%` }} /></div></div>
        <div className="cp-setupgrid">
          <ol className="cp-steps cp-steps--nav">{STEPS.map(k => (
            <li key={k} className={`${stepDone(k, profile, cal) ? 'done' : ''} ${step === k ? 'on' : ''}`}>
              <button onClick={() => go(k)}><span className="cp-steps__tick">{stepDone(k, profile, cal) ? '✓' : ''}</span><Icon name={ICON[k]} size={17} />
                <span><b>{t('st_' + k)}</b><small>{t('st_' + k + '_sub')}</small></span></button></li>))}</ol>
          <div className="cp-stepform">
            <h2>{t('st_' + step)}</h2>
            {step === 'business' && <>
              <Field l={t('f_business')}><input className="cp-input" value={f.business_name || ''} onChange={set('business_name')} /></Field>
              <Field l={t('f_contact')}><input className="cp-input" value={f.owner_name || ''} onChange={set('owner_name')} /></Field>
              <div className="cp-two"><Field l={t('f_phone')}><input className="cp-input" inputMode="tel" value={f.contact_phone || ''} onChange={set('contact_phone')} /></Field>
                <Field l={t('f_email')}><input className="cp-input" type="email" value={f.contact_email || ''} onChange={set('contact_email')} /></Field></div>
              <Field l={t('f_website')}><input className="cp-input" value={f.website || ''} onChange={set('website')} /></Field>
              <Field l={t('f_mailing')}><input className="cp-input" value={f.mailing_address || ''} onChange={set('mailing_address')} placeholder={t('f_mailing_ph')} /></Field>
              <small className="hb-muted">{t('f_mailing_help')}</small>
              <Save t={t} busy={busy} disabled={!f.business_name || !f.contact_phone || !f.contact_email || !f.mailing_address} onClick={() => save({ business_name: f.business_name, owner_name: f.owner_name, contact_phone: f.contact_phone, contact_email: f.contact_email, website: f.website, mailing_address: f.mailing_address })} />
            </>}
            {step === 'services' && <>
              <p className="hb-muted">{t('f_services_help')}</p>
              <textarea className="cp-input" rows={4} value={f.services || ''} onChange={set('services')} placeholder={t('f_services_ph')} />
              <Save t={t} busy={busy} disabled={!split(f.services).length} onClick={() => save({ services: split(f.services) })} />
            </>}
            {step === 'customers' && <>
              <p className="hb-muted">{t('f_customers_help')}</p>
              <div className="cp-chips">{segs.map(s => {
                const on = (f.segments || []).includes(s.key)
                return <button type="button" key={s.key} className={on ? 'on' : ''} onClick={() => setF({ ...f, segments: on ? f.segments.filter(x => x !== s.key) : [...(f.segments || []), s.key] })}>
                  <span>{on ? '✓' : ''}</span>{s.label}</button>
              })}</div>
              <Save t={t} busy={busy} disabled={!(f.segments || []).length} onClick={() => save({ segments: f.segments })} />
            </>}
            {step === 'area' && <>
              <Field l={t('f_cities')}><input className="cp-input" value={f.service_area || ''} onChange={set('service_area')} placeholder={t('f_cities_ph')} /></Field>
              <div className="cp-two"><Field l={t('f_states')}><input className="cp-input" value={f.service_states || ''} onChange={set('service_states')} placeholder="TN" /></Field>
                <Field l={t('f_zips')}><input className="cp-input" value={f.service_zips || ''} onChange={set('service_zips')} placeholder="38103, 38117" /></Field></div>
              <Save t={t} busy={busy} disabled={(!split(f.service_area).length && !split(f.service_zips).length) || !split(f.service_states).length} onClick={() => save({ service_area: split(f.service_area), service_zips: split(f.service_zips), service_states: split(f.service_states) })} />
            </>}
            {step === 'never' && <>
              <p className="hb-muted">{t('f_never_help')}</p>
              {(profile?.setup?.never_contact_count || 0) > 0 && <div className="aq-note">{t('f_never_have', { n: profile.setup.never_contact_count })}</div>}
              <textarea className="cp-input" rows={5} value={f.never_contact || ''} onChange={set('never_contact')} placeholder={t('f_never_ph')} />
              <Save t={t} busy={busy} label={split(f.never_contact).length ? null : t('f_never_none')} onClick={() => save({ never_contact: split(f.never_contact) })} />
            </>}
            {step === 'calendar' && <>
              <p className="hb-muted">{t('f_cal_help')}</p>
              {cal?.ready && <div className="cp-done"><Icon name="check" size={18} />{t('cal_connected')}{cal.provider ? ' · ' + cal.provider : ''}</div>}
              {!cal?.ready && cal?.booking_user && !cal?.is_my_calendar && <p className="hb-muted">{t('f_cal_other', { name: cal.booking_user.name })}</p>}
              {(!cal?.booking_user || cal?.is_my_calendar) && <div className="cp-actions">
                <button className="cp-btn" disabled={busy} onClick={() => connectMine('/calendar/connect')}>{cal?.ready ? t('f_cal_reconnect_g') : t('f_cal_google')}</button>
                <button className="cp-btn cp-btn--ghost" disabled={busy} onClick={() => connectMine('/microsoft/connect')}>{cal?.ready ? t('f_cal_reconnect_m') : t('f_cal_ms')}</button></div>}
              {!cal?.ready && <p className="hb-muted">{t('f_cal_after')}</p>}
              {cal?.ready && nextOf(step) && <button className="cp-btn" onClick={() => go(nextOf(step))}>{t('save_continue')}</button>}
            </>}
            {step === 'offer' && <>
              {!profile?.setup?.offer_configured ? <div className="cp-offer"><b>{t('offer_pending')}</b><p>{t('offer_pending_text')}</p></div> : <>
                <div className="cp-offer">
                  <b>{status.offer_label || t('free3')}</b>
                  {profile?.offer_days ? <p>{t('offer_days', { d: profile.offer_days })}</p> : null}
                  {profile?.offer_terms ? <p>{profile.offer_terms}</p> : <p className="hb-muted">{t('offer_no_terms')}</p>}
                  <small className="hb-muted">{t('offer_readonly')}</small>
                </div>
                {profile?.setup?.offer_accepted_at
                  ? <div className="cp-done"><Icon name="check" size={18} />{t('offer_agreed', { d: new Date(profile.setup.offer_accepted_at).toLocaleDateString() })}</div>
                  : <>{profile?.setup?.offer_previously_accepted_at && <div className="aq-note aq-note--warn">{t('offer_changed')}</div>}
                    <button className="cp-btn cp-btn--block" disabled={busy} onClick={accept}>{t('offer_agree')}</button></>}
              </>}
            </>}
            {fail && <div className="aq-err">{fail}</div>}
          </div>
        </div>
        {!completed && <div className="cp-finish">
          <span>{allDone ? t('setup_all_done') : t('setup_left', { n: STEPS.length - doneCount })}</span>
          <button className="cp-btn" disabled={busy || !allDone} onClick={finish}>{t('setup_finish')}</button></div>}
      </div>
    </div>
  )
}

function Field({ l, children }) { return <label className="cp-field"><span>{l}</span>{children}</label> }
function Save({ t, busy, disabled, onClick, label, ghost }) {
  return <button className={`cp-btn ${ghost ? 'cp-btn--ghost' : ''}`} disabled={busy || disabled} onClick={onClick}>{busy ? t('saving') : (label || t('save_continue'))}</button>
}
