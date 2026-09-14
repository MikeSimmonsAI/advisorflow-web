/**
 * What the God Lead Browser ASKS FOR — extracted so it can be asserted.
 *
 * This module imports nothing and touches no DOM, so `tests/frontend` can run
 * it under plain `node` with no bundler. The reason it exists is the default:
 * the master database must hide synthetic and QA records unless somebody asks
 * for them, and "the default is safe" is a claim worth a test rather than a
 * comment.
 */

export const PAGE_SIZE = 50

/** The Stage 1 column set, in the order the operator reads it. */
export const MASTER_COLUMNS = [
  'Platform', 'Organization', 'Name', 'Email', 'Phone',
  'Source', 'First Seen', 'Last Seen', 'Occurrences',
]

/**
 * Build the query for GET /god/master/contacts.
 *
 * `includeSynthetic` is passed ONLY when true. An omitted parameter takes the
 * server's default, which is also "hide them" — so the two halves cannot drift
 * into disagreeing, and a client that forgets the flag still gets the safe
 * answer rather than a page full of demo people.
 */
export function buildMasterParams({
  search = '',
  platformId = '',
  orgId = '',
  source = '',
  includeSynthetic = false,
  needsReview = false,
  skip = 0,
  limit = PAGE_SIZE,
} = {}) {
  const params = { skip, limit }
  if (search) params.search = search
  if (platformId) params.platform_id = platformId
  if (orgId) params.org_id = orgId
  if (source) params.source = source
  if (includeSynthetic) params.include_synthetic = true
  if (needsReview) params.needs_review = true
  return params
}

/** Build the query for the original tenant-lead view, unchanged in meaning. */
export function buildTenantParams({
  search = '',
  platformSlug = '',
  orgId = '',
  status = '',
  skip = 0,
  limit = PAGE_SIZE,
} = {}) {
  const params = { skip, limit }
  if (search) params.search = search
  if (platformSlug) params.platform_slug = platformSlug
  if (orgId) params.org_id = orgId
  if (status) params.status = status
  return params
}

/** "Production only" is the default view; QA/test records are opt-in. */
export function isProductionOnly(includeSynthetic) {
  return !includeSynthetic
}

export function formatSeen(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString()
}
