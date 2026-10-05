/**
 * FAMILY SERVICE CENTER - a location outreach program's workspace (SCI).
 *
 *   GET  /program/dashboard?location_id=   metrics, attention, pipeline, ...
 *   GET  /program/responses                the response queue (HOT first)
 *   POST /program/responses/{id}/mark      opened / responded / active / closed
 *   GET  /program/records?queue=           Data / Location / Duplicate review
 *   GET  /program/locations, PATCH         location profiles (what families see)
 *   GET  /program/campaigns, PATCH, /preview  one family rendered per location
 *   GET  /program/assets, POST, /active    logos, facility images, approved flyers
 *   PATCH /program/settings, POST /program/import (dry-run / stage only)
 *
 * NOTHING ON THIS PAGE SENDS TO A FAMILY. Previews render text; import stages.
 * The location selector filters every panel that has a location dimension.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, API_BASE, fetchObjectUrl } from '../../api/client'
import { readAuthority } from '../../auth/workspaceAuthority'
import './ProgramCenter.css'

const TABS = [
  { key: 'dashboard', label: 'Dashboard' },
  { key: 'responses', label: 'Responses' },
  { key: 'review', label: 'Review' },
  { key: 'locations', label: 'Locations' },
  { key: 'campaigns', label: 'Campaigns' },
  { key: 'assets', label: 'Assets & Flyers' },
  { key: 'settings', label: 'Settings' },
]
const CLASS_LABEL = { hot: 'HOT', active: 'ACTIVE', low: 'LOW', opt_out: 'OPT-OUT', bad_data: 'BAD DATA' }
const errText = e => e?.detail || e?.message || 'Request failed'
const num = n => (typeof n === 'number' ? n.toLocaleString() : '—')
const when = iso => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
const assetSrc = url => (url ? `${API_BASE}${url}` : null)

export default function ProgramCenter() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.some(t => t.key === params.get('tab')) ? params.get('tab') : 'dashboard'
  const locationId = params.get('location') || ''
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const isManager = readAuthority().isManager
  const navigate = useNavigate()

  const setParam = (key, value) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    setParams(next, { replace: true })
  }
  const load = useCallback(() => {
    let alive = true
    setErr('')
    api.get(`/program/dashboard${locationId ? `?location_id=${encodeURIComponent(locationId)}` : ''}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) { setErr(errText(e)); setData(null) } })
    return () => { alive = false }
  }, [locationId])
  useEffect(() => load(), [load])

  if (err && !data) {
    return <div className="pc"><div className="pc-alert" role="alert">{err}</div></div>
  }
  if (!data) return <div className="pc"><p className="pc-muted">Loading…</p></div>
  const prog = data.program
  const att = data.attention

  return (
    <div className="pc">
      <header className="pc-hero">
        <div className="pc-hero-left">
          {prog.logo_url && <img className="pc-logo" src={assetSrc(prog.logo_url)} alt={`${prog.name} logo`} />}
          <div style={{ minWidth: 0 }}>
            <p className="pc-eyebrow">Company workspace</p>
            <h1 className="pc-title">{prog.hero_title || prog.name}</h1>
            <p className="pc-subtitle">{prog.hero_subtitle || 'Family Service Lead & Communication Center'}</p>
          </div>
        </div>
        <div className="pc-select">
          <label htmlFor="pc-location">Location</label>
          <select id="pc-location" value={locationId} onChange={e => setParam('location', e.target.value)}>
            <option value="">All locations ({data.locations.filter(l => !l.is_review_bucket).length})</option>
            {data.locations.map(l => (
              <option key={l.location_id} value={l.location_id}>{l.name}</option>
            ))}
          </select>
        </div>
      </header>

      <nav className="pc-tabs" role="tablist" aria-label="Family Service Center">
        {TABS.map(t => (
          <button key={t.key} type="button" role="tab" className="pc-tab" aria-selected={tab === t.key}
            onClick={() => setParam('tab', t.key === 'dashboard' ? '' : t.key)}>
            {t.label}
            {t.key === 'responses' && att.hot_responses > 0 && <span className="pc-badge hot">{att.hot_responses}</span>}
          </button>
        ))}
      </nav>

      {tab === 'dashboard' && <Dashboard data={data} go={setParam} navigate={navigate} />}
      {tab === 'responses' && <Responses locationId={locationId} onChange={load} navigate={navigate} />}
      {tab === 'review' && <Review locationId={locationId} locations={data.locations} isManager={isManager}
        initialQueue={params.get('queue') || 'data_review'} onChange={load} />}
      {tab === 'locations' && <Locations isManager={isManager} selected={locationId} onChange={load} />}
      {tab === 'campaigns' && <Campaigns isManager={isManager} locationId={locationId} locations={data.locations} />}
      {tab === 'assets' && <Assets isManager={isManager} locations={data.locations} />}
      {tab === 'settings' && <Settings isManager={isManager} program={prog} readiness={data.readiness} onChange={load} />}
    </div>
  )
}

/* ── dashboard ──────────────────────────────────────────────────────────── */

