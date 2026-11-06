/**
 * THE PROGRAM LAUNCHER'S CARDS - presentation only.
 *
 * Every entry comes from the server's /auth/my-contexts answer for THIS login;
 * nothing here grants or infers access. A workspace the server did not list
 * never becomes a card. Each authorized workspace is shown as the program it
 * runs (server `experience`, then its program route / industry).
 */
const PROGRAMS = {
  sci: { rank: 1, variant: 'sci', symbol: '✦', kind: 'SCI · FAMILY SERVICES',
         summary: 'Families, conversations, appointments and outreach',
         facts: ['Family outreach', 'Conversations', 'Appointments'] },
  energy: { rank: 2, variant: 'energy', symbol: '⚡', kind: 'ENERGY OPERATIONS',
            summary: 'Customers, enrollments and energy operations',
            facts: ['Enrollments', 'Customers', 'Service ops'] },
  roofing: { rank: 3, variant: 'roofing', symbol: '⌂', kind: 'AI ROOFING OPERATIONS',
             summary: 'AI follow-up, homeowner replies and inspections',
             facts: ['AI follow-up', 'Homeowners', 'Inspections'] },
  wholesale: { rank: 4, variant: 'wholesale', symbol: '◎', kind: 'PROPERTY ACQUISITION',
               summary: 'Acquisition command and deal operations',
               facts: ['Acquisitions', 'Deal flow', 'Dispositions'] },
  funeral: { rank: 5, variant: 'funeral', symbol: '❖', kind: 'CEMETERY & FUNERAL SERVICES',
             summary: 'Families, pre-need planning, calendar and records',
             facts: ['Families', 'Pre-need', 'Records'] },
  // An insurance agency running the renewal & retention workspace (#499).
  // Its words come from the server (`presentation`); these are fallbacks.
  insurance_retention: { rank: 6, variant: 'retention', symbol: '↻', kind: 'INSURANCE AGENCY',
                         summary: 'Renewals, client reviews, appointments and team service',
                         facts: ['Renewals', 'Client care', 'Team & locations'] },
  insurance: { rank: 6, variant: 'insurance', symbol: '⛨', kind: 'INSURANCE AGENCY',
               summary: 'Prospects, AI conversations, appointments, applications and policies',
               facts: ['Prospects', 'Applications', 'Policies'] },
  cleaning: { rank: 7, variant: 'cleaning', symbol: '✦', kind: 'COMMERCIAL CLEANING',
              summary: 'Prospects, follow-up, booked walkthroughs and reports',
              facts: ['Prospects', 'Follow-up', 'Walkthroughs'] },
  // Managed client acquisition (Issue #452) - owner-approved purple theme.
  acquisition: { rank: 7, variant: 'acquisition', symbol: '✦', kind: 'COMMERCIAL CLEANING · CLIENT PORTAL',
                 summary: 'Interested businesses, booked walkthroughs, estimates and results',
                 facts: ['Appointments', 'Estimates', 'Results'] },
  acquisition_ops: { rank: 8, variant: 'acquisition', symbol: '◎', kind: 'ACQUISITION CONTROL CENTER',
                     summary: 'Prospects, evidence, pipeline and pilot controls',
                     facts: ['Prospects', 'Pipeline', 'Pilot controls'] },
  // VOLT Marketing recruiting program (#535) - owner-approved red and black.
  volt: { rank: 7, variant: 'volt', symbol: 'ϟ', kind: 'VOLT MARKETING · RECRUITING',
          summary: 'Applicants, reviews, referrals, Zoom sessions and reports',
          facts: ['Applicants', 'Referrals', 'Zoom Sessions'] },
  // EvoSys Gold & Jewelry (gold buyers, jewelry stores, coin dealers). The
  // card keeps the business's own name; its artwork is the neutral EvoSys look
  // until the industry artwork is approved through the redesign lane.
  jewelry: { rank: 6, variant: 'general', symbol: '◇', kind: 'GOLD & JEWELRY · BUSINESS WORKSPACE',
             summary: 'Customers, appointments, promotions, inventory and results',
             facts: ['Customer Vault', 'Appointments', 'Promotions'] },
  business: { rank: 7, variant: 'general', symbol: '◈', kind: 'BUSINESS OPERATIONS',
              summary: 'Your dedicated business workspace',
              facts: ['Leads', 'Conversations', 'Calendar'] },
  back_office: { rank: 8, variant: 'office', symbol: '▤', kind: 'BACK OFFICE',
                 summary: 'Sales pipeline, customers and brand operations',
                 facts: ['Pipeline', 'Customers', 'Brand ops'] },
  executive: { rank: 9, variant: 'office', symbol: '◆', kind: 'EXECUTIVE SUITE',
               summary: 'Portfolio performance across the brand',
               facts: ['Portfolio', 'Performance', 'Reporting'] },
}

