/* EvoSys product components — Wholesale + EvoSense (Phase 7.2).
 *
 * Every Wholesale and EvoSense screen is assembled from these, so the two
 * operating worlds read as ONE product:
 *
 *   EvoApp        the workspace: tokens, module bar, environment indicator
 *   PageHead      eyebrow / title / subtitle / actions / meta
 *   Metrics       the metric strip (only numbers the backend actually counted)
 *   Score/Scores  the three EvoSense scores - one component, three accents
 *   Status        one status vocabulary for EvoSense buckets AND deal stages
 *   Chips, Tag    signals and truth labels (SANDBOX, ESTIMATE, LIVE ...)
 *   PropertyThumb a real, provenance-carrying photo of THIS property when one
 *                 exists; otherwise "Property image unavailable" - never
 *                 a stock or representative house
 *   Panel, Empty, Alert, Skeleton, Drawer, Seg, Tabs, Feed, Search
 */
import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { NavLink } from 'react-router-dom'
import { fetchEnvironment } from '../../../api/demo'
import { Scene } from './scenes'
import './evo-ds.css'

// ── the two operating worlds + settings ────────────────────────────────────
export const WORLDS = {
  acquisition: {
    mark: 'E', label: 'EvoSense · Acquisition', cls: '',
    tabs: [
      ['/wholesale/evosense', 'Acquisition Command', true],
      ['/wholesale/evosense/inbox', 'Discovery Inbox'],
      ['/wholesale/evosense/strategies', 'Strategies'],
      ['/wholesale/evosense/controls', 'Providers & Controls'],
    ],
  },
  operations: {
    mark: 'W', label: 'Wholesale Operations', cls: 'ops',
    tabs: [
      ['/wholesale', 'Deal Operations', true],
      ['/wholesale/properties', 'Properties'],
      ['/wholesale/buyers', 'Cash Buyers'],
      ['/wholesale/closing', 'Contracts & Closing'],
      ['/wholesale/dispositions', 'Dispositions'],
    ],
  },
  settings: {
    mark: '⚙', label: 'Settings', cls: 'set',
    tabs: [['/wholesale/settings', 'Wholesale Settings', true]],
  },
}

let _envCache = null
export function useEnvironment() {
  const [env, setEnv] = useState(_envCache)
  useEffect(() => {
    let alive = true
    if (!_envCache) fetchEnvironment().then((d) => { _envCache = d; if (alive) setEnv(d) })
    return () => { alive = false }
  }, [])
  return env
}

/** One quiet, persistent answer to "am I looking at production?" */
export function EnvironmentPill() {
  const env = useEnvironment()
  if (!env || !env.local_review) return null
  return (
    <span className="evo-env" role="status" title="This is your local review environment: a local database with sandbox data. Nothing here is production, and no real message, call or charge can leave this computer.">
      <span className="evo-env__dot" aria-hidden="true" />
      Local review · Sandbox data
    </span>
  )
}

export function EvoApp({ world = 'acquisition', children, side }) {
  // Phase 7.3: the module bar is gone. Navigation between the two worlds is the
  // focused Wholesale sidebar (Layout), and the global search + environment
  // indicator live in the light top bar - exactly where the approved board
  // puts them. `side` still lets a page park a control at the top right.
  //
  // `ws-page es-page`: the module's older component styles are scoped under
  // those classes, so every existing wholesale / evosense component renders
  // inside the new workspace. evo-ds.css re-points their tokens to light.
  return (
    <div className="evo-app ws-page es-page" data-world={world}>
      {side ? <div className="evo-topline"><div className="evo-topline__side">{side}</div></div> : null}
      {children}
    </div>
  )
}

/**
 * The contextual hero every primary page opens with (the board's banner):
 * a page-specific illustrated scene, a readable overlay, eyebrow, serif title,
 * subtitle, optional quote, meta chips, actions and an optional score ring.
 * `meta` is [{ label, tone: 'live'|'paused'|undefined }] or plain nodes.
 */
