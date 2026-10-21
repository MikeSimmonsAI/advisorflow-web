/**
 * Pipeline — "Every deal, one clear stage at a time." (Lane F, #281)
 *
 * The approved four columns over the REAL lifecycle stages (see
 * simpleSales.SIMPLE_COLUMNS). Each card still shows its exact stage, and the
 * stage is changed on Lead Details through the existing PATCH. Lost deals and
 * any stage this screen does not recognise are listed below the board rather
 * than dropped. The full nine-stage board stays at /sales/pipeline/stages.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import NewProspect from './NewProspect'
import { SIMPLE_COLUMNS, STAGE_LABELS, groupForSimpleBoard, entryStageFor } from './simpleSales'
import { Icon, ErrorLine, Skeleton } from './SimpleParts'

const TONE = ['', 'ss-c2', 'ss-c3', 'ss-c4']

export default function SimplePipeline() {
  return (
    <SalesShell title="Pipeline" subtitle="Every deal, one clear stage at a time.">
      <PipelineBody />
    </SalesShell>
  )
}

function PipelineBody() {
  const nav = useNavigate()
  const [resp, setResp] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [creatingIn, setCreatingIn] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setResp(await api.get('/sales/opportunities?include_lost=true')) }
    catch (e) { setError(e.message || 'Could not load your pipeline.') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  // A manager's list is the brand; "My Pipeline" is their own book, exactly as
  // the existing My Pipeline screen narrows it.
  const groups = useMemo(() => {
    if (!resp) return null
    const keep = c => !resp.is_manager || c.owner_user_id === resp.viewer_user_id
    return groupForSimpleBoard({
      stages: (resp.stages || []).map(s => ({ ...s, opportunities: (s.opportunities || []).filter(keep) })),
      lost: (resp.lost || []).filter(keep),
    })
  }, [resp])

  async function created(opp, colKey) {
    setCreatingIn(null)
    const stage = entryStageFor(colKey)
    // A new lead starts as a Prospect. Adding it from a later column moves it
    // there with the same stage change the Lead Details picker makes.
    if (stage && stage !== opp.stage) {
      try { await api.patch('/sales/opportunities/' + opp.id, { stage }) }
      catch (e) { setError(e.message || 'Lead added, but its stage could not be set.') }
    }
    load()
  }

  return (
    <>
      <div className="ss-sections">
        <div>
          <h2>My Pipeline</h2>
          <p>See where each customer stands. Click a card to work the deal.</p>
        </div>
        <button type="button" className="ss-btn ss-white" onClick={() => setCreatingIn('new')}>
          <Icon name="plus" /> Add Lead
        </button>
      </div>
      <ErrorLine error={error} onRetry={load} />

      {loading && !groups ? <div className="ss-panel"><Skeleton rows={6} /></div> : groups && (
        <>
          <div className="ss-boards">
            {SIMPLE_COLUMNS.map((col, i) => {
              const cards = groups[col.key]
              return (
                <section key={col.key} className={'ss-boardcol ' + TONE[i]} aria-labelledby={'ss-col-' + col.key}>
                  <div className="ss-colheader">
                    <strong id={'ss-col-' + col.key}>{col.label}</strong>
                    <span className="ss-colcount" aria-label={cards.length + ' deals'}>{cards.length}</span>
                  </div>
                  {cards.length === 0 && col.key === 'won' && (
                    <div className="ss-boardempty"><span aria-hidden="true">🏆</span>
                      <strong>No deals won yet</strong>Keep going!</div>
                  )}
                  {cards.length === 0 && col.key !== 'won' && (
                    <div className="ss-boardempty">Nothing here right now.</div>
                  )}
                  {cards.map(c => (
                    <div key={c.id} className="ss-dealcard">
                      <button type="button" onClick={() => nav('/sales/leads/' + c.id)}>
                        <strong>{c.contact_name || c.company_name}</strong>
                        {c.contact_name && <small>{c.company_name}</small>}
                        {col.stages.length > 1 && <div className="ss-stage">{STAGE_LABELS[c.stage] || c.stage_label}</div>}
                      </button>
                    </div>
                  ))}
                  {col.key !== 'won' && (
                    <button type="button" className="ss-btn ss-white ss-addlead" onClick={() => setCreatingIn(col.key)}>
                      <Icon name="plus" /> Add Lead
                    </button>
                  )}
                </section>
              )
            })}
          </div>

          {(groups.lost.length > 0 || groups.other.length > 0) && (
            <section className="ss-panel" style={{ marginTop: 15 }}>
              <div className="ss-panelhd"><div>
                <h3>Closed and other deals</h3>
                <p>Kept here so nothing disappears from your board</p>
              </div></div>
              <div className="ss-upnext">
                {[...groups.other, ...groups.lost].map(c => (
                  <div key={c.id} className="ss-compact">
                    <span className="ss-status">{STAGE_LABELS[c.stage] || c.stage_label || c.stage}</span>
                    <span className="ss-sp"><strong>{c.contact_name || c.company_name}</strong>
                      {c.contact_name ? ' — ' + c.company_name : ''}</span>
                    <button type="button" className="ss-btn ss-white ss-xs" onClick={() => nav('/sales/leads/' + c.id)}>Open</button>
                  </div>
                ))}
              </div>
            </section>
          )}
          <p className="ss-footnote">
            Click any card, then use Pipeline stage on Lead Details to move it. Need every stage?{' '}
            <button type="button" className="ss-link" onClick={() => nav('/sales/pipeline/stages')}>Open the all-stages board</button>
          </p>
        </>
      )}

      {creatingIn && (
        <NewProspect onClose={() => setCreatingIn(null)} onCreated={opp => created(opp, creatingIn)} />
      )}
    </>
  )
}
