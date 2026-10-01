/**
 * RETAIL ENERGY — LEADS WORKSPACE.
 *
 * Rendered by pages/Leads.jsx for the energy vertical only (see the switch at
 * the bottom of that file). Every other vertical keeps the classic Leads page,
 * and this screen links back to it ("Classic list" -> /leads?classic=1) for the
 * capabilities it deliberately does not reproduce: bulk compose/send, AI
 * batch start, bulk reassignment, duplicate resolution, flagged leads, legacy
 * spreadsheet import and batch deletion.
 *
 * ── Sources. Every number and row on this page names one. ────────────────
 *   GET  /leads/workspace-summary          KPI tiles, tier order + counts
 *   GET  /leads/?page&page_size&tier&status&search&assigned_to_id&source&sort
 *                                           board columns + tables, all filtered,
 *                                           sorted and paginated on the server.
 *   GET  /leads/{id}                        drawer record
 *   GET  /leads/{id}/activity               drawer activity
 *   PATCH /leads/{id}                       notes
 *   PATCH /leads/{id}/stage?tier=           "Move to…" (explicit menu action;
 *                                           validated against the org's own
 *                                           stages, status left untouched)
 *   POST /leads/create                      "Add lead" (explicit form submit)
 *   GET  /admin/users                       assignee names (managers)
 *   GET  /leads/import-batches              lead-list filter options (managers)
 *                                           + workspace-summary.sources_30d
 *   GET  /intake/batches                    recent import events
 *
 * ── Rules ─────────────────────────────────────────────────────────────────
 * Nothing on this screen sends a message, enrolls a lead in anything or
 * creates a lead on its own. A null metric renders "Not yet available". SMS
 * contactability is shown exactly as the lead record states it; a phone
 * number is never read as permission.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import { useWorkspaceAuthority } from '../../auth/workspaceAuthority'
import { useTerminology } from '../../terminology'
import { StatusBadge } from '../../components/StatusBadge'
import { formatPhone } from '../../utils/phone'
import './LeadsWorkspace.css'
import { confirmLeadDelete, deleteLeadIds, deleteSummary } from '../../utils/deleteRecords'

const BOARD_PAGE = 50
const TABLE_PAGE = 50
// "All lost" merges one query per lost status; each is capped at this size.
const LOST_MERGE = 200
const LOST_STATUSES = ['not_interested', 'dead', 'dnc']
const NOT_AVAILABLE = 'Not yet available'

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'new', label: 'New' },
  { value: 'queued', label: 'Queued' },
  { value: 'sent', label: 'Sent' },
  { value: 'replied', label: 'Replied' },
  { value: 'hot', label: 'Hot' },
  { value: 'booked', label: 'Booked' },
  { value: 'cold', label: 'Cold' },
  { value: 'not_interested', label: 'Not Interested' },
  { value: 'dnc', label: 'DNC' },
  { value: 'dead', label: 'Dead' },
  { value: 'needs_tier_review', label: 'Needs Review' },
]

const TABS = [
  { key: 'pipeline', label: 'Pipeline' },
  { key: 'all', label: 'All Leads' },
  { key: 'mine', label: 'My Leads' },
  { key: 'recent', label: 'Recently Active' },
  { key: 'lost', label: 'Lost / Closed' },
]

/* ── helpers ──────────────────────────────────────────────────────────── */