export function Hero({ scene = 'skyline', sceneDrawn, art, eyebrow, title, sub, quote, meta, actions, score, children, compact, level = 'h1' }) {
  const H = level
  return (
    <header className={`evo-herobox${compact ? ' evo-herobox--compact' : ''}${actions ? ' has-actions' : ''}${score ? ' has-score' : ''}`}>
      <div className="evo-herobox__art">{art || <Scene name={scene} drawn={sceneDrawn} />}</div>
      <div className="evo-herobox__body">
        {eyebrow ? <p className="evo-herobox__eyebrow">{eyebrow}</p> : null}
        <H className="evo-herobox__title">{title}</H>
        {sub ? <p className="evo-herobox__sub">{sub}</p> : null}
        {meta && meta.length ? (
          <div className="evo-herobox__meta">
            {meta.filter(Boolean).map((m, i) => (m && m.label !== undefined
              ? <span key={i} className={`evo-herobox__chip${m.tone ? ' is-' + m.tone : ''}`}>{m.label}</span>
              : <span key={i} className="evo-herobox__chip">{m}</span>))}
          </div>
        ) : null}
        {children}
      </div>
      {quote && !score ? <p className="evo-herobox__quote">&ldquo;{quote}&rdquo;</p> : null}
      {actions ? <div className="evo-herobox__actions">{actions}</div> : null}
      {score ? <div className="evo-herobox__score">{score}</div> : null}
    </header>
  )
}

/** The round score badge the board puts on photos and in tables. */
export function Ring({ kind = 'opportunity', value, size, title }) {
  const empty = value === null || value === undefined
  const low = !empty && value < 45
  const name = SCORE_META[kind] ? SCORE_META[kind].name : 'Score'
  const state = SCORE_META[kind] ? scoreState(kind, value) : ''
  return (
    <span className={`evo-ring evo-ring--${kind}${size ? ' evo-ring--' + size : ''}${empty ? ' is-empty' : ''}${low ? ' is-low' : ''}`}
          role="img" aria-label={`${title || name}: ${empty ? 'not scored' : value + ' out of 100'}${state ? ', ' + state : ''}`}
          title={`${title || name}: ${state}`}>
      {empty ? '—' : value}
    </span>
  )
}

/**
 * The board's image-led property card (Top Opportunities, strategy results,
 * dispositions). Image is a real photo of the property or "Property image
 * unavailable" - never a stand-in house.
 */
export function PropCard({ onOpen, href, src, address, place, score, scoreKind = 'opportunity', status, facts, money: amounts, foot }) {
  const body = (
    <>
      <div className="evo-pcard__media">
        <PropertyThumb src={src} address={address} size="card" />
        {score !== undefined ? <span className="evo-pcard__ring"><Ring kind={scoreKind} value={score} /></span> : null}
        {status ? <span className="evo-pcard__status">{status}</span> : null}
      </div>
      <div className="evo-pcard__body">
        <div className="evo-pcard__addr">{address}</div>
        {place ? <div className="evo-pcard__place">{place}</div> : null}
        {facts && facts.length ? (
          <dl className="evo-pcard__facts">
            {facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v ?? '—'}</dd></div>)}
          </dl>
        ) : null}
        {amounts && amounts.length ? (
          <dl className="evo-pcard__money">
            {amounts.map(([k, v, tone]) => <div key={k}><dt>{k}</dt><dd className={tone ? 'is-' + tone : ''}>{v}</dd></div>)}
          </dl>
        ) : null}
        {foot ? <div className="evo-pcard__foot">{foot}</div> : null}
      </div>
    </>
  )
  if (href) return <NavLink to={href} className="evo-pcard">{body}</NavLink>
  if (onOpen) return <button type="button" className="evo-pcard" onClick={onOpen}>{body}</button>
  return <div className="evo-pcard">{body}</div>
}

/** Board-style underline tabs with an optional action slot on the right. */
export function TabBar({ items, value, onChange, label, actions, controls = true }) {
  return (
    <div className="evo-tabsbar">
      <Tabs items={items.map((it) => ({ ...it, label: it.count !== undefined && it.count !== null
        ? <>{it.label} <span className="evo-seg__n">({it.count})</span></> : it.label }))}
            value={value} onChange={onChange} label={label} controls={controls} />
      {actions ? <div className="evo-tabsbar__actions">{actions}</div> : null}
    </div>
  )
}

