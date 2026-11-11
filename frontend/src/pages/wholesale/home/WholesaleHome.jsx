/* Wholesale > My Dashboard (#439). ONE page for every kind of person.
 *
 * The server (GET /wholesale/home) decides which panels, numbers and shortcuts
 * this person gets, from their effective access and record visibility. This
 * page only lays them out with shared pieces - a KPI tile, a work queue, a bar
 * list, a table, a shortcut row. A starter profile changes the order; a
 * permission change adds or removes panels on the next load, with no code. */
import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { EvoApp, Empty, Glyph, Hero, Panel, ago, errorText, humanize, money, when } from '../ds/ds'
import './home.css'

const PANEL_ICON = {
  tasks: 'check', pipeline: 'chart', opportunities: 'layers', appointments: 'calendar', callbacks: 'phone',
  replies: 'message', discovery: 'search', routes: 'map', visits: 'pin', team: 'users', reviews: 'alert',
  activity: 'list', areas: 'pin', buyers: 'users', outreach: 'mail', deal_room: 'building', research: 'shield',
  providers: 'gear',
}
const KPI_ICON = {
  open_deals: 'layers', tasks: 'check', appts: 'calendar', callbacks: 'phone', doors: 'map', visits: 'pin',
  needs_you: 'search', reviews: 'alert', team: 'users', listings: 'home', holds: 'key', buyers: 'users',
  conflicts: 'alert', waiting: 'clock',
}
const ACTION_ICON = {
  discovery: 'search', open_property: 'home', callbacks: 'phone', book: 'calendar', appointments: 'calendar',
  route: 'map', underwrite: 'chart', assign: 'users', board: 'users', deals: 'layers', buyers: 'users',
  buy_boxes: 'layers', deal_room: 'building', outreach: 'mail', dispositions: 'link', closing: 'doc',
  foreclosures: 'gavel', exceptions: 'alert', providers: 'gear',
}
// Wide panels are tables; the rest are cards in the grid above them.
const WIDE = new Set(['opportunities', 'activity', 'research', 'visits', 'team'])

// ── shared pieces ───────────────────────────────────────────────────────────
function Kpi({ k }) {
  const body = (
    <>
      <span className="wh-kpi__icon"><Glyph name={KPI_ICON[k.key] || 'layers'} /></span>
      <span className="wh-kpi__text"><span className="wh-kpi__label">{k.label}</span>
        <span className="wh-kpi__value">{Number(k.value || 0).toLocaleString()}</span></span>
    </>
  )
  return k.to ? <Link className="wh-kpi" to={k.to}>{body}</Link> : <div className="wh-kpi">{body}</div>
}

function Shortcuts({ actions }) {
  // The first six follow the starter's order; the rest wait behind "More" so
  // a wide profile isn't a wall of buttons. Every one opens a screen they may use.
  const [more, setMore] = useState(false)
  if (!actions.length) return null
  return (
    <section className="wh-actions" aria-label="Quick actions">
      <h2 className="wh-actions__title"><Glyph name="spark" />Quick Actions</h2>
      <div className="wh-actions__row">
        {actions.map((a, i) => (
          <Link key={a.key} to={a.to} hidden={i >= 6 && !more}
                className={`evo-btn ${i === 0 ? 'evo-btn--primary' : 'evo-btn--secondary'} wh-action`}>
            <Glyph name={ACTION_ICON[a.key] || 'link'} />{a.label}
          </Link>))}
        {actions.length > 6 ? (
          <button type="button" className="evo-btn evo-btn--ghost wh-action wh-action--more" aria-expanded={more}
                  onClick={() => setMore((v) => !v)}>{more ? 'Fewer shortcuts' : `More shortcuts (${actions.length - 6})`}</button>) : null}
      </div>
    </section>
  )
}

const PRI = { urgent: 'is-danger', high: 'is-danger', normal: 'is-warn', low: 'is-info' }
function Queue({ items }) {
  return (
    <ul className="wh-queue">
      {items.map((t, i) => (
        <li key={i}><Link to={t.to}>{t.title}</Link>
          <span className="wh-queue__meta">{t.due_at ? when(t.due_at) : ''}
            <span className={`wh-pri ${PRI[t.priority] || ''}`}>{humanize(t.priority)}</span></span></li>))}
    </ul>
  )
}

function Bars({ rows, valueKey = 'count', labelKey = 'label' }) {
  const max = Math.max(1, ...rows.map((r) => r[valueKey] || 0))
  return (
    <ul className="wh-bars">
      {rows.map((r, i) => (
        <li key={i}><span className="wh-bars__label">{r[labelKey]}</span>
          <span className="wh-bars__track"><span className="wh-bars__fill" style={{ width: `${Math.round(((r[valueKey] || 0) / max) * 100)}%` }} /></span>
          <span className="wh-bars__n">{(r[valueKey] || 0).toLocaleString()}</span></li>))}
    </ul>
  )
}

