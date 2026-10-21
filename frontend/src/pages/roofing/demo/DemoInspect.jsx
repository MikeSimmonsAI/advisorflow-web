/**
 * Public demo: Inspections - the dashboard, one inspection (the field form,
 * read-only) and the finished, printable report. Same look as the real
 * inspection screens; fictional homeowners and labelled SAMPLE PHOTO images
 * served as static files. Nothing here calls the Roofing API.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Avatar, Icon, Panel, Pill } from '../ui'
import { COMPANY, DEMO_BASE, INSPECTIONS, STAGE_LABEL, byId, fmtDate, fmtTime, fmtWhen, inspectionById } from './fixtures'
import { BOOK_DEMO, usePreview } from './demoCtx'

const STATUS = {
  upcoming: ['green', 'Booked'], in_progress: ['orange', 'In progress'], completed: ['slate', 'Completed'],
}
const BUCKETS = [
  ['upcoming', 'Upcoming', 'green', 'Booked and coming up - open the inspection when you arrive.', (i) => i.status === 'upcoming'],
  ['in_progress', 'In progress', 'blue', 'Being documented right now.', (i) => i.status === 'in_progress'],
  ['next_step', 'Needs estimate / follow-up', 'violet', 'Report complete - sales takes the next step.', (i) => i.status === 'completed' && ['inspected', 'estimate_sent'].includes(i.sales)],
  ['completed', 'Completed', 'slate', 'Finished reports, attached to the homeowner.', (i) => i.status === 'completed' && ['won', 'lost'].includes(i.sales)],
]
const COUNTS = { upcoming: 9, in_progress: 1, next_step: 31, completed: 33 }
const ROOF_TYPES = ['Asphalt shingle', 'Architectural shingle', 'Metal', 'Tile', 'Slate', 'Flat / low slope']
const CONDITIONS = ['Good', 'Fair', 'Poor', 'Failed']
const SEVERITIES = ['No damage', 'Minor', 'Moderate', 'Severe']

function action(i) {
  if (i.status === 'upcoming') return 'Open inspection'
  if (i.status === 'in_progress') return 'Continue report'
  if (i.sales === 'inspected') return 'Prepare estimate'
  if (i.sales === 'estimate_sent') return 'Follow up on estimate'
  return 'View report'
}

export function DemoInspections() {
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const preview = usePreview()
  const tab = sp.get('tab') || ''
  const go = (i) => nav(i.status === 'completed' ? `${DEMO_BASE}/inspections/${i.id}/report` : `${DEMO_BASE}/inspections/${i.id}`)
  const cont = INSPECTIONS.find((i) => i.status === 'in_progress')
  const next = INSPECTIONS.find((i) => i.status === 'upcoming')
  const shown = tab ? BUCKETS.filter((b) => b[0] === tab) : BUCKETS
  return (
    <div className="rf-page ri-dash">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Inspections</h1>
          <p className="rf-lede">From booked appointment to finished report. Open an inspection on site to record the roof, the damage and photos - then hand it to sales.</p>
        </div>
        <button className="rf-btn rf-btn--primary" onClick={() => preview('Book an inspection', 'In a live workspace you pick a homeowner, see real open times from your team’s calendars and confirm. Nothing is booked in this demo.')}><Icon name="calendar" size={18} />Book an inspection</button>
      </div>
      <section className="ri-nextup dm-nextup" aria-label="Your next work">
        {[['cont', 'Finish this report', cont], ['next', 'Next inspection', next]].map(([k, t, i]) => (
          <div key={k} className={`ri-nextup__card ri-nextup__card--${k}`}>
            <span className="ri-nextup__k">{t}</span>
            <b>{byId[i.lead].name}</b>
            <span className="ri-nextup__meta">{fmtWhen(i.scheduled)} · {byId[i.lead].address}</span>
            <button className="rf-btn rf-btn--primary ri-nextup__btn" onClick={() => go(i)}>{action(i)}</button>
          </div>
        ))}
      </section>
      <div className="ri-strip" role="tablist" aria-label="Inspection status">
        <button role="tab" aria-selected={!tab} className={`ri-strip__t ${!tab ? 'on' : ''}`} onClick={() => setSp({})}><b>{Object.values(COUNTS).reduce((a, b) => a + b, 0)}</b><span>All</span></button>
        {BUCKETS.map(([k, label, tone]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`ri-strip__t t-${tone} ${tab === k ? 'on' : ''}`} onClick={() => setSp({ tab: k })}><b>{COUNTS[k]}</b><span>{label}</span></button>
        ))}
      </div>
      <div className="ri-buckets">
        {shown.map(([k, label, tone, sub, test]) => {
          const rows = INSPECTIONS.filter(test)
          return (
            <Panel key={k} title={label} sub={sub} actions={<Pill tone={tone}>{COUNTS[k]}</Pill>}>
              <ul className="rf-insp-list">
                {rows.map((i) => {
                  const h = byId[i.lead]
                  return (
                    <li key={i.id} className="rf-insp-row ri-row">
                      <span className="rf-insp-date">
                        <b>{i.scheduled.toLocaleDateString(undefined, { weekday: 'short' })}</b>
                        <span>{i.scheduled.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>
                      </span>
                      <span className="rf-insp-main">
                        <b>{h.name}{i.report ? <span className="ri-rn">{i.report}</span> : null}</b>
                        <span className="rf-muted">{h.address}</span>
                        <span className="rf-muted ri-row__meta">{[i.scheduled.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }), i.inspector,
                          i.severity && `${i.severity} damage`, i.recommendation, i.photos && `${i.photos.length} photos`, i.estimate && `Estimate $${i.estimate.toLocaleString()}`].filter(Boolean).join(' · ')}</span>
                      </span>
                      <button className={`rf-btn rf-btn--sm ${i.sales === 'won' || i.sales === 'lost' ? 'rf-btn--ghost' : 'rf-btn--primary'}`} onClick={() => go(i)}>{action(i)}</button>
                    </li>
                  )
                })}
              </ul>
              {COUNTS[k] > rows.length && <p className="rf-muted" style={{ fontSize: 13, marginTop: 8 }}>Showing {rows.length} sample of {COUNTS[k]}.</p>}
            </Panel>
          )
        })}
      </div>
    </div>
  )
}

function Chips({ options, value, multi }) {
  const has = (o) => (multi ? (value || []).includes(o) : value === o)
  return (
    <div className="ri-chips" role={multi ? 'group' : 'radiogroup'}>
      {options.map((o) => <span key={o} className={`ri-chip ${has(o) ? 'on' : ''}`} aria-pressed={has(o)}>{has(o) && multi && <Icon name="check" size={14} />}{o}</span>)}
    </div>
  )
}
function Section({ n, title, done, children }) {
  return (
    <section className="ri-sec">
      <h2 className="ri-sec__h"><span className={`ri-sec__n ${done ? 'done' : ''}`}>{done ? <Icon name="check" size={14} /> : n}</span>{title}</h2>
      {children}
    </section>
  )
}

function Gallery({ photos }) {
  const [view, setView] = useState(null)
  return (
    <>
      <div className="ri-gallery">
        {photos.map((p, i) => (
          <figure key={i} className="ri-tile">
            <button type="button" className="ri-tile__img" onClick={() => setView(p)} aria-label={`Enlarge photo: ${p.caption}`}>
              <img src={p.src} alt={p.caption} loading="lazy" />
              <span className="ri-tile__tag">{p.label}</span>
            </button>
            <figcaption>{p.caption}</figcaption>
          </figure>
        ))}
      </div>
      {view && (
        <div className="ri-viewer" role="dialog" aria-label="Photo" onClick={() => setView(null)}>
          <div className="ri-viewer__card" onClick={(e) => e.stopPropagation()}>
            <img src={view.src} alt={view.caption} className="ri-viewer__img" />
            <div className="ri-viewer__meta">
              <span><b>{view.label}</b> - {view.caption} <span className="rf-sample">Sample photo</span></span>
              <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => setView(null)} autoFocus>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export function DemoInspection() {
  const { id } = useParams()
  const preview = usePreview()
  const i = inspectionById[id]
  if (!i) return <main className="rf-page"><section className="rf-panel" style={{ padding: 24 }}><h1 className="rf-h1">Inspection not found</h1><Link className="rf-btn rf-btn--primary" to={`${DEMO_BASE}/inspections`}>All inspections</Link></section></main>
  const h = byId[i.lead]
  const [tone, label] = STATUS[i.status]
  const done = i.status === 'completed'
  const blank = i.status === 'upcoming'
  return (
    <div className="rf-page ri">
      <div className="ri-top">
        <Link to={`${DEMO_BASE}/inspections`} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="back" size={16} />Inspections</Link>
        <span className="ri-save">{done ? `Report ${i.report}` : blank ? 'Not started' : 'Draft saved'}</span>
      </div>
      <div className="rf-note dm-fieldnote"><Icon name="clipboard" size={18} />
        <span>This is the screen the inspector uses <b>on site, on a phone</b>. It saves as they go - even without signal - and turns into the report. Shown read-only in the demo.</span></div>
      <header className="ri-head rf-panel">
        <div className="ri-head__who">
          <Avatar name={h.name} size={52} />
          <div>
            <h1 className="ri-h1">{h.name}</h1>
            <p className="ri-addr"><Icon name="pin" size={15} />{h.address}</p>
            <Link className="ri-holink" to={`${DEMO_BASE}/homeowners/${h.id}`}>Homeowner record</Link>
          </div>
        </div>
        <div className="ri-head__tags"><Pill tone={tone}>{label}</Pill><span className="rf-sample">Sample data</span></div>
        <dl className="ri-facts">
          <div><dt>When</dt><dd>{fmtWhen(i.scheduled)}</dd></div>
          <div><dt>Type</dt><dd>{i.type}</dd></div>
          <div><dt>Inspector</dt><dd>{i.inspector}</dd></div>
          <div><dt>Stage</dt><dd>{STAGE_LABEL[h.stage]}</dd></div>
        </dl>
        <p className="ri-issue"><b>Homeowner said:</b> {h.roof_issue}</p>
        {i.note && <p className="ri-issue"><b>Booking note:</b> {i.note}</p>}
      </header>
      {done && (
        <div className="ri-done rf-panel">
          <Icon name="check" size={20} />
          <div><b>Report {i.report} is complete.</b><span>Findings and photos are locked so the report stays exactly as delivered.</span></div>
          <Link className="rf-btn rf-btn--primary rf-btn--sm" to={`${DEMO_BASE}/inspections/${i.id}/report`}>View report</Link>
        </div>
      )}
      {blank ? (
        <section className="rf-panel dm-blank">
          <h2 className="rf-panel__title">Ready for the visit</h2>
          <p>When the inspector arrives, they fill in five short sections on this screen: <b>the roof</b>, <b>damage found</b>, <b>photos</b>, <b>notes</b> and the <b>recommendation</b>.</p>
          <div className="dm-blank__btns">
            <Link className="rf-btn rf-btn--primary" to={`${DEMO_BASE}/inspections/i-progress`}>See one being filled in</Link>
            <Link className="rf-btn rf-btn--ghost" to={`${DEMO_BASE}/inspections/i-1006/report`}>See a finished report</Link>
          </div>
        </section>
      ) : (
        <>
          <Section n={1} title="The roof" done>
            <label className="ri-lbl">Roof type</label><Chips options={ROOF_TYPES} value={i.roof_type} />
            <div className="ri-row2">
              <label className="ri-lbl">Roof age (years)<input className="rf-input" readOnly value={i.roof_age} /></label>
              <label className="ri-lbl">Stories<input className="rf-input" readOnly value={i.stories} /></label>
            </div>
            <label className="ri-lbl">Overall condition</label><Chips options={CONDITIONS} value={i.condition.charAt(0).toUpperCase() + i.condition.slice(1)} />
          </Section>
          <Section n={2} title="Damage found" done>
            <label className="ri-lbl">Everything the inspector saw</label><Chips multi options={i.damage} value={i.damage} />
            <label className="ri-lbl">Severity</label><Chips options={SEVERITIES} value={i.severity} />
            <label className="ri-lbl">Affected areas</label><Chips multi options={i.areas} value={i.areas} />
          </Section>
          <Section n={3} title={`Photos (${i.photos.length})`} done>
            {!done && (
              <div className="ri-photo-btns">
                <button className="rf-btn rf-btn--primary ri-big" onClick={() => preview('Take photo', 'On a phone this opens the camera; the photo is attached to this inspection and labeled (damage, flashing, attic...). The camera is off in the demo.')}><Icon name="camera" size={20} />Take photo</button>
                <button className="rf-btn rf-btn--ghost ri-big" onClick={() => preview('Upload photos', 'In a live inspection you can add photos from the phone’s library. Uploads are off in the demo.')}><Icon name="upload" size={20} />Upload photos</button>
              </div>
            )}
            <p className="rf-muted" style={{ fontSize: 13 }}>Tap a photo to enlarge it. These are labeled sample illustrations.</p>
            <Gallery photos={i.photos} />
          </Section>
          <Section n={4} title="Inspector's notes" done><p className="dm-notes">{i.notes}</p></Section>
          {done ? (
            <Section n={5} title="Recommendation" done>
              <Chips options={['Repair', 'Full replacement', 'Monitor / maintenance', 'Further evaluation recommended']} value={i.recommendation} />
              <p className="dm-notes">{i.scope}</p>
              <label className="ri-lbl">Insurance - as reported by the homeowner</label><Chips options={[i.insurance]} value={i.insurance} />
            </Section>
          ) : (
            <section className="rf-panel dm-finish">
              <div><b>Almost done.</b> <span className="rf-muted">Add the recommendation and the report can be completed.</span></div>
              <div className="dm-finish__btns">
                <button className="rf-btn rf-btn--primary" onClick={() => preview('Complete report', 'Completing locks the findings and photos, gives the report a number and hands it to sales. See a finished one next.')}>Complete report</button>
                <Link className="rf-btn rf-btn--ghost" to={`${DEMO_BASE}/inspections/i-1006/report`}>See a finished report</Link>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}

export function DemoReport() {
  const { id } = useParams()
  const preview = usePreview()
  const r = inspectionById[id]
  if (!r || r.status !== 'completed') return <main className="rf-page"><section className="rf-panel" style={{ padding: 24 }}><h1 className="rf-h1">No finished report here</h1><Link className="rf-btn rf-btn--primary" to={`${DEMO_BASE}/inspections/i-1006/report`}>See a sample report</Link></section></main>
  const H = byId[r.lead]
  const sales = r.sales
  return (
    <div className="rf-page ri-report-page">
      <div className="ri-top no-print">
        <Link to={`${DEMO_BASE}/inspections/${r.id}`} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="back" size={16} />Inspection</Link>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Link to={`${DEMO_BASE}/homeowners/${H.id}`} className="rf-btn rf-btn--ghost rf-btn--sm">Homeowner</Link>
          <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => window.print()}><Icon name="print" size={16} />Print / save PDF</button>
        </div>
      </div>
      <article className="ri-report">
        <div className="ri-report__sample"><span className="rf-sample">Sample data</span> Sample report for demonstration - fictional homeowner and illustrated sample photos.</div>
        <header className="ri-report__head">
          <div>
            <div className="ri-report__co">{COMPANY.name}</div>
            <div className="ri-report__co-sub">{COMPANY.phone} · {COMPANY.email}</div>
          </div>
          <div className="ri-report__id">
            <div className="ri-report__title">Roof Inspection Report</div>
            <div>Report {r.report}</div>
            <div>{fmtDate(r.completed)}</div>
          </div>
        </header>
        <section className="ri-report__grid">
          <div><h3>Property</h3><p><b>{H.name}</b><br />{H.address}<br />{H.phone}</p></div>
          <div><h3>Inspection</h3><p>{r.type}<br />Inspected: {fmtDate(r.started)}<br />Inspected by: {r.inspector}</p></div>
          <div><h3>Roof</h3><p>{r.roof_type}<br />{r.roof_age} years old · {r.stories} stories</p></div>
        </section>
        <section className="ri-report__summary">
          <div className={`ri-report__badge c-${r.condition.toLowerCase()}`}><span>Condition</span><b>{r.condition}</b></div>
          <div className={`ri-report__badge s-${r.severity.toLowerCase()}`}><span>Damage</span><b>{r.severity}</b></div>
          <div className="ri-report__badge"><span>Recommendation</span><b>{r.recommendation}</b></div>
          <div className="ri-report__badge"><span>Insurance (homeowner-reported)</span><b>{r.insurance}</b></div>
        </section>
        <section>
          <h3>Findings</h3>
          <ul className="ri-report__list">{r.damage.map((d) => <li key={d}>{d}</li>)}</ul>
          <p><b>Affected areas:</b> {r.areas.join(', ')}</p>
          <h3>Inspector's notes</h3>
          <p className="ri-report__notes">{r.notes}</p>
          <h3>Recommendation</h3>
          <p><b>{r.recommendation}</b> - {r.scope}</p>
          {r.estimate ? <p><b>Estimate:</b> ${r.estimate.toLocaleString()}</p> : null}
          {['Homeowner reports an open claim', 'Homeowner plans to contact their insurance'].includes(r.insurance) && <p className="ri-report__fine">Coverage and claim decisions are made by the insurance provider.</p>}
        </section>
        <section>
          <h3>Photos ({r.photos.length})</h3>
          <div className="ri-report__photos">
            {r.photos.map((p, k) => (
              <figure key={k}><img src={p.src} alt={p.caption} /><figcaption><b>{k + 1}. {p.label}</b> - {p.caption}</figcaption></figure>
            ))}
          </div>
        </section>
        <footer className="ri-report__foot">
          <div><span>Inspected by</span><b>{r.inspector}</b></div>
          <div><span>Report completed by</span><b>{r.completed_by}</b></div>
          <div><span>Completed on</span><b>{fmtDate(r.completed)}</b></div>
          <p>This report records the visible condition of the roof on the date inspected. It is not a warranty.</p>
        </footer>
        <section className="ri-report__hist">
          <h3>Report history</h3>
          <ul>{r.history.map(([l, by, at]) => <li key={l}><b>{l}</b> - {by}, {fmtTime(at)}</li>)}</ul>
        </section>
      </article>
      <div className="no-print" style={{ maxWidth: 860, margin: '20px auto 0' }}>
        <section className="ri-sec ri-handoff">
          <h2 className="ri-sec__h"><span className="ri-sec__n done"><Icon name="arrow" size={14} /></span>Next step for sales</h2>
          <p className="rf-muted">The homeowner is at <b>{STAGE_LABEL[sales]}</b>{r.estimate ? ` · estimate $${r.estimate.toLocaleString()}` : ''}{r.won ? ` · job $${r.won.toLocaleString()}` : ''}.</p>
          {sales === 'won' || sales === 'lost' ? (
            <p className="rf-muted">This job is closed{sales === 'lost' ? ` (${H.lost_reason})` : ' - won'}. The report stays on the homeowner's record.</p>
          ) : (
            <div className="ri-actions">
              <button className="rf-btn rf-btn--primary" onClick={() => preview(sales === 'estimate_sent' ? 'Follow up on the estimate' : 'Prepare estimate',
                sales === 'estimate_sent' ? 'Saves a follow-up reminder on this homeowner. It is a reminder, not a calendar event, and nothing is sent to the homeowner.'
                  : 'Records the estimate amount and scope on the homeowner. No estimate document is created or sent.')}>
                <Icon name={sales === 'estimate_sent' ? 'calendar' : 'dollar'} size={18} />{sales === 'estimate_sent' ? 'Follow up on estimate' : 'Prepare estimate'}
              </button>
              {sales === 'estimate_sent' && <>
                <button className="rf-btn rf-btn--ghost" onClick={() => preview('Homeowner accepted', 'Moves the homeowner to Won with the job amount and keeps the report and estimate on file. See Robert Hayes for a won job.')}><Icon name="check" size={18} />Homeowner accepted</button>
                <button className="rf-btn rf-btn--ghost" onClick={() => preview('Homeowner declined', 'Moves the homeowner to Lost with the reason they gave. The report stays on file.')}><Icon name="close" size={18} />Homeowner declined</button>
              </>}
            </div>
          )}
        </section>
        <section className="rf-panel dm-cta"><div><b>Like what you see?</b><span>We’ll show you this with your own leads and your own inspection process.</span></div><a className="rf-btn rf-btn--primary" href={BOOK_DEMO}>Book a live demo</a></section>
      </div>
    </div>
  )
}
