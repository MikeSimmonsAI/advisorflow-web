/**
 * EVOSYS SALES (/program/evosys-sales/*) - owner request 2026-11-16, PR #663.
 *
 * EvoSys's own sales program: bring in service businesses, work the leads,
 * book intro calls and demos, track sign-ups. Six screens: Today, Leads,
 * Conversations, Outreach, Appointments, Results. Design baseline: the PR #663
 * screenshots and the approved Today mockup (slim navy sidebar with the
 * official EP emblem, light workspace, blue actions, gold reply/booking
 * accents, six phone tabs, visible return to Program Access).
 *
 * The server is the authority (/sales/evosys/*): only the EvoSys Pro sales
 * team gets in, reps see their own leads, managers change campaigns and
 * groups. AI only suggests; a person decides.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import CallPanel from './CallPanel'
import './evosysSales.css'
import './evosysV2.css'

export const BASE = '/program/evosys-sales'
export const API = '/sales/evosys'
const EMBLEM = '/brands/evosyspro-emblem-96.png'

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
  people: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21a7 7 0 0 1 14 0M17 11a3 3 0 1 0 0-6M22 21a6 6 0 0 0-5-6',
  warn: 'M12 3 2 21h20zM12 10v5M12 18v.01',
  spark: 'M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z',
  tag: 'M3 12V4h8l10 10-8 8zM7.5 7.5v.01',
  copy: 'M8 8h12v12H8zM4 16V4h12',
  fork: 'M12 3v6M6 21a3 3 0 1 0 0-.01M18 21a3 3 0 1 0 0-.01M12 9 6 18M12 9l6 9',
  off: 'M3 3l18 18M10.6 5.1A7 7 0 0 1 19 12M6 7.5A7 7 0 0 0 12 19',
  upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
}
export function Icon({ name, size = 20 }) {
  return (
    <svg className="evs-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name]} />
    </svg>
  )
}

const NAV = [
  { to: BASE, label: 'Inbox', icon: 'chat', end: true },
  { to: `${BASE}/leads`, label: 'Leads', icon: 'leads' },
  { to: `${BASE}/appointments`, label: 'Calls', icon: 'cal' },
  { to: `${BASE}/outreach`, label: 'Campaigns', icon: 'send' },
  { to: `${BASE}/results`, label: 'Results', icon: 'chart' },
]

function AiSwitch() {
  // The same setting as Campaigns > AI answers simple texts (text_auto_reply).
  const [on, setOn] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.get(`${API}/outreach`).then(s => setOn(!!s.text_auto_reply)).catch(() => setOn(null)) }, [])
  if (on === null) return null
  const flip = async () => {
    setBusy(true)
    try { const r = await api.put(`${API}/outreach`, { text_auto_reply: !on }); setOn(!!r.text_auto_reply) } catch { /* stays as it was */ } finally { setBusy(false) }
  }
  return (
    <button className={'v2-aiswitch' + (on ? ' on' : '')} onClick={flip} disabled={busy} aria-pressed={on}
            title={on ? 'AI answers simple texts by itself. Click to turn off.' : 'AI only drafts. Click to let it answer simple texts.'}>
      <span>AI replies</span><b>{on ? 'ON' : 'OFF'}</b>
    </button>
  )
}

export function errText(e) {
  if (typeof e?.detail === 'string') return e.detail
  return e?.message || 'Something went wrong. Please try again.'
}

