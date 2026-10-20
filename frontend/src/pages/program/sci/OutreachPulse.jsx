/* Live, scoped SCI outreach pulse. All metrics come from the existing
 * /program/command/analytics endpoint; never synthesize counts. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { integerValue } from './inboxModel'
import { percentText } from '../command/rateModel'

const fmt = value => value === null ? '—' : value.toLocaleString()
function MiniTrend({ data }) {
  const series = data?.trend?.series?.delivered || []
  const labels = data?.trend?.labels || []
  const max = Math.max(1, ...series)
  if (!series.length) return <p className="sci-micro sci-muted">No delivery trend available for this period.</p>
  return <div className="sci-pulse-trend" role="img"
    aria-label={'Delivered touches over the last 7 days: ' + series.map((v, i) => labels[i] + ': ' + v).join(', ')}>
    {series.map((v, i) => <span key={i} className="sci-pulse-trend-day"
      title={labels[i] + ' — ' + v + ' delivered'}
      style={{ height: `${Math.max(v ? 8 : 2, (v / max) * 100)}%` }} />)}
  </div>
}
export default function OutreachPulse({ locationId, navigate }) {
  const [data, setData] = useState(null)
  const [today, setToday] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    const p = new URLSearchParams({ range: '7d' })
    const tp = new URLSearchParams({ range: 'today' })
    if (locationId) { p.set('location_id', locationId); tp.set('location_id', locationId) }
    const get = () => {
      api.get(`/program/command/analytics?${p}`)
        .then(d => { if (active) { setData(d); setError('') } })
        .catch(() => { if (active) { setData(null); setError('Activity data unavailable. Open Analytics to investigate.') } })
      api.get(`/program/command/analytics?${tp}`)
        .then(d => { if (active) setToday(d) })
        .catch(() => { if (active) setToday(null) })
    }
    setData(null); setToday(null); get()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') get() }, 60000)
    return () => { active = false; clearInterval(timer) }
  }, [locationId])
  const m = data?.metrics
  const t = today?.rates || {}
  const items = [
    ['attempts', 'Outreach attempts', 'Sent or tried'],
    ['delivered', 'Completed touches', 'Confirmed delivered'],
    ['replies', 'Family replies', 'Excludes STOP; tracked separately'],
    ['appointments', 'Appointments', 'Confirmed bookings'],
  ]
  return <section className="sci-pulse sci-panel" aria-label="Real outreach performance">
    <header className="sci-pulse-head">
      <div><span className="sci-pulse-eyebrow">LIVE SCI ACTIVITY</span>
        <h2>Outreach is happening</h2>
        <p>Last 7 days · Real records · {locationId ? 'Selected location' : 'All locations'}</p>
      </div>
      <button type="button" className="sci-btn sm" onClick={() => navigate('/program/command?tab=reports')}>
        Explore analytics ›
      </button>
    </header>
    {error ? <p className="sci-micro sci-muted">{error}</p> : !data ? <p className="sci-micro sci-muted">Loading live outreach totals…</p> : <>
      <div className="sci-pulse-rates">
        <div><span>Engagement today</span><b>{percentText(t.engagement_rate)}</b>
          <small>{t.engagement_denominator
            ? `${fmt(t.engaged_contacted)} of ${fmt(t.engagement_denominator)} families contacted today responded`
            : 'No contacted cohort to measure today'}</small></div>
        <div><span>Engagement · last 7 days</span><b>{percentText(data?.rates?.engagement_rate)}</b>
          <small>{data?.rates?.engagement_denominator
            ? `${fmt(data.rates.engaged_contacted)} of ${fmt(data.rates.engagement_denominator)} families`
            : 'Not enough data for a rate'}</small></div>
        <div><span>Response opt-out share · 7 days</span><b>{percentText(data?.rates?.opt_out_share)}</b>
          <small>{integerValue(m, 'responses_total')
            ? `${fmt(integerValue(m, 'opt_outs'))} of ${fmt(integerValue(m, 'responses_total'))} real responses`
            : 'No meaningful responses yet'}</small></div>
      </div>
      <div className="sci-pulse-main">
        <div className="sci-pulse-stats">{items.map(([key, label, desc]) =>
          <div key={key} className="sci-pulse-stat"><span>{label}</span><strong>{fmt(integerValue(m, key))}</strong>
            <small>{desc}</small></div>)}
        </div>
        <div className="sci-pulse-chart"><h3>Delivered touches over time</h3><MiniTrend data={data} />
          <p className="sci-micro">Confirmed deliveries only · source: SCI Analytics</p></div>
      </div>
      <div className="sci-pulse-bottom">
        <span><b>{fmt(integerValue(m, 'opt_outs'))}</b> opt-outs recorded and respected</span>
        <span><b>{fmt(integerValue(m, 'followup_families'))}</b> existing families reached again</span>
        <span>Delivery and engagement are measured separately—every response matters.</span>
      </div>
    </>}
  </section>
}
