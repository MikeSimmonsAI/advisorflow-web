/* Engagement gauge: of the people we texted or emailed, the share who did
   something back (replied, opened their booking page or booked). Shared by the
   SCI Dashboard and the SCI Live phone screen. Data: GET /program/engagement.
   Opt-outs are shown on their own and never raise the percentage. */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import './EngagementGauge.css'

const fmt = v => (v == null ? '—' : `${Number.isInteger(v) ? v : v.toFixed(1)}%`)

export function useEngagement(refreshMs = 60000) {
  const [d, setD] = useState(null)
  useEffect(() => {
    let alive = true
    const load = () => api.get('/program/engagement').then(x => { if (alive) setD(x) }).catch(() => {})
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, refreshMs)
    return () => { alive = false; clearInterval(t) }
  }, [refreshMs])
  return d
}

function Ring({ pct, size = 132 }) {
  const stroke = Math.round(size * 0.11)
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const frac = Math.max(0, Math.min(1, (pct || 0) / 100))
  return (
    <svg className="eg-ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
         aria-label={`Engagement ${fmt(pct)}`}>
      <circle cx={size / 2} cy={size / 2} r={r} className="eg-track" strokeWidth={stroke} fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} className="eg-fill" strokeWidth={stroke} fill="none"
              strokeLinecap="round" strokeDasharray={`${c * frac} ${c}`}
              transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="47%" textAnchor="middle" className="eg-pct">{fmt(pct)}</text>
      <text x="50%" y="64%" textAnchor="middle" className="eg-cap">engaged</text>
    </svg>
  )
}

function Bar({ label, n, of, tone }) {
  const w = of ? Math.max(n ? 3 : 0, Math.round((100 * n) / of)) : 0
  return (
    <div className={`eg-bar ${tone || ''}`}>
      <div className="eg-bar-top"><span>{label}</span><b>{n}</b></div>
      <div className="eg-bar-track"><span style={{ width: `${w}%` }} /></div>
    </div>
  )
}

export default function EngagementGauge({ data, compact = false }) {
  const a = data?.all
  if (!a) return <div className="eg"><p className="eg-note">Loading engagement…</p></div>
  if (!a.contacted) return <div className="eg"><p className="eg-note">Engagement shows here once people have been contacted.</p></div>
  const t = data.today
  return (
    <div className={`eg ${compact ? 'compact' : ''}`}>
      <div className="eg-main">
        <Ring pct={a.engaged_pct} size={compact ? 116 : 132} />
        <div className="eg-bars">
          <Bar label="Replied" n={a.replied} of={a.contacted} />
          <Bar label="Opened booking page" n={a.opened_booking} of={a.contacted} />
          <Bar label="Booked" n={a.booked} of={a.contacted} />
          <Bar label="Opted out" n={a.opted_out} of={a.contacted} tone="muted" />
        </div>
      </div>
      <p className="eg-note">
        <b>{a.engaged}</b> of <b>{a.contacted}</b> people contacted replied, opened their booking page or booked.
        {' '}Any response, including opt-outs: <b>{fmt(a.responded_pct)}</b>.
        {t && t.contacted > 0 && <> Today: <b>{fmt(t.engaged_pct)}</b> of {t.contacted} contacted.</>}
      </p>
    </div>
  )
}
