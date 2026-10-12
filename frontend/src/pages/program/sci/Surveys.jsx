/* Survey answers for the dashboard: totals, the latest answers, and alerts.
   A 1-3 star answer stays an alert until someone marks it handled. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, Panel, errText, when } from './ui'

function Stars({ n }) {
  return (
    <span aria-label={`${n || 0} of 5 stars`} style={{ color: '#c79a3a', letterSpacing: 1, whiteSpace: 'nowrap' }}>
      {'★'.repeat(n || 0)}<span style={{ color: '#d9d4c7' }}>{'★'.repeat(5 - (n || 0))}</span>
    </span>
  )
}

export default function Surveys({ locationId, navigate, isManager = true }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(() => {
    api.get(`/program/surveys${locationId ? `?location_id=${encodeURIComponent(locationId)}` : ''}`)
      .then(d => { setData(d); setErr('') })
      .catch(e => setErr(errText(e)))
  }, [locationId])
  useEffect(() => { load() }, [load])

  async function handled(id) {
    setBusy(id)
    try { await api.post(`/program/surveys/${id}/handled`); load() } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }

  const s = data?.summary
  const open = rec => rec.lead_id && navigate && navigate(`/leads/${rec.lead_id}`)

  return (
    <div id="sci-surveys">
      <Panel title="Surveys" labelledBy="sci-surveys-h"
        aside={s ? <span className="sci-micro sci-muted">
          {s.count ? <>Average <b>{s.average}</b> of 5 · {s.count} answered{s.share_ok ? ` · ${s.share_ok} OK to share` : ''}</> : 'No answers yet'}
        </span> : null}>
        {err && <div className="sci-alert" role="alert">{err}</div>}
        {!data ? <p className="sci-loading">Loading…</p> : (
          <>
            {data.alerts.length > 0 && (
              <div style={{ marginBottom: 14 }}>
                <p className="sci-micro" style={{ fontWeight: 700, color: '#9b2c2c', margin: '0 0 6px' }}>
                  {data.alerts.length} low rating{data.alerts.length === 1 ? '' : 's'} need a call back
                </p>
                <div className="sci-rows">
                  {data.alerts.map(a => (
                    <div className="sci-kv" key={a.id} style={{ alignItems: 'flex-start', background: '#fdf3f2', borderRadius: 10, padding: '10px 12px' }}>
                      <span style={{ minWidth: 0 }}>
                        <button type="button" onClick={() => open(a)}
                          style={{ background: 'none', border: 0, padding: 0, font: 'inherit', fontWeight: 600, cursor: 'pointer' }}>
                          {a.name || 'Contact'}
                        </button>{' '}<Stars n={a.rating} />
                        <span className="sci-micro sci-muted" style={{ display: 'block' }}>{a.location || 'Location not set'} · {when(a.submitted_at)}</span>
                        {a.feedback && <span className="sci-micro" style={{ display: 'block', marginTop: 4, whiteSpace: 'pre-wrap' }}>{a.feedback}</span>}
                      </span>
                      {isManager && (
                        <button type="button" className="sci-btn sm" disabled={busy === a.id} onClick={() => handled(a.id)}>
                          {busy === a.id ? 'Saving…' : 'Mark handled'}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {data.recent.length === 0
              ? <Empty title="No survey answers yet.">Answers show here, and on each person's record, as they come in.</Empty>
              : (
                <div className="sci-rows">
                  {data.recent.map(r => (
                    <div className="sci-kv" key={r.id}>
                      <span style={{ minWidth: 0 }}>
                        <button type="button" onClick={() => open(r)}
                          style={{ background: 'none', border: 0, padding: 0, font: 'inherit', fontWeight: 600, cursor: 'pointer' }}>
                          {r.name || 'Contact'}
                        </button>{' '}<Stars n={r.rating} />{' '}
                        {r.share_ok && <Chip tone="ok" plain>OK to share</Chip>}
                        <span className="sci-micro sci-muted" style={{ display: 'block' }}>
                          {r.location || 'Location not set'}{r.feedback ? ` · “${r.feedback.slice(0, 90)}${r.feedback.length > 90 ? '…' : ''}”` : ''}
                        </span>
                      </span>
                      <span className="sci-micro">{when(r.submitted_at)}</span>
                    </div>
                  ))}
                </div>
              )}
          </>
        )}
      </Panel>
    </div>
  )
}
