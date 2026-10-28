/* OWNER DEAL ROOM - the dashboard, built to the approved full-page reference
 * (left half of the owner-approved comparison image):
 *
 *   welcome hero + 4 actions  ->  4 metric cards  ->  Investor Growth | Deal Room
 *   Status | Quick Actions  ->  Recent Investor Activity (4 tabs)  ->  Active
 *   Listings in Deal Room
 *
 * Every number and row is a real record from /wholesale/deal-room/dashboard and
 * /wholesale/deal-room/feed. The reference's example names, prices and growth are
 * never shown: a chart appears only when there is real history behind it.
 */
import { useEffect, useMemo, useState } from 'react'
import { api, fetchObjectUrl } from '../../../api/client'
import { Skeleton } from '../ds/ds'
import { DRS, cap, usd, words } from './shared'

const ICON = {
  invite: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM19 8v6M22 11h-6',
  add: 'M3 10l9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1zM12 12v6M9 15h6',
  portal: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  check: 'M20 6L9 17l-5-5',
  listing: 'M4 4h16v16H4zM4 9h16M9 9v11',
  star: 'M12 3l2.7 5.5 6 .9-4.4 4.2 1 6-5.3-2.8-5.3 2.8 1-6L3.3 9.4l6-.9z',
  msg: 'M4 5h16v11H8l-4 4z',
  upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
  manage: 'M4 6h16M4 12h16M4 18h10',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z',
  offer: 'M20 12V7H4v13h8M4 7l2-4h12l2 4M16 17l2 2 4-4',
  sign: 'M3 21l3-1 11-11-2-2L4 18zM14 7l2-2 2 2-2 2',
  bed: 'M3 18V7M3 13h18v5M7 13V9h6a3 3 0 0 1 3 3v1',
  bath: 'M4 12h16v3a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4zM6 12V6a2 2 0 0 1 4 0',
  area: 'M4 4h16v16H4zM9 4v16M4 9h16',
  units: 'M4 21V7l8-4 8 4v14M9 21v-6h6v6',
}
export function Ico({ n, size = 18 }) {
  return (
    <svg className="od-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON[n] || ICON.check} /></svg>
  )
}

export function ago(iso) {
  if (!iso) return '—'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) { const h = Math.floor(s / 3600); return `${h} hour${h === 1 ? '' : 's'} ago` }
  const d = Math.floor(s / 86400)
  return d < 30 ? `${d} day${d === 1 ? '' : 's'} ago` : new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function ListingPhoto({ listingId, fileId, className = '' }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    let u = null; let alive = true
    if (listingId && fileId) fetchObjectUrl(`${DRS}/listings/${listingId}/photos/${fileId}`).then((x) => { u = x; if (alive) setSrc(x) }).catch(() => {})
    return () => { alive = false; if (u) URL.revokeObjectURL(u) }
  }, [listingId, fileId])
  return <div className={`od-photo ${className}`}>{src ? <img src={src} alt="" /> : <Ico n="add" size={28} />}</div>
}

