/**
 * GOD MODE CARD KIT — the shared building blocks of the redesigned God Mode.
 *
 * KPI cards, status pills, action queues, health rows, project and
 * organization cards, expandable panels, and the three states every data card
 * needs (loading, empty, error). God Mode tokens only, no third-party library.
 *
 * Rule for every component here: nothing renders a number or a status it was
 * not given. A missing value shows as "—" or an explicit empty/unknown state,
 * never as a zero or a guess.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import './godKit.css'

export function Page ({ eyebrow, title, sub, actions, children, hero }) {
  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <div className="gk-page">
        <div className="gk-head">
          <div>
            {eyebrow ? <div className={`gk-eyebrow ${hero ? 'gk-eyebrow--god' : ''}`}>{eyebrow}</div> : null}
            <h1 className={`gk-title ${hero ? 'gk-title--xl' : ''}`}>{title}</h1>
            {sub ? <p className="gk-sub">{sub}</p> : null}
          </div>
          {actions ? <div className="gk-actions">{actions}</div> : null}
        </div>
        {children}
      </div>
    </div>
  )
}

export function Card ({ title, note, action, children, style, className = '' }) {
  return (
    <section className={`gk-card ${className}`} style={style} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || action) && (
        <div className="gk-card-head">
          <div>
            {title ? <h2 className="gk-card-title">{title}</h2> : null}
            {note ? <div className="gk-card-note">{note}</div> : null}
          </div>
          {action || null}
        </div>
      )}
      {children}
    </section>
  )
}

export function Kpi ({ label, value, sub, onClick, tone, icon, color }) {
  const wordy = typeof value === 'string' && !/^[\d$,.\s/+%-]+$/.test(value)
  const val = (
    <span className={`gk-kpi-value ${wordy ? 'gk-kpi-value--word' : ''}`} style={tone ? { color: `var(--gm-${tone})` } : undefined}>
      {value === null || value === undefined || value === '' ? '—' : value}
    </span>
  )
  const body = icon ? (
    <>
      <IconTile name={icon} color={color} />
      <span className="gk-kpi-text">
        <span className="gk-kpi-label gk-kpi-label--plain">{label}</span>
        {val}
        {sub ? <span className="gk-kpi-sub">{sub}</span> : null}
      </span>
    </>
  ) : (
    <>
      <span className="gk-kpi-label">{label}</span>
      {val}
      {sub ? <span className="gk-kpi-sub">{sub}</span> : null}
    </>
  )
  const cls = `gk-card gk-kpi ${icon ? 'gk-kpi--icon' : ''}`
  return onClick
    ? <button type="button" className={cls} onClick={onClick}>{body}</button>
    : <div className={cls}>{body}</div>
}

const PILL = {
  good: 'good', ok: 'good', healthy: 'good', green: 'good', yes: 'good',
  bad: 'bad', critical: 'bad', red: 'bad', error: 'bad', failing: 'bad',
  warn: 'warn', warning: 'warn', amber: 'warn', degraded: 'warn', high: 'warn',
  info: 'info', blue: 'info', test: 'test', practice: 'test',
}

export function Pill ({ tone = 'off', children, title }) {
  const t = PILL[tone] || (['good', 'bad', 'warn', 'info', 'test'].includes(tone) ? tone : '')
  return <span className={`gk-pill ${t ? 'gk-pill--' + t : ''}`} title={title}>{children}</span>
}

export function Loading ({ children = 'Loading…' }) {
  return <div className="gk-loading" role="status" aria-live="polite">{children}</div>
}

export function Empty ({ children }) {
  return <div className="gk-empty">{children}</div>
}

export function ErrorState ({ children, onRetry }) {
  return (
    <div className="gk-error" role="alert">
      {children}{' '}
      {onRetry ? <button type="button" className="gk-link" onClick={onRetry}>Try again</button> : null}
    </div>
  )
}

/** Where a row's action goes: an internal route, an external URL, or a handler. */
export function ActionLink ({ to, href, onClick, children, primary, small = true, danger, disabled }) {
  const cls = `gk-btn ${small ? 'gk-btn--sm' : ''} ${primary ? 'gk-btn--primary' : ''} ${danger ? 'gk-btn--danger' : ''}`
  if (onClick || disabled) {
    return <button type="button" className={cls} onClick={onClick} disabled={disabled}>{children}</button>
  }
  if (href) return <a className={cls} href={href} target="_blank" rel="noopener noreferrer">{children}</a>
  if (to) return <Link className={cls} to={to}>{children}</Link>
  return null
}

