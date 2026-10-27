/* PRIVATE BUYER DEAL ROOM - the investor's portal (#415).
 *
 * A restricted experience for invited investors only, on the SAME backend as
 * Wholesale: every opportunity, message, document and signature here is a row
 * the staff screens already own (buyer, buy box, outreach, file, envelope).
 * The server decides what an investor may see (/investor/*, fenced in
 * app/deps.py); this page never widens it.
 *
 * Routes (all under /investors - /investor/:token is the older Buyer Room link):
 *   /investors/invite/:token   accept an invitation (single use)
 *   /investors/login           sign in
 *   /investors                 dashboard
 *   /investors/opportunities[/:id]
 *   /investors/messages[/:id]
 *   /investors/documents
 *   /investors/profile
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
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

function Photo({ listingId, fileId, alt, className = '' }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    let url = null
    let alive = true
    if (listingId && fileId) {
      objectUrl(`/investor/opportunities/${listingId}/photos/${fileId}`)
        .then((u) => { url = u; if (alive) setSrc(u); else URL.revokeObjectURL(u) }).catch(() => {})
    }
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [listingId, fileId])
  return (
    <div className={`ir-photo ${className}`}>
      {src ? <img src={src} alt={alt || ''} /> : <span className="ir-photo__ph" aria-hidden="true">◇</span>}
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
    <span className={`ir-count${compact ? ' ir-count--sm' : ''}`} role="timer" aria-live="off"
          aria-label={`Exclusive review ends ${new Date(endsAt).toLocaleString()}`}>
      {c.left > 0 ? c.text : 'Ended'}
    </span>
  )
}

function Match({ score, size = 'md' }) {
  if (score === null || score === undefined) return <span className="ir-match ir-match--none">No buy box yet</span>
  const tone = score >= 80 ? 'hi' : score >= 60 ? 'mid' : 'lo'
  return (
    <span className={`ir-match ir-match--${tone} ir-match--${size}`} title="How well this property fits the buy box you gave us">
      <b>{score}<i>%</i></b><small>match</small>
    </span>
  )
}

const FACT_LABEL = { bedrooms: 'Beds', bathrooms: 'Baths', square_feet: 'Sq ft', year_built: 'Built',
  unit_count: 'Units', acreage: 'Acres', building_sqft: 'Building sq ft' }

function Facts({ facts }) {
  const items = Object.entries(facts || {}).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (!items.length) return null
  return (
    <ul className="ir-facts">
      {items.map(([k, v]) => <li key={k}><b>{typeof v === 'number' ? v.toLocaleString('en-US') : v}</b> {FACT_LABEL[k] || cap(k)}</li>)}
    </ul>
  )
}

const addr1 = (a) => (a && a.line1) || 'Address on request'
const addr2 = (a) => [a && a.city, a && a.state, a && a.zip_code].filter(Boolean).join(', ')

function OppCard({ o }) {
  const yourHold = o.hold && o.hold.yours && o.hold.status === 'active'
  return (
    <Link to={`/investors/opportunities/${o.id}`} className="ir-card" aria-label={`${addr1(o.address)}, ${addr2(o.address)}`}>
      <div className="ir-card__media">
        <Photo listingId={o.id} fileId={o.cover_photo} alt="" />
        <div className="ir-card__chips">
          {o.is_test ? <span className="ir-chip ir-chip--practice">Practice</span> : null}
          {yourHold ? <span className="ir-chip ir-chip--gold">Your exclusive review</span> : null}
          {o.you && o.you.status ? <span className="ir-chip">{cap(o.you.status)}</span> : null}
        </div>
      </div>
      <div className="ir-card__body">
        <div className="ir-card__top">
          <div>
            <h3 className="ir-card__addr">{addr1(o.address)}</h3>
            <p className="ir-card__place">{addr2(o.address)}{o.address && o.address.county ? ` · ${o.address.county} County` : ''}</p>
          </div>
          <Match score={o.match && o.match.score} size="sm" />
        </div>
        <Facts facts={o.facts} />
        <div className="ir-card__foot">
          <span className="ir-price">{usd(o.asking_price)}</span>
          {yourHold ? <Countdown endsAt={o.hold.ends_at} compact /> : <span className="ir-muted">{cap(o.category || o.address?.property_type)}</span>}
        </div>
      </div>
    </Link>
  )
}

// ── public: invitation and sign in ─────────────────────────────────────────
function Brandmark({ org }) {
  return (
    <div className="ir-brand">
      <span className="ir-brand__mark" aria-hidden="true">◆</span>
      <span><b>{org || 'Private'}</b><small>Deal Room</small></span>
    </div>
  )
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
      <div className="ir-gate__art" aria-hidden="true"><div className="ir-gate__glow" /></div>
      <main className="ir-gate__panel">
        <Brandmark org={p && p.organization} />
        {!p && !err ? <Loading label="Checking your invitation" /> : null}
        {p ? (
          <form onSubmit={submit} className="ir-form">
            <p className="ir-eyebrow">Private invitation</p>
            <h1 className="ir-h1">{p.name ? `Welcome, ${p.name.split(' ')[0]}.` : 'Welcome.'}</h1>
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
            <button className="ir-btn ir-btn--gold ir-btn--block" disabled={busy}>{busy ? 'One moment…' : register ? 'Enter the Deal Room' : 'Accept invitation'}</button>
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
      <div className="ir-gate__art" aria-hidden="true"><div className="ir-gate__glow" /></div>
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
          <button className="ir-btn ir-btn--gold ir-btn--block" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
          <p className="ir-muted ir-small">Access is by invitation. If you need a new link, contact the team that invited you.</p>
        </form>
      </main>
    </div>
  )
}

// ── the signed-in shell ────────────────────────────────────────────────────
const NAV = [
  { to: '/investors', label: 'Overview', icon: '◈', end: true },
  { to: '/investors/opportunities', label: 'Opportunities', short: 'Deals', icon: '▣' },
  { to: '/investors/messages', label: 'Messages', icon: '✉' },
  { to: '/investors/documents', label: 'Documents', icon: '▤' },
  { to: '/investors/profile', label: 'Profile', icon: '◎' },
]

function Shell() {
  const [me, meErr, reloadMe] = useLoad('/investor/me')
  const [dash, , reloadDash] = useLoad('/investor/dashboard')
  const nav = useNavigate()
  const signOut = async () => {
    try { await inv.post('/auth/logout', {}) } catch { /* already gone */ }
    setInvestorToken(null)
    nav('/investors/login', { replace: true })
  }
  const unread = dash && dash.counts ? dash.counts.unread : 0
  const name = me && me.account ? (me.account.preferred_name || me.account.legal_name) : ''
  const reload = useCallback(() => { reloadMe(); reloadDash() }, [reloadMe, reloadDash])
  return (
    <div className="ir-app">
      <a className="ir-skip" href="#ir-main">Skip to content</a>
      <aside className="ir-side" aria-label="Deal Room">
        <Brandmark org={me && me.organization} />
        <nav className="ir-nav" aria-label="Deal Room sections">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `ir-nav__a${isActive ? ' is-on' : ''}`}>
              <span className="ir-nav__i" aria-hidden="true">{n.icon}</span><span className="ir-nav__l">{n.short ? <><span className="ir-long">{n.label}</span><span className="ir-short">{n.short}</span></> : n.label}</span>
              {n.label === 'Messages' && unread ? <span className="ir-badge" aria-label={`${unread} unread`}>{unread}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="ir-side__foot">
          {me && me.account && me.account.is_test ? <span className="ir-chip ir-chip--practice">Practice account</span> : null}
          <div className="ir-who"><span className="ir-avatar" aria-hidden="true">{(name || '?').slice(0, 1)}</span>
            <span><b>{name || '…'}</b><small>{me && me.account && me.account.company_legal_name}</small></span></div>
          <button type="button" className="ir-link" onClick={signOut}>Sign out</button>
        </div>
      </aside>
      <main className="ir-main" id="ir-main" tabIndex={-1}>
        <div className="ir-mtop"><Brandmark org={me && me.organization} />
          <button type="button" className="ir-link" onClick={signOut}>Sign out</button></div>
        <Note kind="error">{meErr}</Note>
        <Routes>
          <Route index element={<Overview me={me} dash={dash} />} />
          <Route path="opportunities" element={<Opportunities />} />
          <Route path="opportunities/:id" element={<Opportunity onChange={reload} />} />
          <Route path="messages" element={<Messages onChange={reloadDash} />} />
          <Route path="messages/:id" element={<Messages onChange={reloadDash} />} />
          <Route path="documents" element={<Documents onChange={reload} />} />
          <Route path="profile" element={<Profile me={me} onChange={reload} />} />
          <Route path="*" element={<Navigate to="/investors" replace />} />
        </Routes>
      </main>
    </div>
  )
}

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

