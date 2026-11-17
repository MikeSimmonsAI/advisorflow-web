import { useState } from 'react'
import { cardPhoto } from './photos'
import { variantForIndustry } from '../../../design/art/industryVariant.js'
import { demoGroup, demoStatus, DEMO_FILTERS, photoKeyFor, previewDisplayName } from './commandCenterModel'

/**
 * DEMOS - every demo built for a prospect, whatever happened to the deal
 * (open, won or lost). The list is the server's /launcher/demos answer for this
 * login (the brand's sales managers only). A demo whose link expired or was
 * revoked is still listed, without an Open button: nothing is re-opened here.
 */
export default function DemosPanel({ demos, query, rawQuery, onBack }) {
  const [filter, setFilter] = useState('all')
  const counts = DEMO_FILTERS.reduce((m, f) => ({ ...m, [f.key]: demos.filter(d => f.key === 'all' || demoGroup(d) === f.key).length }), {})
  const filters = DEMO_FILTERS.filter(f => f.key === 'all' || counts[f.key] > 0)
  const active = filters.some(f => f.key === filter) ? filter : 'all'
  const matches = d => !query || [previewDisplayName(d), d.contact_name, d.stage_label, d.industry]
    .some(t => t && String(t).toLowerCase().includes(query))
  const shown = demos.filter(d => (active === 'all' || demoGroup(d) === active) && matches(d))

  return (
    <section className="pa2-demos" aria-labelledby="pa2-demos-title">
      <div className="pa2-section-head">
        <div className="pa2-demos-titles">
          <h2 id="pa2-demos-title">{rawQuery ? 'Demos matching “' + rawQuery + '”' : 'Demos'}</h2>
          <p className="pa2-sub">Every demo built for a prospect, whether the deal is open, won or lost. A demo whose link has expired is listed but can’t be opened.</p>
        </div>
        <div className="pa2-filters" role="group" aria-label="Filter demos">
          {filters.map(f => (
            <button type="button" key={f.key} aria-pressed={active === f.key}
                    className={'pa2-chip' + (active === f.key ? ' is-on' : '')} onClick={() => setFilter(f.key)}>
              {f.label} ({counts[f.key]})
            </button>
          ))}
        </div>
        <button type="button" className="pa2-btn pa2-btn--outline pa2-demos-back" onClick={onBack}>
          ← Program Access
        </button>
      </div>
      {shown.length > 0 ? (
        <div className="pa2-demo-grid" role="list" aria-label="Demos">
          {shown.map(d => <DemoCard key={d.opportunity_id} d={d} />)}
        </div>
      ) : <p className="pa2-none">No demo matches that search.</p>}
    </section>
  )
}

function DemoCard({ d }) {
  const name = previewDisplayName(d)
  const st = demoStatus(d)
  const photo = photoKeyFor({ program: 'preview', industry: d.industry, display: name, company_name: d.company_name,
                              variant: variantForIndustry(d.industry || name, 'preview') })
  const when = d.expires_at ? new Date(d.expires_at).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : null
  return (
    <article className={'pa2-demo' + (d.live ? '' : ' is-dead')} role="listitem"
             data-program-key={'demo:' + d.opportunity_id}>
      <img className="pa2-demo-img" src={cardPhoto(photo)} alt="" width="720" height="330" loading="lazy" decoding="async" />
      <div className="pa2-demo-body">
        <div className="pa2-demo-pills">
          <span className={'pa2-dstat pa2-dstat--' + st.tone}>{st.label}</span>
          {d.stage_label && st.label !== d.stage_label && <span className="pa2-dstage">{d.stage_label}</span>}
        </div>
        <h3 className="pa2-demo-name">{name}</h3>
        {d.contact_name && <p className="pa2-demo-meta">{d.contact_name}</p>}
        <p className="pa2-demo-meta">
          {(d.demo_slot === 'platform' ? 'Platform demo' : 'Website concept')}
          {' · '}
          {d.live ? (when ? 'Link works until ' + when : 'Link works') : (when ? 'Link expired ' + when : 'Link no longer works')}
        </p>
        {d.live && d.url ? (
          <a className="pa2-btn pa2-btn--primary pa2-demo-open" href={d.url} target="_blank" rel="noopener noreferrer"
             aria-label={'Open demo: ' + name + ' (opens in a new tab)'}>
            Open Demo
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" /></svg>
          </a>
        ) : (
          <span className="pa2-demo-closed">Link no longer works</span>
        )}
      </div>
    </article>
  )
}
