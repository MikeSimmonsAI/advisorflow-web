// Shared vocabulary for the Communications command center (WS5).
// Labels only — every value shown comes from the server.

export const CLASSIFICATIONS = {
  interested: { label: 'Interested', tone: 'green' },
  callback: { label: 'Callback', tone: 'amber' },
  question: { label: 'Question', tone: 'violet' },
  neutral: { label: 'Neutral', tone: 'grey' },
  not_interested: { label: 'Not interested', tone: 'grey' },
  wrong_number: { label: 'Wrong number', tone: 'grey' },
  dnc: { label: 'DNC / STOP', tone: 'red' },
}

export const STATUSES = {
  new: { label: 'New', tone: 'blue' },
  needs_attention: { label: 'Needs attention', tone: 'amber' },
  callback: { label: 'Callback', tone: 'amber' },
  reviewed: { label: 'Reviewed', tone: 'green' },
  closed: { label: 'Closed', tone: 'grey' },
}

export const ROLE_LABELS = {
  org_admin: 'Org Admin', super_admin: 'Super Admin', god_admin: 'Platform Owner',
  advisor: 'Advisor', manager: 'Manager', sales_manager: 'Sales Manager',
}

export function roleLabel(role) {
  if (!role) return ''
  return ROLE_LABELS[role] || role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

export function Tag({ tone = 'grey', children, title }) {
  return <span className={`cc-tag cc-tag--${tone}`} title={title}>{children}</span>
}

export function ClassTag({ value }) {
  if (!value) return null
  const c = CLASSIFICATIONS[value] || { label: value, tone: 'grey' }
  return <Tag tone={c.tone}>{c.label}</Tag>
}

export function StatusTag({ value }) {
  if (!value) return null
  const s = STATUSES[value] || { label: value, tone: 'grey' }
  return <Tag tone={s.tone}>{s.label}</Tag>
}

export function timeAgo(iso) {
  if (!iso) return '—'
  const t = new Date(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z').getTime()
  const diff = Date.now() - t
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d ago`
  return new Date(t).toLocaleDateString()
}

export function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function fmtPhone(v) {
  if (!v) return null
  const d = String(v).replace(/\D/g, '')
  if (d.length === 11 && d.startsWith('1')) return `(${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7)}`
  if (d.length === 10) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`
  return v
}

const PATHS = {
  search: 'M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm10 2-4.35-4.35',
  alert: 'M12 9v4m0 4h.01M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.7 3.86a2 2 0 0 0-3.4 0z',
  phone: 'M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.18 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.1 9.9a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z',
  check: 'M20 6 9 17l-5-5',
  ban: 'M4.93 4.93l14.14 14.14M12 22a10 10 0 1 1 0-20 10 10 0 0 1 0 20z',
  bot: 'M12 8V4H8M4 12h16M6 8h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2zm3 6h.01M15 14h.01',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z',
  edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4L16.5 3.5z',
  note: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM14 2v6h6M16 13H8M16 17H8',
  task: 'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  back: 'M15 18l-6-6 6-6',
  chevL: 'M15 18l-6-6 6-6',
  chevR: 'M9 18l6-6-6-6',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm18 2-10 7L2 6',
  msg: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  x: 'M18 6 6 18M6 6l12 12',
  ext: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',
}

export function Icon({ name, size = 16 }) {
  return (
    <svg className="cc-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] || ''} />
    </svg>
  )
}

export function channelIcon(ch) {
  if (ch === 'email') return 'mail'
  if (ch === 'voice' || ch === 'call') return 'phone'
  return 'msg'
}
