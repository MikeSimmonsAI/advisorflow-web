// Which LeadDetail tab a URL asks for (?tab=...). The Sales Board and other
// screens deep-link to /leads/{id}?tab=timeline, so the page must open on the
// tab named in the URL instead of always starting on Conversation.
//
// Lead Command Center tabs (Oct 2026): Conversation, Calls, Activity, History,
// Overview. The old "timeline" (Full History) was the activity log, so the
// links that still say ?tab=timeline open Activity.
export const LEAD_DETAIL_TABS = ['conversation', 'calls', 'activity', 'history', 'overview']

const ALIASES = {
  conversation: 'conversation', conversations: 'conversation', messages: 'conversation', sms: 'conversation',
  calls: 'calls', call: 'calls', phone: 'calls', voicemail: 'calls', voicemails: 'calls',
  activity: 'activity', timeline: 'activity', log: 'activity',
  history: 'history', 'full-history': 'history', full_history: 'history', audit: 'history',
  overview: 'overview', details: 'overview', profile: 'overview', edit: 'overview',
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
