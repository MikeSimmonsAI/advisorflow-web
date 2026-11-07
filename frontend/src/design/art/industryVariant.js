/**
 * Which artwork a business shows, from what the SERVER recorded about it
 * (program, industry). A vocabulary of industries, never a list of clients.
 */
const INDUSTRY_VARIANT = [
  [/gold|jewel|coin|precious|bullion|pawn/i, 'gold'],
  [/insur/i, 'insurance'],
  [/real.?estate|land|property|wholesal|realty|home ?buy/i, 'wholesale'],
  [/energy|electric|power|solar|utility/i, 'energy'],
  [/clean|janitor|custodial/i, 'cleaning'],
  [/funeral|cemetery|memorial|mortuary/i, 'funeral'],
  [/roof/i, 'roofing'],
]
export function variantForIndustry(industry, fallback = 'general') {
  const s = String(industry || '')
  for (const [re, v] of INDUSTRY_VARIANT) if (re.test(s)) return v
  return fallback
}
