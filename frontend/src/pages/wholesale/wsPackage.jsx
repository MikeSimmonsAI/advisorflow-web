/* INVESTOR DEAL PACKAGE - Draft -> In review -> Approved -> Published -> Under offer -> Closed / Withdrawn.
 * Server: /wholesale/deals/{id}/package (+ /submit, /publish, /withdraw, .pdf).
 * Content is exactly what the investor page shows (set in the Sharing panel below).
 * Publishing is refused unless the Approval Center approved this exact content.
 */
import { useCallback, useEffect, useState } from 'react'
import { api, fetchObjectUrl } from '../../api/client'

const ORDER = ['draft', 'review', 'approved', 'published', 'under_offer', 'closed']
const NAMES = { draft: 'Draft', review: 'In review', approved: 'Approved', published: 'Published', under_offer: 'Under offer', closed: 'Closed' }

export function DealPackagePanel({ dealId, onChanged }) {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const load = useCallback(() => api.get(`/wholesale/deals/${dealId}/package`).then(setSt).catch((e) => setErr(e.message)), [dealId])
  useEffect(() => { load() }, [load])

  async function act(path, body) {
    setBusy(true); setErr(null)
    try { setSt(await api.post(`/wholesale/deals/${dealId}/package${path}`, body || {})); if (onChanged) onChanged() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  async function pdf() {
    setErr(null)
    try { const url = await fetchObjectUrl(`/wholesale/deals/${dealId}/package.pdf`); window.open(url, '_blank', 'noopener') } catch (e) { setErr(e.message) }
  }
  if (!st) return <div className="panel ws-panel">{err ? <div className="ws-error">{err}</div> : 'Loading the deal package…'}</div>
  const idx = ORDER.indexOf(st.status)
  return (
    <div className="panel ws-panel uw-package">
      <div className="panel-title ws-panel-title">
        <span>Deal package{st.version ? ` v${st.version}` : ''} · <b>{st.label}</b></span>
        <span className="uw-row" style={{ marginTop: 0 }}>
          {st.status === 'none' || st.status === 'withdrawn'
            ? <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => act('')}>Create deal package</button>
            : <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => act('')} title="Start a new version from the current content">New version</button>}
          {st.status !== 'none' ? <button type="button" className="btn btn--secondary btn--sm" onClick={pdf}>PDF</button> : null}
        </span>
      </div>
      {st.status !== 'none' && st.status !== 'withdrawn' ? (
        <ol className="uw-flow" aria-label="Package status" style={{ listStyle: 'none', padding: 0 }}>
          {ORDER.map((k, i) => (
            <li key={k} className={`uw-step ${i < idx ? 'is-done' : i === idx ? 'is-attention' : ''}`} style={{ cursor: 'default' }}>
              <span className="uw-step__icon" aria-hidden="true">{i < idx ? '✓' : i === idx ? '●' : '○'}</span>{NAMES[k]}
            </li>))}
        </ol>
      ) : null}
      {err ? <div className="ws-error">{err}</div> : null}
      {st.notes.map((n) => <div key={n} className="uw-missing" style={{ marginBottom: 6 }}>{n}</div>)}
      {st.missing.length && st.status !== 'none' ? (
        <div className="uw-missing"><b>Before review</b><ul>{st.missing.map((m) => <li key={m}>{m}</li>)}</ul>
          <span className="uw-small">Set these in the investor page settings below.</span></div>
      ) : null}
      <div className="uw-row">
        {st.can.submit ? <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => act('/submit')}>Send for approval</button> : null}
        {st.status === 'review' ? <span className="ws-muted uw-small">Waiting in the Approval Center.</span> : null}
        {st.can.publish ? <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => act('/publish')}>Publish to investors</button> : null}
        {st.can.withdraw ? <button type="button" className="btn btn--secondary btn--sm" disabled={busy}
                                   onClick={() => { const r = window.prompt('Reason for withdrawing (optional)') ; if (r !== null) act('/withdraw', { reason: r }) }}>Withdraw</button> : null}
      </div>
      <p className="ws-muted uw-small">
        The package is exactly what investors see - address, asking price, photos and anything you switch on below.
        It never includes the seller, your contract price, MAO or fee. Edits after approval send it back for review.
      </p>
    </div>
  )
}