export function PageHead({ eyebrow, world, title, sub, actions, meta, children }) {
  const cls = world === 'operations' ? ' evo-head__eyebrow--ops' : world === 'settings' ? ' evo-head__eyebrow--set' : ''
  return (
    <header className="evo-head">
      <div style={{ minWidth: 0 }}>
        {eyebrow ? <p className={`evo-head__eyebrow${cls}`}>{eyebrow}</p> : null}
        <h1 className="evo-head__title">{title}</h1>
        {sub ? <p className="evo-head__sub">{sub}</p> : null}
        {meta ? <div className="evo-head__meta">{meta}</div> : null}
        {children}
      </div>
      {actions ? <div className="evo-head__actions">{actions}</div> : null}
    </header>
  )
}

export function Panel({ title, count, hint, action, children, flush, raised, className = '', id, as: As = 'section', labelledBy }) {
  const hid = useId()
  return (
    <As className={`evo-panel${flush ? ' evo-panel--flush' : ''}${raised ? ' evo-panel--raised' : ''} ${className}`}
        id={id} aria-labelledby={title ? (labelledBy || hid) : undefined}>
      {(title || action || hint) ? (
        <div className="evo-panel__head">
          <h2 className="evo-panel__title" id={labelledBy || hid}>
            {title}
            {count !== undefined && count !== null ? <span className="evo-panel__count">{count}</span> : null}
          </h2>
          {hint ? <span className="evo-panel__hint">{hint}</span> : null}
          {action || null}
        </div>
      ) : null}
      {children}
    </As>
  )
}

// ── metrics ────────────────────────────────────────────────────────────────
export function Metrics({ children, label = 'Key metrics' }) {
  return <div className="evo-metrics" role="group" aria-label={label}>{children}</div>
}
export function Metric({ label, value, sub, tone, attention }) {
  const empty = value === null || value === undefined || value === ''
  return (
    <div className={`evo-metric${tone ? ' is-' + tone : ''}${attention ? ' is-attention' : ''}`}>
      <div className="evo-metric__label"><span className="evo-metric__dot" aria-hidden="true" />{label}</div>
      <div className="evo-metric__value">{empty ? '—' : value}</div>
      {sub ? <div className="evo-metric__sub">{sub}</div> : null}
    </div>
  )
}

// ── the three EvoSense scores ───────────────────────────────────────────────
export const SCORE_META = {
  opportunity: { name: 'Property Opportunity', short: 'Opportunity',
    states: [[85, 'Strong opportunity'], [70, 'Good opportunity'], [50, 'Moderate'], [0, 'Weak']],
    empty: 'Not scored yet' },
  contact: { name: 'Contact Confidence', short: 'Contact',
    states: [[85, 'High confidence'], [65, 'Likely reachable'], [40, 'Uncertain'], [0, 'Low confidence']],
    empty: 'No contact found yet' },
  intent: { name: 'Seller Intent', short: 'Intent',
    states: [[75, 'Ready for human follow-up'], [55, 'Warming up'], [30, 'Early interest'], [1, 'Cool'], [0, 'No intent shown']],
    empty: 'No reply yet' },
}
export function scoreState(kind, value) {
  const m = SCORE_META[kind]
  if (value === null || value === undefined) return m.empty
  for (const [min, label] of m.states) if (value >= min) return label
  return m.states[m.states.length - 1][1]
}