function ago (iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const m = Math.round((Date.now() - t) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} hr ago`
  return `${Math.round(h / 24)} days ago`
}
export { ago }

/** A ranked list of things that need the owner, each with one action. */
export function ActionQueue ({ items, limit, onAction, empty = 'Nothing needs you right now.', compact }) {
  const shown = limit ? items.slice(0, limit) : items
  if (!shown.length) return <Empty>{empty}</Empty>
  if (compact) {
    return (
      <ul className="gk-list gk-attn">
        {shown.map(x => {
          const href = x.to && !x.to.startsWith('#') && !x.org ? x.to : undefined
          return (
            <li key={x.id} className="gk-attn-row">
              <span className={`gk-sev gk-sev--${x.sev || 'info'}`} aria-label={`Severity ${x.sev || 'info'}`} />
              <div className="gk-row-main">
                <div className="gk-attn-title">{x.title}</div>
                {x.detail ? <div className="gk-attn-sub" title={x.detail}>{x.detail}</div> : null}
              </div>
              {href
                ? <Link className="gk-viewlink" to={href} title={x.action || 'View'}>View <Icon name="arrowRight" size={14} /></Link>
                : <button type="button" className="gk-viewlink" title={x.action || 'View'}
                    onClick={onAction ? () => onAction(x) : undefined}>View <Icon name="arrowRight" size={14} /></button>}
            </li>
          )
        })}
      </ul>
    )
  }
  return (
    <ul className="gk-list">
      {shown.map(x => (
        <li key={x.id} className="gk-row">
          <span className={`gk-sev gk-sev--${x.sev || 'info'}`} aria-label={`Severity ${x.sev || 'info'}`} />
          <div className="gk-row-main">
            <div className="gk-row-title">{x.title}</div>
            {x.detail ? <div className="gk-row-sub">{x.detail}</div> : null}
          </div>
          {x.org_name ? <span className="gk-row-meta" title="Organization">{x.org_name}</span> : null}
          {x.at || x.age ? <span className="gk-row-meta">{x.at ? ago(x.at) : x.age}</span> : null}
          {x.action ? (
            <ActionLink to={x.to && !x.to.startsWith('#') ? x.to : undefined}
              onClick={onAction && (!x.to || x.to.startsWith('#') || x.org) ? () => onAction(x) : undefined}>
              {x.action}
            </ActionLink>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

const HEALTH_TONE = { ok: 'good', healthy: 'good', green: 'good', good: 'good',
  warning: 'warn', degraded: 'warn', amber: 'warn', attention: 'warn',
  critical: 'bad', red: 'bad', down: 'bad', error: 'bad',
  warn: 'warn', bad: 'bad', off: 'off', no_source: 'off',
  unknown: 'off', not_configured: 'off', idle: 'off' }

export function HealthRow ({ label, status, statusLabel, headline, to }) {
  return (
    <li className="gk-row">
      <div className="gk-row-main">
        <div className="gk-row-title">{label}</div>
        {headline ? <div className="gk-row-sub">{headline}</div> : null}
      </div>
      <Pill tone={HEALTH_TONE[(status || '').toLowerCase()] || 'off'}>{statusLabel || status || 'Unknown'}</Pill>
      {to ? <ActionLink to={to}>Open</ActionLink> : null}
    </li>
  )
}

/** A card whose body opens and closes. Keyboard and screen-reader friendly. */
export function Expandable ({ header, children, defaultOpen = false, id }) {
  const [open, setOpen] = useState(defaultOpen)
  const bodyId = (id || Math.random().toString(36).slice(2)) + '-body'
  return (
    <div className="gk-card">
      <button type="button" className="gk-expand" aria-expanded={open} aria-controls={bodyId}
        onClick={() => setOpen(o => !o)}>
        <svg className={`gk-chev ${open ? 'gk-chev--open' : ''}`} width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M9 18l6-6-6-6" /></svg>
        <div style={{ flex: 1, minWidth: 0 }}>{header}</div>
      </button>
      {open ? <div id={bodyId} className="gk-panel-body">{children}</div> : null}
    </div>
  )
}

/** A tiny bar chart from real daily counts. Renders nothing it was not given. */
export function Spark ({ values = [], label, color = 'var(--gm-blue)' }) {
  const max = Math.max(1, ...values)
  const w = 100 / Math.max(1, values.length)
  return (
    <svg className="gk-spark" viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label={label}>
      {values.map((v, i) => {
        const h = (v / max) * 36
        return <rect key={i} x={i * w + w * 0.15} width={w * 0.7} y={40 - h} height={Math.max(h, v ? 1.5 : 0.4)}
          rx="0.6" fill={v ? color : 'var(--gm-card-line)'} />
      })}
    </svg>
  )
}

/** The delivery ladder for a project: each stage is yes, no or unknown. */
export function Steps ({ steps }) {
  return (
    <div className="gk-steps">
      {steps.map(s => (
        <span key={s.key} className={`gk-step gk-step--${s.state}`}
          title={s.state === 'unknown' ? 'Unable to verify automatically' : undefined}>
          {s.state === 'yes' ? '✓ ' : s.state === 'unknown' ? '? ' : '· '}{s.label}
        </span>
      ))}
    </div>
  )
}

/* ── OWNER DESK building blocks (Oct 26 2026) ─────────────────────────────
   Used by the Owner Desk and the six section landing pages. Same rule as the
   rest of the kit: nothing here invents a number or a status. */

const ICON_PATHS = {
  building: 'M3 21h18M5 21V7l7-4 7 4v14M9 9h1M14 9h1M9 13h1M14 13h1M9 17h1M14 17h1',
  cpu: 'M9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2M7 7h10v10H7zM10 10h4v4h-4z',
  flag: 'M5 21V4M5 4h11l-2 4 2 4H5',
  dollar: 'M12 3v18M16.5 7.5c0-1.9-2-3-4.5-3s-4.5 1.2-4.5 3 2 2.6 4.5 3.1 4.5 1.3 4.5 3.3-2 3.1-4.5 3.1-4.5-1.2-4.5-3',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z',
  briefcase: 'M3 8h18v12H3zM8 8V5h8v3M3 13h18',
  plus: 'M12 5v14M5 12h14',
  login: 'M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  stop: 'M8 3h8l5 5v8l-5 5H8l-5-5V8zM9 9l6 6M15 9l-6 6',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  shield: 'M12 3l8 3v6c0 4.5-3.3 7.6-8 9-4.7-1.4-8-4.5-8-9V6z',
  arrow: 'M9 6l6 6-6 6',
  check: 'M5 12l5 5 9-10',
  arrowRight: 'M5 12h14M13 6l6 6-6 6',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  pulse: 'M3 12h4l3-7 4 14 3-7h4',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21a7 7 0 0 1 14 0M17 3.5a4 4 0 0 1 0 7.5M22 21a7 7 0 0 0-4-6.3',
  rocket: 'M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2M14 4c3-1 6-1 6-1s0 3-1 6l-6 6-5-5zM9 10l-4 1-2 3 4 1M14 15l-1 4-3 2-1-4M15 9h.01',
  plug: 'M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5',
  file: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h6M9 17h6',
  bolt: 'M13 2 4 14h7l-1 8 9-12h-7z',
  server: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01',
  alert: 'M12 3 2 20h20zM12 10v4M12 17h.01',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  crown: 'M3 8l4 4 5-7 5 7 4-4-2 11H5z',
  heart: 'M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z',
  home: 'M3 11l9-7 9 7M5 10v10h14V10M10 20v-6h4v6',
  exit: 'M10 17l-5-5 5-5M5 12h12M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
}

export function Icon ({ name, size = 18 }) {
  const d = ICON_PATHS[name]
  if (!d) return null
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={d} /></svg>
  )
}

/** A solid colour square with a white glyph — the mockup's icon tile. */
export function IconTile ({ name, color = 'blue', size = 'md' }) {
  return (
    <span className={`gk-ictile gk-ictile--${size}`} aria-hidden="true"
      style={{ '--ic': `var(--gm-ic-${color})` }}>
      <Icon name={name} size={size === 'lg' ? 24 : size === 'sm' ? 15 : 20} />
    </span>
  )
}

/** A titled group on a page — a heading, not another box around boxes. */
export function Section ({ title, sub, action, children, id, icon }) {
  return (
    <section className="gk-section" id={id} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || action) && (
        <div className="gk-section-head">
          <div>
            {title ? <h2 className="gk-section-title">
              {icon ? <span className="gk-section-icon"><Icon name={icon} size={18} /></span> : null}
              {title}
              {sub && icon ? <span className="gk-section-inline">{sub}</span> : null}
            </h2> : null}
            {sub && !icon ? <p className="gk-section-sub">{sub}</p> : null}
          </div>
          {action || null}
        </div>
      )}
      {children}
    </section>
  )
}

/** A compact row of summary numbers (2–4). Same "—" rule as Kpi. */
export function Strip ({ items, cols }) {
  return (
    <div className={`gk-strip ${cols ? `gk-strip--${cols}` : ''}`}>
      {items.map(k => <Kpi key={k.label} {...k} />)}
    </div>
  )
}

/** A destination card: icon, what it is for, and a live status if one is known.
 *  With `cta`, the mockup's tile: icon square, name, one line, then a tinted
 *  "Open …" button across the bottom. */
export function ToolCard ({ icon, title, description, status, meta, to, color, cta }) {
  if (cta) {
    return (
      <Link to={to} className="gk-card gk-tool gk-tool--tile" style={{ '--ic': `var(--gm-ic-${color || 'blue'})`, '--ic-wash': `var(--gm-ic-${color || 'blue'}-wash)` }}>
        <span className="gk-tool-row">
          <IconTile name={icon} color={color || 'blue'} />
          <span className="gk-tool-body">
            <span className="gk-tool-title" style={{ display: 'block' }}>{title}</span>
            {description ? <span className="gk-tool-desc" style={{ display: 'block' }}>{description}</span> : null}
          </span>
        </span>
        {status ? <span className="gk-tool-foot" style={{ marginTop: 0 }}><Pill tone={status.tone}>{status.label}</Pill></span> : null}
        <span className="gk-tool-cta"><span>{cta}</span><Icon name="arrowRight" size={15} /></span>
      </Link>
    )
  }
  return (
    <Link to={to} className="gk-card gk-tool">
      {color ? <IconTile name={icon} color={color} /> : <span className="gk-tool-icon"><Icon name={icon} /></span>}
      <span className="gk-tool-body">
        <span className="gk-tool-title" style={{ display: 'block' }}>{title}</span>
        {description ? <span className="gk-tool-desc" style={{ display: 'block' }}>{description}</span> : null}
        {(status || meta) ? (
          <span className="gk-tool-foot">
            {status ? <Pill tone={status.tone}>{status.label}</Pill> : null}
            {meta ? <span className="gk-tool-meta">{meta}</span> : null}
          </span>
        ) : null}
      </span>
      <span className="gk-tool-arrow"><Icon name="arrow" size={16} /></span>
    </Link>
  )
}

/** Which business an organization IS, from its own industry. Never from a
 *  feature switch (see app/services/program_experience.py). */
const BUSINESS = {
  energy: { kind: 'Energy', acc: 'energy', icon: 'bolt' },
  roofing: { kind: 'Roofing', acc: 'roofing', icon: 'shield' },
  funeral: { kind: 'Cemetery & funeral', acc: 'funeral', icon: 'home' },
  cemetery: { kind: 'Cemetery & funeral', acc: 'funeral', icon: 'home' },
  insurance: { kind: 'Insurance', acc: 'insurance', icon: 'shield' },
  cleaning: { kind: 'Commercial cleaning', acc: 'cleaning', icon: 'building' },
  real_estate: { kind: 'Real estate', acc: 'wholesale', icon: 'home' },
}
export function businessOf (industry) {
  return BUSINESS[String(industry || '').trim().toLowerCase()] || { kind: 'Business', acc: 'general', icon: 'briefcase' }
}
export function initialsOf (name) {
  const w = String(name || '').replace(/\(.*?\)/g, '').trim().split(/\s+/).filter(Boolean)
  if (!w.length) return '?'
  return (w[0][0] + (w.length > 1 ? w[w.length - 1][0] : '')).toUpperCase()
}

/** One organization the owner runs, with its own accent. */
export function ProgramCard ({ name, industry, kind, pills, facts, actions }) {
  const b = businessOf(industry)
  const style = { '--acc': `var(--gm-acc-${b.acc})`, '--acc-wash': `var(--gm-acc-${b.acc}-wash)` }
  return (
    <article className="gk-card gk-program" style={style} aria-label={name}>
      <div className="gk-program-top" />
      <div className="gk-program-body">
        <div className="gk-program-head">
          <span className="gk-program-mark" aria-hidden="true">{initialsOf(name)}</span>
          <div style={{ minWidth: 0 }}>
            <div className="gk-program-name">{name}</div>
            <div className="gk-program-kind">{kind || b.kind}</div>
          </div>
        </div>
        {pills && pills.length ? <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{pills}</div> : null}
        {facts && facts.length ? (
          <div className="gk-program-facts">
            {facts.map(f => <span key={f.label}><b>{f.value === null || f.value === undefined ? '—' : f.value}</b> {f.label}</span>)}
          </div>
        ) : null}
        {actions ? <div className="gk-program-actions">{actions}</div> : null}
      </div>
    </article>
  )
}

/** The mockup's compact program tile: colour square, name, one line of
 *  real counts, a status pill and a chevron. The whole tile opens the
 *  organization; `extra` (e.g. the audited Enter button) sits above the
 *  stretched link so it stays its own control. */
export function ProgramTile ({ name, industry, line, pill, to, extra }) {
  const b = businessOf(industry)
  const style = { '--acc': `var(--gm-acc-${b.acc})`, '--acc-wash': `var(--gm-acc-${b.acc}-wash)` }
  return (
    <article className="gk-card gk-ptile" style={style} aria-label={name}>
      <span className="gk-ptile-mark" aria-hidden="true"><Icon name={b.icon} size={22} /></span>
      <div className="gk-ptile-body">
        <Link to={to} className="gk-ptile-name gk-stretch">{name}</Link>
        <div className="gk-ptile-line">{line || b.kind}</div>
        <div className="gk-ptile-foot">{pill}{extra}</div>
      </div>
      <span className="gk-ptile-chev" aria-hidden="true"><Icon name="arrow" size={16} /></span>
    </article>
  )
}

/** Buttons for the owner's most common moves. */
export function QuickActions ({ items }) {
  return (
    <div className="gk-quick">
      {items.map(q => (
        <ActionLinkBig key={q.label} {...q} />
      ))}
    </div>
  )
}
function ActionLinkBig ({ label, icon, to, onClick, primary, danger, color }) {
  const cls = `gk-btn gk-qbtn ${primary ? 'gk-btn--primary' : ''} ${danger ? 'gk-btn--danger' : ''}`
  const inner = <>
    {icon ? <span className="gk-qbtn-ic" style={color ? { color: `var(--gm-ic-${color})` } : undefined}><Icon name={icon} size={18} /></span> : null}
    <span className="gk-qbtn-label">{label}</span>
    <span className="gk-qbtn-chev"><Icon name="arrow" size={14} /></span>
  </>
  if (onClick) return <button type="button" className={cls} onClick={onClick}>{inner}</button>
  return <Link className={cls} to={to}>{inner}</Link>
}

const STRIP_TONE = { ok: 'good', good: 'good', healthy: 'good', warn: 'warn', warning: 'warn', degraded: 'warn',
  bad: 'bad', critical: 'bad', red: 'bad', off: 'off', unknown: 'off', no_source: 'off', not_configured: 'off' }
/** One line of service chips: dot, name, plain status. Each opens its page. */
export function StatusStrip ({ title, sub, items }) {
  return (
    <div className="gk-card gk-sstrip">
      <div className="gk-sstrip-head">
        <IconTile name="server" color="slate" size="lg" />
        <div><div className="gk-card-title">{title}</div>{sub ? <div className="gk-card-note">{sub}</div> : null}</div>
      </div>
      <div className="gk-sstrip-items">
        {items.map(r => {
          const tone = STRIP_TONE[String(r.status || '').toLowerCase()] || 'off'
          const body = <>
            <span className={`gk-sdot gk-sdot--${tone}`} aria-hidden="true" />
            <span style={{ minWidth: 0 }}>
              <span className="gk-schip-name">{r.label}</span>
              <span className={`gk-schip-state gk-schip-state--${tone}`}>{r.statusLabel || r.status || 'Unknown'}</span>
            </span>
          </>
          return r.to
            ? <Link key={r.key} to={r.to} className="gk-schip" title={r.headline || undefined}>{body}</Link>
            : <div key={r.key} className="gk-schip" title={r.headline || undefined}>{body}</div>
        })}
      </div>
    </div>
  )
}