export function useLoad(path) {
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

export function when(iso, opts) {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, opts || { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
export const LONG = { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }

export function Status({ o }) {
  if (!o) return null
  const tone = { active: 'blue', replied: 'gold', booked: 'good', finished: 'muted', no_email: 'warn',
                 waiting: 'muted', opted_out: 'bad', email_failed: 'bad', excluded: 'bad' }[o.key] || 'muted'
  return <span className={'evs-pill evs-' + tone}>{o.label}</span>
}

export function ReviewPill({ status, label }) {
  if (!status || status === 'ok') return null
  const tone = { unclear_market: 'warn', possible_duplicate: 'warn', held: 'muted', excluded: 'bad' }[status] || 'muted'
  return <span className={'evs-pill evs-' + tone}>{label || status}</span>
}

export function Box({ error, data, children }) {
  if (error) return <div className="evs-error" role="alert">{error}</div>
  if (!data) return <div className="evs-loading">Loading…</div>
  return children
}

export function CallBtn({ lead, label = 'Call' }) {
  // Calls go through the EvoSys Sales number in the browser, never tel:.
  const [open, setOpen] = useState(false)
  if (!lead?.phone || lead?.outreach?.key === 'opted_out') return null
  return (
    <>
      <button className="evs-act evs-act-call" onClick={e => { e.preventDefault(); e.stopPropagation(); setOpen(true) }}><Icon name="phone" size={16} />{label}</button>
      {open && <div className="v2-modal" onClick={e => { e.stopPropagation(); if (e.target === e.currentTarget) setOpen(false) }}><CallPanel lead={lead} onClose={() => setOpen(false)} /></div>}
    </>
  )
}

export function PageHead({ title, sub, children }) {
  return (
    <div className="evs-head">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {children && <div className="evs-head-acts">{children}</div>}
    </div>
  )
}

// ── shell ────────────────────────────────────────────────────────────────────

export default function EvoSysSalesShell() {
  const user = getCurrentUser() || {}
  const name = user.full_name || user.name || ''
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || 'ES'
  const { pathname } = useLocation()
  useEffect(() => { window.scrollTo?.(0, 0) }, [pathname])
  useEffect(() => {
    // Cormorant Garamond + Inter. Loaded as a plain <link>: if it can't load,
    // the screen still works with the fallback fonts.
    if (document.getElementById('evs-v2-fonts')) return
    const l = document.createElement('link')
    l.id = 'evs-v2-fonts'; l.rel = 'stylesheet'
    l.href = 'https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap'
    document.head.appendChild(l)
  }, [])
  return (
    <div className="evs v2">
      <aside className="evs-side" aria-label="EvoSys Sales">
        <div className="evs-logo">
          <span className="v2-ep">EP</span>
          <div><b>EvoSys Sales</b></div>
        </div>
        <nav className="evs-nav">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => 'evs-navlink' + (isActive ? ' on' : '')}>
              <Icon name={n.icon} />{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="evs-side-foot">
          <Link to="/workspaces" className="evs-back"><Icon name="back" size={16} />Program Access</Link>
          <div className="evs-me"><span className="evs-avatar">{initials}</span><span>{name || 'Signed in'}</span></div>
          <AiSwitch />
        </div>
      </aside>

      <header className="evs-mtop">
        <span className="v2-ep">EP</span>
        <b>EvoSys Sales</b>
        <span className="v2-mtop-me">{(name || '').split(' ')[0]}</span>
        <AiSwitch />
      </header>

      <main className="evs-main"><Outlet /></main>

      <nav className="evs-tabbar" aria-label="EvoSys Sales sections">
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => 'evs-tab' + (isActive ? ' on' : '')}>
            <Icon name={n.icon} size={20} /><span>{n.short || n.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

// ── Today ────────────────────────────────────────────────────────────────────

const ATT_ICON = { possible_duplicate: 'copy', unclear_market: 'fork', reply_needs_person: 'tag', ai_failed: 'spark',
                   campaign_paused: 'off', campaign_unapproved: 'warn', campaign_blocked: 'warn', sender_disconnected: 'warn',
                   bounce: 'warn', booking_issue: 'cal', unmatched_reply: 'chat', no_contact: 'warn' }

function attLink(i) {
  if (i.lead_id) return `${BASE}/leads/${i.lead_id}`
  if (i.campaign_id) return `${BASE}/outreach?campaign=${i.campaign_id}`
  if (['possible_duplicate', 'unclear_market', 'no_contact'].includes(i.kind)) return `${BASE}/leads?view=needs_review`
  if (['sender_disconnected'].includes(i.kind)) return `${BASE}/outreach`
  return BASE
}

export function Today() {
  const { data, error } = useLoad(`${API}/overview`)
  const att = useLoad(`${API}/attention`)
  const camps = useLoad(`${API}/campaigns`)
  const [tab, setTab] = useState('replies')
  const c = data?.counts || {}
  const replies = data?.replies || []
  const items = att.data?.items || []
  const aiItems = items.filter(i => i.kind === 'reply_needs_person')
  const review = items.filter(i => ['possible_duplicate', 'unclear_market', 'no_contact'].includes(i.kind))
  const other = items.filter(i => !aiItems.includes(i) && !review.includes(i))
  const onCount = (camps.data?.campaigns || []).filter(x => x.can_send).length
  const next = (data?.appointments || [])[0]
  return (
    <Box error={error} data={data}>
      <PageHead title="Today">
        <Link to={`${BASE}/outreach`} className={'evs-chipbtn' + (onCount ? ' on' : '')}>
          <Icon name={onCount ? 'send' : 'off'} size={16} />{onCount ? `Outreach on · ${onCount}` : 'Outreach off'}
        </Link>
        <Link to={`${BASE}/conversations`} className="evs-chipbtn evs-chipbtn-blue"><Icon name="reply" size={16} />{replies.length} {replies.length === 1 ? 'reply' : 'replies'} waiting</Link>
        <Link to={`${BASE}/leads?add=1`} className="evs-btn evs-btn-add"><Icon name="plus" size={18} />Add leads</Link>
      </PageHead>

      <div className="evs-strip">
        <Link to={`${BASE}/conversations`} className="evs-strip-i gold"><Icon name="chat" size={26} /><div><b>{replies.length}</b><span>Replies</span></div></Link>
        <a href="#queue" onClick={() => setTab('calls')} className="evs-strip-i"><Icon name="phone" size={26} /><div><b>{c.to_call ?? 0}</b><span>To call</span></div></a>
        <Link to={`${BASE}/leads?view=followup`} className="evs-strip-i"><Icon name="people" size={26} /><div><b>{c.in_followup ?? 0}</b><span>In follow-up</span></div></Link>
        <Link to={`${BASE}/appointments`} className="evs-strip-i"><Icon name="cal" size={26} /><div><b>{c.booked ?? 0}</b><span>Booked</span></div></Link>
      </div>

      <div className="evs-today">
        <section className="evs-card evs-queue" id="queue">
          <h2>Work queue</h2>
          <div className="evs-tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'replies'} className={tab === 'replies' ? 'on' : ''} onClick={() => setTab('replies')}><Icon name="chat" size={16} />Replies ({replies.length})</button>
            <button role="tab" aria-selected={tab === 'calls'} className={tab === 'calls' ? 'on' : ''} onClick={() => setTab('calls')}><Icon name="phone" size={16} />Calls ({c.to_call ?? 0})</button>
            <button role="tab" aria-selected={tab === 'review'} className={tab === 'review' ? 'on' : ''} onClick={() => setTab('review')}><Icon name="warn" size={16} />Needs review ({items.length})</button>
          </div>
          {tab === 'replies' && (replies.length ? replies.map((m, i) => (
            <div key={m.id} className={'evs-qrow' + (i === 0 ? ' first' : '')}>
              <Link to={`${BASE}/leads/${m.lead.id}`} className="evs-row-main">
                <b>{m.lead.company}</b>
                <small>{m.lead.contact} · Replied {when(m.at)}</small>
                <span className="evs-snip">“{m.body}”</span>
              </Link>
              <Link className={'evs-act ' + (i === 0 ? 'evs-act-primary' : '')} to={`${BASE}/leads/${m.lead.id}`}>{i === 0 ? 'Open reply' : 'Review reply'}<Icon name="arrow" size={14} /></Link>
            </div>
          )) : <p className="evs-empty">No replies waiting. A reply to a follow-up email shows up here by itself and stops that lead's emails.</p>)}
          {tab === 'calls' && (data?.to_call?.length ? data.to_call.map(l => (
            <div key={l.id} className="evs-qrow">
              <Link to={`${BASE}/leads/${l.id}`} className="evs-row-main"><b>{l.company}</b><small>{l.contact || 'No name'} · {l.phone} · no email on file</small></Link>
              <div className="evs-row-acts"><CallBtn lead={l} /><Link className="evs-act" to={`${BASE}/leads/${l.id}#log`}>Log call</Link></div>
            </div>
          )) : <p className="evs-empty">Nobody to call right now.</p>)}
          {tab === 'review' && (items.length ? items.map((i, k) => (
            <div key={k} className="evs-qrow">
              <div className="evs-row-main"><b>{i.title}</b><small>{i.text}</small></div>
              <Link className="evs-act" to={attLink(i)}>{i.action}<Icon name="arrow" size={14} /></Link>
            </div>
          )) : <p className="evs-empty">Nothing needs you right now.</p>)}
        </section>

        <div className="evs-side-col">
          <section className="evs-card">
            <h3 className="evs-h3i"><Icon name="spark" />AI suggestions</h3>
            {aiItems.length ? aiItems.slice(0, 3).map((i, k) => (
              <div key={k} className="evs-aibox">
                <Icon name="tag" size={22} />
                <div>
                  <b>{i.title.split(': ').slice(1).join(': ') || i.title}</b>
                  <small>{i.text}</small>
                  <Link className="evs-btn evs-btn-sm" to={attLink(i)}>Open conversation<Icon name="arrow" size={14} /></Link>
                </div>
              </div>
            )) : <p className="evs-empty">When AI reads a reply and something needs you - a price question with no approved offer, a wrong person - it shows here.</p>}
          </section>
          <section className="evs-card">
            <h3 className="evs-h3i"><Icon name="warn" />Needs review</h3>
            {review.length || other.length ? (
              <>
                {[...review, ...other].slice(0, 5).map((i, k) => (
                  <Link key={k} to={attLink(i)} className="evs-attrow"><Icon name={ATT_ICON[i.kind] || 'warn'} size={18} /><span>{i.title}</span></Link>
                ))}
                <Link className="evs-more" to={`${BASE}/leads?view=needs_review`}>Review leads <Icon name="arrow" size={14} /></Link>
              </>
            ) : <p className="evs-empty">All clear.</p>}
          </section>
          <section className="evs-card">
            <h3 className="evs-h3i"><Icon name="cal" />Next booked call</h3>
            {next ? (
              <Link to={next.lead_id ? `${BASE}/leads/${next.lead_id}` : `${BASE}/appointments`} className="evs-nextcall">
                <div><b>{next.company || next.title}</b><small>{when(next.starts_at, LONG)}</small></div><Icon name="arrow" />
              </Link>
            ) : <p className="evs-empty">No calls booked yet.</p>}
          </section>
        </div>
      </div>
    </Box>
  )
}

// ── Leads ────────────────────────────────────────────────────────────────────

const VIEWS = [['all', 'All'], ['needs_review', 'Needs review'], ['followup', 'In follow-up'], ['no_email', 'No email'],
               ['replied', 'Replied'], ['booked', 'Booked'], ['held', 'Held'], ['excluded', 'Excluded'], ['dnc', 'Do Not Contact']]

export function Leads() {
  const loc = useLocation()
  const params = new URLSearchParams(loc.search)
  const [view, setView] = useState(params.get('view') || 'all')
  const [group, setGroup] = useState(params.get('group') || '')
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(params.get('add') === '1')
  const meta = useLoad(`${API}/groups`)
  const { data, error, reload } = useLoad(`${API}/leads?view=${view}&group=${encodeURIComponent(group)}`)
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase()
    return (data?.leads || []).filter(l => !n || [l.company, l.contact, l.phone, l.email, l.source_record_id].join(' ').toLowerCase().includes(n))
  }, [data, q])
  const rv = meta.data?.review || {}
  return (
    <>
      <PageHead title="Leads" sub={data ? `${rows.length} of ${data.total} businesses` : 'Businesses EvoSys is signing up'}>
        {meta.data?.can_manage && <button className="evs-btn evs-btn-add" onClick={() => setAdding(a => !a)}><Icon name="plus" size={18} />Add leads</button>}
      </PageHead>
      {adding && meta.data && <ImportPanel meta={meta.data} onDone={() => { reload(); meta.reload() }} onClose={() => setAdding(false)} />}
      <div className="evs-filters">
        {VIEWS.map(([k, l]) => (
          <button key={k} className={'evs-chip' + (view === k ? ' on' : '')} onClick={() => setView(k)}>
            {l}{k === 'needs_review' && (rv.unclear_market || 0) + (rv.possible_duplicate || 0) ? ` (${(rv.unclear_market || 0) + (rv.possible_duplicate || 0)})` : ''}
          </button>
        ))}
      </div>
      <div className="evs-filters">
        <select className="evs-input evs-groupsel" value={group} onChange={e => setGroup(e.target.value)} aria-label="Group">
          <option value="">Every industry and market</option>
          {(meta.data?.groups || []).map(g => {
            const k = `${g.industry_key || ''}|${g.market_key || ''}`
            return <option key={k} value={k}>{g.label} ({g.leads})</option>
          })}
        </select>
        <input className="evs-input evs-search" placeholder="Search company, name, phone, source ID…" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      <Box error={error} data={data}>
        <div className="evs-card evs-list">
          {rows.length ? rows.map(l => (
            <div key={l.id} className="evs-row">
              <LogoThumb lead={l} />
              <Link to={`${BASE}/leads/${l.id}`} className="evs-row-main">
                <b>{l.company}</b>
                <small>{[l.contact, l.phone, l.email].filter(Boolean).join(' · ')}</small>
                <span className="evs-meta">{[l.industry || 'Industry unclear', l.market || (l.state ? `${l.city ? l.city + ', ' : ''}${l.state} - market unclear` : 'Market unclear'), l.source_label, l.source_record_id].filter(Boolean).join(' · ')}</span>
              </Link>
              <div className="evs-row-acts">
                <ReviewPill status={l.review_status} label={l.review_label} />
                <Status o={l.outreach} />
                <CallBtn lead={l} />
                <Link className="evs-act" to={`${BASE}/leads/${l.id}`}>Open</Link>
              </div>
            </div>
          )) : <p className="evs-empty">No leads here.</p>}
        </div>
      </Box>
    </>
  )
}

// The existing importer (/intake/sales/<team>/...): preview, duplicate checks,
// held rows, safe retry. Nothing is sent and nobody joins a campaign by importing.
function ImportPanel({ meta, onDone, onClose }) {
  const team = meta.team_id
  const [source, setSource] = useState('facebook_ads')
  const [file, setFile] = useState(null)
  const [batch, setBatch] = useState(null)
  const [rows, setRows] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [done, setDone] = useState(null)
  const [mode, setMode] = useState('one')
  const [one, setOne] = useState({ company: '', contact: '', phone: '', email: '', city: '', state: '', industry: 'Commercial cleaning', notes: '', spanish: false })
  const [camps, setCamps] = useState([])
  const [campId, setCampId] = useState('')
  const [added, setAdded] = useState(null)
  useEffect(() => { api.get(`${API}/campaigns`).then(r => {
    const list = (r.campaigns || []).filter(c => c.kind !== 'group' && !c.archived_at)
    setCamps(list)
    const fb = list.find(c => /FB Cleaners - Texts$/.test(c.name || '')) || list[0]
    if (fb) setCampId(fb.id)
  }).catch(() => {}) }, [])
  const base = `/intake/sales/${team}/batches`
  const csvCell = v => '"' + String(v || '').replace(/"/g, '""') + '"'
  const oneAsFile = () => {
    const head = ['company', 'contact', 'phone', 'email', 'city', 'state', 'industry', 'source ID', 'notes']
    const notes = [one.notes, one.spanish ? 'Spanish only - write to them in Spanish' : ''].filter(Boolean).join('. ')
    const row = [one.company, one.contact, one.phone, one.email, one.city, one.state, one.industry,
                 'ONE-' + Date.now().toString(36).toUpperCase(), notes]
    return new File([head.map(csvCell).join(',') + '\n' + row.map(csvCell).join(',') + '\n'], 'one-lead.csv', { type: 'text/csv' })
  }
  const step = async fn => { setBusy(true); setMsg(null); try { await fn() } catch (e) { setMsg(errText(e)) } finally { setBusy(false) } }
  const upload = () => step(async () => {
    const fd = new FormData()
    fd.append('file', mode === 'one' ? oneAsFile() : file); fd.append('source_system', source)
    const b = await api.post(base, fd)
    const a = await api.post(`${base}/${b.id}/analyze`, {})
    setBatch(a)
    setRows((await api.get(`${base}/${b.id}/rows`)).items)
  })
  const create = () => step(async () => {
    const r = await api.post(`${base}/${batch.id}/commit`, { confirm_target_id: team })
    if (mode === 'one' && campId && (r.created || 0) > 0) {
      const digits = (one.phone || '').replace(/\D/g, '').slice(-10)
      const found = await api.get(`${API}/leads?q=${encodeURIComponent(digits || one.company)}`)
      const lead = (found.leads || []).find(l => (l.phone || '').replace(/\D/g, '').endsWith(digits) || l.company === one.company)
      if (lead) {
        try { const a = await api.post(`${API}/campaigns/${campId}/leads`, { lead_ids: [lead.id] }); setAdded({ ok: (a.added || 0) > 0, lead, out: a }) }
        catch (e) { setAdded({ ok: false, lead, err: errText(e) }) }
      }
    }
    setDone(r); onDone()
  })
  const src = meta.sources.find(s => s.key === source)
  const counts = batch?.counts || {}
  return (
    <section className="evs-card evs-import">
      <div className="evs-card-h"><h3><Icon name="upload" size={18} /> Add leads</h3><button className="evs-link-plain" onClick={onClose}>Close</button></div>
      {!batch && (
        <div className="evs-chips" style={{ marginBottom: 12 }}>
          <button className={'evs-chip' + (mode === 'one' ? ' on' : '')} onClick={() => setMode('one')}>One lead</button>
          <button className={'evs-chip' + (mode === 'file' ? ' on' : '')} onClick={() => setMode('file')}>A file of leads</button>
        </div>
      )}
      {!batch && mode === 'one' && (
        <div className="evs-grid">
          <label className="evs-field"><span>Business name *</span><input className="evs-input" value={one.company} onChange={e => setOne({ ...one, company: e.target.value })} /></label>
          <label className="evs-field"><span>Contact name</span><input className="evs-input" value={one.contact} onChange={e => setOne({ ...one, contact: e.target.value })} /></label>
          <label className="evs-field"><span>Phone *</span><input className="evs-input" value={one.phone} onChange={e => setOne({ ...one, phone: e.target.value })} placeholder="214-555-0101" /></label>
          <label className="evs-field"><span>Email</span><input className="evs-input" value={one.email} onChange={e => setOne({ ...one, email: e.target.value })} /></label>
          <label className="evs-field"><span>City / area *</span><input className="evs-input" value={one.city} onChange={e => setOne({ ...one, city: e.target.value })} placeholder="Dallas, or DFW" /></label>
          <label className="evs-field"><span>State *</span><input className="evs-input" value={one.state} onChange={e => setOne({ ...one, state: e.target.value })} placeholder="TX" /></label>
          <label className="evs-field"><span>Industry</span><input className="evs-input" value={one.industry} onChange={e => setOne({ ...one, industry: e.target.value })} /></label>
          <label className="evs-field"><span>Where they came from</span>
            <select className="evs-input" value={source} onChange={e => setSource(e.target.value)}>
              {meta.sources.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
          <label className="evs-field wide2"><span>Notes (services, what the ad said)</span><input className="evs-input" value={one.notes} onChange={e => setOne({ ...one, notes: e.target.value })} /></label>
          <label className="evs-field"><span>Language</span><label className="evs-check"><input type="checkbox" checked={one.spanish} onChange={e => { const on = e.target.checked; setOne({ ...one, spanish: on }); const es = camps.find(c => /Spanish/i.test(c.name || '')); const en = camps.find(c => /FB Cleaners - Texts$/.test(c.name || '')); if (on && es) setCampId(es.id); if (!on && en && campId === es?.id) setCampId(en.id) }} /> Speaks Spanish only</label></label>
          <label className="evs-field"><span>Start texting them in</span>
            <select className="evs-input" value={campId} onChange={e => setCampId(e.target.value)}>
              <option value="">No campaign yet</option>
              {camps.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <p className="evs-hint wide">It goes through the same checks as a file (duplicates, location, grouping). Nothing is sent until you press Create below{campId ? '; then it joins the campaign and gets its first text in the next sending window.' : '.'}</p>
          <div className="evs-actions wide"><button className="evs-btn" disabled={busy || !one.company.trim() || !(one.phone.trim() || one.email.trim()) || !(one.city.trim() || one.state.trim())} onClick={upload}>{busy ? 'Checking…' : 'Check this lead'}</button></div>
        </div>
      )}
      {!batch && mode === 'file' && (
        <div className="evs-grid">
          <label className="evs-field"><span>Where they came from</span>
            <select className="evs-input" value={source} onChange={e => setSource(e.target.value)}>
              {meta.sources.map(s => <option key={s.key} value={s.key}>{s.label}{s.access === 'import' ? ' (file import)' : ''}</option>)}
            </select>
          </label>
          <label className="evs-field wide2"><span>CSV or Excel file (company, contact, phone, email, city, state, industry, source ID, notes)</span>
            <input className="evs-input" type="file" accept=".csv,.xlsx,.xls" onChange={e => setFile(e.target.files?.[0] || null)} />
          </label>
          <p className="evs-hint wide">{src?.access === 'import'
            ? `${src.label} isn't connected to EvoSys - bring these in as a file. Nothing is sent and nobody joins a campaign until you approve one.`
            : 'Website requests arrive by themselves. You can also import a file.'}</p>
          <div className="evs-actions wide"><button className="evs-btn" disabled={!file || busy} onClick={upload}>{busy ? 'Checking…' : 'Check the file'}</button></div>
        </div>
      )}
      {batch && !done && (
        <>
          <div className="evs-impcounts">
            <span className="evs-pill evs-blue">{counts.ready || 0} ready</span>
            <span className="evs-pill evs-muted">{counts.matched_existing || 0} already here</span>
            <span className="evs-pill evs-warn">{counts.held_for_review || 0} held (location)</span>
            <span className="evs-pill evs-muted">{counts.duplicate_in_file || 0} repeated in file</span>
            {(counts.invalid || counts.excluded) ? <span className="evs-pill evs-bad">{(counts.invalid || 0) + (counts.excluded || 0)} can't import</span> : null}
          </div>
          {(batch.next_actions || []).map(a => <p key={a.key} className="evs-hint">{a.text}</p>)}
          <div className="evs-improws">
            {(rows || []).slice(0, 50).map(r => (
              <div key={r.id} className="evs-improw">
                <b>{r.business?.business_name || '(no name)'}</b>
                <small>{[r.business?.source_record_id, [r.business?.city, r.business?.state].filter(Boolean).join(', '), r.business?.industry].filter(Boolean).join(' · ')}</small>
                <span className={'evs-pill ' + ({ ready: 'evs-blue', matched_existing: 'evs-muted', held_for_review: 'evs-warn' }[r.outcome] || 'evs-bad')}>{r.outcome.replace(/_/g, ' ')}</span>
                {r.match_reason && <small className="evs-why">{r.match_reason}</small>}
              </div>
            ))}
          </div>
          <div className="evs-actions">
            <button className="evs-btn" disabled={busy || !counts.ready} onClick={create}>{mode === 'one' ? 'Create this lead' : `Create ${counts.ready || 0} leads`}</button>
            <button className="evs-btn evs-ghost" disabled={busy} onClick={() => { setBatch(null); setRows(null) }}>Start over</button>
          </div>
        </>
      )}
      {done && added && (
        <div className={added.ok ? 'evs-okmsg' : 'evs-error'}>
          {added.ok ? <>{added.lead.company} joined <b>{camps.find(c => c.id === campId)?.name}</b> - their first text goes out in the next sending window.</>
            : <>{added.lead.company} was created but didn't join the campaign: {added.err || ((added.out?.refused || [])[0]?.reason) || 'it was refused'}.</>}
        </div>
      )}
      {done && (
        <div className="evs-okmsg">
          {done.created || 0} leads created and grouped by industry and market. Messages sent: 0.
          {' '}Anything unclear or a possible duplicate is waiting under <b>Needs review</b>.
        </div>
      )}
      {msg && <div className="evs-error" role="alert">{msg}</div>}
    </section>
  )
}

// ── one lead ─────────────────────────────────────────────────────────────────

// ── the business's own logo (owner request 2026-11-17, PR #691) ─────────────
// Cut from the ad they ran. Clean and name-only logos go at the top of our
// emails to them; a rough one is kept here for reference and never sent.
const LOGO_LABEL = { clean: 'Clean', name_only: 'Name only', rough: 'Rough' }

export function LogoThumb({ lead }) {
  return (
    <span className="evs-logo-thumb" aria-hidden={!lead?.logo_url}>
      {lead?.logo_url
        ? <img src={lead.logo_url} alt={`${lead.company} logo`} loading="lazy" />
        : <span>{(lead?.company || '?').trim().charAt(0).toUpperCase()}</span>}
    </span>
  )
}

function LogoCard({ id, lead, manage, onChange }) {
  const [quality, setQuality] = useState('clean')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (lead?.logo_quality) setQuality(lead.logo_quality) }, [lead?.logo_quality])
  const upload = async (file) => {
    if (!file) return
    setBusy(true); setMsg(null)
    try {
      const image_base64 = await new Promise((resolve, reject) => {
        const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(file)
      })
      await api.put(`${API}/leads/${id}/logo`, { image_base64, quality })
      await onChange(); setMsg({ ok: true, text: 'Logo saved. Nothing was sent.' })
    } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const remove = async () => {
    if (!window.confirm(`Remove the logo from ${lead?.company}?`)) return
    setBusy(true); setMsg(null)
    try { await api.delete(`${API}/leads/${id}/logo`); await onChange(); setMsg({ ok: true, text: 'Logo removed.' }) }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  return (
    <section className="evs-card evs-logo-card">
      <h3>Their logo</h3>
      {lead?.logo_url ? (
        <>
          <div className="evs-logo-big"><img src={lead.logo_url} alt={`${lead.company} logo`} /></div>
          <p className="evs-hint">
            {LOGO_LABEL[lead.logo_quality] || 'Saved'} ·{' '}
            {lead.logo_in_email ? 'Shown at the top of our emails to them.' : 'Kept for reference. Not used in emails.'}
          </p>
        </>
      ) : <p className="evs-empty">No logo yet. Emails go out without one.</p>}
      {manage && (
        <div className="evs-actions">
          <select className="evs-input" value={quality} disabled={busy} onChange={e => setQuality(e.target.value)} aria-label="Logo quality">
            <option value="clean">Clean - use in emails</option>
            <option value="name_only">Name only - use in emails</option>
            <option value="rough">Rough - keep, don't use</option>
          </select>
          <label className="evs-btn evs-ghost">
            <Icon name="upload" size={14} />{lead?.logo_url ? 'Replace' : 'Add logo'}
            <input type="file" accept="image/*" hidden disabled={busy} onChange={e => { upload(e.target.files?.[0]); e.target.value = '' }} />
          </label>
          {lead?.logo_url && <button className="evs-btn evs-ghost" disabled={busy} onClick={remove}>Remove</button>}
        </div>
      )}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}
    </section>
  )
}

const NEXT_STEPS = {
  replied: ['Interested', 'Answer them in the inbox, or book a time from open slots.'],
  booked: ['Booked', 'Open the appointment and check the calendar and Zoom details.'],
  active: ['In follow-up', 'The next step goes out on schedule unless they reply.'],
  opted_out: ['Opted out', 'On Do Not Contact. Nothing else will be sent.'],
  finished: ['Sequence finished', 'No reply after every step. Call them or leave it.'],
}

function Cadence({ lead, data }) {
  const k = lead.outreach?.key
  const msgs = data?.messages || []
  const recent = [...msgs].slice(-4)
  const [title, nextText] = NEXT_STEPS[lead.booked ? 'booked' : k] || [lead.outreach?.label || 'Not started', 'Add them to a campaign to start the sequence.']
  return (
    <div className="evs-two v2-cadence">
      <section className="evs-card">
        <h3>Cadence and history</h3>
        <p className="evs-hint">{lead.outreach?.label}{data?.next ? ` · next: step ${data.next.step} (${data.next.channel || 'email'}) ${when(data.next.due_at)}` : ''}</p>
        {recent.length ? recent.map(m => (
          <div key={m.id} className="evs-hist"><small>{when(m.at)}</small> <b>{m.direction === 'in' ? 'Inbound ' + m.channel : m.channel === 'call' ? 'Call' : (m.label === 'ai_answer' ? 'AI ' : m.step ? 'Step ' + m.step + ' ' : '') + m.channel}</b> · {(m.translation || m.body || '').slice(0, 90)}</div>
        )) : <p className="evs-empty">Nothing sent yet.</p>}
        {k === 'replied' && <Link className="evs-act evs-act-primary" to={`${BASE}?tab=needs&c=${lead.id}`}>Answer {lead.company.split(' ')[0]}</Link>}
      </section>
      <section className="evs-card">
        <h3>Next step</h3>
        <div className="evs-hist"><b>{title}</b><br /><small>{nextText}</small></div>
        {lead.booked && <Link className="evs-act" to={`${BASE}/appointments?view=upcoming`}>Open appointments</Link>}
      </section>
    </div>
  )
}

export function Lead() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data, error, reload } = useLoad(`${API}/leads/${id}`)
  const grp = useLoad(`${API}/leads/${id}/group`)
  const meta = useLoad(`${API}/groups`)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [calling, setCalling] = useState(false)
  const act = async (path, body, okText) => {
    setBusy(true); setMsg(null)
    try { await api.post(`${API}/leads/${id}/${path}`, body || {}); setNote(''); await Promise.all([reload(), grp.reload()]); if (okText) setMsg({ ok: true, text: okText }) }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const l = data?.lead
  const manage = meta.data?.can_manage
  const lastReply = [...(data?.messages || [])].reverse().find(m => m.direction === 'in')
  return (
    <Box error={error} data={data}>
      <Link className="evs-crumb" to={`${BASE}/leads`}><Icon name="back" size={14} />Leads</Link>
      <PageHead title={l?.company} sub={[l?.contact, grp.data?.industry, grp.data?.market].filter(Boolean).join(' · ')}>
        <Status o={l?.outreach} /><ReviewPill status={grp.data?.review_status} label={grp.data?.review_label} />
        {l && l.outreach?.key !== 'opted_out' && <>
          <button className="evs-act" disabled={!l.phone} onClick={() => setCalling(true)}>Call</button>
          <Link className="evs-act" to={`${BASE}?tab=needs&c=${l.id}`}>Text</Link>
          <Link className="evs-act evs-act-primary" to={`${BASE}/appointments/new?lead=${l.id}`}>Book a call</Link>
        </>}
      </PageHead>
      {calling && l && <div className="v2-modal" onClick={e => e.target === e.currentTarget && setCalling(false)}><CallPanel lead={l} onClose={() => { setCalling(false); reload() }} onLogged={reload} /></div>}
      {l && <Cadence lead={l} data={data} />}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}

      <div className="evs-two">
        <div>
          {lastReply && l?.outreach?.key !== 'active' && <ReplyHelp key={lastReply.id} lead={l} reply={lastReply} saved={data?.reply_ai} onSent={reload} />}

          <section className="evs-card" id="log">
            <h3>What happened</h3>
            <textarea className="evs-input" rows={3} placeholder="What they said, or how the call went…" value={note} onChange={e => setNote(e.target.value)} />
            <div className="evs-actions">
              <button className="evs-btn" disabled={busy} onClick={() => act('call', { note }, 'Call logged.')}>I called them</button>
              <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('reply', { note, channel: 'email' }, 'Reply logged; emails stopped.')}>They replied (phone or another inbox)</button>
              {l?.outreach?.key === 'active'
                ? <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('stop', {}, 'Follow-up stopped.')}>Stop follow-up</button>
                : ['manual', 'replied', 'finished'].includes(l?.outreach?.key) &&
                  <button className="evs-btn evs-ghost" disabled={busy} onClick={() => { if (window.confirm('Restart the automatic emails for this lead?')) act('restart', {}, 'Follow-up restarted.') }}>Restart follow-up</button>}
            </div>
            <p className="evs-hint">Email replies are picked up by themselves and stop the follow-up. Use “They replied” only when they answered some other way.</p>
          </section>

          {data?.next && (
            <section className="evs-card">
              <h3>Next {data.next.channel === 'text' ? 'text' : 'email'} · {data.next.campaign} · step {data.next.step} · {when(data.next.due_at)}</h3>
              {data.next.paused && <p className="evs-banner">This campaign is paused or needs approving, so this email is waiting.</p>}
              {l?.logo_in_email && <img className="evs-email-logo" src={l.logo_url} alt={`${l.company} logo`} />}
              <p className="evs-subject">{data.next.subject}</p>
              <pre className="evs-body">{data.next.body}</pre>
            </section>
          )}

          <section className="evs-card">
            <h3>Conversation</h3>
            {data?.messages?.length ? data.messages.map(m => <Message key={m.id} m={m} />)
              : <p className="evs-empty">Nothing sent yet.</p>}
          </section>

          <section className="evs-card">
            <h3>History</h3>
            {(data?.timeline || []).map((e, i) => (
              <div key={i} className="evs-hist"><small>{when(e.at)}</small> {e.summary}{e.detail && e.type !== 'created' ? <span className="evs-histd"> - {String(e.detail).slice(0, 160)}</span> : null}</div>
            ))}
          </section>
        </div>

        <div>
          <LogoCard id={id} lead={l} manage={manage} onChange={reload} />
          <GroupCard id={id} grp={grp} meta={meta.data} manage={manage} onChange={() => { grp.reload(); reload() }} />
          <IntroCall lead={l} onBooked={() => { reload(); grp.reload() }} />
          <section className="evs-card evs-facts1">
            <h3>Details</h3>
            <div><span>Email</span><b>{l?.email || '—'}</b></div>
            <div><span>Website</span><b>{l?.website ? <a href={l.website.startsWith('http') ? l.website : 'https://' + l.website} target="_blank" rel="noreferrer">{l.website}</a> : '—'}</b></div>
            <div><span>Pipeline</span><b>{l?.stage_label}</b></div>
            <div><span>Source</span><b>{grp.data?.source_label || '—'}</b></div>
          </section>
          {manage && l?.stage !== 'won' && (
            <section className="evs-card">
              <h3>Signed up?</h3>
              <p className="evs-hint">Hands {l?.company} to service-company onboarding with its source and campaign. Nothing is emailed.</p>
              <button className="evs-btn evs-btn-gold" disabled={busy} onClick={async () => {
                if (!window.confirm(`Mark ${l?.company} as signed up and start their onboarding?`)) return
                setBusy(true); setMsg(null)
                try { const r = await api.post(`${API}/leads/${id}/signed-up`, {}); setMsg({ ok: r.status !== 'duplicate', text: r.message || 'Signed up. Onboarding started (' + r.attribution + ').' }); reload() }
                catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
              }}>They signed up</button>
            </section>
          )}
        </div>
      </div>
      {!data && navigate && null}
    </Box>
  )
}

function GroupCard({ id, grp, meta, manage, onChange }) {
  const g = grp.data
  const [ind, setInd] = useState('')
  const [mk, setMk] = useState('')
  const [ai, setAi] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (g) { setInd(g.industry_key || ''); setMk(g.market_key || ''); setAi(g.ai) } }, [g])
  if (!g || !meta) return null
  const run = async (fn, ok) => { setBusy(true); setMsg(null); try { await fn(); setMsg(ok ? { ok: true, text: ok } : null); onChange() } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) } }
  const review = action => run(() => api.post(`${API}/leads/${id}/review`, { action }), 'Saved.')
  return (
    <section className="evs-card">
      <h3>Industry and market</h3>
      <p className="evs-hint">{g.group_source === 'manual' ? 'Set by a person.' : g.group_source === 'ai_accepted' ? 'AI suggestion accepted by a person.' : 'Set by rule: ' + (g.group_reason || '')}</p>
      {g.campaign && <p className="evs-hint">Campaign: <Link to={`${BASE}/outreach?campaign=${g.campaign.id}`}>{g.campaign.name}</Link></p>}
      {g.review_status === 'possible_duplicate' && g.duplicate_of && (
        <div className="evs-banner">
          <b>Possible duplicate</b> of <Link to={`${BASE}/leads/${g.duplicate_of.id}`}>{g.duplicate_of.company}</Link> ({[g.duplicate_of.email, g.duplicate_of.phone].filter(Boolean).join(' · ')}).
          {manage && <div className="evs-actions"><button className="evs-btn evs-btn-sm" disabled={busy} onClick={() => review('not_duplicate')}>Different businesses</button><button className="evs-btn evs-ghost evs-btn-sm" disabled={busy} onClick={() => review('same_business')}>Same business - keep the other</button></div>}
        </div>
      )}
      {manage ? (
        <>
          <div className="evs-grid2">
            <label className="evs-field"><span>Industry</span>
              <select className="evs-input" value={ind} onChange={e => setInd(e.target.value)}><option value="">Unclear</option>{meta.industries.map(i => <option key={i.key} value={i.key}>{i.label}</option>)}</select></label>
            <label className="evs-field"><span>Market</span>
              <select className="evs-input" value={mk} onChange={e => setMk(e.target.value)}><option value="">Unclear</option>{meta.markets.map(m => <option key={m.key} value={m.key}>{m.label}{m.state ? ` (${m.state})` : ''}</option>)}</select></label>
          </div>
          <div className="evs-actions">
            <button className="evs-btn evs-btn-sm" disabled={busy || (ind === (g.industry_key || '') && mk === (g.market_key || ''))}
                    onClick={() => run(() => api.put(`${API}/leads/${id}/group`, { industry_key: ind || null, market_key: mk || null }), 'Group saved.')}>Save</button>
            <button className="evs-btn evs-ghost evs-btn-sm" disabled={busy} onClick={() => run(async () => setAi(await api.post(`${API}/leads/${id}/ai-group${ai ? '?again=true' : ''}`, {})))}><Icon name="spark" size={14} />{ai ? 'Ask AI again' : 'Ask AI'}</button>
            {g.review_status === 'held'
              ? <button className="evs-btn evs-ghost evs-btn-sm" disabled={busy} onClick={() => review('release')}>Release hold</button>
              : g.review_status !== 'excluded' && <button className="evs-btn evs-ghost evs-btn-sm" disabled={busy} onClick={() => review('hold')}>Hold</button>}
            {g.review_status !== 'excluded' && <button className="evs-link" disabled={busy} onClick={() => review('exclude')}>Exclude</button>}
          </div>
        </>
      ) : <p><b>{g.industry || 'Industry unclear'}</b> · <b>{g.market || 'Market unclear'}</b></p>}
      {ai && (
        <div className={'evs-aibox ' + (ai.status === 'failed' ? 'bad' : '')}>
          <Icon name="spark" size={20} />
          <div>
            {ai.status === 'failed' ? <><b>AI couldn't finish</b><small>{ai.error}</small></> : (
              <>
                <b>AI suggests: {meta.industries.find(i => i.key === ai.output?.industry_key)?.label || 'unclear'} · {meta.markets.find(m => m.key === ai.output?.market_key)?.label || 'unclear'}</b>
                <small>{ai.output?.reason}</small>
                {(ai.output?.evidence || []).map((e, k) => <small key={k} className="evs-quote">“{e}”</small>)}
                {(ai.output?.problems || []).map((p, k) => <small key={k} className="evs-err-small">{p}</small>)}
                {manage && ai.output?.industry_key && ai.output?.market_key && (
                  <button className="evs-btn evs-btn-sm" disabled={busy} onClick={() => run(() => api.put(`${API}/leads/${id}/group`, { industry_key: ai.output.industry_key, market_key: ai.output.market_key, from_ai_task_id: ai.id }), 'Suggestion accepted.')}>Use this</button>
                )}
              </>
            )}
          </div>
        </div>
      )}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'}>{msg.text}</div>}
    </section>
  )
}

