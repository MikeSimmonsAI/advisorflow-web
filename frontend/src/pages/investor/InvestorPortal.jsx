/* PRIVATE BUYER DEAL ROOM - the investor portal (/investors).
 *
 * Built to the owner-approved combined design (EvoSys Pro Investor Portal):
 * premium navy sidebar, rich blue accents, bright workspace, photo hero with a
 * personal welcome, clean metric tiles, property cards, and a right rail with
 * the AI concierge, messages & support and the investor's criteria.
 *
 * EVERY NUMBER IS REAL. The mockup's illustrative portfolio value, ROI and cash
 * flow have no source in this product, so they are not shown; the tiles carry
 * counts and figures from the investor's own records and the published deals.
 *
 * The server decides what an investor may see (/investor/*, fenced in
 * app/deps.py). /investors/preview is the owner's read-only look at the same
 * design, fed by /wholesale/deal-room/portal-preview with the staff session.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api as staffApi, fetchObjectUrl } from '../../api/client'
import { engine } from '../wholesale/engine'
import { cap, fileSize, inv, investorToken, objectUrl, setInvestorToken, usd, when, words } from './investorApi'
import './investor.css'

// ── small pieces ───────────────────────────────────────────────────────────
function useLoad(path, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [n, setN] = useState(0)
  useEffect(() => {
    let alive = true
    if (!path) return undefined
    setError(null)
    inv.get(path).then((d) => { if (alive) setData(d) }).catch((e) => { if (alive) setError(e.message) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, n, ...deps])
  return [data, error, useCallback(() => setN((x) => x + 1), [])]
}

function Note({ kind = 'info', children }) {
  if (!children) return null
  return <div className={`ir-note ir-note--${kind}`} role={kind === 'error' ? 'alert' : 'status'}>{children}</div>
}

function Loading({ label = 'Loading' }) {
  return <div className="ir-loading" role="status" aria-live="polite"><span className="ir-spin" aria-hidden="true" />{label}…</div>
}

const I = {
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  deals: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  criteria: 'M4 6h16M7 12h10M10 18h4',
  review: 'M12 3l2.7 5.5 6 .9-4.4 4.2 1 6-5.3-2.8-5.3 2.8 1-6L3.3 9.4l6-.9z',
  offers: 'M20 12V7H4v13h8M4 7l2-4h12l2 4M16 17l2 2 4-4',
  msg: 'M4 5h16v11H8l-4 4z',
  docs: 'M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6',
  account: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5',
  pin: 'M12 21s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12zM12 11a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  heart: 'M12 20s-7-4.5-9-9a5 5 0 0 1 9-3 5 5 0 0 1 9 3c-2 4.5-9 9-9 9z',
  send: 'M4 12l16-8-6 16-2-7z',
  spark: 'M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z',
  bed: 'M3 18V7M3 13h18v5M7 13V9h6a3 3 0 0 1 3 3v1',
  bath: 'M4 12h16v3a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4zM6 12V6a2 2 0 0 1 4 0',
  area: 'M4 4h16v16H4zM9 4v16M4 9h16',
  units: 'M4 21V7l8-4 8 4v14M9 21v-6h6v6',
}
function Ico({ n, size = 18 }) {
  return (
    <svg className="ir-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={I[n]} /></svg>
  )
}

/** Photo through the authorized route (an <img src> cannot carry the token). */
function Photo({ listingId, fileId, alt, className = '', preview }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    let url = null
    let alive = true
    if (listingId && fileId) {
      const p = preview ? fetchObjectUrl(`/wholesale/deal-room/listings/${listingId}/photos/${fileId}`)
        : objectUrl(`/investor/opportunities/${listingId}/photos/${fileId}`)
      p.then((u) => { url = u; if (alive) setSrc(u); else URL.revokeObjectURL(u) }).catch(() => {})
    }
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [listingId, fileId, preview])
  return (
    <div className={`ir-photo ${className}`}>
      {src ? <img src={src} alt={alt || ''} /> : <span className="ir-photo__ph" aria-hidden="true"><Ico n="home" size={30} /></span>}
    </div>
  )
}

function useCountdown(endsAt) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!endsAt) return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [endsAt])
  if (!endsAt) return null
  const left = Math.max(0, Math.floor((new Date(endsAt).getTime() - now) / 1000))
  const d = Math.floor(left / 86400); const h = Math.floor((left % 86400) / 3600)
  const m = Math.floor((left % 3600) / 60); const s = left % 60
  return { left, text: (d ? `${d}d ` : '') + `${String(h).padStart(2, '0')}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s` }
}

function Countdown({ endsAt, compact }) {
  const c = useCountdown(endsAt)
  if (!c) return null
  return (
    <span className={`ir-count${compact ? ' ir-count--sm' : ''}`} role="timer"
          aria-label={`Exclusive review ends ${new Date(endsAt).toLocaleString()}`}>{c.left > 0 ? c.text : 'Ended'}</span>
  )
}

const addr1 = (a) => (a && a.line1) || 'Address on request'
const addr2 = (a) => [a && a.city, a && a.state, a && a.zip_code].filter(Boolean).join(', ')
const CAT = { residential: 'Single family', single_family: 'Single family', multifamily: 'Multifamily', commercial: 'Commercial',
  land: 'Land', industrial: 'Industrial', mixed_use: 'Mixed use', agricultural: 'Land', portfolio: 'Portfolio' }
const catOf = (o) => CAT[o.category] || CAT[(o.address || {}).property_type] || cap(o.category || (o.address || {}).property_type || 'Property')
const isNew = (o) => o.published_at && (Date.now() - new Date(o.published_at).getTime()) < 7 * 86400000

function FactsRow({ facts }) {
  const f = facts || {}
  const items = []
  if (f.unit_count) items.push(['units', `${f.unit_count} units`])
  if (f.bedrooms) items.push(['bed', `${Number(f.bedrooms)} bd`])
  if (f.bathrooms) items.push(['bath', `${Number(f.bathrooms)} ba`])
  if (f.square_feet) items.push(['area', `${Number(f.square_feet).toLocaleString('en-US')} sq ft`])
  if (f.acreage) items.push(['area', `${f.acreage} acres`])
  if (!items.length) return null
  return <ul className="ir-facts">{items.map(([n, t]) => <li key={t}><Ico n={n} size={15} />{t}</li>)}</ul>
}

const WHY = { geography: 'location', price: 'price', property_type: 'property type', strategy: 'strategy', beds: 'bedrooms',
  baths: 'bathrooms', sqft: 'size', year_built: 'year built', rehab: 'rehab level', spread: 'deal spread', acres: 'acreage',
  units: 'units', building_sqft: 'building size', asset_class: 'asset class' }
const matchWhy = (m) => {
  if (!m || m.score === null || m.score === undefined) return 'Not scored against your criteria yet'
  const w = (m.why || []).map((k) => WHY[k] || words(k))
  return w.length ? `Matches your ${listWords(w)}` : 'Scored against your saved criteria'
}