// A GENERAL business keeps its own identity: the card is titled with the
// organization's real name and labelled with the kind of business the server
// recorded for it (organizations.industry). This is a vocabulary of
// industries, never a list of clients - a new client appears by itself.
const INDUSTRY_KINDS = {
  cleaning: 'COMMERCIAL CLEANING',
  real_estate: 'REAL ESTATE',
  land: 'LAND & REAL ESTATE',
  fiber: 'FIBER & TELECOM',
  wellness: 'HEALTH & WELLNESS',
  dental: 'DENTAL PRACTICE',
  automotive: 'AUTOMOTIVE',
}
export function businessKind(industry) {
  return INDUSTRY_KINDS[String(industry || '').trim().toLowerCase()] || PROGRAMS.business.kind
}

/** Which program a server-listed workspace runs, or null (shown under "Other"). */
export function programOf(w) {
  if (!w) return null
  // A program workspace that is NOT SCI (Restland, Issue #507) opens the same
  // program shell but keeps its own business identity on its card.
  if (w.is_program && w.program_is_sci === false && (w.industry === 'funeral' || w.industry === 'cemetery')) return 'funeral'
  if (w.is_program && w.program_is_sci === false) return 'business'
  if (w.is_program || w.experience === 'sci') return 'sci'
  if (w.program_route === '/program/roofing' || w.experience === 'roofing') return 'roofing'
  if (w.program_route === '/program/jewelry' || w.experience === 'jewelry') return 'jewelry'
  if (w.program_route === '/program/insurance' || w.presentation?.kind === 'insurance_retention') return 'insurance_retention'
  if (w.experience === 'insurance' || w.industry === 'insurance') return 'insurance'
  if (w.experience === 'energy' || w.industry === 'energy') return 'energy'
  if (w.industry === 'funeral' || w.industry === 'cemetery') return 'funeral'
  if (w.program_route === '/program/acquisition' || w.experience === 'acquisition') return 'acquisition'
  if (w.industry === 'cleaning') return 'cleaning'
  return null
}

function initialsOf(name) {
  return String(name || '').split(/\s+/).filter(w => /^[A-Za-z]/.test(w)).slice(0, 2).map(w => w[0]).join('').toUpperCase()
}

function displayName(program, w) {
  if (program === 'sci') return 'SCI'
  if (program === 'roofing') return 'Roofing'
  if (program === 'insurance') return 'Insurance'
  return w.organization_name
}

