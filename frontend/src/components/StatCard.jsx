import './StatCard.css'

export default function StatCard({ label, value, sublabel, accent = 'blue', trend }) {
  return (
    <div className={`stat-card stat-card--${accent}`}>
      <div className="stat-card-label">{label}</div>
      <div className={`stat-card-value stat-card-value--${accent}`}>{value}</div>
      {sublabel && <div className="stat-card-sublabel">{sublabel}</div>}
      {trend && <div className={`stat-card-trend stat-card-trend--${trend.direction}`}>{trend.text}</div>}
    </div>
  )
}
