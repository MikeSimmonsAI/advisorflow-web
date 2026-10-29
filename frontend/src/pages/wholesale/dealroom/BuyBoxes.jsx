/* BUY BOXES - every buyer's criteria in one list (Buyers > Buy Boxes).
 * Read view over the existing Buyer Intelligence rows (/wholesale/buyer-intel/buyers);
 * a row opens that buyer's profile where the boxes are edited. No new store. */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../../api/client'
import { Alert, Empty, EvoApp, Hero, Search, Skeleton, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import { errText } from '../wsShared'
import { cap, usd } from './shared'
import './dealroom.css'

const SEG = { single_family: 'Single-family', multifamily: 'Multifamily', land: 'Land', commercial: 'Commercial',
  industrial: 'Industrial', mixed_use: 'Mixed-use', portfolio: 'Portfolio', other: 'Other' }
const TONE = { CONFIRMED: 'live', STATED: 'info', INFERRED: 'estimate', STALE: 'danger', FILE: undefined, NONE: undefined }

export default function BuyBoxes() {
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [practice, setPractice] = useState(false)
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    const t = setTimeout(() => {
      const p = new URLSearchParams({ segment: 'all', include_test: String(practice), limit: '200' })
      if (q) p.set('q', q)
      api.get(`/wholesale/buyer-intel/buyers?${p}`).then(setD).catch((e) => setErr(errText(e)))
    }, 200)
    return () => clearTimeout(t)
  }, [q, practice])
  const rows = d ? d.buyers.filter((b) => b.buy_box && b.buy_box.status !== 'NONE') : []
  return (
    <EvoApp world="operations">
      <Hero compact scene="capital" eyebrow="Buyers" title="Buy Boxes" sub="What each buyer is looking for - stated, confirmed or inferred from their purchases." />
      <Alert>{err}</Alert>
      <section className="drm-card">
        <div className="drm-card__head">
          <h2 className="drm-h3">Buy boxes <span className="drm-count">{d ? rows.length : '…'}</span></h2>
          <div className="drm-row">
            <Search value={q} onChange={setQ} placeholder="Search buyer name or ZIP" label="Search buy boxes" />
            <label className="drm-check"><input type="checkbox" checked={practice} onChange={(e) => setPractice(e.target.checked)} />Practice records</label>
          </div>
        </div>
        {!d ? <Skeleton rows={5} /> : rows.length ? (
          <div className="evo-table-wrap"><table className="evo-table evo-table--cards">
            <thead><tr><th scope="col">Buyer</th><th scope="col">Segments</th><th scope="col">Where</th><th scope="col">Price</th>
              <th scope="col">Units</th><th scope="col">Strategies</th><th scope="col">Buy box</th></tr></thead>
            <tbody>{rows.map((b) => (
              <tr key={b.buyer_id} className="bi-row" tabIndex={0} onClick={() => nav(`/wholesale/buyers?buyer=${b.buyer_id}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter') nav(`/wholesale/buyers?buyer=${b.buyer_id}`) }}>
                <td className="is-lead" data-label=""><span className="evo-strong">{b.name}</span>{b.is_test ? <span className="evo-prop__sub">practice</span> : null}</td>
                <td data-label="Segments">{Object.keys(b.segments || {}).map((k) => <Tag key={k}>{SEG[k] || cap(k)}</Tag>)}</td>
                <td data-label="Where" className="evo-small">{[...b.geography.counties.map((c) => c + ' Co.'), ...b.geography.cities].slice(0, 3).join(', ') || b.geography.states.join(', ') || '—'}</td>
                <td data-label="Price" className="evo-small">{b.price.min || b.price.max ? `${b.price.min ? usd(b.price.min) : 'Any'} – ${b.price.max ? usd(b.price.max) : 'Any'}` : '—'}</td>
                <td data-label="Units" className="evo-small">{b.units.min || b.units.max ? `${b.units.min || 'Any'} – ${b.units.max || 'Any'}` : '—'}</td>
                <td data-label="Strategies" className="evo-small">{(b.strategies || []).map(cap).join(', ') || '—'}</td>
                <td data-label="Buy box"><Tag kind={TONE[b.buy_box.status]}>{b.buy_box.label}</Tag></td>
              </tr>))}</tbody>
          </table></div>
        ) : <Empty title="No buy boxes yet">Buyers' criteria appear here once they are stated, confirmed in the Deal Room, or inferred from purchases.</Empty>}
      </section>
    </EvoApp>
  )
}
