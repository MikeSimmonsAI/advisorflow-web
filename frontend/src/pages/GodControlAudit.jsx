/**
 * God Mode — control-plane audit (Checkpoint 6 §23).
 *
 * Reads the ONE audit table. Checkpoint 6 added columns to it rather than
 * building a second engine, so this view and an implementation's own history
 * are the same rows through different filters and cannot disagree.
 */
import { Fragment, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Panel, Empty, whenExact, errText } from './god/GodOpsShared'
import './god/GodOps.css'

function pretty(json) {
  if (!json) return null
  try { return JSON.stringify(JSON.parse(json), null, 1) } catch (_) { return json }
}

export default function GodControlAudit() {
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [action, setAction] = useState('')
  const [open, setOpen] = useState({})

  useEffect(() => {
    setD(null)
    api.get('/god/ops/audit' + (action ? '?action=' + encodeURIComponent(action) : ''))
      .then(setD).catch(e => setErr(errText(e)))
  }, [action])

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/sales-operations')}>← Sales Operations</button>
          <h1 style={{ marginTop: 8 }}>Control-plane audit</h1>
          <p>Who did what, to which record, from what to what, and why. No secrets
             are recorded here — activation links and integration keys never appear.</p>
        </div>
      </div>

      {err ? <div className="go-note err">{err}</div> : null}

      <div className="go-filters">
        <select value={action} onChange={e => setAction(e.target.value)}>
          <option value="">All control-plane actions</option>
          {(d && d.actions ? d.actions : []).map(a => (
            <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>

      <Panel title="Entries" count={d ? d.entries.length : null}>
        {d === null ? <Empty>Loading…</Empty>
          : !d.entries.length ? <Empty>Nothing recorded for this filter.</Empty> : (
          <table className="go-table">
            <thead>
              <tr><th>When</th><th>Action</th><th>Actor</th><th>Target</th><th></th></tr>
            </thead>
            <tbody>
              {d.entries.map(e => (
                // Keyed Fragment, not <>. A shorthand fragment cannot take a
                // key, and React would warn about every row in this list.
                <Fragment key={e.id}>
                  <tr>
                    <td data-label="When">{whenExact(e.at)}</td>
                    <td data-label="Action"><span className="go-badge">{e.action.replace(/_/g, ' ')}</span></td>
                    <td data-label="Actor">{e.actor || e.actor_user_id}</td>
                    <td data-label="Target">{e.target_type}</td>
                    <td data-label="">
                      <button className="go-btn sm ghost"
                              onClick={() => setOpen(o => ({ ...o, [e.id]: !o[e.id] }))}>
                        {open[e.id] ? 'Hide' : 'Detail'}
                      </button>
                    </td>
                  </tr>
                  {open[e.id] ? (
                    <tr>
                      <td colSpan={5} data-label="Detail">
                        <div className="go-facts">
                          <div className="go-fact">
                            <div className="k">Before</div>
                            <pre className="v" style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 11 }}>
                              {pretty(e.before) || '—'}</pre>
                          </div>
                          <div className="go-fact">
                            <div className="k">After</div>
                            <pre className="v" style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 11 }}>
                              {pretty(e.after) || '—'}</pre>
                          </div>
                          <div className="go-fact">
                            <div className="k">Details</div>
                            <pre className="v" style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 11 }}>
                              {pretty(e.details) || '—'}</pre>
                          </div>
                          <div className="go-fact">
                            <div className="k">Context</div>
                            <div className="v" style={{ fontSize: 11 }}>
                              org {e.organization_id || '—'}<br />
                              platform {e.platform_id || '—'}<br />
                              brand {e.brand_sales_org_id || '—'}<br />
                              {e.note ? 'note: ' + e.note : null}
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}
