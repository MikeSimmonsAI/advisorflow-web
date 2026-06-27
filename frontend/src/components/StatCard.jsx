import './StatCard.css'

/**
 * sparkline is OPTIONAL - an array of numbers (e.g. [3, 5, 4, 8, 7, 9]).
 * When provided, renders a tiny inline trend line at the bottom of the
 * card. Deliberately optional and additive: StatCard is used on 7
 * different pages today (Admin, Cadence, EmailQueue, Overview, Replies,
 * Reports, UserDetail) - every existing call site that doesn't pass
 * sparkline renders EXACTLY as it did before this was added, no visual
 * change at all. Only Overview's redesign actually supplies real
 * sparkline data, computed from real history, never fabricated.
 */
function Sparkline({ data, accent }) {
  if (!data || data.length < 2) return null

  const width = 100
  const height = 28
  const max = Math.max(...data, 1)
  const min = Math.min(...data, 0)
  const range = max - min || 1
  const stepX = width / (data.length - 1)

  const points = data.map((value, i) => {
    const x = i * stepX
    const y = height - ((value - min) / range) * height
    return `${x},${y}`
  }).join(' ')

  return (
    <svg className="stat-card-sparkline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline
        points={points}
        fill="none"
        stroke={`var(--signal-${accent === 'neutral' ? 'blue' : accent})`}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

export default function StatCard({ label, value, sublabel, accent = 'blue', trend, sparkline }) {
  return (
    <div className={`stat-card stat-card--${accent}`}>
      <div className="stat-card-label">{label}</div>
      <div className={`stat-card-value stat-card-value--${accent}`}>{value}</div>
      {sublabel && <div className="stat-card-sublabel">{sublabel}</div>}
      {trend && <div className={`stat-card-trend stat-card-trend--${trend.direction}`}>{trend.text}</div>}
      <Sparkline data={sparkline} accent={accent} />
    </div>
  )
}