/** One score. `onExplain` makes it a button that opens the factor drilldown. */
export function Score({ kind, value, size, onExplain, expanded }) {
  const m = SCORE_META[kind]
  const empty = value === null || value === undefined
  const body = (
    <>
      <div className="evo-score__value">{empty ? '—' : value}{empty ? null : <span className="evo-score__max">/100</span>}</div>
      <div className="evo-score__name">{m.name}</div>
      <div className="evo-score__state">{scoreState(kind, value)}</div>
      {!empty ? <div className="evo-score__bar" aria-hidden="true"><span style={{ width: `${Math.max(2, Math.min(100, value))}%` }} /></div> : null}
    </>
  )
  const cls = `evo-score evo-score--${kind}${size === 'sm' ? ' evo-score--sm' : ''}${empty ? ' is-empty' : ''}`
  if (onExplain) {
    return (
      <button type="button" className={cls} onClick={onExplain} aria-expanded={!!expanded}
              aria-label={`${m.name} ${empty ? 'not scored' : value + ' out of 100'}: ${scoreState(kind, value)}. Show why.`}>
        {body}
      </button>
    )
  }
  return <div className={cls} role="group" aria-label={`${m.name}: ${empty ? 'not scored' : value + ' out of 100'}, ${scoreState(kind, value)}`}>{body}</div>
}

export function Scores({ opportunity, contact, intent, size }) {
  return (
    <div className="evo-scores">
      <Score kind="opportunity" value={opportunity} size={size} />
      <Score kind="contact" value={contact} size={size} />
      <Score kind="intent" value={intent} size={size} />
    </div>
  )
}

/** The compact number used in tables; the column header names the score. */
export function Num({ kind, value }) {
  const empty = value === null || value === undefined
  const low = !empty && value < 45
  return (
    <span className={`evo-num evo-num--${kind}${empty ? ' is-empty' : ''}${low ? ' is-low' : ''}`}
          title={`${SCORE_META[kind].name}: ${scoreState(kind, value)}`}>
      {empty ? '—' : value}
      <span className="evo-sr"> — {SCORE_META[kind].name}, {scoreState(kind, value)}</span>
    </span>
  )
}

/** A score's WHY: every factor with its points. Deterministic, never mysterious. */
export function ScoreWhy({ score }) {
  if (!score) return <p className="evo-muted" style={{ margin: '12px 0 0' }}>Not scored yet — nothing to explain.</p>
  return (
    <>
      <ul className="evo-why">
        {(score.factors || []).map((f, i) => (
          <li key={i} className={f.points > 0 ? 'is-plus' : f.points < 0 ? 'is-minus' : ''}>
            <span className="evo-why__pts">{f.points > 0 ? `+${f.points}` : f.points}</span>
            <span>{f.label}</span>
          </li>
        ))}
      </ul>
      <div className="evo-why__foot">Deterministic · {score.version}</div>
    </>
  )
}

// ── status: one vocabulary for the whole product ────────────────────────────
const STATUS = {
  // EvoSense buckets
  needs_you: ['needs', 'Needs You'], new: ['quiet', 'New'], high_opportunity: ['info', 'High Opportunity'],
  needs_enrichment: ['attention', 'Needs Approval'], budget_blocked: ['attention', 'Budget Blocked'],
  waiting_for_data: ['attention', 'Waiting for Data'], contact_found: ['info', 'Contact Found'],
  ready_for_outreach: ['good', 'Ready for Outreach'], outreach_active: ['active', 'Outreach Active'],
  responded: ['good', 'Seller Replied'], nurture: ['violet', 'Nurture'], suppressed: ['bad', 'Suppressed'],
  needs_review: ['attention', 'Needs Review'], promoted: ['good', 'In Deal Operations'],
  low_opportunity: ['quiet', 'Below Threshold'], closed_out: ['quiet', 'Closed Out'],
  // Wholesale deal stages
  new_property: ['quiet', 'New Property'], owner_identified: ['info', 'Owner Identified'],
  enrichment_needed: ['attention', 'Enrichment Needed'], seller_engaged: ['good', 'Seller Engaged'],
  qualifying: ['info', 'Qualifying'], qualified: ['good', 'Qualified'], analysis: ['info', 'Analysis'],
  offer_review: ['attention', 'Awaiting Approval'], offer_sent: ['active', 'Offer Sent'],
  negotiating: ['active', 'Negotiating'], under_contract: ['good', 'Under Contract'],
  disposition: ['violet', 'Disposition'], buyer_identified: ['violet', 'Buyer Identified'],
  assignment_pending: ['attention', 'Assignment Pending'], title_closing: ['active', 'Title / Closing'],
  closed: ['good', 'Closed'], dead: ['quiet', 'Dead / Lost'],
  // generic
  active: ['good', 'Active'], paused: ['attention', 'Paused'], archived: ['quiet', 'Archived'],
  draft: ['quiet', 'Draft'], running: ['good', 'Running'], not_configured: ['quiet', 'Not Configured'],
  failed: ['bad', 'Failed'], succeeded: ['good', 'Succeeded'], partial: ['attention', 'Partial'],
  skipped: ['quiet', 'Skipped'], pending: ['attention', 'Pending'], approved: ['good', 'Approved'],
  rejected: ['bad', 'Rejected'], open: ['attention', 'Open'],
}
export function statusLabel(status) {
  return (STATUS[status] || [null, humanize(status)])[1]
}
export function Status({ status, label, tone }) {
  const [t, l] = STATUS[status] || ['quiet', humanize(status)]
  return <span className={`evo-status is-${tone || t}`}>{label || l}</span>
}

