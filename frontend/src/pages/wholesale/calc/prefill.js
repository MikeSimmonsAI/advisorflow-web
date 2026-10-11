/** Calculator pre-fill from an EvoSense property detail payload. Only the
 *  numbers the record actually has: an ARV only when one was established
 *  (never the tax value), the seller's own asking price, the living area. */
export function prefillFromProperty(d) {
  if (!d) return {}
  const eco = d.economics || {}
  const line = (label) => (eco.lines || []).find((l) => l.label === label) || {}
  const phys = (d.facts && d.facts.physical) || {}
  const p = d.property || {}
  return {
    arv: line('ARV').value ?? null,
    asking: line('Seller asking').value ?? null,
    taxValue: line('Appraisal District Tax Value').value ?? null,
    sqft: phys.square_feet ?? null,
    address: [p.address, p.city].filter(Boolean).join(', ') || null,
  }
}
