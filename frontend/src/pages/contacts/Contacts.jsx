// THE CONTACTS WORKSPACE.
//
// The organization's canonical contact database - every person or company an
// import brought in - browsable, filterable and inspectable. It is NOT an
// outreach surface: there is no bulk action, nothing here sends a message,
// and the only write is the per-contact "Promote to Lead" in the drawer,
// which a person has to confirm.
//
// Every number on this page is the server's. A KPI key the API does not
// return reads "Not yet available" rather than a zero someone would believe.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import PageShell from '../../components/PageShell'
import '../../components/StatusBadge.css'
import './Contacts.css'
import ContactDrawer from './ContactDrawer'
import {
  RECORD_CLASS_OPTIONS, classTone, displayName, emailTone, fmtNum, humanize,
  initials, personName,
} from './contactsShared'

const PER_PAGE = 50
const EXCLUDED_BATCH_STATUSES = ['rolled_back', 'cancelled']

const TRI = [
  { value: '', label: 'Any' },
  { value: 'true', label: 'Yes' },
  { value: 'false', label: 'No' },
]

const SORTS = [
  { value: 'recent', label: 'Most recent' },
  { value: 'name', label: 'Name' },
  { value: 'company', label: 'Company' },
]

const EMPTY_FILTERS = {
  record_class: '', has_email: '', has_phone: '', email_ready: '',
  needs_enrichment: '', promoted: '', batch_id: '', sort: 'recent',
}

/* ── icons (inline, stroke-only, inherit colour) ─────────────────────────── */
const ICON_PATHS = {
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 6l-10 7L2 6',
  phone: 'M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z',
  alert: 'M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z',
  history: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l4 2',
  arrow: 'M5 12h14M12 5l7 7-7 7',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
}
function Icon({ name, size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICON_PATHS[name]} />
    </svg>
  )
}

function has(obj, key) {
  return obj && Object.prototype.hasOwnProperty.call(obj, key) && obj[key] !== null && obj[key] !== undefined
}

function pct(part, whole) {
  if (typeof part !== 'number' || typeof whole !== 'number' || whole <= 0) return null
  return `${Math.round((part / whole) * 100)}% of contacts`
}

function Kpi({ icon, tone, label, value, sub, loading }) {
  const missing = value === null || value === undefined
  return (
    <div className={`cw-kpi cw-kpi--${tone}`}>
      <div className="cw-kpi-icon"><Icon name={icon} size={20} /></div>
      <div className="cw-kpi-body">
        {loading
          ? <div className="cw-kpi-value cw-kpi-value--none">Loading…</div>
          : missing
          ? <div className="cw-kpi-value cw-kpi-value--none">Not yet available</div>
          : <div className="cw-kpi-value">{fmtNum(value)}</div>}
        <div className="cw-kpi-label">{label}</div>
        {!loading && !missing && sub ? <div className="cw-kpi-sub">{sub}</div> : null}
      </div>
    </div>
  )
}

function Select({ label, value, onChange, options }) {
  return (
    <label className="cw-select">
      <span>{label}</span>
      <select value={value} onChange={e => onChange(e.target.value)}>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  )
}