export function humanize(s) {
  if (s === null || s === undefined) return ''
  return String(s).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function Chips({ items, max = 4, kind = 'signal' }) {
  if (!items || !items.length) return <span className="evo-muted">—</span>
  const shown = items.slice(0, max)
  return (
    <span className="evo-chips">
      {shown.map((s) => <span key={s} className={`evo-chip evo-chip--${kind}`}>{s}</span>)}
      {items.length > max ? <span className="evo-chip evo-chip--more" title={items.slice(max).join(', ')}>+{items.length - max}</span> : null}
    </span>
  )
}

export function Tag({ kind, children, title }) {
  return <span className={`evo-tag${kind ? ' evo-tag--' + kind : ''}`} title={title}>{children}</span>
}

export function SandboxTag() {
  return <Tag kind="sandbox" title="Created by a sandbox adapter or the review seed. Not live data.">Sandbox</Tag>
}

// ── imagery ────────────────────────────────────────────────────────────────
function hash(s) {
  let h = 0
  for (const c of String(s || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return h
}
/**
 * A real photo of THIS property when one exists (an operator's upload or a
 * verified subject-property image, each carrying its own provenance).
 *
 * Otherwise: "Property image unavailable". Never a stock photo, never a
 * "representative" house - a picture of a different house next to an address
 * reads as that address no matter what the caption says. The unavailable
 * state is plainly not a photograph: a quiet panel with a line icon and the
 * words, so an operator can tell at a glance there is no image, not guess.
 */
export function PropertyThumb({ src, address, size, label, lazy = true, credit }) {
  const [failed, setFailed] = useState(false)
  const cls = `evo-thumb${size ? ' evo-thumb--' + size : ''}`
  if (src && !failed) {
    return (
      <span className={cls}>
        <img src={src} alt={address ? `Photo of ${address}` : 'Property photo'} loading={lazy ? 'lazy' : 'eager'}
             decoding="async" onError={() => setFailed(true)} />
        {label ? <span className="evo-thumb__label">{label}</span> : null}
        {credit ? <span className="evo-thumb__credit">{credit}</span> : null}
      </span>
    )
  }
  const text = label || 'Property image unavailable'
  const showText = size && size !== 'sm'
  return (
    <span className={`${cls} is-unavailable`} role="img"
          aria-label={address ? `${text} for ${address}` : text} title={text}>
      <svg className="evo-thumb__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" fill="none" stroke="currentColor"
              strokeWidth="1.6" strokeLinejoin="round" />
        <path d="M4 4 20 20" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      {showText ? <span className="evo-thumb__none">{text}</span> : null}
    </span>
  )
}

// ── glyphs (line icons for tiles; decorative) ─────────────────────────────
const GLYPHS = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  map: 'M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14',
  building: 'M4 21V5l8-2v18M12 8h8v13M8 8h.01M8 12h.01M8 16h.01M16 12h.01M16 16h.01M2 21h20',
  user: 'M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  key: 'M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zM15.5 7.5l3 3L22 7l-3-3',
  dollar: 'M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  doc: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8',
  alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  chart: 'M3 3v18h18M7 15l4-4 3 3 6-6',
  phone: 'M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.9.6 2.8.7a2 2 0 0 1 1.7 2z',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 6l-10 7L2 6',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4',
  layers: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  spark: 'M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2zM19 3v4M21 5h-4',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-4.35-4.35',
  link: 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
  check: 'M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14l-3-3',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  gavel: 'M14 13l-8.5 8.5a2.1 2.1 0 0 1-3-3L11 10M16 16l6-6M8 8l6-6M9 7l8 8M21 11l-8-8',
  pin: 'M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0 1 18 0zM12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  calendar: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
  message: 'M21 11.5a8.4 8.4 0 0 1-9 8.4 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 12 3a8.5 8.5 0 0 1 9 8.5z',
}
export function Glyph({ name }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={GLYPHS[name] || GLYPHS.layers} /></svg>
  )
}

/** A capability / feature tile (board: provider cards, seller portal list). */
export function Tile({ icon, tone, name, desc, foot, children }) {
  return (
    <div className="evo-tile">
      <span className={`evo-tile__icon${tone ? ' is-' + tone : ''}`}><Glyph name={icon} /></span>
      <div className="evo-tile__name">{name}</div>
      {desc ? <p className="evo-tile__desc">{desc}</p> : null}
      {children}
      {foot ? <div className="evo-tile__foot">{foot}</div> : null}
    </div>
  )
}

// ── states ─────────────────────────────────────────────────────────────────
export function Empty({ title, children, action, page, icon = '◇' }) {
  return (
    <div className={`evo-empty${page ? ' evo-empty--page' : ''}`}>
      <div className="evo-empty__icon" aria-hidden="true">{icon}</div>
      {title ? <p className="evo-empty__title">{title}</p> : null}
      {children ? <p className="evo-empty__body">{children}</p> : null}
      {action || null}
    </div>
  )
}

export function Alert({ kind, children }) {
  if (!children) return null
  return <div className={`evo-alert${kind ? ' evo-alert--' + kind : ''}`} role={kind === 'ok' ? 'status' : 'alert'}>{children}</div>
}

export function Skeleton({ rows = 3, height = 18 }) {
  return (
    <div aria-busy="true" aria-label="Loading" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="evo-skel" style={{ height, width: `${92 - i * 11}%` }} />
      ))}
    </div>
  )
}

