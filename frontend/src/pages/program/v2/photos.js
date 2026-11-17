/**
 * Bundled, licensed workspace photography for Program Access V2.
 * Two crops per photo: `card` (720x450, 16:10) and `wide` (1600x440, banner).
 * Provenance: docs/redesign/program-access-v2/PHOTO_CREDITS.md
 */
const cards = import.meta.glob('./img/*-card.webp', { eager: true, query: '?url', import: 'default' })
const wides = import.meta.glob('./img/*-wide.webp', { eager: true, query: '?url', import: 'default' })

const byKey = (mods, suffix) => Object.fromEntries(Object.entries(mods).map(([p, url]) => [
  p.replace('./img/', '').replace(suffix, ''), url]))

const CARD = byKey(cards, '-card.webp')
const WIDE = byKey(wides, '-wide.webp')

export function cardPhoto(key) {
  return CARD[key] || CARD.business
}

export function widePhoto(key) {
  return WIDE[key] || CARD[key] || WIDE.business
}
