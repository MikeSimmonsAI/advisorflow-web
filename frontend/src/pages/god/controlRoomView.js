// Pure helpers for the Control Room (no React) so they can be tested with node.

export const POLL_MS = 20000

// Status -> visual tone. Four visibly different states; Needs Mike is the loudest.
export const TONES = {
  Working:           { key: 'working',  label: 'Working',         bg: '#eff6ff', fg: '#1d4ed8', bd: '#93c5fd' },
  Complete:          { key: 'complete', label: 'Complete',        bg: '#ecfdf5', fg: '#047857', bd: '#6ee7b7' },
  Blocked:           { key: 'blocked',  label: 'Blocked',         bg: '#fff7ed', fg: '#c2410c', bd: '#fdba74' },
  'Approval Needed': { key: 'needs',    label: 'Approval Needed', bg: '#fef2f2', fg: '#b91c1c', bd: '#f87171' },
  Idle:              { key: 'idle',     label: 'Idle',            bg: '#f3f4f6', fg: '#4b5563', bd: '#d1d5db' },
}

export const toneFor = (status) => TONES[status] || TONES.Idle

// The NEEDS MIKE panel exists only when the server reports a true approval gate.
export const showNeedsMike = (data) => !!(data && data.needs_mike && data.needs_mike.decision)

export const KIND_LABEL = {
  mike_direction: 'Mike', chatgpt_directive: 'ChatGPT', next_directive: 'ChatGPT',
  github_accepted: 'GitHub', claude_started: 'Claude', claude_progress: 'Claude',
  claude_complete: 'Claude', chatgpt_review: 'ChatGPT', approval_gate: 'Needs Mike',
}

// Newest first for reading; the server order (oldest first) is the audit order.
export const timelineNewestFirst = (events) => [...(events || [])].reverse()

// Duplicate-submission guard: same text+mode while a send is in flight or just done.
export function makeSubmitGuard() {
  let inFlight = false
  let last = ''
  return {
    begin(text, mode) {
      const sig = mode + '|' + text.trim().toLowerCase().replace(/\s+/g, ' ')
      if (inFlight || !text.trim() || sig === last) return false
      inFlight = true
      last = sig
      return true
    },
    end(ok) { inFlight = false; if (!ok) last = '' },
  }
}

export const directionDisabledReason = (data) =>
  data && data.give_direction && data.give_direction.setup_required
    ? (data.give_direction.message || 'SETUP REQUIRED') : null

export function errorMessage(e) {
  const d = e && e.detail
  return (d && d.message) || (e && e.message) || 'Something went wrong. Nothing was sent.'
}