export function programCards(ctx) {
  const workspaces = ctx?.workspace_contexts || []
  const products = ctx?.product_contexts || []
  const isProductHost = w => products.some(p => p.organization_id === w.organization_id)
  const cards = []
  const others = []
  const voltOrgs = new Set((ctx?.volt_contexts || []).map(v => v.organization_id))
  for (const w of workspaces) {
    if (isProductHost(w)) continue
    if (voltOrgs.has(w.organization_id)) continue
    // A workspace whose kind of business has no dedicated identity yet still
    // gets a full card, in the neutral EvoSys look (Mike, 2026-10-21) — never
    // a plain row. Only the presentation is neutral; the workspace and its
    // destination are exactly what the server listed.
    const program = programOf(w) || 'business'
    const p = PROGRAMS[program]
    if (program === 'insurance_retention') {
      // The agency's own identity, as the server configured it - presentation only.
      const pr = w.presentation || {}
      const title = pr.title || w.organization_name
      cards.push({ ...w, program, variant: p.variant, symbol: initialsOf(title) || p.symbol, rank: p.rank,
                   kind: pr.eyebrow || p.kind, facts: pr.chips || p.facts, display: title,
                   subtitle: pr.subtitle || null, summary: pr.description || p.summary,
                   actionLabel: pr.button || null, badge: pr.sample ? 'Sample data' : null,
                   orgLabel: null })
      continue
    }
    const kind = program === 'business' ? businessKind(w.industry) : p.kind
    cards.push({ ...w, program, variant: p.variant, symbol: p.symbol, rank: p.rank, kind, facts: p.facts,
                 display: displayName(program, w), summary: p.summary,
                 orgLabel: displayName(program, w) === w.organization_name ? null : w.organization_name })
  }
  for (const pr of products) {
    const p = PROGRAMS[pr.key] || PROGRAMS.business
    cards.push({ ...pr, program: pr.key, variant: p.variant, symbol: p.symbol, rank: p.rank, kind: p.kind, facts: p.facts,
                 display: pr.key === 'wholesale' ? 'Wholesale' : (pr.label || pr.key), summary: p.summary,
                 orgLabel: null })
  }
  // ACQUISITION OPERATIONS (Issue #452): only where the server granted this
  // login operator access for that client - never God Mode.
  for (const a of ctx?.acquisition_ops || []) {
    const p = PROGRAMS.acquisition_ops
    cards.push({ ...a, type: 'acquisition_ops', organization_id: 'acquisition-ops:' + a.organization_id,
                 client_organization_id: a.organization_id, program: 'acquisition_ops', variant: p.variant,
                 symbol: p.symbol, rank: p.rank, kind: a.hub ? 'EVOSYS PRO · ACQUISITION CONTROL CENTER' : p.kind,
                 facts: a.hub ? ['Import', 'Qualify', 'Assign', 'Cleaning clients'] : p.facts,
                 display: 'Acquisition Control Center',
                 summary: a.hub ? 'Master prospects, research, distribution and every cleaning client' : p.summary,
                 orgLabel: a.hub ? 'EvoSys Pro' : a.organization_name })
  }
  // VOLT MARKETING (#535): only where the server confirmed recruiting-staff
  // or owner access for that tenant.
  for (const v of ctx?.volt_contexts || []) {
    const p = PROGRAMS.volt
    cards.push({ ...v, type: 'volt_ops', organization_id: 'volt-ops:' + v.organization_id,
                 volt_organization_id: v.organization_id, program: 'volt', variant: p.variant,
                 symbol: p.symbol, rank: p.rank, kind: p.kind, facts: p.facts, display: 'VOLT Marketing',
                 summary: p.summary, orgLabel: v.access === 'viewer' ? 'View only' : null })
  }
  // BACK OFFICE AND EXECUTIVE SUITE are separate destinations on the same
  // launcher - only when the server says this person has them, never the
  // platform console (God Mode has its own home).
  if (ctx?.has_back_office && ctx.back_office_path && ctx.back_office_path !== '/god') {
    const p = PROGRAMS.back_office
    // ONE CARD PER BRAND (#272) for someone on more than one brand's sales
    // team, each naming its brand and carrying it, so the Back Office opens on
    // the brand they chose. One brand: the same single card as always.
    const dests = ctx.back_office_destinations || []
    const many = dests.length > 1
    for (const d of (many ? dests : [dests[0] || null])) {
      cards.push({ type: 'back_office', organization_id: many ? 'back-office:' + d.brand_sales_org_id : 'back-office',
                   path: (d && d.path) || ctx.back_office_path,
                   // Only when there is a choice to carry: a one-brand Back Office
                   // opens exactly as it always has.
                   platform_id: many ? d.platform_id : undefined, platform_name: many ? d.platform_name : undefined,
                   brand_sales_org_id: many ? d.brand_sales_org_id : undefined,
                   program: 'back_office', variant: p.variant, symbol: p.symbol, rank: p.rank, kind: p.kind, facts: p.facts,
                   display: 'Back Office', summary: p.summary, orgLabel: many ? (d.platform_name || d.label || null) : null })
    }
  }
  for (const ex of ctx?.executive_contexts || []) {
    const p = PROGRAMS.executive
    cards.push({ ...ex, organization_id: 'executive:' + ex.platform_id, program: 'executive', variant: p.variant,
                 symbol: p.symbol, rank: p.rank, kind: p.kind, facts: p.facts, display: 'Executive Suite',
                 summary: p.summary, orgLabel: ex.platform_name || null })
  }
  cards.sort((a, b) => a.rank - b.rank || String(a.display).localeCompare(String(b.display)))
  // NO DUPLICATES: a business that already opens as a workspace for this login
  // is not offered again as a preview (exact name match only).
  const held = new Set(workspaces.map(w => String(w.organization_name || '').trim().toLowerCase()))
  const previews = previewCards(ctx).filter(p => !held.has(String(p.display).trim().toLowerCase()))
  return { visible: cards, others, previews }
}

