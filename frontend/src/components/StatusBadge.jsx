import './StatusBadge.css'
import { useTerminology } from '../terminology'

const STATUS_CONFIG = {
  new: { label: 'New', color: 'blue' },
  queued: { label: 'Queued', color: 'blue' },
  sent: { label: 'Sent', color: 'neutral' },
  replied: { label: 'Replied', color: 'amber' },
  hot: { label: 'Hot', color: 'red' },
  booked: { label: 'Booked', color: 'green' },
  dnc: { label: 'DNC', color: 'neutral-dim' },
  dead: { label: 'Dead', color: 'neutral-dim' },
  needs_tier_review: { label: 'Needs Review', color: 'amber' },
  /* Written in production long before they were declared. Without these two
   * rows the badge fell through and printed the raw column value. "Not
   * Interested" is not DNC - it stops this work, not all contact. */
  cold: { label: 'Cold', color: 'neutral' },
  not_interested: { label: 'Not Interested', color: 'neutral-dim' },
}

/* ONE INDUSTRY'S TIER NAMES, PRINTED FOR EVERY INDUSTRY.
 *
 * This map was the label source for every tier badge in the product — the
 * Leads table and its tier counts, Lead Detail, Campaign Builder, Cadence and
 * the Admin book. It knows eight keys and all of the business ones are
 * deathcare: Pre-Need, At-Need, Imminent, Contract Sold.
 *
 * For a customer configured as anything else that produced BOTH failure modes
 * at once. A key this map happens to hold rendered SOMEBODY ELSE'S WORD —
 * an energy customer's `contract_sold` came out "Contract Sold" from a funeral
 * vocabulary — and every key it does not hold fell through to `label: tier`
 * and printed the raw column value, so Atlantis Light & Power showed
 * `rate_review` and `renewal_due` in snake_case on its own leads.
 *
 * The organization's configured tier model is the label authority — the same
 * one `/org-settings/` gives the tier filter and the tier editor — so a tenant
 * is never shown a word it did not choose. What stays here is COLOUR, which is
 * a visual convention rather than vocabulary, keyed by meaning that survives
 * across industries (an inbound enquiry reads cool, a closed deal green) and
 * defaulting to neutral rather than guessing.
 *
 * LEGACY KEYS ARE STILL READABLE. A lead carrying a tier its organization no
 * longer lists is humanised from its own key rather than dropped or relabelled
 * — the row has to stay findable, and nothing stored is rewritten.
 */
const TIER_COLOR = {
  pre_need: 'blue',
  at_need: 'amber',
  imminent: 'red',
  contract_sold: 'green',
  contract_signed: 'green',
  new_inquiry: 'purple',
  email_only: 'neutral',
  addr_only: 'neutral-dim',
  partial: 'amber',
  needs_tier_review: 'amber',
}

function humanizeTier(value) {
  return String(value || '')
    .split(/[_\-\s]+/).filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || { label: status, color: 'neutral' }
  return <span className={`badge badge--${config.color}`}>{config.label}</span>
}

export function TierBadge({ tier }) {
  const terminology = useTerminology()
  const configured = (terminology.tiers || []).find(t => t.value === tier)
  const label = configured?.label || humanizeTier(tier)
  const color = configured?.color || TIER_COLOR[tier] || 'neutral'
  return <span className={`badge badge--${color}`}>{label}</span>
}
