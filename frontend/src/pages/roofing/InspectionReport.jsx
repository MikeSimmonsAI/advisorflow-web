/**
 * The inspection report - /program/roofing/inspections/:id/report
 *
 * What the homeowner (and the estimator) get: findings, recommendation and
 * every photo, laid out to print cleanly on letter paper (window.print()).
 * A draft is shown with a DRAFT banner, never passed off as final.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ErrorNote, Icon, Loading, SampleTag, localWhen, money, useApi, useToast } from './ui'
import { Handoff, PhotoImg } from './InspectionWorkspace'
import './roofing-inspect.css'

const BASE = '/program/roofing/inspections'

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
}

export default function InspectionReport() {
  const { id } = useParams()
  const res = useApi(`/roofing-ops/inspections/${id}`)
  const [over, setOver] = useState(null)
  const [toast, say] = useToast()
  if (res.error) return <div className="rf-page"><ErrorNote onRetry={res.reload}>{res.error}</ErrorNote></div>
  if (!res.data) return <div className="rf-page"><Loading label="Preparing the report" /></div>
  const r = over || res.data
  const H = r.homeowner || {}
  const draft = r.status !== 'completed'
  return (
    <div className="rf-page ri-report-page">
      <div className="ri-top no-print">
        <Link to={`${BASE}/${id}`} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="back" size={16} />{draft ? 'Back to inspection' : 'Inspection'}</Link>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link to={BASE} className="rf-btn rf-btn--ghost rf-btn--sm">All inspections</Link>
          <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => window.print()}><Icon name="print" size={16} />Print / save PDF</button>
        </div>
      </div>

      <article className="ri-report">
        {draft && <div className="ri-report__draft">DRAFT - this inspection has not been completed</div>}
        {r.sample_data && <div className="ri-report__sample"><SampleTag /> Sample report for demonstration - not a real property.</div>}
        <header className="ri-report__head">
          <div>
            <div className="ri-report__co" style={r.company?.color ? { color: r.company.color } : undefined}>{r.company?.name}</div>
            <div className="ri-report__co-sub">{[r.company?.phone, r.company?.email].filter(Boolean).join(' · ')}</div>
          </div>
          <div className="ri-report__id">
            <div className="ri-report__title">Roof Inspection Report</div>
            <div>{r.report_number ? `Report ${r.report_number}` : 'Report number assigned on completion'}</div>
            <div>{fmtDate(r.completed_at || r.started_at)}</div>
          </div>
        </header>

        <section className="ri-report__grid">
          <div><h3>Property</h3><p><b>{H.name}</b><br />{H.address || 'No address on file'}<br />{[H.phone, H.email].filter(Boolean).join(' · ')}</p></div>
          <div><h3>Inspection</h3><p>{r.inspection_type}<br />{r.scheduled_at ? localWhen(r.scheduled_at) : fmtDate(r.started_at)}<br />Inspector: {r.inspector_name || '—'}</p></div>
          <div><h3>Roof</h3><p>{r.roof_type || 'Type not recorded'}<br />{r.roof_age_years != null ? `${r.roof_age_years} years old` : 'Age not recorded'}{r.stories ? ` · ${r.stories} ${r.stories === 1 ? 'story' : 'stories'}` : ''}</p></div>
        </section>

        <section className="ri-report__summary">
          <div className={`ri-report__badge c-${r.roof_condition || 'na'}`}><span>Condition</span><b>{r.roof_condition_label || '—'}</b></div>
          <div className={`ri-report__badge s-${r.severity || 'na'}`}><span>Damage</span><b>{r.severity_label || '—'}</b></div>
          <div className="ri-report__badge"><span>Recommendation</span><b>{r.recommendation_label || '—'}</b></div>
          <div className="ri-report__badge"><span>Insurance</span><b>{r.insurance_claim_label || '—'}</b></div>
        </section>

        <section>
          <h3>Findings</h3>
          {r.damage_labels?.length
            ? <ul className="ri-report__list">{r.damage_labels.map((d) => <li key={d}>{d}</li>)}</ul>
            : <p>No damage items recorded.</p>}
          {r.affected_areas?.length > 0 && <p><b>Affected areas:</b> {r.affected_areas.join(', ')}</p>}
          <h3>Inspector's notes</h3>
          <p className="ri-report__notes">{r.notes || '—'}</p>
          <h3>Recommendation</h3>
          <p><b>{r.recommendation_label || '—'}</b>{r.recommendation_notes ? ` - ${r.recommendation_notes}` : ''}</p>
          {r.opportunity?.estimate_amount ? <p><b>Estimate:</b> {money(r.opportunity.estimate_amount, false)}</p> : null}
        </section>

        <section>
          <h3>Photos ({r.photos?.length || 0})</h3>
          <div className="ri-report__photos">
            {(r.photos || []).map((p, i) => (
              <figure key={p.id}>
                <PhotoImg url={p.url} alt={p.caption || p.label_text} />
                <figcaption><b>{i + 1}. {p.label_text || 'Photo'}</b>{p.caption ? ` - ${p.caption}` : ''}</figcaption>
              </figure>
            ))}
          </div>
        </section>

        <footer className="ri-report__foot">
          <div><span>Inspector</span><b>{r.completed_by_name || r.inspector_name || '—'}</b></div>
          <div><span>Completed</span><b>{r.completed_at ? fmtDate(r.completed_at) : 'Not completed'}</b></div>
          <p>This report records the visible condition of the roof on the date inspected. It is not a warranty.</p>
        </footer>
      </article>

      {!draft && <div className="no-print" style={{ maxWidth: 860, margin: '20px auto 0' }}>
        <Handoff ins={r} setIns={(f) => setOver(typeof f === 'function' ? f(r) : f)} say={say} />
      </div>}
      {toast}
    </div>
  )
}