function IntroCall({ lead, onBooked }) {
  const [slots, setSlots] = useState(null)
  const [pick, setPick] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  if (!lead) return null
  const load = async () => {
    setBusy(true); setMsg(null)
    try { setSlots(await api.get(`${API}/leads/${lead.id}/intro-slots`)) } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const book = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await api.post(`${API}/leads/${lead.id}/intro-call`, { starts_at: pick, prospect_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })
      if (r.status === 'booked') { setMsg({ ok: true, text: 'Booked for ' + when(r.starts_at || pick, LONG) + (r.already_booked ? ' (already booked).' : '. Their follow-up emails stopped.') }); setSlots(null); onBooked() }
      else { setMsg({ ok: false, text: r.message }); await load() }
    } catch (e) { setMsg({ ok: false, text: errText(e) + ' Nothing was booked; try again.' }) } finally { setBusy(false) }
  }
  return (
    <section className="evs-card">
      <h3 className="evs-h3i"><Icon name="cal" size={18} />Intro call</h3>
      {lead.booked ? <p className="evs-okmsg">A call is booked. See Appointments.</p> : (
        <>
          {!slots && <button className="evs-btn evs-btn-sm" disabled={busy} onClick={load}>{busy ? 'Finding times…' : 'Find a time'}</button>}
          {slots && (slots.slots?.length ? (
            <>
              <p className="evs-hint">{slots.meeting} · {slots.duration_minutes} min · times in {slots.timezone || 'your time zone'}</p>
              <div className="evs-slots">
                {slots.slots.map(s => (
                  <button key={s.starts_at} className={'evs-slot' + (pick === s.starts_at ? ' on' : '')} onClick={() => setPick(s.starts_at)}>
                    {when(s.starts_at, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                  </button>
                ))}
              </div>
              <div className="evs-actions"><button className="evs-btn evs-btn-gold" disabled={!pick || busy} onClick={book}>{busy ? 'Booking…' : 'Book this time'}</button></div>
            </>
          ) : <p className="evs-hint">{slots.problem || 'No open times in the next week. Check your availability in the Sales Workspace.'}</p>)}
        </>
      )}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}
    </section>
  )
}

