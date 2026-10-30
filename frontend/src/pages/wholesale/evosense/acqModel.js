/* Acquisition Command (Design B, #439) - the screen's pure rules, kept apart
 * from the JSX so they can be tested with node --test. Reads only the property
 * payload from GET /wholesale/evosense/properties/{id}; invents nothing. */

// Same output as ds.money / ds.humanize (ds.jsx is JSX, so not importable by node --test).
function fmtMoney(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isNaN(n) ? '—' : '$' + Math.round(n).toLocaleString()
}
const humanize = (v) => (v === null || v === undefined ? '' : String(v).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()))

export const ACQ_TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'ownership', label: 'Ownership' },
  { key: 'signals', label: 'Distress Signals' },
  { key: 'comps', label: 'Comps & Value' },
  { key: 'underwriting', label: 'Underwriting' },
  { key: 'buyers', label: 'Buyers' },
  { key: 'contacts', label: 'Contacts' },
  { key: 'ai', label: 'AI Assistant' },
  { key: 'activity', label: 'Activity' },
]
export const TAB_KEYS = ACQ_TABS.map((t) => t.key)

// Old in-page anchors (#deal etc., still used by a few panels) open the tab
// that now holds that section.
export const HASH_TO_TAB = {
  deal: 'underwriting', record: 'ownership', owner: 'contacts', signals: 'signals', timeline: 'activity',
  data: 'activity', conversation: 'contacts', facts: 'ai', intel: 'ai', value: 'comps', readiness: 'overview',
  actions: 'overview', ptype: 'ownership', opportunity: 'overview',
  'intel-sources': 'ownership', 'contact-intel': 'contacts', verify: 'ownership',
}

/** The tab to open for a ?tab= value or a legacy #hash; Overview otherwise. */
export function tabFrom(search, hash) {
  const q = new URLSearchParams(search || '').get('tab')
  if (q && TAB_KEYS.includes(q)) return q
  const h = String(hash || '').replace(/^#/, '')
  return HASH_TO_TAB[h] || 'overview'
}

/** The six KPI values, straight from the property payload. Pure; tested. */
export function kpis(d) {
  const p = (d && d.property) || {}
  const f = (d && d.facts) || {}
  const eco = (d && d.economics) || {}
  const line = (label) => (eco.lines || []).find((l) => l.label === label) || {}
  const signals = (d && d.signals) || []
  const scored = signals.filter((s) => s.scored !== false && s.freshness !== 'stale')
  const onRecord = scored.filter((s) => !s.derived)
  const repairs = line('Repairs')
  const mao = line('Preliminary MAO')
  const arv = eco.arv && eco.arv.value != null ? eco.arv : null
  const eq = f.equity_pct || {}
  return {
    distress: {
      value: p.opportunity_score ?? null,
      sub: p.opportunity_score == null ? 'not scored yet' : 'rules-based points from evidence on file, not a probability',
    },
    signals: {
      value: signals.length ? scored.length : null,
      sub: !signals.length ? 'no signals on file'
        : `${onRecord.length} on a public record${scored.length - onRecord.length ? ` · ${scored.length - onRecord.length} computed` : ''}${signals.length - scored.length ? ` · ${signals.length - scored.length} stale or unverified (not counted)` : ''}`,
    },
    equity: {
      value: eq.value != null ? `${eq.value}%` : null,
      sub: eq.value != null ? String(eq.truth || 'estimate').split(' (')[0].toLowerCase() : 'loan balance not on record - unknown',
    },
    arv: {
      value: arv ? fmtMoney(arv.value) : null,
      sub: arv ? `${arv.comp_count} eligible closed sale${arv.comp_count === 1 ? '' : 's'} · ${arv.confidence} confidence`
        : ((f.arv && f.arv.label) || 'Insufficient comparable sales'),
    },
    repairs: {
      value: repairs.value != null ? fmtMoney(repairs.value) : null,
      sub: repairs.value != null ? (repairs.truth_label || 'estimate') : (repairs.display || 'no repair estimate yet'),
    },
    mao: {
      value: mao.value != null ? fmtMoney(mao.value) : null,
      sub: mao.value != null ? `preliminary · ${mao.truth_label || 'calculated'}` : (mao.display || 'needs a verified ARV'),
    },
  }
}

/** Ownership verification, read from what is on file. Pure; tested. */
export function ownershipChecks(d) {
  const owners = (d && d.owners) || []
  const current = owners.filter((o) => o.current !== false)
  const p = (d && d.property) || {}
  const conflicts = ((d && d.conflicts) || []).filter((c) => /owner/i.test(String(c.field || '')))
  const sources = [...new Set(current.flatMap((o) => o.sources || []))]
  const resolved = current.some((o) => o.resolution && o.resolution !== 'unresolved')
  return [
    { key: 'owner', ok: current.length > 0, label: 'Owner of record on file',
      detail: current.length ? current.map((o) => o.name || 'name not on record').join('; ') : 'No owner of record - nothing is guessed.' },
    { key: 'resolved', ok: resolved, label: 'Owner identity resolved',
      detail: current.length ? current.map((o) => o.resolution_label || humanize(o.resolution || 'unresolved')).join('; ') : 'No owner to resolve.' },
    { key: 'sources', ok: sources.length > 0, label: 'Source of the ownership record',
      detail: sources.length ? sources.join(', ') : 'No source recorded.' },
    { key: 'conflicts', ok: conflicts.length === 0 && !current.some((o) => o.in_conflict), label: 'No conflicting owner records',
      detail: conflicts.length || current.some((o) => o.in_conflict) ? 'Sources disagree - both are kept and shown; nothing is overwritten.' : 'No owner conflict on file.' },
    { key: 'identity', ok: !((d && d.identity_reviews) || []).length && p.identity_status !== 'review', label: 'Property identity confirmed',
      detail: ((d && d.identity_reviews) || []).length ? 'A possible duplicate record needs a person to decide.' : 'No identity review open.' },
  ]
}

/** The readiness label for the header, from the opportunity view. */
export function readiness(d) {
  const o = (d && d.opportunity) || {}
  const conf = o.confidence && o.confidence.label
  const stage = o.workflow_state && o.workflow_state.label
  const tone = conf === 'verified' ? 'live' : conf === 'insufficient' ? 'danger' : 'estimate'
  return { label: conf ? `${humanize(conf)} evidence` : 'Evidence not assessed', stage: stage || null, tone }
}

