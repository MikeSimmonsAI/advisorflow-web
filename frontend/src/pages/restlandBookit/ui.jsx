// Restland BookIT — small shared pieces: icons, money/date formatting,
// charts drawn from real data only (nothing is drawn when there is no data).
import React from 'react'

const P = {
  day: 'M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0zM12 7v5l3 2',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  repeat: 'M4 12a8 8 0 0 1 14-5l2 2M20 12a8 8 0 0 1-14 5l-2-2M18 4v5h-5M6 20v-5h5',
  network: 'M12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M5 5m-1.8 0a1.8 1.8 0 1 0 3.6 0a1.8 1.8 0 1 0-3.6 0M19 5m-1.8 0a1.8 1.8 0 1 0 3.6 0a1.8 1.8 0 1 0-3.6 0M5 19m-1.8 0a1.8 1.8 0 1 0 3.6 0a1.8 1.8 0 1 0-3.6 0M19 19m-1.8 0a1.8 1.8 0 1 0 3.6 0a1.8 1.8 0 1 0-3.6 0M6.4 6.4l3.8 3.8M17.6 6.4l-3.8 3.8M6.4 17.6l3.8-3.8M17.6 17.6l-3.8-3.8',
  heart: 'M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z',
  users: 'M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20a6 6 0 0 1 12 0M17 11a3 3 0 1 0 0-6M21 20a6 6 0 0 0-4-5.6',
  dollar: 'M12 3v18M16.5 7.5c0-1.7-2-3-4.5-3s-4.5 1.3-4.5 3 2 2.6 4.5 3 4.5 1.3 4.5 3-2 3-4.5 3-4.5-1.3-4.5-3',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  bell: 'M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 21h4',
  spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  out: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  menu: 'M4 7h16M4 12h16M4 17h16',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
  phone: 'M5 4h3l2 5-2 1a11 11 0 0 0 6 6l1-2 5 2v3a2 2 0 0 1-2 2A17 17 0 0 1 3 6a2 2 0 0 1 2-2z',
  text: 'M4 5h16v11H8l-4 4z',
  mail: 'M3 6h18v12H3zM3 7l9 6 9-6',
  clip: 'M9 4h6v3H9zM7 5H5v16h14V5h-2M8 12h8M8 16h5',
  hourglass: 'M7 3h10M7 21h10M8 3c0 5 8 5 8 9s-8 4-8 9M16 3c0 5-8 5-8 9',
  wrench: 'M14 6a4 4 0 0 0 5 5l-9 9a2 2 0 0 1-3-3l9-9a4 4 0 0 0-2-2z',
  plus: 'M12 5v14M5 12h14',
  x: 'M6 6l12 12M18 6L6 18',
  check: 'M5 12l4 4 10-10',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-2-4-2 .5-1.5-1L15 4h-4l-.5 2.5L9 7.5 7 7 5 11l2 1v0l-2 1 2 4 2-.5 1.5 1L11 20h4l.5-2.5 1.5-1 2 .5 2-4z',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  family: 'M7 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM17 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2 20a5 5 0 0 1 10 0M12 20a5 5 0 0 1 10 0',
  audit: 'M5 3h10l4 4v14H5zM9 12h6M9 16h6M9 8h3',
}

export function Icon({ name, ...rest }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" {...rest}><path d={P[name] || P.spark} /></svg>
  )
}

export const usd = (cents, opts = {}) => {
  if (cents === null || cents === undefined) return 'N/A'
  return (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD',
    maximumFractionDigits: opts.cents ? 2 : 0, minimumFractionDigits: opts.cents ? 2 : 0 })
}
export const pct = v => (v === null || v === undefined ? 'N/A' : Math.round(v * 1000) / 10 + '%')
export const num = v => (v === null || v === undefined ? 'N/A' : Number(v).toLocaleString('en-US'))

