/** Small shared pieces for the insurance retention workspace (Issue #499). */
import { useEffect, useRef, useState } from 'react'

const PATHS = {
  home: 'M3 11l9-7 9 7M5 10v10h5v-6h4v6h5V10',
  calendar: 'M5 5h14v15H5zM5 9h14M9 3v4M15 3v4',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21c0-4 3-6 7-6s7 2 7 6M17 11a3 3 0 1 0 0-6M22 20c0-3-2-5-5-5',
  chat: 'M4 5h16v11H9l-5 4z',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  pin: 'M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  chart: 'M4 20V10M10 20V4M16 20v-8M22 20H2',
  spark: 'M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z',
  upload: 'M12 16V4M7 9l5-5 5 5M4 16v4h16v-4',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  menu: 'M4 6h16M4 12h16M4 18h16',
  alert: 'M12 3l10 18H2zM12 10v4M12 17.5v.5',
  check: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12l3 3 5-6',
  calcheck: 'M5 5h14v15H5zM5 9h14M9 3v4M15 3v4M9 14l2 2 4-4',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0',
  msg: 'M4 5h16v11H9l-5 4zM8 9h8M8 12h5',
  x: 'M6 6l12 12M18 6L6 18',
  car: 'M5 16h14M6 16l1.5-5h9L18 16M7 16v2M17 16v2M8 13.5h.01M16 13.5h.01',
  house: 'M4 11l8-6 8 6v9H4zM10 20v-5h4v5',
  shield: 'M12 3l8 3v6c0 4.5-3.3 7.6-8 9-4.7-1.4-8-4.5-8-9V6z',
  pause: 'M8 5v14M16 5v14',
  play: 'M7 4l13 8-13 8z',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM16 16l5 5',
  user: 'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 3.6-6 8-6s8 2 8 6',
}

export function Icon({ name, size = 18, className = '' }) {
  return (
    <svg className={`ins-icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={PATHS[name] || PATHS.spark} />
    </svg>
  )
}

export function productIcon(p) {
  return { auto: 'car', home: 'house', condo: 'house', renters: 'house', landlord: 'house' }[p] || 'shield'
}

export function errText(e) {
  if (!e) return ''
  return e.message || String(e)
}

export function initials(name) {
  return String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(s => s[0]).join('').toUpperCase()
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export function fmtDate(iso) {
  if (!iso) return '—'
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number)
  if (!y) return '—'
  return `${MONTHS[m - 1]} ${d}`
}
export function fmtDateTime(iso, tz) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', { timeZone: tz || 'America/Chicago', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  } catch { return iso }
}
export function fmtTime(iso, tz) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString('en-US', { timeZone: tz || 'America/Chicago', hour: 'numeric', minute: '2-digit' })
  } catch { return iso }
}
export function agoText(iso) {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 0) return 'scheduled'
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

const ATT_TONE = {
  reply_premium: 'red', reply_cancel: 'red', outreach_failed: 'red', reply_question: 'amber', reply_review: 'blue',
  unassigned: 'amber', follow_up_overdue: 'purple', review_not_booked: 'amber', annual_review_overdue: 'purple',
}
export function StatusPill({ row }) {
  if (row.outcome) return <span className={`ins-pill ins-pill--${row.outcome === 'confirmed' ? 'green' : 'grey'}`}>{row.outcome === 'confirmed' ? 'Renewal confirmed' : 'Lost'}</span>
  if (row.attention) return <span className={`ins-pill ins-pill--${ATT_TONE[row.attention.code] || 'amber'}`}>{shortReason(row.attention.code)}</span>
  if (row.review && ['booked', 'confirmed', 'pending'].includes(row.review.status)) return <span className="ins-pill ins-pill--green">Review booked</span>
  const map = { in_outreach: ['blue', 'Awaiting reply'], not_started: ['grey', 'Not started'], reviewed: ['green', 'Reviewed'], closed: ['grey', 'Closed'] }
  const [t, l] = map[row.workflow_status] || ['grey', row.workflow_status]
  return <span className={`ins-pill ins-pill--${t}`}>{l}</span>
}
export function shortReason(code) {
  return {
    reply_premium: 'Premium concern', reply_cancel: 'Cancellation risk', reply_question: 'Reply needs a person',
    reply_review: 'Wants a review', outreach_failed: 'Outreach failed', unassigned: 'Unassigned',
    follow_up_overdue: 'Follow-up overdue', review_not_booked: 'Review needed', annual_review_overdue: 'Annual review overdue',
  }[code] || code
}

export function useToast() {
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (!msg) return; const t = setTimeout(() => setMsg(null), 4200); return () => clearTimeout(t) }, [msg])
  const node = msg ? <div className={`ins-toast ins-toast--${msg.tone || 'ok'}`} role="status" aria-live="polite">{msg.text}</div> : null
  return [node, (text, tone) => setMsg({ text, tone })]
}

export function Modal({ title, onClose, children, width = 460 }) {
  const ref = useRef(null)
  useEffect(() => {
    const prev = document.activeElement
    ref.current?.querySelector('input,select,textarea,button')?.focus()
    const onKey = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [onClose])
  return (
    <div className="ins-modal" role="dialog" aria-modal="true" aria-label={title} onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ins-modal__box" style={{ maxWidth: width }} ref={ref}>
        <div className="ins-modal__head"><h2>{title}</h2><button className="ins-btn ins-btn--icon" onClick={onClose} aria-label="Close"><Icon name="x" /></button></div>
        {children}
      </div>
    </div>
  )
}

export function Empty({ children }) {
  return <div className="ins-empty">{children}</div>
}
