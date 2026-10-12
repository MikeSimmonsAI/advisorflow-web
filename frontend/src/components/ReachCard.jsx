import { useEffect, useState } from 'react'
import { api } from '../api/client'

/* Program contacts only: how this person is reached and when we last texted /
   emailed them. Renders nothing for people who aren't on a program. */
function fmt(iso) {
  if (!iso) return 'Not yet'
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function ReachCard({ leadId }) {
  const [r, setR] = useState(null)

  useEffect(() => {
    let off = false
    api.get(`/program/reach/${leadId}`)
      .then(d => { if (!off) setR(d || null) })
      .catch(() => { if (!off) setR(null) })
    return () => { off = true }
  }, [leadId])

  if (!r) return null
  const green = r.reaches === 'Text + Email'
  return (
    <section className="lcc-card lcc-rail-card" aria-labelledby="lcc-reach-h">
      <div className="lcc-rail-head">
        <h2 className="lcc-h2" id="lcc-reach-h">Reaches by</h2>
        <span className={`lcc-chip ${green ? 'lcc-chip--green' : 'lcc-chip--amber'}`}>{r.reaches}</span>
      </div>
      <p className="lcc-small" style={{ margin: '4px 0 0' }}>Last text: {fmt(r.last_text_at)}</p>
      <p className="lcc-small" style={{ margin: '2px 0 0' }}>Last email: {fmt(r.last_email_at)}</p>
      {r.line_type === 'landline' ? <p className="lcc-small lcc-muted" style={{ margin: '4px 0 0' }}>Landline — no texts, call instead.</p> : null}
    </section>
  )
}