export function fmtDate(d, opts) {
  if (!d) return ''
  const s = String(d)
  const dt = s.length <= 10 ? new Date(s + 'T12:00:00') : new Date(s.endsWith('Z') ? s : s)
  if (isNaN(dt)) return s
  return dt.toLocaleDateString('en-US', opts || { weekday: 'short', month: 'short', day: 'numeric' })
}
export function fmtTime(hhmm) {
  if (!hhmm) return ''
  const [h, m] = hhmm.split(':').map(Number)
  return ((h % 12) || 12) + ':' + String(m).padStart(2, '0') + (h < 12 ? ' AM' : ' PM')
}
export function fmtStamp(s) {
  if (!s) return ''
  const dt = new Date(s.endsWith('Z') || s.length > 16 ? s : s + ':00')
  if (isNaN(dt)) return s
  return dt.toLocaleString('en-US', { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// Names the owner chose (Nov 26): the two family networks.
const NAMES = { evo6_block: 'File Review 6 Block', add_lead: 'At-Need 6 Block', evo6: 'File Review 6 Block', evo6_blocks: 'File Review 6 Blocks' }
export const label = s => NAMES[s] || (s || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

export function Kpi({ l, v, s, icon, onClick, tone }) {
  return (
    <div className={'bk-card bk-kpi' + (onClick ? ' click' : '')} onClick={onClick} role={onClick ? 'button' : undefined}>
      <div className="l">{icon && <Icon name={icon} width="15" height="15" />}{l}</div>
      <div className="v" style={tone ? { color: tone } : null}>{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  )
}

export function Empty({ children }) { return <div className="bk-empty">{children}</div> }
export function Skeleton({ rows = 4 }) {
  return <div>{Array.from({ length: rows }).map((_, i) => <div key={i} className="bk-skel" style={{ width: (90 - i * 9) + '%' }} />)}</div>
}

/** A cumulative area chart from real points [{date, cumulative_cents}]. */
export function AreaChart({ points, height = 150 }) {
  if (!points || points.length < 2) return <div className="faint">The trend appears once there are at least two closed sales in this range.</div>
  const W = 600, H = height, pad = 6
  const max = Math.max(...points.map(p => p.cumulative_cents)) || 1
  const xs = i => pad + (i * (W - pad * 2)) / (points.length - 1)
  const ys = v => H - pad - (v / max) * (H - pad * 2)
  const line = points.map((p, i) => (i ? 'L' : 'M') + xs(i).toFixed(1) + ' ' + ys(p.cumulative_cents).toFixed(1)).join(' ')
  const area = line + ` L${xs(points.length - 1)} ${H - pad} L${xs(0)} ${H - pad} Z`
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="bk-net" role="img" aria-label="Cumulative closed contract value">
      <defs><linearGradient id="bkA" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stopColor="#23885E" stopOpacity=".28" /><stop offset="1" stopColor="#23885E" stopOpacity="0" />
      </linearGradient></defs>
      <path d={area} fill="url(#bkA)" />
      <path d={line} fill="none" stroke="#176B4A" strokeWidth="2.2" />
      {points.map((p, i) => <circle key={i} cx={xs(i)} cy={ys(p.cumulative_cents)} r="3" fill="#176B4A"><title>{fmtDate(p.date)}: {usd(p.cumulative_cents)}</title></circle>)}
    </svg>
  )
}

/** Horizontal bars from a {label: cents} map. */
export function Bars({ data, money = true, labels = {} }) {
  const rows = Object.entries(data || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
  if (!rows.length) return <div className="faint">Nothing recorded in this range yet.</div>
  const max = rows[0][1] || 1
  return (
    <div className="bk-bars">{rows.map(([k, v]) => (
      <div className="b" key={k}><span>{labels[k] || label(k)}</span>
        <div className="track"><div className="fill" style={{ width: Math.max(2, (v / max) * 100) + '%' }} /></div>
        <b className="money" style={{ textAlign: 'right' }}>{money ? usd(v) : num(v)}</b></div>
    ))}</div>
  )
}

export function Spark({ values }) {
  if (!values || values.length < 3 || values.every(v => !v)) return null
  const W = 90, H = 24, max = Math.max(...values) || 1
  const d = values.map((v, i) => (i ? 'L' : 'M') + (i * W / (values.length - 1)).toFixed(1) + ' ' + (H - 2 - (v / max) * (H - 4)).toFixed(1)).join(' ')
  return <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden="true"><path d={d} fill="none" stroke="#23885E" strokeWidth="1.6" /></svg>
}

/** The File Review 6 Block radial network: the family in the middle, each contact
 *  around it, colored AND labeled by state (never color alone). */
export function Network({ batch, onPick, onAdd }) {
  const contacts = batch.contacts || []
  const slots = Math.max(6, contacts.length + 1)
  const W = 560, H = 430, cx = W / 2, cy = H / 2, R = 160
  const items = Array.from({ length: slots }).map((_, i) => contacts[i] || null)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="bk-net" role="group" aria-label="Family network">
      {items.map((c, i) => {
        const a = (-Math.PI / 2) + (i * 2 * Math.PI) / slots
        return <line key={'l' + i} className="link" x1={cx} y1={cy} x2={cx + R * Math.cos(a)} y2={cy + R * Math.sin(a)} />
      })}
      <g className="node center"><circle cx={cx} cy={cy} r="46" />
        <text x={cx} y={cy - 4}>{(batch.origin_family || 'Family').slice(0, 18)}</text>
        <text x={cx} y={cy + 12} style={{ fontSize: 10, fontWeight: 400 }}>{batch.label}</text></g>
      {items.map((c, i) => {
        const a = (-Math.PI / 2) + (i * 2 * Math.PI) / slots
        const x = cx + R * Math.cos(a), y = cy + R * Math.sin(a)
        const color = c ? c.status_color : 'gray'
        const state = !c ? 'Add contact' : c.suppressed ? 'Suppressed' : c.validation_status === 'duplicate' ? 'Already on file'
          : c.enrollment_state !== 'not_enrolled' ? label(c.enrollment_state) : c.eligible ? 'Eligible' : label(c.validation_status)
        return (
          <g key={'n' + i} className={'node ' + color} tabIndex="0" role="button"
             aria-label={c ? `${c.first_name || ''} ${c.last_name || ''}, ${c.relationship || ''}, ${state}` : 'Add a contact'}
             onClick={() => (c ? onPick(c) : onAdd && onAdd())} onKeyDown={e => { if (e.key === 'Enter') (c ? onPick(c) : onAdd && onAdd()) }}>
            <circle cx={x} cy={y} r="30" />
            <text x={x} y={y - 2}>{c ? ((c.first_name || '') + ' ' + (c.last_name || '').slice(0, 1)).trim().slice(0, 12) : '+'}</text>
            <text className="sub" x={x} y={y + 12}>{c ? (c.relationship || '').slice(0, 12) : ''}</text>
            <text className="sub" x={x} y={y + 46}>{state}</text>
          </g>
        )
      })}
    </svg>
  )
}

export function Toast({ toast }) {
  if (!toast) return null
  return <div className={'bk-toast' + (toast.err ? ' err' : '')} role="status">{toast.text}</div>
}

export function newKey() {
  return (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID()
    : 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2)
}