function Dashboard({ data, go }) {
  const [allLocations, setAllLocations] = useState(false)
  const m = data.metrics
  const att = data.attention
  const total = m.leads || 0
  const sel = data.selected_location
  const metrics = [
    { label: 'Leads', value: m.leads },
    { label: 'Contacts', value: m.contacts },
    { label: 'Locations', value: m.locations },
    { label: 'Qualified', value: m.qualified },
    { label: 'New Responses', value: m.new_responses },
    { label: 'Hot Responses', value: m.hot_responses, hot: m.hot_responses > 0 },
  ]
  const attention = [
    { label: 'Hot Responses', value: att.hot_responses, tone: 'hot', to: ['tab', 'responses'] },
    { label: 'New SMS Replies', value: att.new_sms_replies, to: ['tab', 'responses'] },
    { label: 'New Email Replies', value: att.new_email_replies, to: ['tab', 'responses'] },
    { label: 'Follow-Ups Due', value: att.follow_ups_due, tone: 'amber', to: ['tab', 'responses'] },
    { label: 'Unhandled Hot Leads (past SLA)', value: att.unhandled_hot, tone: 'hot', to: ['tab', 'responses'] },
    { label: 'Data Review', value: att.data_review, tone: 'amber', to: ['tab', 'review'], queue: 'data_review' },
    { label: 'Location Review', value: att.location_review, tone: 'amber', to: ['tab', 'review'], queue: 'location_review' },
    { label: 'Duplicate Review', value: att.duplicate_review, to: ['tab', 'review'], queue: 'duplicate_review' },
  ]
  const openReview = queue => { go('queue', queue); go('tab', 'review') }
  const a = data.automation

  return (
    <>
      <section className="pc-metrics" aria-label="Key numbers">
        {metrics.map(x => (
          <div key={x.label} className={`pc-metric${x.hot ? ' hot' : ''}`}>
            <div className="pc-metric-label">{x.label}</div>
            <div className="pc-metric-value">{num(x.value)}</div>
          </div>
        ))}
      </section>

      <div className="pc-actions">
        <button type="button" className="pc-action primary" onClick={() => go('tab', 'review')}>Work Leads</button>
        <button type="button" className="pc-action" onClick={() => go('tab', 'responses')}>Open Responses</button>
        <button type="button" className="pc-action" onClick={() => go('tab', 'responses')}>Hot Leads</button>
        <a className="pc-action" href="/availability">Appointments</a>
      </div>

      <div className="pc-grid">
        <section className="pc-card" aria-labelledby="pc-att">
          <h2 id="pc-att">Needs My Attention</h2>
          {attention.map(x => (
            <div className="pc-row" key={x.label}>
              <button type="button" className="pc-link"
                onClick={() => (x.queue ? openReview(x.queue) : go(x.to[0], x.to[1]))}>{x.label}</button>
              <span className={`pc-count ${x.value > 0 ? (x.tone || '') : ''}`}>{num(x.value)}</span>
            </div>
          ))}
        </section>

        <section className="pc-card" aria-labelledby="pc-pipe">
          <h2 id="pc-pipe">Lead Pipeline <span className="pc-muted pc-small">{num(total)} total</span></h2>
          {data.pipeline.length === 0 ? <p className="pc-empty">No leads staged yet.</p> : data.pipeline.map(p => (
            <div className="pc-row" key={p.status}>
              <span style={{ width: 90 }}>{p.status}</span>
              <span className="pc-bar navy" aria-hidden="true"><span style={{ width: `${p.share}%` }} /></span>
              <span className="pc-count" style={{ width: 54, textAlign: 'right' }}>{num(p.count)}</span>
              <span className="pc-muted pc-small" style={{ width: 46, textAlign: 'right' }}>{p.share}%</span>
            </div>
          ))}
        </section>

        <section className="pc-card" aria-labelledby="pc-id">
          <h2 id="pc-id">Outbound Identity</h2>
          {sel ? (
            <>
              <div className="pc-identity">
                {sel.hero_url ? <img src={assetSrc(sel.hero_url)} alt="" /> : <div className="pc-ph">No facility image yet</div>}
                <div style={{ minWidth: 0 }}>
                  <strong>{sel.official_name}</strong>
                  <div className="pc-small pc-muted">
                    {[sel.address?.address_line1, sel.address?.city, sel.address?.state].filter(Boolean).join(', ') || 'Address not on file yet'}
                  </div>
                  <div className="pc-small" style={{ marginTop: 6 }}>Email from: <strong>{sel.email_display_name}</strong></div>
                  <div className="pc-small">Text sign-off: <strong>{sel.sms_signoff}</strong></div>
                </div>
              </div>
              <div className="pc-note">All outbound texts and emails for this location carry {sel.official_name}'s name. Families reply by text or email.</div>
            </>
          ) : (
            <>
              <p className="pc-small">{data.program.primary_contact_name || 'The primary contact'} is the person every family hears from; the sender name changes with each family's own location, for example:</p>
              <div className="pc-pre">{data.program.primary_contact_name || 'Kerry Allan'} | {data.locations.find(l => !l.is_review_bucket)?.name || 'Location'}</div>
              <p className="pc-small pc-muted">Choose a location above to see its identity. Contacts with no location are never sent to.</p>
            </>
          )}
        </section>
      </div>

      <div className="pc-grid-2">
        <section className="pc-card" aria-labelledby="pc-locperf">
          <h2 id="pc-locperf">Location Performance</h2>
          <div className="pc-tablewrap">
            <table className="pc-table">
              <thead><tr><th>Location</th><th>Leads</th><th>Qualified</th><th>New Responses</th><th>Hot</th><th>Response rate</th></tr></thead>
              <tbody>
                {(allLocations ? data.location_performance : data.location_performance.slice(0, 10)).map(r => (
                  <tr key={r.location_id} className="clickable" tabIndex={0}
                    onClick={() => go('location', r.location_id)}
                    onKeyDown={e => { if (e.key === 'Enter') go('location', r.location_id) }}>
                    <td>{r.name}{r.is_review_bucket && <span className="pc-badge warn" style={{ marginLeft: 6 }}>review</span>}</td>
                    <td>{num(r.leads)}</td><td>{num(r.qualified)}</td><td>{num(r.new_responses)}</td>
                    <td>{r.hot ? <span className="pc-count hot">{r.hot}</span> : 0}</td>
                    <td>{r.response_rate == null ? '—' : `${r.response_rate}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.location_performance.length > 10 && (
            <button type="button" className="pc-btn" style={{ marginTop: 10 }} onClick={() => setAllLocations(v => !v)}>
              {allLocations ? 'Show top 10' : `Show all ${data.location_performance.length} locations`}
            </button>
          )}
        </section>

        <section className="pc-card" aria-labelledby="pc-recent">
          <h2 id="pc-recent">Recent Conversations</h2>
          {data.recent_conversations.length === 0 ? <p className="pc-empty">No replies yet. Replies by text or email land here.</p>
            : data.recent_conversations.map(r => (
              <div className="pc-row" key={r.id}>
                <div style={{ minWidth: 0 }}>
                  <strong>{r.name || 'Contact'}</strong> <span className={`pc-badge ${r.class}`}>{CLASS_LABEL[r.class]}</span>
                  <div className="pc-small pc-muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.channel === 'sms' ? 'Text' : 'Email'} · {r.location || 'Location review'}
                  </div>
                </div>
                <span className="pc-small pc-muted">{when(r.received_at)}</span>
              </div>
            ))}
        </section>
      </div>

      <div className="pc-grid-2">
        <section className="pc-card" aria-labelledby="pc-camp">
          <h2 id="pc-camp">Campaign Performance</h2>
          {data.campaign_performance.map(c => (
            <div className="pc-row" key={c.key}>
              <span style={{ width: 190 }}>{c.name} {!c.is_active && <span className="pc-badge low">off</span>}</span>
              <span className="pc-bar" aria-hidden="true"><span style={{ width: `${c.share}%` }} /></span>
              <span className="pc-count" style={{ width: 48, textAlign: 'right' }}>{num(c.leads)}</span>
              <span className="pc-muted pc-small" style={{ width: 80, textAlign: 'right' }}>{num(c.responses)} replies</span>
            </div>
          ))}
        </section>
        <section className="pc-card" aria-labelledby="pc-auto">
          <h2 id="pc-auto">Automation Health</h2>
          <div className="pc-health">
            <div><div className="v">{num(a.active_cadences)}</div><div className="pc-small pc-muted">Active cadences</div></div>
            <div><div className="v">{num(a.paused_cadences)}</div><div className="pc-small pc-muted">Paused by a reply</div></div>
            <div><div className="v">{num(a.review_needed)}</div><div className="pc-small pc-muted">Review needed</div></div>
            <div><div className="v">{num(a.suppressed)}</div><div className="pc-small pc-muted">Opted out</div></div>
          </div>
          <div className="pc-note">
            {a.active_campaign_families === 0 ? 'No campaign is switched on. Nothing is being sent.' : `${a.active_campaign_families} campaign(s) switched on.`}
            {a.staged_not_live > 0 && ` ${num(a.staged_not_live)} contacts are staged and not yet live.`}
          </div>
        </section>
      </div>

      {data.reporting && <Reporting r={data.reporting} />}
    </>
  )
}

function Reporting({ r }) {
  const e = r.email
  const s = r.sms
  const rt = r.response_time_minutes
  const cells = [
    ['Emails sent', e.sent], ['Delivered', e.delivered == null ? 'not tracked' : e.delivered], ['Bounced', e.bounced],
    ['Failed', e.failed], ['Opened', e.opened], ['Email replies', e.replied], ['Unsubscribed', e.unsubscribed],
    ['Texts sent', s.sent], ['Texts delivered', s.delivered], ['Text replies', s.replied], ['Appointments', r.appointments],
    ['Response time (median)', rt.median == null ? '—' : `${rt.median} min`],
  ]
  return (
    <section className="pc-card" style={{ marginTop: 14 }} aria-labelledby="pc-rep">
      <h2 id="pc-rep">Delivery & Response Reporting</h2>
      <div className="pc-health" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))' }}>
        {cells.map(([k, v]) => (
          <div key={k}><div className="v" style={{ fontSize: typeof v === 'string' ? 14 : 22 }}>{typeof v === 'number' ? num(v) : v}</div><div className="pc-small pc-muted">{k}</div></div>
        ))}
      </div>
      <div className="pc-note">{e.note} Sent is never counted as delivered.</div>
    </section>
  )
}

/* ── responses ──────────────────────────────────────────────────────────── */

function Responses({ locationId, onChange, navigate }) {
  const [status, setStatus] = useState('open')
  const [cls, setCls] = useState('')
  const [rows, setRows] = useState(null)
  const [sel, setSel] = useState(null)
  const [err, setErr] = useState('')
  const seq = useRef(0)
  const load = useCallback(() => {
    const q = new URLSearchParams()
    if (status) q.set('status', status)
    if (cls) q.set('response_class', cls)
    if (locationId) q.set('location_id', locationId)
    const mine = ++seq.current           // only the newest request may answer
    api.get(`/program/responses?${q}`)
      .then(d => { if (mine === seq.current) { setRows(d.items); setErr('') } })
      .catch(e => { if (mine === seq.current) { setErr(errText(e)); setRows([]) } })
  }, [status, cls, locationId])
  useEffect(() => { load() }, [load])
  const mark = async (r, state) => {
    try {
      await api.post(`/program/responses/${r.id}/mark`, { state })
      setSel({ ...r, status: state }); load(); onChange()
    } catch (e) { setErr(errText(e)) }
  }
  return (
    <section className="pc-card">
      {err && <div className="pc-alert" role="alert">{err}</div>}
      <div className="pc-filters">
        <label className="pc-small">Status
          <select className="pc-input" value={status} onChange={e => setStatus(e.target.value)}>
            <option value="open">Open</option><option value="new">New</option><option value="opened">Opened</option>
            <option value="responded">Responded</option><option value="active">Active</option><option value="closed">Closed</option>
            <option value="">All</option>
          </select>
        </label>
        <label className="pc-small">Class
          <select className="pc-input" value={cls} onChange={e => setCls(e.target.value)}>
            <option value="">All</option>
            {Object.entries(CLASS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>
      <div className="pc-split">
        <div className="pc-list" aria-label="Responses">
          {rows === null ? <p className="pc-muted">Loading…</p> : rows.length === 0 ? <p className="pc-empty">Nothing here.</p>
            : rows.map(r => (
              <button key={r.id} type="button" aria-current={sel?.id === r.id} onClick={() => setSel(r)}>
                <span className={`pc-badge ${r.class}`}>{r.label}</span> {r.sla_breached && <span className="pc-badge hot">past SLA</span>}
                <div><strong>{r.name || 'Contact'}</strong></div>
                <div className="pc-small pc-muted">{r.location || 'Location review'} · {when(r.received_at)}</div>
              </button>
            ))}
        </div>
        <div>
          {!sel ? <p className="pc-empty">Select a response to see the summary and next step.</p> : (
            <div>
              <h2 style={{ fontSize: 17, margin: '0 0 6px' }}>{sel.name || 'Contact'} <span className={`pc-badge ${sel.class}`}>{sel.label}</span></h2>
              <div className="pc-small pc-muted">{sel.channel === 'sms' ? 'Text reply' : 'Email reply'} · {sel.location || 'Location review'} · Lead ID {sel.source_lead_id || '—'} · {sel.campaign_family || 'no campaign'}</div>
              <p><strong>Summary.</strong> {sel.summary}</p>
              <div className="pc-pre">{sel.body}</div>
              <p><strong>Recommended next step.</strong> {sel.recommended_action}</p>
              <p className="pc-small pc-muted">Status: {sel.status}{sel.cadence_paused ? ' · cadence paused' : ''}{sel.sla_due_at ? ` · SLA due ${when(sel.sla_due_at)}` : ''}</p>
              <div className="pc-detail-actions">
                <button type="button" className="pc-btn" onClick={() => mark(sel, 'opened')}>Mark opened</button>
                <button type="button" className="pc-btn" onClick={() => mark(sel, 'responded')}>I responded</button>
                <button type="button" className="pc-btn" onClick={() => mark(sel, 'active')}>Conversation active</button>
                <button type="button" className="pc-btn" onClick={() => mark(sel, 'closed')}>Close</button>
                <button type="button" className="pc-btn primary" onClick={() => navigate(`/leads/${sel.lead_id}`)}>Open conversation</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

/* ── review queues ──────────────────────────────────────────────────────── */

function Review({ locationId, locations, isManager, initialQueue, onChange }) {
  const [queue, setQueue] = useState(initialQueue)
  const [search, setSearch] = useState('')
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    const q = new URLSearchParams({ limit: '300' })
    if (queue && queue !== 'all') q.set('queue', queue)
    if (locationId) q.set('location_id', locationId)
    if (search.trim()) q.set('search', search.trim())
    api.get(`/program/records?${q}`).then(setData).catch(e => { setErr(errText(e)); setData({ total: 0, items: [] }) })
  }, [queue, locationId, search])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])
  const assign = async (rec, loc) => {
    if (!loc) return
    try { await api.post(`/program/records/${rec.id}/location`, { location_id: loc }); load(); onChange() } catch (e) { setErr(errText(e)) }
  }
  const homes = locations.filter(l => !l.is_review_bucket)
  return (
    <section className="pc-card">
      {err && <div className="pc-alert" role="alert">{err}</div>}
      <div className="pc-filters">
        <label className="pc-small">Queue
          <select className="pc-input" value={queue} onChange={e => setQueue(e.target.value)}>
            <option value="data_review">Data Review (source notes)</option>
            <option value="location_review">Location Review</option>
            <option value="duplicate_review">Duplicate Review</option>
            <option value="linked">Linked under one contact</option>
            <option value="all">All staged leads</option>
          </select>
        </label>
        <label className="pc-small">Search
          <input className="pc-input" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name, email or Lead ID" />
        </label>
        <span className="pc-small pc-muted">{data ? `${num(data.total)} records` : ''}</span>
      </div>
      <div className="pc-tablewrap">
        <table className="pc-table">
          <thead><tr><th>Lead ID</th><th>Name (as supplied)</th><th>Location</th><th>Status</th><th>Campaign</th><th>Why it is here</th></tr></thead>
          <tbody>
            {(data?.items || []).map(r => (
              <tr key={r.id}>
                <td className="pc-small">{r.source_lead_id}</td>
                <td>{r.first_name} {r.last_name}<div className="pc-small pc-muted">{r.email} · {r.phone}</div></td>
                <td>
                  {r.location_status === 'mapped' ? r.location : (
                    isManager ? (
                      <select className="pc-input" aria-label={`Assign a location for ${r.source_lead_id}`} defaultValue="" onChange={e => assign(r, e.target.value)}>
                        <option value="">Assign location…</option>
                        {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                      </select>
                    ) : <span className="pc-badge warn">Location review</span>
                  )}
                  {r.source_location_name && r.location_status !== 'mapped' && <div className="pc-small pc-muted">source: {r.source_location_name}</div>}
                </td>
                <td>{r.source_status}</td>
                <td className="pc-small">{r.campaign_family || '—'}</td>
                <td className="pc-small">
                  {r.data_note_flags.length > 0 && <div><span className="pc-badge warn">SOURCE DATA NOTE DETECTED — REVIEW</span> {r.data_note_flags.join(', ')}</div>}
                  {r.duplicate_review_reason && <div>Duplicate review: {r.duplicate_review_reason}</div>}
                  {r.link_reason && <div>{r.link_reason}</div>}
                  {r.location_status !== 'mapped' && <div>No location resolved - nothing is sent.</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && <p className="pc-empty">Nothing in this queue.</p>}
      </div>
    </section>
  )
}

/* ── locations ──────────────────────────────────────────────────────────── */

const PROFILE_FIELDS = [
  ['official_name', 'Official location name'], ['website', 'Website'], ['facility_phone', 'Facility phone (not shown to families)'],
  ['manager_name', 'Manager'], ['address_line1', 'Address'], ['city', 'City'], ['state', 'State'], ['postal_code', 'ZIP'],
  ['appointment_link', 'Appointment link'], ['email_display_name', 'Email sender name (override)'], ['sms_identity_name', 'Text sign-off name (override)'],
]

function Locations({ isManager, selected, onChange }) {
  const [rows, setRows] = useState(null)
  const [cur, setCur] = useState(null)
  const [form, setForm] = useState({})
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [assets, setAssets] = useState([])
  const load = useCallback(() => {
    api.get('/program/locations').then(d => {
      setRows(d)
      const pick = d.find(p => p.location_id === selected) || d[0]
      if (pick) choose(pick)
    }).catch(e => setErr(errText(e)))
    api.get('/program/assets').then(d => setAssets(d.items || [])).catch(() => {})
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [load])
  function choose(p) {
    setCur(p); setMsg('')
    setForm({
      official_name: p.official_name || '', website: p.website || '', facility_phone: p.facility_phone || '',
      manager_name: p.manager_name || '', address_line1: p.address?.address_line1 || '', city: p.address?.city || '',
      state: p.address?.state || '', postal_code: p.address?.postal_code || '', appointment_link: p.appointment_link || '',
      email_display_name: '', sms_identity_name: '', advisor_names: (p.advisor_names || []).join(', '),
      logo_asset_id: '', hero_asset_id: '',
    })
  }
  const save = async () => {
    setErr(''); setMsg('')
    const body = {}
    Object.entries(form).forEach(([k, v]) => {
      if (k === 'advisor_names') body.advisor_names = v.split(',').map(s => s.trim()).filter(Boolean)
      else if ((k === 'email_display_name' || k === 'sms_identity_name' || k === 'logo_asset_id' || k === 'hero_asset_id') && !v) return
      else body[k] = v
    })
    try { const p = await api.patch(`/program/locations/${cur.id}`, body); choose(p); setMsg('Saved.'); onChange() } catch (e) { setErr(errText(e)) }
  }
  const imgs = assets.filter(a => a.kind !== 'flyer')
  return (
    <section className="pc-card">
      {err && <div className="pc-alert" role="alert">{err}</div>}
      <div className="pc-split">
        <div className="pc-list" aria-label="Locations">
          {(rows || []).map(p => (
            <button key={p.id} type="button" aria-current={cur?.id === p.id} onClick={() => choose(p)}>
              {p.official_name}{p.is_review_bucket && <span className="pc-badge warn" style={{ marginLeft: 6 }}>review</span>}
            </button>
          ))}
        </div>
        {cur && (
          <div>
            <h2 style={{ fontSize: 17, margin: '0 0 4px' }}>{cur.official_name}</h2>
            {cur.is_review_bucket ? <p className="pc-note">Contacts whose location could not be resolved wait here. Nothing in this bucket is ever sent to.</p> : (
              <>
                <p className="pc-small pc-muted">Families see: <strong>{cur.email_display_name}</strong> · texts end “{cur.sms_signoff}”. Source names: {cur.source_names.join(', ') || '—'}</p>
                {msg && <div className="pc-okmsg">{msg}</div>}
                <div className="pc-form">
                  {PROFILE_FIELDS.map(([k, label]) => (
                    <label key={k}>{label}
                      <input className="pc-input" value={form[k] ?? ''} disabled={!isManager}
                        placeholder={k.endsWith('_name') && k !== 'official_name' && k !== 'manager_name' ? 'Leave blank for the default' : ''}
                        onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} />
                    </label>
                  ))}
                  <label className="full">Advisors (comma separated)
                    <input className="pc-input" value={form.advisor_names} disabled={!isManager} onChange={e => setForm(f => ({ ...f, advisor_names: e.target.value }))} />
                  </label>
                  <label>Location logo
                    <select className="pc-input" value={form.logo_asset_id} disabled={!isManager} onChange={e => setForm(f => ({ ...f, logo_asset_id: e.target.value }))}>
                      <option value="">{cur.logo_url ? 'Keep current' : 'None'}</option>
                      {imgs.map(a => <option key={a.id} value={a.id}>{a.title} v{a.version}</option>)}
                    </select>
                  </label>
                  <label>Facility image
                    <select className="pc-input" value={form.hero_asset_id} disabled={!isManager} onChange={e => setForm(f => ({ ...f, hero_asset_id: e.target.value }))}>
                      <option value="">{cur.hero_url ? 'Keep current' : 'None'}</option>
                      {imgs.map(a => <option key={a.id} value={a.id}>{a.title} v{a.version}</option>)}
                    </select>
                  </label>
                </div>
                {isManager && <div className="pc-detail-actions"><button type="button" className="pc-btn primary" onClick={save}>Save location</button></div>}
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

/* ── campaigns ──────────────────────────────────────────────────────────── */

function Campaigns({ isManager, locationId, locations }) {
  const [rows, setRows] = useState(null)
  const [cur, setCur] = useState(null)
  const [loc, setLoc] = useState(locationId || locations.find(l => !l.is_review_bucket)?.location_id || '')
  const [touch, setTouch] = useState('first')
  const [preview, setPreview] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get('/program/campaigns').then(d => { setRows(d); setCur(c => c || d[0] || null) }).catch(e => setErr(errText(e)))
  }, [])
  useEffect(() => {
    if (!cur || !loc) { setPreview(null); return }
    api.get(`/program/campaigns/${cur.id}/preview?location_id=${encodeURIComponent(loc)}&touch=${touch}`)
      .then(p => { setPreview(p); setErr('') }).catch(e => setErr(errText(e)))
  }, [cur, loc, touch])
  const setMode = async (key, value) => {
    try {
      const f = await api.patch(`/program/campaigns/${cur.id}`, { [key]: value })
      setCur(f); setRows(rs => rs.map(r => (r.id === f.id ? f : r)))
    } catch (e) { setErr(errText(e)) }
  }
  return (
    <section className="pc-card">
      {err && <div className="pc-alert" role="alert">{err}</div>}
      <p className="pc-note" style={{ marginTop: 0 }}>Each campaign is written once and filled in per location from the location profile and the program's primary contact. Campaigns are <strong>off</strong> until switched on, and nothing is enrolled from this screen.</p>
      <div className="pc-split">
        <div className="pc-list" aria-label="Campaigns">
          {(rows || []).map(f => (
            <button key={f.id} type="button" aria-current={cur?.id === f.id} onClick={() => setCur(f)}>
              {f.name} <span className={`pc-badge ${f.is_active ? 'ok' : 'low'}`}>{f.is_active ? 'on' : 'off'}</span>
            </button>
          ))}
        </div>
        {cur && (
          <div>
            <div className="pc-filters">
              <label className="pc-small">Preview for location
                <select className="pc-input" value={loc} onChange={e => setLoc(e.target.value)}>
                  {locations.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
                </select>
              </label>
              <label className="pc-small">Touch
                <select className="pc-input" value={touch} onChange={e => setTouch(e.target.value)}>
                  <option value="first">First touch</option><option value="followup">Engaged / follow-up</option>
                </select>
              </label>
              <label className="pc-small">First-touch email
                <select className="pc-input" value={cur.first_touch_email_mode} disabled={!isManager} onChange={e => setMode('first_touch_email_mode', e.target.value)}>
                  <option value="none">No flyer</option><option value="hosted">Hosted / view flyer</option><option value="attached">Attached PDF</option>
                </select>
              </label>
              <label className="pc-small">Follow-up email
                <select className="pc-input" value={cur.followup_email_mode} disabled={!isManager} onChange={e => setMode('followup_email_mode', e.target.value)}>
                  <option value="none">No flyer</option><option value="hosted">Hosted / view flyer</option><option value="attached">Attached PDF</option>
                </select>
              </label>
            </div>
            {isManager && (
              <div className="pc-note" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <span>This campaign is <strong>{cur.is_active ? 'ON' : 'OFF'}</strong>. Switching it on lets automated messages go to contacts already enrolled in it; it enrolls no one by itself.</span>
                <button type="button" className="pc-btn" onClick={async () => {
                  let confirm = null
                  if (!cur.is_active) {
                    confirm = window.prompt(`Type "${cur.name}" to switch this campaign on.`)
                    if (confirm == null) return
                  }
                  try {
                    const f = await api.post(`/program/campaigns/${cur.id}/activation`, { active: !cur.is_active, confirm })
                    setCur(f); setRows(rs => rs.map(r => (r.id === f.id ? f : r)))
                  } catch (e) { setErr(errText(e)) }
                }}>{cur.is_active ? 'Switch off' : 'Switch on…'}</button>
              </div>
            )}
            {preview && !preview.ok && <div className="pc-alert">{preview.reason}</div>}
            {preview?.ok && (
              <>
                <p className="pc-small"><strong>From:</strong> {preview.from_display_name} · <strong>Flyer:</strong> {preview.flyer_available ? (preview.email_mode === 'attached' ? 'attached PDF' : preview.email_mode === 'hosted' ? 'hosted link' : 'not included') : 'none uploaded yet'}</p>
                <h3 className="pc-small" style={{ margin: '10px 0 4px' }}>Text message</h3>
                <div className="pc-pre">{preview.sms}</div>
                <h3 className="pc-small" style={{ margin: '10px 0 4px' }}>Email - {preview.email_subject}</h3>
                <div className="pc-pre">{preview.email_body}</div>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

/* ── assets ─────────────────────────────────────────────────────────────── */

function Assets({ isManager, locations }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ kind: 'flyer', title: '', category: 'veteran_planning_guide', location_id: '', activate: false })
  const [file, setFile] = useState(null)
  const load = () => api.get('/program/assets').then(setData).catch(e => setErr(errText(e)))
  useEffect(() => { load() }, [])
  const upload = async e => {
    e.preventDefault(); setErr(''); setMsg('')
    if (!file || !form.title.trim()) { setErr('Choose a file and give it a title.'); return }
    const fd = new FormData()
    fd.append('file', file); fd.append('kind', form.kind); fd.append('title', form.title.trim())
    if (form.kind === 'flyer' && form.category) fd.append('category', form.category)
    if (form.location_id) fd.append('location_id', form.location_id)
    fd.append('activate', form.activate ? 'true' : 'false')
    setBusy(true)
    try { const a = await api.upload('/program/assets', fd); setMsg(`Uploaded ${a.title} (version ${a.version}).`); setFile(null); load() } catch (er) { setErr(errText(er)) } finally { setBusy(false) }
  }
  const toggle = async a => {
    try { await api.post(`/program/assets/${a.id}/active`, { active: !a.is_active }); load() } catch (e) { setErr(errText(e)) }
  }
  const open = async a => {
    try { const url = await fetchObjectUrl(`/program/assets/${a.id}/preview`); window.open(url, '_blank', 'noopener') } catch (e) { setErr(errText(e)) }
  }
  const cats = data?.categories || {}
  const locName = useMemo(() => Object.fromEntries(locations.map(l => [l.location_id, l.name])), [locations])
  return (
    <section className="pc-card">
      {err && <div className="pc-alert" role="alert">{err}</div>}
      {msg && <div className="pc-okmsg">{msg}</div>}
      <p className="pc-note" style={{ marginTop: 0 }}>Approved flyers drop in here. Each upload is a new version; only the <strong>active</strong> version is used by campaigns and served at its hosted link. Campaigns pick the location's own flyer first, then the shared one for the category.</p>
      {isManager && (
        <form className="pc-form" onSubmit={upload} style={{ marginBottom: 16 }}>
          <label>Type
            <select className="pc-input" value={form.kind} onChange={e => setForm(f => ({ ...f, kind: e.target.value }))}>
              <option value="flyer">Flyer (PDF or image)</option><option value="logo">Logo</option><option value="facility_image">Facility image</option>
            </select>
          </label>
          <label>Title<input className="pc-input" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} placeholder="e.g. Veteran Planning Guide" /></label>
          {form.kind === 'flyer' && (
            <label>Flyer category
              <select className="pc-input" value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
                {Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
          )}
          <label>Location
            <select className="pc-input" value={form.location_id} onChange={e => setForm(f => ({ ...f, location_id: e.target.value }))}>
              <option value="">Every location</option>
              {locations.filter(l => !l.is_review_bucket).map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
            </select>
          </label>
          <label>File (PDF, PNG, JPEG, WebP, SVG · up to 20 MB)
            <input className="pc-input" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.svg" onChange={e => setFile(e.target.files?.[0] || null)} />
          </label>
          <label style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" checked={form.activate} onChange={e => setForm(f => ({ ...f, activate: e.target.checked }))} /> Make this the active version
          </label>
          <div className="full"><button className="pc-btn primary" type="submit" disabled={busy}>{busy ? 'Uploading…' : 'Upload'}</button></div>
        </form>
      )}
      <div className="pc-tablewrap">
        <table className="pc-table">
          <thead><tr><th>Title</th><th>Type</th><th>Category</th><th>Location</th><th>Version</th><th>Status</th><th /></tr></thead>
          <tbody>
            {(data?.items || []).map(a => (
              <tr key={a.id}>
                <td>{a.title}<div className="pc-small pc-muted">{a.filename} · {Math.round(a.size_bytes / 1024)} KB</div></td>
                <td>{a.kind}</td><td>{cats[a.category] || '—'}</td><td>{a.location_id ? locName[a.location_id] : 'Every location'}</td>
                <td>v{a.version}</td>
                <td><span className={`pc-badge ${a.is_active ? 'ok' : 'low'}`}>{a.is_active ? 'active' : 'inactive'}</span></td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button type="button" className="pc-btn" onClick={() => open(a)}>Preview</button>{' '}
                  {isManager && <button type="button" className="pc-btn" onClick={() => toggle(a)}>{a.is_active ? 'Deactivate' : 'Activate'}</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && <p className="pc-empty">No assets yet. Approved flyers will be added here.</p>}
      </div>
    </section>
  )
}

/* ── settings ───────────────────────────────────────────────────────────── */

function Settings({ isManager, program, readiness, onChange }) {
  const [form, setForm] = useState({
    primary_contact_name: program.primary_contact_name || '', primary_contact_title: program.primary_contact_title || '',
    alert_email: program.alert_email || '', alert_phone: program.alert_phone || '',
    hot_sla_minutes: program.hot_sla_minutes, reply_instructions_sms: program.reply_instructions_sms || '',
    reply_instructions_email: program.reply_instructions_email || '',
    managers: (program.management_recipients || []).map(m => [m.name, m.email, m.phone].map(x => x || '').join(' | ')).join('\n'),
  })
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [alerts, setAlerts] = useState([])
  const [file, setFile] = useState(null)
  const [importRes, setImportRes] = useState(null)
  useEffect(() => { if (isManager) api.get('/program/alerts?limit=20').then(setAlerts).catch(() => {}) }, [isManager])
  const save = async () => {
    setErr(''); setMsg('')
    const managers = form.managers.split('\n').map(l => l.split('|').map(s => s.trim())).filter(p => p[0] || p[1] || p[2])
      .map(([name, email, phone]) => ({ name, email, phone, role: 'management' }))
    try {
      await api.patch('/program/settings', {
        primary_contact_name: form.primary_contact_name, primary_contact_title: form.primary_contact_title,
        alert_email: form.alert_email || null, alert_phone: form.alert_phone || null,
        hot_sla_minutes: Number(form.hot_sla_minutes) || 15, reply_instructions_sms: form.reply_instructions_sms,
        reply_instructions_email: form.reply_instructions_email, management_recipients: managers,
      })
      setMsg('Saved.'); onChange()
    } catch (e) { setErr(errText(e)) }
  }
  const runImport = async dry => {
    if (!file) { setErr('Choose the source CSV first.'); return }
    const fd = new FormData(); fd.append('file', file); fd.append('dry_run', dry ? 'true' : 'false')
    try { setImportRes(await api.upload('/program/import', fd)); onChange() } catch (e) { setErr(errText(e)) }
  }
  return (
    <>
      <section className="pc-card">
        <h2>Readiness for production outreach</h2>
        {readiness.items.map(i => (
          <div className="pc-row" key={i.key}>
            <span>{i.key.replace(/_/g, ' ')}</span>
            <span className="pc-small" style={{ textAlign: 'right' }}><span className={`pc-badge ${i.ok ? 'ok' : 'warn'}`}>{i.ok ? 'ready' : 'not ready'}</span> {i.detail}</span>
          </div>
        ))}
      </section>
      <section className="pc-card" style={{ marginTop: 14 }}>
        <h2>Program settings</h2>
        {err && <div className="pc-alert" role="alert">{err}</div>}
        {msg && <div className="pc-okmsg">{msg}</div>}
        <div className="pc-form">
          <label>Primary contact<input className="pc-input" disabled={!isManager} value={form.primary_contact_name} onChange={e => setForm(f => ({ ...f, primary_contact_name: e.target.value }))} /></label>
          <label>Title<input className="pc-input" disabled={!isManager} value={form.primary_contact_title} onChange={e => setForm(f => ({ ...f, primary_contact_title: e.target.value }))} /></label>
          <label>Alert email (blank until supplied)<input className="pc-input" disabled={!isManager} value={form.alert_email} onChange={e => setForm(f => ({ ...f, alert_email: e.target.value }))} /></label>
          <label>Alert phone (blank until supplied)<input className="pc-input" disabled={!isManager} value={form.alert_phone} onChange={e => setForm(f => ({ ...f, alert_phone: e.target.value }))} /></label>
          <label>HOT response SLA (minutes)<input className="pc-input" type="number" min="1" max="1440" disabled={!isManager} value={form.hot_sla_minutes} onChange={e => setForm(f => ({ ...f, hot_sla_minutes: e.target.value }))} /></label>
          <label>Staff text/email alerts<input className="pc-input" disabled value={program.staff_alerts_enabled ? 'On' : 'Off (in-app alerts only)'} /></label>
          <label className="full">Management alert recipients - one per line: name | email | phone
            <textarea disabled={!isManager} value={form.managers} onChange={e => setForm(f => ({ ...f, managers: e.target.value }))} />
          </label>
          <label className="full">Reply instructions in texts<input className="pc-input" disabled={!isManager} value={form.reply_instructions_sms} onChange={e => setForm(f => ({ ...f, reply_instructions_sms: e.target.value }))} /></label>
          <label className="full">Reply instructions in emails<input className="pc-input" disabled={!isManager} value={form.reply_instructions_email} onChange={e => setForm(f => ({ ...f, reply_instructions_email: e.target.value }))} /></label>
        </div>
        {isManager && <div className="pc-detail-actions"><button type="button" className="pc-btn primary" onClick={save}>Save settings</button></div>}
      </section>
      {isManager && (
        <section className="pc-card" style={{ marginTop: 14 }}>
          <h2>Source import (staging only)</h2>
          <p className="pc-small pc-muted">A dry run changes nothing. Staging stores the rows with their decisions - it creates no leads, enrolls no one and sends nothing.</p>
          <input className="pc-input" type="file" accept=".csv" aria-label="Source CSV" onChange={e => setFile(e.target.files?.[0] || null)} />
          <div className="pc-detail-actions">
            <button type="button" className="pc-btn" onClick={() => runImport(true)}>Dry run</button>
            <button type="button" className="pc-btn primary" onClick={() => runImport(false)}>Stage rows</button>
          </div>
          {importRes && <div className="pc-pre" style={{ marginTop: 10 }}>{JSON.stringify(importRes.summary, null, 2)}</div>}
          <h2 style={{ marginTop: 16 }}>Recent alerts</h2>
          {alerts.length === 0 ? <p className="pc-empty">No alerts yet.</p> : alerts.map(a => (
            <div className="pc-row" key={a.id}>
              <span className="pc-small">{a.kind} · {a.audience} · {a.channel}</span>
              <span className="pc-small pc-muted">{a.delivered ? 'delivered' : a.reason} · {when(a.created_at)}</span>
            </div>
          ))}
        </section>
      )}
    </>
  )
}
