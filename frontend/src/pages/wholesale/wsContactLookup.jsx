/* GET PHONES & EMAILS - one screen for every list that holds people (cash
 * buyers, funding partners, property owners). It only ever works on the
 * records the person ticked: it shows what will happen to each one and the
 * most it can cost, lets a record without a street address be given one,
 * and then runs in small batches so the progress - and the spend - is in view.
 * Charged per person found; misses are free. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Drawer, Skeleton, Tag } from './ds/ds'
import { ErrorBox, errText, Note } from './wsShared'

const STATUS = {
  ready: ['Will look up', 'live'],
  has_both: ['Has phone & email', null],
  looked_up: ['Already looked up', null],
  no_address: ['Needs an address', 'info'],
  blocked: ['Skipped', null],
  not_found: ['Not found', null],
}

const money = (c) => '$' + ((c || 0) / 100).toFixed(2)

export default function ContactLookup({ kind, ids, what = 'record', onClose, onDone }) {
  const [est, setEst] = useState(null)
  const [again, setAgain] = useState(false)
  const [addr, setAddr] = useState({})
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(null)

  const check = useCallback(async () => {
    setError(null)
    try { setEst(await api.post('/wholesale/contact-lookup/estimate', { kind, ids, again })) }
    catch (e) { setError(errText(e)) }
  }, [kind, ids, again])
  useEffect(() => { check() }, [check])

  async function saveAddress(id) {
    setBusy(true); setError(null)
    try {
      await api.post('/wholesale/contact-lookup/address', { kind, id, address: addr[id] || '' })
      setAddr((a) => ({ ...a, [id]: '' }))
      await check()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  async function run() {
    const todo = (est && est.ready_ids) || []
    const each = est.cost_per_find_cents
    const size = est.batch_size || 10
    let looked = 0, found = 0, spent = 0
    setBusy(true); setError(null)
    setProgress({ looked, found, spent, total: todo.length })
    try {
      for (let i = 0; i < todo.length; i += size) {
        const chunk = todo.slice(i, i + size)
        const r = await api.post('/wholesale/contact-lookup/run',
                                 { kind, ids: chunk, again, max_cost_cents: chunk.length * each })
        looked += r.looked_up; found += r.found; spent += r.cost_cents
        setProgress({ looked, found, spent, total: todo.length })
        if (!r.looked_up) break
      }
      onDone(`Looked up ${looked} ${what}(s): found a phone or email for ${found}. Cost ${money(spent)}.`)
      setAgain(false)
      await check()
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  const rows = (est && est.rows) || []
  const n = (est && est.count) || 0
  const c = (est && est.counts) || {}
  return (
    <Drawer open onClose={onClose} title="Get phones & emails"
            sub={`${ids.length} ${what}(s) selected · ${money(est ? est.cost_per_find_cents : 10)} per one found · misses are free`}>
      <ErrorBox error={error} />
      {!est ? <Skeleton rows={3} /> : (
        <>
          {!est.configured ? (
            <Note>Not connected yet. Add your Tracerfy API token in Render (advisorflow-backend → Environment)
              as <b>TRACERFY_API_TOKEN</b>, then come back here.</Note>
          ) : null}
          {est.cap_refusal ? <Note>{est.cap_refusal}</Note> : null}
          <div className="ws-actions" style={{ flexWrap: 'wrap', gap: 10, margin: '8px 0', alignItems: 'center' }}>
            <button className="btn btn--primary btn--sm" disabled={busy || !est.configured || !!est.cap_refusal || !n}
                    onClick={run}>
              {busy && progress ? 'Looking up…' : n ? `Look up ${n} - up to ${money(est.max_cost_cents)}` : 'Nothing to look up'}
            </button>
            {c.looked_up ? (
              <label className="evo-small"><input type="checkbox" checked={again} disabled={busy}
                     onChange={(e) => setAgain(e.target.checked)} /> Look up the {c.looked_up} already looked up again</label>
            ) : null}
          </div>
          {progress ? (
            <div className="ws-hint">
              {progress.looked} of {progress.total} looked up · {progress.found} found · {money(progress.spent)} spent
            </div>
          ) : null}
          <div className="evo-table-wrap" style={{ maxHeight: 460, overflow: 'auto', marginTop: 8 }}>
            <table className="evo-table">
              <thead><tr><th>Name</th><th>Looked up by</th><th>What happens</th></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const [label, tone] = STATUS[r.status] || [r.status, null]
                  return (
                    <tr key={r.id}>
                      <td><span className="evo-strong">{r.name || '—'}</span>
                        <span className="evo-prop__sub">{[r.phone, r.email].filter(Boolean).join(' · ') || 'no phone or email'}</span></td>
                      <td className="evo-small">
                        {r.status === 'no_address' ? (
                          <span style={{ display: 'flex', gap: 6 }}>
                            <input className="ws-input ws-input--inline" placeholder="street, city, TX zip"
                                   value={addr[r.id] || ''} disabled={busy}
                                   onChange={(e) => setAddr((a) => ({ ...a, [r.id]: e.target.value }))} />
                            <button className="btn btn--secondary btn--sm" disabled={busy || !(addr[r.id] || '').trim()}
                                    onClick={() => saveAddress(r.id)}>Save</button>
                          </span>
                        ) : (r.address || '—')}
                      </td>
                      <td><Tag kind={tone || undefined}>{label}</Tag>{r.why ? <span className="evo-prop__sub">{r.why}</span> : null}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <Note>A phone or email you typed in is never replaced. If the lookup finds a different person at
            someone's address, nothing is filled in and their notes say who was found. Numbers on the Do Not
            Call list are never filled in - they are listed in the notes for you to judge.</Note>
        </>
      )}
    </Drawer>
  )
}
