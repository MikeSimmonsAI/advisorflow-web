/** SCI inbox triage model. Client-side only; no CRM state mutation. */
export const isOpenResponse = r => r.status !== 'closed'
export const needsAttention = r => ['new', 'opened', 'active'].includes(r.status)
export const priorityWeight = r => {
  if (r.sla_breached && isOpenResponse(r)) return 0
  if (r.status === 'new' && r.class === 'hot') return 1
  if (r.status === 'new') return 2
  if (r.class === 'hot' && isOpenResponse(r)) return 3
  if (r.status === 'opened') return 4
  if (r.status === 'active') return 5
  if (r.status === 'responded') return 6
  return 7
}
export const latestMs = r => {
  const ms = Date.parse(r.received_at || '')
  return Number.isFinite(ms) ? ms : 0
}
export const sortInbox = (items, mode = 'attention') =>
  [...items].sort((a, b) => mode === 'recent'
    ? latestMs(b) - latestMs(a) || String(a.id).localeCompare(String(b.id))
    : priorityWeight(a) - priorityWeight(b) || latestMs(b) - latestMs(a) || String(a.id).localeCompare(String(b.id)))
export const queueMatches = (r, queue) => {
  if (queue === 'new') return r.status === 'new'
  if (queue === 'hot') return r.class === 'hot' && needsAttention(r)
  if (queue === 'followup') return ['opened', 'active'].includes(r.status)
  if (queue === 'handled') return ['responded', 'closed'].includes(r.status)
  if (queue === 'recent') return true
  return needsAttention(r)
}
export const statusMatches = (r, status) =>
  !status || (status === 'open' ? isOpenResponse(r) : r.status === status)
export const integerValue = (metrics, key) => {
  const v = metrics?.[key]?.value
  return v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v)
}