/** "Commercial Cleaning Blueprint — Daniel Almaguer" -> the business name,
 *  only when the tail is exactly the deal's contact. Never guesses beyond that. */
export function previewName(p) {
  const name = String(p?.company_name || '').trim()
  const contact = String(p?.contact_name || '').trim()
  const tail = ' — ' + contact
  return contact && name.endsWith(tail) && name.length > tail.length ? name.slice(0, -tail.length) : name
}

/**
 * PREVIEWS - a live demo of an OPEN deal (server `preview_contexts`, sales
 * managers only). Shown apart from the workspaces and labelled as a preview:
 * it opens the deal's existing demo link and is never a live business.
 * One card per deal; a deal that already has a workspace is never here.
 */
export function previewCards(ctx) {
  const seen = new Set()
  const out = []
  for (const p of ctx?.preview_contexts || []) {
    // A GOD-GRANTED PREVIEW (deal_preview_grants): one person may open one
    // deal's existing demo. The server sends only the grant id, the title, the
    // label and a signed-in path - never the demo's link - and re-checks the
    // grant on every open.
    if (p?.grant_id && p.path) {
      if (seen.has('grant:' + p.grant_id)) continue
      seen.add('grant:' + p.grant_id)
      out.push({ ...p, organization_id: 'grant:' + p.grant_id, program: 'preview', variant: 'preview',
                 symbol: '◇', rank: 20, kind: String(p.label || 'Authorized preview').toUpperCase(),
                 display: p.title || 'Preview', orgLabel: null, href: p.path,
                 summary: 'A demo you have been given permission to view. Not a live workspace.',
                 facts: [p.label || 'Authorized preview'] })
      continue
    }
    if (!p?.url || !p.opportunity_id || seen.has(p.opportunity_id)) continue
    seen.add(p.opportunity_id)
    const display = previewName(p)
    const facts = [p.stage_label, p.proposal_sent ? 'Proposal sent' : null,
                   p.demo_slot === 'platform' ? 'Platform demo' : 'Website concept'].filter(Boolean)
    out.push({ ...p, organization_id: 'preview:' + p.opportunity_id, program: 'preview', variant: 'preview', href: p.url,
               symbol: '◇', rank: 20, kind: 'PREVIEW · PROSPECT DEMO',
               display, orgLabel: p.contact_name && display !== p.company_name ? p.contact_name : null,
               summary: 'The demo built for this prospect. Not a live workspace: no customer data, not a customer yet.',
               facts })
  }
  return out.sort((a, b) => String(a.display).localeCompare(String(b.display)))
}

/** The server says this login's Program Access wears the BookaBoost look
 *  (/auth/my-contexts `brand`). Presentation only - it changes no card. */
export function isBookaBoostBrand(ctx) {
  return String(ctx?.brand?.theme || '').toLowerCase() === 'bookaboost'
}