function isNum(v) { return typeof v === 'number' && Number.isFinite(v) }
function num(v) { return isNum(v) ? v.toLocaleString('en-US') : NOT_AVAILABLE }
function leadName(l) {
  const n = `${l?.first_name || ''} ${l?.last_name || ''}`.trim()
  return n || 'Unnamed lead'
}
function initials(name) {
  const p = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!p.length) return '—'
  return (p[0][0] + (p[1] ? p[1][0] : '')).toUpperCase()
}
function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}
function ago(iso) {
  if (!iso) return ''
  // API timestamps are naive UTC; read as local they land hours in the future.
  const ms = Date.now() - (asUtc(iso) || new Date(NaN)).getTime()
  if (Number.isNaN(ms)) return ''
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d}d ago`
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
function humanize(v) {
  return String(v || '').split(/[_\-\s]+/).filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
}
function envelope(data) {
  const items = Array.isArray(data) ? data : (data?.items ?? [])
  const total = Array.isArray(data) ? data.length : (data?.total ?? items.length)
  return { items, total }
}
function errText(e) { return e?.message || 'Request failed' }
function leadsQuery({ page = 1, pageSize = TABLE_PAGE, tier, status, source, q, assigned, sort }) {
  let u = `/leads/?page=${page}&page_size=${pageSize}`
  if (tier) u += `&tier=${encodeURIComponent(tier)}`
  if (status) u += `&status=${encodeURIComponent(status)}`
  if (source) u += `&source=${encodeURIComponent(source)}`
  if (q && q.trim()) u += `&search=${encodeURIComponent(q.trim().slice(0, 120))}`
  if (assigned) u += `&assigned_to_id=${encodeURIComponent(assigned)}`
  if (sort && sort !== 'recent') u += `&sort=${encodeURIComponent(sort)}`
  return u
}
// The filter bar's server parameters, shared by the board and the tables.
function filterParams(f) {
  return { tier: f.tier, status: f.status, source: f.list, q: f.q, assigned: f.assigned }
}
// Debounce the search box so typing does not fire a request per keystroke.
function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

/* ── icons (inline, stroke) ───────────────────────────────────────────── */

const ICONS = {
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  calendar: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
  target: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  check: 'M22 11.08V12a10 10 0 1 1-5.93-9.14M22 4 12 14.01l-3-3',
  bolt: 'M13 2 3 14h9l-1 8 10-12h-9l1-8z',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  dots: 'M5 12h.01M12 12h.01M19 12h.01',
  x: 'M18 6 6 18M6 6l12 12',
  chevron: 'M9 18l6-6-6-6',
  down: 'M6 9l6 6 6-6',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-2.82 1.17V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 3.18 14H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  flow: 'M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
}
function Icon({ name, size = 18 }) {
  return (
    <svg className="lw-icon" width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name] || ''} />
    </svg>
  )
}

/* ═════════════════════════════════════════════════════════════════════════
   PAGE
   ═════════════════════════════════════════════════════════════════════════ */

export default function LeadsWorkspace() {
  const navigate = useNavigate()
  const me = getCurrentUser()
  const { branding, isManager } = useWorkspaceAuthority()
  const terminology = useTerminology()

  const [summary, setSummary] = useState(null)
  const [summaryErr, setSummaryErr] = useState(null)
  // Drill-down links (Overview tiles etc.) arrive as /leads?status=&tier=&q=.
  // They seed the same filters the bar drives; a filtered link opens the list.
  const [urlParams] = useSearchParams()
  const [filters, setFilters] = useState(() => ({
    q: urlParams.get('q') || '',
    list: urlParams.get('source') || urlParams.get('import_list_name') || '',
    status: STATUS_OPTIONS.some(o => o.value && o.value === urlParams.get('status')) ? urlParams.get('status') : '',
    assigned: '',
    tier: String(urlParams.get('tier') || '').replace(/[^a-zA-Z0-9_-]/g, ''),
  }))
  const [tab, setTab] = useState(() => (
    urlParams.get('status') || urlParams.get('q') || urlParams.get('source') || urlParams.get('import_list_name') ? 'all' : 'pipeline'))
  const [users, setUsers] = useState([])
  const [listNames, setListNames] = useState([])
  const [reloadKey, setReloadKey] = useState(0)
  const [openLeadId, setOpenLeadId] = useState(null)
  const [addFor, setAddFor] = useState(null) // null | { tier }
  const [addMenu, setAddMenu] = useState(false)
  const [notice, setNotice] = useState(null) // { kind, text }

  const reload = useCallback(() => setReloadKey(k => k + 1), [])

  // Workspace summary — KPIs, tier order, counts.
  useEffect(() => {
    let alive = true
    setSummaryErr(null)
    api.get('/leads/workspace-summary')
      .then(d => { if (alive) setSummary(d || null) })
      .catch(e => { if (alive) { setSummary(null); setSummaryErr(errText(e)) } })
    return () => { alive = false }
  }, [reloadKey])

  // Assignee directory (the same endpoint the classic page uses; managers only
  // are entitled to it, so a refusal just means names are not shown).
  useEffect(() => {
    api.get('/admin/users')
      .then(list => setUsers((Array.isArray(list) ? list : []).filter(u => u && u.id)))
      .catch(() => setUsers([]))
  }, [])

  // Lead-list names for the source filter (server filters on import_list_name).
  useEffect(() => {
    if (!isManager) return
    api.get('/leads/import-batches')
      .then(rows => {
        const s = new Set()
        ;(Array.isArray(rows) ? rows : []).forEach(r => { if (r.import_list_name) s.add(r.import_list_name) })
        setListNames([...s].sort())
      })
      .catch(() => setListNames([]))
  }, [isManager, reloadKey])

  // THE ORGANIZATION'S PIPELINE, in its configured order. The summary is the
  // authority; terminology (GET /org-settings/) is the same registry and
  // covers the moment before — or a failure of — the summary call.
  const tiers = useMemo(() => {
    if (Array.isArray(summary?.tiers) && summary.tiers.length) {
      return summary.tiers.filter(t => t && t.key).map(t => ({ key: t.key, label: t.label || humanize(t.key) }))
    }
    return (terminology.tiers || []).filter(t => t && t.value).map(t => ({ key: t.value, label: t.label || humanize(t.value) }))
  }, [summary, terminology.tiers])
  const tierLabel = useCallback(
    key => (tiers.find(t => t.key === key)?.label) || (key ? humanize(key) : 'No stage'), [tiers])
  const userName = useCallback(id => {
    if (!id) return null
    const u = users.find(x => x.id === id)
    if (u) return u.full_name || u.name || [u.first_name, u.last_name].filter(Boolean).join(' ') || u.email || 'User'
    if (me && me.id === id) return me.full_name || me.name || me.email || 'You'
    return 'Assigned user'
  }, [users, me])

  const totalLeads = isNum(summary?.total_leads) ? summary.total_leads : null
  const noLeadsAtAll = totalLeads === 0

  // Lead source filter: the server's `source` param matches Lead.source OR the
  // import list name, so both vocabularies are offered.
  const sourceOptions = useMemo(() => {
    const seen = new Map()
    ;(Array.isArray(summary?.sources_30d) ? summary.sources_30d : []).forEach(r => {
      if (r && r.source && r.source !== 'unknown') seen.set(r.source, humanize(r.source))
    })
    listNames.forEach(n => { if (!seen.has(n)) seen.set(n, n) })
    if (filters.list && !seen.has(filters.list)) seen.set(filters.list, filters.list)
    return [...seen.entries()].map(([value, label]) => ({ value, label }))
  }, [summary, listNames, filters.list])
  const debouncedQ = useDebounced(filters.q)
  const serverFilters = useMemo(() => ({ ...filters, q: debouncedQ }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [debouncedQ, filters.list, filters.status, filters.assigned, filters.tier])

  function setFilter(key, value) { setFilters(f => ({ ...f, [key]: value })) }
  function clearFilters() { setFilters({ q: '', list: '', status: '', assigned: '', tier: '' }) }
  const filtersActive = Object.values(filters).some(Boolean)

  // ONE endpoint: PATCH /leads/{id}/stage validates against this org's own
  // stages and leaves status and message track untouched.
  async function moveLead(lead, newTier) {
    if (!lead || !newTier || newTier === lead.tier) return false
    setNotice(null)
    try {
      await api.patch(`/leads/${lead.id}/stage?tier=${encodeURIComponent(newTier)}`, {})
    } catch (e) {
      setNotice({ kind: 'error', text: `Could not move ${leadName(lead)}: ${errText(e)}` })
      return false
    }
    setNotice({ kind: 'ok', text: `${leadName(lead)} moved to ${tierLabel(newTier)}.` })
    reload()
    return true
  }

  const brandName = branding?.brand_name || null

  return (
    <div className="lw">
      {/* ── hero ─────────────────────────────────────────────────────── */}
      <header className="lw-hero">
        <div className="lw-hero-brand">
          {branding?.brand_logo_url
            ? <img src={branding.brand_logo_url} alt={brandName || 'Workspace logo'} className="lw-hero-logo" />
            : brandName ? <span className="lw-hero-name">{brandName}</span> : null}
        </div>
        <div className="lw-hero-title">
          <h1>Leads</h1>
          <p>Manage, nurture and close your best opportunities.</p>
        </div>
        {branding?.tagline ? <div className="lw-hero-tagline">{branding.tagline}</div> : null}
      </header>

      {/* ── KPI row ──────────────────────────────────────────────────── */}
      <section className="lw-kpis" aria-label="Lead metrics">
        <Kpi icon="users" tone="blue" value={summary?.active_leads} label="Active leads"
          sub={isNum(summary?.active_leads) && isNum(summary?.total_contacts) && summary.total_contacts > 0
            ? `${Math.round((summary.active_leads / summary.total_contacts) * 1000) / 10}% of contacts` : null}
          onClick={() => setTab('all')} />
        <Kpi icon="calendar" tone="violet" value={summary?.appointments_upcoming} label="Appointments"
          sub={isNum(summary?.appointments_upcoming) ? 'Upcoming' : null} />
        <Kpi icon="target" tone="green" value={summary?.under_contract} label="Under contract"
          sub={summary?.under_contract == null ? 'No contract stage configured' : null} />
        <Kpi icon="check" tone="amber" value={summary?.closed_won} label="Closed"
          sub={summary?.closed_won == null ? 'Not tracked as a stage' : null} onClick={() => setTab('lost')} />
        <Kpi icon="bolt" tone="blue" value={summary?.total_contacts} label="Total contacts"
          sub={isNum(summary?.contacts_not_promoted) ? `${summary.contacts_not_promoted.toLocaleString('en-US')} available to convert` : null}
          to={isManager ? '/contacts' : undefined} />
        <div className="lw-add">
          <button type="button" className="lw-btn lw-btn--primary lw-add-main" onClick={() => setAddFor({ tier: '' })}>
            <Icon name="plus" size={16} /> Add lead
          </button>
          <button type="button" className="lw-btn lw-btn--primary lw-add-caret" aria-label="More add options"
            aria-expanded={addMenu} onClick={() => setAddMenu(v => !v)}>
            <Icon name="down" size={16} />
          </button>
          {addMenu && (
            <div className="lw-menu lw-menu--right" role="menu" onMouseLeave={() => setAddMenu(false)}>
              {isManager && <button type="button" role="menuitem" onClick={() => { setAddMenu(false); navigate('/contacts') }}>Convert from contacts</button>}
              {isManager && <button type="button" role="menuitem" onClick={() => { setAddMenu(false); navigate('/imports/new') }}>Import leads from a file</button>}
              <button type="button" role="menuitem" onClick={() => { setAddMenu(false); navigate('/leads?classic=1') }}>Open classic list</button>
            </div>
          )}
        </div>
      </section>
      {summaryErr && (
        <div className="lw-banner lw-banner--warn">Lead metrics are not available right now ({summaryErr}). Stages below come from your workspace settings.</div>
      )}
      {notice && (
        <div className={`lw-banner lw-banner--${notice.kind === 'error' ? 'error' : 'ok'}`}>
          {notice.text}
          <button type="button" className="lw-link" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}

      {/* ── tabs ─────────────────────────────────────────────────────── */}
      <div className="lw-tabbar">
        <nav className="lw-tabs" role="tablist">
          {TABS.map(t => (
            <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
              className={`lw-tab${tab === t.key ? ' is-active' : ''}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="lw-tabbar-actions">
          <Link className="lw-btn" to="/leads?classic=1"><Icon name="list" size={16} /> Classic list</Link>
          {isManager && <Link className="lw-btn" to="/tier-definitions"><Icon name="gear" size={16} /> Pipeline settings</Link>}
        </div>
      </div>

      {/* ── filters ──────────────────────────────────────────────────── */}
      <div className="lw-filters">
        <label className="lw-search">
          <Icon name="search" size={16} />
          <input type="search" placeholder="Search name, phone, email…" value={filters.q}
            onChange={e => setFilter('q', e.target.value)} aria-label="Search leads" />
        </label>
        <FilterSelect label="Lead source" value={filters.list} onChange={v => setFilter('list', v)}
          options={[{ value: '', label: 'All sources' }].concat(sourceOptions)} />
        <FilterSelect label="Lead status" value={filters.status} onChange={v => setFilter('status', v)} options={STATUS_OPTIONS} />
        {users.length > 0 && (
          <FilterSelect label="Assigned to" value={filters.assigned} onChange={v => setFilter('assigned', v)}
            options={[{ value: '', label: 'All users' }]
              .concat(users.map(u => ({ value: u.id, label: userName(u.id) })))} />
        )}
        <FilterSelect label="Stage" value={filters.tier} onChange={v => setFilter('tier', v)}
          options={[{ value: '', label: 'All stages' }].concat(tiers.map(t => ({ value: t.key, label: t.label })))} />
        {filtersActive && <button type="button" className="lw-link" onClick={clearFilters}>Clear filters</button>}
      </div>

      {/* ── main surface ─────────────────────────────────────────────── */}
      {tab === 'pipeline' ? (
        <PipelineBoard tiers={tiers} byTier={summary?.by_tier || null} filters={serverFilters}
          reloadKey={reloadKey} noLeadsAtAll={noLeadsAtAll} tierLabel={tierLabel} userName={userName}
          totalLeads={totalLeads} canContacts={isManager}
          onOpen={setOpenLeadId} onMove={moveLead} onAdd={tier => setAddFor({ tier })}
          onViewAll={tier => { setFilter('tier', tier); setTab('all') }} />
      ) : (
        <LeadTable mode={tab} filters={serverFilters} me={me} reloadKey={reloadKey}
          tierLabel={tierLabel} userName={userName} summary={summary} noLeadsAtAll={noLeadsAtAll} canContacts={isManager}
          onOpen={setOpenLeadId} onAdd={() => setAddFor({ tier: '' })}
          onDeleted={(r) => { setNotice({ kind: r.ok ? 'ok' : 'error', text: r.text }); reload() }} />
      )}

      {/* ── lower panels ─────────────────────────────────────────────── */}
      <div className="lw-lower">
        <RecentActivity reloadKey={reloadKey} isManager={isManager} onOpenLead={setOpenLeadId} />
        <ConversionTools isManager={isManager} />
      </div>

      {openLeadId && (
        <LeadDrawer leadId={openLeadId} tiers={tiers} tierLabel={tierLabel} userName={userName}
          reloadKey={reloadKey} onClose={() => setOpenLeadId(null)} onMove={moveLead} onSaved={reload}
          onDeleted={(text) => { setOpenLeadId(null); setNotice({ kind: 'ok', text }); reload() }} />
      )}
      {addFor && (
        <AddLeadModal tiers={tiers} initialTier={addFor.tier || tiers[0]?.key || ''}
          onClose={() => setAddFor(null)}
          onCreated={res => {
            setAddFor(null)
            setNotice({ kind: 'ok', text: res?.is_duplicate
              ? `${res.name || 'Lead'} was added and flagged as a possible duplicate — review it in the classic list.`
              : `${res?.name || 'Lead'} was added. No message was sent.` })
            reload()
          }} />
      )}
    </div>
  )
}

