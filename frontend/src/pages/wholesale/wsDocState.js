/* Pure document / contract / e-sign state truth — no React, no DOM, no
 * imports — so `node tests/frontend/wsDocState.test.mjs` runs it.
 *
 *   1. SIGNED NEEDS A COPY. "Signed" is shown as signed only when an executed
 *      copy is stored on the row. Marked signed with nothing attached reads as
 *      a contradiction, not as a completed signature.
 *   2. NOTHING IS INFERRED. Signed does not imply title clear, funded, closed
 *      or paid, and none of those words are produced here.
 *   3. OPERATOR WORDS. A stored key becomes words; one this code has never seen
 *      keeps its words plus "(reference: key)".
 *   4. PREREQUISITES. What is missing is listed, not hidden.
 */

export const DOC_STATUS_LABEL = {
  draft: 'Draft',
  ready_for_review: 'Ready for review',
  approved: 'Approved',
  sent: 'Sent',
  viewed: 'Opened by the other party',
  signed: 'Signed',
  declined: 'Declined',
  voided: 'Voided',
  superseded: 'Superseded',
}

export const SIGNATURE_STATES = [
  ['none', 'Not sent'],
  ['out_for_signature', 'Out for signature'],
  ['partially_signed', 'Partially signed'],
  ['signed', 'Signed'],
  ['declined', 'Declined'],
]
const SIGNATURE_LABEL = Object.fromEntries(SIGNATURE_STATES)

export const CONTRACT_STATUS_LABEL = {
  none: 'No contract yet',
  preparing: 'Being prepared',
  sent: 'Sent to the other party',
  signed: 'Recorded as signed',
  cancelled: 'Cancelled',
}

function words(key) {
  const w = String(key).replace(/[_-]+/g, ' ').trim()
  return `${w.charAt(0).toUpperCase()}${w.slice(1)} (reference: ${key})`
}

export function hasExecutedCopy(doc) {
  return !!(doc && doc.stored_file)
}

/* One reading of one document: { key, label, tone, warning }. */
export function docTruth(doc) {
  if (!doc) return { key: 'unavailable', label: 'Unavailable', tone: 'is-muted', warning: null }
  const status = doc.status_key || null
  const sig = doc.signature_status || 'none'
  const held = hasExecutedCopy(doc)
  const claimsSigned = status === 'signed' || sig === 'signed'

  if (claimsSigned && !held) {
    return { key: 'signed_unverified', tone: 'is-warn',
             label: 'Marked signed — no executed copy attached',
             warning: 'Attach the executed copy before relying on this signature.' }
  }
  if (claimsSigned) {
    return { key: 'signed', tone: 'is-ok', label: 'Signed — executed copy attached', warning: null }
  }
  if (status === 'declined' || sig === 'declined') {
    return { key: 'declined', tone: 'is-dnc', label: 'Declined', warning: null }
  }
  if (status === 'voided') return { key: 'voided', tone: 'is-dnc', label: 'Voided', warning: null }
  if (status === 'superseded') return { key: 'superseded', tone: 'is-muted', label: 'Superseded', warning: null }
  if (status === 'sent' || status === 'viewed' || sig === 'out_for_signature'
      || sig === 'partially_signed') {
    const label = sig === 'partially_signed' ? 'Partially signed'
      : status === 'viewed' ? 'Sent — opened by the other party' : 'Sent — awaiting signature'
    return { key: 'sent', tone: 'is-warn', label, warning: null }
  }
  if (status && !DOC_STATUS_LABEL[status]) {
    return { key: 'unknown', tone: 'is-muted', label: words(status), warning: null }
  }
  if (!held && !doc.file_name) {
    return { key: 'missing', tone: 'is-muted', label: 'Needed — nothing attached', warning: null }
  }
  if (!held) {
    return { key: 'name_only', tone: 'is-muted', label: 'Name only — no file held', warning: null }
  }
  return { key: status || 'uploaded', tone: 'is-muted',
           label: status ? DOC_STATUS_LABEL[status] : 'Uploaded', warning: null }
}

/* Signature states the operator may record on this row. SIGNED needs a copy. */
export function signatureOptions(doc) {
  const held = hasExecutedCopy(doc)
  const out = SIGNATURE_STATES.map(([key, label]) => ({
    key, label,
    disabled: key === 'signed' && !held,
    reason: key === 'signed' && !held ? 'Attach the executed copy first' : null,
  }))
  const cur = doc && doc.signature_status
  if (cur && !SIGNATURE_LABEL[cur]) {
    out.push({ key: cur, label: words(cur), disabled: true, reason: 'Not a state this screen can record' })
  }
  return out
}

/* The deal's contract status in words, and whether anything backs "signed". */
export function contractTruth(deal, documents) {
  const status = deal && deal.contract_status
  if (status === null || status === undefined || status === '') {
    return { key: 'unavailable', label: 'Contract status unavailable', backed: false, warning: null }
  }
  const label = CONTRACT_STATUS_LABEL[status] || words(status)
  if (status !== 'signed') return { key: status, label, backed: false, warning: null }
  const backed = (documents || []).some((d) => d && d.doc_type === 'purchase_contract'
                                              && hasExecutedCopy(d)
                                              && (d.status_key === 'signed' || d.signature_status === 'signed'))
  return { key: 'signed', label: backed ? 'Signed — executed copy attached' : label, backed,
           warning: backed ? null
             : 'Recorded as signed by a person; no executed purchase contract is attached.' }
}

/* What the drawer is still missing, in order. Facts only. */
export function missingPrerequisites(deal, documents) {
  const docs = documents || []
  const out = []
  if (!docs.some((d) => d.doc_type === 'purchase_contract' && hasExecutedCopy(d))) {
    out.push('No purchase contract file is attached.')
  }
  if (deal && (deal.contract_price === null || deal.contract_price === undefined || deal.contract_price === '')) {
    out.push('No contract price is recorded.')
  }
  return out
}
