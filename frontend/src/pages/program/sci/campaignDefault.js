/**
 * WHICH CAMPAIGN OUTREACH CADENCE OPENS ON (#507, Nov 16). Pure, node-testable.
 *
 *   1. a valid deep link (?campaign=<key>) - the campaign it names, when this
 *      workspace has it;
 *   2. the workspace's main campaign: Restland opens on Family File Review
 *      (its Return campaign and the others stay one tap away);
 *   3. otherwise the first campaign in the list, as before (SCI unchanged).
 */
export const MAIN_CAMPAIGNS = ['restland_family_file_review']

export function defaultCampaign(rows, deepLink = '', current = null) {
  const list = Array.isArray(rows) ? rows : []
  const linked = deepLink && list.find(f => f.key === deepLink)
  if (linked) return linked
  if (current && list.some(f => f.id === current.id)) return current
  for (const key of MAIN_CAMPAIGNS) {
    const main = list.find(f => f.key === key)
    if (main) return main
  }
  return list[0] || null
}