function Table({ cols, rows }) {
  return (
    <div className="evo-table-wrap">
      <table className="evo-table wh-table">
        <thead><tr>{cols.map((c) => <th key={c.k}>{c.h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i}>{cols.map((c) => <td key={c.k} data-label={c.h}>{c.r ? c.r(r) : (r[c.k] ?? '—')}</td>)}</tr>))}</tbody>
      </table>
    </div>
  )
}

function Stat({ label, value, to }) {
  const v = <><span className="wh-stat__v">{Number(value || 0).toLocaleString()}</span><span className="wh-stat__l">{label}</span></>
  return to ? <Link className="wh-stat" to={to}>{v}</Link> : <div className="wh-stat">{v}</div>
}

function Note({ children }) {
  return children ? <p className="wh-note">{children}</p> : null
}

const open = (to, label = 'Open') => (to ? <Link className="wh-open" to={to} aria-label={label}><Glyph name="link" /></Link> : null)

// ── one renderer per panel kind, all built from the pieces above ───────────
const RENDER = {
  tasks: (p) => (p.items.length ? <Queue items={p.items} /> : null),
  pipeline: (p) => (p.rows.length ? <Bars rows={p.rows} /> : null),
  callbacks: (p) => (
    <>
      <div className="wh-stats"><Stat label="Due today" value={p.due_today} to={p.to} /><Stat label="Overdue" value={p.overdue} to={p.to} />
        <Stat label="Scheduled" value={p.scheduled} to={p.to} /></div>
      {p.rows.length ? <ul className="wh-list">{p.rows.map((r) => <li key={r.id}><Link to={r.to}>{r.note || 'Seller callback'}</Link><span>{when(r.due_at)}</span></li>)}</ul> : null}
    </>
  ),
  appointments: (p) => (
    <>
      <div className="wh-stats"><Stat label="Next 7 days" value={p.week} to={p.to} /><Stat label="Next 48 hours (to confirm)" value={p.next_48h} to={p.to} /></div>
      {p.rows.length ? <ul className="wh-list">{p.rows.map((r) => <li key={r.id}><Link to={r.to}>{r.address || r.title}</Link><span>{when(r.starts_at)}{r.who ? ` · ${r.who}` : ''}</span></li>)}</ul> : null}
      <Note>{p.note}</Note>
    </>
  ),
  replies: (p) => (p.rows.length ? <ul className="wh-list wh-list--msgs">{p.rows.map((r, i) => (
    <li key={i}><Link to={r.to}><strong>{r.property || 'Property'}</strong><span>{r.text}</span></Link><span>{ago(r.at)}</span></li>))}</ul> : null),
  discovery: (p) => (
    <>
      <div className="wh-stats"><Stat label="Needs you" value={p.counts.needs_you} to={p.to} /><Stat label="High evidence" value={p.counts.high} to={p.to} />
        <Stat label="New this week" value={p.counts.new_7d} to={p.to} /></div>
      {p.rows.length ? <ul className="wh-list">{p.rows.map((r) => <li key={r.id}><Link to={r.to}>{r.address}</Link>
        <span>{r.score !== null && r.score !== undefined ? <span className="wh-score">{r.score}/100</span> : null} {r.signals} signals</span></li>)}</ul> : null}
    </>
  ),
  routes: (p) => (
    <>
      <div className="wh-stats"><Stat label="Doors left" value={p.doors_left} to={p.to} /><Stat label="Routes" value={p.routes.length} to={p.to} /></div>
      {p.stops.length ? <ol className="wh-stops">{p.stops.map((s) => <li key={s.n} className={s.knocked ? 'is-done' : ''}>
        <span className="wh-stops__n">{s.n}</span><span>{s.address}</span><span className="wh-stops__r">{s.result ? humanize(s.result) : ''}</span></li>)}</ol> : null}
    </>
  ),
  reviews: (p) => (p.rows.length ? <ul className="wh-list">{p.rows.map((r) => (
    <li key={r.id}><Link to={r.to}>{humanize(r.kind)}{r.address ? ` · ${r.address}` : ''}</Link><span>{r.amount ? money(r.amount) : ''}</span></li>))}</ul> : null),
  areas: (p) => (
    <>{p.rows.length ? <Bars rows={p.rows} labelKey="city" /> : null}<Note>{p.note}</Note></>
  ),
  buyers: (p) => (
    <>
      <div className="wh-stats"><Stat label="Active buyers" value={p.buyers} to={p.to} />
        {p.buy_boxes !== undefined ? <Stat label="Buy boxes" value={p.buy_boxes} to="/wholesale/buyers/buy-boxes" /> : null}</div>
      {p.rows.length ? <ul className="wh-list">{p.rows.map((r, i) => <li key={i}><Link to={r.to}>{r.buyer}<span>{r.deal}</span></Link>
        {r.score !== null && r.score !== undefined ? <span className="wh-fit">{r.score}% fit</span> : null}</li>)}</ul> : null}
    </>
  ),
  outreach: (p) => (
    <div className="wh-stats">
      {Object.entries(p.sent).map(([ch, n]) => <Stat key={ch} label={`${humanize(ch)} sent`} value={n} to={p.to} />)}
      <Stat label="Replies" value={p.replies} to={p.to} /><Stat label="Offers" value={p.offers} to={p.to} />
    </div>
  ),
  deal_room: (p) => (p.error ? <p className="wt-err">{p.error}</p> : (
    <>
      <ul className="wh-checks">
        <li><span>Deal Room</span><strong>{humanize(p.status || 'not set up')}</strong></li>
        <li><span>Live listings</span><strong>{p.counts.listings_live}</strong></li>
        <li><span>Investors</span><strong>{p.counts.investors}</strong></li>
        <li><span>Exclusive requests waiting</span><strong>{p.counts.holds_pending}</strong></li>
        <li><span>Unread investor messages</span><strong>{p.counts.messages_unread}</strong></li>
        <li><span>Files to review</span><strong>{p.counts.files_to_review}</strong></li>
      </ul>
      <Note>{p.note}</Note>
    </>
  )),
  providers: (p) => (
    <>
      <ul className="wh-checks">{p.rows.map((r) => <li key={r.key}><span>{r.label}</span><strong className="wh-prov">{r.state.split(' - ')[0]}</strong></li>)}</ul>
      <Note>{p.note}</Note>
    </>
  ),
  // wide tables
  opportunities: (p) => (p.rows.length ? <Table rows={p.rows} cols={[
    { k: 'address', h: 'Property', r: (r) => <Link to={r.to} className="wh-strong">{r.address || 'Property'}</Link> },
    ...(p.rows.some((r) => r.owner) ? [{ k: 'owner', h: 'Owner' }] : []),
    { k: 'score', h: 'Distress score', r: (r) => (r.score !== null && r.score !== undefined ? <span className="wh-score">{r.score}/100</span> : '—') },
    { k: 'stage_label', h: 'Stage', r: (r) => <span className="wh-chip">{r.stage_label}</span> },
    { k: 'last_activity', h: 'Last activity', r: (r) => <>{r.last_activity || '—'}<div className="evo-muted evo-small">{ago(r.last_activity_at)}</div></> },
    { k: 'next_step', h: 'Next step', r: (r) => r.next_step || '—' },
    ...(p.rows.some((r) => r.assigned_to) ? [{ k: 'assigned_to', h: 'Assigned' }] : []),
    { k: 'to', h: '', r: (r) => open(r.to, 'Open deal') },
  ]} /> : null),
  activity: (p) => (p.rows.length ? <Table rows={p.rows} cols={[
    { k: 'who', h: 'Team member', r: (r) => <span className="wh-strong">{r.who}</span> },
    { k: 'address', h: 'Property / deal', r: (r) => r.address || '—' },
    { k: 'summary', h: 'Activity' }, { k: 'at', h: 'When', r: (r) => ago(r.at) },
    { k: 'to', h: '', r: (r) => open(r.to, 'Open deal') },
  ]} /> : null),
  research: (p) => (
    <>
      <div className="wh-stats"><Stat label="Conflicting sources" value={p.counts.conflicts} to={p.to} /><Stat label="Waiting for data" value={p.counts.waiting_data} to={p.to} />
        <Stat label="Identity reviews" value={p.counts.identity_reviews} to={p.to} /><Stat label="Needs review" value={p.counts.needs_review} to={p.to} /></div>
      {p.rows.length ? <Table rows={p.rows} cols={[
        { k: 'address', h: 'Property', r: (r) => <Link to={r.to} className="wh-strong">{r.address}</Link> },
        { k: 'county', h: 'County' }, { k: 'issue', h: 'What needs checking' },
        { k: 'confidence', h: 'Data confidence', r: (r) => (r.confidence ? <span className={`wh-conf is-${r.confidence}`}>{humanize(r.confidence)}</span> : '—') },
        { k: 'to', h: '', r: (r) => open(r.to, 'Open record') },
      ]} /> : null}
    </>
  ),
  visits: (p) => (
    <>{p.rows.length ? <Table rows={p.rows} cols={[
      { k: 'address', h: 'Address', r: (r) => <Link to={r.to} className="wh-strong">{r.address || 'Visit'}</Link> },
      { k: 'outcome', h: 'Outcome', r: (r) => <span className="wh-chip">{humanize(r.outcome)}</span> },
      { k: 'at', h: 'Last visit', r: (r) => ago(r.at) }, { k: 'to', h: '', r: (r) => open(r.to, 'Open visit') },
    ]} /> : null}<Note>{p.note}</Note></>
  ),
  team: (p) => (p.rows.length ? <Table rows={p.rows} cols={[
    { k: 'name', h: 'Team member', r: (r) => <><span className="wh-strong">{r.name}</span><div className="evo-muted evo-small">{r.role}</div></> },
    { k: 'properties', h: 'Properties' }, { k: 'deals', h: 'Open deals' }, { k: 'appointments', h: 'Appts this week' },
  ]} /> : null),
}

