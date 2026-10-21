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


/** Which program a server-listed workspace runs, or null (shown under "Other"). */
export function programOf(w) {
  if (!w) return null
  if (w.is_program || w.experience === 'sci') return 'sci'
  if (w.program_route === '/program/roofing' || w.experience === 'roofing') return 'roofing'
  if (w.experience === 'insurance' || w.industry === 'insurance') return 'insurance'
  if (w.experience === 'energy' || w.industry === 'energy') return 'energy'
  if (w.industry === 'funeral' || w.industry === 'cemetery') return 'funeral'
  if (w.industry === 'cleaning') return 'business'
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
    const program = programOf(w)
    if (!program) { others.push(w); continue }
    const p = PROGRAMS[program]
    cards.push({ ...w, program, variant: p.variant, symbol: p.symbol, rank: p.rank, kind: p.kind, facts: p.facts,
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
    cards.push({ type: 'back_office', organization_id: 'back-office', path: ctx.back_office_path,
                 program: 'back_office', variant: p.variant, symbol: p.symbol, rank: p.rank, kind: p.kind, facts: p.facts,
                 display: 'Back Office', summary: p.summary, orgLabel: null })
  }
  for (const ex of ctx?.executive_contexts || []) {
    const p = PROGRAMS.executive
    cards.push({ ...ex, organization_id: 'executive:' + ex.platform_id, program: 'executive', variant: p.variant,
                 symbol: p.symbol, rank: p.rank, kind: p.kind, facts: p.facts, display: 'Executive Suite',
                 summary: p.summary, orgLabel: ex.platform_name || null })
  }
  cards.sort((a, b) => a.rank - b.rank || String(a.display).localeCompare(String(b.display)))
  return { visible: cards, others }
}
