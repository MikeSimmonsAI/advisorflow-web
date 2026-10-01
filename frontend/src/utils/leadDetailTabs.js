// Which LeadDetail tab a URL asks for (?tab=...). The Sales Board and other
// screens deep-link to /leads/{id}?tab=timeline, so the page must open on the
// tab named in the URL instead of always starting on Conversation.
export const LEAD_DETAIL_TABS = ['conversation', 'calls', 'timeline']

const ALIASES = {
  conversation: 'conversation', conversations: 'conversation', messages: 'conversation', sms: 'conversation',
  calls: 'calls', call: 'calls', phone: 'calls',
  timeline: 'timeline', history: 'timeline', 'full-history': 'timeline', full_history: 'timeline', activity: 'timeline',
}

// `search` is location.search ("?tab=timeline") or a URLSearchParams.
// Unknown or missing values fall back to `fallback` (default 'conversation').
export function leadDetailTabFromSearch(search, fallback = 'conversation') {
  let raw = null
  try {
    const params = typeof search === 'string' || search == null ? new URLSearchParams(search || '') : search
    raw = params.get('tab')
  } catch {
    raw = null
  }
  const key = String(raw || '').trim().toLowerCase()
  return ALIASES[key] || fallback
}
