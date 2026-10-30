/* OLP Land Partners: the workspace home for land deals and the partners who
 * buy them. Everything here is read from the signed-in workspace's own
 * records through the existing, tenant-scoped APIs:
 *   - land and farm opportunities  -> /wholesale/evosense/opportunity-radar (markets "land", "farm")
 *   - land buyers and partners     -> /wholesale/buyer-intel/buyers (segment "land")
 * Nothing is saved, sent, bought or invented. Empty is shown as empty.
 * Same Wholesale access as the Radar: no new data source, no new permission. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import photoLand from '../ds/photos/land-field.jpg'
import photoFarm from '../ds/photos/farm-barn.jpg'
import photoBuyers from '../ds/photos/capital.jpg'
import photoDeal from '../ds/photos/contract.jpg'
import '../evosense/OpportunityRadar.css'
import './landPartners.css'
import { LandDealTools } from './landTools'

const RADAR = '/wholesale/evosense/opportunity-radar?'
const BUYERS = '/wholesale/buyer-intel/buyers?'

function money(v) {
  if (typeof v !== 'number') return null
  return v >= 1e6 ? '$' + (v / 1e6).toFixed(v % 1e6 ? 1 : 0) + 'M' : v >= 1e3 ? '$' + Math.round(v / 1e3) + 'K' : '$' + v
}
function range(lo, hi, f) {
  const a = f(lo), b = f(hi)
  return a && b ? `${a}–${b}` : a ? `${a}+` : b ? `up to ${b}` : null
}
const acres = (v) => (typeof v === 'number' ? `${v.toLocaleString()} ac` : null)
function place(p) {
  const c = [p.city, p.state].filter(Boolean).join(', ')
  return [c || null, p.county ? `${p.county} County` : null].filter(Boolean).join(' · ') || 'Location not verified'
}
const label = (p) => p.address || (p.parcel_apn ? `Parcel ${p.parcel_apn}` : 'Address not verified')
const kindOf = (p) => (p.property_category === 'agricultural' ? 'Farm & Ranch' : p.property_category === 'land' ? 'Land' : 'Type needs review')

function useLoad(url) {
  const [state, setState] = useState({ data: null, error: '', loading: true })
  useEffect(() => {
    let alive = true
    setState({ data: null, error: '', loading: true })
    api.get(url)
      .then((d) => { if (alive) setState({ data: d, error: '', loading: false }) })
      .catch((e) => { if (alive) setState({ data: null, error: e?.message || 'Could not load.', loading: false }) })
    return () => { alive = false }
  }, [url])
  return state
}

function Kpi({ n, label: l, help, tone, loading }) {
  return (
    <article className="bbr-kpi lp-kpi">
      <div className="bbr-kpi__txt">
        <div className={'bbr-kpi__num ' + (tone || '')}>{loading ? '…' : n}</div>
        <div className="bbr-kpi__label">{l}</div>
        <div className="bbr-kpi__help">{help}</div>
      </div>
    </article>
  )
}

export default function LandPartners() {
  const land = useLoad(RADAR + new URLSearchParams({ market: 'land', sort: 'score', limit: '5' }))
  const farm = useLoad(RADAR + new URLSearchParams({ market: 'farm', limit: '1' }))
  const buyers = useLoad(BUYERS + new URLSearchParams({ segment: 'land', limit: '6' }))
  const farmBuyers = useLoad(BUYERS + new URLSearchParams({ segment: 'land', sub: 'agricultural', limit: '1' }))

  const ls = land.data?.summary
  const items = land.data?.items || []
  const people = buyers.data?.buyers || []

  return (
    <main className="bbr bbr--olp lp" data-product="olp">
      <section className="bbr-hero">
        <img className="bbr-hero__photo" src={photoLand} alt="" aria-hidden="true" decoding="async" />
        <div className="bbr-hero__text">
          <div className="bbr-eyebrow">OLP · LAND PARTNERS</div>
          <h1>Land <span className="bbr-gold">Partners</span></h1>
          <p>Land deals and the partners who buy them. Find land and acreage opportunities in your records, see which land buyers fit, and move the right deals forward together.</p>
        </div>
        <div className="bbr-hero__side"><div>LAND DEALS</div><span>Find → Match → Partner → Close</span></div>
      </section>

      <div className="bbr-kpis">
        <Kpi n={(ls?.total ?? 0).toLocaleString()} label="Land opportunities" help="Land, farm and ranch records in your workspace" loading={land.loading} />
        <Kpi n={(farm.data?.total ?? 0).toLocaleString()} label="Farm & ranch" help="Agricultural acreage on record" tone="gold" loading={farm.loading} />
        <Kpi n={(buyers.data?.total ?? 0).toLocaleString()} label="Land buyers & partners" help="Buy boxes asking for land" tone="green" loading={buyers.loading} />
        <Kpi n={(farmBuyers.data?.total ?? 0).toLocaleString()} label="Agricultural buyers" help="Buy boxes asking for farmland" loading={farmBuyers.loading} />
      </div>

      <LandDealTools />

      <div className="lp-grid">
        <section className="lp-panel" aria-labelledby="lp-opps">
          <header className="lp-panel__head">
            <h2 id="lp-opps">Top land opportunities</h2>
            <Link className="lp-link" to="/wholesale/evosense/overlooked?market=land">Open land radar →</Link>
          </header>
          {land.error ? <p className="lp-empty" role="alert">Land opportunities are unavailable. {land.error}</p> : null}
          {land.loading ? <p className="lp-empty" role="status">Checking your land records…</p> : null}
          {!land.loading && !land.error && !items.length ? (
            <div className="lp-empty">
              <b>No land records in your workspace yet.</b>
              <span>Land and acreage you add or import shows up here with its evidence and score. Nothing is invented.</span>
            </div>
          ) : null}
          <ul className="lp-list">
            {items.map((p) => (
              <li key={p.id} className="lp-row">
                <span className="lp-score" title="Opportunity Score">{p.existing_opportunity_score ?? '—'}</span>
                <span className="lp-row__main">
                  <b>{label(p)}</b>
                  <small>{place(p)} · {kindOf(p)}</small>
                  <small className="lp-row__why">{(p.why_review || []).slice(0, 2).map((r) => r.label).join(' · ') || 'Evidence needed'}</small>
                </span>
                <span className="lp-row__side">
                  <small>Buyer matches <b>{p.buyer_matches?.buyers_on_file ? p.buyer_matches.confirmed : '—'}</b></small>
                  <Link to={`/wholesale/evosense/property/${encodeURIComponent(p.id)}`}>Research →</Link>
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section className="lp-panel" aria-labelledby="lp-buyers">
          <header className="lp-panel__head">
            <h2 id="lp-buyers">Land buyers &amp; partners</h2>
            <Link className="lp-link" to="/wholesale/buyers">Buyer Intelligence →</Link>
          </header>
          {buyers.error ? <p className="lp-empty" role="alert">Land buyers are unavailable. {buyers.error}</p> : null}
          {buyers.loading ? <p className="lp-empty" role="status">Checking your buy boxes…</p> : null}
          {!buyers.loading && !buyers.error && !people.length ? (
            <div className="lp-empty">
              <b>No land buyers on file yet.</b>
              <span>Add a buy box that asks for land, acreage or farmland and the buyer appears here.</span>
              <Link to="/wholesale/buyers/buy-boxes">Open Buy Boxes →</Link>
            </div>
          ) : null}
          <ul className="lp-list">
            {people.map((b) => {
              const where = (b.geography?.states || []).slice(0, 4).join(', ')
              const subs = b.segments?.land?.subs || []
              const facts = [
                where || null,
                range(b.price?.min, b.price?.max, money),
                range(b.acres?.min, b.acres?.max, acres),
              ].filter(Boolean)
              return (
                <li key={b.buyer_id} className="lp-row">
                  <span className="lp-avatar" aria-hidden="true">{String(b.name || '?').trim().charAt(0).toUpperCase()}</span>
                  <span className="lp-row__main">
                    <b>{b.name}</b>
                    <small>{facts.join(' · ') || 'Criteria not complete yet'}</small>
                    {subs.length ? <small className="lp-row__why">{subs.slice(0, 3).map((x) => String(x).replaceAll('_', ' ')).join(' · ')}</small> : null}
                  </span>
                  <span className="lp-row__side">{b.headline_label ? <span className="lp-tag">{b.headline_label}</span> : null}</span>
                </li>
              )
            })}
          </ul>
        </section>
      </div>

      <nav className="lp-tiles" aria-label="Land Partners actions">
        <Link className="lp-tile" to="/wholesale/farm-ranch" style={{ backgroundImage: `url(${photoFarm})` }}>
          <span className="lp-tile__scrim" aria-hidden="true" /><b>Farm &amp; Ranch</b><small>Farmland, ranches and acreage</small></Link>
        <Link className="lp-tile" to="/wholesale/buyers/buy-boxes" style={{ backgroundImage: `url(${photoBuyers})` }}>
          <span className="lp-tile__scrim" aria-hidden="true" /><b>Buy Boxes</b><small>What each land buyer is looking for</small></Link>
        <Link className="lp-tile" to="/wholesale/deal-room" style={{ backgroundImage: `url(${photoDeal})` }}>
          <span className="lp-tile__scrim" aria-hidden="true" /><b>Deal Room</b><small>Share land deals with partners</small></Link>
        <Link className="lp-tile" to="/wholesale/funding" style={{ backgroundImage: `url(${photoLand})` }}>
          <span className="lp-tile__scrim" aria-hidden="true" /><b>Funding Partners</b><small>Capital for land and acreage</small></Link>
      </nav>

      <p className="bbr-footnote">Only records in your authorized workspace. The Opportunity Score is a research aid from existing evidence — not an appraisal, a listing, or a sign the owner will sell. Buyer and partner counts come from your own buy boxes. Confirm source records and licensing before acting.</p>
    </main>
  )
}
