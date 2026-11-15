// Consumer Leads - VOLT Marketing's subsidiary business (separate from
// recruiting and from HIP).
//   CoverageFunnel      public, one-question-at-a-time eligibility page (/volt/check-coverage);
//                       also rendered signed-in by the back office's Preview (test data only)
//   ConsumerLeadsCard   card on the VOLT Operations overview (counts only)
// The private back office is ConsumerLeadsWorkspace.jsx (/consumer-leads/:orgId).
// Public website design: V4 (docs/volt/design/consumer_leads_v4_design_demo.html).
// All access rules are enforced by the server (app/services/volt/leads.py).
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import './volt.css'

// Stored in UTC; shown in Central time with CST/CDT.
export const fmtCT = (iso) => (iso ? new Date(iso).toLocaleString('en-US', {
  timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  timeZoneName: 'short',
}) : '—')
const NAVY = '#163B60'

export const publicSiteUrl = () => {
  const h = window.location.hostname
  return h.endsWith('voltmarketing.live') || h === 'localhost' || h === '127.0.0.1'
    ? '/volt/check-coverage' : 'https://voltmarketing.live/volt/check-coverage'
}

// The public website (V4) lives in ConsumerSite.jsx; this name is kept for the
// back-office preview and older imports.
export { default as CoverageFunnel } from './ConsumerSite'

// ── campaign card (VOLT Operations overview) ─────────────────────────────────
export function ConsumerLeadsCard({ orgId }) {
  const [s, setS] = useState(null)
  const [hidden, setHidden] = useState(false)
  useEffect(() => { api.get(`/volt/ops/${orgId}/leads/summary`).then(setS).catch(() => setHidden(true)) }, [orgId])
  if (hidden) return null
  const nums = s ? [['total', 'Total leads'], ['new', 'New'], ['contacted', 'Contacted'], ['qualified', 'Qualified'],
    ['appointments', 'Appointments'], ['enrollments', 'Enrollments']] : []
  return (
    <section className="v-panel" data-campaign-card="consumer-leads" style={{ borderLeft: `6px solid ${NAVY}` }}>
      <div className="v-row"><div><h3 style={{ margin: 0 }}>Consumer Leads</h3>
        <p className="v-small v-muted" style={{ margin: 0 }}>Health Insurance Lead Acquisition &amp; Enrollment · a VOLT Marketing business</p></div>
        {s && <span className="v-tag">{s.intake_enabled ? 'Public intake ON' : 'Public intake OFF'}</span>}</div>
      {!s ? <p className="v-mono">Loading…</p> : <>
        <div className="v-kpis" style={{ marginTop: 10 }}>
          {nums.map(([k, l]) => <div key={k} className="v-kpi" data-card-kpi={k}><b>{s[k]}</b><span>{l}</span></div>)}
        </div>
        <p className="v-small v-muted">Last activity: {fmtCT(s.last_activity_at)}{s.access === 'assigned' ? ' · counts include only leads assigned to you' : ''}</p>
        <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
          <Link className="v-btn v-btn--red v-btn--sm" to={`/consumer-leads/${orgId}`} data-card-open>Open Consumer Leads</Link>
          {s.can_preview && <Link className="v-btn v-btn--line v-btn--sm" to={`/consumer-leads/${orgId}/preview`} data-card-preview>Preview Customer Website</Link>}
          <a className="v-btn v-btn--line v-btn--sm" href={publicSiteUrl()} target="_blank" rel="noreferrer" data-card-site>View Public Website ↗</a>
          {s.can_develop && <Link className="v-btn v-btn--line v-btn--sm" to={`/consumer-leads/${orgId}/dev`} data-card-dev>Development</Link>}
          {s.can_settings && <Link className="v-btn v-btn--line v-btn--sm" to={`/consumer-leads/${orgId}/settings`} data-card-settings>Settings</Link>}
        </div></>}
    </section>
  )
}
