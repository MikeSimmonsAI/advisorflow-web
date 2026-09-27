/* SELLER INTELLIGENCE (GET /wholesale/sellers/{id}/intelligence).
 *
 * Three questions kept apart, never merged into one score:
 *   Where is this seller?       the lifecycle, derived from existing states
 *   May we reach them, and how?  contactability, per channel, with the reason
 *   Do they want to sell?        Seller Intent + the qualification outcome
 * and what they actually said - every fact with the words it came from. An
 * AI reading is "seller stated", never verified; only a person verifies.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { errText } from './wsShared'
import { humanize } from './ds/ds'

const CH = { sms: 'SMS', email: 'Email', voice: 'Phone call' }

export function SellerIntelPanel({ profileId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    if (!profileId) return
    api.get(`/wholesale/sellers/${profileId}/intelligence`).then(setData).catch((e) => setError(errText(e)))
  }, [profileId])
  useEffect(() => { load() }, [load])

  const post = async (path, body) => {
    setBusy(true); setError(null)
    try { setData(await api.post(path, body || {})) } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  if (!profileId) return null
  if (error && !data) return <div className="panel ws-panel"><div className="ws-error">{error}</div></div>
  if (!data) return null
  const q = data.qualification || {}
  const c = data.contactability || {}
  const si = data.seller_intent || {}
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">Seller intelligence</div>
      {error ? <div className="ws-error">{error}</div> : null}
      <div className="ws-kv ws-kv--tight">
        <div><span className="ws-kv-label">Where they are</span>
          <span className="ws-pill">{data.lifecycle?.label}</span>
          <div className="ws-muted">{data.lifecycle?.why}</div></div>
        <div><span className="ws-kv-label">Qualification</span>
          <span className="ws-pill">{q.label || '—'}</span>
          {(q.reasons || []).map((r, i) => <div key={i} className="ws-muted">{r}</div>)}
          {q.known_unknowns && q.known_unknowns.length
            ? <div className="ws-muted">Still unknown: {q.known_unknowns.map(humanize).join(', ')}</div> : null}</div>
        <div><span className="ws-kv-label">Seller Intent</span>
          <b>{si.value ?? 'insufficient'}</b>
          {(si.factors || []).slice(0, 4).map((f, i) => <div key={i} className="ws-muted">{f.points > 0 ? '+' : ''}{f.points} {f.label}</div>)}</div>
        <div><span className="ws-kv-label">Contactability</span>
          <span className="ws-pill">{c.label}</span>
          {Object.entries(c.channels || {}).map(([ch, v]) => (
            <div key={ch} className="ws-muted">{CH[ch] || ch}: {humanize(v.state)}{v.reason ? ` — ${v.reason}` : ''}</div>
          ))}</div>
        {data.nurture?.until ? (
          <div><span className="ws-kv-label">Nurture</span>
            until {new Date(data.nurture.until).toLocaleDateString()}{data.nurture.reason ? ` — ${data.nurture.reason}` : ''}
            <button type="button" className="btn btn-sm" disabled={busy}
                    onClick={() => post(`/wholesale/sellers/${profileId}/nurture`, { clear: true })}>End nurture</button></div>
        ) : null}
        {data.acquisition_cost ? (
          <div><span className="ws-kv-label">Cost to find</span>
            {data.acquisition_cost.total} ({data.acquisition_cost.charged_lookups} paid lookup{data.acquisition_cost.charged_lookups === 1 ? '' : 's'})</div>
        ) : null}
      </div>
      <div className="panel-title ws-panel-title" style={{ marginTop: 12 }}>What they told us</div>
      {!data.facts.length ? <div className="ws-muted">Nothing yet.</div> : (
        <ul className="ws-facts" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          {data.facts.map((f) => (
            <li key={f.id} style={{ marginBottom: 8 }}>
              <b>{humanize(f.fact_type)}</b>{f.value ? `: ${f.value}` : ''}
              {f.quote ? <div className="ws-muted">“{f.quote}”</div> : null}
              <div className="ws-muted">
                {f.extracted_by === 'ai' ? 'Read by AI' : f.extracted_by === 'rules' ? 'Read by rules'
                  : f.extracted_by === 'form' ? 'From the inquiry form' : 'Entered by a person'}
                {' · '}{f.verified ? 'verified' : humanize(f.truth_state)}
                {!f.verified ? (
                  <button type="button" className="btn btn-sm" style={{ marginLeft: 6 }} disabled={busy}
                          onClick={() => post(`/wholesale/sellers/${profileId}/facts/${f.id}/verify`)}>Verify</button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
