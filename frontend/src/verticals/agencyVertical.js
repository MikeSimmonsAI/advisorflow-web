/**
 * INSURANCE AGENCY (Max Life Command) — the rail for a workspace that holds
 * the `insurance_agency` entitlement.
 *
 * Selected by FEATURE, not by industry or customer name: a workspace whose
 * enabled_features EXPLICITLY lists `insurance_agency` wears this rail. A
 * legacy workspace with enabled_features = null ("everything") does NOT —
 * null means "not configured", and switching an unconfigured workspace's
 * whole navigation would be the sidebar deciding a product for it.
 *
 * Every /agency/* route is guarded by the same feature on the route and on
 * the server (require_feature("insurance_agency") → 402).
 */
export const AGENCY_FEATURE = 'insurance_agency'
export const VERTICAL_AGENCY = 'agency'

const F = AGENCY_FEATURE

export const AGENCY = {
  key: VERTICAL_AGENCY,
  skin: 'agency',
  productName: 'Max Life Command',
  poweredBy: 'Powered by EvoSysPro',
  navGroups: [
    {
      label: 'Command',
      items: [
        { to: '/agency', label: 'Command Center', icon: 'grid', featureKey: F, end: true },
        { to: '/agency/opportunities', label: 'AI Opportunity Center', icon: 'target', featureKey: F },
      ],
    },
    {
      label: 'Client Acquisition',
      items: [
        { to: '/agency/prospects', label: 'Prospects & Families', icon: 'users', featureKey: F },
        { to: '/agency/distribution', label: 'Lead Distribution', icon: 'repeat', featureKey: F },
        // Prospects' conversations in THIS workspace (Message / Reply /
        // simulated copilot sends), each opening the prospect's thread + copilot.
        { to: '/agency/conversations', label: 'Conversations', icon: 'message', featureKey: F },
        { to: '/agency/appointments', label: 'Appointments', icon: 'calendar', featureKey: F },
      ],
    },
    {
      label: 'Case Management',
      items: [
        { to: '/agency/applications', label: 'Applications & Underwriting', icon: 'file-text', featureKey: F },
        { to: '/agency/policies', label: 'Policies & Clients', icon: 'shield-check', featureKey: F },
      ],
    },
    {
      label: 'Agency Growth',
      items: [
        { to: '/agency/recruits', label: 'Recruiting & Licensing', icon: 'user-plus', featureKey: F },
        { to: '/agency/agents', label: 'Agents & Production', icon: 'trending-up', featureKey: F },
      ],
    },
    {
      label: 'Intelligence',
      items: [
        { to: '/agency/intelligence', label: 'Agency Intelligence', icon: 'activity', featureKey: F },
      ],
    },
    {
      label: 'System',
      items: [
        { to: '/crm-connectors', label: 'Integrations', icon: 'link', adminOnly: true, featureKey: 'crm_connectors' },
        { to: '/users', label: 'Team & Access', icon: 'user-plus', adminOnly: true, featureKey: 'users' },
        { to: '/settings', label: 'Settings', icon: 'settings' },
        { to: '/audit-log', label: 'Audit & Compliance', icon: 'shield', adminOnly: true, featureKey: 'audit_log' },
      ],
    },
  ],
}

/** True only when the workspace's feature list EXPLICITLY includes the key. */
export function hasAgencyFeature(branding) {
  const f = branding && branding.enabled_features
  return Array.isArray(f) && f.includes(AGENCY_FEATURE)
}

/** The agency vertical for this workspace, or null. */
export function agencyVerticalFor(branding) {
  return hasAgencyFeature(branding) ? AGENCY : null
}
