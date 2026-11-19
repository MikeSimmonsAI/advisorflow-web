/**
 * CLIENT PORTAL dashboard, owner-approved design 2026-11-18 (frames B and C).
 * Day one (nothing delivered yet): a friendly "first appointment starts here"
 * and the pilot card - no rows of zeros. Once appointments arrive: pilot
 * progress, the numbers, the next 3 appointments, "report a result" right on
 * the page, results by customer type and the toolbox.
 *
 * ?demo=1 shows the same screen filled with SAMPLE appointments (fictional
 * businesses, every number tagged "Sample data") for a sales demo. Nothing in
 * demo mode is read from or written to the client's records.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, getAcqClientView } from '../../api/client'
import { BASE, useAcq } from './AcquisitionShell'
import { Icon, Loading, errText, money, useLoad, useToast } from './ui'
import { useT } from './portalText'
import { SetupCard } from './ClientSetup'

const sampleDay = (d, h, m = 0) => { const x = new Date(); x.setDate(x.getDate() + d); x.setHours(h, m, 0, 0); return x.toISOString() }
function demoData() {
  const up = [
    { id: 'demo-1', business_name: 'Poplar Avenue Medical Plaza', segment: 'medical', segment_label: 'Medical office', city: 'Memphis, TN', stage: 'appointment_booked',
      contact_name: 'Dana Whitfield', contact_title: 'Practice manager', notes: 'Nightly office cleaning · about 9,000 sq ft', appointment: { time: sampleDay(1, 10) } },
    { id: 'demo-2', business_name: 'Riverside Professional Center', segment: 'office', segment_label: 'Office building', city: 'Germantown, TN', stage: 'appointment_booked',
      contact_name: 'Marcus Hale', contact_title: 'Building manager', notes: 'Recurring office cleaning · 3 floors', appointment: { time: sampleDay(1, 14) } },
    { id: 'demo-3', business_name: 'Cordova Property Group', segment: 'property', segment_label: 'Property management', city: 'Cordova, TN', stage: 'appointment_booked',
      contact_name: 'Lena Ortiz', contact_title: 'Property manager', notes: 'Move-out and common-area cleaning', appointment: { time: sampleDay(2, 11) } },
  ]
  const all = [...up,
    { id: 'demo-4', business_name: 'Bartlett Family Dental', segment: 'medical', segment_label: 'Medical office', stage: 'won', monthly_value: 2500 },
  ]
  return {
    demo: true, pilot_day: 12, delivered: 4,
    counts: { upcoming: 3, held: 1, booked: 4, quoted: 1, won: 1, no_show: 0, delivered: 4 },
    upcoming: up, needs_outcome: [], won_monthly_value: 2500, open_estimates: 1, all,
    calendar: { ready: true, provider: 'Google' },
  }
}

const dedupe = list => list.filter((x, i) => list.findIndex(y => y.id === x.id) === i)

function Tag({ on, t }) { return on ? <span className="cp-sample">{t('sample')}</span> : null }

function greeting(t) {
  const h = new Date().getHours()
  return h < 12 ? t('morning') : h < 18 ? t('afternoon') : t('evening')
}

export function Home() {
  const { name, status } = useAcq()
  const { t, lang } = useT()
  // Sample view only for EvoSys staff looking through a client's portal (sales
  // demos). A client's own login never sees sample data.
  const demo = new URLSearchParams(useLocation().search).get('demo') === '1' && !!getAcqClientView()
  const [d, err, reload] = useLoad(demo ? null : '/acquisition/dashboard')
  const [list, , reloadList] = useLoad(demo ? null : '/acquisition/appointments')
  const [cal] = useLoad(demo ? null : '/acquisition/calendar')
  const data = useMemo(() => demo ? demoData() : (d && list ? {
    pilot_day: status?.pilot_day || 0, delivered: d.counts.delivered ?? d.counts.booked, counts: d.counts,
    upcoming: d.upcoming || [], needs_outcome: d.needs_outcome || [], won_monthly_value: d.won_monthly_value,
    open_estimates: (list.all || []).filter(x => x.stage === 'quote_issued').length, all: list.all || [],
    calendar: cal ? { ready: !!cal.ready, provider: cal.provider || '' } : null,
  } : null), [demo, d, list, cal, status])
  if (err) return <div className="aq-page cp"><div className="aq-err" role="alert">{t('load_failed')} <button className="cp-btn cp-btn--sm" onClick={() => { reload(); reloadList && reloadList() }}>{t('retry')}</button></div></div>
  if (!data) return <div className="aq-page cp"><Loading /></div>
  const first = (status?.me?.name || '').split(' ')[0]
  const delivered = data.delivered || 0
  const started = demo || !!status?.pilot_started_at
  const day = Math.max(0, data.pilot_day || 0)
  const refresh = () => { reload && reload(); reloadList && reloadList() }
  // Frame A: until the client finishes setup, the dashboard is the setup.
  const setupOpen = !demo && !!status?.can_manage && !status?.profile?.setup?.completed_at && delivered === 0
  return (
    <div className="aq-page cp" lang={lang}>
      <div className="cp-head">
        <div><h1 className="cp-h1">{t('dashboard')}</h1><p className="cp-hello">{first ? `${greeting(t)}, ${first}` : name}</p></div>
        <div className="cp-head__right">
          {data.calendar && (data.calendar.ready
            ? <span className="cp-chip"><Icon name="calendar" size={16} />{t('cal_connected')} <b className="cp-ok">✓ {data.calendar.provider || ''}</b></span>
            : <Link className="cp-chip" to={`${BASE}/settings`}><Icon name="calendar" size={16} />{t('cal_connect')}</Link>)}
        </div>
      </div>
      {demo && <div className="cp-demo">{t('demo_note')}</div>}
      <div className="cp-banner"><Icon name="sparkle" size={20} />{t('banner')}</div>

      {setupOpen ? <SetupCard profile={status.profile} cal={cal} /> : <>
      {!demo && status?.profile?.setup?.completed_at && delivered === 0 && <div className="cp-done"><Icon name="check" size={20} />{t('setup_done')}</div>}
      <Pilot t={t} started={started} day={day} days={demo ? 30 : status?.offer_days} delivered={delivered} demo={demo} offer={status?.offer_label} />

      {delivered === 0 ? <FirstAppointment t={t} running={demo || !!status?.running} setupDone={!!status?.profile?.setup?.completed_at} open={data.open_estimates} /> : <>
        <div className="cp-stats">
          <Num icon="calendar" label={t('upcoming')} v={data.counts.upcoming} demo={demo} t={t} to={`${BASE}/appointments`} />
          <Num icon="check" label={t('completed')} v={data.counts.held} demo={demo} t={t} to={`${BASE}/appointments`} />
          <Num icon="doc" label={t('estimates')} v={data.counts.quoted} demo={demo} t={t} to={`${BASE}/estimates`} />
          <Num icon="star" label={t('won')} v={data.counts.won} demo={demo} t={t} to={`${BASE}/results`} />
          <Num icon="dollar" label={t('mrv')} v={money(data.won_monthly_value)} demo={demo} t={t} to={`${BASE}/results`} />
        </div>
        <div className="cp-grid">
          <div className="cp-col">
            <NextAppointments items={data.upcoming.slice(0, 3)} t={t} demo={demo} open={data.open_estimates} />
            <ResultsBox all={data.all} counts={data.counts} t={t} demo={demo} />
          </div>
          <ReportResult items={dedupe([...data.needs_outcome, ...data.upcoming])} t={t} demo={demo} canManage={demo || status?.can_manage} onSaved={refresh} />
        </div>
      </>}
      </>}
      <Toolbox t={t} />
    </div>
  )
}

function Pilot({ t, started, day, days, delivered, demo, offer }) {
  const total = days || null
  const pct = started && total ? Math.min(100, Math.round((Math.min(day, total) / total) * 100)) : 0
  const ended = started && total && day > total
  return (
    <section className="cp-card cp-pilot">
      <span className="cp-pilot__icon"><Icon name="rocket" size={22} /></span>
      <div className="cp-pilot__txt">
        <b>{total ? t('pilot_title', { d: total }) : (offer || t('free3'))}</b>
        {started
          ? <span>{ended ? t('pilot_ended', { d: total }) : total ? t('pilot_line', { day: Math.min(day, total), d: total, n: delivered }) : t('pilot_line_nodays', { n: delivered })} <Tag on={demo} t={t} /></span>
          : <span><em className="cp-ready">{t('ready')}</em> · {t('pilot_wait')}</span>}
        {offer && !demo && total && <small className="hb-muted">{offer}</small>}
      </div>
      {total && <div className="cp-pilot__bar"><small>{started ? t('elapsed', { p: pct }) : t('pending')}</small>
        <div className="cp-bar"><i style={{ width: `${pct}%` }} /></div></div>}
    </section>
  )
}

function FirstAppointment({ t, running, setupDone, open }) {
  return (
    <section className="cp-card cp-first">
      <span className="cp-first__art"><Icon name="calendar" size={46} /></span>
      <h2>{t('first_title')}</h2>
      <p>{running ? t('first_text') : setupDone ? t('first_review') : t('first_setup')}</p>
      <Link className="cp-btn cp-btn--ghost" to={`${BASE}/settings`}>{t('view_business')}</Link>
      <div><Link className="cp-estline cp-estline--center" to={`${BASE}/estimates`}><Icon name="doc" size={16} />{t('open_est', { n: open || 0 })} →</Link></div>
    </section>
  )
}

function Num({ icon, label, v, demo, t, to }) {
  return (
    <Link className="cp-num" to={to}>
      <span className="cp-num__icon"><Icon name={icon} size={20} /></span>
      <span><small>{label}</small><b>{v}</b><Tag on={demo} t={t} /></span>
    </Link>
  )
}

const fmtDay = (iso, lang) => iso ? new Date(iso).toLocaleDateString(lang === 'es' ? 'es-US' : 'en-US', { month: 'short', day: 'numeric' }) : ''
export const fmtWhen = (iso, lang) => iso ? new Date(iso).toLocaleString(lang === 'es' ? 'es-US' : 'en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }) : ''
const fmtTime = (iso, lang) => iso ? new Date(iso).toLocaleTimeString(lang === 'es' ? 'es-US' : 'en-US', { hour: 'numeric', minute: '2-digit' }) : ''
const SEG_ICON = { medical: 'plus', office: 'building', property: 'building' }

function NextAppointments({ items, t, demo, open }) {
  const { lang } = useT()
  return (
    <section className="cp-card">
      <div className="cp-card__head"><h2>{t('next3')}</h2><Link to={`${BASE}/appointments`}>{t('view_all')} →</Link></div>
      {items.length === 0 ? <p className="hb-muted">{t('no_upcoming')}</p> : items.map(x => (
        <div className="cp-appt" key={x.id}>
          <span className="cp-appt__icon"><Icon name={SEG_ICON[x.segment] || 'building'} size={20} /></span>
          <div className="cp-appt__who"><b>{x.business_name}</b><small>{x.segment_label || ''}</small></div>
          <div className="cp-appt__where"><Icon name="pin" size={14} />{x.city || ''}</div>
          <div className="cp-appt__when"><Icon name="calendar" size={14} /><span><b>{fmtDay(x.appointment?.time, lang)}</b> {fmtTime(x.appointment?.time, lang)}</span></div>
          <a className="cp-btn cp-btn--sm" href={`#report-${x.id}`}
             onClick={e => { e.preventDefault(); window.dispatchEvent(new CustomEvent('cp-pick', { detail: x.id })); document.getElementById('cp-report')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>{t('update_result')}</a>
        </div>))}
      <Link className="cp-estline" to={`${BASE}/estimates`}><Icon name="doc" size={16} />{t('open_est', { n: open })} → <Tag on={demo} t={t} /></Link>
    </section>
  )
}

function ResultsBox({ all, counts, t, demo }) {
  const bars = [[t('appointments'), counts.booked], [t('visits'), counts.held], [t('estimates_short'), counts.quoted], [t('won'), counts.won]]
  const max = Math.max(1, ...bars.map(b => b[1] || 0))
  const by = {}
  all.forEach(x => { const k = x.segment_label || t('other'); by[k] = by[k] || { a: 0, w: 0 }; by[k].a += 1; if (x.stage === 'won') by[k].w += 1 })
  const rows = Object.entries(by)
  const outcomes = (counts.held || 0) + (counts.quoted || 0) + (counts.won || 0)
  return (
    <section className="cp-card">
      <div className="cp-card__head"><h2>{t('results_month')}</h2><Tag on={demo} t={t} /></div>
      {outcomes === 0 && !demo && <p className="hb-muted">{t('no_outcomes')}</p>}
      {(outcomes > 0 || demo) && <div className="cp-bars">{bars.map(([l, v], i) => (
        <div className="cp-bars__col" key={l}><b>{v || 0}</b><i className={`cp-bars__b cp-bars__b--${i}`} style={{ height: `${Math.max(6, ((v || 0) / max) * 120)}px` }} /><span>{l}</span></div>))}</div>}
      {rows.length > 0 && <table className="cp-table"><thead><tr><th>{t('cust_type')}</th><th>{t('appointments')}</th><th>{t('won')}</th></tr></thead>
        <tbody>{rows.map(([k, v]) => <tr key={k}><td>{k}</td><td>{v.a}</td><td>{v.w}</td></tr>)}</tbody></table>}
    </section>
  )
}

// "Didn't show" is a true no-show. "They cancelled" and "Not a fit" are NOT
// no-shows: they are recorded as not moving forward, with the reason, so the
// no-show count stays honest. "Other" is a no-show and needs a few words.
const NOSHOW = [['ns_didnt', 'no_show'], ['ns_cancelled', 'lost'], ['ns_fit', 'lost'], ['ns_other', 'no_show']]
const CAN_REPORT = ['appointment_booked', 'appointment_held', 'quote_issued', 'no_show']

export function ReportResult({ items, t, demo, canManage, onSaved, single }) {
  const { lang } = useT()
  const [pickId, setPickId] = useState(items[0]?.id || null)
  const [kind, setKind] = useState('appointment_held')
  const [f, setF] = useState({ contract_type: 'recurring', currency: 'USD' })
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  useEffect(() => {
    const h = e => setPickId(e.detail)
    window.addEventListener('cp-pick', h)
    return () => window.removeEventListener('cp-pick', h)
  }, [])
  const x = items.find(i => i.id === pickId) || items[0]
  if (!x) return <section className="cp-card" id="cp-report"><h2>{t('report')}</h2><p className="hb-muted">{t('report_none')}</p></section>
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const stage = x.stage
  // Which results make sense from where this appointment is now.
  const allowed = {
    appointment_held: ['appointment_booked', 'no_show'].includes(stage),
    no_show: stage === 'appointment_booked',
    quote_issued: ['appointment_booked', 'appointment_held'].includes(stage),
    won: ['appointment_booked', 'appointment_held', 'quote_issued'].includes(stage),
  }
  const k = allowed[kind] ? kind : (Object.keys(allowed).find(z => allowed[z]) || null)
  const ns = NOSHOW.find(([key]) => key === f.reason)
  const missing = k === 'no_show' ? (!f.reason || (f.reason === 'ns_other' && !(f.why || '').trim()))
    : k === 'quote_issued' ? !(Number(f.amount) > 0)
    : k === 'won' ? (!f.start || !(Number(f.money) > 0)) : false
  const save = async () => {
    setFail(null)
    if (demo) { say(t('saved_demo')); return }
    setBusy(true)
    try {
      const post = body => api.post(`/acquisition/prospects/${x.id}/outcome`, body)
      if (k === 'appointment_held') await post({ outcome: 'appointment_held', note: f.note })
      if (k === 'no_show') {
        const reason = t(ns[0], {}, 'en') + (f.reason === 'ns_other' && f.why ? ': ' + f.why.trim() : '')
        await post({ outcome: ns[1], reason, note: [reason, f.note].filter(Boolean).join('\n') })
      }
      if (k === 'quote_issued') await post({ outcome: 'quote_issued', quote_amount: f.amount, note: [f.currency !== 'USD' ? 'Currency: ' + f.currency : '', f.note].filter(Boolean).join('\n') })
      if (k === 'won') {
        if (stage === 'appointment_booked') await post({ outcome: 'appointment_held' })
        const one = f.contract_type === 'one_time'
        await post({ outcome: 'won', contract_type: f.contract_type, contract_start: f.start,
                     monthly_value: one ? undefined : f.money, contract_value: one ? f.money : undefined, note: f.note })
      }
      say(t('saved')); setF({ contract_type: 'recurring', currency: 'USD' }); onSaved && onSaved()
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const opt = (key, label, extra) => allowed[key] ? (
    <div className={`cp-opt ${k === key ? 'on' : ''}`}>
      <label><input type="radio" name={'cp-kind-' + x.id} checked={k === key} onChange={() => setKind(key)} />{label}</label>
      {k === key && extra}
    </div>) : null
  const closed = !Object.values(allowed).some(Boolean) || !CAN_REPORT.includes(stage)
  return (
    <section className="cp-card cp-report" id="cp-report">{toast}
      <h2>{t('report')}</h2>
      {!single && items.length > 1 && <select className="cp-select" value={x.id} onChange={e => setPickId(e.target.value)} aria-label={t('report')}>
        {items.map(i => <option key={i.id} value={i.id}>{i.business_name} · {fmtDay(i.appointment?.time, lang)}</option>)}</select>}
      <dl className="cp-dl">
        <dt><Icon name="users" size={15} />{t('who')}</dt><dd>{[x.business_name, [x.contact_name, x.contact_title].filter(Boolean).join(', ')].filter(Boolean).join(' · ') || '—'}</dd>
        <dt><Icon name="pin" size={15} />{t('where')}</dt><dd>{x.address || x.city || '—'}</dd>
        <dt><Icon name="calendar" size={15} />{t('when')}</dt><dd>{x.appointment?.time ? fmtWhen(x.appointment.time, lang) : '—'}</dd>
        {(x.notes || x.appointment?.label) && <><dt><Icon name="doc" size={15} />{t('notes')}</dt><dd>{x.notes || x.appointment?.label}</dd></>}
      </dl>
      {!canManage ? <p className="hb-muted">{t('admin_only')}</p> : closed ? <p className="hb-muted">{t('report_closed')}</p> : <>
        {opt('appointment_held', t('visit_held'))}
        {opt('no_show', t('no_show'), <div className="cp-won">
          <select className="cp-select" value={f.reason || ''} onChange={set('reason')} aria-label={t('reason')}>
            <option value="">{t('reason')}</option>{NOSHOW.map(([key]) => <option key={key} value={key}>{t(key)}</option>)}</select>
          {f.reason === 'ns_other' && <input className="cp-input" placeholder={t('why_other')} value={f.why || ''} onChange={set('why')} />}
        </div>)}
        {opt('quote_issued', t('estimate_sent'), <div className="cp-money">
          <select className="cp-select cp-cur" value={f.currency} onChange={set('currency')} aria-label="Currency"><option>USD</option><option>CAD</option><option>MXN</option></select>
          <input className="cp-input" inputMode="decimal" placeholder={t('amount')} value={f.amount || ''} onChange={set('amount')} /></div>)}
        {opt('won', t('won_opt'), <div className="cp-won">
          <div className="cp-seg">{[['one_time', t('one_time')], ['recurring', t('recurring')]].map(([key, l]) =>
            <button type="button" key={key} className={f.contract_type === key ? 'on' : ''} onClick={() => setF({ ...f, contract_type: key })}>{l}</button>)}</div>
          <label>{t('start_date')}<input className="cp-input" type="date" value={f.start || ''} onChange={set('start')} /></label>
          <label>{f.contract_type === 'one_time' ? t('total_value') : t('monthly_value')}<input className="cp-input" inputMode="decimal" placeholder="$" value={f.money || ''} onChange={set('money')} /></label>
          <small className="hb-muted">{f.contract_type === 'one_time' ? t('one_time_note') : t('recurring_note')}</small>
        </div>)}
        <button className="cp-btn cp-btn--block" disabled={busy || !k || missing} onClick={save}>{busy ? t('saving') : t('save_result')}</button>
      </>}
      <Link className="cp-btn cp-btn--ghost cp-btn--block" to={demo ? `${BASE}?demo=1` : `${BASE}/appointments/${x.id}#followup`}><Icon name="calendar" size={16} />{t('ask_evosys')}</Link>
      {fail && <div className="aq-err">{fail}</div>}
    </section>
  )
}

function Toolbox({ t }) {
  const { ops } = useAcq()
  const on = !!ops?.ops_enabled
  // Only for companies with Service Operations switched on - never offered
  // to a core appointment-setting client.
  if (!on) return null
  const tools = [['list', 'tb_jobs', 'jobs'], ['calendar', 'tb_schedule', 'schedule'], ['users', 'tb_team', 'team'], ['building', 'tb_customers', 'customers'], ['dollar', 'tb_invoices', 'invoices']]
  return (
    <section className="cp-card cp-tools">
      <div className="cp-card__head"><h2><Icon name="tools" size={18} /> {t('toolbox')}</h2><span className="cp-free">{t('toolbox_free')}</span></div>
      <p className="hb-muted" style={{ marginTop: 0 }}>{t('toolbox_text')}</p>
      <div className="cp-tools__grid">{tools.map(([i, k, path]) => on
        ? <Link key={k} className="cp-tool" to={`${BASE}/ops/${path}`}><Icon name={i} size={20} /><b>{t(k)}</b></Link>
        : <Link key={k} className="cp-tool cp-tool--off" to={`${BASE}/feedback`}><Icon name={i} size={20} /><b>{t(k)}</b><small>{t('tb_ask')}</small></Link>)}</div>
    </section>
  )
}
