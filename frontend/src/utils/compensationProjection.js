/* Pure helpers for the read-only compensation projection page.
 *
 * Nothing here computes money. Amounts arrive as server-formatted integer-cent
 * `display` strings and are only prefixed; a missing amount is a dash, never $0.
 */

// Order is the page's reading order: money that exists first, forecasts last.
export const LEGEND = [
  { key: 'earned', label: 'Earned', kind: 'earned',
    text: 'Commission on collected customer payments (on hold + payable + paid).' },
  { key: 'on_hold', label: 'On hold', kind: 'earned',
    text: 'Earned, still inside the holdback period.' },
  { key: 'payable', label: 'Payable', kind: 'earned',
    text: 'Earned, holdback elapsed. Settlement happens elsewhere, not here.' },
  { key: 'paid', label: 'Paid', kind: 'earned',
    text: 'Already settled.' },
  { key: 'pending', label: 'Pending approval', kind: 'pending',
    text: 'Deals awaiting approval. Not in the forecast and not earned.' },
  { key: 'forecast', label: 'Unweighted forecast', kind: 'forecast',
    text: 'Commission if every open deal closes. A forecast, not money owed.' },
  { key: 'weighted', label: 'Weighted forecast', kind: 'forecast',
    text: 'Forecast scaled by stage probability. A forecast, not money owed.' },
  { key: 'excluded', label: 'Excluded / blockers', kind: 'excluded',
    text: 'Deals left out, and why. Excluded is not $0.' },
]

export const TILE_LABELS = {
  gross: 'Projected gross sales (unweighted)',
  weightedGross: 'Expected weighted revenue (forecast)',
  commission: 'Unweighted forecast commission',
  weightedCommission: 'Weighted forecast commission',
  pending: 'Pending approval (not in forecast)',
  onHold: 'Earned — on hold',
  payable: 'Earned — payable (holdback elapsed)',
  paid: 'Earned — paid',
  earned: 'Total earned',
}

export function usd(m) {
  return m && typeof m.display === 'string' ? '$' + m.display : '—'
}

// The server's own detail wins: it names the actual cause. Only a missing
// detail falls back to a generic line for that class of status.
export function errorMessage(e) {
  const s = e && e.status
  const detail = e && typeof e.detail === 'string' && e.detail ? e.detail
    : (e && e.message) || ''
  if (s === 400) return 'Request refused (400): ' + (detail || 'invalid request.')
  if (s === 403) return 'Not authorized (403): ' + (detail || 'no access to this compensation scope.')
  if (s === 422) return 'Projection refused (422) — stored data is inconsistent: ' + (detail || 'see server log.')
  if (typeof s === 'number' && s >= 500) {
    return 'Server error (' + s + '): ' + (detail || 'the projection could not be produced.') +
      ' No figures are shown.'
  }
  if (typeof s === 'number') return 'Request failed (' + s + '): ' + (detail || 'unknown error.')
  return 'Could not reach the server: ' + (detail || 'network error.') + ' No figures are shown.'
}

// 'no_records' = nothing matched at all; 'blocked' = deals exist but were
// excluded or a configuration blocker applies; 'data' = at least one figure.
export function emptyState(d) {
  if (!d) return 'none'
  const deals = (d.deals || []).length
  const excluded = (d.excluded || []).length
  const blockers = (d.blockers || []).length
  const earned = d.earned ? (d.earned.on_hold.count + d.earned.payable.count + d.earned.paid.count) : 0
  const pending = d.pending ? d.pending.deal_count : 0
  if (deals + earned + pending > 0) return 'data'
  if (excluded + blockers > 0) return 'blocked'
  return 'no_records'
}

export const EMPTY_TEXT = {
  no_records: 'No matching records: there are no open deals, pending deals or earned entries for this scope and filter. Nothing was excluded and no configuration blocker applies.',
  blocked: 'No deal could be counted. Matching deals were excluded or a configuration blocker applies — see below. This is not a $0 forecast.',
}

// Query string from filters; ids are encoded, empty values dropped, order fixed.
export function projectionQuery(brand, stage) {
  const p = []
  if (brand) p.push('brand_sales_org_id=' + encodeURIComponent(brand))
  if (stage) p.push('stage=' + encodeURIComponent(stage))
  return p.length ? '?' + p.join('&') : ''
}

// Stage choices for the dropdown. The server's available_stages (derived from
// the unfiltered, scope-limited deals) is authoritative; it is de-duplicated and
// sorted here so order never depends on arrival. Deal rows are NOT used: they
// are already stage-filtered. The selected stage is kept as an option so the
// control never shows a value it cannot display.
export function stageOptions(available, selected) {
  const set = new Set((Array.isArray(available) ? available : []).filter(Boolean))
  if (selected) set.add(selected)
  return Array.from(set).sort()
}
