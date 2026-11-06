/* VOLT homepage imagery, in ONE place so real VOLT photos can replace these
 * later by changing this file only.
 *
 * Today's images are ILLUSTRATIVE: they are the generated people images from
 * Mike's approved homepage mockup (Nov 6, 2026), cropped into separate assets
 * and upscaled. They are not photos of VOLT staff, so alt text describes the
 * scene and nothing on the page names or quotes the people in them.
 *
 * Rules kept here on purpose:
 *  - General homepage imagery shows people, connection and growth. Travel and
 *    door-to-door scenes are reserved for the Fiber opportunity (the mockup's
 *    road inset and door scene are not used on the general page).
 *  - Every entry carries its intrinsic width/height so the browser reserves
 *    the space before the file arrives (no layout shift).
 *
 * Files: frontend/public/brands/volt/people/<name>-<width>.webp
 */
const base = '/brands/volt/people'

function img(name, sizes, [w1, h1], [w2, h2], alt, position = 'center') {
  return {
    src: `${base}/${name}-${w1}.webp`,
    srcSet: `${base}/${name}-${w1}.webp ${w1}w, ${base}/${name}-${w2}.webp ${w2}w`,
    sizes, width: w2, height: h2, alt, position,
  }
}

export const VOLT_IMAGES = {
  hero: img('hero', '(max-width: 760px) 100vw, 560px', [720, 519], [1200, 866],
    'Illustrative photo: a group of smiling young professionals laughing together outdoors, one holding a tablet.', '50% 30%'),
  // Opportunity cards. The Fiber card may show neighborhood field work; the
  // ACA card does not show door-to-door work.
  cards: {
    fiber: img('fiber', '(max-width: 760px) 100vw, 560px', [640, 173], [1200, 324],
      'Illustrative photo: two smiling team members walking through a sunny residential neighborhood.', '50% 20%'),
    aca: img('aca', '(max-width: 760px) 100vw, 560px', [640, 267], [1100, 460],
      'Illustrative photo: a smiling professional holding a tablet outdoors.', '40% 25%'),
  },
  // Beneath the three company-wide reasons, in the same order.
  reasons: [
    img('connect', '(max-width: 760px) 100vw, 380px', [480, 240], [900, 450],
      'Illustrative photo: three colleagues smiling and taking notes together at a table.'),
    img('train', '(max-width: 760px) 100vw, 380px', [480, 236], [900, 443],
      'Illustrative photo: a team member explaining something to colleagues over open notebooks.'),
    img('grow', '(max-width: 760px) 100vw, 380px', [480, 214], [900, 401],
      'Illustrative photo: teammates laughing together outdoors.', '50% 30%'),
  ],
  banner: img('banner', '100vw', [900, 265], [1800, 531],
    'Illustrative photo: a group of friends and coworkers laughing together outdoors.', '60% 30%'),
}