const CATEGORY = { interested: 'Interested', pricing: 'Pricing question', question: 'Question', not_interested: 'Not interested',
                   wrong_person: 'Wrong person', unsubscribe: 'Wants no more emails', unclear: 'Unclear' }

function ReplyHelp({ lead, reply, saved, onSent }) {
  const isText = reply.channel === 'text'
  const reSubject = 'Re: ' + (reply.subject || 'our conversation').replace(/^Re:\s*/i, '')
  const [t, setT] = useState(saved && !saved.outcome ? saved : null)
  const [subject, setSubject] = useState(saved ? reSubject : '')
  const [body, setBody] = useState(saved && !saved.outcome ? (saved.output?.draft || '') : '')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const ask = async again => {
    setBusy(true); setMsg(null)
    try {
      const r = await api.post(`${API}/messages/${reply.id}/assist${again ? '?again=true' : ''}`, {})
      setT(r); setBody(r.output?.draft || ''); setSubject(reply.subject ? (reply.subject.startsWith('Re:') ? reply.subject : 'Re: ' + reply.subject) : 'Re: our conversation')
    } catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const send = async () => {
    setBusy(true); setMsg(null)
    try { await api.post(`${API}/leads/${lead.id}/send-reply`, { subject, body, ai_task_id: t?.id, channel: isText ? 'text' : 'email' }); setMsg({ ok: true, text: isText ? 'Text sent from your EvoSys number. Their answer will land here too.' : 'Sent. Their next reply will land here too.' }); setBody(''); onSent() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const out = t?.output || {}
  return (
    <section className="evs-card evs-replyhelp">
      <h3 className="evs-h3i"><Icon name="reply" size={18} />They replied {when(reply.at)}</h3>
      <blockquote className="evs-quoteblock">{reply.body}{reply.translation && <><br /><small><b>In English:</b> {reply.translation}</small></>}</blockquote>
      <p className="evs-hint">{isText ? 'Their automatic texts already stopped.' : 'Their emails already stopped.'} {reply.how === 'reply_address' ? 'Matched by their reply address.' : reply.how === 'sender' ? (isText ? 'Matched by their phone number.' : 'Matched by their email address.') : ''}</p>
      {!t && (
        <div className="evs-actions">
          <button className="evs-btn" disabled={busy} onClick={() => ask(false)}><Icon name="spark" size={16} />{busy ? 'Reading…' : 'Summarise and draft an answer'}</button>
          <button className="evs-btn evs-ghost" disabled={busy} onClick={() => { setT({ manual: true }); setSubject('Re: ' + (reply.subject || 'our conversation').replace(/^Re:\s*/i, '')) }}>Write it myself</button>
        </div>
      )}
      {t && t.status === 'failed' && <div className="evs-error">{t.error} <button className="evs-link-plain" onClick={() => ask(true)}>Try again</button></div>}
      {t && !t.manual && t.status !== 'failed' && (
        <div className="evs-aibox">
          <Icon name="spark" size={20} />
          <div>
            <b>{CATEGORY[out.category] || 'Reply'}: {out.summary}</b>
            <small>{isText ? 'AI used: their text, the conversation, your offer (3 free appointments, 30 days, no obligation), the booking link.' : <>AI used: their reply, our last email, {out.used_offer ? 'your approved offer' : 'no offer (none approved)'}, the booking link.</>}</small>
            {(out.needs_person || []).map((n, k) => <small key={k} className="evs-err-small">{n}</small>)}
          </div>
        </div>
      )}
      {t && (
        <>
          {!isText && <label className="evs-field"><span>Subject</span><input className="evs-input" value={subject} onChange={e => setSubject(e.target.value)} /></label>}
          <label className="evs-field"><span>Your answer {t.manual ? '' : '(edit before sending)'}{isText ? ` - ${body.length} characters` : ''}</span><textarea className="evs-input" rows={isText ? 4 : 6} value={body} onChange={e => setBody(e.target.value)} placeholder="Write your answer…" /></label>
          {isText && out.draft_english && body === out.draft && <p className="evs-hint"><b>In English:</b> {out.draft_english}</p>}
          <div className="evs-actions">
            <button className="evs-btn" disabled={busy || !body.trim()} onClick={send}>{isText ? 'Send text' : 'Send answer'}</button>
            {!t.manual && <button className="evs-btn evs-ghost" disabled={busy} onClick={() => ask(true)}>Draft again</button>}
          </div>
          <p className="evs-hint">{isText ? 'Sends from your EvoSys texting number. It does not restart the follow-up.' : 'Sends from your EvoSys address with your mailing address and opt-out link. It does not restart the follow-up.'}</p>
        </>
      )}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}
    </section>
  )
}

const HOW = { reply_address: 'matched by their reply address', sender: 'matched by their email address', manual: 'logged by hand' }

function Message({ m }) {
  const inbound = m.direction === 'in'
  const who = inbound ? 'They replied' : m.label === 'ai_answer' ? 'AI answered' : (m.label || '').startsWith('rd:') ? 'Reminder (day before)' : (m.label || '').startsWith('r1:') ? 'Reminder (1 hour before)' : m.label === 'booking_confirmation' ? 'Booking confirmed' : m.channel === 'call' ? 'Call' : m.channel === 'text' ? (m.step ? 'Text ' + m.step : 'Your text') : m.step ? 'Email ' + m.step : 'Your answer'
  const problem = m.status === 'held' ? (m.channel === 'text' ? 'Not sent - no texting number yet' : 'Not sent') : m.status === 'failed' ? 'Did not send' : null
  return (
    <div className={'evs-msg ' + (inbound ? 'in' : 'out')}>
      <div className="evs-msg-head"><b>{who}</b><small>{when(m.at)}{inbound && HOW[m.how] ? ' · ' + (m.channel === 'text' && m.how === 'sender' ? 'matched by their phone number' : HOW[m.how]) : ''}</small>
        {m.label && CATEGORY[m.label] && <span className="evs-pill evs-gold">{CATEGORY[m.label]}</span>}
        {problem && <span className="evs-pill evs-bad">{problem}</span>}
      </div>
      {m.subject && <div className="evs-subject">{m.subject}</div>}
      <pre className="evs-body">{m.body}</pre>
      {m.translation && <div className="evs-translation"><small>In English</small><p>{m.translation}</p></div>}
      {m.error && m.status === 'failed' && <small className="evs-err-small">{m.error}</small>}
    </div>
  )
}

// ── Conversations ────────────────────────────────────────────────────────────

export function Conversations() {
  const { data, error } = useLoad(`${API}/conversations`)
  return (
    <>
      <PageHead title="Conversations" sub="Everyone you've emailed, newest first. Replies come in by themselves." />
      <Box error={error} data={data}>
        <div className="evs-card evs-list">
          {data?.conversations?.length ? data.conversations.map(c => {
            const lm = c.last_message || {}
            return (
              <div key={c.id} className={'evs-row' + (lm.direction === 'in' ? ' evs-row-new' : '')}>
                <Link to={`${BASE}/leads/${c.id}`} className="evs-row-main">
                  <b>{c.company}</b>
                  <small>{lm.direction === 'in' ? 'They replied' : (lm.subject || lm.channel)} · {when(lm.at)} · {c.message_count} message{c.message_count === 1 ? '' : 's'}</small>
                  {lm.direction === 'in' && lm.body && <span className="evs-snip">“{lm.body}”</span>}
                </Link>
                <div className="evs-row-acts">
                  <Status o={c.outreach} />
                  <CallBtn lead={{ id: c.lead_id || c.id, company: c.company, phone: c.phone }} />
                  <Link className={'evs-act' + (lm.direction === 'in' ? ' evs-act-primary' : '')} to={`${BASE}/leads/${c.id}`}>{lm.direction === 'in' ? 'Answer' : 'Open'}</Link>
                </div>
              </div>
            )
          }) : <p className="evs-empty">No messages yet. They appear here once a campaign is on.</p>}
        </div>
      </Box>
    </>
  )
}

export { Outreach } from './EvoSysOutreach'

// ── Appointments ─────────────────────────────────────────────────────────────

export function Appointments() {
  const { data, error } = useLoad(`${API}/appointments`)
  const row = a => (
    <div key={a.id} className="evs-row">
      <div className="evs-row-main"><b>{a.company || a.title}</b><small>{a.title} · {a.contact ? a.contact + ' · ' : ''}{when(a.starts_at, LONG)}</small></div>
      <div className="evs-row-acts">
        <span className={'evs-pill ' + (a.status === 'scheduled' ? 'evs-gold' : 'evs-muted')}>{a.status === 'scheduled' ? 'Booked' : a.status}</span>
        {a.lead_id && <Link className="evs-act" to={`${BASE}/leads/${a.lead_id}`}>Open lead</Link>}
      </div>
    </div>
  )
  return (
    <>
      <PageHead title="Appointments" sub="Intro calls and demos with businesses EvoSys is signing up. Book an intro call from a lead's page." />
      <Box error={error} data={data}>
        <section className="evs-card"><h3>Coming up</h3>
          {data?.upcoming?.length ? data.upcoming.map(row) : <p className="evs-empty">No calls booked yet. Leads book from the link in your emails, or you book one from their page.</p>}
        </section>
        <section className="evs-card"><h3>Last 30 days</h3>
          {data?.recent?.length ? data.recent.map(row) : <p className="evs-empty">None.</p>}
        </section>
      </Box>
    </>
  )
}

// ── Results ──────────────────────────────────────────────────────────────────

const BY = [['campaign', 'Campaign'], ['industry', 'Industry'], ['market', 'Market'], ['source', 'Source']]
const COLS = [['leads', 'Leads'], ['contacted', 'Emailed'], ['replied', 'Replied'], ['interested', 'Interested'],
              ['intro_booked', 'Intro calls'], ['intro_completed', 'Calls held'], ['demos', 'Demos'], ['signed_up', 'Signed up']]

function Funnel({ stages, days }) {
  const [open, setOpen] = useState(null)
  const top = Math.max(1, stages[0]?.count || 0)
  if (!stages.length) return null
  const st = stages.find(x => x.key === open)
  return (
    <section className="evs-card v2-funnel">
      <h3>Last {days} days: from first message to customer</h3>
      {stages.map((x, i) => (
        <button key={x.key} className={'v2-fstage' + (open === x.key ? ' on' : '')} onClick={() => setOpen(open === x.key ? null : x.key)} aria-expanded={open === x.key}>
          <span className="v2-flabel">{x.label}</span>
          <span className="v2-fbar"><i style={{ width: `${Math.max(x.count ? 4 : 0, Math.round(100 * x.count / top))}%` }} /></span>
          <b>{x.count}</b>
          <small>{i ? (stages[i - 1].count ? Math.round(100 * x.count / stages[i - 1].count) + '%' : '-') : ''}</small>
        </button>
      ))}
      {st && (
        <div className="v2-fdrill">
          {st.leads.length ? st.leads.map(l => <Link key={l.id} to={`${BASE}/leads/${l.id}`} className="v2-tag">{l.company}</Link>) : <span className="evs-hint">Nobody at this stage yet.</span>}
          {st.count > st.leads.length && <span className="evs-hint">and {st.count - st.leads.length} more</span>}
        </div>
      )}
      <p className="evs-hint">Counts come from real messages, bookings, call outcomes and sign-ups. Attended only counts calls with an outcome recorded.</p>
    </section>
  )
}

export function Results() {
  const [days, setDays] = useState(30)
  const [by, setBy] = useState('campaign')
  const { data, error } = useLoad(`${API}/results?days=${days}`)
  const bd = useLoad(`${API}/results/breakdown?by=${by}&days=${days}`)
  const t = data?.totals || {}
  const max = Math.max(1, ...(data?.by_day || []).map(d => d.sent))
  return (
    <>
      <PageHead title="Results" sub="Each outcome counted separately, from the same records you work." />
      <div className="evs-filters">
        {[7, 30, 90].map(d => <button key={d} className={'evs-chip' + (days === d ? ' on' : '')} onClick={() => setDays(d)}>Last {d} days</button>)}
      </div>
      <Box error={error} data={data}>
        <Funnel stages={data?.funnel || []} days={days} />
        <div className="evs-stats evs-stats-6">
          <div className="evs-stat"><b>{t.emails_sent}</b><span>Messages sent</span></div>
          <div className="evs-stat evs-stat-gold"><b>{t.replied}</b><span>Replied ({t.reply_rate}%)</span></div>
          <div className="evs-stat"><b>{t.booked}</b><span>Booked calls</span></div>
          <div className="evs-stat"><b>{t.calls_logged}</b><span>Calls logged</span></div>
          <div className="evs-stat"><b>{t.opted_out}</b><span>Opted out</span></div>
          <div className="evs-stat"><b>{t.bounced}</b><span>Bad emails</span></div>
        </div>
        <section className="evs-card">
          <div className="evs-card-h"><h3>By {BY.find(b => b[0] === by)[1].toLowerCase()}</h3>
            <div className="evs-segs">{BY.map(([k, l]) => <button key={k} className={'evs-chip' + (by === k ? ' on' : '')} onClick={() => setBy(k)}>{l}</button>)}</div>
          </div>
          <div className="evs-tablewrap">
            <table className="evs-table">
              <thead><tr><th>{BY.find(b => b[0] === by)[1]}</th>{COLS.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
              <tbody>
                {(bd.data?.rows || []).map(r => (
                  <tr key={r.name}><td>{r.name}</td>{COLS.map(([k]) => <td key={k} className={k === 'signed_up' && r[k] ? 'evs-win' : ''}>{r[k]}</td>)}</tr>
                ))}
                {bd.data && !bd.data.rows.length && <tr><td colSpan={COLS.length + 1} className="evs-empty">No leads yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
        <section className="evs-card">
          <h3>Campaign messages sent by day</h3>
          {data?.by_day?.length ? (
            <div className="evs-bars">
              {data.by_day.map(d => (
                <div key={d.date} className="evs-bar" title={`${d.date}: ${d.sent} sent, ${d.replies} replies`}>
                  <i style={{ height: `${Math.round(100 * d.sent / max)}%` }} />
                  <small>{d.date.slice(5)}</small>
                </div>
              ))}
            </div>
          ) : <p className="evs-empty">Nothing sent in this period.</p>}
        </section>
      </Box>
    </>
  )
}
