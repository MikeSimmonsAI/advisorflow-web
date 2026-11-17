/**
 * EVOSYS BUSINESS DEVELOPMENT (/program/evosys-business-development/*) - PR #663,
 * shown next to EvoSys Sales (/program/evosys-sales) on the same data so the two
 * versions can be compared.
 *
 * How EvoSys reaches out to people and businesses it wants to work with:
 * networking cards, referrals, inbound requests, researched lists. Add the
 * contacts, see them next to the leads already in the system, and build a
 * reach-out campaign around what you're working on.
 *
 * Today, Leads, Conversations, Outreach, Appointments, Results. Design:
 * "EvoSys Sales workflow dashboard redesign.png" (compact navy sidebar, EP
 * emblem, light work area, cobalt actions, gold only for what needs
 * attention). 
 *
 * The server is the authority (/sales/evosys/*): only the EvoSys Pro team
 * gets in, reps see their own leads, and only a manager can change, approve
 * or switch on a campaign.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import './evosysBD.css'

export const BASE = '/program/evosys-business-development'
const API = '/sales/evosys'
const EMBLEM = '/brands/evosyspro-emblem-96.png'
export const PRODUCT = 'EvoSys Business Development'
const TZ = 'America/Chicago'

// ── icons (inline, no new dependency) ────────────────────────────────────────
const P = {
  today: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  leads: 'M16 11a4 4 0 1 0-8 0M4 21a8 8 0 0 1 16 0M12 7a3 3 0 1 0 0-.01',
  chat: 'M4 5h16v11H8l-4 4z',
  send: 'M3 11 21 3l-8 18-2-8z',
  cal: 'M4 6h16v15H4zM4 10h16M8 3v4M16 3v4',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  phone: 'M5 3h4l2 5-3 2a11 11 0 0 0 6 6l2-3 5 2v4a2 2 0 0 1-2 2A17 17 0 0 1 3 5a2 2 0 0 1 2-2z',
  back: 'M15 18l-6-6 6-6',
  arrow: 'M9 6l6 6-6 6',
  reply: 'M10 9V5l-7 7 7 7v-4c5 0 8 1.5 11 5-1-6-4-10-11-11z',
  plus: 'M12 5v14M5 12h14',
  people: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M2 21a7 7 0 0 1 14 0M17 11a3 3 0 1 0 0-6M22 21a6 6 0 0 0-4-5.6',
  warn: 'M12 3 2 21h20zM12 10v5M12 18v.01',
  spark: 'M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z',
  off: 'M3 3l18 18M10.6 6.1A9 9 0 0 1 21 12a9 9 0 0 1-1.6 3.4M6 6.5A9 9 0 0 0 3 12c2 4 5.5 6 9 6 1.6 0 3.1-.4 4.4-1',
  tag: 'M3 12V3h9l9 9-9 9zM7.5 7.5v.01',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  branch: 'M6 3v12M18 9a3 3 0 1 0 0-.01M6 21a3 3 0 1 0 0-.01M18 12c0 4-6 3-12 6',
  upload: 'M12 16V4M6 10l6-6 6 6M4 20h16',
}
export function Icon({ name, size = 20 }) {
  return (
    <svg className="ebd-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name]} />
    </svg>
  )
}

const NAV = [
  { to: BASE, label: 'Today', icon: 'today', end: true },
  { to: `${BASE}/leads`, label: 'Leads', icon: 'leads' },
  { to: `${BASE}/conversations`, label: 'Conversations', short: 'Replies', icon: 'chat' },
  { to: `${BASE}/outreach`, label: 'Outreach', icon: 'send' },
  { to: `${BASE}/appointments`, label: 'Appointments', short: 'Calls', icon: 'phone' },
  { to: `${BASE}/results`, label: 'Results', icon: 'chart' },
]

export function errText(e) {
  if (typeof e?.detail === 'string') return e.detail
  if (e?.detail?.message) return e.detail.message
  return e?.message || 'Something went wrong. Please try again.'
}

function useLoad(path) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const load = useCallback(() => {
    if (!path) return Promise.resolve()
    setError(null)
    return api.get(path).then(setData).catch(e => setError(errText(e)))
  }, [path])
  useEffect(() => { load() }, [load])
  return { data, error, reload: load }
}

function useTitle(t) {
  useEffect(() => { document.title = `${t} · ${PRODUCT}` }, [t])
}

// Dates are shown with their time zone wherever the operator acts on them.
export function when(iso, opts) {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, opts || { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: TZ, timeZoneName: 'short' })
}
export function whenTZ(iso) {
  if (!iso) return ''
  return when(iso, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: TZ, timeZoneName: 'short' })
}
function day(iso) {
  return iso ? new Date(iso + (iso.length === 10 ? 'T12:00:00' : '')).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : ''
}

const TONE = { active: 'blue', replied: 'gold', booked: 'good', finished: 'muted', no_email: 'warn', none: 'muted',
               queued: 'muted', held: 'warn', opted_out: 'bad', email_failed: 'bad', manual: 'muted', stage: 'muted', not_in: 'muted' }
function Status({ o }) {
  if (!o) return null
  return <span className={'ebd-pill ' + (TONE[o.key] || 'muted')}>{o.label}</span>
}
const CAMP_TONE = { on: 'good', approved: 'blue', needs_approval: 'gold', draft: 'muted', archived: 'muted' }

function Box({ error, data, children }) {
  if (error) return <div className="ebd-error" role="alert">{error}</div>
  if (!data) return <div className="ebd-loading">Loading…</div>
  return children
}

function PageHead({ title, sub, children }) {
  return (
    <div className="ebd-head">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {children && <div className="ebd-head-acts">{children}</div>}
    </div>
  )
}

function CallBtn({ phone, label = 'Call' }) {
  if (!phone) return null
  return <a className="ebd-btn sm" href={'tel:' + phone} onClick={e => e.stopPropagation()}><Icon name="phone" size={16} />{label}</a>
}

// ── shell ────────────────────────────────────────────────────────────────────

export default function EvoSysBusinessDevelopmentShell() {
  const user = getCurrentUser() || {}
  const { pathname } = useLocation()
  const [adding, setAdding] = useState(false)
  const [bump, setBump] = useState(0)
  useEffect(() => { window.scrollTo?.(0, 0) }, [pathname])
  return (
    <div className="ebd">
      <aside className="ebd-side" aria-label={PRODUCT}>
        <div className="ebd-logo">
          <img src={EMBLEM} alt="EvoSys Pro" width="40" height="40" />
          <div><b>EvoSys Business</b><small>Development</small></div>
        </div>
        <nav className="ebd-nav">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => 'ebd-navlink' + (isActive ? ' on' : '')}>
              <Icon name={n.icon} />{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="ebd-side-foot">
          <div className="ebd-me">{user.full_name || user.email || ''}</div>
          <Link to="/workspaces" className="ebd-back"><Icon name="back" size={16} />Program Access</Link>
        </div>
      </aside>

      <header className="ebd-mtop">
        <img src={EMBLEM} alt="EvoSys Pro" width="32" height="32" />
        <b>EvoSys Business<small>Development</small></b>
        <Link to="/workspaces" className="ebd-back"><Icon name="back" size={14} />Programs</Link>
      </header>

      <main className="ebd-main"><Outlet context={{ openAdd: () => setAdding(true), bump }} /></main>

      <nav className="ebd-tabbar" aria-label={`${PRODUCT} sections`}>
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => 'ebd-tab' + (isActive ? ' on' : '')}>
            <Icon name={n.icon} size={20} /><span>{n.short || n.label}</span>
          </NavLink>
        ))}
      </nav>
      {adding && <AddContact onClose={() => setAdding(false)} onSaved={() => setBump(b => b + 1)} />}
    </div>
  )
}

// ── Add contacts ─────────────────────────────────────────────────────────────

const SOURCE_OPTS = [
  ['networking', 'Networking event / business card'], ['referral', 'Referral'], ['website', 'Website / inbound'],
  ['facebook', 'Facebook'], ['tiktok', 'TikTok'], ['instagram', 'Instagram / Meta'], ['google', 'Google'],
  ['cold_email', 'Researched'], ['other', 'Other'],
]
const BLANK = { contact_name: '', company_name: '', email: '', phone: '', role: '', industry: '', market: '',
                source: 'networking', source_detail: '', met_on: '', notes: '', intro_note: '', preferred_contact: '', website: '', city: '' }

export function AddContact({ onClose, onSaved }) {
  const [f, setF] = useState(() => {
    try { const last = JSON.parse(localStorage.getItem('ebd.lastEvent') || 'null'); return last ? { ...BLANK, ...last } : BLANK }
    catch { return BLANK }
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [dupes, setDupes] = useState(null)
  const [saved, setSaved] = useState([])
  const set = k => e => setF(x => ({ ...x, [k]: e.target.value }))
  const detailLabel = f.source === 'referral' ? 'Who referred them' : f.source === 'networking' ? 'Event' : 'Where you found them'
  async function save(force = false) {
    setBusy(true); setErr(null)
    const body = Object.fromEntries(Object.entries({ ...f, force }).filter(([, v]) => v !== ''))
    try {
      const r = await api.post(`${API}/contacts`, body)
      setSaved(s => [r.lead, ...s]); setDupes(null)
      try { localStorage.setItem('ebd.lastEvent', JSON.stringify({ source: f.source, source_detail: f.source_detail, met_on: f.met_on, city: f.city })) } catch { /* not kept */ }
      setF(x => ({ ...BLANK, source: x.source, source_detail: x.source_detail, met_on: x.met_on, city: x.city }))
      onSaved?.()
    } catch (e) {
      if (e.status === 409 && e.detail?.duplicates) setDupes(e.detail.duplicates)
      else setErr(errText(e))
    } finally { setBusy(false) }
  }
  return (
    <div className="ebd-modal-bg" role="dialog" aria-modal="true" aria-label="Add contacts" onClick={onClose}>
      <div className="ebd-modal" onClick={e => e.stopPropagation()}>
        <h2>Add contacts</h2>
        <p className="ebd-muted" style={{ marginTop: 0 }}>Type in a business card, a referral or anyone you want to reach. Only a name or company is needed. Card photo scanning isn't built yet.</p>
        {err && <div className="ebd-error">{err}</div>}
        {saved.length > 0 && <div className="ebd-ok">Saved {saved.length}: {saved.map(s => s.contact || s.company).join(', ')}. Add the next one, or close.</div>}
        {dupes && (
          <div className="ebd-note">
            <b>This looks like a contact you already have:</b>
            {dupes.map(d => <div key={d.id} style={{ marginTop: 6 }}><Link to={`${BASE}/leads/${d.id}`} onClick={onClose}>{d.company}{d.contact ? ` · ${d.contact}` : ''}</Link> <span className="ebd-muted ebd-small">{d.email || d.phone} · {d.source_label}</span></div>)}
            <div className="ebd-acts" style={{ marginTop: 10 }}>
              <button className="ebd-btn sm" onClick={() => setDupes(null)}>Edit details</button>
              <button className="ebd-btn sm" disabled={busy} onClick={() => save(true)}>Save as a new contact anyway</button>
            </div>
          </div>
        )}
        <form className="ebd-form" onSubmit={e => { e.preventDefault(); save(false) }}>
          <div className="two">
            <label>Name<input value={f.contact_name} onChange={set('contact_name')} autoFocus autoComplete="off" /></label>
            <label>Company<input value={f.company_name} onChange={set('company_name')} autoComplete="off" /></label>
          </div>
          <div className="two">
            <label>Email<input type="email" value={f.email} onChange={set('email')} autoComplete="off" /></label>
            <label>Phone<input type="tel" value={f.phone} onChange={set('phone')} autoComplete="off" /></label>
          </div>
          <div className="two">
            <label>How you know them<select value={f.source} onChange={set('source')}>{SOURCE_OPTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label>{detailLabel} <input value={f.source_detail} onChange={set('source_detail')} placeholder={f.source === 'networking' ? 'e.g. Dallas Chamber Mixer' : ''} /></label>
          </div>
          <details className="ebd-more">
            <summary>More details <span className="opt">(optional)</span></summary>
            <div className="ebd-form" style={{ marginTop: 10 }}>
              <div className="two">
                <label>Their role<input value={f.role} onChange={set('role')} placeholder="Owner, office manager…" /></label>
                <label>Date you met<input type="date" value={f.met_on} onChange={set('met_on')} /></label>
              </div>
              <div className="two">
                <label>Industry<input value={f.industry} onChange={set('industry')} placeholder="Cleaning, roofing…" /></label>
                <label>City<input value={f.city} onChange={set('city')} placeholder="Dallas, Plano…" /></label>
              </div>
              <div className="two">
                <label>Best way to reach them<select value={f.preferred_contact} onChange={set('preferred_contact')}><option value="">Not sure</option><option value="email">Email</option><option value="phone">Phone call</option><option value="text">Text</option></select></label>
                <label>Website<input value={f.website} onChange={set('website')} /></label>
              </div>
              <label>Personal note for your first message <span className="opt">(only used if you write it)</span><input value={f.intro_note} onChange={set('intro_note')} placeholder="e.g. Congrats on the new location" /></label>
              <label>Notes<textarea value={f.notes} onChange={set('notes')} /></label>
            </div>
          </details>
          <div className="ebd-modal-foot">
            <Link className="ebd-btn" to={`${BASE}/leads?import=1`} onClick={onClose}><Icon name="upload" size={16} />Import a list instead</Link>
            <button type="button" className="ebd-btn" onClick={onClose}>Close</button>
            <button className="ebd-btn primary" disabled={busy}>{busy ? 'Saving…' : 'Save contact'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function AddButton({ label = 'Add contacts' }) {
  const ctx = useOutletCtx()
  return <button className="ebd-btn primary" onClick={() => ctx?.openAdd?.()}><Icon name="plus" size={18} />{label}</button>
}

// The shell passes { openAdd, bump } to every screen through the outlet.
function useOutletCtx() {
  return useOutletContext() || null
}

// ── Today ────────────────────────────────────────────────────────────────────

export function Today() {
  useTitle('Today')
  const ctx = useOutletCtx()
  const { data, error, reload } = useLoad(`${API}/today`)
  useEffect(() => { if (ctx?.bump) reload() }, [ctx?.bump]) // eslint-disable-line react-hooks/exhaustive-deps
  const [tab, setTab] = useState('replies')
  const c = data?.counts || {}
  const o = data?.outreach || {}
  const replies = data?.replies || []
  const calls = data?.to_call || []
  const review = data?.needs_review || []
  const s = data?.suggestion
  const dupN = review.filter(x => x.kind === 'duplicate').length
  const heldN = review.filter(x => x.kind === 'held').length
  const mktN = review.filter(x => x.kind === 'market').length
  if (error || !data) return <Box error={error} data={data} />
  return (
    <>
      <PageHead title="Today">
        <Link to={`${BASE}/outreach`} className={'ebd-chip' + (o.any_on ? ' on' : '')}>
          <Icon name={o.any_on ? 'send' : 'off'} size={18} />{o.any_on ? `Outreach on · ${o.campaigns_on} campaign${o.campaigns_on === 1 ? '' : 's'}` : 'Outreach off'}
        </Link>
        {c.replies > 0 && <Link to={`${BASE}/conversations`} className="ebd-chip soft"><Icon name="reply" size={18} />{c.replies} {c.replies === 1 ? 'reply' : 'replies'} waiting</Link>}
        <AddButton />
      </PageHead>

      <nav className="ebd-counts" aria-label="Today's numbers">
        <Count n={c.replies} label="Replies" icon="chat" attn={c.replies > 0} onClick={() => setTab('replies')} />
        <Count n={c.to_call} label="To call" icon="phone" onClick={() => setTab('calls')} />
        <Count n={c.in_followup} label="In follow-up" icon="people" to={`${BASE}/leads?view=followup`} />
        <Count n={c.booked} label="Booked" icon="cal" to={`${BASE}/appointments`} />
      </nav>

      <div className="ebd-today">
        <section className="ebd-card" aria-label="Work queue">
          <h2>Work queue</h2>
          <div className="ebd-tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'replies'} className={tab === 'replies' ? 'on' : ''} onClick={() => setTab('replies')}><Icon name="chat" size={18} />Replies ({replies.length})</button>
            <button role="tab" aria-selected={tab === 'calls'} className={tab === 'calls' ? 'on' : ''} onClick={() => setTab('calls')}><Icon name="phone" size={18} />Calls ({c.to_call || 0})</button>
            <button role="tab" aria-selected={tab === 'review'} className={tab === 'review' ? 'on' : ''} onClick={() => setTab('review')}><Icon name="warn" size={18} />Needs review ({review.length})</button>
          </div>
          {tab === 'replies' && (replies.length ? replies.map((m, i) => (
            <div key={m.id} className={'ebd-item' + (i === 0 ? ' hot' : '')}>
              <div style={{ minWidth: 0 }}>
                <b>{m.lead.company}</b>
                <div className="who">{m.lead.contact || m.to || ''} · Replied {when(m.at)}{m.needs_review && <> · <span className="ebd-pill gold">Needs review</span></>}</div>
                <q>{(m.body || '').slice(0, 220)}</q>
              </div>
              <div className="ebd-item-acts">
                <Link className={'ebd-btn sm' + (i === 0 ? ' primary' : '')} to={`${BASE}/conversations?lead=${m.lead.id}`}>{m.needs_review ? 'Review reply' : 'Open reply'} <Icon name="arrow" size={14} /></Link>
              </div>
            </div>
          )) : <p className="ebd-empty">No replies waiting. Replies to your campaigns show up here by themselves.</p>)}
          {tab === 'calls' && (calls.length ? calls.map(l => (
            <div key={l.id} className="ebd-item">
              <div style={{ minWidth: 0 }}>
                <b>{l.company}</b>
                <div className="who">{[l.contact, l.phone, l.market].filter(Boolean).join(' · ')}</div>
                <span className="ebd-small ebd-muted">{l.email ? 'Prefers a call' : 'No email on file'} · {l.source_label}</span>
              </div>
              <div className="ebd-item-acts"><CallBtn phone={l.phone} /><Link className="ebd-btn sm" to={`${BASE}/leads/${l.id}`}>Open</Link></div>
            </div>
          )) : <p className="ebd-empty">Nobody to call right now.</p>)}
          {tab === 'review' && (review.length ? review.map((r, i) => (
            <div key={i} className="ebd-item">
              <div><b>{r.text}</b><div className="who">{r.label}{r.detail ? ` · ${r.detail}` : ''}</div></div>
              <div className="ebd-item-acts"><Link className="ebd-btn sm" to={`${BASE}/leads/${r.lead_id}`}>Review</Link></div>
            </div>
          )) : <p className="ebd-empty">Nothing needs review.</p>)}
        </section>

        <div>
          <section className="ebd-side-card" aria-label="Suggestion">
            <h3><Icon name="spark" />Suggestion</h3>
            {s ? (
              <div className="ebd-ai">
                <Icon name={s.kind === 'pricing' ? 'tag' : s.kind === 'review' ? 'warn' : 'spark'} />
                <div>
                  <b>{s.title}</b><p>{s.text}</p>
                  <Link className="ebd-btn sm primary" to={s.lead_id ? `${BASE}/conversations?lead=${s.lead_id}` : `${BASE}/${s.to}`}>{s.action} <Icon name="arrow" size={14} /></Link>
                </div>
              </div>
            ) : <p className="ebd-empty">Nothing to suggest. Add contacts from your last event to get started.</p>}
            <p className="ebd-small ebd-muted" style={{ margin: '8px 0 0' }}>Worked out from your replies, review list and contacts.</p>
          </section>
          <section className="ebd-side-card" aria-label="Needs review">
            <h3><Icon name="warn" />Needs review</h3>
            <div className="ebd-revrow"><Icon name="copy" />{dupN} possible duplicate{dupN === 1 ? '' : 's'}</div>
            <div className="ebd-revrow"><Icon name="branch" />{mktN} unclear market{mktN === 1 ? '' : 's'}</div>
            <div className="ebd-revrow"><Icon name="warn" />{heldN} held</div>
            <Link to={`${BASE}/leads?view=review`} style={{ fontWeight: 700, display: 'inline-block', marginTop: 8 }}>Review leads <Icon name="arrow" size={14} /></Link>
          </section>
          <section className="ebd-side-card" aria-label="Next booked call">
            <h3><Icon name="cal" />Next booked call</h3>
            {data.next_call ? (
              <Link className="ebd-nextcall" to={data.next_call.lead_id ? `${BASE}/leads/${data.next_call.lead_id}` : `${BASE}/appointments`}>
                <div><b>{data.next_call.company || data.next_call.title}</b><span>{whenTZ(data.next_call.starts_at)}</span></div><Icon name="arrow" />
              </Link>
            ) : <p className="ebd-empty" style={{ padding: 0 }}>No calls booked yet.</p>}
          </section>
        </div>
      </div>
    </>
  )
}

function Count({ n, label, icon, to, attn, onClick }) {
  const inner = <><Icon name={icon} size={26} /><div><b>{n ?? 0}</b><span>{label}</span></div></>
  if (to) return <Link to={to} className={'ebd-count' + (attn ? ' attn' : '')}>{inner}</Link>
  return <a href="#queue" className={'ebd-count' + (attn ? ' attn' : '')} onClick={e => { e.preventDefault(); onClick?.() }}>{inner}</a>
}

// ── Leads ────────────────────────────────────────────────────────────────────

const VIEWS = [['all', 'All'], ['unassigned', 'Not in a campaign'], ['followup', 'In follow-up'], ['no_email', 'To call'],
               ['replied', 'Replied'], ['booked', 'Booked'], ['review', 'Needs review'], ['stopped', 'Stopped']]

export function Leads() {
  useTitle('Leads')
  const ctx = useOutletCtx()
  const [sp, setSp] = useSearchParams()
  const view = sp.get('view') || 'all'
  const source = sp.get('source') || ''
  const campaign = sp.get('campaign') || ''
  const batch = sp.get('batch') || ''
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  useEffect(() => { const t = setTimeout(() => setDebounced(q), 250); return () => clearTimeout(t) }, [q])
  const qs = `view=${view}&source=${encodeURIComponent(source)}&campaign=${encodeURIComponent(campaign)}&batch=${encodeURIComponent(batch)}&q=${encodeURIComponent(debounced)}`
  const { data, error, reload } = useLoad(`${API}/contacts?${qs}`)
  useEffect(() => { if (ctx?.bump) reload() }, [ctx?.bump]) // eslint-disable-line react-hooks/exhaustive-deps
  const [sel, setSel] = useState([])
  const [modal, setModal] = useState(null)
  const [showBatches, setShowBatches] = useState(sp.get('import') === '1')
  const rows = data?.leads || []
  useEffect(() => { setSel([]) }, [qs])
  const setParam = (k, v) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n, { replace: true }) }
  const toggle = id => setSel(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])
  const all = rows.length > 0 && sel.length === rows.length
  const filterPick = { view, q: debounced, source, campaign, batch }
  const picked = (data?.campaigns || []).filter(k => k.kind !== 'group')
  return (
    <>
      <PageHead title="Leads" sub={data ? `${data.total} contacts · ${data.shown} shown` : ''}>
        <button className="ebd-btn" onClick={() => setShowBatches(v => !v)}><Icon name="upload" size={16} />Import & batches</button>
        <AddButton />
      </PageHead>
      {showBatches && <Batches onPick={b => setParam('batch', b)} />}
      <div className="ebd-views" role="group" aria-label="Show">
        {VIEWS.map(([k, l]) => <button key={k} className={view === k ? 'on' : ''} onClick={() => setParam('view', k === 'all' ? '' : k)}>{l}</button>)}
      </div>
      <div className="ebd-filters">
        <input type="search" placeholder="Search name, company, phone, city…" value={q} onChange={e => setQ(e.target.value)} aria-label="Search leads" />
        <select value={source} onChange={e => setParam('source', e.target.value)} aria-label="Source">
          <option value="">All sources</option>
          {(data?.sources || []).map(s => <option key={s.key} value={s.key}>{s.label} ({s.count})</option>)}
        </select>
        <select value={campaign} onChange={e => setParam('campaign', e.target.value)} aria-label="Campaign">
          <option value="">All campaigns</option><option value="none">Not in a campaign</option>
          {(data?.campaigns || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {batch && <button className="ebd-chip on" onClick={() => setParam('batch', '')}>Batch: {batch} ✕</button>}
      </div>
      {data?.can_manage && rows.length > 0 && (
        <div className="ebd-bulk" role="region" aria-label="Bulk actions">
          <span>{sel.length ? `${sel.length} selected` : `Use all ${data.shown} shown`}</span>
          <button className="ebd-btn sm primary" onClick={() => setModal({ kind: 'create', pick: sel.length ? { lead_ids: sel } : filterPick })}><Icon name="plus" size={14} />Create campaign</button>
          <button className="ebd-btn sm" onClick={() => setModal({ kind: 'add', pick: sel.length ? { lead_ids: sel } : filterPick })}>Add to campaign</button>
          {sel.length > 0 && <button className="ebd-btn sm" onClick={() => setSel([])}>Clear</button>}
        </div>
      )}
      <Box error={error} data={data}>
        {rows.length === 0 ? <p className="ebd-empty">No contacts here. Add contacts or import a list.</p> : (
          <>
            <table className="ebd-table">
              <thead><tr>
                {data.can_manage && <th><input type="checkbox" aria-label="Select all" checked={all} onChange={() => setSel(all ? [] : rows.map(r => r.id))} /></th>}
                <th>Contact</th><th>Source</th><th>Group</th><th>Campaign</th><th>Status</th><th />
              </tr></thead>
              <tbody>{rows.map(r => (
                <tr key={r.id}>
                  {data.can_manage && <td><input type="checkbox" aria-label={`Select ${r.company}`} checked={sel.includes(r.id)} onChange={() => toggle(r.id)} /></td>}
                  <td><Link className="row-link" to={`${BASE}/leads/${r.id}`}>{r.company}</Link><div className="ebd-small ebd-muted">{[r.contact !== r.company ? r.contact : null, r.role].filter(Boolean).join(', ')}{r.email ? ` · ${r.email}` : r.phone ? ` · ${r.phone}` : ''}</div></td>
                  <td>{r.source_label}{r.source_detail && <div className="ebd-small ebd-muted">{r.source_detail}</div>}</td>
                  <td>{[r.industry, r.market].filter(Boolean).join(' · ') || <span className="ebd-muted">-</span>}{r.review !== 'ok' && r.review_label && <div><span className="ebd-pill gold">{r.review_label}</span></div>}</td>
                  <td>{r.campaign || <span className="ebd-muted">-</span>}</td>
                  <td><Status o={r.outreach} />{r.booked && <> <span className="ebd-pill good">Booked</span></>}</td>
                  <td><CallBtn phone={r.phone} /></td>
                </tr>
              ))}</tbody>
            </table>
            <div className="ebd-cards">{rows.map(r => (
              <div key={r.id} className="ebd-lcard">
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  {data.can_manage && <input type="checkbox" aria-label={`Select ${r.company}`} checked={sel.includes(r.id)} onChange={() => toggle(r.id)} />}
                  <Link to={`${BASE}/leads/${r.id}`}>{r.company}</Link>
                </div>
                <span className="ebd-small ebd-muted">{[r.contact !== r.company ? r.contact : null, r.source_label, r.market].filter(Boolean).join(' · ')}</span>
                <div><Status o={r.outreach} />{r.review !== 'ok' && r.review_label && <> <span className="ebd-pill gold">{r.review_label}</span></>}{r.campaign && <span className="ebd-small ebd-muted"> · {r.campaign}</span>}</div>
              </div>
            ))}</div>
          </>
        )}
      </Box>
      {modal?.kind === 'create' && <CreateCampaign pick={modal.pick} onClose={() => setModal(null)} />}
      {modal?.kind === 'add' && <AddToCampaign pick={modal.pick} campaigns={picked} onClose={() => setModal(null)} onDone={() => { setModal(null); reload() }} />}
    </>
  )
}

function Batches({ onPick }) {
  const { data, error } = useLoad(`${API}/batches`)
  const { data: src } = useLoad(`${API}/sources`)
  const tone = { connected: 'good', import: 'blue', setup: 'gold' }
  return (
    <section className="ebd-card" aria-label="Lead batches">
      <div className="ebd-card-h"><h3>Lead batches</h3>{src?.import_path && <Link className="ebd-btn sm primary" to={src.import_path}><Icon name="upload" size={14} />Import a list</Link>}</div>
      <p className="ebd-small ebd-muted" style={{ marginTop: 0 }}>Every list already in the system, counted. Re-importing the same list matches the contacts you already have; it never makes duplicates. Choose a batch to see its contacts.</p>
      <Box error={error} data={data}>
        <div style={{ overflowX: 'auto' }}>
          <table className="ebd-table" style={{ display: 'table' }}>
            <thead><tr><th>Batch</th><th>Total</th><th>Still workable</th><th>With email</th><th>In a campaign</th><th>Closed</th><th>Added</th></tr></thead>
            <tbody>{(data?.batches || []).map(b => (
              <tr key={b.batch}><td><button className="ebd-btn sm ghost" onClick={() => onPick(b.batch)}><b>{b.batch}</b></button><div className="ebd-small ebd-muted">{b.source}</div></td><td>{b.total}</td><td>{b.working}</td><td>{b.with_email}</td><td>{b.in_campaign}</td><td>{b.closed}</td><td className="ebd-small">{day(b.first)}{b.last && day(b.last) !== day(b.first) ? ` – ${day(b.last)}` : ''}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </Box>
      {src && (
        <>
          <h3 style={{ marginTop: 16 }}>Where contacts come from</h3>
          <div className="ebd-filters">{src.sources.map(s => <span key={s.key} className="ebd-chip" title={s.how}>{s.label} <span className={'ebd-pill ' + tone[s.status]}>{s.status_label}</span></span>)}</div>
          <p className="ebd-small ebd-muted">{src.card_scanning}</p>
        </>
      )}
    </section>
  )
}

const KIND_OPTS = [['event', 'Event'], ['relationship', 'Relationship / referrals'], ['project', 'Project'], ['offer', 'Offer'], ['list', 'List']]

function AudienceTable({ rows }) {
  if (!rows) return null
  return (
    <div style={{ maxHeight: 260, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 12, marginTop: 10 }}>
      <table className="ebd-table" style={{ display: 'table' }}>
        <thead><tr><th>Contact</th><th>Will it be emailed?</th></tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.id}><td><b>{r.company}</b><div className="ebd-small ebd-muted">{[r.contact !== r.company ? r.contact : null, r.email, r.market].filter(Boolean).join(' · ')}</div></td>
            <td>{r.already_here ? <span className="ebd-pill muted">Already in it</span> : r.will_email ? <span className="ebd-pill good">Yes</span> : <span className="ebd-pill warn">{r.held_label}</span>}
              {r.other_campaign && <div className="ebd-small ebd-muted">Now in "{r.other_campaign}"</div>}</td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}

function CreateCampaign({ pick, onClose }) {
  const nav = useNavigate()
  const [groups, setGroups] = useState(null)
  const [g, setG] = useState(null)
  const [f, setF] = useState({ name: '', kind: 'list', context: '', tone: 'researched' })
  const [aud, setAud] = useState(null)
  const [move, setMove] = useState(false)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  function choose(x) {
    setG(x)
    setF({ name: x.name, kind: x.kind, context: x.context || '', tone: x.tone })
  }
  useEffect(() => {
    api.post(`${API}/campaigns/suggest`, pick).then(r => {
      setGroups(r.groups)
      if (r.groups[0]) choose(r.groups[0])
    }).catch(e => setErr(errText(e)))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const thePick = g ? { lead_ids: g.lead_ids } : pick
  useEffect(() => {
    if (!groups) return
    api.post(`${API}/campaigns/audience/preview`, { kind: f.kind, tone: f.tone, pick: thePick })
      .then(setAud).catch(e => setErr(errText(e)))
  }, [groups, g, f.kind, f.tone]) // eslint-disable-line react-hooks/exhaustive-deps
  async function create() {
    setBusy(true); setErr(null)
    try {
      const r = await api.post(`${API}/campaigns/picked`, { ...f, context: f.context || null, pick: thePick, move })
      nav(`${BASE}/outreach/${r.campaign.id}`)
    } catch (e) { setErr(errText(e)); setBusy(false) }
  }
  const set = k => e => setF(x => ({ ...x, [k]: e.target.value }))
  return (
    <div className="ebd-modal-bg" role="dialog" aria-modal="true" aria-label="Create campaign" onClick={onClose}>
      <div className="ebd-modal wide" onClick={e => e.stopPropagation()}>
        <h2>Create campaign</h2>
        <p className="ebd-muted" style={{ marginTop: 0 }}>Build it around the actual work: an event, a referral, a project or a list. It starts Off. Nothing is sent until you approve the messages and turn it on.</p>
        {err && <div className="ebd-error">{err}</div>}
        {!groups ? <div className="ebd-loading">Looking at these contacts…</div> : (
          <>
            {groups.length > 1 && (
              <>
                <b className="ebd-small">Suggested groups</b>
                <div className="ebd-groups">
                  {groups.map((x, i) => <button key={i} className={'ebd-group' + (g === x ? ' on' : '')} onClick={() => choose(x)}><b>{x.name} · {x.count}</b><span>{x.why}</span></button>)}
                  <button className={'ebd-group' + (g === null ? ' on' : '')} onClick={() => setG(null)}><b>Everyone selected · {groups.reduce((n, x) => n + x.count, 0)}</b><span>One campaign for all of them</span></button>
                </div>
              </>
            )}
            {groups.length === 1 && <p className="ebd-small ebd-muted">Suggested because: {groups[0].why}.</p>}
            <div className="ebd-form">
              <div className="two">
                <label>Campaign name<input value={f.name} onChange={set('name')} /></label>
                <label>Built around<select value={f.kind} onChange={set('kind')}>{KIND_OPTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
              </div>
              <div className="two">
                <label>{f.kind === 'event' ? 'Event' : f.kind === 'relationship' ? 'Relationship' : 'About'} <span className="opt">(optional)</span><input value={f.context} onChange={set('context')} /></label>
                <label>Voice<select value={f.tone} onChange={set('tone')}><option value="warm">Warm: people you met or were introduced to</option><option value="researched">Researched: no prior relationship</option></select></label>
              </div>
            </div>
            {aud && (
              <>
                <p style={{ margin: '14px 0 0' }}><b>{aud.will_email}</b> will be emailed · <b>{aud.held}</b> held for review or calls{aud.in_other_campaigns > 0 && <> · <b>{aud.in_other_campaigns}</b> already in another campaign</>}</p>
                {aud.in_other_campaigns > 0 && <label className="ebd-small" style={{ display: 'flex', gap: 8, marginTop: 6 }}><input type="checkbox" checked={move} onChange={e => setMove(e.target.checked)} />Move them here (never while another campaign is emailing them)</label>}
                <AudienceTable rows={aud.rows} />
              </>
            )}
          </>
        )}
        <div className="ebd-modal-foot">
          <button className="ebd-btn" onClick={onClose}>Cancel</button>
          <button className="ebd-btn primary" disabled={busy || !f.name.trim() || !groups} onClick={create}>{busy ? 'Creating…' : 'Create campaign (Off)'}</button>
        </div>
      </div>
    </div>
  )
}

function AddToCampaign({ pick, campaigns, onClose, onDone }) {
  const [id, setId] = useState(campaigns[0]?.id || '')
  const [aud, setAud] = useState(null)
  const [move, setMove] = useState(false)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    if (!id) return
    setAud(null)
    api.post(`${API}/campaigns/${id}/audience/preview`, pick).then(setAud).catch(e => setErr(errText(e)))
  }, [id]) // eslint-disable-line react-hooks/exhaustive-deps
  async function add() {
    setErr(null)
    try { setRes(await api.post(`${API}/campaigns/${id}/leads`, { ...pick, move })) } catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="ebd-modal-bg" role="dialog" aria-modal="true" aria-label="Add to campaign" onClick={onClose}>
      <div className="ebd-modal wide" onClick={e => e.stopPropagation()}>
        <h2>Add to campaign</h2>
        {err && <div className="ebd-error">{err}</div>}
        {!campaigns.length ? <p className="ebd-empty">No event, referral, project or list campaigns yet. Use Create campaign. (Industry + market campaigns pick up their group by themselves.)</p> : (
          <div className="ebd-form"><label>Campaign<select value={id} onChange={e => setId(e.target.value)}>{campaigns.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label></div>
        )}
        {res ? (
          <div className="ebd-ok" style={{ marginTop: 12 }}>Added {res.added}{res.moved ? ` (moved ${res.moved})` : ''}. {res.already_here ? `${res.already_here} were already in it. ` : ''}{res.held.length ? `${res.held.length} held for review or calls. ` : ''}{res.refused.length ? `${res.refused.length} not added: ${res.refused.map(r => `${r.company} (${r.reason})`).join('; ')}` : ''}</div>
        ) : aud && (
          <>
            <p style={{ margin: '14px 0 0' }}><b>{aud.will_email}</b> new to email · <b>{aud.already_here}</b> already in it · <b>{aud.held}</b> held · <b>{aud.in_other_campaigns}</b> in another campaign</p>
            {aud.in_other_campaigns > 0 && <label className="ebd-small" style={{ display: 'flex', gap: 8, marginTop: 6 }}><input type="checkbox" checked={move} onChange={e => setMove(e.target.checked)} />Move them here (never while another campaign is emailing them)</label>}
            <AudienceTable rows={aud.rows} />
          </>
        )}
        <div className="ebd-modal-foot">
          <button className="ebd-btn" onClick={res ? onDone : onClose}>{res ? 'Done' : 'Cancel'}</button>
          {!res && <button className="ebd-btn primary" disabled={!id || !aud} onClick={add}>Add</button>}
        </div>
      </div>
    </div>
  )
}

// ── one lead ─────────────────────────────────────────────────────────────────

export function Lead() {
  const { id } = useParams()
  const { data, error, reload } = useLoad(`${API}/contacts/${id}`)
  useTitle(data?.lead?.company || 'Lead')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [edit, setEdit] = useState(false)
  const [book, setBook] = useState(false)
  const [handoff, setHandoff] = useState(null)
  async function act(path, body, ok) {
    setBusy(true); setMsg(null)
    try { await api.post(path, body || {}); setNote(''); setMsg({ ok: true, text: ok }); await reload() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  async function signup() {
    if (!window.confirm('Mark this business as signed up? It is handed to service-company onboarding, with its source and campaign.')) return
    setBusy(true); setMsg(null)
    try {
      const r = await api.post(`${API}/leads/${id}/signed-up`, {})
      setHandoff(r); await reload()
    } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  if (error || !data) return <Box error={error} data={data} />
  const l = data.lead
  const g = data.group || {}
  return (
    <>
      <Link className="ebd-crumb" to={`${BASE}/leads`}><Icon name="back" size={14} />Leads</Link>
      <PageHead title={l.company} sub={[l.contact !== l.company ? l.contact : null, l.role].filter(Boolean).join(', ')}>
        <CallBtn phone={l.phone} />
        <button className="ebd-btn" onClick={() => setBook(true)}><Icon name="cal" size={16} />Book intro call</button>
        {data.signed_up
          ? <Link className="ebd-btn primary" to={`/sales/opportunities/${l.id}`}>Open deal & onboarding</Link>
          : data.can_manage && <button className="ebd-btn primary" disabled={busy} onClick={signup}>Signed up</button>}
      </PageHead>
      {msg && <div className={msg.ok ? 'ebd-ok' : 'ebd-error'}>{msg.text}</div>}
      {handoff && (
        <div className={handoff.status === 'handed_off' ? 'ebd-ok' : 'ebd-note'}>
          {handoff.status === 'handed_off'
            ? <>Handed to service-company onboarding. Kept with it: {handoff.attribution}. <Link to={`/sales/opportunities/${l.id}`}>Open the deal</Link></>
            : <>{handoff.message || 'This business already has a workspace.'}</>}
        </div>
      )}
      {book && <BookCall lead={l} onClose={() => setBook(false)} onBooked={t => { setBook(false); setMsg({ ok: true, text: t }); reload() }} />}
      <div className="ebd-grid2">
        <div>
          <section className="ebd-card">
            <div className="ebd-card-h"><h3>Outreach</h3><Status o={l.outreach} /></div>
            <p style={{ margin: '0 0 6px' }}>Campaign: {l.campaign ? <Link to={`${BASE}/outreach/${l.campaign_id}`}><b>{l.campaign}</b></Link> : <span className="ebd-muted">not in a campaign</span>}</p>
            {data.next && (
              <div className="ebd-note">
                <b>Next: message {data.next.step}</b> · {data.next.campaign_on && data.next.due_at ? `due ${whenTZ(data.next.due_at)}` : 'waiting (campaign is off)'}
                <div className="ebd-small" style={{ marginTop: 6 }}><b>{data.next.subject}</b></div>
                <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', margin: '6px 0 0', fontSize: 13.5 }}>{data.next.body}</pre>
                <div className="ebd-facts-used">{data.next.facts_used.map(f => <span key={f.key} className="ebd-pill blue" title={f.from}>{f.key}: {f.value}</span>)}
                  {data.next.missing.map(m => <span key={m} className="ebd-pill warn">missing: {m}. Add it under Details and it goes out.</span>)}</div>
              </div>
            )}
            <div className="ebd-acts">
              {l.outreach?.key === 'active' && <button className="ebd-btn sm" disabled={busy} onClick={() => act(`${API}/leads/${id}/stop`, {}, 'Follow-up stopped.')}>Stop follow-up</button>}
              {['manual', 'finished'].includes(l.outreach?.key) && l.campaign && <button className="ebd-btn sm" disabled={busy} onClick={() => act(`${API}/leads/${id}/restart`, {}, 'Follow-up restarted.')}>Restart follow-up</button>}
            </div>
          </section>
          <section className="ebd-card">
            <h3>Conversation</h3>
            {data.messages.length ? <div className="ebd-thread">{data.messages.map(m => <Message key={m.id} m={m} />)}</div> : <p className="ebd-empty">Nothing sent or received yet.</p>}
            {data.reply_waiting && <p style={{ marginTop: 10 }}><Link className="ebd-btn sm primary" to={`${BASE}/conversations?lead=${id}`}><Icon name="spark" size={14} />Answer with AI help</Link></p>}
            <div className="ebd-form" style={{ marginTop: 12 }}>
              <label>Note<textarea value={note} onChange={e => setNote(e.target.value)} placeholder="What they said, or what you did" /></label>
              <div className="ebd-acts">
                <button className="ebd-btn sm" disabled={busy} onClick={() => act(`${API}/leads/${id}/reply`, { note }, 'Reply logged. Automatic follow-up stopped.')}>They replied</button>
                <button className="ebd-btn sm" disabled={busy} onClick={() => act(`${API}/leads/${id}/call`, { note }, 'Call logged.')}>I called them</button>
                {data.reply_waiting && <button className="ebd-btn sm" disabled={busy} onClick={() => act(`${API}/contacts/${id}/handled`, { note }, 'Marked answered.')}>Mark answered</button>}
              </div>
            </div>
          </section>
        </div>
        <div>
          <section className="ebd-card">
            <div className="ebd-card-h"><h3>Details</h3><button className="ebd-btn sm ghost" onClick={() => setEdit(e => !e)}>{edit ? 'Close' : 'Edit'}</button></div>
            {edit ? <EditLead lead={l} intro={data.intro_note} onSaved={() => { setEdit(false); reload() }} /> : (
              <dl className="ebd-facts">
                <dt>Email</dt><dd>{l.email || '-'}</dd>
                <dt>Phone</dt><dd>{l.phone || '-'}</dd>
                <dt>Source</dt><dd>{l.source_label}{l.source_detail ? ` · ${l.source_detail}` : ''}</dd>
                <dt>Met</dt><dd>{l.met_on ? day(l.met_on) : '-'}</dd>
                <dt>Group</dt><dd>{[g.industry, g.market].filter(Boolean).join(' · ') || '-'}{g.review && g.review !== 'ok' && <> <span className="ebd-pill gold">{g.review_label}</span></>}</dd>
                {g.reason && <><dt>Why</dt><dd className="ebd-small">{g.reason}</dd></>}
                <dt>Prefers</dt><dd>{l.preferred_contact || '-'}</dd>
                <dt>Stage</dt><dd>{l.stage_label}</dd>
                <dt>Added</dt><dd>{whenTZ(l.created_at)}</dd>
                <dt>Batch</dt><dd>{l.batch}</dd>
                {data.intro_note && <><dt>Personal note</dt><dd>{data.intro_note}</dd></>}
              </dl>
            )}
          </section>
          {data.appointments.length > 0 && (
            <section className="ebd-card">
              <h3>Calls</h3>
              {data.appointments.map(a => <div key={a.id} className="ebd-revrow"><Icon name="cal" />{whenTZ(a.starts_at)} <span className="ebd-pill muted">{a.status}</span></div>)}
            </section>
          )}
          <section className="ebd-card">
            <h3>Timeline</h3>
            <ul className="ebd-timeline">{data.timeline.map((e, i) => <li key={i}><time>{whenTZ(e.at)}</time>{e.summary}{e.detail && <div className="ebd-small ebd-muted">{e.detail.slice(0, 200)}</div>}</li>)}</ul>
          </section>
        </div>
      </div>
    </>
  )
}

function Message({ m }) {
  const who = m.direction === 'in' ? 'They wrote' : m.channel === 'call' ? 'Call' : m.status === 'logged' ? 'You noted' : m.status === 'held' ? 'Held (text)' : 'Sent'
  return (
    <article className={'ebd-msg' + (m.direction === 'in' ? ' in' : '')}>
      <header><span><b>{who}</b>{m.step ? ` · message ${m.step}` : ''}{m.campaign ? ` · ${m.campaign}` : ''}</span><time>{whenTZ(m.at)}</time></header>
      {m.subject && <div style={{ fontWeight: 700, marginBottom: 4 }}>{m.subject}</div>}
      <pre>{m.body}</pre>
      {m.status === 'failed' && <div className="ebd-small" style={{ color: 'var(--bad)' }}>Didn't send: {m.error}</div>}
    </article>
  )
}

function EditLead({ lead, intro, onSaved }) {
  const [f, setF] = useState({ contact_name: lead.contact || '', company_name: lead.company || '', email: lead.email || '', phone: lead.phone || '',
    role: lead.role || '', source_detail: lead.source_detail || '', met_on: lead.met_on || '', intro_note: intro || '', preferred_contact: lead.preferred_contact || '' })
  const [err, setErr] = useState(null)
  const set = k => e => setF(x => ({ ...x, [k]: e.target.value }))
  async function save() {
    setErr(null)
    try { await api.patch(`${API}/contacts/${lead.id}`, { ...f, met_on: f.met_on || null }); onSaved() } catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="ebd-form">
      {err && <div className="ebd-error">{err}</div>}
      <label>Name<input value={f.contact_name} onChange={set('contact_name')} /></label>
      <label>Company<input value={f.company_name} onChange={set('company_name')} /></label>
      <label>Email<input value={f.email} onChange={set('email')} /></label>
      <label>Phone<input value={f.phone} onChange={set('phone')} /></label>
      <label>Role<input value={f.role} onChange={set('role')} /></label>
      <label>{lead.source_key === 'referral' ? 'Referred by' : 'Event'}<input value={f.source_detail} onChange={set('source_detail')} /></label>
      <label>Date met<input type="date" value={f.met_on} onChange={set('met_on')} /></label>
      <label>Personal note<input value={f.intro_note} onChange={set('intro_note')} /></label>
      <label>Prefers<select value={f.preferred_contact} onChange={set('preferred_contact')}><option value="">Not sure</option><option value="email">Email</option><option value="phone">Phone call</option><option value="text">Text</option></select></label>
      <button className="ebd-btn primary" onClick={save}>Save</button>
    </div>
  )
}

function BookCall({ lead, onClose, onBooked }) {
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10)
  const [d, setD] = useState(tomorrow)
  const [slots, setSlots] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    setSlots(null)
    api.get(`${API}/leads/${lead.id}/intro-slots?date_from=${d}`).then(r => {
      setSlots(r.slots || [])
      if (r.problem) setMsg(typeof r.problem === 'string' ? r.problem : 'Times could not be loaded.')
    }).catch(e => { setSlots([]); setMsg(errText(e)) })
  }, [d, lead.id])
  useEffect(() => { load() }, [load])
  async function book(s) {
    setBusy(true); setMsg(null)
    try {
      const r = await api.post(`${API}/leads/${lead.id}/intro-call`, { starts_at: s.starts_at, timezone: TZ })
      if (r.status === 'booked') onBooked(r.already_booked ? 'That call was already booked.' : `Intro call booked for ${whenTZ(r.starts_at || s.starts_at)}. Follow-up emails stop for this contact.`)
      else { setMsg(r.message); load() }
    } catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="ebd-modal-bg" role="dialog" aria-modal="true" aria-label="Book intro call" onClick={onClose}>
      <div className="ebd-modal" onClick={e => e.stopPropagation()}>
        <h2>Book an intro call</h2>
        <p className="ebd-muted" style={{ marginTop: 0 }}>With {lead.contact || lead.company}. Open times from your calendar for the week from the date you pick, shown in Central time. A time that was just taken is never double-booked.</p>
        {msg && <div className="ebd-note">{msg}</div>}
        <div className="ebd-form"><label>Starting<input type="date" value={d} onChange={e => setD(e.target.value)} /></label></div>
        {slots === null ? <div className="ebd-loading">Finding open times…</div> : slots.length === 0 ? <p className="ebd-empty">No open times that week. Try a later date.</p> : (
          <div className="ebd-acts" style={{ marginTop: 10, maxHeight: 260, overflowY: 'auto' }}>
            {slots.map(s => <button key={s.starts_at} disabled={busy} className="ebd-btn sm" onClick={() => book(s)}>{whenTZ(s.starts_at)}</button>)}
          </div>
        )}
        <div className="ebd-modal-foot"><button className="ebd-btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  )
}

// ── Conversations ────────────────────────────────────────────────────────────

export function Conversations() {
  useTitle('Conversations')
  const [sp, setSp] = useSearchParams()
  const { data, error, reload } = useLoad(`${API}/inbox`)
  const rows = data?.conversations || []
  const pick = sp.get('lead') || (rows.find(r => r.waiting) || rows[0])?.id
  return (
    <>
      <PageHead title="Conversations" sub="Replies first. What they said, what AI suggests, and what you can do." />
      <Box error={error} data={data}>
        {!rows.length ? <p className="ebd-empty">No conversations yet. Messages and replies from your campaigns appear here.</p> : (
          <div className="ebd-grid2">
            <section className="ebd-card" aria-label="All conversations">
              {rows.map(r => (
                <button key={r.id} className={'ebd-item' + (r.id === pick ? ' hot' : '')} style={{ width: '100%', textAlign: 'left' }} onClick={() => setSp({ lead: r.id }, { replace: true })}>
                  <div style={{ minWidth: 0 }}>
                    <b>{r.company}</b>
                    <div className="who">{r.contact && r.contact !== r.company ? r.contact + ' · ' : ''}{when(r.last_at)}</div>
                    <span className="ebd-small">{((r.waiting ? r.reply?.body : r.last_message?.body) || '').slice(0, 90)}</span>
                  </div>
                  <div>{r.waiting ? <span className={'ebd-pill ' + (r.needs_review ? 'gold' : 'blue')}>{r.needs_review ? 'Needs review' : r.intent?.label}</span> : <span className="ebd-pill muted">{r.message_count}</span>}</div>
                </button>
              ))}
            </section>
            {pick && <Thread id={pick} row={rows.find(r => r.id === pick)} onChanged={reload} />}
          </div>
        )}
      </Box>
    </>
  )
}

function Thread({ id, row, onChanged }) {
  const { data, error, reload } = useLoad(`${API}/contacts/${id}`)
  const [ai, setAi] = useState(null)
  const [busy, setBusy] = useState(false)
  const [subject, setSubject] = useState('')
  const [draft, setDraft] = useState('')
  const [msg, setMsg] = useState(null)
  useEffect(() => { setAi(null); setDraft(''); setMsg(null) }, [id])
  const mid = row?.reply_message_id
  async function suggest(again) {
    setBusy(true); setAi(null)
    try {
      const r = await api.post(`${API}/messages/${mid}/assist${again ? '?again=true' : ''}`, {})
      setAi(r); setDraft(r?.output?.draft || '')
      setSubject('Re: ' + (row?.reply?.subject || 'your message').replace(/^re:\s*/i, ''))
    } catch (e) { setAi({ status: 'failed', error: errText(e) }) } finally { setBusy(false) }
  }
  async function send() {
    setBusy(true); setMsg(null)
    try {
      await api.post(`${API}/leads/${id}/send-reply`, { subject, body: draft, ai_task_id: ai?.id || null, reply_to_message_id: mid })
      setMsg({ ok: true, text: 'Sent.' }); setAi(null); setDraft(''); reload(); onChanged()
    } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  async function handled() {
    try { await api.post(`${API}/contacts/${id}/handled`, {}); setMsg({ ok: true, text: 'Marked answered.' }); reload(); onChanged() } catch (e) { setMsg({ ok: false, text: errText(e) }) }
  }
  const out = ai?.output || {}
  return (
    <section className="ebd-card" aria-label="Conversation">
      <Box error={error} data={data}>
        <div className="ebd-card-h"><h3><Link to={`${BASE}/leads/${id}`}>{data?.lead?.company}</Link></h3><CallBtn phone={data?.lead?.phone} /></div>
        {row?.needs_review && <div className="ebd-note"><b>Pricing question.</b> There's no approved offer on file, so this needs your answer. AI won't quote a price.</div>}
        <div className="ebd-thread">{(data?.messages || []).map(m => <Message key={m.id} m={m} />)}</div>
        {row?.waiting && (
          <div style={{ marginTop: 14 }}>
            <div className="ebd-acts">
              {!row.following_up && <button className="ebd-btn sm" disabled={busy} onClick={() => suggest(false)}><Icon name="spark" size={14} />{busy ? 'Thinking…' : 'Suggest a reply'}</button>}
              <button className="ebd-btn sm" onClick={handled}>Mark answered</button>
              <Link className="ebd-btn sm" to={`${BASE}/leads/${id}`}>Book a call</Link>
            </div>
            {ai?.status === 'failed' && <div className="ebd-error" style={{ marginTop: 10 }}>AI couldn't help this time ({ai.error || 'no answer'}). Write the reply yourself, or call them.</div>}
            {ai && ai.status !== 'failed' && (
              <div className="ebd-form" style={{ marginTop: 10 }}>
                {out.summary && <p className="ebd-small" style={{ margin: 0 }}><b>What they want:</b> {out.summary}</p>}
                {(out.needs || []).map((n, i) => <div key={i} className="ebd-note">{n}</div>)}
                <label>Subject<input value={subject} onChange={e => setSubject(e.target.value)} /></label>
                <label>Your reply (edit before sending)<textarea value={draft} onChange={e => setDraft(e.target.value)} /></label>
                <p className="ebd-small ebd-muted" style={{ margin: 0 }}>Drafted by AI from what's saved on this contact. Nothing is sent until you press Send.</p>
                <div className="ebd-acts">
                  <button className="ebd-btn sm primary" disabled={busy || !draft.trim() || !subject.trim()} onClick={send}>Send reply</button>
                  <button className="ebd-btn sm" disabled={busy} onClick={() => suggest(true)}>Try again</button>
                </div>
              </div>
            )}
            {!ai && (
              <details className="ebd-more" style={{ marginTop: 10 }}>
                <summary>Write a reply yourself</summary>
                <div className="ebd-form" style={{ marginTop: 8 }}>
                  <label>Subject<input value={subject} onChange={e => setSubject(e.target.value)} placeholder={'Re: ' + (row?.reply?.subject || '').replace(/^re:\s*/i, '')} /></label>
                  <label>Message<textarea value={draft} onChange={e => setDraft(e.target.value)} /></label>
                  <div><button className="ebd-btn sm primary" disabled={busy || !draft.trim() || !subject.trim()} onClick={send}>Send reply</button></div>
                </div>
              </details>
            )}
          </div>
        )}
        {msg && <div className={msg.ok ? 'ebd-ok' : 'ebd-error'} style={{ marginTop: 10 }}>{msg.text}</div>}
      </Box>
    </section>
  )
}

// ── Outreach: campaigns and team settings ────────────────────────────────────

export function Outreach() {
  useTitle('Outreach')
  const { data, error, reload } = useLoad(`${API}/campaign-list`)
  const [creating, setCreating] = useState(false)
  return (
    <>
      <PageHead title="Outreach" sub="Each campaign has its own messages, approval and on/off switch. New campaigns start Off.">
        {data?.can_manage && <button className="ebd-btn primary" onClick={() => setCreating(true)}><Icon name="plus" size={16} />New campaign</button>}
      </PageHead>
      <Box error={error} data={data}>
        {data?.team_problems?.length > 0 && <div className="ebd-note"><b>Before any campaign can go on:</b><ul className="ebd-blockers">{data.team_problems.map(p => <li key={p}>{p}</li>)}</ul></div>}
        <div className="ebd-camps">
          {(data?.campaigns || []).map(k => (
            <Link key={k.id} to={`${BASE}/outreach/${k.id}`} className="ebd-camp">
              <div>
                <b>{k.name}</b> <span className="ebd-pill muted">{k.kind_label}</span>
                <div className="meta">{k.group ? `${k.group} · ` : ''}{k.counts.audience} contacts · {k.counts.in_followup} in follow-up · {k.counts.sent} sent · {k.counts.replied} replied{k.counts.held ? ` · ${k.counts.held} held` : ''}{k.next_send_at && k.sending_on ? ` · next email due ${whenTZ(k.next_send_at)}` : ''}</div>
                {k.problems.length > 0 && <ul className="ebd-blockers">{k.problems.slice(0, 3).map(p => <li key={p}>{p}</li>)}</ul>}
              </div>
              <div><span className={'ebd-pill ' + (CAMP_TONE[k.status.key] || 'muted')}>{k.status.label}</span></div>
            </Link>
          ))}
        </div>
        <TeamSettings />
        <Markets />
      </Box>
      {creating && <NewCampaign onClose={() => setCreating(false)} onDone={reload} />}
    </>
  )
}

function NewCampaign({ onClose, onDone }) {
  const nav = useNavigate()
  const { data: grp } = useLoad(`${API}/groups`)
  const [f, setF] = useState({ name: '', industry_key: '', market_key: '' })
  const [err, setErr] = useState(null)
  const set = k => e => setF(x => ({ ...x, [k]: e.target.value }))
  async function go() {
    try {
      const r = await api.post(`${API}/campaigns`, { name: f.name || null, industry_key: f.industry_key || null, market_key: f.market_key || null })
      onDone(); nav(`${BASE}/outreach/${r.id}`)
    } catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="ebd-modal-bg" role="dialog" aria-modal="true" aria-label="New campaign" onClick={onClose}>
      <div className="ebd-modal" onClick={e => e.stopPropagation()}>
        <h2>New campaign</h2>
        <div className="ebd-note"><b>For an event, a referral or a project:</b> pick the contacts on Leads and choose Create campaign. <Link to={`${BASE}/leads?view=unassigned`} onClick={onClose}>Go to Leads</Link></div>
        <p className="ebd-muted">Or make an <b>industry + market</b> campaign. It reaches every lead in that group by itself, with its own messages.</p>
        {err && <div className="ebd-error">{err}</div>}
        <div className="ebd-form">
          <div className="two">
            <label>Industry<select value={f.industry_key} onChange={set('industry_key')}><option value="">Any industry</option>{(grp?.industries || []).map(i => <option key={i.key} value={i.key}>{i.label}</option>)}</select></label>
            <label>Market<select value={f.market_key} onChange={set('market_key')}><option value="">Any market</option>{(grp?.markets || []).map(m => <option key={m.key} value={m.key}>{m.label}</option>)}</select></label>
          </div>
          <label>Name <span className="opt">(optional)</span><input value={f.name} onChange={set('name')} placeholder="e.g. Commercial Cleaning · San Antonio" /></label>
        </div>
        <div className="ebd-modal-foot"><button className="ebd-btn" onClick={onClose}>Cancel</button><button className="ebd-btn primary" disabled={!f.industry_key && !f.market_key} onClick={go}>Create (Off)</button></div>
      </div>
    </div>
  )
}

function TeamSettings() {
  const { data, error, reload } = useLoad(`${API}/outreach`)
  const [f, setF] = useState(null)
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (data) setF({ ...data }) }, [data])
  if (error) return <div className="ebd-error">{error}</div>
  if (!data || !f) return null
  const set = k => e => setF(x => ({ ...x, [k]: e.target.value }))
  async function save() {
    setMsg(null)
    try {
      await api.put(`${API}/outreach`, { sender_name: f.sender_name, from_email: f.from_email, reply_to_email: f.reply_to_email, mailing_address: f.mailing_address })
      setMsg({ ok: true, text: 'Saved. If the sender or address changed, every campaign is off until it is approved and turned on again.' }); reload()
    } catch (e) { setMsg({ ok: false, text: errText(e) }) }
  }
  return (
    <section className="ebd-card" style={{ marginTop: 16 }} aria-label="Team settings">
      <div className="ebd-card-h"><h3>Sending settings (all campaigns)</h3></div>
      {msg && <div className={msg.ok ? 'ebd-ok' : 'ebd-error'}>{msg.text}</div>}
      {data.reply_tracking?.text && <p className="ebd-small">{data.reply_tracking.text}</p>}
      <div className="ebd-form">
        <div className="two">
          <label>From name<input value={f.sender_name || ''} onChange={set('sender_name')} disabled={!data.can_manage} /></label>
          <label>From address<input value={f.from_email || ''} onChange={set('from_email')} disabled={!data.can_manage} /></label>
        </div>
        <label>Replies go to<input value={f.reply_to_email || ''} onChange={set('reply_to_email')} disabled={!data.can_manage} /></label>
        <label>Mailing address (shown in every email)<input value={f.mailing_address || ''} onChange={set('mailing_address')} disabled={!data.can_manage} placeholder="Required by law before anything sends" /></label>
        <p className="ebd-small ebd-muted" style={{ margin: 0 }}>Each campaign has its own sending days, hours and daily limit. Texting isn't set up for this team, so any text step is held, never sent.</p>
        {data.can_manage && <div><button className="ebd-btn primary" onClick={save}>Save settings</button></div>}
      </div>
    </section>
  )
}

function Markets() {
  const { data, error, reload } = useLoad(`${API}/markets`)
  const [rows, setRows] = useState(null)
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (data) setRows(data.markets.map(m => ({ ...m }))) }, [data])
  if (error) return <div className="ebd-error">{error}</div>
  if (!data || !rows) return null
  const set = (i, k) => e => setRows(rs => rs.map((r, j) => j === i ? { ...r, [k]: e.target.value } : r))
  async function save() {
    setMsg(null)
    try { await api.put(`${API}/markets`, { markets: rows }); setMsg({ ok: true, text: 'Saved. Leads were regrouped; your own choices were kept.' }); reload() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) }
  }
  return (
    <section className="ebd-card" aria-label="Markets">
      <div className="ebd-card-h"><h3>Markets outreach may reach</h3><span className="ebd-small ebd-muted">{data.custom ? 'Your list' : 'Starting list'}</span></div>
      <p className="ebd-small" style={{ marginTop: 0 }}>A lead whose city isn't in one of these, or that names more than one, is marked <b>Unclear market</b> and waits for you. Market campaigns only reach their own market. Older territory rules aren't applied here.</p>
      {msg && <div className={msg.ok ? 'ebd-ok' : 'ebd-error'}>{msg.text}</div>}
      {rows.map((r, i) => (
        <div key={i} className="ebd-step">
          <div className="ebd-form">
            <div className="two">
              <label>Market<input value={r.label} onChange={set(i, 'label')} disabled={!data.can_manage} /></label>
              <label>Also called <span className="opt">(comma separated)</span><input value={r.aliases} onChange={set(i, 'aliases')} disabled={!data.can_manage} /></label>
            </div>
            <label>Cities <span className="opt">(comma separated)</span><textarea value={r.cities} onChange={set(i, 'cities')} disabled={!data.can_manage} style={{ minHeight: 54 }} /></label>
            {data.can_manage && <div><button className="ebd-btn sm ghost danger" onClick={() => setRows(rs => rs.filter((_, j) => j !== i))}>Remove market</button></div>}
          </div>
        </div>
      ))}
      {data.can_manage && <div className="ebd-acts"><button className="ebd-btn sm" onClick={() => setRows(rs => [...rs, { label: '', aliases: '', cities: '' }])}><Icon name="plus" size={14} />Add market</button><button className="ebd-btn sm primary" onClick={save}>Save markets</button></div>}
    </section>
  )
}

export function Campaign() {
  const { id } = useParams()
  const { data: base, error: e1, reload: r1 } = useLoad(`${API}/campaigns/${id}`)
  const { data: mem, error: e2, reload: r2 } = useLoad(`${API}/campaigns/${id}/members`)
  const reload = useCallback(() => Promise.all([r1(), r2()]), [r1, r2])
  useTitle(base?.name || 'Campaign')
  const [steps, setSteps] = useState(null)
  const [meta, setMeta] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(null)
  const [ai, setAi] = useState(null)
  const [testTo, setTestTo] = useState('')
  useEffect(() => {
    if (!base) return
    setSteps(base.steps.map(s => ({ ...s })))
    setMeta({ name: base.name, offer: base.offer || '', booking_url: base.booking_url || '', daily_limit: base.daily_limit })
    setAi(base.ai_draft && base.ai_draft.outcome == null && base.ai_draft.status !== 'failed' ? base.ai_draft : null)
    api.get(`${API}/campaigns/${id}/examples`).then(setPreview).catch(() => setPreview(null))
  }, [base, id])
  useEffect(() => { if (mem?.my_email) setTestTo(t => t || mem.my_email) }, [mem])
  const dirty = useMemo(() => base && steps && meta && (JSON.stringify(steps) !== JSON.stringify(base.steps) || meta.offer !== (base.offer || '')
    || meta.booking_url !== (base.booking_url || '') || meta.name !== base.name || Number(meta.daily_limit) !== base.daily_limit), [base, steps, meta])
  async function run(fn, ok) {
    setBusy(true); setMsg(null)
    try { const r = await fn(); setMsg({ ok: true, text: typeof ok === 'function' ? ok(r) : ok }); await reload() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  async function aiDraft(again) {
    setBusy(true); setMsg(null)
    try {
      const r = await api.post(`${API}/campaigns/${id}/ai-draft${again ? '?again=true' : ''}`, {})
      if (!r || r.status === 'failed') setMsg({ ok: false, text: `AI couldn't draft this time${r?.error ? ` (${r.error})` : ''}. Your messages are unchanged.` })
      else setAi(r)
    } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const error = e1 || e2
  if (error) return <div className="ebd-error">{error}</div>
  if (!base || !mem || !steps || !meta) return <div className="ebd-loading">Loading…</div>
  const can = mem.can_manage
  const st = mem.status
  const setStep = (i, k, v) => setSteps(ss => ss.map((s, j) => j === i ? { ...s, [k]: k === 'day' ? Number(v) : v } : s))
  return (
    <>
      <Link className="ebd-crumb" to={`${BASE}/outreach`}><Icon name="back" size={14} />Outreach</Link>
      <PageHead title={base.name} sub={`${mem.kind_label}${mem.context ? ' · ' + mem.context : ''}${mem.group ? ' · ' + mem.group : ''} · version ${base.version}${base.approved_version ? ` (approved: v${base.approved_version})` : ''}`}>
        <span className={'ebd-pill ' + (CAMP_TONE[st.key] || 'muted')}>{st.label}</span>
        {can && !mem.archived && (
          <span className="ebd-switch">
            <button aria-label={base.sending_on ? 'Turn campaign off' : 'Turn campaign on'} className={base.sending_on ? 'on' : ''} disabled={busy || (!base.sending_on && (!base.approved || base.problems.length > 0)) || dirty}
                    onClick={() => run(() => api.post(`${API}/campaigns/${id}/switch`, { on: !base.sending_on }), r => r.sending_on ? 'On. Emails go out in its sending hours.' : 'Off. Queued emails wait; nothing is lost.')} />
            {base.sending_on ? 'On' : 'Off'}
          </span>
        )}
      </PageHead>
      {msg && <div className={msg.ok ? 'ebd-ok' : 'ebd-error'}>{msg.text}</div>}
      {base.approval_note && <div className="ebd-note">{base.approval_note}</div>}
      {base.problems.length > 0 && <div className="ebd-note"><b>What's blocking launch:</b><ul className="ebd-blockers">{base.problems.map(p => <li key={p}>{p}</li>)}</ul><span className="ebd-small">You can still add contacts and plan while these are open.</span></div>}
      {!base.problems.length && !base.approved && !dirty && <div className="ebd-note">Approve this version to be able to turn the campaign on. Approval alone sends nothing.</div>}

      <div className="ebd-kv" style={{ marginBottom: 16 }}>
        <div><b>{mem.picked ? mem.counts.audience : base.audience.matching_leads}</b><span>{mem.picked ? 'Contacts' : 'Leads in group'}</span></div>
        <div><b>{mem.counts.in_followup}</b><span>In follow-up</span></div>
        <div><b>{mem.picked ? mem.counts.waiting : base.audience.waiting}</b><span>Waiting for On</span></div>
        <div><b>{mem.counts.held}</b><span>Held</span></div>
        <div><b>{mem.counts.sent}</b><span>Sent</span></div>
        <div><b>{mem.counts.replied}</b><span>Replied</span></div>
      </div>
      <p className="ebd-small">{base.hours} · up to {base.daily_limit} a day{mem.next_send_at && base.sending_on ? ` · next email due ${whenTZ(mem.next_send_at)}; it goes out in the sending window` : ''}</p>

      <div className="ebd-grid2">
        <section className="ebd-card" aria-label="Messages">
          <div className="ebd-card-h"><h3>Messages</h3>{can && <button className="ebd-btn sm" disabled={busy} onClick={() => aiDraft(false)}><Icon name="spark" size={14} />Draft with AI</button>}</div>
          {ai && (
            <div className="ebd-note">
              <b>AI draft</b> <span className="ebd-small ebd-muted">(nothing changes until you use it, then approve it)</span>
              {(ai.output?.problems || []).map((p, i) => <div key={i} className="ebd-small" style={{ color: 'var(--warn)' }}>{p}</div>)}
              {(ai.output?.steps || []).map((s, i) => <div key={i} className="ebd-msg" style={{ marginTop: 8 }}><header><span>Message {i + 1} · day {s.day}</span></header><div style={{ fontWeight: 700 }}>{s.subject}</div><pre>{s.body}</pre></div>)}
              {ai.output?.notes && <p className="ebd-small">{ai.output.notes}</p>}
              <div className="ebd-acts" style={{ marginTop: 8 }}>
                <button className="ebd-btn sm primary" disabled={busy || dirty || !(ai.output?.steps || []).length} onClick={() => run(() => api.post(`${API}/campaigns/${id}/ai-draft/${ai.id}/apply`, {}), 'Draft applied. Review it, approve it, then turn it on.').then(() => setAi(null))}>Use this draft</button>
                <button className="ebd-btn sm" disabled={busy} onClick={() => aiDraft(true)}>Try again</button>
                <button className="ebd-btn sm ghost" onClick={() => setAi(null)}>Dismiss</button>
              </div>
            </div>
          )}
          <div className="ebd-form">
            <div className="two">
              <label>Name<input value={meta.name} onChange={e => setMeta(m => ({ ...m, name: e.target.value }))} disabled={!can} /></label>
              <label>Daily limit<input type="number" min="1" max="300" value={meta.daily_limit} onChange={e => setMeta(m => ({ ...m, daily_limit: e.target.value }))} disabled={!can} /></label>
            </div>
            <label>Offer <span className="opt">(only used where a message says {'{offer}'}; leave blank if you haven't decided)</span><textarea value={meta.offer} onChange={e => setMeta(m => ({ ...m, offer: e.target.value }))} disabled={!can} style={{ minHeight: 60 }} /></label>
            <label>Booking link<input value={meta.booking_url} onChange={e => setMeta(m => ({ ...m, booking_url: e.target.value }))} disabled={!can} /></label>
          </div>
          <p className="ebd-small ebd-muted">Fill-ins: {mem.placeholders.map(p => `{${p}}`).join(' ')}. {'{event}'}, {'{referrer}'}, {'{personal_note}'}, {'{industry}'} and {'{market}'} only come from what's saved on the contact; anyone without it is held for you.</p>
          {steps.map((s, i) => (
            <div key={i} className="ebd-step">
              <div className="ebd-step-h">Message {i + 1} · day <input type="number" min="0" max="90" value={s.day} onChange={e => setStep(i, 'day', e.target.value)} disabled={!can} aria-label={`Day for message ${i + 1}`} />
                {s.channel === 'text' && <span className="ebd-pill warn">Text: held, not sent</span>}
                {can && <button className="ebd-btn sm ghost danger" onClick={() => setSteps(ss => ss.filter((_, j) => j !== i))}>Remove</button>}</div>
              <input aria-label={`Subject ${i + 1}`} value={s.subject || ''} onChange={e => setStep(i, 'subject', e.target.value)} disabled={!can} style={{ border: '1px solid #d5dce8', borderRadius: 10, padding: '9px 12px' }} />
              <textarea aria-label={`Message ${i + 1}`} value={s.body || ''} onChange={e => setStep(i, 'body', e.target.value)} disabled={!can} style={{ border: '1px solid #d5dce8', borderRadius: 10, padding: '9px 12px', minHeight: 140 }} />
            </div>
          ))}
          {can && (
            <div className="ebd-acts">
              <button className="ebd-btn sm" onClick={() => setSteps(ss => [...ss, { day: (ss[ss.length - 1]?.day || 0) + 4, channel: 'email', subject: '', body: '' }])}><Icon name="plus" size={14} />Add message</button>
              <button className="ebd-btn sm primary" disabled={busy || !dirty} onClick={() => run(() => api.put(`${API}/campaigns/${id}`, { name: meta.name, offer: meta.offer, booking_url: meta.booking_url, daily_limit: Number(meta.daily_limit) || base.daily_limit, steps }), r => r.version !== base.version ? 'Saved as a new version. Approve it, then turn it on.' : 'Saved.')}>Save changes</button>
              <button className="ebd-btn sm" disabled={busy || dirty || base.approved || base.problems.length > 0} onClick={() => run(() => api.post(`${API}/campaigns/${id}/approve`, {}), 'Approved this exact version. Turn it on when ready.')}>{base.approved ? 'Approved' : 'Approve this version'}</button>
            </div>
          )}
        </section>
        <div>
          <section className="ebd-card" aria-label="Preview">
            <h3>Preview with real contacts</h3>
            {dirty && <p className="ebd-small ebd-muted">Save to refresh the preview.</p>}
            {!preview?.examples?.length ? <p className="ebd-empty">Add contacts to see real examples.</p> : preview.examples.map((ex, n) => (
              <details key={ex.lead.id} open={n === 0} style={{ marginBottom: 10 }}>
                <summary><b>{ex.lead.company}</b> <span className="ebd-small ebd-muted">{ex.lead.email || 'no email'}</span></summary>
                {ex.steps.slice(0, 2).map((s, i) => (
                  <div key={i} className="ebd-msg" style={{ marginTop: 8 }}>
                    <header><span>Message {i + 1} · day {s.day}</span></header>
                    <div style={{ fontWeight: 700 }}>{s.subject}</div><pre>{s.text}</pre>
                    <div className="ebd-facts-used">{s.facts_used.map(f => <span key={f.key} className="ebd-pill blue" title={f.from}>{f.key}</span>)}{s.missing.map(m => <span key={m} className="ebd-pill warn">missing: {m}</span>)}</div>
                  </div>
                ))}
              </details>
            ))}
            {can && (
              <div className="ebd-form" style={{ marginTop: 10 }}>
                <label>Send message 1 to yourself<input type="email" value={testTo} onChange={e => setTestTo(e.target.value)} /></label>
                <div><button className="ebd-btn sm" disabled={busy || !testTo} onClick={() => run(() => api.post(`${API}/outreach/test`, { to: testTo, step: 1, campaign_id: id }), r => `Test sent to ${r.sent_to}.`)}>Send test to me</button></div>
              </div>
            )}
          </section>
          <section className="ebd-card" aria-label="Audience">
            <div className="ebd-card-h"><h3>{mem.picked ? 'Audience' : 'In follow-up'}</h3>{mem.picked && <Link className="ebd-btn sm" to={`${BASE}/leads?campaign=none`}>Add from Leads</Link>}</div>
            {!mem.picked && <p className="ebd-small ebd-muted" style={{ marginTop: 0 }}>Every lead in {mem.group || 'this group'} joins by itself once the campaign is on.</p>}
            {!mem.audience.length ? <p className="ebd-empty">No contacts yet.</p> : mem.audience.map(a => (
              <div key={a.id} className="ebd-revrow" style={{ justifyContent: 'space-between' }}>
                <span><Link to={`${BASE}/leads/${a.id}`}>{a.company}</Link> <span className="ebd-small ebd-muted">{a.email || a.phone || ''}</span></span>
                <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}><Status o={a.outreach} />
                  {can && mem.picked && <button className="ebd-btn sm ghost danger" aria-label={`Remove ${a.company}`} onClick={() => run(() => api.delete(`${API}/campaigns/${id}/leads/${a.id}`), 'Removed.')}>✕</button>}</span>
              </div>
            ))}
          </section>
          {can && mem.picked && !mem.archived && <button className="ebd-btn sm danger" onClick={() => { if (window.confirm('Archive this campaign? Its contacts stop getting its emails.')) run(() => api.post(`${API}/campaigns/${id}/archive`, {}), 'Archived.') }}>Archive campaign</button>}
        </div>
      </div>
    </>
  )
}

// ── Appointments ─────────────────────────────────────────────────────────────

export function Appointments() {
  useTitle('Appointments')
  const { data, error } = useLoad(`${API}/calls`)
  return (
    <>
      <PageHead title="Appointments" sub="Intro calls and demos. Times in Central." />
      <Box error={error} data={data}>
        {data?.problems?.length > 0 && (
          <section className="ebd-card"><h3><Icon name="warn" />Needs attention</h3>
            {data.problems.map(a => <div key={a.id} className="ebd-item"><div><b>{a.company || a.title}</b><div className="who">{whenTZ(a.starts_at)}</div><span className="ebd-pill gold">{a.problem}</span></div>{a.lead_id && <Link className="ebd-btn sm" to={`${BASE}/leads/${a.lead_id}`}>Open</Link>}</div>)}
          </section>
        )}
        <section className="ebd-card"><h3>Upcoming</h3>
          {data?.upcoming?.length ? data.upcoming.map(a => (
            <div key={a.id} className="ebd-item"><div><b>{a.company || a.title}</b><div className="who">{whenTZ(a.starts_at)}{a.contact && a.contact !== a.company ? ` · ${a.contact}` : ''}</div><span className="ebd-pill blue">{a.status}</span></div>{a.lead_id && <Link className="ebd-btn sm" to={`${BASE}/leads/${a.lead_id}`}>Open</Link>}</div>
          )) : <p className="ebd-empty">No calls booked. Book one from a contact's page, or they book from the link in your emails.</p>}
        </section>
        {data?.recent?.length > 0 && <section className="ebd-card"><h3>Recent</h3>{data.recent.map(a => <div key={a.id} className="ebd-revrow">{whenTZ(a.starts_at)} · {a.company || a.title} <span className="ebd-pill muted">{a.status}</span></div>)}</section>}
      </Box>
    </>
  )
}

// ── Results ──────────────────────────────────────────────────────────────────

const BY = [['campaign', 'By campaign'], ['source', 'By source'], ['industry', 'By industry'], ['market', 'By market']]

export function Results() {
  useTitle('Results')
  const [days, setDays] = useState(90)
  const [by, setBy] = useState('campaign')
  const { data, error } = useLoad(`${API}/results/breakdown?by=${by}&days=${days}`)
  const cols = [['leads', 'Leads'], ['contacted', 'Emailed'], ['replied', 'Replied'], ['interested', 'Interested'], ['intro_booked', 'Intro booked'], ['intro_completed', 'Intro held'], ['demos', 'Demos'], ['signed_up', 'Signed up']]
  const rows = data?.rows || []
  const tot = Object.fromEntries(cols.map(([k]) => [k, rows.reduce((n, r) => n + (r[k] || 0), 0)]))
  return (
    <>
      <PageHead title="Results" sub="Replies, booked intro calls, completed calls, demos and signups, kept separate.">
        <select value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Period" className="ebd-btn"><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option><option value={365}>Last year</option></select>
      </PageHead>
      <div className="ebd-views" role="group" aria-label="Group results">{BY.map(([k, l]) => <button key={k} className={by === k ? 'on' : ''} onClick={() => setBy(k)}>{l}</button>)}</div>
      <Box error={error} data={data}>
        <div className="ebd-kv" style={{ marginBottom: 16 }}>{cols.map(([k, l]) => <div key={k}><b>{tot[k]}</b><span>{l}</span></div>)}</div>
        <section className="ebd-card">
          <div style={{ overflowX: 'auto' }}>
            <table className="ebd-table" style={{ display: 'table' }}>
              <thead><tr><th />{cols.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
              <tbody>{rows.map(r => <tr key={r.name}><td><b>{r.name}</b></td>{cols.map(([k]) => <td key={k}>{r[k]}</td>)}</tr>)}</tbody>
            </table>
          </div>
          <p className="ebd-small ebd-muted">Emails and replies count in the period chosen; leads, calls, demos and signups count for the leads in it.</p>
        </section>
      </Box>
    </>
  )
}