function Overview({ me, dash }) {
  if (!dash) return <Loading />
  const ob = dash.onboarding || {}
  const steps = [
    { k: 'identity', l: 'Confirm who you buy as', to: '/investors/profile' },
    { k: 'buy_box', l: 'Confirm your buy box', to: '/investors/profile#buy-box' },
    { k: 'proof_of_funds', l: 'Upload proof of funds', to: '/investors/documents' },
  ]
  const left = steps.filter((s) => !ob[s.k])
  const opps = dash.opportunities || []
  return (
    <>
      <header className="ir-hero">
        <div>
          <p className="ir-eyebrow">{dash.welcome && dash.welcome.organization} · Private Deal Room</p>
          <h1 className="ir-h1">{greeting()}{dash.welcome && dash.welcome.name ? `, ${dash.welcome.name}` : ''}.</h1>
          <p className="ir-lead">{opps.length
            ? `${opps.length} opportunit${opps.length === 1 ? 'y' : 'ies'} selected for you. Ask your concierge anything - answers come only from what the team has published.`
            : 'No opportunities match you right now. When one does, it appears here first.'}</p>
        </div>
        <div className="ir-stats" role="list">
          {[['Opportunities', dash.counts.opportunities, '/investors/opportunities'],
            ['In exclusive review', dash.counts.in_review, '/investors/opportunities'],
            ['To sign', dash.counts.to_sign, '/investors/documents'],
            ['Unread', dash.counts.unread, '/investors/messages']].map(([l, v, to]) => (
            <Link role="listitem" key={l} to={to} className="ir-stat"><b>{v}</b><span>{l}</span></Link>
          ))}
        </div>
      </header>
      {left.length ? (
        <section className="ir-panel ir-onboard" aria-labelledby="ob-h">
          <h2 id="ob-h" className="ir-h2">Finish setting up</h2>
          <p className="ir-muted ir-small">So we only send you what fits - and so the team can move fast when one does.</p>
          <ol className="ir-steps">
            {steps.map((s) => (
              <li key={s.k} className={ob[s.k] ? 'is-done' : ''}>
                <span className="ir-steps__dot" aria-hidden="true">{ob[s.k] ? '✓' : ''}</span>
                {ob[s.k] ? <span>{s.l}</span> : <Link to={s.to}>{s.l}</Link>}
                <span className="ir-sr">{ob[s.k] ? 'done' : 'to do'}</span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      <section aria-labelledby="ops-h">
        <div className="ir-section-head"><h2 id="ops-h" className="ir-h2">Selected for you</h2>
          {opps.length ? <Link to="/investors/opportunities" className="ir-link">View all</Link> : null}</div>
        {opps.length ? <div className="ir-grid">{opps.slice(0, 6).map((o) => <OppCard key={o.id} o={o} />)}</div>
          : <div className="ir-empty">Nothing published for you yet.</div>}
      </section>
      {me && me.funding && !me.funding.documented ? (
        <p className="ir-muted ir-small ir-foot-note">A budget you tell us is never treated as verified funding. Proof of funds is reviewed by a person.</p>
      ) : null}
    </>
  )
}

function Opportunities() {
  const [d, err] = useLoad('/investor/opportunities')
  const [filter, setFilter] = useState('all')
  const items = useMemo(() => {
    const all = (d && d.items) || []
    if (filter === 'review') return all.filter((o) => o.hold && o.hold.yours)
    if (filter === 'responded') return all.filter((o) => o.you && o.you.status)
    return all
  }, [d, filter])
  return (
    <>
      <header className="ir-pagehead"><p className="ir-eyebrow">Opportunities</p>
        <h1 className="ir-h1">Deals published to you</h1>
        <p className="ir-lead">Only properties the team approved for investors - matched against your buy box.</p></header>
      <div className="ir-tabs" role="tablist" aria-label="Filter opportunities">
        {[['all', 'All'], ['review', 'My exclusive reviews'], ['responded', 'Responded']].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={filter === k} className={filter === k ? 'is-on' : ''} onClick={() => setFilter(k)}>{l}</button>
        ))}
      </div>
      <Note kind="error">{err}</Note>
      {!d ? <Loading /> : items.length ? <div className="ir-grid">{items.map((o) => <OppCard key={o.id} o={o} />)}</div>
        : <div className="ir-empty">Nothing here yet.</div>}
    </>
  )
}

function Opportunity({ onChange }) {
  const { id } = useParams()
  const nav = useNavigate()
  const [o, err, reload] = useLoad(`/investor/opportunities/${id}`)
  const [photo, setPhoto] = useState(0)
  const [mode, setMode] = useState(null)        // offer | review
  const [f, setF] = useState({ amount: '', financing: 'cash', note: '' })
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  if (err) return <><Link to="/investors/opportunities" className="ir-back">← Opportunities</Link><Note kind="error">{err}</Note></>
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
    try {
      const t = await inv.post('/investor/threads', { kind: 'deal', listing_id: id })
      nav(`/investors/messages/${t.id}`)
    } catch (x) { setMsg({ kind: 'error', text: x.message }) }
  }
  const status = o.you && o.you.status
  return (
    <>
      <Link to="/investors/opportunities" className="ir-back">← Opportunities</Link>
      {mine ? (
        <div className="ir-hold" role="region" aria-label="Your exclusive review">
          <div><p className="ir-eyebrow">Your exclusive review</p>
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
                <button key={p.id} role="listitem" className={i === photo ? 'is-on' : ''} onClick={() => setPhoto(i)}
                        aria-label={`Show photo ${i + 1}`} aria-pressed={i === photo}>
                  <Photo listingId={id} fileId={p.id} alt="" />
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="ir-detail__side">
          {o.is_test ? <span className="ir-chip ir-chip--practice">Practice listing - not a real offer</span> : null}
          <h1 className="ir-h1 ir-h1--addr">{addr1(room.property)}</h1>
          <p className="ir-lead">{addr2(room.property)}{room.property && room.property.county ? ` · ${room.property.county} County` : ''}</p>
          <div className="ir-pricebox">
            <div><small>Asking</small><b>{usd(room.asking_price)}</b></div>
            {'arv' in room ? <div><small>Est. after repair</small><b>{usd(room.arv)}</b></div> : null}
            {'estimated_repairs' in room ? <div><small>Est. repairs</small><b>{usd(room.estimated_repairs)}</b></div> : null}
          </div>
          <Facts facts={o.facts} />
          <div className="ir-matchbox">
            <Match score={o.match && o.match.score} />
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
              <button className="ir-btn ir-btn--gold" disabled={busy} onClick={() => setMode(mode === 'offer' ? null : 'offer')}>Make an offer</button>
              <button className="ir-btn" disabled={busy} onClick={() => act('interested')}>I&apos;m interested</button>
              <button className="ir-btn" onClick={ask}>Ask a question</button>
              {!hold && !o.hold_requested ? <button className="ir-btn ir-btn--ghost" onClick={() => setMode(mode === 'review' ? null : 'review')}>Request exclusive review</button> : null}
              {o.hold_requested ? <span className="ir-muted ir-small">Exclusive review requested - waiting for a decision.</span> : null}
              <button className="ir-btn ir-btn--quiet" disabled={busy} onClick={() => act('pass')}>Pass</button>
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
              <button className="ir-btn ir-btn--gold" disabled={busy}>Send offer</button>
            </form>
          ) : null}
          {mode === 'review' ? (
            <div className="ir-panel ir-form ir-form--tight">
              <h2 className="ir-h3">Request exclusive review</h2>
              <p className="ir-small">{o.hold_notice}</p>
              <label className="ir-field"><span>Why you need the time (optional)</span>
                <textarea rows={2} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
              <button className="ir-btn ir-btn--gold" disabled={busy} onClick={askReview}>Send request</button>
            </div>
          ) : null}
        </div>
      </div>
      <section className="ir-panel">
        <h2 className="ir-h2">About this property</h2>
        <p className="ir-prose">{room.summary || 'No summary published.'}</p>
        {room.condition_summary ? <><h3 className="ir-h3">Condition</h3><p className="ir-prose">{room.condition_summary}</p></> : null}
        {(room.documents || []).length ? (
          <><h3 className="ir-h3">Documents</h3>
            <ul className="ir-files">{room.documents.map((dc) => <li key={dc.id}><DocLink path={`/investor/opportunities/${id}/documents/${dc.id}`} name={dc.title || 'Document'} /></li>)}</ul></>
        ) : null}
        {room.closing && (room.closing.target_close || room.closing.title_company) ? (
          <p className="ir-small ir-muted">Target close {room.closing.target_close || '—'}{room.closing.title_company ? ` · Title: ${room.closing.title_company}` : ''}</p>
        ) : null}
      </section>
      {room.disclosure ? (
        <section className="ir-disclosure" aria-label="Disclosure">
          <h2 className="ir-h3">{room.disclosure.title}</h2>
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

// ── messages ───────────────────────────────────────────────────────────────
const ROLE = { ai: 'Concierge', staff: 'Team', system: 'Deal Room', buyer: 'You' }
const KIND = { concierge: 'Investment concierge', relationship: 'Your team', deal: 'Property questions' }

function Messages({ onChange }) {
  const { id } = useParams()
  const nav = useNavigate()
  const [d, err, reload] = useLoad('/investor/threads')
  const threads = (d && d.threads) || []
  const current = id ? threads.find((t) => t.id === id) : null
  useEffect(() => {
    if (!id && threads.length && window.matchMedia('(min-width: 900px)').matches) nav(`/investors/messages/${threads[0].id}`, { replace: true })
  }, [id, threads, nav])
  return (
    <>
      <header className="ir-pagehead"><p className="ir-eyebrow">Messages</p><h1 className="ir-h1">Your conversations</h1></header>
      <Note kind="error">{err}</Note>
      <div className={`ir-inbox${id ? ' has-thread' : ''}`}>
        <ul className="ir-threads" aria-label="Conversations">
          {threads.map((t) => {
            const last = t.messages && t.messages[t.messages.length - 1]
            return (
              <li key={t.id}>
                <Link to={`/investors/messages/${t.id}`} className={t.id === id ? 'is-on' : ''} aria-current={t.id === id ? 'page' : undefined}>
                  <span className="ir-threads__k">{KIND[t.kind] || cap(t.kind)}{t.unread ? <span className="ir-badge">{t.unread}</span> : null}</span>
                  <span className="ir-threads__l">{last ? last.text : ''}</span>
                  <small>{when(t.last_message_at)}</small>
                </Link>
              </li>
            )
          })}
          {!d ? <li><Loading /></li> : null}
        </ul>
        {id ? <Thread key={id} id={id} summary={current} onChange={() => { reload(); onChange && onChange() }} /> : null}
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
  useEffect(() => { if (end.current && end.current.scrollIntoView) end.current.scrollIntoView({ block: 'end' }) }, [t])
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
        <Link to="/investors/messages" className="ir-back ir-back--inline">←</Link>
        <div><b>{KIND[t.kind] || cap(t.kind)}</b>
          <small>{t.ai_active ? 'Concierge answers from published information; ask for a person any time.' : 'A person from the team is handling this conversation.'}</small></div>
      </header>
      <ol className="ir-msgs" aria-live="polite">
        {(t.messages || []).map((m, i) => (
          <li key={i} className={`ir-msg ir-msg--${m.role}`}>
            <span className="ir-msg__who">{m.role === 'staff' && m.by ? m.by : ROLE[m.role] || cap(m.role)} · {when(m.at)}</span>
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
        <button className="ir-btn ir-btn--gold" disabled={busy || !text.trim()}>{busy ? 'Sending…' : 'Send'}</button>
      </form>
      <Note kind="error">{sendErr}</Note>
      <p className="ir-muted ir-small ir-pad">Reply STOP to stop the concierge. Labels: BUYER STATED is what you told us; DOCUMENT VERIFIED was checked by a person.</p>
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
      <header className="ir-pagehead"><p className="ir-eyebrow">Documents</p><h1 className="ir-h1">Documents &amp; signatures</h1></header>
      <Note kind="error">{err || sErr}</Note>
      <section className="ir-panel" aria-labelledby="sig-h">
        <h2 id="sig-h" className="ir-h2">To sign</h2>
        {!sigs ? <Loading /> : (sigs.documents || []).length ? (
          <ul className="ir-rows">
            {sigs.documents.map((s) => (
              <li key={s.envelope_id || s.id}>
                <div><b>{s.title}</b><small>{s.completed ? 'Completed by everyone' : s.your_turn ? `Your turn - you sign as ${s.your_role}` : (s.your_status === 'signed' ? 'You signed - waiting for the other party' : 'Waiting for another signer')}</small></div>
                {s.your_turn && !s.completed ? <button className="ir-btn ir-btn--gold" onClick={() => sign(s.envelope_id || s.id)}>Review &amp; sign</button>
                  : <span className={`ir-chip${s.completed ? ' ir-chip--ok' : ''}`}>{s.completed ? 'Signed' : 'Pending'}</span>}
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
        <h3 className="ir-h3">Your files</h3>
        {!d ? <Loading /> : (d.files || []).length ? (
          <ul className="ir-rows">
            {d.files.map((x) => (
              <li key={x.id}>
                <div><DocLink path={`/investor/files/${x.id}`} name={x.name} />
                  <small>{CATEGORY[x.category] || cap(x.category)} · {fileSize(x.size)} · {x.from_you ? 'from you' : 'from the team'} · {when(x.uploaded_at)}</small></div>
                <span className={`ir-chip${x.review_status === 'accepted' ? ' ir-chip--ok' : x.review_status === 'rejected' || x.scan_status === 'blocked' ? ' ir-chip--bad' : ''}`}>
                  {x.from_you ? (REVIEW[x.review_status] || cap(x.review_status)) : 'Shared with you'}</span>
              </li>
            ))}
          </ul>
        ) : <p className="ir-muted">No files yet.</p>}
      </section>
    </>
  )
}

// ── profile ────────────────────────────────────────────────────────────────
const LABEL_TONE = { 'BUYER STATED': 'stated', 'DOCUMENT VERIFIED': 'ok', 'SYSTEM INFERRED': 'inferred', UNKNOWN: 'unknown' }
const BOX_LABEL = { property_types: 'Property types', strategies: 'Strategies', states: 'States', counties: 'Counties',
  cities: 'Cities', zips: 'ZIP codes', min_price: 'Min price', max_price: 'Max price', min_units: 'Min units',
  max_units: 'Max units', min_acres: 'Min acres', max_acres: 'Max acres', rehab_tolerance: 'Rehab tolerance', min_cap_rate: 'Min cap rate' }
const ALWAYS = ['property_types', 'counties', 'max_price']
const TYPES = ['single_family', 'multifamily', 'townhouse', 'condo', 'land', 'commercial', 'mixed_use', 'mobile_home']
const COUNTIES = ['Dallas', 'Tarrant', 'Collin', 'Denton', 'Rockwall', 'Ellis', 'Johnson', 'Kaufman', 'Parker']

function Label({ l }) { return <span className={`ir-lbl ir-lbl--${LABEL_TONE[l] || 'unknown'}`}>{l}</span> }

function Profile({ me, onChange }) {
  const [msg, setMsg] = useState(null)
  if (!me) return <Loading />
  return (
    <>
      <header className="ir-pagehead"><p className="ir-eyebrow">Profile</p><h1 className="ir-h1">Who you are and what you buy</h1>
        <p className="ir-lead">Everything here is labeled by where it came from. Nothing you tell us is treated as verified until a person checks a document.</p></header>
      <Note kind={msg && msg.kind}>{msg && msg.text}</Note>
      <Identity me={me} onSaved={(t) => { setMsg({ kind: 'ok', text: t }); onChange() }} onError={(t) => setMsg({ kind: 'error', text: t })} />
      <section className="ir-panel" id="buy-box" aria-labelledby="bb-h">
        <h2 id="bb-h" className="ir-h2">Your buy box</h2>
        {(me.buy_boxes || []).length ? me.buy_boxes.map((b) => <BuyBox key={b.id} box={b} onSaved={(t) => { setMsg({ kind: 'ok', text: t }); onChange() }}
                                                                   onError={(t) => setMsg({ kind: 'error', text: t })} />)
          : <BuyBox box={null} onSaved={(t) => { setMsg({ kind: 'ok', text: t }); onChange() }} onError={(t) => setMsg({ kind: 'error', text: t })} />}
      </section>
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
      <h2 id="id-h" className="ir-h2">Identity {a.identity_complete ? <span className="ir-chip ir-chip--ok">Complete</span> : null}
        {a.email_verified ? <span className="ir-chip">Email verified</span> : null}</h2>
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
        <div className="ir-span2"><button className="ir-btn ir-btn--gold" disabled={busy}>Save identity</button></div>
      </form>
      <h3 className="ir-h3">Entities you buy through</h3>
      <ul className="ir-rows">
        {(me.entities || []).map((x) => (
          <li key={x.id}><div><b>{x.name}</b><small>{[x.position, x.authority && cap(x.authority)].filter(Boolean).join(' · ')}</small></div>
            <span className={`ir-chip${x.status === 'confirmed' ? ' ir-chip--ok' : ''}`}>{x.status === 'needs_review' ? 'Being reviewed' : cap(x.status)}</span></li>
        ))}
      </ul>
      <form onSubmit={addEnt} className="ir-form ir-form--inline">
        <label className="ir-field"><span>Entity legal name</span><input required value={ent.legal_name} onChange={(e) => setEnt({ ...ent, legal_name: e.target.value })} /></label>
        <label className="ir-field"><span>Your position</span><input value={ent.position} onChange={(e) => setEnt({ ...ent, position: e.target.value })} /></label>
        <label className="ir-field"><span>Authority</span>
          <select value={ent.authority} onChange={(e) => setEnt({ ...ent, authority: e.target.value })}>
            <option value="signer">Can sign</option><option value="representative">Representative</option><option value="viewer">Viewer</option>
          </select></label>
        <button className="ir-btn" disabled={busy}>Add entity</button>
      </form>
    </section>
  )
}

function BuyBox({ box, onSaved, onError }) {
  const v = (k) => (box && box.fields && box.fields[k] ? box.fields[k].value : null)
  const [edit, setEdit] = useState(!box)
  const [f, setF] = useState(() => ({ property_types: v('property_types') || [], counties: v('counties') || [],
    cities: (v('cities') || []).join(', '), zips: (v('zips') || []).join(', '), min_price: v('min_price') ?? '', max_price: v('max_price') ?? '',
    min_units: v('min_units') ?? '', max_units: v('max_units') ?? '', rehab_tolerance: v('rehab_tolerance') || '' }))
  const [busy, setBusy] = useState(false)
  const toggle = (k, x) => setF({ ...f, [k]: f[k].includes(x) ? f[k].filter((y) => y !== x) : [...f[k], x] })
  const save = async (e) => {
    e.preventDefault(); setBusy(true)
    const body = { ...f, cities: f.cities.split(',').map((s) => s.trim()).filter(Boolean), zips: f.zips.split(',').map((s) => s.trim()).filter(Boolean) }
    try {
      if (box) await inv.put(`/investor/me/buy-boxes/${box.id}`, body); else await inv.post('/investor/me/buy-boxes', body)
      setEdit(false); onSaved('Buy box saved - that confirms it. We will match new deals against it.')
    } catch (x) { onError(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="ir-box">
      {box ? (
        <div className="ir-box__head"><b>{box.label}</b>
          <span className={`ir-chip${box.confirmed_at ? ' ir-chip--ok' : ''}`}>{box.confirmed_at ? 'Confirmed by you' : 'Please review'}</span>
          {!edit ? <button className="ir-btn ir-btn--sm" onClick={() => setEdit(true)}>{box.confirmed_at ? 'Edit' : 'Review & confirm'}</button> : null}</div>
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
          <fieldset className="ir-field"><legend>Counties</legend>
            <div className="ir-pills">{COUNTIES.map((t) => <button type="button" key={t} aria-pressed={f.counties.includes(t)} className={f.counties.includes(t) ? 'is-on' : ''} onClick={() => toggle('counties', t)}>{t}</button>)}</div></fieldset>
          <div className="ir-form--grid">
            <label className="ir-field"><span>Cities (comma separated)</span><input value={f.cities} onChange={(e) => setF({ ...f, cities: e.target.value })} /></label>
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
          </div>
          <p className="ir-muted ir-small">{(box && box.note) || 'Funding is never part of a buy box.'}</p>
          <div className="ir-actions"><button className="ir-btn ir-btn--gold" disabled={busy}>Save and confirm</button>
            {box ? <button type="button" className="ir-btn ir-btn--quiet" onClick={() => setEdit(false)}>Cancel</button> : null}</div>
        </form>
      )}
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
    document.title = 'Private Deal Room'
    return () => { document.title = prev }
  }, [])
  return (
    <div className="ir-root">
      <Routes>
        <Route path="invite/:token" element={<Invite />} />
        <Route path="login" element={<Login />} />
        <Route path="*" element={<RequireInvestor><Shell /></RequireInvestor>} />
      </Routes>
    </div>
  )
}