function OppCard({ o, onSave, preview }) {
  const nav = useNavigate()
  const yourHold = o.hold && o.hold.yours && o.hold.status === 'active'
  const badge = yourHold ? ['Exclusive review', 'gold'] : o.is_test ? ['Practice', 'practice'] : isNew(o) ? ['New', 'blue'] : null
  const base = preview ? null : `/investors/deals/${o.id}`
  const score = o.match && o.match.score !== null && o.match.score !== undefined ? o.match.score : null
  const avail = yourHold ? null : o.hold_requested ? 'Review requested' : o.expires_at
    ? `Open until ${new Date(o.expires_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : 'Available'
  return (
    <article className="ir-card">
      <div className="ir-card__media">
        <Photo listingId={o.id} fileId={o.cover_photo} alt={`Photo of ${addr1(o.address)}`} preview={preview} />
        {badge ? <span className={`ir-badge-tag ir-badge-tag--${badge[1]}`}>{badge[0]}</span> : null}
        {!preview ? (
          <button type="button" className={`ir-heart${o.saved ? ' is-on' : ''}`} aria-pressed={!!o.saved}
                  aria-label={o.saved ? 'Remove from saved' : 'Save this property'} onClick={() => onSave && onSave(o)}><Ico n="heart" size={17} /></button>
        ) : null}
      </div>
      <div className="ir-card__body">
        <h3 className="ir-card__addr">{addr1(o.address)}</h3>
        <p className="ir-card__place">{addr2(o.address)}</p>
        <div className="ir-card__price">{o.asking_price ? usd(o.asking_price) : 'Price on request'}</div>
        <FactsRow facts={o.facts} />
        <div className="ir-tags"><span className="ir-tag">{catOf(o)}</span>
          {o.arv ? <span className="ir-tag ir-tag--green" title="After-repair value published by the team">ARV {usdK(o.arv)}</span> : null}
          {o.you && o.you.status ? <span className="ir-tag ir-tag--blue">{cap(o.you.status)}</span> : null}</div>
        <div className="ir-card__match">
          <span className={`ir-match${score !== null && score >= 80 ? ' is-hi' : score !== null && score < 60 ? ' is-lo' : ''}`} title={matchWhy(o.match)}>
            <i aria-hidden="true" />Match: {score !== null ? `${score}%` : '—'}</span>
          <span className="ir-card__avail">{yourHold ? <>Review ends <Countdown endsAt={o.hold.ends_at} compact /></> : avail}</span>
        </div>
        <p className="ir-card__why">{matchWhy(o.match)}</p>
        {base ? (
          <div className="ir-card__btns">
            <Link to={base} className="ir-btn ir-btn--primary ir-btn--sm">View Details</Link>
            <button type="button" className="ir-btn ir-btn--outline ir-btn--sm" onClick={() => nav(`${base}?offer=1`)}>Submit Offer</button>
          </div>
        ) : null}
      </div>
    </article>
  )
}

// ── public: invitation and sign in ─────────────────────────────────────────
function Brandmark({ name, light }) {
  return (
    <div className={`ir-brand${light ? ' ir-brand--light' : ''}`}>
      <span className="ir-brand__mark" aria-hidden="true"><svg viewBox="0 0 32 32" width="30" height="30"><rect x="3" y="3" width="26" height="26" rx="7" fill="#2563eb" /><path d="M10 11h12M10 16h9M10 21h12" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" /></svg></span>
      <span><b>{name || 'Deal Room'}</b><small>Investor portal</small></span>
    </div>
  )
}

function GateArt() {
  return <div className="ir-gate__art" aria-hidden="true"><div className="ir-gate__glow" /><p className="ir-gate__quote">We find. You grow.</p></div>
}

function Invite() {
  const { token } = useParams()
  const nav = useNavigate()
  const [p, setP] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [f, setF] = useState({ full_name: '', kind: 'individual', password: '', confirm: '' })
  useEffect(() => {
    inv.pub.get(`/investor/invite/${encodeURIComponent(token)}`).then((d) => {
      setP(d); setF((x) => ({ ...x, full_name: d.name || '' }))
    }).catch((e) => setErr(e.status === 404 || e.status === 410
      ? 'This invitation link is no longer valid. It may have been used, revoked or expired - ask your contact for a new one.'
      : e.message))
  }, [token])
  const register = p && p.next === 'register'
  const submit = async (e) => {
    e.preventDefault()
    setErr(null)
    if (register && f.password !== f.confirm) { setErr('The two passwords do not match.'); return }
    setBusy(true)
    try {
      const r = await inv.pub.post(`/investor/invite/${encodeURIComponent(token)}/accept`,
        register ? { password: f.password, full_name: f.full_name, kind: f.kind } : { password: f.password })
      setInvestorToken(r.access_token)
      nav('/investors', { replace: true })
    } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="ir-gate">
      <GateArt />
      <main className="ir-gate__panel">
        <Brandmark name={p && p.organization} />
        {!p && !err ? <Loading label="Checking your invitation" /> : null}
        {p ? (
          <form onSubmit={submit} className="ir-form">
            <p className="ir-eyebrow">Private invitation</p>
            <h1 className="ir-h1">{p.name ? <>Welcome, <span className="ir-accent">{p.name.split(' ')[0]}</span>.</> : 'Welcome.'}</h1>
            <p className="ir-lead">{p.organization} has invited you to a private Deal Room: off-market opportunities
              matched to what you buy, with a direct line to the team.</p>
            <p className="ir-muted ir-small">Invitation for {p.email_masked}. The link works once.</p>
            {register ? (
              <>
                <label className="ir-field"><span>Your full legal name</span>
                  <input required autoComplete="name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></label>
                <fieldset className="ir-field ir-seg">
                  <legend>I buy as</legend>
                  {[['individual', 'An individual'], ['business', 'A company or LLC']].map(([k, l]) => (
                    <label key={k} className={f.kind === k ? 'is-on' : ''}>
                      <input type="radio" name="kind" value={k} checked={f.kind === k} onChange={() => setF({ ...f, kind: k })} />{l}
                    </label>
                  ))}
                </fieldset>
                <label className="ir-field"><span>Create a password</span>
                  <input required type="password" minLength={10} autoComplete="new-password" value={f.password}
                         onChange={(e) => setF({ ...f, password: e.target.value })} />
                  <small>At least 10 characters.</small></label>
                <label className="ir-field"><span>Confirm password</span>
                  <input required type="password" autoComplete="new-password" value={f.confirm}
                         onChange={(e) => setF({ ...f, confirm: e.target.value })} /></label>
              </>
            ) : (
              <label className="ir-field"><span>You already have a Deal Room login - enter its password to add this invitation</span>
                <input required type="password" autoComplete="current-password" value={f.password}
                       onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
            )}
            <Note kind="error">{err}</Note>
            <button className="ir-btn ir-btn--primary ir-btn--block" disabled={busy}>{busy ? 'One moment…' : register ? 'Enter the Deal Room' : 'Accept invitation'}</button>
          </form>
        ) : <Note kind="error">{err}</Note>}
      </main>
    </div>
  )
}

function Login() {
  const nav = useNavigate()
  const loc = useLocation()
  const [f, setF] = useState({ email: '', password: '' })
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const next = new URLSearchParams(loc.search).get('next')
  const safeNext = next && next.startsWith('/investors') && !next.startsWith('//') ? next : '/investors'
  const submit = async (e) => {
    e.preventDefault(); setErr(null); setBusy(true)
    try {
      const r = await inv.pub.post('/investor/auth/login', f)
      setInvestorToken(r.access_token)
      nav(safeNext, { replace: true })
    } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="ir-gate">
      <GateArt />
      <main className="ir-gate__panel">
        <Brandmark />
        <form onSubmit={submit} className="ir-form">
          <p className="ir-eyebrow">Investors</p>
          <h1 className="ir-h1">Sign in to your Deal Room</h1>
          <label className="ir-field"><span>Email</span>
            <input required type="email" autoComplete="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label className="ir-field"><span>Password</span>
            <input required type="password" autoComplete="current-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
          <Note kind="error">{err}</Note>
          <button className="ir-btn ir-btn--primary ir-btn--block" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
          <p className="ir-muted ir-small">Access is by invitation. If you need a new link, contact the team that invited you.</p>
        </form>
      </main>
    </div>
  )
}

// ── the signed-in shell ────────────────────────────────────────────────────
function navItems(c) {
  return [
    { to: '/investors', label: 'Dashboard', icon: 'home', end: true },
    { to: '/investors/deals', label: 'Available Deals', short: 'Deals', icon: 'deals' },
    { to: '/investors/criteria', label: 'My Criteria', short: 'Criteria', icon: 'criteria' },
    { to: '/investors/reviews', label: 'Exclusive Reviews', short: 'Reviews', icon: 'review', n: c && c.in_review },
    { to: '/investors/offers', label: 'My Offers', short: 'Offers', icon: 'offers', n: c && c.responses },
    { to: '/investors/messages', label: 'Messages', icon: 'msg', n: c && c.unread, alert: true },
    { to: '/investors/documents', label: 'Documents', short: 'Docs', icon: 'docs', n: c && c.to_sign, alert: true },
    { to: '/investors/account', label: 'Account', icon: 'account' },
  ]
}
const PHONE_NAV = ['/investors', '/investors/deals', '/investors/messages', '/investors/documents', '/investors/account']

function Sidebar({ room, counts, me, preview }) {
  const items = navItems(counts)
  const nav = useNavigate()
  const name = me && me.account ? (me.account.preferred_name || me.account.legal_name) : (preview ? 'Investor preview' : '')
  const help = async () => {
    if (preview) return
    try { const t = await inv.post('/investor/threads', { kind: 'relationship' }); nav(`/investors/messages/${t.id}`) } catch { nav('/investors/messages') }
  }
  return (
    <aside className="ir-side" aria-label="Deal Room">
      <Brandmark name={room && (room.brand_name || room.name)} light />
      <nav className="ir-nav" aria-label="Deal Room sections">
        {items.map((n) => (
          <NavLink key={n.to} to={preview ? '/investors/preview' : n.to} end={n.end}
                   className={({ isActive }) => `ir-nav__a${(preview ? n.to === '/investors' : isActive) ? ' is-on' : ''}${PHONE_NAV.includes(n.to) ? ' is-phone' : ''}`}
                   onClick={preview ? (e) => e.preventDefault() : undefined}>
            <Ico n={n.icon} /><span className="ir-nav__l"><span className="ir-long">{n.label}</span><span className="ir-short">{n.short || n.label}</span></span>
            {n.n ? <span className={`ir-nbadge${n.alert ? ' is-alert' : ''}`} aria-label={`${n.n}`}>{n.n}</span> : null}
          </NavLink>
        ))}
      </nav>
      <div className="ir-side__foot">
        <button type="button" className="ir-help" onClick={help} disabled={preview}>
          <span className="ir-help__ic" aria-hidden="true">?</span>
          <span><b>Need Help?</b><small>Contact Support</small></span>
        </button>
        <div className="ir-me">
          <span className="ir-me__av" aria-hidden="true">{(name || '?').split(/\s+/).slice(0, 2).map((x) => x[0]).join('').toUpperCase()}</span>
          <span className="ir-me__t"><b>{name || '…'}</b><small>{me && me.account ? (me.account.company_legal_name || 'Individual investor') : (room && room.name) || ''}</small></span>
        </div>
      </div>
    </aside>
  )
}
const usdK = (v) => (v >= 1e6 ? '$' + (v / 1e6).toFixed(v % 1e6 ? 2 : 0) + 'M' : v >= 1e3 ? '$' + Math.round(v / 1e3) + 'K' : usd(v))
const listWords = (a) => (a.length <= 1 ? a.join('') : a.slice(0, -1).join(', ') + ' & ' + a[a.length - 1])

function Topbar({ me, counts, room, q, setQ, market, setMarket, onSignOut, preview }) {
  const nav = useNavigate()
  const name = me && me.account ? (me.account.preferred_name || me.account.legal_name) : (preview ? 'Investor preview' : '')
  const [open, setOpen] = useState(false)
  return (
    <header className="ir-top">
      <form className="ir-searchbox" role="search" onSubmit={(e) => { e.preventDefault(); if (!preview) nav(`/investors/deals?q=${encodeURIComponent(q)}`) }}>
        <Ico n="search" /><label className="ir-sr" htmlFor="ir-q">Search deals</label>
        <input id="ir-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search deals, addresses, or cities…" />
      </form>
      <label className="ir-market"><Ico n="pin" /><span className="ir-sr">Market</span>
        <select value={market} onChange={(e) => setMarket(e.target.value)}>
          <option value="">{room && room.markets && room.markets.length ? 'Dallas - Fort Worth' : 'All markets'}</option>
          {((room && room.markets) || []).map((m) => <option key={m} value={m}>{m} County</option>)}
        </select></label>
      <Link to={preview ? '/investors/preview' : '/investors/messages'} className="ir-bell" aria-label={`Messages${counts && counts.unread ? `, ${counts.unread} unread` : ''}`}>
        <Ico n="bell" size={20} />{counts && counts.unread ? <span className="ir-bell__n">{counts.unread}</span> : null}</Link>
      <div className="ir-user">
        <button type="button" className="ir-user__btn" aria-expanded={open} onClick={() => setOpen((x) => !x)}>
          <span className="ir-avatar" aria-hidden="true">{(name || '?').slice(0, 1)}</span>
          <span className="ir-user__t"><b>{name || '…'}</b><small>{me && me.account ? (me.account.company_legal_name || 'Individual investor') : (room && room.name)}</small></span>
          <span aria-hidden="true">▾</span>
        </button>
        {open && !preview ? (
          <div className="ir-menu" role="menu">
            <Link role="menuitem" to="/investors/account" onClick={() => setOpen(false)}>Account</Link>
            <button role="menuitem" type="button" onClick={onSignOut}>Sign out</button>
          </div>
        ) : null}
      </div>
    </header>
  )
}

function Shell() {
  const [me, meErr, reloadMe] = useLoad('/investor/me')
  const [dash, , reloadDash] = useLoad('/investor/dashboard')
  const nav = useNavigate()
  const loc = useLocation()
  const [q, setQ] = useState(() => new URLSearchParams(window.location.search).get('q') || '')
  const [market, setMarket] = useState('')
  const signOut = async () => {
    try { await inv.post('/auth/logout', {}) } catch { /* already gone */ }
    setInvestorToken(null)
    nav('/investors/login', { replace: true })
  }
  const reload = useCallback(() => { reloadMe(); reloadDash() }, [reloadMe, reloadDash])
  const room = (dash && dash.room) || (me && me.room)
  const counts = dash && dash.counts
  useEffect(() => { if (document.getElementById('ir-main')) document.getElementById('ir-main').scrollTop = 0 }, [loc.pathname])
  const shared = { me, dash, reload, q, market, setQ }
  return (
    <div className="ir-app">
      <a className="ir-skip" href="#ir-main">Skip to content</a>
      <Sidebar room={room} counts={counts} me={me} />
      <div className="ir-frame">
        <Topbar me={me} counts={counts} room={room} q={q} setQ={setQ} market={market} setMarket={setMarket} onSignOut={signOut} />
        <main className="ir-main" id="ir-main" tabIndex={-1}>
          <Note kind="error">{meErr}</Note>
          {me && me.account && me.account.is_test ? <div className="ir-practice">Practice account - opportunities and documents here are practice records, not real offers.</div> : null}
          <Routes>
            <Route index element={<Dashboard {...shared} />} />
            <Route path="deals" element={<Deals {...shared} />} />
            <Route path="deals/:id" element={<Opportunity onChange={reload} />} />
            <Route path="opportunities/:id" element={<OldLink />} />
            <Route path="criteria" element={<Criteria me={me} onChange={reload} />} />
            <Route path="reviews" element={<Reviews />} />
            <Route path="offers" element={<Offers />} />
            <Route path="messages" element={<Messages onChange={reloadDash} />} />
            <Route path="messages/:id" element={<Messages onChange={reloadDash} />} />
            <Route path="documents" element={<Documents onChange={reload} />} />
            <Route path="account" element={<Account me={me} onChange={reload} />} />
            <Route path="profile" element={<Navigate to="/investors/account" replace />} />
            <Route path="*" element={<Navigate to="/investors" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

function OldLink() {
  const { id } = useParams()
  return <Navigate to={`/investors/deals/${id}`} replace />
}

const filterOpps = (list, q, market, cat) => {
  const needle = (q || '').trim().toLowerCase()
  return (list || []).filter((o) => {
    const a = o.address || {}
    if (market && (a.county || '').toLowerCase() !== market.toLowerCase()) return false
    if (cat && cat !== 'all' && catOf(o) !== cat) return false
    if (needle && ![a.line1, a.city, a.zip_code, a.county, o.summary, catOf(o)].filter(Boolean).join(' ').toLowerCase().includes(needle)) return false
    return true
  })
}

// ── dashboard ──────────────────────────────────────────────────────────────
function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

export function DashboardView({ dash, me, q, market, preview, onSave, concierge, support }) {
  const [cat, setCat] = useState('all')
  const opps = (dash && dash.opportunities) || []
  const cats = useMemo(() => {
    const seen = []
    opps.forEach((o) => { const c = catOf(o); if (!seen.includes(c)) seen.push(c) })
    return seen
  }, [opps])
  const shown = filterOpps(opps, q, market, cat)
  const c = (dash && dash.counts) || {}
  const room = (dash && dash.room) || {}
  const hero = opps.find((o) => o.cover_photo)
  const name = dash && dash.welcome ? dash.welcome.name : ''
  return (
    <div className="ir-dash2">
      <section className="ir-hero2" aria-labelledby="ir-welcome">
        {hero ? <Photo listingId={hero.id} fileId={hero.cover_photo} preview={preview} className="ir-hero2__photo" /> : <div className="ir-hero2__photo ir-hero2__photo--plain" />}
        <div className="ir-hero2__veil" />
        <div className="ir-hero2__body">
          <h1 id="ir-welcome" className="ir-hero2__h">Welcome Back, <span>{name || 'Investor'}</span></h1>
          <p className="ir-hero2__p">Your personalized investment opportunities in Dallas–Fort Worth.</p>
          <ul className="ir-hero2__stats">
            {[['deals', 'Recommended Deals', c.matching], ['review', 'Exclusive Reviews', c.in_review],
              ['offers', 'Active Offers', c.offers], ['spark', 'New This Week', c.new_this_week]].map(([ic, l, v]) => (
              <li key={l}><span className="ir-hero2__ic"><Ico n={ic} size={20} /></span><span><b>{v ?? 0}</b><small>{l}</small></span></li>
            ))}
          </ul>
        </div>
      </section>
      {!preview && dash && dash.onboarding ? <NextSteps ob={dash.onboarding} toSign={c.to_sign} /> : null}
      <section className="ir-panel ir-opps" aria-labelledby="ir-opps-h">
        <div className="ir-panel__head"><h2 id="ir-opps-h" className="ir-h2">Recommended Investment Opportunities</h2>
          <span className="ir-row">{!preview ? <Link to="/investors/deals" className="ir-link">View All</Link> : null}
            <span className="ir-gridtag" aria-hidden="true"><Ico n="deals" size={15} />Grid</span></span></div>
        <div className="ir-chips" role="tablist" aria-label="Property type">
          <button type="button" role="tab" aria-selected={cat === 'all'} className={cat === 'all' ? 'is-on' : ''} onClick={() => setCat('all')}>All Deals ({opps.length})</button>
          {cats.map((k) => <button key={k} type="button" role="tab" aria-selected={cat === k} className={cat === k ? 'is-on' : ''} onClick={() => setCat(k)}>{k}</button>)}
        </div>
        {shown.length ? <div className="ir-grid4">{shown.slice(0, 8).map((o) => <OppCard key={o.id} o={o} onSave={onSave} preview={preview} />)}
          {shown.length < 4 ? (
            <div className={`ir-morecard ir-morecard--span${4 - shown.length}`}>
              <span className="ir-morecard__ic" aria-hidden="true"><Ico n="spark" size={22} /></span>
              <h3>More opportunities on the way</h3>
              <p>New deals that fit your criteria appear here first. Keeping your criteria current means fewer, better matches.</p>
              {!preview ? (
                <div className="ir-morecard__btns">
                  <Link to="/investors/criteria" className="ir-btn ir-btn--primary ir-btn--sm">Review my criteria</Link>
                  <Link to="/investors/deals" className="ir-btn ir-btn--outline ir-btn--sm">Browse all deals</Link>
                </div>
              ) : null}
            </div>
          ) : null}</div>
          : <div className="ir-empty">{opps.length ? 'Nothing matches this filter.' : 'No opportunities are published for you yet. When one matches your criteria it appears here first.'}</div>}
      </section>
      <div className="ir-row2">
        <CriteriaPanel me={me} preview={preview} />
        <OpportunityChart opps={opps} counts={c} />
      </div>
      <div className="ir-row3">
        {concierge}
        {support}
      </div>
    </div>
  )
}

function OpportunityChart({ opps, counts }) {
  const now = new Date()
  const months = []
  for (let i = 5; i >= 0; i -= 1) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    months.push({ key: `${d.getFullYear()}-${d.getMonth()}`, label: d.toLocaleDateString('en-US', { month: 'short' }), n: 0, m: 0 })
  }
  opps.forEach((o) => {
    if (!o.published_at) return
    const d = new Date(o.published_at); const k = `${d.getFullYear()}-${d.getMonth()}`
    const x = months.find((y) => y.key === k)
    if (x) { x.n += 1; if (o.match && o.match.score >= 60) x.m += 1 }
  })
  const enough = months.filter((x) => x.n).length >= 2
  const cats = {}
  opps.forEach((o) => { const k = catOf(o); cats[k] = (cats[k] || 0) + 1 })
  return (
    <section className="ir-panel ir-chartcard" aria-labelledby="ir-chart-h">
      <div className="ir-panel__head"><h2 id="ir-chart-h" className="ir-h3"><Ico n="spark" />{enough ? 'Opportunities Over Time' : 'Opportunity Summary'}</h2>
        {enough ? <span className="ir-sel">6 Months</span> : null}</div>
      {enough ? (() => {
        const W = 440; const H = 170; const pad = 24
        const max = Math.max(1, ...months.map((x) => x.n))
        const xy = (i, v) => [pad + (i * (W - pad * 2)) / 5, H - pad - ((H - pad * 2) * v) / max]
        const path = (key) => months.map((x, i) => (i ? 'L' : 'M') + xy(i, x[key]).join(' ')).join(' ')
        return (
          <figure className="ir-chart">
            <div className="ir-chart__legend"><span><i className="is-a" />New opportunities</span><span><i className="is-b" />Strong matches (60%+)</span></div>
            <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={months.map((x) => `${x.label}: ${x.n} new, ${x.m} strong matches`).join('; ')}>
              {[0, 1, 2, 3].map((i) => <line key={i} x1={pad} x2={W - pad} y1={pad + (i * (H - pad * 2)) / 3} y2={pad + (i * (H - pad * 2)) / 3} stroke="#e6ecf5" />)}
              <path d={path('n') + ` L ${W - pad} ${H - pad} L ${pad} ${H - pad} Z`} fill="rgba(37,99,235,.12)" />
              <path d={path('n')} fill="none" stroke="#2563eb" strokeWidth="2.5" />
              <path d={path('m')} fill="none" stroke="#60a5fa" strokeWidth="2" strokeDasharray="5 4" />
              {months.map((x, i) => <text key={x.key} x={xy(i, 0)[0]} y={H - 5} textAnchor="middle" fontSize="11" fill="#64748b">{x.label}</text>)}
            </svg>
          </figure>
        )
      })() : (
        <div className="ir-sum">
          <div><b>{counts.opportunities ?? opps.length}</b><span>Opportunities shared with you</span></div>
          <div><b>{counts.average_match !== null && counts.average_match !== undefined ? `${counts.average_match}%` : '—'}</b><span>Average match</span></div>
          <div><b>{counts.price_min ? `${usdK(counts.price_min)}${counts.price_max !== counts.price_min ? '–' + usdK(counts.price_max) : ''}` : '—'}</b><span>Asking price range</span></div>
          <div><b>{counts.saved ?? 0}</b><span>Saved properties</span></div>
          <ul className="ir-sum__cats">{Object.entries(cats).map(([k, v]) => <li key={k}><span>{k}</span><b>{v}</b></li>)}</ul>
          <p className="ir-small ir-muted">A trend chart appears once opportunities have been shared with you in two or more months.</p>
        </div>
      )}
    </section>
  )
}

function NextSteps({ ob, toSign }) {
  const steps = [
    { k: 'identity', l: 'Confirm who you buy as', to: '/investors/account' },
    { k: 'buy_box', l: 'Confirm your investment criteria', to: '/investors/criteria' },
    { k: 'proof_of_funds', l: 'Upload proof of funds', to: '/investors/documents' },
  ]
  const left = steps.filter((s) => !ob[s.k])
  if (!left.length && !toSign) return null
  return (
    <div className="ir-nextstrip" role="region" aria-label="Your next steps">
      <b>Next steps</b>
      {left.map((s) => <Link key={s.k} to={s.to}>{s.l}</Link>)}
      {toSign ? <Link to="/investors/documents">{toSign} document{toSign === 1 ? '' : 's'} to sign</Link> : null}
    </div>
  )
}

function Dashboard({ dash, me, q, market, reload }) {
  const save = async (o) => { try { await inv.post(`/investor/opportunities/${o.id}/save`, { saved: !o.saved }); reload() } catch { /* shown on retry */ } }
  if (!dash) return <Loading />
  return <DashboardView dash={dash} me={me} q={q} market={market} onSave={save}
                        concierge={<ConciergePanel room={dash.room} top={(dash.opportunities || [])[0]} />} support={<SupportPanel room={dash.room} />} />
}

// ── concierge, messages, criteria ──────────────────────────────────────────
function ConciergePanel({ room, top }) {
  const [threads, , reloadT] = useLoad('/investor/threads')
  const nav = useNavigate()
  const con = threads && (threads.threads || []).find((t) => t.kind === 'concierge')
  const [t, setT] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const end = useRef(null)
  useEffect(() => { if (con) setT(con) }, [con])
  useEffect(() => { if (end.current && end.current.parentElement) end.current.parentElement.scrollTop = end.current.parentElement.scrollHeight }, [t])
  const send = async (msg, threadId) => {
    const body = (msg || text).trim()
    const id = threadId || (t && t.id)
    if (!body || !id) return
    setBusy(true); setErr(null)
    try { const r = await inv.post(`/investor/threads/${id}/messages`, { text: body }); if (!threadId) { setT(r.thread); setText('') } reloadT(); return r }
    catch (x) { setErr(x.message); return null } finally { setBusy(false) }
  }
  const explain = async () => {
    if (!top) { send('What deals are available for me?'); return }
    try {
      const th = await inv.post('/investor/threads', { kind: 'deal', listing_id: top.id })
      await send('Can you give me an overview of this property?', th.id)
      nav(`/investors/messages/${th.id}`)
    } catch (x) { setErr(x.message) }
  }
  const msgs = t ? (t.messages || []).slice(-4) : []
  const aiLive = !!(room && room.ai_available)
  const human = t && t.ai_active === false
  return (
    <section className="ir-panel ir-concierge" aria-labelledby="ir-con-h">
      <div className="ir-panel__head">
        <h2 id="ir-con-h" className="ir-h3"><span className="ir-con__mark" aria-hidden="true"><Ico n="spark" size={16} /></span>AI Investment Concierge</h2>
        <span className="ir-row">
          <span className={`ir-online${aiLive && !human ? '' : ' is-off'}`}>{human ? 'With your team' : aiLive ? 'AI available' : 'Automatic replies'}</span>
          {t ? <Link className="ir-link ir-small" to={`/investors/messages/${t.id}`}>Full conversation</Link> : null}
        </span>
      </div>
      {!aiLive ? <p className="ir-con__note">AI answers are not switched on for this Deal Room right now. You'll get automatic replies built from the deals shared with you, and a person from the team can take over any time.</p> : null}
      <ol className="ir-con__msgs" aria-live="polite">
        {!t ? <li><Loading /></li> : msgs.map((m, i) => {
          const who = m.role === 'ai' ? (m.engine === 'ai' ? ['AI', 'ai'] : ['Automatic reply', 'auto']) : m.role === 'staff' ? [m.by || 'Team', 'team'] : null
          return (
            <li key={i} className={`ir-con__m ir-con__m--${m.role}${who ? ' is-' + who[1] : ''}`}>
              {m.role !== 'buyer' ? <span className={`ir-con__av${m.role === 'staff' ? ' is-team' : ''}`} aria-hidden="true">{m.role === 'staff' ? (m.by || 'T').slice(0, 1).toUpperCase() : <Ico n="spark" size={16} />}</span> : null}
              <div>
                {who ? <span className={`ir-con__who is-${who[1]}`}>{who[0]}</span> : null}
                <p>{m.text}</p>
                <small>{m.role === 'buyer' ? 'You · ' : ''}{when(m.at)}</small>
              </div>
            </li>
          )
        })}
        <li ref={end} aria-hidden="true" />
      </ol>
      <div className="ir-con__quick" role="group" aria-label="Quick questions">
        <button type="button" disabled={busy || !t} onClick={() => send('What deals are available for me?')}>Find matching deals</button>
        <button type="button" disabled={busy} onClick={explain}>Explain a property</button>
        <button type="button" onClick={() => nav('/investors/criteria')}>Update my criteria</button>
        <button type="button" className="is-human" disabled={busy || !t} onClick={() => send('I would like to talk to a person please.')}>Talk to a human</button>
      </div>
      <form className="ir-con__form" onSubmit={(e) => { e.preventDefault(); send() }}>
        <label className="ir-sr" htmlFor="ir-con-in">Message the concierge</label>
        <input id="ir-con-in" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your message…" maxLength={2000} />
        <button className="ir-sendbtn" disabled={busy || !text.trim()} aria-label="Send"><Ico n="send" /></button>
      </form>
      <Note kind="error">{err}</Note>
    </section>
  )
}

const KIND = { concierge: 'AI Concierge', relationship: 'Your team', deal: 'Deal conversation' }

function SupportPanel({ room }) {
  const [d] = useLoad('/investor/threads')
  const nav = useNavigate()
  const messageTeam = async () => {
    try { const t = await inv.post('/investor/threads', { kind: 'relationship' }); nav(`/investors/messages/${t.id}`) } catch { nav('/investors/messages') }
  }
  const [tab, setTab] = useState('all')
  const list = ((d && d.threads) || []).filter((t) => tab === 'all' || t.kind === tab)
  const all = (d && d.threads) || []
  const count = (k) => all.filter((t) => k === 'all' || t.kind === k).length
  return (
    <section className="ir-panel" aria-labelledby="ir-sup-h">
      <div className="ir-panel__head"><h2 id="ir-sup-h" className="ir-h3"><Ico n="msg" />Messages &amp; Support</h2><Link className="ir-link" to="/investors/messages">View All</Link></div>
      <div className="ir-chips ir-chips--sm" role="tablist" aria-label="Conversation type">
        {[['all', 'All'], ['concierge', 'AI Concierge'], ['relationship', 'Team'], ['deal', 'Deals']].map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? 'is-on' : ''} onClick={() => setTab(k)}>{l} ({count(k)})</button>))}
      </div>
      <ul className="ir-sup">
        {list.slice(0, 4).map((t) => {
          const last = (t.messages || [])[t.messages.length - 1] || {}
          const handoff = t.kind !== 'relationship' && !t.ai_active
          const from = last.role === 'buyer' ? 'You' : last.role === 'staff' ? (last.by || 'Team') : last.role === 'ai' ? (last.engine === 'ai' ? 'AI' : 'Automatic reply') : ''
          return (
            <li key={t.id} className={t.unread ? 'is-unread' : ''}><Link to={`/investors/messages/${t.id}`}>
              <span className={`ir-sup__av is-${t.kind}`} aria-hidden="true"><Ico n={t.kind === 'concierge' ? 'spark' : t.kind === 'deal' ? 'home' : 'account'} size={17} /></span>
              <span className="ir-sup__t"><b>{KIND[t.kind] || cap(t.kind)}{handoff ? <em> · with the team</em> : null}</b>
                <small>{from ? <span className="ir-sup__from">{from}:</span> : null} {last.text}</small></span>
              <span className="ir-sup__r"><small>{when(t.last_message_at)}</small>{t.unread ? <i className="ir-dot" aria-label={`${t.unread} unread`} /> : <i className="ir-dot ir-dot--read" aria-label="read" />}</span>
            </Link></li>
          )
        })}
        {d && !list.length ? <li className="ir-muted ir-small">No conversations here yet.</li> : null}
      </ul>
      <div className="ir-sup__help">
        <span className="ir-sup__helpic" aria-hidden="true"><Ico n="account" size={18} /></span>
        <span className="ir-sup__helpt"><b>Need a person?</b>
          <small>{room && (room.support_email || room.support_phone) ? [room.support_email, room.support_phone].filter(Boolean).join(' · ') : 'Your Deal Room team replies here.'}</small></span>
        <button type="button" className="ir-btn ir-btn--outline ir-btn--sm" onClick={messageTeam}>Message the team</button>
      </div>
    </section>
  )
}

const TIMELINE = { '0_30': '0 – 30 days', '30_60': '30 – 60 days', '60_90': '60 – 90 days', '90_plus': '90+ days' }

function CriteriaPanel({ me, preview }) {
  const box = me && (me.buy_boxes || [])[0]
  const f = box ? box.fields : {}
  const cr = (box && box.criteria) || {}
  const v = (k) => (f[k] ? f[k].value : null)
  const rows = [
    ['review', 'Strategy', (v('strategies') || []).map(words).map(cap).join(', ')],
    ['home', 'Property Types', (v('property_types') || []).map(words).map(cap).join(', ')],
    ['pin', 'Target Counties', [...(v('counties') || []), ...(v('cities') || [])].join(', ')],
    ['offers', 'Budget Range', v('min_price') || v('max_price') ? `${v('min_price') ? usd(v('min_price')) : 'Any'} – ${v('max_price') ? usd(v('max_price')) : 'Any'}` : ''],
    ['docs', 'Minimum Cash Flow', cr.min_cash_flow ? `${usd(cr.min_cash_flow)} / month` : ''],
    ['spark', 'Timeline', TIMELINE[cr.timeline] || ''],
  ]
  return (
    <section className="ir-panel" aria-labelledby="ir-crit-h">
      <div className="ir-panel__head"><h2 id="ir-crit-h" className="ir-h3"><Ico n="criteria" />My Investment Criteria</h2>{!preview ? <Link className="ir-link" to="/investors/criteria">Edit</Link> : null}</div>
      {preview ? <p className="ir-small ir-muted">Each investor sees their own criteria here, labeled by source.</p> : !me ? <Loading /> : box ? (
        <>
          <dl className="ir-crit">{rows.map(([ic, l, val]) => (
            <div key={l}><span className="ir-crit__ic"><Ico n={ic} size={17} /></span><div><dt>{l}</dt><dd>{val || <span className="ir-muted">Not set</span>}</dd></div></div>))}</dl>
          {cr.preferences ? <p className="ir-small"><b>Buying preferences:</b> {cr.preferences}</p> : null}
          <p className="ir-small ir-muted">{box.confirmed_at ? 'Confirmed by you.' : 'Stated in conversation - please review and confirm.'}</p>
        </>
      ) : <p className="ir-small">Tell us what you buy so we only send what fits. <Link to="/investors/criteria">Set your criteria</Link></p>}
    </section>
  )
}

function Deals({ q, market, setQ }) {
  const [d, err, reload] = useLoad('/investor/opportunities')
  const [cat, setCat] = useState('all')
  const [only, setOnly] = useState('all')
  const loc = useLocation()
  useEffect(() => { const x = new URLSearchParams(loc.search).get('q'); if (x !== null) setQ(x) }, [loc.search, setQ])
  const all = (d && d.items) || []
  const cats = [...new Set(all.map(catOf))]
  let shown = filterOpps(all, q, market, cat)
  if (only === 'saved') shown = shown.filter((o) => o.saved)
  if (only === 'review') shown = shown.filter((o) => o.hold && o.hold.yours)
  const save = async (o) => { try { await inv.post(`/investor/opportunities/${o.id}/save`, { saved: !o.saved }); reload() } catch { /* retry */ } }
  return (
    <>
      <header className="ir-pagehead"><h1 className="ir-h1">Available Deals</h1>
        <p className="ir-lead">Only properties the team approved for investors, matched against your criteria.</p></header>
      <div className="ir-toolbar">
        <div className="ir-chips" role="tablist" aria-label="Property type">
          <button type="button" role="tab" aria-selected={cat === 'all'} className={cat === 'all' ? 'is-on' : ''} onClick={() => setCat('all')}>All deals ({all.length})</button>
          {cats.map((k) => <button key={k} type="button" role="tab" aria-selected={cat === k} className={cat === k ? 'is-on' : ''} onClick={() => setCat(k)}>{k}</button>)}
        </div>
        <div className="ir-chips" role="group" aria-label="Show">
          {[['all', 'Everything'], ['saved', 'Saved'], ['review', 'My exclusive reviews']].map(([k, l]) => (
            <button key={k} type="button" aria-pressed={only === k} className={only === k ? 'is-on' : ''} onClick={() => setOnly(k)}>{l}</button>))}
        </div>
      </div>
      {q ? <p className="ir-small">Searching for “{q}” · <button type="button" className="ir-link" onClick={() => setQ('')}>clear</button></p> : null}
      <Note kind="error">{err}</Note>
      {!d ? <Loading /> : shown.length ? <div className="ir-grid">{shown.map((o) => <OppCard key={o.id} o={o} onSave={save} />)}</div>
        : <div className="ir-empty">Nothing here yet.</div>}
    </>
  )
}

function Opportunity({ onChange }) {
  const { id } = useParams()
  const loc = useLocation()
  const nav = useNavigate()
  const [o, err, reload] = useLoad(`/investor/opportunities/${id}`)
  const [photo, setPhoto] = useState(0)
  const [mode, setMode] = useState(() => (new URLSearchParams(loc.search).get('offer') ? 'offer' : null))
  const [f, setF] = useState({ amount: '', financing: 'cash', note: '' })
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  if (err) return <><Link to="/investors/deals" className="ir-back">← Available deals</Link><Note kind="error">{err}</Note></>
  if (!o) return <Loading />
  const room = o.room || {}
  const photos = room.photos || []
  const hold = o.hold
  const mine = hold && hold.yours && hold.status === 'active'
  const act = async (kind, extra = {}) => {
    setBusy(true); setMsg(null)
    try {
      await inv.post(`/investor/opportunities/${id}/interest`, { kind, ...extra })
      setMsg({ kind: 'ok', text: kind === 'pass' ? 'Noted - we will not send you more on this one.'
        : kind === 'offer' ? 'Offer sent to the team. They will reply here and in Messages.' : 'Interest sent. The team will be in touch.' })
      setMode(null); reload(); onChange && onChange()
    } catch (x) { setMsg({ kind: 'error', text: x.message }) } finally { setBusy(false) }
  }
  const askReview = async () => {
    setBusy(true); setMsg(null)
    try {
      await inv.post(`/investor/opportunities/${id}/exclusive-review`, { note: f.note })
      setMsg({ kind: 'ok', text: 'Request sent. A person decides - you will see the result here.' })
      setMode(null); reload()
    } catch (x) { setMsg({ kind: 'error', text: x.message }) } finally { setBusy(false) }
  }
  const ask = async () => {
    try { const t = await inv.post('/investor/threads', { kind: 'deal', listing_id: id }); nav(`/investors/messages/${t.id}`) }
    catch (x) { setMsg({ kind: 'error', text: x.message }) }
  }
  const save = async () => { try { await inv.post(`/investor/opportunities/${id}/save`, { saved: !o.saved }); reload(); onChange && onChange() } catch { /* retry */ } }
  const status = o.you && o.you.status
  return (
    <>
      <Link to="/investors/deals" className="ir-back">← Available deals</Link>
      {mine ? (
        <div className="ir-hold" role="region" aria-label="Your exclusive review">
          <div><p className="ir-eyebrow ir-eyebrow--light">Your exclusive review</p>
            <p className="ir-hold__t">Hidden from other investors for</p>
            <Countdown endsAt={hold.ends_at} /></div>
          <p className="ir-hold__n">{o.hold_notice}</p>
        </div>
      ) : null}
      <div className="ir-detail">
        <div className="ir-detail__media">
          <Photo listingId={id} fileId={photos[photo] && photos[photo].id} alt={`Photo ${photo + 1} of ${addr1(room.property)}`} className="ir-photo--hero" />
          {photos.length > 1 ? (
            <div className="ir-thumbs" role="list" aria-label="Photos">
              {photos.map((p, i) => (
                <button key={p.id} type="button" role="listitem" className={i === photo ? 'is-on' : ''} onClick={() => setPhoto(i)}
                        aria-label={`Show photo ${i + 1}`} aria-pressed={i === photo}>
                  <Photo listingId={id} fileId={p.id} alt="" />
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="ir-detail__side">
          <div className="ir-row">
            <span className="ir-tag">{catOf(o)}</span>
            {o.is_test ? <span className="ir-tag ir-tag--practice">Practice listing - not a real offer</span> : null}
            <button type="button" className={`ir-heart ir-heart--inline${o.saved ? ' is-on' : ''}`} aria-pressed={!!o.saved} onClick={save}
                    aria-label={o.saved ? 'Remove from saved' : 'Save this property'}><Ico n="heart" size={17} /></button>
          </div>
          <h1 className="ir-h1 ir-h1--addr">{addr1(room.property)}</h1>
          <p className="ir-lead">{addr2(room.property)}{room.property && room.property.county ? ` · ${room.property.county} County` : ''}</p>
          <div className="ir-pricebox">
            <div><small>Asking</small><b>{usd(room.asking_price)}</b></div>
            {'arv' in room ? <div><small>Est. after repair</small><b>{usd(room.arv)}</b></div> : null}
            {'estimated_repairs' in room ? <div><small>Est. repairs</small><b>{usd(room.estimated_repairs)}</b></div> : null}
          </div>
          <FactsRow facts={o.facts} />
          <div className="ir-matchbox">
            <span className={`ir-matchring${o.match && o.match.score >= 80 ? ' is-hi' : ''}`}><b>{o.match && o.match.score !== null && o.match.score !== undefined ? o.match.score : '—'}</b><small>% match</small></span>
            <ul className="ir-why">
              {((o.match && o.match.factors) || []).slice(0, 5).map((x, i) => (
                <li key={i} className={x.matched ? 'is-yes' : x.matched === false ? 'is-no' : ''}>{x.matched ? '✓' : x.matched === false ? '✕' : '·'} {x.detail}</li>
              ))}
            </ul>
          </div>
          <Note kind={msg && msg.kind}>{msg && msg.text}</Note>
          {status ? <p className="ir-status">Your response: <b>{cap(status)}</b>{o.you.your_offer ? ` · ${usd(o.you.your_offer)}` : ''}</p> : null}
          {o.can_submit !== false ? (
            <div className="ir-actions">
              <button type="button" className="ir-btn ir-btn--primary" disabled={busy} onClick={() => setMode(mode === 'offer' ? null : 'offer')}>Submit offer</button>
              <button type="button" className="ir-btn ir-btn--outline" disabled={busy} onClick={() => act('interested')}>I&apos;m interested</button>
              <button type="button" className="ir-btn ir-btn--outline" onClick={ask}>Ask a question</button>
              {!hold && !o.hold_requested ? <button type="button" className="ir-btn ir-btn--outline" onClick={() => setMode(mode === 'review' ? null : 'review')}>Request exclusive review</button> : null}
              {o.hold_requested ? <span className="ir-muted ir-small">Exclusive review requested - waiting for a decision.</span> : null}
              <button type="button" className="ir-btn ir-btn--quiet" disabled={busy} onClick={() => act('pass')}>Pass</button>
            </div>
          ) : null}
          {mode === 'offer' ? (
            <form className="ir-panel ir-form ir-form--tight" onSubmit={(e) => { e.preventDefault(); act('offer', { amount: f.amount, financing: f.financing, note: f.note }) }}>
              <h2 className="ir-h3">Your offer</h2>
              <label className="ir-field"><span>Amount (USD)</span>
                <input required inputMode="numeric" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^0-9.]/g, '') })} /></label>
              <label className="ir-field"><span>How you will pay</span>
                <select value={f.financing} onChange={(e) => setF({ ...f, financing: e.target.value })}>
                  {['cash', 'hard_money', 'private', 'conventional', 'mixed'].map((k) => <option key={k} value={k}>{cap(k)}</option>)}
                </select></label>
              <label className="ir-field"><span>Note (closing timeline, conditions)</span>
                <textarea rows={3} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
              <p className="ir-muted ir-small">An offer here tells the team your terms. It is not a contract; any agreement is signed separately.</p>
              <button className="ir-btn ir-btn--primary" disabled={busy}>Send offer</button>
            </form>
          ) : null}
          {mode === 'review' ? (
            <div className="ir-panel ir-form ir-form--tight">
              <h2 className="ir-h3">Request exclusive review</h2>
              <p className="ir-small">{o.hold_notice}</p>
              <label className="ir-field"><span>Why you need the time</span>
                <textarea rows={2} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
              <button type="button" className="ir-btn ir-btn--primary" disabled={busy} onClick={askReview}>Send request</button>
            </div>
          ) : null}
        </div>
      </div>
      <div className="ir-two">
        <section className="ir-panel">
          <h2 className="ir-h2">About this property</h2>
          <p className="ir-prose">{room.summary || 'No summary published.'}</p>
          {room.condition_summary ? <><h3 className="ir-h4">Condition</h3><p className="ir-prose">{room.condition_summary}</p></> : null}
          {room.closing && (room.closing.target_close || room.closing.title_company) ? (
            <p className="ir-small ir-muted">Target close {room.closing.target_close || '—'}{room.closing.title_company ? ` · Title: ${room.closing.title_company}` : ''}</p>
          ) : null}
        </section>
        <section className="ir-panel">
          <h2 className="ir-h2">Documents &amp; signatures</h2>
          {(room.documents || []).length ? (
            <ul className="ir-rows">{room.documents.map((dc) => <li key={dc.id}><div><DocLink path={`/investor/opportunities/${id}/documents/${dc.id}`} name={dc.title || 'Document'} /><small>Published by the team</small></div></li>)}</ul>
          ) : <p className="ir-muted ir-small">No documents published for this property.</p>}
          {(o.signatures || []).length ? (
            <ul className="ir-rows">{o.signatures.map((s) => (
              <li key={s.envelope_id}><div><b>{s.title}</b><small>{s.completed ? 'Signed by everyone' : s.your_turn ? 'Waiting for your signature' : s.your_status === 'signed' ? 'You signed - waiting for the other party' : 'Waiting for another signer'}</small></div>
                {s.your_turn && !s.completed ? <Link className="ir-btn ir-btn--primary ir-btn--sm" to="/investors/documents">Review &amp; sign</Link>
                  : <span className={`ir-pill${s.completed ? ' ir-pill--ok' : ''}`}>{s.completed ? 'Completed' : 'Pending'}</span>}</li>))}</ul>
          ) : null}
        </section>
      </div>
      {room.disclosure ? (
        <section className="ir-disclosure" aria-label="Disclosure">
          <h2 className="ir-h4">{room.disclosure.title}</h2>
          <p>{room.disclosure.text}</p>
          <small>{room.disclosure.law}</small>
        </section>
      ) : null}
    </>
  )
}

function DocLink({ path, name }) {
  const [busy, setBusy] = useState(false)
  const open = async () => {
    setBusy(true)
    try { const u = await objectUrl(path); window.open(u, '_blank', 'noopener'); setTimeout(() => URL.revokeObjectURL(u), 60000) }
    catch { /* shown as unavailable */ } finally { setBusy(false) }
  }
  return <button type="button" className="ir-link" onClick={open} disabled={busy}>{busy ? 'Opening…' : name}</button>
}

// ── reviews / offers ───────────────────────────────────────────────────────
function Reviews() {
  const [d, err] = useLoad('/investor/holds')
  const rows = (d && d.holds) || []
  const label = { requested: 'Waiting for a decision', active: 'Active', declined: 'Declined', ended: 'Ended' }
  return (
    <>
      <header className="ir-pagehead"><h1 className="ir-h1">Exclusive Reviews</h1>
        <p className="ir-lead">{d ? d.notice : ''}</p></header>
      <Note kind="error">{err}</Note>
      {!d ? <Loading /> : rows.length ? (
        <ul className="ir-list">{rows.map((h) => (
          <li key={h.id} className={`ir-list__row${h.status === 'active' ? ' is-active' : ''}`}>
            <div><b>{h.address || 'Property'}</b><small>Requested {when(h.requested_at)}{h.note ? ` · “${h.note}”` : ''}</small>
              {h.status === 'ended' && h.end_reason ? <small>Ended: {words(h.end_reason)}</small> : null}</div>
            <div className="ir-row">
              {h.status === 'active' ? <Countdown endsAt={h.ends_at} compact /> : null}
              <span className={`ir-pill${h.status === 'active' ? ' ir-pill--ok' : h.status === 'declined' ? ' ir-pill--bad' : ''}`}>{label[h.status] || cap(h.status)}</span>
              {h.listing_id ? <Link className="ir-btn ir-btn--outline ir-btn--sm" to={`/investors/deals/${h.listing_id}`}>Open</Link> : null}
            </div>
          </li>))}</ul>
      ) : <div className="ir-empty">You haven&apos;t requested an exclusive review. Request one from a property page when you need time to evaluate it.</div>}
    </>
  )
}

function Offers() {
  const [d, err] = useLoad('/investor/offers')
  const rows = (d && d.offers) || []
  return (
    <>
      <header className="ir-pagehead"><h1 className="ir-h1">My Offers</h1>
        <p className="ir-lead">Your interest, offers and passes. An offer tells the team your terms - it is not a contract.</p></header>
      <Note kind="error">{err}</Note>
      {!d ? <Loading /> : rows.length ? (
        <ul className="ir-list">{rows.map((o, i) => (
          <li key={i} className="ir-list__row">
            <div><b>{o.address}</b><small>{when(o.at)}{o.financing ? ` · ${cap(o.financing)}` : ''}{o.note ? ` · ${o.note}` : ''}</small></div>
            <div className="ir-row">
              {o.offer ? <b className="ir-money">{usd(o.offer)}</b> : null}
              <span className={`ir-pill${o.status === 'offer_submitted' ? ' ir-pill--blue' : ''}`}>{cap(o.status)}</span>
              {o.listing_id ? <Link className="ir-btn ir-btn--outline ir-btn--sm" to={`/investors/deals/${o.listing_id}`}>Open</Link> : null}
            </div>
          </li>))}</ul>
      ) : <div className="ir-empty">No offers yet. Open a deal and choose Submit offer.</div>}
    </>
  )
}

// ── messages ───────────────────────────────────────────────────────────────
function Messages({ onChange }) {
  const { id } = useParams()
  const nav = useNavigate()
  const [d, err, reload] = useLoad('/investor/threads')
  const threads = (d && d.threads) || []
  useEffect(() => {
    if (!id && threads.length && window.matchMedia('(min-width: 900px)').matches) nav(`/investors/messages/${threads[0].id}`, { replace: true })
  }, [id, threads, nav])
  return (
    <>
      <header className="ir-pagehead"><h1 className="ir-h1">Messages</h1></header>
      <Note kind="error">{err}</Note>
      <div className={`ir-inbox${id ? ' has-thread' : ''}`}>
        <ul className="ir-threads" aria-label="Conversations">
          {threads.map((t) => {
            const last = t.messages && t.messages[t.messages.length - 1]
            return (
              <li key={t.id}>
                <Link to={`/investors/messages/${t.id}`} className={t.id === id ? 'is-on' : ''} aria-current={t.id === id ? 'page' : undefined}>
                  <span className="ir-threads__k">{KIND[t.kind] || cap(t.kind)}{t.unread ? <span className="ir-nbadge is-alert">{t.unread}</span> : null}</span>
                  <span className="ir-threads__l">{last ? last.text : ''}</span>
                  <small>{when(t.last_message_at)}</small>
                </Link>
              </li>
            )
          })}
          {!d ? <li><Loading /></li> : null}
        </ul>
        {id ? <Thread key={id} id={id} onChange={() => { reload(); onChange && onChange() }} /> : null}
      </div>
    </>
  )
}

function Thread({ id, onChange }) {
  const [t, err, reload] = useLoad(`/investor/threads/${id}`)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [sendErr, setSendErr] = useState(null)
  const end = useRef(null)
  useEffect(() => { if (t) onChange && onChange() }, [t]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (end.current && end.current.parentElement) end.current.parentElement.scrollTop = end.current.parentElement.scrollHeight }, [t])
  useEffect(() => { const x = setInterval(reload, 15000); return () => clearInterval(x) }, [reload])
  const send = async (e) => {
    e.preventDefault()
    if (!text.trim()) return
    setBusy(true); setSendErr(null)
    try { await inv.post(`/investor/threads/${id}/messages`, { text }); setText(''); reload() }
    catch (x) { setSendErr(x.message) } finally { setBusy(false) }
  }
  if (err) return <Note kind="error">{err}</Note>
  if (!t) return <div className="ir-thread"><Loading /></div>
  return (
    <section className="ir-thread" aria-label={KIND[t.kind] || 'Conversation'}>
      <header className="ir-thread__head">
        <Link to="/investors/messages" className="ir-back ir-back--inline" aria-label="All conversations">←</Link>
        <div><b>{KIND[t.kind] || cap(t.kind)}</b>
          <small>{t.ai_active ? 'The concierge answers from published information; ask for a person any time.' : 'A person from the team is handling this conversation.'}</small></div>
      </header>
      <ol className="ir-msgs" aria-live="polite">
        {(t.messages || []).map((m, i) => (
          <li key={i} className={`ir-msg ir-msg--${m.role}`}>
            <span className="ir-msg__who">{m.role === 'staff' && m.by ? m.by : m.role === 'ai' ? (m.engine === 'ai' ? 'Concierge (AI)' : 'Concierge (automatic reply)') : m.role === 'buyer' ? 'You' : 'Deal Room'} · {when(m.at)}</span>
            <p>{m.text}</p>
          </li>
        ))}
        <li ref={end} aria-hidden="true" />
      </ol>
      <form className="ir-compose" onSubmit={send}>
        <label className="ir-sr" htmlFor="ir-msg">Message</label>
        <textarea id="ir-msg" rows={2} value={text} maxLength={4000} placeholder="Write a message…"
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(e) }} />
        <button className="ir-btn ir-btn--primary" disabled={busy || !text.trim()}>{busy ? 'Sending…' : 'Send'}</button>
      </form>
      <Note kind="error">{sendErr}</Note>
      <p className="ir-muted ir-small ir-pad">Reply STOP to stop outreach. Ask for a person and the team joins this conversation.</p>
    </section>
  )
}

// ── documents ──────────────────────────────────────────────────────────────
const CATEGORY = { proof_of_funds: 'Proof of funds', identity: 'Identity', entity_documents: 'Entity documents',
  rent_roll: 'Rent roll', offer: 'Offer', other: 'Other' }
const REVIEW = { pending: 'Waiting for review', accepted: 'Accepted', rejected: 'Not accepted', blocked: 'Blocked' }

function Documents({ onChange }) {
  const [d, err, reload] = useLoad('/investor/files')
  const [sigs, sErr, reloadSigs] = useLoad('/investor/signatures')
  const [cat, setCat] = useState('proof_of_funds')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const input = useRef(null)
  const nav = useNavigate()
  const lim = (d && d.limits) || { max_mb: 25, types: [] }
  const upload = async (file) => {
    if (!file) return
    if (file.size > lim.max_mb * 1048576) { setMsg({ kind: 'error', text: `That file is over ${lim.max_mb} MB.` }); return }
    const fd = new FormData(); fd.append('file', file); fd.append('category', cat)
    setBusy(true); setMsg(null)
    try {
      await inv.upload('/investor/files', fd)
      setMsg({ kind: 'ok', text: `${file.name} uploaded. A person will review it.` })
      reload(); onChange && onChange()
    } catch (x) { setMsg({ kind: 'error', text: x.message }) } finally { setBusy(false); if (input.current) input.current.value = '' }
  }
  const sign = async (envId) => {
    try { const s = await inv.post(`/investor/signatures/${envId}/session`, {}); nav(s.url || `/sign/${s.token}`) }
    catch (x) { setMsg({ kind: 'error', text: x.message }) }
  }
  useEffect(() => { const x = setInterval(reloadSigs, 30000); return () => clearInterval(x) }, [reloadSigs])
  return (
    <>
      <header className="ir-pagehead"><h1 className="ir-h1">Documents</h1>
        <p className="ir-lead">Signatures waiting for you, and the files you share with the team.</p></header>
      <Note kind="error">{err || sErr}</Note>
      <section className="ir-panel" aria-labelledby="sig-h">
        <h2 id="sig-h" className="ir-h2">To sign</h2>
        {!sigs ? <Loading /> : (sigs.documents || []).length ? (
          <ul className="ir-rows">
            {sigs.documents.map((s) => (
              <li key={s.envelope_id}>
                <div><b>{s.title}</b><small>{s.completed ? 'Completed by everyone' : s.your_turn ? `Your turn - you sign as ${s.your_role}` : (s.your_status === 'signed' ? 'You signed - waiting for the other party' : 'Waiting for another signer')}</small></div>
                {s.your_turn && !s.completed ? <button type="button" className="ir-btn ir-btn--primary ir-btn--sm" onClick={() => sign(s.envelope_id)}>Review &amp; sign</button>
                  : <span className={`ir-pill${s.completed ? ' ir-pill--ok' : ''}`}>{s.completed ? 'Signed' : 'Pending'}</span>}
              </li>
            ))}
          </ul>
        ) : <p className="ir-muted">Nothing to sign right now.</p>}
      </section>
      <section className="ir-panel" aria-labelledby="up-h">
        <h2 id="up-h" className="ir-h2">Upload</h2>
        <div className="ir-upload">
          <label className="ir-field"><span>What is it?</span>
            <select value={cat} onChange={(e) => setCat(e.target.value)}>
              {(d ? d.categories : Object.keys(CATEGORY)).map((k) => <option key={k} value={k}>{CATEGORY[k] || cap(k)}</option>)}
            </select></label>
          <label className={`ir-drop${busy ? ' is-busy' : ''}`}
                 onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files[0]) }}>
            <input ref={input} type="file" className="ir-sr" accept={(lim.types || []).join(',')} onChange={(e) => upload(e.target.files[0])} disabled={busy} />
            <b>{busy ? 'Uploading…' : 'Choose a file or drop it here'}</b>
            <small>{(lim.types || []).map((x) => x.replace('.', '').toUpperCase()).join(', ')} · up to {lim.max_mb} MB</small>
          </label>
        </div>
        <Note kind={msg && msg.kind}>{msg && msg.text}</Note>
        <h3 className="ir-h4">Your files</h3>
        {!d ? <Loading /> : (d.files || []).length ? (
          <ul className="ir-rows">
            {d.files.map((x) => (
              <li key={x.id}>
                <div><DocLink path={`/investor/files/${x.id}`} name={x.name} />
                  <small>{CATEGORY[x.category] || cap(x.category)} · {fileSize(x.size)} · {x.from_you ? 'from you' : 'from the team'} · {when(x.uploaded_at)}</small></div>
                <span className={`ir-pill${x.review_status === 'accepted' ? ' ir-pill--ok' : x.review_status === 'rejected' || x.scan_status === 'blocked' ? ' ir-pill--bad' : ''}`}>
                  {x.from_you ? (REVIEW[x.review_status] || cap(x.review_status)) : 'Shared with you'}</span>
              </li>
            ))}
          </ul>
        ) : <p className="ir-muted">No files yet.</p>}
      </section>
    </>
  )
}

// ── criteria & account ─────────────────────────────────────────────────────
const LABEL_TONE = { 'BUYER STATED': 'stated', 'DOCUMENT VERIFIED': 'ok', 'SYSTEM INFERRED': 'inferred', UNKNOWN: 'unknown' }
const BOX_LABEL = { property_types: 'Property types', strategies: 'Strategies', states: 'States', counties: 'Counties',
  cities: 'Cities', zips: 'ZIP codes', min_price: 'Min price', max_price: 'Max price', min_units: 'Min units',
  max_units: 'Max units', min_acres: 'Min acres', max_acres: 'Max acres', rehab_tolerance: 'Rehab tolerance', min_cap_rate: 'Min cap rate' }
const ALWAYS = ['property_types', 'counties', 'max_price']
const TYPES = ['single_family', 'multifamily', 'townhouse', 'condo', 'land', 'commercial', 'industrial', 'mixed_use', 'mobile_home']
const STRATS = ['flip', 'rental', 'brrrr', 'value_add', 'development', 'land']
const COUNTIES = ['Dallas', 'Tarrant', 'Collin', 'Denton', 'Rockwall', 'Ellis', 'Johnson', 'Kaufman', 'Parker']

function Label({ l }) { return <span className={`ir-lbl ir-lbl--${LABEL_TONE[l] || 'unknown'}`}>{l}</span> }

function Criteria({ me, onChange }) {
  const [msg, setMsg] = useState(null)
  if (!me) return <Loading />
  return (
    <>
      <header className="ir-pagehead"><h1 className="ir-h1">My Criteria</h1>
        <p className="ir-lead">What you buy. Every value is labeled by where it came from; saving confirms it.</p></header>
      <Note kind={msg && msg.kind}>{msg && msg.text}</Note>
      <section className="ir-panel" aria-labelledby="bb-h">
        <h2 id="bb-h" className="ir-h2">Buy box</h2>
        {(me.buy_boxes || []).length ? me.buy_boxes.map((b) => <BuyBox key={b.id} box={b} onSaved={(t) => { setMsg({ kind: 'ok', text: t }); onChange() }}
                                                                   onError={(t) => setMsg({ kind: 'error', text: t })} />)
          : <BuyBox box={null} onSaved={(t) => { setMsg({ kind: 'ok', text: t }); onChange() }} onError={(t) => setMsg({ kind: 'error', text: t })} />}
        <p className="ir-small ir-muted"><Label l="BUYER STATED" /> you told us · <Label l="DOCUMENT VERIFIED" /> a person checked a document · <Label l="SYSTEM INFERRED" /> from public records · <Label l="UNKNOWN" /> not given</p>
      </section>
    </>
  )
}

function Account({ me, onChange }) {
  const [msg, setMsg] = useState(null)
  if (!me) return <Loading />
  return (
    <>
      <header className="ir-pagehead"><h1 className="ir-h1">Account</h1>
        <p className="ir-lead">Who you are and the entities you buy through.</p></header>
      <Note kind={msg && msg.kind}>{msg && msg.text}</Note>
      <Identity me={me} onSaved={(t) => { setMsg({ kind: 'ok', text: t }); onChange() }} onError={(t) => setMsg({ kind: 'error', text: t })} />
      <section className="ir-panel" aria-labelledby="fund-h">
        <h2 id="fund-h" className="ir-h2">Funding</h2>
        <p>{me.funding && me.funding.documented
          ? <><Label l="DOCUMENT VERIFIED" /> Proof of funds accepted{me.funding.pof_expires ? ` · valid until ${me.funding.pof_expires}` : ''}.</>
          : <><Label l={me.funding && me.funding.stated ? 'BUYER STATED' : 'UNKNOWN'} /> {me.funding && me.funding.stated ? `You told us: ${me.funding.stated}` : 'Not provided yet.'} <Link to="/investors/documents">Upload proof of funds</Link></>}</p>
      </section>
    </>
  )
}

function Identity({ me, onSaved, onError }) {
  const a = me.account || {}
  const [f, setF] = useState({ kind: a.kind || 'individual', legal_name: a.legal_name || '', preferred_name: a.preferred_name || '',
    phone: a.phone || '', company_legal_name: a.company_legal_name || '', dba: a.dba || '', entity_type: a.entity_type || 'llc',
    business_address: a.business_address || '', position: a.position || '' })
  const [ent, setEnt] = useState({ legal_name: '', position: '', authority: 'signer' })
  const [busy, setBusy] = useState(false)
  const save = async (e) => {
    e.preventDefault(); setBusy(true)
    try { await inv.patch('/investor/me/identity', f); onSaved('Saved.') } catch (x) { onError(x.message) } finally { setBusy(false) }
  }
  const addEnt = async (e) => {
    e.preventDefault(); setBusy(true)
    try {
      const r = await inv.post('/investor/me/entities', ent)
      setEnt({ legal_name: '', position: '', authority: 'signer' })
      onSaved(r.status === 'needs_review' ? 'Added. That name matches another record, so a person will confirm the link.' : 'Entity added.')
    } catch (x) { onError(x.message) } finally { setBusy(false) }
  }
  const T = (k, l, p = {}) => (
    <label className="ir-field"><span>{l}</span><input value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} {...p} /></label>
  )
  return (
    <section className="ir-panel" aria-labelledby="id-h">
      <h2 id="id-h" className="ir-h2">Identity {a.identity_complete ? <span className="ir-pill ir-pill--ok">Complete</span> : null}
        {a.email_verified ? <span className="ir-pill">Email verified</span> : null}</h2>
      <form onSubmit={save} className="ir-form ir-form--grid">
        <fieldset className="ir-field ir-seg ir-span2"><legend>I buy as</legend>
          {[['individual', 'An individual'], ['business', 'A company or LLC']].map(([k, l]) => (
            <label key={k} className={f.kind === k ? 'is-on' : ''}><input type="radio" name="idkind" checked={f.kind === k} onChange={() => setF({ ...f, kind: k })} />{l}</label>
          ))}
        </fieldset>
        {T('legal_name', 'Full legal name', { required: true, autoComplete: 'name' })}
        {T('preferred_name', 'What should we call you?')}
        {T('phone', 'Phone', { type: 'tel', autoComplete: 'tel' })}
        {f.kind === 'business' ? <>
          {T('company_legal_name', 'Company legal name', { required: true, autoComplete: 'organization' })}
          {T('dba', 'DBA (if any)')}
          <label className="ir-field"><span>Entity type</span>
            <select value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })}>
              {['llc', 'corporation', 'partnership', 'trust', 'sole_proprietor', 'other'].map((k) => <option key={k} value={k}>{k === 'llc' ? 'LLC' : cap(k)}</option>)}
            </select></label>
          {T('position', 'Your position')}
          {T('business_address', 'Business address', { autoComplete: 'street-address' })}
        </> : null}
        <div className="ir-span2"><button className="ir-btn ir-btn--primary" disabled={busy}>Save identity</button></div>
      </form>
      <h3 className="ir-h4">Entities you buy through</h3>
      <ul className="ir-rows">
        {(me.entities || []).map((x) => (
          <li key={x.id}><div><b>{x.name}</b><small>{[x.position, x.authority && cap(x.authority)].filter(Boolean).join(' · ')}</small></div>
            <span className={`ir-pill${x.status === 'confirmed' ? ' ir-pill--ok' : ''}`}>{x.status === 'needs_review' ? 'Being reviewed' : cap(x.status)}</span></li>
        ))}
      </ul>
      <form onSubmit={addEnt} className="ir-form ir-form--inline">
        <label className="ir-field"><span>Entity legal name</span><input required value={ent.legal_name} onChange={(e) => setEnt({ ...ent, legal_name: e.target.value })} /></label>
        <label className="ir-field"><span>Your position</span><input value={ent.position} onChange={(e) => setEnt({ ...ent, position: e.target.value })} /></label>
        <label className="ir-field"><span>Authority</span>
          <select value={ent.authority} onChange={(e) => setEnt({ ...ent, authority: e.target.value })}>
            <option value="signer">Can sign</option><option value="representative">Representative</option><option value="viewer">Viewer</option>
          </select></label>
        <button className="ir-btn ir-btn--outline" disabled={busy}>Add entity</button>
      </form>
    </section>
  )
}

function BuyBox({ box, onSaved, onError }) {
  const v = (k) => (box && box.fields && box.fields[k] ? box.fields[k].value : null)
  const [edit, setEdit] = useState(!box)
  const [f, setF] = useState(() => ({ property_types: v('property_types') || [], strategies: v('strategies') || [], counties: v('counties') || [],
    cities: (v('cities') || []).join(', '), zips: (v('zips') || []).join(', '), min_price: v('min_price') ?? '', max_price: v('max_price') ?? '',
    min_units: v('min_units') ?? '', max_units: v('max_units') ?? '', rehab_tolerance: v('rehab_tolerance') || '',
    min_cash_flow: (box && box.criteria && box.criteria.min_cash_flow) ?? '', timeline: (box && box.criteria && box.criteria.timeline) || '',
    preferences: (box && box.criteria && box.criteria.preferences) || '' }))
  const [busy, setBusy] = useState(false)
  const toggle = (k, x) => setF({ ...f, [k]: f[k].includes(x) ? f[k].filter((y) => y !== x) : [...f[k], x] })
  const save = async (e) => {
    e.preventDefault(); setBusy(true)
    const { min_cash_flow: mcf, timeline, preferences, ...rest } = f
    const cash = String(mcf).replace(/[^0-9.]/g, '')
    const body = { ...rest, cities: f.cities.split(',').map((s) => s.trim()).filter(Boolean), zips: f.zips.split(',').map((s) => s.trim()).filter(Boolean),
      criteria: { min_cash_flow: cash ? Number(cash) : null, timeline: timeline || null, preferences: preferences.trim() || null } }
    try {
      if (box) await inv.put(`/investor/me/buy-boxes/${box.id}`, body); else await inv.post('/investor/me/buy-boxes', body)
      setEdit(false); onSaved('Criteria saved - that confirms them. New deals are matched against them.')
    } catch (x) { onError(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="ir-box">
      {box ? (
        <div className="ir-box__head"><b>{box.label}</b>
          <span className={`ir-pill${box.confirmed_at ? ' ir-pill--ok' : ''}`}>{box.confirmed_at ? 'Confirmed by you' : 'Please review'}</span>
          {!edit ? <button type="button" className="ir-btn ir-btn--outline ir-btn--sm" onClick={() => setEdit(true)}>{box.confirmed_at ? 'Edit' : 'Review & confirm'}</button> : null}</div>
      ) : null}
      {box && !edit ? (
        <dl className="ir-dl">
          {Object.entries(box.fields).filter(([k, x]) => x.label !== 'UNKNOWN' || ALWAYS.includes(k)).map(([k, x]) => (
            <div key={k}><dt>{BOX_LABEL[k] || cap(k)}</dt>
              <dd>{Array.isArray(x.value) ? (x.value.length ? x.value.map(words).join(', ') : '—') : k.includes('price') ? usd(x.value) : (x.value ?? '—')} <Label l={x.label} /></dd></div>
          ))}
        </dl>
      ) : (
        <form onSubmit={save} className="ir-form">
          <fieldset className="ir-field"><legend>Property types</legend>
            <div className="ir-pills">{TYPES.map((t) => <button type="button" key={t} aria-pressed={f.property_types.includes(t)} className={f.property_types.includes(t) ? 'is-on' : ''} onClick={() => toggle('property_types', t)}>{cap(t)}</button>)}</div></fieldset>
          <fieldset className="ir-field"><legend>Strategies</legend>
            <div className="ir-pills">{STRATS.map((t) => <button type="button" key={t} aria-pressed={f.strategies.includes(t)} className={f.strategies.includes(t) ? 'is-on' : ''} onClick={() => toggle('strategies', t)}>{cap(t)}</button>)}</div></fieldset>
          <fieldset className="ir-field"><legend>Counties</legend>
            <div className="ir-pills">{COUNTIES.map((t) => <button type="button" key={t} aria-pressed={f.counties.includes(t)} className={f.counties.includes(t) ? 'is-on' : ''} onClick={() => toggle('counties', t)}>{t}</button>)}</div></fieldset>
          <div className="ir-form--grid">
            <label className="ir-field"><span>Cities / neighborhoods (comma separated)</span><input value={f.cities} onChange={(e) => setF({ ...f, cities: e.target.value })} /></label>
            <label className="ir-field"><span>ZIP codes</span><input value={f.zips} onChange={(e) => setF({ ...f, zips: e.target.value })} /></label>
            <label className="ir-field"><span>Min price</span><input inputMode="numeric" value={f.min_price} onChange={(e) => setF({ ...f, min_price: e.target.value })} /></label>
            <label className="ir-field"><span>Max price</span><input inputMode="numeric" value={f.max_price} onChange={(e) => setF({ ...f, max_price: e.target.value })} /></label>
            {f.property_types.includes('multifamily') ? <>
              <label className="ir-field"><span>Min units</span><input inputMode="numeric" value={f.min_units} onChange={(e) => setF({ ...f, min_units: e.target.value })} /></label>
              <label className="ir-field"><span>Max units</span><input inputMode="numeric" value={f.max_units} onChange={(e) => setF({ ...f, max_units: e.target.value })} /></label>
            </> : null}
            <label className="ir-field"><span>Rehab you will take on</span>
              <select value={f.rehab_tolerance} onChange={(e) => setF({ ...f, rehab_tolerance: e.target.value })}>
                <option value="">Not sure</option>{['light', 'moderate', 'heavy', 'full_gut'].map((k) => <option key={k} value={k}>{cap(k)}</option>)}
              </select></label>
            <label className="ir-field"><span>Minimum monthly cash flow ($)</span><input inputMode="numeric" value={f.min_cash_flow} onChange={(e) => setF({ ...f, min_cash_flow: e.target.value })} /></label>
            <label className="ir-field"><span>Timeline to buy</span>
              <select value={f.timeline} onChange={(e) => setF({ ...f, timeline: e.target.value })}>
                <option value="">Not sure</option>{Object.entries(TIMELINE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select></label>
          </div>
          <label className="ir-field"><span>Buying preferences (anything else we should know)</span>
            <textarea rows={2} maxLength={500} value={f.preferences} onChange={(e) => setF({ ...f, preferences: e.target.value })} /></label>
          <p className="ir-muted ir-small">{(box && box.note) || 'Funding is never part of a buy box.'}</p>
          <div className="ir-actions"><button className="ir-btn ir-btn--primary" disabled={busy}>Save and confirm</button>
            {box ? <button type="button" className="ir-btn ir-btn--quiet" onClick={() => setEdit(false)}>Cancel</button> : null}</div>
        </form>
      )}
    </div>
  )
}

// ── owner preview (staff session, read-only) ───────────────────────────────
function Preview() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [q, setQ] = useState('')
  const [market, setMarket] = useState('')
  useEffect(() => {
    staffApi.get('/wholesale/deal-room/portal-preview').then(setD).catch((e) => setErr(e.message || (engine() ? 'Sign in to EvoSys to preview your Deal Room.' : 'Sign in to preview your Deal Room.')))
  }, [])
  const dash = useMemo(() => {
    if (!d) return null
    const opps = d.opportunities.map((o) => ({ ...o, cover_photo: (o.photos || [])[0], match: { score: null }, hold: null, saved: false }))
    const prices = opps.map((o) => o.asking_price).filter(Boolean)
    const week = Date.now() - 7 * 86400000
    return { room: d.room, welcome: { name: 'Investor' }, opportunities: opps,
      counts: { new_this_week: opps.filter((o) => o.published_at && new Date(o.published_at).getTime() >= week).length, in_review: 0, offers: 0, saved: 0,
        matching: opps.length, average_match: null, price_min: prices.length ? Math.min(...prices) : null, price_max: prices.length ? Math.max(...prices) : null } }
  }, [d])
  if (err) return <div className="ir-gate ir-gate--single"><main className="ir-gate__panel"><Brandmark /><Note kind="error">{err}</Note></main></div>
  if (!dash) return <Loading label="Building the preview" />
  return (
    <div className="ir-app ir-app--preview">
      <Sidebar room={dash.room} counts={dash.counts} me={null} preview />
      <div className="ir-frame">
        <div className="ir-previewbar" role="status">Owner preview - this is what investors see. {d.notice}</div>
        <Topbar me={null} counts={dash.counts} room={dash.room} q={q} setQ={setQ} market={market} setMarket={setMarket} preview />
        <main className="ir-main" id="ir-main">
          <DashboardView dash={dash} me={null} q={q} market={market} preview
                         concierge={<section className="ir-panel ir-concierge"><div className="ir-panel__head"><h2 className="ir-h3"><span className="ir-con__mark" aria-hidden="true"><Ico n="spark" size={16} /></span>AI Investment Concierge
                           </h2><span className={`ir-online${dash.room.ai_available ? '' : ' is-off'}`}>{dash.room.ai_available ? 'AI available' : dash.room.ai_concierge ? 'Automatic replies' : 'Team replies'}</span></div>
                           <ol className="ir-con__msgs"><li className="ir-con__m ir-con__m--ai"><span className="ir-con__av" aria-hidden="true"><Ico n="spark" size={15} /></span><div><span className="ir-con__who is-auto">Example</span><p>{dash.room.welcome_title || 'Welcome to your Deal Room'}. I'm your investment concierge - I learn what you buy and answer questions from what the team publishes.</p></div></li></ol>
                           <div className="ir-con__quick">{['Find matching deals', 'Explain a property', 'Update my criteria', 'Talk to a human'].map((x) => <button key={x} type="button" disabled>{x}</button>)}</div></section>}
                         support={<section className="ir-panel"><div className="ir-panel__head"><h2 className="ir-h3"><Ico n="msg" />Messages &amp; Support</h2></div>
                           <p className="ir-small ir-muted">Each investor sees their own conversations with the concierge and your team, with read and unread status.</p>
                           {dash.room.support_email || dash.room.support_phone ? <p className="ir-small">Support: {[dash.room.support_email, dash.room.support_phone].filter(Boolean).join(' · ')}</p> : null}</section>} />
        </main>
      </div>
    </div>
  )
}

// ── entry ──────────────────────────────────────────────────────────────────
function RequireInvestor({ children }) {
  const loc = useLocation()
  if (!investorToken()) return <Navigate to={`/investors/login?next=${encodeURIComponent(loc.pathname)}`} replace />
  return children
}

export default function InvestorPortal() {
  useEffect(() => {
    const prev = document.title
    document.title = 'Investor Portal'
    return () => { document.title = prev }
  }, [])
  return (
    <div className="ir-root">
      <Routes>
        <Route path="invite/:token" element={<Invite />} />
        <Route path="login" element={<Login />} />
        <Route path="preview" element={<Preview />} />
        <Route path="*" element={<RequireInvestor><Shell /></RequireInvestor>} />
      </Routes>
    </div>
  )
}
