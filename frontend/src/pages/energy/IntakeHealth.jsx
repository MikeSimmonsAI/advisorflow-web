/**
 * NEW REQUESTS — manager-only panel: unassigned New Rate Requests and how fast
 * website requests / field referrals get their first recorded contact.
 *
 *   GET /energy-ops/intake-health?days=30   (403 for non-managers -> hidden)
 *
 * Times are when a person marked the first-contact task done, not a verified
 * call time; the panel shows the server's own wording for that.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

function hrs(h) {
  if (h == null) return '—'
  return h < 48 ? `${h}h` : `${Math.round(h / 24)}d`
}

export default function IntakeHealth() {
  const [data, setData] = useState(null)
  const [hidden, setHidden] = useState(false)
  const [days, setDays] = useState(30)
  useEffect(() => {
    let live = true
    api.get(`/energy-ops/intake-health?days=${days}`)
      .then(d => { if (live) setData(d) })
      .catch(() => { if (live) setHidden(true) })
    return () => { live = false }
  }, [days])
  if (hidden || !data) return null
  const un = data.unassigned
  return (
    <section className="eo-panel eo-intake" data-testid="intake-health">
      <div className="eo-head" style={{ marginBottom: 8 }}>
        <h2 className="eo-sec" style={{ margin: 0 }}>New requests</h2>
        <label className="eo-sub">Last&nbsp;
          <select value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Period">
            {[7, 30, 90].map(d => <option key={d} value={d}>{d} days</option>)}
          </select>
        </label>
      </div>
      <div className="eo-kpis">
        <div className={`eo-kpi${un.count ? ' eo-kpi--warn' : ''}`} data-kpi="unassigned">
          <span className="eo-kpi-n">{un.count}</span><span className="eo-kpi-l">Unassigned in New</span>
        </div>
        {data.first_contact.map(c => (
          <div key={c.source} className="eo-kpi" data-kpi={c.source}>
            <span className="eo-kpi-n">{hrs(c.median_hours)}</span>
            <span className="eo-kpi-l">{c.label}: typical time to first contact</span>
            <span className="eo-sub">{c.contacted}/{c.requests} contacted · {c.open} open{c.overdue ? ` · ${c.overdue} overdue` : ''}</span>
          </div>
        ))}
      </div>
      {un.oldest.length > 0 && (
        <table className="eo-table">
          <thead><tr><th>Waiting for an owner</th><th>Source</th><th>Waiting</th></tr></thead>
          <tbody>
            {un.oldest.slice(0, 5).map(l => (
              <tr key={l.lead_id}><td><Link to={l.link}>{l.name}</Link></td><td>{l.source || '—'}</td><td>{hrs(l.age_hours)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {data.open_by_owner.some(o => o.overdue) && (
        <p className="eo-note">Overdue first contacts: {data.open_by_owner.filter(o => o.overdue).map(o => `${o.owner} ${o.overdue}`).join(' · ')}</p>
      )}
      <p className="eo-note">{data.basis}</p>
    </section>
  )
}
