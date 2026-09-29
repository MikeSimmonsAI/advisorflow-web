/**
 * RATE REQUESTS — the energy work queue.
 *
 * A rate request is a lead at one of the organization's rate-request stages.
 * The status names map to the org's tiers on the server
 * (app/routers/rate_requests_router.py):
 *   New = new_inquiry · In Review = rate_review · Options Sent = proposal_sent
 *   Completed = contract_signed · Booked = derived (open request whose lead has
 *   a booked/confirmed appointment) — filterable, never settable.
 *
 * ── Sources ──────────────────────────────────────────────────────────────
 *   GET   /rate-requests/summary     KPI cards + tab counts
 *   GET   /rate-requests/?status&search&source&assigned&days&sort&page
 *   GET   /rate-requests/{id}        drawer
 *   POST  /rate-requests/            "New Rate Request" (explicit form submit)
 *   PATCH /rate-requests/{id}        status / assignment / fields (explicit)
 *   GET   /admin/users               assignee names (managers only)
 *
 * Nothing on this screen sends a message or enrols anyone in anything. A null
 * count renders "Not yet available".
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import { useWorkspaceAuthority } from '../../auth/workspaceAuthority'
import { formatPhone } from '../../utils/phone'
import './LeadsWorkspace.css'
import './RateRequests.css'

const PAGE_SIZES = [10, 25, 50]
const NOT_AVAILABLE = 'Not yet available'

const TABS = [
  { key: 'open', label: 'All Requests' },
  { key: 'new', label: 'New' },
  { key: 'in_review', label: 'In Review' },
  { key: 'options_sent', label: 'Options Sent' },
  { key: 'booked', label: 'Booked' },
  { key: 'completed', label: 'Completed' },
  { key: 'unassigned', label: 'Unassigned' },
]

const KPI_CARDS = [
  { key: 'new', label: 'New', tone: 'blue', icon: 'inbox' },
  { key: 'in_review', label: 'In Review', tone: 'violet', icon: 'search' },
  { key: 'options_sent', label: 'Options Sent', tone: 'amber', icon: 'file' },
  { key: 'booked', label: 'Booked', tone: 'green', icon: 'calendar' },
  { key: 'unassigned', label: 'Unassigned', tone: 'slate', icon: 'user' },
]

const RANGES = [
  { value: '', label: 'Any time' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
]

const SORTS = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'updated', label: 'Recently updated' },
  { value: 'name', label: 'Name' },
]

const SETTABLE = ['new', 'in_review', 'options_sent', 'completed']
const STATUS_LABEL = { new: 'New', in_review: 'In Review', options_sent: 'Options Sent', completed: 'Completed', booked: 'Booked' }

const ICONS = {
  inbox: 'M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8',
  calendar: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  plus: 'M12 5v14M5 12h14',
  x: 'M18 6 6 18M6 6l12 12',
  chevL: 'M15 18l-6-6 6-6',
  chevR: 'M9 18l6-6-6-6',
  zap: 'M13 2 3 14h9l-1 8 10-12h-9l1-8z',
}
function Icon({ name, size = 18 }) {
  return (
    <svg className="lw-icon" width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name] || ''} />
    </svg>
  )
}

function isNum(v) { return typeof v === 'number' && Number.isFinite(v) }
function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}
function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}
function humanize(v) {
  return String(v || '').split(/[_\-\s]+/).filter(Boolean).map(w => w[0].toUpperCase() + w.slice(1)).join(' ')
}
function errText(e) { return e?.message || 'Request failed' }
function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

function StatusPill({ row }) {
  if (!row?.status) return <span className="rr-pill rr-pill--slate">Not in queue</span>
  return (
    <span className="rr-pills">
      <span className={`rr-pill rr-pill--${row.status}`}>{row.status_label || STATUS_LABEL[row.status]}</span>
      {row.booked && <span className="rr-pill rr-pill--booked">Booked</span>}
    </span>
  )
}

/* ═════════════════════════════════════════════════════════════════════ */

