import './StatusBadge.css'

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
}

const TIER_CONFIG = {
  pre_need: { label: 'Pre-Need', color: 'blue' },
  at_need: { label: 'At-Need', color: 'amber' },
  imminent: { label: 'Imminent', color: 'red' },
  contract_sold: { label: 'Contract Sold', color: 'green' },
  email_only: { label: 'Email Only', color: 'neutral' },
  addr_only: { label: 'Address Only', color: 'neutral-dim' },
  partial: { label: 'Needs Review', color: 'amber' },
}

export function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || { label: status, color: 'neutral' }
  return <span className={`badge badge--${config.color}`}>{config.label}</span>
}

export function TierBadge({ tier }) {
  const config = TIER_CONFIG[tier] || { label: tier, color: 'neutral' }
  return <span className={`badge badge--${config.color}`}>{config.label}</span>
}
