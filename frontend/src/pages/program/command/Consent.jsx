/* OPT-INS & OPT-OUTS - every family's texting status, read only (Oct 16).
   Who opted in (and how), who opted out (when, and the words they sent),
   and who has no record yet. Nothing here changes who is texted. */
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'

const LABEL = { opted_in: 'Opted in', opted_out: 'Opted out', no_record: 'No record yet' }
const TAG = { opted_in: 'green', opted_out: 'red', no_record: 'orange' }
const when = iso => (iso ? new Date(iso).toLocaleString([], { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' CT' : '—')

export default function Consent() {
  const [sp, setSp] = useSearchParams()
  const status = ['opted_in', 'opted_out', 'no_record'].includes(sp.get('c')) ? sp.get('c') : ''
  const [q, setQ] = useState('')
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    if (q.trim()) p.set('q', q.trim())
    const t = setTimeout(() => {
      api.get('/program/command/consent' + (p.toString() ? `?${p}` : '')).then(r => { setD(r); setErr('') })
        .catch(e => setErr(e.message || 'Could not load.'))
    }, q ? 250 : 0)
    return () => clearTimeout(t)
  }, [status, q])
  const pick = c => { const n = new URLSearchParams(sp); if (c) n.set('c', c); else n.delete('c'); setSp(n) }
  const c = d?.counts || {}
  return (
    <section className="cs-wrap" aria-label="Opt-ins and opt-outs">
      <header className="cs-head"><h2>Opt-ins &amp; Opt-outs</h2>
        <p>Every family's texting status. Read only - texting still follows the consent and STOP rules.</p></header>
      <div className="cs-cards">
        {[['', 'All families', (c.opted_in || 0) + (c.opted_out || 0) + (c.no_record || 0)], ['opted_in', 'Opted in', c.opted_in],
          ['opted_out', 'Opted out', c.opted_out], ['no_record', 'No record yet', c.no_record]].map(([k, l, v]) => (
          <button key={l} type="button" className={status === k ? 'on' : ''} onClick={() => pick(k)}><small>{l}</small><b>{d ? (v ?? 0).toLocaleString() : '…'}</b></button>))}
      </div>
      <input className="cs-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, phone or location…" aria-label="Search" />
      {err && <p className="cmd-err">{err}</p>}
      {!d ? <p className="cmd-muted">Loading…</p> : !d.items.length ? <p className="cmd-muted">No families match.</p> : (
        <div className="cs-table" role="table">
          <div className="cs-row cs-th" role="row"><span>Family</span><span>Location</span><span>Status</span><span>How</span><span>When</span><span>What they said</span></div>
          {d.items.map(i => (
            <div className="cs-row" role="row" key={i.lead_id}>
              <span><Link to={`/program/live?f=${i.lead_id}&v=journey`}><b>{i.name}</b></Link><small>{i.phone || ''}</small></span>
              <span>{i.location || '—'}</span>
              <span><em className={`sl-tag ${TAG[i.status]}`}>{LABEL[i.status]}</em></span>
              <span>{i.how || '—'}</span>
              <span>{when(i.at)}</span>
              <span>{i.words ? `"${i.words}"` : '—'}</span>
            </div>))}
          {d.total > d.items.length && <p className="cmd-muted">Showing {d.items.length} of {d.total}. Search to narrow.</p>}
        </div>)}
    </section>
  )
}