const initials = (n) => (n || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((x) => x[0]).join('').toUpperCase()
const CAT = { residential: 'Single family', single_family: 'Single family', multifamily: 'Multifamily', commercial: 'Commercial',
  land: 'Land', industrial: 'Industrial', mixed_use: 'Mixed use' }
const AUD = { matched: 'Matched investors', all: 'All investors', selected: 'Selected investors' }

export default function OwnerDashboard({ practice, bump, fail, go, onInvite, onAdd, onPreview, market }) {
  const [d, setD] = useState(null)
  const [tab, setTab] = useState('activity')
  const [rows, setRows] = useState(null)
  useEffect(() => { api.get(`${DRS}/dashboard?include_test=${practice}`).then(setD).catch(fail) }, [practice, bump, fail])
  useEffect(() => {
    setRows(null)
    api.get(`${DRS}/feed?kind=${tab}&include_test=${practice}&limit=8`).then((x) => setRows(x.rows)).catch(fail)
  }, [tab, practice, bump, fail])
  const listings = useMemo(() => ((d && d.listings) || []).filter((l) => !market || (l.county || '').toLowerCase() === market.toLowerCase()), [d, market])
  if (!d) return <Skeleton rows={8} />
  const m = d.metrics
  const open = (r) => {
    const o = r.open || {}
    if (o.view === 'messages' && o.thread) go('messages', { thread: o.thread })
    else if (o.view === 'investors' && o.buyer_id) go('investors', { buyer: o.buyer_id })
    else if (o.view) go(o.view)
  }
  return (
    <div className="od">
      {/* 1. welcome hero */}
      <section className="od-hero" aria-labelledby="od-welcome">
        {d.hero_photo ? <ListingPhoto listingId={d.hero_photo.listing_id} fileId={d.hero_photo.file_id} className="od-hero__photo" /> : <div className="od-hero__photo od-hero__photo--plain" />}
        <div className="od-hero__veil" />
        <div className="od-hero__body">
          <h1 id="od-welcome" className="od-hero__h">Welcome Back, <span>{d.owner_first_name || 'Owner'}</span></h1>
          <p className="od-hero__p">Manage your investor network, publish investment opportunities and track deal activity.</p>
          <div className="od-hero__btns">
            <button type="button" className="od-btn od-btn--primary" onClick={onInvite}><Ico n="invite" />Invite Investor</button>
            <button type="button" className="od-btn od-btn--white" onClick={onAdd}><Ico n="add" />Add Deal to Room</button>
            <a className="od-btn od-btn--white" href="/investors/preview" target="_blank" rel="noopener noreferrer"><Ico n="portal" />View Investor Portal</a>
            <button type="button" className="od-btn od-btn--white od-btn--end" onClick={() => go('settings')}><Ico n="settings" />Settings</button>
          </div>
        </div>
      </section>

      {/* 2. metrics */}
      <div className="od-metrics" role="list">
        {[['users', 'Total Investors', m.total_investors, `${m.new_investors_30d} joined in the last 30 days`, 'investors'],
          ['invite', 'Active Investors', m.active_investors, 'Verified identity and active access', 'investors'],
          ['listing', 'Active Listings', m.active_listings, 'In Deal Room', 'listings'],
          ['star', 'Exclusive Reviews', m.exclusive_reviews, m.expiring ? `${m.expiring} expiring within 24h` : `${m.pending_reviews} waiting for a decision`, 'holds']].map(([ic, l, v, s, to]) => (
          <button type="button" role="listitem" key={l} className="od-metric" onClick={() => go(to)}>
            <span className="od-metric__ic"><Ico n={ic} size={22} /></span>
            <span className="od-metric__t"><small>{l}</small><b>{v}</b><span className={l === 'Exclusive Reviews' && m.expiring ? 'is-warn' : ''}>{s}</span></span>
          </button>
        ))}
      </div>

      {/* 3. growth | status | quick actions */}
      <div className="od-mid">
        <section className="od-card" aria-labelledby="od-growth-h">
          <div className="od-card__head"><h2 id="od-growth-h" className="od-h2">Investor Growth</h2><span className="od-select">6 Months</span></div>
          <GrowthChart growth={d.growth} total={m.total_investors} />
        </section>
        <section className="od-card" aria-labelledby="od-status-h">
          <div className="od-card__head"><h2 id="od-status-h" className="od-h2">Deal Room Status</h2>
            <span className={`od-pill od-pill--${d.status === 'active' ? 'ok' : 'warn'}`}>{d.status === 'active' ? 'Active' : d.status === 'paused' ? 'Paused' : 'Setup'}</span></div>
          <ul className="od-status">
            {d.status_items.map((s) => (
              <li key={s.key}><span className={`od-status__dot is-${s.state}`} aria-hidden="true">{s.state === 'ok' ? <Ico n="check" size={13} /> : s.state === 'warn' ? '!' : '·'}</span>
                <span className="od-status__l">{s.label}</span><span className={`od-status__v is-${s.state}`}>{s.value}</span></li>
            ))}
          </ul>
          {d.issues.length ? <p className="od-issues">{d.issues.length} setup item(s) still open.</p> : null}
          <button type="button" className="od-btn od-btn--ghost od-btn--block" onClick={() => go('settings')}>View Setup Details</button>
        </section>
        <section className="od-card" aria-labelledby="od-qa-h">
          <div className="od-card__head"><h2 id="od-qa-h" className="od-h2">Quick Actions</h2></div>
          <ul className="od-qa">
            {[['invite', 'Invite New Investor', onInvite], ['add', 'Add Deal to Room', onAdd], ['manage', 'Manage Listings', () => go('listings')],
              ['star', 'Review Requests', () => go('holds')], ['msg', 'Send Message', () => go('messages')], ['upload', 'Upload Document', () => go('documents')]].map(([ic, l, fn]) => (
              <li key={l}><button type="button" onClick={fn}><span className="od-qa__ic"><Ico n={ic} size={16} /></span>{l}</button></li>
            ))}
          </ul>
        </section>
      </div>

      {/* 4. recent investor activity */}
      <section className="od-card od-activity" aria-labelledby="od-act-h">
        <h2 id="od-act-h" className="evo-sr">Recent investor activity</h2>
        <div className="od-tabs" role="tablist" aria-label="Activity">
          {[['activity', 'Recent Investor Activity'], ['holds', 'Exclusive Review Requests'], ['signups', 'New Investor Signups'], ['messages', 'Messages']].map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? 'is-on' : ''} onClick={() => setTab(k)}>{l}</button>
          ))}
          <button type="button" className="od-link od-tabs__all" onClick={() => go({ activity: 'activity', holds: 'holds', signups: 'investors', messages: 'messages' }[tab])}>View All</button>
        </div>
        {!rows ? <Skeleton rows={4} /> : rows.length ? (
          <div className="od-table-wrap">
            <table className="od-table">
              <thead><tr><th scope="col">Investor</th><th scope="col">Activity</th><th scope="col">Related Deal</th><th scope="col">Date</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} tabIndex={0} onClick={() => open(r)} onKeyDown={(e) => { if (e.key === 'Enter') open(r) }}>
                    <td data-label="Investor"><span className="od-who"><span className="od-av" aria-hidden="true">{initials(r.investor)}</span>
                      <span><b>{r.investor}</b>{r.company ? <small>{r.company}</small> : null}</span></span></td>
                    <td data-label="Activity" className="od-table__act">{r.activity}</td>
                    <td data-label="Related deal">{r.deal ? <span className="od-deal">
                      {r.deal.listing_id && r.deal.photo ? <ListingPhoto listingId={r.deal.listing_id} fileId={r.deal.photo} className="od-deal__img" /> : <span className="od-deal__img od-photo"><Ico n="add" size={14} /></span>}
                      <span><b>{r.deal.address || 'Deal'}</b><small>{r.deal.place}</small></span></span> : <span className="od-muted">—</span>}</td>
                    <td data-label="Date" className="od-muted">{ago(r.at)}</td>
                    <td data-label="Status"><span className={`od-pill od-pill--${r.tone}`}>{r.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="od-empty">{{ activity: 'No investor activity yet. Invite an investor to get started.', holds: 'No exclusive review requests yet.',
          signups: 'No investors have joined yet.', messages: 'No investor conversations yet.' }[tab]}</p>}
      </section>

      {/* 5. active listings */}
      <section className="od-card" aria-labelledby="od-list-h">
        <div className="od-card__head"><h2 id="od-list-h" className="od-h2">Active Listings in Deal Room</h2>
          <button type="button" className="od-link" onClick={() => go('listings')}>View All</button></div>
        {listings.length ? (
          <div className="od-listings">
            {listings.map((l) => {
              const badge = l.hold ? ['Exclusive Review', 'orange'] : l.is_test ? ['Practice', 'amber'] : (l.published_at && Date.now() - new Date(l.published_at).getTime() < 7 * 864e5) ? ['New', 'blue'] : null
              const f = l.facts || {}
              return (
                <article key={l.id} className="od-lcard">
                  <div className="od-lcard__media"><ListingPhoto listingId={l.id} fileId={l.cover_photo} />
                    {badge ? <span className={`od-badge od-badge--${badge[1]}`}>{badge[0]}</span> : null}</div>
                  <div className="od-lcard__body">
                    <h3>{l.address || 'Address on file'}</h3>
                    <p className="od-muted">{[l.city, l.state_code, l.zip_code].filter(Boolean).join(', ')}</p>
                    <div className="od-price">{l.asking_price ? usd(l.asking_price) : 'Price not published'}</div>
                    <ul className="od-facts">
                      {f.unit_count ? <li><Ico n="units" size={14} />{f.unit_count} units</li> : null}
                      {f.bedrooms ? <li><Ico n="bed" size={14} />{f.bedrooms} bd</li> : null}
                      {f.bathrooms ? <li><Ico n="bath" size={14} />{f.bathrooms} ba</li> : null}
                      {f.square_feet ? <li><Ico n="area" size={14} />{Number(f.square_feet).toLocaleString('en-US')} sq ft</li> : null}
                    </ul>
                    <div className="od-tags"><span className="od-tag">{CAT[l.category] || cap(words(l.category || 'Property'))}</span><span className="od-tag od-tag--blue">{AUD[l.audience]}</span></div>
                    <p className={`od-state is-${l.hold ? 'warn' : 'ok'}`}>● {l.hold ? `Under exclusive review · ${l.hold.buyer_name}` : 'Published'}
                      {l.hold_requests ? ` · ${l.hold_requests} request(s)` : ''}</p>
                    <p className="od-muted od-small">{l.expires_at ? `Open until ${new Date(l.expires_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : 'No closing date'}</p>
                    <button type="button" className="od-btn od-btn--primary od-btn--block od-btn--sm" onClick={() => onPreview(l)}>View Details</button>
                  </div>
                </article>
              )
            })}
          </div>
        ) : (
          <div className="od-emptybox"><p>No deals published yet.</p>
            <button type="button" className="od-btn od-btn--primary" onClick={onAdd}><Ico n="add" />Add Deal</button></div>
        )}
      </section>
    </div>
  )
}

function GrowthChart({ growth, total }) {
  const s = growth.series
  if (!growth.enough) {
    return (
      <div className="od-chart-empty">
        <b>{total}</b><span>investor{total === 1 ? '' : 's'} so far</span>
        <p>A growth chart appears once investors have joined in at least two different months - no estimated history is shown.</p>
      </div>
    )
  }
  const W = 420; const H = 170; const pad = 26
  const max = Math.max(1, ...s.map((x) => x.total))
  const pts = s.map((x, i) => [pad + (i * (W - pad * 2)) / (s.length - 1), H - pad - ((H - pad * 2) * x.total) / max])
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
  const area = line + ` L ${pts[pts.length - 1][0]} ${H - pad} L ${pts[0][0]} ${H - pad} Z`
  const last = s[s.length - 1]
  return (
    <figure className="od-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Investors over 6 months: ${s.map((x) => `${x.label} ${x.total}`).join(', ')}`}>
        <defs><linearGradient id="odg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#2563eb" stopOpacity=".35" /><stop offset="1" stopColor="#2563eb" stopOpacity="0" /></linearGradient></defs>
        {[0, 1, 2, 3].map((i) => <line key={i} x1={pad} x2={W - pad} y1={pad + (i * (H - pad * 2)) / 3} y2={pad + (i * (H - pad * 2)) / 3} stroke="#e5ebf5" />)}
        <path d={area} fill="url(#odg)" /><path d={line} fill="none" stroke="#2563eb" strokeWidth="2.5" />
        {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="3.5" fill="#fff" stroke="#2563eb" strokeWidth="2" />)}
        {s.map((x, i) => <text key={i} x={pts[i][0]} y={H - 6} textAnchor="middle" fontSize="11" fill="#64748b">{x.label}</text>)}
      </svg>
      <figcaption className="od-chart__tip"><b>{last.total}</b> investors · {last.new} new this month</figcaption>
    </figure>
  )
}
