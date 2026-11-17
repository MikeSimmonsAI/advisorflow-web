/**
 * Bundled, licensed workspace photography for Program Access V2.
 * Crops: `card` (720x330, short and wide), `wide` (900x440, left side of the
 * featured banner) and, for some programs, `accent` (520x440, right side).
 * Provenance: docs/redesign/program-access-v2/PHOTO_CREDITS.md
 */
const cards = import.meta.glob('./img/*-card.webp', { eager: true, query: '?url', import: 'default' })
const wides = import.meta.glob('./img/*-wide.webp', { eager: true, query: '?url', import: 'default' })
const accents = import.meta.glob('./img/*-accent.webp', { eager: true, query: '?url', import: 'default' })

const byKey = (mods, suffix) => Object.fromEntries(Object.entries(mods).map(([p, url]) => [
  p.replace('./img/', '').replace(suffix, ''), url]))

const CARD = byKey(cards, '-card.webp')
const WIDE = byKey(wides, '-wide.webp')
const ACCENT = byKey(accents, '-accent.webp')

export function cardPhoto(key) {
  return CARD[key] || CARD.business
}

export function widePhoto(key) {
  return WIDE[key] || CARD[key] || WIDE.business
}

/** The optional right-hand picture in the featured banner (e.g. cleaning supplies). */
export function accentPhoto(key) {
  return ACCENT[key] || null
}