function PanelView({ p }) {
  const body = p.error ? <p className="wt-err">{p.error}</p> : (RENDER[p.key] ? RENDER[p.key](p) : null)
  const count = p.count !== undefined ? p.count : undefined
  return (
    <Panel title={<><span className="wh-ptitle-icon"><Glyph name={PANEL_ICON[p.key] || 'layers'} /></span>{p.title}</>} count={count}
           action={p.to ? <Link className="evo-link" to={p.to}>View all</Link> : null}
           className={`wh-panel wh-panel--${p.key}${WIDE.has(p.key) ? ' wh-panel--wide' : ''}`}>
      {body || <Empty title={p.empty || 'Nothing here yet.'} />}
    </Panel>
  )
}

export default function WholesaleHome() {
  const [params] = useSearchParams()
  const previewId = params.get('preview')
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const load = useCallback(() => {
    setErr(null)
    api.get('/wholesale/home' + (previewId ? `?preview_profile_id=${encodeURIComponent(previewId)}` : ''))
      .then(setData).catch((e) => setErr(errorText ? errorText(e) : (e.detail || e.message)))
  }, [previewId])
  useEffect(() => { load() }, [load])
  // Permission changes apply on the next load: refresh when the tab comes back.
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [load])

  if (err) return <EvoApp world="operations"><div className="wh"><Empty page title="Your dashboard couldn't load">{err}</Empty></div></EvoApp>
  if (!data) return <EvoApp world="operations"><div className="wh"><p className="evo-muted">Loading your dashboard…</p></div></EvoApp>
  // The server's order is the starter profile's order: the first four cards
  // sit above the shortcuts (as on the approved boards), everything else
  // follows in order - tables full width, cards three across.
  // When a layout leads with a table (Research leads with its queue), it goes first.
  const lead = data.panels.length && WIDE.has(data.panels[0].key) ? data.panels[0] : null
  const firstCards = []
  for (const p of data.panels) { if (!WIDE.has(p.key) && firstCards.length < 4) firstCards.push(p) }
  const top = firstCards
  const rest = data.panels.filter((p) => !top.includes(p) && p !== lead)
  return (
    <EvoApp world="operations">
      <div className="wh" data-focus={data.focus}>
        {data.preview ? (
          <div className="wh-preview" role="status"><strong>Preview:</strong> what the “{data.preview.name}” profile gets - its panels, shortcuts and record
            visibility, filled with records you would see under it. <Link to="/wholesale/team">Back to Team</Link></div>) : null}
        <Hero compact scene="home" eyebrow={data.focus_label}
              title={<>Welcome back, <span className="wh-name">{data.person.first_name}</span></>}
              sub={data.focus_sub}
              meta={[data.person.profile ? { label: data.person.profile } : (data.person.mode === 'admin' ? { label: 'Owner' } : null),
                     { label: data.record_scope_label, tone: 'info' }]} />
        {data.kpis.length ? <div className="wh-kpis">{data.kpis.map((k) => <Kpi key={k.key} k={k} />)}</div> : null}
        {data.actions_first ? <Shortcuts actions={data.actions} /> : null}
        {lead ? <PanelView p={lead} /> : null}
        {top.length ? <div className={`wh-grid wh-grid--${Math.min(top.length, 4)}`}>{top.map((p) => <PanelView key={p.key} p={p} />)}</div> : null}
        {!data.actions_first ? <Shortcuts actions={data.actions} /> : null}
        {rest.length ? <div className="wh-grid wh-grid--3 wh-grid--flow">{rest.map((p) => <PanelView key={p.key} p={p} />)}</div> : null}
        {!data.panels.length ? <Empty page title="Nothing to show yet">Your access doesn't include any Wholesale areas with a dashboard. Ask the workspace owner.</Empty> : null}
      </div>
    </EvoApp>
  )
}
