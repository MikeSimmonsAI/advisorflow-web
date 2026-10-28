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
  insurance: { rank: 6, variant: 'insurance', symbol: '⛨', kind: 'INSURANCE AGENCY',
               summary: 'Prospects, AI conversations, appointments, applications and policies',
               facts: ['Prospects', 'Applications', 'Policies'] },
  cleaning: { rank: 7, variant: 'cleaning', symbol: '✦', kind: 'COMMERCIAL CLEANING',
              summary: 'Prospects, follow-up, booked walkthroughs and reports',
              facts: ['Prospects', 'Follow-up', 'Walkthroughs'] },
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
  if (w.is_program || w.experience === 'sci') return 'sci'
  if (w.program_route === '/program/roofing' || w.experience === 'roofing') return 'roofing'
  if (w.experience === 'insurance' || w.industry === 'insurance') return 'insurance'
  if (w.experience === 'energy' || w.industry === 'energy') return 'energy'
  if (w.industry === 'funeral' || w.industry === 'cemetery') return 'funeral'
  if (w.industry === 'cleaning') return 'cleaning'
  return null
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
  for (const w of workspaces) {
    if (isProductHost(w)) continue
    // A workspace whose kind of business has no dedicated identity yet still
    // gets a full card, in the neutral EvoSys look (Mike, 2026-10-21) — never
    // a plain row. Only the presentation is neutral; the workspace and its
    // destination are exactly what the server listed.
    const program = programOf(w) || 'business'
    const p = PROGRAMS[program]
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
    if (!p?.url || !p.opportunity_id || seen.has(p.opportunity_id)) continue
    seen.add(p.opportunity_id)
    const display = previewName(p)
    const facts = [p.stage_label, p.proposal_sent ? 'Proposal sent' : null,
                   p.demo_slot === 'platform' ? 'Platform demo' : 'Website concept'].filter(Boolean)
    out.push({ ...p, organization_id: 'preview:' + p.opportunity_id, program: 'preview', variant: 'preview',
               symbol: '◇', rank: 20, kind: 'PREVIEW · PROSPECT DEMO',
               display, orgLabel: p.contact_name && display !== p.company_name ? p.contact_name : null,
               summary: 'The demo built for this prospect. Not a live workspace: no customer data, not a customer yet.',
               facts })
  }
  return out.sort((a, b) => String(a.display).localeCompare(String(b.display)))
}