export default function RateRequests() {
  const me = getCurrentUser()
  const { isManager } = useWorkspaceAuthority()
  const [summary, setSummary] = useState(null)
  const [summaryErr, setSummaryErr] = useState(null)
  const [tab, setTab] = useState('open')
  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  const [assigned, setAssigned] = useState('')
  const [range, setRange] = useState('')
  const [sort, setSort] = useState('newest')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [data, setData] = useState({ items: [], total: 0, available: true })
  const [loading, setLoading] = useState(true)
  const [listErr, setListErr] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [users, setUsers] = useState([])
  const [openId, setOpenId] = useState(null)
  const [creating, setCreating] = useState(false)
  const [notice, setNotice] = useState(null)
  const debouncedQ = useDebounced(q)

  const reload = useCallback(() => setReloadKey(k => k + 1), [])

  useEffect(() => {
    let alive = true
    setSummaryErr(null)
    api.get('/rate-requests/summary')
      .then(d => { if (alive) setSummary(d || null) })
      .catch(e => { if (alive) { setSummary(null); setSummaryErr(errText(e)) } })
    return () => { alive = false }
  }, [reloadKey])

  useEffect(() => {
    if (!isManager) { setUsers([]); return }
    api.get('/admin/users')
      .then(list => setUsers((Array.isArray(list) ? list : []).filter(u => u && u.id && u.is_active !== false)))
      .catch(() => setUsers([]))
  }, [isManager])

  // Reset to page 1 whenever the question changes.
  useEffect(() => { setPage(1) }, [tab, debouncedQ, source, assigned, range, sort, pageSize])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setListErr(null)
    const p = new URLSearchParams()
    p.set('status', tab === 'unassigned' ? 'open' : tab)
    if (tab === 'unassigned') p.set('assigned', 'unassigned')
    else if (assigned) p.set('assigned', assigned)
    if (debouncedQ.trim()) p.set('search', debouncedQ.trim().slice(0, 120))
    if (source) p.set('source', source)
    if (range) p.set('days', range)
    if (sort !== 'newest') p.set('sort', sort)
    p.set('page', String(page))
    p.set('page_size', String(pageSize))
    api.get(`/rate-requests/?${p.toString()}`)
      .then(d => { if (alive) setData({ items: d?.items || [], total: d?.total || 0, available: d?.available !== false }) })
      .catch(e => { if (alive) { setData({ items: [], total: 0, available: true }); setListErr(errText(e)) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [tab, debouncedQ, source, assigned, range, sort, page, pageSize, reloadKey])

  const countFor = useCallback(key => {
    if (!summary) return null
    if (key === 'unassigned') return summary.unassigned
    if (key === 'open') return summary.open
    const v = summary.counts?.[key]
    return isNum(v) ? v : null
  }, [summary])

  const sources = useMemo(() => {
    const s = new Set([...(summary?.sources || []), ...(summary?.source_choices || [])])
    return [...s].filter(Boolean).sort()
  }, [summary])

  const pages = Math.max(1, Math.ceil((data.total || 0) / pageSize))
  const firstShown = data.total ? (page - 1) * pageSize + 1 : 0
  const lastShown = Math.min(page * pageSize, data.total || 0)
  const configured = summary ? summary.configured !== false : true

  async function quickStatus(row, status) {
    try {
      await api.patch(`/rate-requests/${row.id}`, { status })
      setNotice({ kind: 'ok', text: `${row.name || 'Request'} moved to ${STATUS_LABEL[status]}.` })
      reload()
    } catch (e) { setNotice({ kind: 'error', text: errText(e) }) }
  }

  return (
    <div className="lw rr">
      <header className="rr-header">
        <div className="rr-title">
          <h1>Rate Requests</h1>
          <p>Plan shopping and bill analysis, from first ask to the option that was chosen.</p>
        </div>
        <button type="button" className="lw-btn lw-btn--primary" onClick={() => setCreating(true)} disabled={!configured}>
          <Icon name="plus" size={16} /> New Rate Request
        </button>
      </header>

      {notice && (
        <div className={`lw-banner lw-banner--${notice.kind === 'error' ? 'error' : 'ok'}`} role="status">
          <span>{notice.text}</span>
          <button type="button" className="lw-iconbtn" onClick={() => setNotice(null)} aria-label="Dismiss"><Icon name="x" size={16} /></button>
        </div>
      )}
      {summaryErr && <div className="lw-banner lw-banner--error">Summary unavailable: {summaryErr}</div>}

      <div className="lw-tabbar">
        <div className="lw-tabs" role="tablist">
          {TABS.map(t => {
            const n = countFor(t.key)
            return (
              <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
                className={`lw-tab${tab === t.key ? ' is-active' : ''}`} onClick={() => setTab(t.key)}>
                {t.label}{isNum(n) ? <span className="rr-tabcount">{n}</span> : null}
              </button>
            )
          })}
        </div>
      </div>

      <section className="rr-kpis" aria-label="Request counts">
        {KPI_CARDS.map(c => {
          const n = countFor(c.key)
          return (
            <button key={c.key} type="button" className={`lw-kpi rr-kpi${tab === c.key ? ' is-active' : ''}`}
              onClick={() => setTab(c.key)}>
              <span className={`lw-kpi-icon lw-tone-${c.tone === 'slate' ? 'blue' : c.tone}`}><Icon name={c.icon} /></span>
              <span className="lw-kpi-text">
                <span className="lw-kpi-label">{c.label}</span>
                <span className={`lw-kpi-value${isNum(n) ? '' : ' is-na'}`}>{isNum(n) ? n.toLocaleString('en-US') : (summary || summaryErr ? NOT_AVAILABLE : '—')}</span>
              </span>
            </button>
          )
        })}
      </section>
      {summary && isNum(summary.stale_7d) && summary.stale_7d > 0 && (
        <p className="lw-note">{summary.stale_7d} open request{summary.stale_7d === 1 ? ' has' : 's have'} not been updated in 7 days.</p>
      )}

      <div className="lw-filters rr-filters">
        <label className="lw-search">
          <Icon name="search" size={16} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Name, email, phone, address or supplier" aria-label="Search rate requests" />
        </label>
        <label className="lw-select">
          <span className="lw-select-label">Source</span>
          <select value={source} onChange={e => setSource(e.target.value)}>
            <option value="">All sources</option>
            {sources.map(s => <option key={s} value={s}>{humanize(s)}</option>)}
          </select>
        </label>
        {tab !== 'unassigned' && (
          <label className="lw-select">
            <span className="lw-select-label">Assigned to</span>
            <select value={assigned} onChange={e => setAssigned(e.target.value)}>
              <option value="">Anyone</option>
              <option value="me">Me</option>
              {isManager && <option value="unassigned">Unassigned</option>}
              {users.filter(u => u.id !== me?.id).map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
            </select>
          </label>
        )}
        <label className="lw-select">
          <span className="lw-select-label">Request date</span>
          <select value={range} onChange={e => setRange(e.target.value)}>
            {RANGES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
        <label className="lw-select">
          <span className="lw-select-label">Sort</span>
          <select value={sort} onChange={e => setSort(e.target.value)}>
            {SORTS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
      </div>

      <section className="lw-panel rr-panel">
        {listErr && <div className="lw-banner lw-banner--error">{listErr}</div>}
        {!configured || !data.available ? (
          <EmptyState configured={false} onCreate={null} />
        ) : !loading && data.items.length === 0 ? (
          <EmptyState configured filtered={Boolean(debouncedQ || source || assigned || range || tab !== 'open')}
            onCreate={() => setCreating(true)} />
        ) : (
          <div className="lw-tablewrap">
            <table className="lw-table rr-table">
              <thead>
                <tr>
                  <th>Customer</th><th>Property / Address</th><th>Source</th><th>Request Date</th>
                  <th>Status</th><th>Assigned To</th><th>Next Step</th><th className="rr-actions-h">Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading && data.items.length === 0 && (
                  <tr><td colSpan="8" className="lw-muted">Loading…</td></tr>
                )}
                {data.items.map(r => (
                  <tr key={r.id} tabIndex={0} onClick={() => setOpenId(r.id)}
                    onKeyDown={e => { if (e.key === 'Enter') setOpenId(r.id) }}>
                    <td data-label="Customer">
                      <div className="lw-strong">{r.name || 'Unnamed'}</div>
                      <div className="lw-muted rr-sub">{r.phone ? formatPhone(r.phone) : (r.email || '')}</div>
                    </td>
                    <td data-label="Address">
                      <div>{r.service_address || <span className="lw-muted">—</span>}</div>
                      {r.current_supplier && <div className="lw-muted rr-sub">Supplier: {r.current_supplier}</div>}
                    </td>
                    <td data-label="Source">{r.source ? humanize(r.source) : <span className="lw-muted">—</span>}</td>
                    <td data-label="Request date" className="lw-nowrap">{fmtDate(r.created_at)}</td>
                    <td data-label="Status"><StatusPill row={r} /></td>
                    <td data-label="Assigned to">{r.assigned_to_name || (r.assigned_to_id ? (r.assigned_to_id === me?.id ? 'You' : 'Assigned') : <span className="lw-muted">Unassigned</span>)}</td>
                    <td data-label="Next step">{r.next_step || <span className="lw-muted">—</span>}</td>
                    <td className="rr-actions" onClick={e => e.stopPropagation()}>
                      <RowMenu row={r} onOpen={() => setOpenId(r.id)} onStatus={s => quickStatus(r, s)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data.total > 0 && (
          <div className="lw-pager">
            <span className="lw-muted">Showing {firstShown}–{lastShown} of {data.total.toLocaleString('en-US')} requests</span>
            <div className="rr-pager-controls">
              <button type="button" className="lw-iconbtn" disabled={page <= 1} onClick={() => setPage(p => p - 1)} aria-label="Previous page"><Icon name="chevL" size={16} /></button>
              <span className="rr-page">Page {page} of {pages}</span>
              <button type="button" className="lw-iconbtn" disabled={page >= pages} onClick={() => setPage(p => p + 1)} aria-label="Next page"><Icon name="chevR" size={16} /></button>
              <select className="rr-pagesize" value={pageSize} onChange={e => setPageSize(Number(e.target.value))} aria-label="Rows per page">
                {PAGE_SIZES.map(n => <option key={n} value={n}>{n} per page</option>)}
              </select>
            </div>
          </div>
        )}
      </section>

      {openId && (
        <RequestDrawer id={openId} users={users} isManager={isManager} me={me}
          onClose={() => setOpenId(null)}
          onChanged={text => { setNotice({ kind: 'ok', text }); reload() }} />
      )}
      {creating && (
        <NewRequestModal users={users} isManager={isManager} sources={summary?.source_choices || []}
          onClose={() => setCreating(false)}
          onCreated={row => {
            setCreating(false)
            setNotice({ kind: 'ok', text: `Rate request created for ${row.name || 'the customer'}. Nothing was sent.` })
            setTab('new')
            reload()
            setOpenId(row.id)
          }} />
      )}
    </div>
  )
}

function RowMenu({ row, onOpen, onStatus }) {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return undefined
    const close = () => setOpen(false)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [open])
  return (
    <div className="lw-menu-wrap">
      <button type="button" className="lw-iconbtn" aria-haspopup="menu" aria-expanded={open}
        aria-label={`Actions for ${row.name || 'request'}`}
        onClick={e => { e.stopPropagation(); setOpen(o => !o) }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden="true"><path d="M5 12h.01M12 12h.01M19 12h.01" /></svg>
      </button>
      {open && (
        <div className="lw-menu lw-menu--right" role="menu">
          <button type="button" role="menuitem" onClick={onOpen}>Open details</button>
          <div className="lw-menu-head">Move to</div>
          {SETTABLE.filter(s => s !== row.status).map(s => (
            <button key={s} type="button" role="menuitem" onClick={() => onStatus(s)}>{STATUS_LABEL[s]}</button>
          ))}
        </div>
      )}
    </div>
  )
}

function EmptyState({ configured, filtered, onCreate }) {
  if (!configured) {
    return (
      <div className="lw-empty rr-empty">
        <Icon name="zap" size={28} />
        <strong>This workspace has no rate-request stages</strong>
        <p>Rate requests are leads at the New Inquiry, Rate Review and Proposal Sent stages. This workspace's pipeline does not define them, so there is no queue to show. An administrator can add those stages in Settings.</p>
      </div>
    )
  }
  if (filtered) {
    return (
      <div className="lw-empty rr-empty">
        <Icon name="search" size={28} />
        <strong>No requests match these filters</strong>
        <p>Clear the search or pick another tab to see the rest of the queue.</p>
      </div>
    )
  }
  return (
    <div className="lw-empty rr-empty">
      <Icon name="inbox" size={28} />
      <strong>No open rate requests</strong>
      <p>This queue holds every customer asking for a rate review or supplier options. Requests arrive from your website forms and imports at the New Inquiry stage, or an advisor logs one here after a call.</p>
      {onCreate && <button type="button" className="lw-btn lw-btn--primary" onClick={onCreate}><Icon name="plus" size={16} /> New Rate Request</button>}
    </div>
  )
}

/* ── drawer ───────────────────────────────────────────────────────────── */

function RequestDrawer({ id, users, isManager, me, onClose, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [edit, setEdit] = useState(null)
  const [key, setKey] = useState(0)

  useEffect(() => {
    let alive = true
    setErr(null)
    api.get(`/rate-requests/${id}`)
      .then(x => { if (alive) setD(x) })
      .catch(e => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [id, key])

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  async function patch(body, text) {
    setBusy(true)
    try {
      await api.patch(`/rate-requests/${id}`, body)
      setKey(k => k + 1)
      onChanged(text)
      setEdit(null)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const available = (d?.statuses || []).filter(s => s.available).map(s => s.key)
  return (
    <div className="lw-drawer-layer" onClick={onClose}>
      <aside className="lw-drawer" role="dialog" aria-modal="true" aria-label="Rate request" onClick={e => e.stopPropagation()}>
        <div className="lw-drawer-head">
          <h2>Rate request</h2>
          <button type="button" className="lw-iconbtn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        {err && <div className="lw-banner lw-banner--error">{err}</div>}
        {!d ? (!err && <p className="lw-muted">Loading…</p>) : (
          <div className="lw-drawer-body">
            <div className="lw-drawer-id">
              <div className="lw-avatar">{(d.name || '?').split(/\s+/).map(p => p[0]).slice(0, 2).join('').toUpperCase()}</div>
              <div>
                <div className="lw-drawer-name">{d.name || 'Unnamed'}</div>
                <StatusPill row={d} />
              </div>
            </div>

            <div className="rr-controls">
              <label>
                <span>Status</span>
                <select value={d.status || ''} disabled={busy}
                  onChange={e => e.target.value && patch({ status: e.target.value }, `Moved to ${STATUS_LABEL[e.target.value]}.`)}>
                  {!d.status && <option value="">Not in queue</option>}
                  {SETTABLE.filter(s => available.includes(s)).map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                </select>
              </label>
              {isManager ? (
                <label>
                  <span>Assigned to</span>
                  <select value={d.assigned_to_id || 'unassigned'} disabled={busy}
                    onChange={e => patch({ assign_to: e.target.value }, 'Assignment updated.')}>
                    <option value="unassigned">Unassigned</option>
                    {d.assigned_to_id && !users.some(u => u.id === d.assigned_to_id) && (
                      <option value={d.assigned_to_id}>{d.assigned_to_name || 'Current owner'}</option>)}
                    {users.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}{u.id === me?.id ? ' (you)' : ''}</option>)}
                  </select>
                </label>
              ) : (
                <div className="rr-owner"><span>Assigned to</span><strong>{d.assigned_to_name || 'Unassigned'}</strong></div>
              )}
            </div>
            {d.booked && d.booking && (
              <p className="lw-note">Appointment {d.booking.status}: {fmtDateTime(d.booking.booked_time)}{d.booking.appointment_type ? ` · ${d.booking.appointment_type}` : ''}</p>
            )}

            <h3>Customer</h3>
            <dl className="lw-dl">
              <dt>Phone</dt><dd>{d.phone ? formatPhone(d.phone) : '—'}</dd>
              <dt>Email</dt><dd>{d.email || '—'}</dd>
              <dt>Source</dt><dd>{d.source ? humanize(d.source) : '—'}</dd>
              <dt>Requested</dt><dd>{fmtDateTime(d.created_at)}{isNum(d.age_days) ? ` (${d.age_days}d ago)` : ''}</dd>
              <dt>Last activity</dt><dd>{fmtDateTime(d.last_activity_at)}</dd>
              <dt>Next step</dt><dd>{d.next_step || '—'}</dd>
            </dl>

            <div className="rr-section-head">
              <h3>Service &amp; usage</h3>
              {!edit && <button type="button" className="lw-link" onClick={() => setEdit({
                service_address: d.custom_fields?.service_address || '',
                current_supplier: d.custom_fields?.current_supplier || '',
                annual_usage_kwh: d.custom_fields?.annual_usage_kwh ?? '',
                contract_end_date: d.custom_fields?.contract_end_date || '',
                rate_type: d.custom_fields?.rate_type || '',
                segment: d.custom_fields?.segment || '',
                notes: d.notes || '',
              })}>Edit</button>}
            </div>
            {edit ? (
              <form className="lw-modal rr-inline-form" onSubmit={e => {
                e.preventDefault()
                const { notes, ...fields } = edit
                const body = { fields: { ...fields, annual_usage_kwh: fields.annual_usage_kwh === '' ? '' : Number(fields.annual_usage_kwh) } }
                if (notes !== (d.notes || '')) body.notes = notes
                patch(body, 'Request details saved.')
              }}>
                <FieldInputs value={edit} onChange={setEdit} />
                <label className="rr-block">Notes<textarea rows={3} value={edit.notes} onChange={e => setEdit({ ...edit, notes: e.target.value })} /></label>
                <div className="lw-row-actions lw-row-actions--end">
                  <button type="button" className="lw-btn" onClick={() => setEdit(null)}>Cancel</button>
                  <button type="submit" className="lw-btn lw-btn--primary" disabled={busy}>Save</button>
                </div>
              </form>
            ) : (
              <>
                <dl className="lw-dl">
                  <dt>Service address</dt><dd>{d.service_address || '—'}</dd>
                  <dt>Current supplier</dt><dd>{d.current_supplier || '—'}</dd>
                  <dt>Annual usage</dt><dd>{d.annual_usage_kwh != null && d.annual_usage_kwh !== '' ? `${Number(d.annual_usage_kwh).toLocaleString('en-US')} kWh` : '—'}</dd>
                  <dt>Contract end</dt><dd>{d.contract_end_date || '—'}</dd>
                  <dt>Rate type</dt><dd>{d.rate_type || '—'}</dd>
                  <dt>Segment</dt><dd>{d.segment || '—'}</dd>
                </dl>
                {d.notes && (<><h3>Notes</h3><p className="rr-notes">{d.notes}</p></>)}
              </>
            )}

            <h3>Timeline</h3>
            {(d.timeline || []).length === 0 ? <p className="lw-muted">No recorded changes yet.</p> : (
              <ul className="lw-feed">
                {d.timeline.map((t, i) => (
                  <li key={i}>
                    <div className="lw-feed-title">{humanize(String(t.action).replace(/^rate_request\.|^lead\./, ''))}</div>
                    {t.details?.from !== undefined && <div className="lw-feed-body lw-muted">{humanize(t.details.from) || '—'} → {humanize(t.details.to) || '—'}</div>}
                    <div className="lw-feed-time lw-muted">{fmtDateTime(t.at)}{t.actor ? ` · ${t.actor}` : ''}</div>
                  </li>
                ))}
              </ul>
            )}
            <div className="lw-row-actions">
              <Link className="lw-btn lw-btn--outline" to={`/leads/${d.id}`}>Open full lead record</Link>
            </div>
            <p className="lw-note">Changing status or assignment here sends nothing to the customer.</p>
          </div>
        )}
      </aside>
    </div>
  )
}

function FieldInputs({ value, onChange }) {
  const set = k => e => onChange({ ...value, [k]: e.target.value })
  return (
    <div className="lw-form-grid">
      <label className="lw-span2">Service address<input value={value.service_address} onChange={set('service_address')} maxLength={300} /></label>
      <label>Current supplier<input value={value.current_supplier} onChange={set('current_supplier')} maxLength={120} /></label>
      <label>Annual usage (kWh)<input type="number" min="0" step="any" value={value.annual_usage_kwh} onChange={set('annual_usage_kwh')} /></label>
      <label>Contract end date<input type="date" value={value.contract_end_date} onChange={set('contract_end_date')} /></label>
      <label>Rate type
        <select value={value.rate_type} onChange={set('rate_type')}>
          <option value="">—</option>{['Fixed', 'Variable', 'Indexed', 'Unknown'].map(o => <option key={o}>{o}</option>)}
        </select>
      </label>
      <label>Segment
        <select value={value.segment} onChange={set('segment')}>
          <option value="">—</option>{['Residential', 'Commercial'].map(o => <option key={o}>{o}</option>)}
        </select>
      </label>
    </div>
  )
}

/* ── create modal ─────────────────────────────────────────────────────── */

function NewRequestModal({ users, isManager, sources, onClose, onCreated }) {
  const [f, setF] = useState({
    first_name: '', last_name: '', phone: '', email: '', source: 'phone', notes: '', assign_to: '',
    service_address: '', current_supplier: '', annual_usage_kwh: '', contract_end_date: '', rate_type: '', segment: '',
  })
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = k => e => setF({ ...f, [k]: e.target.value })

  async function submit(e) {
    e.preventDefault()
    setErr(null)
    if (!f.first_name.trim()) { setErr('A first name is required.'); return }
    if (!f.phone.trim() && !f.email.trim() && !f.service_address.trim()) {
      setErr('Give at least a phone, an email or a service address.'); return
    }
    const body = {}
    Object.entries(f).forEach(([k, v]) => { if (String(v).trim() !== '') body[k] = typeof v === 'string' ? v.trim() : v })
    if (body.annual_usage_kwh !== undefined) body.annual_usage_kwh = Number(body.annual_usage_kwh)
    setBusy(true)
    try {
      const row = await api.post('/rate-requests/', body)
      onCreated(row)
    } catch (e2) { setErr(errText(e2)) } finally { setBusy(false) }
  }

  return (
    <div className="lw-drawer-layer lw-modal-layer" onClick={onClose}>
      <form className="lw-modal" role="dialog" aria-modal="true" aria-label="New rate request"
        onClick={e => e.stopPropagation()} onSubmit={submit}>
        <div className="lw-drawer-head">
          <h2>New rate request</h2>
          <button type="button" className="lw-iconbtn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <p className="lw-note">Creates a lead at the New Inquiry stage. It records no SMS consent, starts no follow-up and sends nothing.</p>
        {err && <div className="lw-banner lw-banner--error">{err}</div>}
        <div className="lw-form-grid">
          <label>First name *<input value={f.first_name} onChange={set('first_name')} required maxLength={120} autoFocus /></label>
          <label>Last name / company<input value={f.last_name} onChange={set('last_name')} maxLength={120} /></label>
          <label>Phone<input type="tel" value={f.phone} onChange={set('phone')} maxLength={40} /></label>
          <label>Email<input type="email" value={f.email} onChange={set('email')} maxLength={200} /></label>
          <label>Source
            <select value={f.source} onChange={set('source')}>
              {(sources.length ? sources : ['phone', 'website', 'email', 'referral', 'walk_in', 'manual', 'other']).map(s => <option key={s} value={s}>{humanize(s)}</option>)}
            </select>
          </label>
          {isManager ? (
            <label>Assign to
              <select value={f.assign_to} onChange={set('assign_to')}>
                <option value="">Me</option>
                <option value="unassigned">Leave unassigned</option>
                {users.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
              </select>
            </label>
          ) : <div />}
        </div>
        <h3 className="rr-form-h">Service &amp; usage</h3>
        <FieldInputs value={f} onChange={setF} />
        <label className="rr-block">Notes<textarea rows={3} value={f.notes} onChange={set('notes')} maxLength={4000} /></label>
        <div className="lw-row-actions lw-row-actions--end">
          <button type="button" className="lw-btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="lw-btn lw-btn--primary" disabled={busy}>{busy ? 'Creating…' : 'Create request'}</button>
        </div>
      </form>
    </div>
  )
}