/* ── KPI tile ─────────────────────────────────────────────────────────── */

function Kpi({ icon, tone, value, label, sub, to, onClick }) {
  const body = (
    <>
      <span className={`lw-kpi-icon lw-tone-${tone}`}><Icon name={icon} size={22} /></span>
      <span className="lw-kpi-text">
        <span className={`lw-kpi-value${isNum(value) ? '' : ' is-na'}`}>{num(value)}</span>
        <span className="lw-kpi-label">{label}</span>
        {sub && <span className="lw-kpi-sub">{sub}</span>}
      </span>
    </>
  )
  if (to) return <Link className="lw-kpi" to={to}>{body}</Link>
  if (onClick) return <button type="button" className="lw-kpi" onClick={onClick}>{body}</button>
  return <div className="lw-kpi">{body}</div>
}

function FilterSelect({ label, value, onChange, options }) {
  return (
    <label className="lw-select">
      <span className="lw-select-label">{label}</span>
      <select value={value} onChange={e => onChange(e.target.value)}>
        {options.map(o => <option key={o.value || '_all'} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  )
}

/* ── "Move to…" menu, shared by cards, rows and the drawer ────────────── */

function MoveMenu({ lead, tiers, onMove, onOpen, align = 'right' }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  return (
    <div className="lw-menu-wrap" onClick={e => e.stopPropagation()}>
      <button type="button" className="lw-iconbtn" aria-label={`Actions for ${leadName(lead)}`}
        aria-expanded={open} onClick={() => setOpen(v => !v)}>
        <Icon name="dots" size={18} />
      </button>
      {open && (
        <div className={`lw-menu lw-menu--${align}`} role="menu" onMouseLeave={() => setOpen(false)}>
          {onOpen && <button type="button" role="menuitem" onClick={() => { setOpen(false); onOpen(lead.id) }}>Open lead</button>}
          <div className="lw-menu-head">Move to…</div>
          {tiers.map(t => (
            <button key={t.key} type="button" role="menuitem" disabled={busy || t.key === lead.tier}
              onClick={async () => { setBusy(true); await onMove(lead, t.key); setBusy(false); setOpen(false) }}>
              {t.label}{t.key === lead.tier ? ' (current)' : ''}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ═════════════════════════════════════════════════════════════════════════
   PIPELINE BOARD — one column per configured tier.
   ═════════════════════════════════════════════════════════════════════════ */

const TONES = ['blue', 'amber', 'violet', 'green', 'orange', 'teal', 'slate']

function PipelineBoard({ canContacts, tiers, byTier, filters, reloadKey, noLeadsAtAll, totalLeads, tierLabel, userName, onOpen, onMove, onAdd, onViewAll }) {
  const [cols, setCols] = useState({})
  const visible = filters.tier ? tiers.filter(t => t.key === filters.tier) : tiers
  const tierKeys = visible.map(t => t.key).join('|')

  useEffect(() => {
    let alive = true
    const keys = tierKeys ? tierKeys.split('|') : []
    setCols(prev => {
      const next = {}
      keys.forEach(k => { next[k] = { ...(prev[k] || {}), loading: true, error: null } })
      return next
    })
    const base = filterParams(filters)
    Promise.allSettled(keys.map(k => api.get(leadsQuery({ ...base, pageSize: BOARD_PAGE, tier: k }))))
      .then(results => {
        if (!alive) return
        const next = {}
        results.forEach((r, i) => {
          const k = keys[i]
          if (r.status === 'fulfilled') {
            const { items, total } = envelope(r.value)
            next[k] = { items, total, loading: false, error: null }
          } else {
            next[k] = { items: [], total: null, loading: false, error: errText(r.reason) }
          }
        })
        setCols(next)
      })
    return () => { alive = false }
  }, [tierKeys, filters.status, filters.list, filters.q, filters.assigned, reloadKey])

  if (!tiers.length) {
    return <div className="lw-panel lw-empty">No pipeline stages are configured for this workspace yet.</div>
  }

  // Leads whose tier is not one of the configured stages cannot sit in a
  // column; say how many there are instead of dropping them silently.
  let unstaged = null
  if (byTier && isNum(totalLeads)) {
    const staged = tiers.reduce((s, t) => s + (isNum(byTier[t.key]) ? byTier[t.key] : 0), 0)
    unstaged = Math.max(0, totalLeads - staged)
  }
  const filtered = Boolean(filters.q || filters.assigned || filters.status || filters.list)

  return (
    <>
      <div className="lw-board" role="list">
        {visible.map((t, idx) => {
          const col = cols[t.key] || { loading: true, items: [] }
          const items = col.items || []
          const count = isNum(col.total) ? col.total
            : (!filtered && byTier && isNum(byTier[t.key]) ? byTier[t.key] : null)
          const tone = TONES[tiers.findIndex(x => x.key === t.key) % TONES.length] || TONES[idx % TONES.length]
          const first = tiers[0]?.key === t.key
          return (
            <section key={t.key} className={`lw-col lw-col--${tone}`} role="listitem" aria-label={t.label}>
              <header className="lw-col-head">
                <span className="lw-col-title">{t.label}</span>
                <span className="lw-count">{count == null ? '—' : count.toLocaleString('en-US')}</span>
                <button type="button" className="lw-iconbtn lw-col-add" aria-label={`Add lead to ${t.label}`} onClick={() => onAdd(t.key)}>
                  <Icon name="plus" size={16} />
                </button>
              </header>
              <div className="lw-col-body">
                {col.loading && <div className="lw-muted lw-pad">Loading…</div>}
                {!col.loading && col.error && <div className="lw-banner lw-banner--error">{col.error}</div>}
                {!col.loading && !col.error && items.length === 0 && (
                  <div className="lw-col-empty">
                    <span className="lw-col-empty-icon"><Icon name={first ? 'users' : 'calendar'} size={28} /></span>
                    <strong>{noLeadsAtAll && first ? 'No leads yet' : filtered ? 'No matching leads' : 'No leads in this stage'}</strong>
                    <p>{noLeadsAtAll && first
                      ? 'Leads will appear here when you convert from your contacts or add new leads.'
                      : filtered ? 'Nothing in this stage matches the current filters.'
                        : `Use “Move to…” on a lead to place it in ${t.label}.`}</p>
                    <button type="button" className="lw-btn lw-btn--outline" onClick={() => onAdd(t.key)}>
                      <Icon name="plus" size={16} /> Add lead
                    </button>
                    {noLeadsAtAll && first && canContacts && <Link className="lw-link" to="/contacts">Browse contacts</Link>}
                  </div>
                )}
                {!col.loading && items.map(l => (
                  <article key={l.id} className="lw-card" tabIndex={0} onClick={() => onOpen(l.id)}
                    onKeyDown={e => { if (e.key === 'Enter') onOpen(l.id) }}>
                    <div className="lw-card-top">
                      <span className="lw-card-name">{leadName(l)}</span>
                      <MoveMenu lead={l} tiers={tiers} onMove={onMove} onOpen={onOpen} />
                    </div>
                    <div className="lw-card-meta">
                      {l.phone ? formatPhone(l.phone) : l.email || 'No phone or email'}
                    </div>
                    <div className="lw-card-foot">
                      {l.status ? <StatusBadge status={l.status} /> : null}
                      <span className="lw-card-owner" title={userName(l.assigned_to_id) || 'Unassigned'}>
                        {l.assigned_to_id ? initials(userName(l.assigned_to_id)) : 'Unassigned'}
                      </span>
                      <span className="lw-muted lw-card-age">{ago(l.created_at)}</span>
                    </div>
                  </article>
                ))}
                {!col.loading && isNum(col.total) && col.total > (col.items || []).length && (
                  <button type="button" className="lw-link lw-col-more" onClick={() => onViewAll(t.key)}>
                    Showing newest {(col.items || []).length} of {col.total.toLocaleString('en-US')} — view all
                  </button>
                )}
              </div>
            </section>
          )
        })}
      </div>
      {unstaged > 0 && !filters.tier && (
        <p className="lw-note">{unstaged.toLocaleString('en-US')} lead{unstaged === 1 ? ' has' : 's have'} a stage outside this pipeline
          and {unstaged === 1 ? 'is' : 'are'} not shown on the board. Find {unstaged === 1 ? 'it' : 'them'} under All Leads.</p>
      )}
    </>
  )
}

/* ═════════════════════════════════════════════════════════════════════════
   TABLE — All / Mine / Recently Active / Lost.
   ═════════════════════════════════════════════════════════════════════════ */

function LeadTable({ canContacts, mode, filters, me, reloadKey, tierLabel, userName, summary, noLeadsAtAll, onOpen, onAdd, onDeleted }) {
  const [page, setPage] = useState(1)
  const [state, setState] = useState({ loading: true, rows: [], total: 0, error: null, windowed: false, truncated: false })
  const [selected, setSelected] = useState(() => new Set())
  const [deleting, setDeleting] = useState(false)

  useEffect(() => { setPage(1) }, [mode, filters.q, filters.list, filters.status, filters.assigned, filters.tier])

  const lostStatuses = mode === 'lost'
    ? (filters.status ? (LOST_STATUSES.includes(filters.status) ? [filters.status] : []) : LOST_STATUSES)
    : null
  // "All lost" is the one view the server cannot express as a single query
  // (status takes one value), so it merges one bounded query per lost status.
  const merged = mode === 'lost' && lostStatuses.length > 1

  useEffect(() => {
    let alive = true
    setState(s => ({ ...s, loading: true, error: null }))
    const base = filterParams(filters)
    if (mode === 'mine') base.assigned = me?.id || '__no_user__'
    const sort = mode === 'recent' ? 'activity' : 'recent'
    let req
    if (mode === 'lost' && lostStatuses.length === 0) {
      req = Promise.resolve({ items: [], total: 0, windowed: false, truncated: false })
    } else if (merged) {
      req = Promise.all(lostStatuses.map(st => api.get(leadsQuery({ ...base, status: st, pageSize: LOST_MERGE }))))
        .then(res => {
          const envs = res.map(envelope)
          const items = envs.flatMap(e => e.items)
            .sort((x, y) => new Date(y.created_at || 0) - new Date(x.created_at || 0))
          return { items, total: items.length, windowed: true, truncated: envs.some(e => e.total > e.items.length) }
        })
    } else {
      const status = mode === 'lost' ? lostStatuses[0] : base.status
      req = api.get(leadsQuery({ ...base, status, sort, page, pageSize: TABLE_PAGE }))
        .then(d => ({ ...envelope(d), windowed: false, truncated: false }))
    }
    req.then(({ items, total, windowed, truncated }) => {
      if (alive) setState({ loading: false, rows: items, total, error: null, windowed, truncated })
    }).catch(e => { if (alive) setState({ loading: false, rows: [], total: 0, error: errText(e), windowed: false, truncated: false }) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, merged ? 0 : page, filters.q, filters.list, filters.status, filters.assigned, filters.tier, reloadKey, me?.id])

  const pageRows = state.windowed ? state.rows.slice((page - 1) * TABLE_PAGE, page * TABLE_PAGE) : state.rows
  const pages = Math.max(1, Math.ceil((state.total || 0) / TABLE_PAGE))

  // A different page, tab or filter starts with nothing selected.
  useEffect(() => { setSelected(new Set()) }, [mode, page, filters.q, filters.list, filters.status, filters.assigned, filters.tier, reloadKey])
  const pageIds = pageRows.map(l => l.id)
  const allOnPage = pageIds.length > 0 && pageIds.every(id => selected.has(id))
  function toggleOne(id) {
    setSelected(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }
  function togglePage() {
    setSelected(prev => {
      const n = new Set(prev)
      if (allOnPage) pageIds.forEach(id => n.delete(id)); else pageIds.forEach(id => n.add(id))
      return n
    })
  }
  async function deleteSelected() {
    if (!selected.size || deleting) return
    if (!confirmLeadDelete(selected.size)) return
    setDeleting(true)
    try {
      const r = await deleteLeadIds(api, selected)
      setSelected(new Set())
      onDeleted && onDeleted(deleteSummary(r))
    } finally { setDeleting(false) }
  }

  return (
    <div className="lw-panel lw-tablepanel">
      {mode === 'recent' && (
        <p className="lw-note">
          Ordered by most recent outbound message; leads never messaged follow, newest first.
          {isNum(summary?.recently_active_7d) ? ` ${summary.recently_active_7d.toLocaleString('en-US')} lead${summary.recently_active_7d === 1 ? ' was' : 's were'} active in the last 7 days.` : ''}
        </p>
      )}
      {mode === 'mine' && !me?.id && <p className="lw-note">Your user record is not loaded, so assigned leads cannot be matched.</p>}
      {mode === 'lost' && (
        <p className="lw-note">
          Lost leads are those with status Not Interested, Dead or DNC.
          {summary?.closed_won == null ? ' Closed-won is not tracked as a separate stage for this workspace.' : ` Closed-won: ${summary.closed_won.toLocaleString('en-US')}.`}
          {filters.status && !LOST_STATUSES.includes(filters.status) ? ' The selected status is not a lost status.' : ''}
        </p>
      )}
      {state.windowed && state.truncated && (
        <p className="lw-note">Showing the newest {LOST_MERGE} leads per lost status. Choose a single status in the filter to page through all of them.</p>
      )}
      {state.error && <div className="lw-banner lw-banner--error">{state.error}</div>}
      {selected.size > 0 && (
        <div className="lw-bulkbar" data-testid="leads-bulkbar">
          <strong>{selected.size.toLocaleString('en-US')} selected</strong>
          <button type="button" className="lw-btn lw-btn--danger" disabled={deleting} onClick={deleteSelected}
            data-testid="leads-delete-selected">
            {deleting ? 'Deleting…' : `Delete ${selected.size === 1 ? 'lead' : 'leads'}`}
          </button>
          <button type="button" className="lw-btn" disabled={deleting} onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
      )}
      {state.loading ? <div className="lw-muted lw-pad">Loading…</div> : !state.error && pageRows.length === 0 ? (
        <div className="lw-empty">
          <span className="lw-col-empty-icon"><Icon name="users" size={28} /></span>
          <strong>{noLeadsAtAll ? 'No leads yet' : 'No leads match this view'}</strong>
          <p>{noLeadsAtAll ? 'Leads will appear here when you convert from your contacts or add new leads.' : 'Try clearing a filter or switching tabs.'}</p>
          {noLeadsAtAll && (
            <div className="lw-row-actions">
              <button type="button" className="lw-btn lw-btn--primary" onClick={onAdd}><Icon name="plus" size={16} /> Add lead</button>
              {canContacts && <Link className="lw-btn" to="/contacts">Browse contacts</Link>}
            </div>
          )}
        </div>
      ) : (
        <div className="lw-tablewrap">
          <table className="lw-table">
            <thead>
              <tr><th className="lw-check"><input type="checkbox" checked={allOnPage} onChange={togglePage} aria-label="Select all leads on this page" /></th><th>Name</th><th>Contact</th><th>Stage</th><th>Status</th><th>Assigned</th><th>Lead list</th><th>{mode === 'recent' ? 'Last activity' : 'Created'}</th></tr>
            </thead>
            <tbody>
              {pageRows.map(l => (
                <tr key={l.id} tabIndex={0} onClick={() => onOpen(l.id)} onKeyDown={e => { if (e.key === 'Enter') onOpen(l.id) }}>
                  <td className="lw-check" onClick={e => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggleOne(l.id)} aria-label={`Select ${leadName(l)}`} />
                  </td>
                  <td className="lw-strong">{leadName(l)}</td>
                  <td>{l.phone ? formatPhone(l.phone) : ''}{l.phone && l.email ? <br /> : null}<span className="lw-muted">{l.email || (!l.phone ? '—' : '')}</span></td>
                  <td>{tierLabel(l.tier)}</td>
                  <td>{l.status ? <StatusBadge status={l.status} /> : '—'}</td>
                  <td>{userName(l.assigned_to_id) || <span className="lw-muted">Unassigned</span>}</td>
                  <td>{l.import_list_name || l.source_file || '—'}</td>
                  <td className="lw-nowrap">{mode === 'recent' ? ago(l.last_messaged_at || l.created_at) : fmtDateTime(l.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!state.loading && state.total > TABLE_PAGE && (
        <div className="lw-pager">
          <button type="button" className="lw-btn" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
          <span className="lw-muted">Page {page} of {pages} · {state.total.toLocaleString('en-US')} leads</span>
          <button type="button" className="lw-btn" disabled={page >= pages} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}
    </div>
  )
}

/* ═════════════════════════════════════════════════════════════════════════
   LEAD DRAWER
   ═════════════════════════════════════════════════════════════════════════ */

const DRAWER_TABS = ['overview', 'activity', 'notes', 'tasks', 'related']

function LeadDrawer({ leadId, tiers, tierLabel, userName, reloadKey, onClose, onMove, onSaved, onDeleted }) {
  const [deleting, setDeleting] = useState(false)
  const [delErr, setDelErr] = useState(null)
  const [lead, setLead] = useState(null)
  const [err, setErr] = useState(null)
  const [tab, setTab] = useState('overview')
  const [activity, setActivity] = useState(null)
  const [actErr, setActErr] = useState(null)
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState(null)

  useEffect(() => {
    let alive = true
    setErr(null)
    api.get(`/leads/${leadId}`)
      .then(d => { if (alive) { setLead(d); setNotes(d?.notes || '') } })
      .catch(e => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [leadId, reloadKey])

  useEffect(() => {
    if (tab !== 'activity') return
    let alive = true
    setActErr(null)
    api.get(`/leads/${leadId}/activity`)
      .then(d => { if (alive) setActivity(Array.isArray(d?.events) ? [...d.events].reverse() : []) })
      .catch(e => { if (alive) setActErr(errText(e)) })
    return () => { alive = false }
  }, [tab, leadId, reloadKey])

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function saveNotes() {
    setSaving(true); setSaveMsg(null)
    try {
      await api.patch(`/leads/${leadId}`, { notes })
      setSaveMsg({ kind: 'ok', text: 'Notes saved.' })
      onSaved()
    } catch (e) {
      setSaveMsg({ kind: 'error', text: errText(e) })
    } finally { setSaving(false) }
  }

  const name = lead ? leadName(lead) : ''

  async function deleteThis() {
    if (!lead || deleting) return
    if (!confirmLeadDelete(1, name)) return
    setDeleting(true); setDelErr(null)
    try {
      const r = await deleteLeadIds(api, [lead.id])
      const out = deleteSummary(r)
      if (out.ok) onDeleted && onDeleted(`${name || 'Lead'} deleted.`)
      else setDelErr(out.text)
    } finally { setDeleting(false) }
  }
  const address = lead ? [lead.street_address, [lead.city, lead.state].filter(Boolean).join(', '), lead.zip_code].filter(Boolean).join(' ') : ''
  const sms = lead?.sms_consent === true
    ? `Consent on record${lead.sms_consent_timestamp ? ` · ${fmtDateTime(lead.sms_consent_timestamp)}` : ''}${lead.sms_consent_source ? ` · ${lead.sms_consent_source}` : ''}`
    : lead?.sms_consent === false ? 'No SMS consent on record' : 'Not recorded'
  const perm = v => (v === true ? 'Allowed' : v === false ? 'Not allowed' : 'Not recorded')

  return (
    <div className="lw-drawer-layer" onClick={onClose}>
      <aside className="lw-drawer" role="dialog" aria-modal="true" aria-label={name || 'Lead'} onClick={e => e.stopPropagation()}>
        <div className="lw-drawer-head">
          <div className="lw-drawer-tags">
            {lead && <span className="lw-chip">{tierLabel(lead.tier)}</span>}
            {lead?.status && <StatusBadge status={lead.status} />}
          </div>
          <button type="button" className="lw-iconbtn" aria-label="Close" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        {err && <div className="lw-banner lw-banner--error">{err}</div>}
        {!lead && !err && <div className="lw-muted lw-pad">Loading…</div>}
        {lead && (
          <>
            <div className="lw-drawer-id">
              <span className="lw-avatar">{initials(name)}</span>
              <div>
                <div className="lw-drawer-name">{name}</div>
                {lead.import_list_name && <div className="lw-muted">{lead.import_list_name}</div>}
              </div>
            </div>
            <div className="lw-drawer-actions">
              <Link className="lw-btn lw-btn--primary" to={`/leads/${lead.id}`}>Open full record</Link>
              <MoveMenu lead={lead} tiers={tiers} align="right"
                onMove={async (l, t) => { const ok = await onMove(l, t); if (ok) setLead(x => ({ ...x, tier: t })); return ok }} />
              <button type="button" className="lw-btn lw-btn--danger" onClick={deleteThis} disabled={deleting}
                data-testid="lead-delete">{deleting ? 'Deleting…' : 'Delete lead'}</button>
            </div>
            {delErr && <div className="lw-banner lw-banner--error" role="alert">{delErr}</div>}
            <nav className="lw-drawer-tabs" role="tablist">
              {DRAWER_TABS.map(t => (
                <button key={t} type="button" role="tab" aria-selected={tab === t}
                  className={`lw-tab${tab === t ? ' is-active' : ''}`} onClick={() => setTab(t)}>{humanize(t)}</button>
              ))}
            </nav>
            <div className="lw-drawer-body">
              {tab === 'overview' && (
                <>
                  <h3>Contact information</h3>
                  <dl className="lw-dl">
                    <dt>Name</dt><dd>{name}</dd>
                    <dt>Phone</dt><dd>{lead.phone ? formatPhone(lead.phone) : '—'}</dd>
                    <dt>Email</dt><dd>{lead.email || '—'}</dd>
                    <dt>Address</dt><dd>{address || '—'}</dd>
                    <dt>Source</dt><dd>{[lead.source ? humanize(lead.source) : null, lead.source_detail, lead.source_file].filter(Boolean).join(' · ') || '—'}</dd>
                    <dt>Created</dt><dd>{fmtDateTime(lead.created_at)}</dd>
                    <dt>Assigned to</dt><dd>{userName(lead.assigned_to_id) || 'Unassigned'}</dd>
                  </dl>
                  <h3>Lead details</h3>
                  <dl className="lw-dl">
                    <dt>Stage</dt><dd>{tierLabel(lead.tier)}</dd>
                    <dt>Status</dt><dd>{lead.status ? <StatusBadge status={lead.status} /> : '—'}</dd>
                    <dt>Relationship</dt><dd>{lead.relationship_type ? humanize(lead.relationship_type) : '—'}</dd>
                    <dt>Last contact</dt><dd>{fmtDateTime(lead.last_contact_date || lead.last_messaged_at)}</dd>
                  </dl>
                  <h3>Contactability</h3>
                  <dl className="lw-dl">
                    <dt>SMS consent</dt><dd>{sms}</dd>
                    <dt>SMS</dt><dd>{perm(lead.allow_sms)}</dd>
                    <dt>Email</dt><dd>{perm(lead.allow_email)}</dd>
                    <dt>Voice</dt><dd>{perm(lead.allow_voice)}</dd>
                    {lead.permission_review === true && (<><dt>Review</dt><dd>Permissions need review</dd></>)}
                  </dl>
                  <p className="lw-note">Having a phone number is not SMS permission.</p>
                </>
              )}
              {tab === 'activity' && (
                actErr ? <div className="lw-banner lw-banner--error">{actErr}</div>
                  : activity == null ? <div className="lw-muted lw-pad">Loading…</div>
                    : activity.length === 0 ? <p className="lw-muted">No activity recorded.</p>
                      : (
                        <ul className="lw-feed">
                          {activity.map(ev => (
                            <li key={ev.id}>
                              <div className="lw-feed-title">{ev.label || humanize(ev.type)}</div>
                              {ev.body && <div className="lw-muted lw-feed-body">{ev.body}</div>}
                              <div className="lw-muted lw-feed-time">{fmtDateTime(ev.ts)}</div>
                            </li>
                          ))}
                        </ul>
                      )
              )}
              {tab === 'notes' && (
                <div className="lw-notes">
                  <textarea rows={8} value={notes} onChange={e => setNotes(e.target.value)} aria-label="Lead notes" />
                  <div className="lw-row-actions">
                    <button type="button" className="lw-btn lw-btn--primary" disabled={saving || notes === (lead.notes || '')} onClick={saveNotes}>
                      {saving ? 'Saving…' : 'Save notes'}
                    </button>
                    {saveMsg && <span className={saveMsg.kind === 'error' ? 'lw-error' : 'lw-muted'}>{saveMsg.text}</span>}
                  </div>
                </div>
              )}
              {tab === 'tasks' && (
                <div>
                  <p className="lw-muted">Follow-ups and callbacks are worked from Tasks &amp; Follow-Up.</p>
                  <Link className="lw-btn" to="/workqueue">Open in Tasks &amp; Follow-Up</Link>
                </div>
              )}
              {tab === 'related' && (
                <ul className="lw-related">
                  {lead.org_contact_id && (
                    <li><span>Linked contact</span><Link to={`/contacts?focus=${encodeURIComponent(lead.org_contact_id)}`}>Open contact</Link></li>
                  )}
                  {lead.duplicate_of_lead_id && (
                    <li><span>Possible duplicate of</span><Link to={`/leads/${lead.duplicate_of_lead_id}`}>Open lead</Link></li>
                  )}
                  {!lead.org_contact_id && !lead.duplicate_of_lead_id && <li className="lw-muted">No related records.</li>}
                </ul>
              )}
            </div>
          </>
        )}
      </aside>
    </div>
  )
}

/* ═════════════════════════════════════════════════════════════════════════
   ADD LEAD — explicit form submit only (POST /leads/create, same fields the
   classic page sends). Nothing is sent to the lead.
   ═════════════════════════════════════════════════════════════════════════ */

function AddLeadModal({ tiers, initialTier, onClose, onCreated }) {
  const [form, setForm] = useState({ first_name: '', last_name: '', phone: '', email: '', tier: initialTier, notes: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  async function submit(e) {
    e.preventDefault()
    if (!form.first_name.trim() || !form.last_name.trim()) { setError('First and last name are required.'); return }
    setSaving(true); setError(null)
    try {
      const payload = {
        first_name: form.first_name.trim(), last_name: form.last_name.trim(),
        phone: form.phone.trim() || null, email: form.email.trim() || null,
        tier: form.tier || null, source_year: null,
      }
      if (form.notes.trim()) payload.notes = form.notes.trim()
      const res = await api.post('/leads/create', payload)
      onCreated(res)
    } catch (err) {
      setError(errText(err))
    } finally { setSaving(false) }
  }

  return (
    <div className="lw-drawer-layer lw-modal-layer" onClick={onClose}>
      <form className="lw-modal" onSubmit={submit} onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Add lead">
        <div className="lw-drawer-head">
          <h2>Add lead</h2>
          <button type="button" className="lw-iconbtn" aria-label="Close" onClick={onClose}><Icon name="x" size={18} /></button>
        </div>
        <div className="lw-form-grid">
          <label>First name *<input value={form.first_name} onChange={e => set('first_name', e.target.value)} required /></label>
          <label>Last name *<input value={form.last_name} onChange={e => set('last_name', e.target.value)} required /></label>
          <label>Phone<input type="tel" value={form.phone} onChange={e => set('phone', e.target.value)} /></label>
          <label>Email<input type="email" value={form.email} onChange={e => set('email', e.target.value)} /></label>
          <label className="lw-span2">Stage
            <select value={form.tier} onChange={e => set('tier', e.target.value)}>
              {tiers.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          </label>
          <label className="lw-span2">Notes<textarea rows={3} value={form.notes} onChange={e => set('notes', e.target.value)} /></label>
        </div>
        <p className="lw-note">Adding a lead does not send a message or enroll it in any sequence. A phone number is not SMS permission.</p>
        {error && <div className="lw-banner lw-banner--error">{error}</div>}
        <div className="lw-row-actions lw-row-actions--end">
          <button type="button" className="lw-btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="lw-btn lw-btn--primary" disabled={saving}>{saving ? 'Adding…' : 'Add lead'}</button>
        </div>
      </form>
    </div>
  )
}

/* ═════════════════════════════════════════════════════════════════════════
   RECENT ACTIVITY — import batches (GET /intake/batches) and newly added
   leads (GET /leads/, created_at). Nothing else is claimed.
   ═════════════════════════════════════════════════════════════════════════ */

function RecentActivity({ reloadKey, isManager, onOpenLead }) {
  const [items, setItems] = useState(null)
  const [batchesDenied, setBatchesDenied] = useState(false)

  useEffect(() => {
    let alive = true
    Promise.allSettled([
      api.get('/intake/batches?page=1&per_page=5'),
      api.get(leadsQuery({ pageSize: 5 })),
    ]).then(([b, l]) => {
      if (!alive) return
      const out = []
      if (b.status === 'fulfilled') {
        ;(b.value?.batches || []).forEach(x => {
          const c = x.counts || {}
          const detail = [x.batch_code, x.filename].filter(Boolean).join(' — ')
          const parts = []
          if (isNum(c.imported) && c.imported) parts.push(`${c.imported.toLocaleString('en-US')} contacts imported`)
          if (isNum(c.leads_created)) parts.push(`${c.leads_created.toLocaleString('en-US')} leads created`)
          out.push({
            key: `b-${x.id}`, icon: 'upload',
            title: x.committed_at ? 'Import completed' : x.rolled_back_at ? 'Import rolled back' : `Import ${humanize(x.status || 'staged').toLowerCase()}`,
            body: [detail, parts.join(' · ')].filter(Boolean).join(' · '),
            ts: x.rolled_back_at || x.committed_at || x.created_at, to: `/imports/${x.id}`,
          })
        })
      } else setBatchesDenied(true)
      if (l.status === 'fulfilled') {
        envelope(l.value).items.forEach(x => out.push({
          key: `l-${x.id}`, icon: 'users', title: 'Lead added', body: leadName(x), ts: x.created_at, leadId: x.id,
        }))
      }
      out.sort((a, c) => new Date(c.ts || 0) - new Date(a.ts || 0))
      setItems(out.slice(0, 6))
    })
    return () => { alive = false }
  }, [reloadKey])

  return (
    <section className="lw-panel">
      <header className="lw-panel-head">
        <h2>Recent Activity</h2>
        {isManager && !batchesDenied && <Link className="lw-link" to="/imports">View all imports</Link>}
      </header>
      {items == null ? <div className="lw-muted lw-pad">Loading…</div>
        : items.length === 0 ? <p className="lw-muted lw-pad">No recent imports or new leads.</p> : (
          <ul className="lw-activity">
            {items.map(it => {
              const inner = (
                <>
                  <span className="lw-activity-icon"><Icon name={it.icon} size={18} /></span>
                  <span className="lw-activity-text">
                    <strong>{it.title}</strong>
                    {it.body && <span className="lw-muted">{it.body}</span>}
                  </span>
                  <span className="lw-muted lw-nowrap">{ago(it.ts)}</span>
                </>
              )
              return (
                <li key={it.key}>
                  {it.to ? <Link to={it.to} className="lw-activity-row">{inner}</Link>
                    : <button type="button" className="lw-activity-row" onClick={() => onOpenLead(it.leadId)}>{inner}</button>}
                </li>
              )
            })}
          </ul>
        )}
    </section>
  )
}

/* ── Lead Conversion Tools ────────────────────────────────────────────── */

function ConversionTools({ isManager }) {
  const tools = [
    isManager ? { to: '/contacts', icon: 'users', title: 'Browse Contacts', body: 'Search contacts and convert qualified ones to leads.' } : null,
    isManager ? { to: '/imports/new', icon: 'upload', title: 'Import New Leads', body: 'Add records from a file through the import center.' } : null,
    isManager ? { to: '/tier-definitions', icon: 'gear', title: 'Pipeline Settings', body: 'Review your lead stages and what each one means.' } : null,
  ].filter(Boolean)
  return (
    <section className="lw-panel">
      <header className="lw-panel-head"><h2>Lead Conversion Tools</h2></header>
      <ul className="lw-tools">
        {tools.map(t => (
          <li key={t.to}>
            <Link to={t.to} className="lw-tool">
              <span className="lw-activity-icon"><Icon name={t.icon} size={18} /></span>
              <span className="lw-activity-text"><strong>{t.title}</strong><span className="lw-muted">{t.body}</span></span>
              <Icon name="chevron" size={16} />
            </Link>
          </li>
        ))}
        <li>
          <div className="lw-tool lw-tool--info">
            <span className="lw-activity-icon"><Icon name="flow" size={18} /></span>
            <span className="lw-activity-text">
              <strong>Automation</strong>
              <span className="lw-muted">Not enabled from this screen. Leads added or moved here are not enrolled in any sequence and nothing is sent.</span>
            </span>
          </div>
        </li>
      </ul>
    </section>
  )
}
