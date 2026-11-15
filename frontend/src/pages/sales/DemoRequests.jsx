/**
 * CUSTOM DEMO REQUESTS — /sales/demo-requests, under My Team.
 *
 * One queue for managers and builders: what needs a decision, what is
 * assigned, what is overdue. Ready and delivered demos are not counted as
 * outstanding. Everything shown and every button is decided by the server
 * (GET /sales/demo-requests, POST .../demo-request/progress).
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { dateTime } from './parts'

const card = { background: '#fff', border: '1px solid #dce5ea', borderRadius: 12, padding: 14, marginBottom: 10 }
const btn = { border: '1px solid #c9d7df', background: '#fff', borderRadius: 8, padding: '6px 10px', cursor: 'pointer', fontSize: 13, marginRight: 6 }
const TABS = [['needs_review', 'Needs a decision'], ['assigned', 'Assigned'], ['overdue', 'Overdue'], ['my_requests', 'My requests'], ['recently_ready', 'Recently ready']]

function errText(e) { return (e && (e.detail || e.message)) || 'Something went wrong.' }

function Row({ it, tab, onProgress, busy }) {
  const [url, setUrl] = useState(it.demo_url || '')
  return (
    <div style={card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <b>{it.company_name}</b>
          <div style={{ fontSize: 13, color: '#425466' }}>
            {it.status_label}{it.salesperson ? ' · for ' + it.salesperson : ''}
            {it.builder ? ' · builder ' + it.builder : ''}
            {it.target_at ? ' · target ' + dateTime(it.target_at) : ''}
            {it.overdue && <b style={{ color: '#a1340c' }}> · overdue</b>}
          </div>
          {it.needs && <div style={{ fontSize: 13, marginTop: 4, whiteSpace: 'pre-wrap' }}>{it.needs}</div>}
        </div>
        <div>
          {tab === 'needs_review' && <Link style={btn} to={it.href}>Open to decide</Link>}
          {(tab === 'my_requests' || tab === 'recently_ready') && <Link style={btn} to={it.href}>Open deal</Link>}
          {it.demo_url && <a style={btn} href={it.demo_url} target="_blank" rel="noopener noreferrer">Open demo</a>}
        </div>
      </div>
      {(tab === 'assigned' || tab === 'overdue') && onProgress && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8, alignItems: 'center' }}>
          {it.status === 'requested' && <button style={btn} disabled={busy} onClick={() => onProgress(it, 'in_progress')}>Start building</button>}
          <input aria-label={'Demo link for ' + it.company_name} placeholder="https:// demo link" value={url}
                 onChange={e => setUrl(e.target.value)} style={{ ...btn, minWidth: 220, cursor: 'text' }} />
          <button style={{ ...btn, background: '#087f83', color: '#fff', borderColor: '#087f83' }} disabled={busy}
                  onClick={() => onProgress(it, 'ready', url)}>Mark ready</button>
        </div>
      )}
    </div>
  )
}

export default function DemoRequests() {
  const [sp, setSp] = useSearchParams()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try { setD(await api.get('/sales/demo-requests')); setErr('') } catch (e) { setErr(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  if (!d) return <div style={{ padding: 20 }}>{err || 'Loading custom demo requests…'}</div>
  const s = d.summary
  const counts = { needs_review: s.needs_review, assigned: s.assigned, overdue: s.overdue,
                   my_requests: d.my_requests.length, recently_ready: d.recently_ready.length }
  const defaultTab = s.needs_review ? 'needs_review' : s.assigned ? 'assigned' : 'my_requests'
  const tab = TABS.some(([k]) => k === sp.get('view')) ? sp.get('view') : defaultTab
  const items = d[tab] || []

  const progress = async (it, status, url) => {
    setBusy(true); setErr('')
    try {
      await api.post('/sales/opportunities/' + it.opportunity_id + '/demo-request/progress',
                     { status, demo_url: url || null })
      await load()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div style={{ padding: '4px 0 20px' }}>
      <p style={{ margin: '0 0 12px' }}><Link to="/sales/my-team">← My Team</Link></p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {[['Needs a decision', s.needs_review], ['Assigned builds', s.assigned], ['Overdue', s.overdue]].map(([l, n]) => (
          <div key={l} style={{ ...card, marginBottom: 0, flex: '1 1 150px' }}>
            <div style={{ fontSize: 24, fontWeight: 800 }}>{n}</div><div style={{ fontSize: 13, color: '#607486' }}>{l}</div>
          </div>
        ))}
      </div>
      <div role="tablist" aria-label="Custom demo requests" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {TABS.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setSp({ view: k })}
                  style={{ ...btn, ...(tab === k ? { background: '#102b40', color: '#fff', borderColor: '#102b40' } : {}) }}>
            {l} ({counts[k]})
          </button>
        ))}
      </div>
      {err && <p role="alert" style={{ color: '#a1340c', fontWeight: 600 }}>{err}</p>}
      {items.length === 0
        ? <div style={card}>Nothing here.</div>
        : items.map(it => <Row key={tab + it.opportunity_id} it={it} tab={tab} busy={busy} onProgress={progress} />)}
    </div>
  )
}
