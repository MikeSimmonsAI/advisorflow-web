/* BOOKABOOST PRODUCT BRAND (exploration, Oct 29).
 *
 * BookaBoost is the platform. "OLP - Overlooked Properties" is the product
 * identity being explored on top of it, endorsed "Powered by BookaBoost".
 * Nothing here changes tenants, accounts, sign-in or routing: it only chooses
 * which face the BookaBoost theme shows.
 *
 * Switch without a code change: build with VITE_BOOKABOOST_PRODUCT_BRAND=bookaboost
 * to show the plain BookaBoost sign-in again (the approved #432 page is kept).
 */
export const OLP = Object.freeze({
  key: 'olp',
  short: 'OLP',
  name: 'Overlooked Properties',
  endorsement: 'Powered by BookaBoost',
  // Proposed OLP brand assets supplied by the owner (Oct 29) - awaiting final
  // approval; not a registered trademark. docs/redesign/olp/OLP_BRAND_ASSETS_README.md
  logo: '/brands/olp/OLP_logo_primary_dark.svg',            // stacked, dark grounds (920 x 486)
  logoOnLight: '/brands/olp/OLP_logo_primary_light.svg',    // stacked, light grounds
  horizontal: '/brands/olp/OLP_logo_horizontal_dark.svg',   // app header / sidebar (1170 x 310)
  horizontalOnLight: '/brands/olp/OLP_logo_horizontal_light.svg',
  icon: '/brands/olp/OLP_icon.svg',                         // product tile / favicon (520 x 420)
})

export const PRODUCT_BRANDS = Object.freeze(['olp', 'bookaboost'])

/** 'olp' unless the build says 'bookaboost'. */
export function bookaboostProductBrand(env = (typeof import.meta !== 'undefined' && import.meta.env) || {}) {
  const v = String(env.VITE_BOOKABOOST_PRODUCT_BRAND || '').trim().toLowerCase()
  return v === 'bookaboost' ? 'bookaboost' : 'olp'
}
