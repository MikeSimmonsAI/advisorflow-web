export function programCards(ctx) {
  const workspaces = ctx?.workspace_contexts || []
  const products = ctx?.product_contexts || []
  const program = workspaces.filter(w => w.is_program ||
    w.program_route === '/program/roofing' ||
    ['atlantis-light-power'].includes(w.organization_slug) ||
    ['energy', 'cleaning', 'insurance'].includes(w.industry))
  const others = workspaces.filter(w =>
    !program.some(p => p.organization_id === w.organization_id) &&
    !products.some(p => p.organization_id === w.organization_id))
  const visible = [
    ...program.map(w => ({
      ...w,
      kind: w.is_program ? 'SCI · FAMILY SERVICES' :
            w.program_route === '/program/roofing' ? 'ROOFING OPERATIONS' :
            w.industry === 'energy' ? 'ENERGY OPERATIONS' : 'BUSINESS OPERATIONS',
      display: w.is_program ? 'SCI' : w.program_route === '/program/roofing' ? 'Roofing' : w.organization_name,
      summary: w.is_program ? 'Families, conversations, appointments and outreach' :
               w.program_route === '/program/roofing' ? 'AI activity, homeowner replies and inspections' :
               w.industry === 'energy' ? 'Customers, enrollments and energy operations' :
               'Your dedicated business workspace',
    })),
    ...products.map(p => ({
      ...p, kind: 'PROPERTY ACQUISITION',
      display: 'Wholesale', summary: 'Acquisition command and deal operations',
    })),
  ]
  return { visible, others }
}

