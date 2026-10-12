import { useEffect, useState } from 'react'
import { api } from '../api/client'

/* The person's own survey answers, on their record. Alerts for low ratings
   live on the dashboard, not here. */
const STAR = '★'

function fmt(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function SurveyCard({ leadId }) {
  const [rows, setRows] = useState(null)

  useEffect(() => {
    let off = false
    api.get(`/survey/results/${leadId}`)
      .then(d => { if (!off) setRows(d?.responses || []) })
      .catch(() => { if (!off) setRows([]) })
    return () => { off = true }
  }, [leadId])

  return (
    <section className="lcc-card lcc-rail-card" aria-labelledby="lcc-survey-h">
      <div className="lcc-rail-head">
        <h2 className="lcc-h2" id="lcc-survey-h">Survey</h2>
        {rows && rows.length > 0 && rows[0].rating
          ? <span className={`lcc-chip ${rows[0].rating >= 4 ? 'lcc-chip--green' : 'lcc-chip--amber'}`}>{rows[0].rating} of 5</span>
          : null}
      </div>
      {rows === null ? <p className="lcc-small lcc-muted">Loading…</p>
        : rows.length === 0 ? <p className="lcc-small lcc-muted">No survey answered yet.</p>
        : rows.map(r => (
          <div key={r.id} style={{ borderTop: '1px solid rgba(0,0,0,.06)', paddingTop: 8, marginTop: 8 }}>
            <div style={{ fontSize: 18, letterSpacing: 2, color: '#c79a3a' }} aria-label={`${r.rating || 0} of 5 stars`}>
              {STAR.repeat(r.rating || 0)}<span style={{ color: '#d9d4c7' }}>{STAR.repeat(5 - (r.rating || 0))}</span>
            </div>
            <p className="lcc-small lcc-muted" style={{ margin: '2px 0 4px' }}>
              {fmt(r.submitted_at)}{r.location ? ` · ${r.location}` : ''}{r.share_ok ? ' · OK to share' : ''}
            </p>
            {r.feedback ? <p className="lcc-small" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{r.feedback}</p> : null}
          </div>
        ))}
    </section>
  )
}
