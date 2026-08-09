// ── Industry member-role labels ────────────────────────────────────────────
// Maps industry key → { singular, plural } for the non-admin user role.
// Org admins can override these in OrgSettings (member_label / members_label).
// Retrieve via getMemberLabel(branding, plural) throughout the app.

export const INDUSTRY_MEMBER_LABELS = {
  // Funeral & Cemetery — FSA, NOT "Director" (that's the licensed funeral director)
  funeral: { singular: 'Advisor', plural: 'Advisors' },

  // Insurance
  insurance: { singular: 'Agent', plural: 'Agents' },
  health_insurance: { singular: 'Agent', plural: 'Agents' },
  medicare: { singular: 'Agent', plural: 'Agents' },
  annuities: { singular: 'Advisor', plural: 'Advisors' },

  // Finance / Real Estate
  mortgage: { singular: 'Advisor', plural: 'Advisors' },
  financial_services: { singular: 'Advisor', plural: 'Advisors' },
  real_estate: { singular: 'Agent', plural: 'Agents' },

  // Field Sales / D2D
  fiber: { singular: 'Rep', plural: 'Reps' },
  door_to_door: { singular: 'Rep', plural: 'Reps' },
  direct_sales: { singular: 'Rep', plural: 'Reps' },
  solar: { singular: 'Rep', plural: 'Reps' },
  telecom: { singular: 'Rep', plural: 'Reps' },
  security: { singular: 'Rep', plural: 'Reps' },
  roofing: { singular: 'Rep', plural: 'Reps' },
  landscaping: { singular: 'Rep', plural: 'Reps' },
  windows_doors: { singular: 'Rep', plural: 'Reps' },
  painting: { singular: 'Rep', plural: 'Reps' },
  flooring: { singular: 'Rep', plural: 'Reps' },
  cleaning: { singular: 'Rep', plural: 'Reps' },
  tree_service: { singular: 'Rep', plural: 'Reps' },
  water_treatment: { singular: 'Rep', plural: 'Reps' },

  // Home Services / Trades
  hvac: { singular: 'Tech', plural: 'Techs' },
  plumbing: { singular: 'Tech', plural: 'Techs' },
  electrical: { singular: 'Tech', plural: 'Techs' },
  pest_control: { singular: 'Tech', plural: 'Techs' },
  pool_spa: { singular: 'Tech', plural: 'Techs' },
  auto_repair: { singular: 'Tech', plural: 'Techs' },

  // Healthcare / Professional
  dental: { singular: 'Rep', plural: 'Reps' },
  medical: { singular: 'Rep', plural: 'Reps' },
  chiropractic: { singular: 'Rep', plural: 'Reps' },
  physical_therapy: { singular: 'Rep', plural: 'Reps' },
  veterinary: { singular: 'Rep', plural: 'Reps' },
  legal: { singular: 'Rep', plural: 'Reps' },
  fitness: { singular: 'Rep', plural: 'Reps' },
  education: { singular: 'Rep', plural: 'Reps' },

  // Catch-all
  custom: { singular: 'Rep', plural: 'Reps' },
}

const DEFAULT_LABELS = { singular: 'Advisor', plural: 'Advisors' }

/**
 * Returns the correct member label for this org.
 *
 * Priority:
 *   1. Org override stored in branding (member_label / members_label)
 *   2. Industry default from INDUSTRY_MEMBER_LABELS
 *   3. Fallback: "Advisor" / "Advisors"
 *
 * @param {object|null} branding  — result of getBranding() or null
 * @param {boolean}     plural    — true → return plural form
 * @returns {string}
 */
export function getMemberLabel(branding, plural = false) {
  if (plural) {
    if (branding?.members_label) return branding.members_label
    const industry = branding?.industry || 'funeral'
    return (INDUSTRY_MEMBER_LABELS[industry] || DEFAULT_LABELS).plural
  } else {
    if (branding?.member_label) return branding.member_label
    const industry = branding?.industry || 'funeral'
    return (INDUSTRY_MEMBER_LABELS[industry] || DEFAULT_LABELS).singular
  }
}