export default function Contacts() {
  const [params, setParams] = useSearchParams()
  const openId = params.get('contact')

  const [summary, setSummary] = useState(null)
  const [summaryErr, setSummaryErr] = useState(null)
  const [batches, setBatches] = useState(null)

  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [page, setPage] = useState(1)

  const [list, setList] = useState(null)
  const [loading, setLoading] = useState(true)
  const [listErr, setListErr] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const seq = useRef(0)

  // Debounce the search box: 300ms after the last keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      const next = searchInput.trim()
      if (next !== search) { setSearch(next); setPage(1) }
    }, 300)
    return () => clearTimeout(t)
  }, [searchInput, search])

  const loadSummary = useCallback(() => {
    api.get('/intake/contacts/summary')
      .then(d => { setSummary(d || {}); setSummaryErr(null) })
      .catch(e => setSummaryErr(e))
  }, [])

  // Every batch, every page - the review banner has to be a true total, not
  // the first page's.
  const loadBatches = useCallback(async () => {
    const all = []
    try {
      for (let p = 1; p <= 50; p += 1) {
        const d = await api.get(`/intake/batches?page=${p}&per_page=100`)
        const rows = (d && Array.isArray(d.batches)) ? d.batches : []
        all.push(...rows)
        const total = d && typeof d.total === 'number' ? d.total : all.length
        if (!rows.length || all.length >= total) break
      }
      setBatches(all)
    } catch (e) {
      setBatches(all.length ? all : null)
    }
  }, [])

  useEffect(() => { loadSummary(); loadBatches() }, [loadSummary, loadBatches])

  const query = useMemo(() => {
    const q = new URLSearchParams()
    if (search) q.set('search', search)
    Object.entries(filters).forEach(([k, v]) => { if (v !== '' && v !== null && v !== undefined) q.set(k, v) })
    q.set('page', String(page))
    q.set('per_page', String(PER_PAGE))
    return q.toString()
  }, [search, filters, page])

  useEffect(() => {
    const mine = ++seq.current
    setLoading(true); setListErr(null)
    api.get(`/intake/contacts?${query}`)
      .then(d => { if (mine === seq.current) setList(d || { total: 0, contacts: [] }) })
      .catch(e => { if (mine === seq.current) { setListErr(e); setList(null) } })
      .finally(() => { if (mine === seq.current) setLoading(false) })
  }, [query, reloadKey])

  function setFilter(key, value) {
    setFilters(f => ({ ...f, [key]: value }))
    setPage(1)
  }

  function clearFilters() {
    setFilters(EMPTY_FILTERS); setSearchInput(''); setSearch(''); setPage(1)
  }

  function openContact(id) {
    const next = new URLSearchParams(params)
    next.set('contact', id)
    setParams(next, { replace: true })
  }
  const closeContact = useCallback(() => {
    const next = new URLSearchParams(params)
    next.delete('contact')
    setParams(next, { replace: true })
  }, [params, setParams])

  const onChanged = useCallback(() => {
    loadSummary()
    setReloadKey(k => k + 1)
  }, [loadSummary])

  // Review rows waiting across batches that were not rolled back/cancelled.
  const reviewWaiting = useMemo(() => {
    if (!Array.isArray(batches)) return 0
    return batches
      .filter(b => !EXCLUDED_BATCH_STATUSES.includes(b.status))
      .reduce((sum, b) => sum + (Number(b.counts && b.counts.review) || 0), 0)
  }, [batches])

  const batchOptions = useMemo(() => {
    const opts = [{ value: '', label: 'All batches' }]
    ;(batches || []).forEach(b => {
      if (!b || !b.id) return
      const name = b.batch_code || b.display_name || b.filename || b.id
      opts.push({ value: b.id, label: b.status === 'rolled_back' ? `${name} (rolled back)` : name })
    })
    return opts
  }, [batches])

  const s = summary || {}
  const total = s.contacts
  const contacts = (list && Array.isArray(list.contacts)) ? list.contacts : []
  const listTotal = list && typeof list.total === 'number' ? list.total : 0
  const pageCount = Math.max(1, Math.ceil(listTotal / PER_PAGE))
  const from = listTotal ? (page - 1) * PER_PAGE + 1 : 0
  const to = Math.min(page * PER_PAGE, listTotal)
  const filtersActive = !!search || Object.entries(filters).some(([k, v]) => v !== EMPTY_FILTERS[k])
  const kpiLoading = !summary && !summaryErr
  const noAccess = (listErr && listErr.status === 403) || (summaryErr && summaryErr.status === 403)

  return (
    <PageShell
      className="cw"
      eyebrow="Customer workspace"
      title="Contacts"
      subtitle="Every contact your imports brought in. Browse, inspect, and promote one at a time to a Lead when it is ready for sales work."
      action={<Link className="cw-btn" to="/imports">Import Center</Link>}
    >
      {noAccess ? (
        <div className="cw-error" role="alert">
          You do not have access to the contact database in this workspace. Ask an administrator for import review access.
        </div>
      ) : null}

      {!noAccess ? (
        <div className="cw-kpis">
          <Kpi loading={kpiLoading} icon="users" tone="blue" label="Total contacts"
               value={has(s, 'contacts') ? s.contacts : null} />
          <Kpi loading={kpiLoading} icon="mail" tone="green" label="Email ready"
               value={has(s, 'email_ready') ? s.email_ready : null}
               sub={pct(s.email_ready, total)} />
          <Kpi loading={kpiLoading} icon="phone" tone="cyan" label="Valid phones"
               value={has(s, 'valid_phones') ? s.valid_phones : null}
               sub={has(s, 'mobile') ? `${fmtNum(s.mobile)} mobile` : 'Mobile count not yet available'} />
          <Kpi loading={kpiLoading} icon="alert" tone="amber" label="Needs enrichment"
               value={has(s, 'needs_enrichment') ? s.needs_enrichment : null}
               sub={pct(s.needs_enrichment, total)} />
          <Kpi loading={kpiLoading} icon="history" tone="purple" label="Previous customers"
               value={has(s, 'previous_customers') ? s.previous_customers : null} />
          <Kpi loading={kpiLoading} icon="arrow" tone="navy" label="Promoted to leads"
               value={has(s, 'promoted') ? s.promoted : null}
               sub={pct(s.promoted, total)} />
        </div>
      ) : null}
      {summaryErr && !noAccess ? (
        <div className="cw-notice cw-notice--warn">Contact totals could not be loaded: {summaryErr.message || 'unknown error'}.</div>
      ) : null}

      {reviewWaiting > 0 ? (
        <Link to="/imports" className="cw-banner">
          <Icon name="alert" />
          <span>
            <strong>{fmtNum(reviewWaiting)}</strong> imported {reviewWaiting === 1 ? 'row is' : 'rows are'} waiting
            in Import review (possible duplicates and conflicts).
          </span>
          <span className="cw-banner-cta">Open Import Center →</span>
        </Link>
      ) : null}

      {!noAccess ? (
        <div className="cw-panel">
          <div className="cw-filters">
            <label className="cw-search">
              <Icon name="search" size={16} />
              <input type="search" value={searchInput} onChange={e => setSearchInput(e.target.value)}
                     placeholder="Search name, company, email or phone…" aria-label="Search contacts" />
            </label>
            <Select label="Classification" value={filters.record_class}
                    onChange={v => setFilter('record_class', v)} options={RECORD_CLASS_OPTIONS} />
            <Select label="Has email" value={filters.has_email} onChange={v => setFilter('has_email', v)} options={TRI} />
            <Select label="Has phone" value={filters.has_phone} onChange={v => setFilter('has_phone', v)} options={TRI} />
            <Select label="Email ready" value={filters.email_ready} onChange={v => setFilter('email_ready', v)} options={TRI} />
            <Select label="Needs enrichment" value={filters.needs_enrichment} onChange={v => setFilter('needs_enrichment', v)} options={TRI} />
            <Select label="Promoted" value={filters.promoted} onChange={v => setFilter('promoted', v)} options={TRI} />
            <Select label="Source batch" value={filters.batch_id} onChange={v => setFilter('batch_id', v)} options={batchOptions} />
            <Select label="Sort" value={filters.sort} onChange={v => setFilter('sort', v)} options={SORTS} />
            {filtersActive ? <button className="cw-link" onClick={clearFilters}>Clear filters</button> : null}
          </div>

          <div className="cw-table-meta">
            <span>
              {loading && !list ? 'Loading…'
                : listTotal ? <>Showing <strong>{fmtNum(from)}–{fmtNum(to)}</strong> of <strong>{fmtNum(listTotal)}</strong></>
                : null}
            </span>
            {loading && list ? <span className="cw-muted">Updating…</span> : null}
          </div>

          {listErr && !noAccess ? (
            <div className="cw-error" role="alert">
              Contacts could not be loaded: {listErr.message || 'unknown error'}.{' '}
              <button className="cw-link" onClick={() => setReloadKey(k => k + 1)}>Try again</button>
            </div>
          ) : null}

          {!listErr && !loading && list && contacts.length === 0 ? (
            <div className="cw-empty">
              <Icon name="users" size={30} />
              {filtersActive ? (
                <>
                  <strong>No contacts match these filters</strong>
                  <span>Try a different search or clear the filters.</span>
                  <button className="cw-btn" onClick={clearFilters}>Clear filters</button>
                </>
              ) : (
                <>
                  <strong>No contacts yet</strong>
                  <span>Contacts appear here after an import is committed.</span>
                  <Link className="cw-btn cw-btn--primary" to="/imports">Go to Import Center</Link>
                </>
              )}
            </div>
          ) : null}

          {contacts.length > 0 ? (
            <div className="cw-table-wrap">
              <table className="cw-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Company</th>
                    <th>Email</th>
                    <th>Phone</th>
                    <th>Classification</th>
                    <th>Enrichment</th>
                    <th>Source / batch</th>
                    <th>Lead</th>
                  </tr>
                </thead>
                <tbody>
                  {contacts.map(c => {
                    const person = personName(c)
                    const phone = c.phone || c.mobile_phone
                    const isMobile = !!c.mobile_phone && phone === c.mobile_phone
                    const needsEnrich = c.needs_enrichment === true || c.lifecycle === 'needs_enrichment'
                    return (
                      <tr key={c.id} className={openId === c.id ? 'cw-row--active' : ''}
                          onClick={() => openContact(c.id)} tabIndex={0}
                          onKeyDown={e => { if (e.key === 'Enter') openContact(c.id) }}>
                        <td>
                          <div className="cw-name">
                            <span className="cw-avatar">{initials(c)}</span>
                            <span className={person ? '' : 'cw-muted'}>{displayName(c)}</span>
                          </div>
                        </td>
                        <td>{c.company || <span className="cw-muted">—</span>}</td>
                        <td>
                          {c.email ? (
                            <div className="cw-cell-stack">
                              <span className="cw-ellipsis" title={c.email}>{c.email}</span>
                              {c.email_status ? <span className={`badge badge--${emailTone(c.email_status)}`}>{humanize(c.email_status)}</span> : null}
                            </div>
                          ) : <span className="cw-muted">—</span>}
                        </td>
                        <td className="cw-nowrap">
                          {phone ? <>{phone}{isMobile ? <span className="cw-tag">Mobile</span> : null}</> : <span className="cw-muted">—</span>}
                        </td>
                        <td>
                          {c.record_class ? (
                            <span className={`badge badge--${classTone(c.record_class)} cw-class cw-class--${c.record_class}`}>
                              {humanize(c.record_class)}
                            </span>
                          ) : <span className="cw-muted">—</span>}
                        </td>
                        <td>
                          {needsEnrich
                            ? <span className="badge badge--amber">Needs enrichment</span>
                            : c.lifecycle ? <span className="cw-muted">{humanize(c.lifecycle)}</span>
                            : <span className="cw-muted">—</span>}
                        </td>
                        <td>
                          <div className="cw-cell-stack">
                            <span>{c.source ? humanize(c.source) : '—'}</span>
                            {c.batch_code ? <span className="mono cw-small">{c.batch_code}</span> : null}
                          </div>
                        </td>
                        <td onClick={e => e.stopPropagation()}>
                          {c.lead_id
                            ? <Link to={`/leads/${c.lead_id}`} className="cw-link">View lead →</Link>
                            : <span className="cw-muted">—</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : null}

          {listTotal > PER_PAGE ? (
            <div className="cw-pager">
              <button className="cw-btn" disabled={page <= 1 || loading} onClick={() => setPage(1)}>First</button>
              <button className="cw-btn" disabled={page <= 1 || loading} onClick={() => setPage(p => Math.max(1, p - 1))}>Previous</button>
              <span>Page {fmtNum(page)} of {fmtNum(pageCount)}</span>
              <button className="cw-btn" disabled={page >= pageCount || loading} onClick={() => setPage(p => Math.min(pageCount, p + 1))}>Next</button>
              <button className="cw-btn" disabled={page >= pageCount || loading} onClick={() => setPage(pageCount)}>Last</button>
            </div>
          ) : null}
        </div>
      ) : null}

      {openId ? <ContactDrawer contactId={openId} onClose={closeContact} onChanged={onChanged} /> : null}
    </PageShell>
  )
}