export function PageSkeleton() {
  return (
    <div className="evo-stack" aria-busy="true" aria-label="Loading">
      <div className="evo-skel" style={{ height: 34, width: 320 }} />
      <div className="evo-skel" style={{ height: 88 }} />
      <div className="evo-cols"><div className="evo-skel" style={{ height: 320 }} /><div className="evo-skel" style={{ height: 320 }} /></div>
    </div>
  )
}

// ── drawer (portalled; carries the tokens itself) ───────────────────────────
export function Drawer({ open, title, sub, onClose, children, footer, wide }) {
  const ref = useRef(null)
  const last = useRef(null)
  const tid = useId()
  useEffect(() => {
    if (!open) return undefined
    last.current = document.activeElement
    const t = setTimeout(() => {
      const el = ref.current && ref.current.querySelector('input, select, textarea, button:not(.evo-x)')
      if (el) el.focus()
    }, 30)
    function onKey(e) {
      if (e.key === 'Escape') onClose()
      if (e.key === 'Tab' && ref.current) {
        const f = ref.current.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])')
        if (!f.length) return
        const first = f[0], lastEl = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); lastEl.focus() }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      clearTimeout(t)
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
      if (last.current && last.current.focus) last.current.focus()
    }
  }, [open, onClose])
  if (!open) return null
  return createPortal(
    <div className="evo-tokens evo-drawer-root">
      <div className="evo-drawer__scrim" onClick={onClose} aria-hidden="true" />
      <div className={`evo-drawer${wide ? ' evo-drawer--wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={tid} ref={ref}>
        <div className="evo-drawer__head">
          <div>
            <h2 className="evo-drawer__title" id={tid}>{title}</h2>
            {sub ? <p className="evo-drawer__sub">{sub}</p> : null}
          </div>
          <button type="button" className="evo-x" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="evo-drawer__body">{children}</div>
        {footer ? <div className="evo-drawer__foot">{footer}</div> : null}
      </div>
    </div>,
    document.body)
}

// ── controls ───────────────────────────────────────────────────────────────
export function Seg({ items, value, onChange, label }) {
  return (
    <div className="evo-seg" role="group" aria-label={label}>
      {items.map((it) => (
        <button key={it.key} type="button" className="evo-seg__btn" aria-pressed={value === it.key}
                onClick={() => onChange(it.key)}>
          {it.label}{it.count !== undefined ? <span className="evo-seg__n">{it.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export function Tabs({ items, value, onChange, label, controls = true }) {
  return (
    <div className="evo-tabs" role="tablist" aria-label={label}>
      {items.map((it) => (
        <button key={it.key} type="button" role="tab" id={`tab-${it.key}`} aria-selected={value === it.key}
                aria-controls={controls ? `panel-${it.key}` : undefined} tabIndex={value === it.key ? 0 : -1} className="evo-tab"
                onClick={() => onChange(it.key)}
                onKeyDown={(e) => {
                  const i = items.findIndex((x) => x.key === value)
                  if (e.key === 'ArrowRight') { e.preventDefault(); onChange(items[(i + 1) % items.length].key) }
                  if (e.key === 'ArrowLeft') { e.preventDefault(); onChange(items[(i - 1 + items.length) % items.length].key) }
                }}>
          {it.label}
        </button>
      ))}
    </div>
  )
}

export function Search({ value, onChange, placeholder = 'Search', label = 'Search' }) {
  return (
    <label className="evo-search">
      <span className="evo-sr">{label}</span>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" />
      </svg>
      <input className="evo-input" type="search" value={value} placeholder={placeholder}
             onChange={(e) => onChange(e.target.value)} />
    </label>
  )
}

export function Field({ label, hint, children, full }) {
  return (
    <label className={`evo-field${full ? ' is-full' : ''}`}>
      <span className="evo-field__label">{label}</span>
      {children}
      {hint ? <span className="evo-field__hint">{hint}</span> : null}
    </label>
  )
}

export function Feed({ items, empty = 'Nothing yet.' }) {
  if (!items || !items.length) return <p className="evo-muted" style={{ margin: 0 }}>{empty}</p>
  return (
    <ul className="evo-feed">
      {items.map((it, i) => (
        <li key={it.key || i} className="evo-feed__item">
          <span className={`evo-feed__dot is-${it.tone || 'quiet'}`} aria-hidden="true" />
          <div style={{ minWidth: 0 }}>
            <div className="evo-feed__text">{it.text}</div>
            {it.meta ? <div className="evo-feed__meta">{it.meta}</div> : null}
          </div>
        </li>
      ))}
    </ul>
  )
}

// ── formatting ─────────────────────────────────────────────────────────────
export function money(v, blank = '—') {
  if (v === null || v === undefined || v === '') return blank
  const n = Number(v)
  if (Number.isNaN(n)) return blank
  return '$' + Math.round(n).toLocaleString()
}
export function moneyK(v, blank = '—') {
  if (v === null || v === undefined || v === '') return blank
  const n = Number(v)
  if (Number.isNaN(n)) return blank
  if (Math.abs(n) >= 1e6) return '$' + (n / 1e6).toFixed(n % 1e6 === 0 ? 0 : 1) + 'M'
  if (Math.abs(n) >= 1e3) return '$' + Math.round(n / 1e3) + 'K'
  return '$' + Math.round(n)
}
export function cents(v, blank = '—') {
  if (v === null || v === undefined) return blank
  return '$' + (Number(v) / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
export function ago(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const s = Math.round((Date.now() - d.getTime()) / 1000)
  if (s < 0) return 'just now'
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`
  return d.toLocaleDateString()
}
/** A future time, said plainly. Only ever called with a real timestamp. */
export function when(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const now = new Date()
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1)
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (d <= now) return `Due now`
  if (d.toDateString() === now.toDateString()) return `Today · ${time}`
  if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow · ${time}`
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} · ${time}`
}
export function shortDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' })
}
export function errorText(e) {
  if (!e) return null
  if (typeof e === 'string') return e
  return e.detail || e.message || 'Something went wrong.'
}
