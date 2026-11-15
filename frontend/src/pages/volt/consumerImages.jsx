/* Consumer Leads (CoveragePath) website photography, kept in ONE place.
 *
 * Every photo is bundled with the site (frontend/public/brands/coveragepath/),
 * never hotlinked. Each comes in two widths, as AVIF with a WebP fallback, and
 * carries its intrinsic width/height so the browser reserves the space before
 * the file arrives (no layout shift).
 *
 * Provenance (all under the Pexels License, https://www.pexels.com/license/ :
 * free for commercial use, no attribution required, may be modified; people
 * must not be shown as endorsing anything). Originals downloaded Nov 15, 2026
 * and recorded in docs/volt/design/consumer_site_photos.md.
 *  - hero-couple:   "Happy Couple Embracing on Sofa" by Centre for Ageing Better
 *                   https://www.pexels.com/photo/happy-couple-embracing-on-sofa-12884986/
 *  - family-laptop: "Smiling Grandma with Grandchildren" by Kampus Production
 *                   https://www.pexels.com/photo/smiling-grandma-with-grandchildren-6248452/
 *  - couple-call:   "Elderly Couple Sitting on the Sofa" by Yan Krukau
 *                   https://www.pexels.com/photo/elderly-couple-sitting-on-the-sofa-6817697/
 *
 * Rules: the people shown are not customers and the page never names, quotes or
 * attributes a result to them. No doctors, insurance cards or government marks.
 */
const base = '/brands/coveragepath'

function photo(name, widths, [w, h], sizes, alt, position = 'center') {
  const set = (ext) => widths.map((x) => `${base}/${name}-${x}.${ext} ${x}w`).join(', ')
  return {
    src: `${base}/${name}-${widths[widths.length - 1]}.webp`,
    avif: set('avif'), webp: set('webp'), sizes, width: w, height: h, alt, position,
  }
}

export const CONSUMER_PHOTOS = {
  hero: photo('hero-couple', [480, 800], [800, 1000], '(max-width: 500px) 300px, (max-width: 850px) 340px, 390px',
    'An older couple sitting close together on a sofa at home, smiling at each other.', '50% 30%'),
  band: [
    photo('family-laptop', [640, 1100], [1100, 734], '(max-width: 850px) 100vw, 560px',
      'A grandmother on the sofa with her two grandchildren, looking at a laptop together and smiling.', '50% 40%'),
    photo('couple-call', [640, 1100], [1100, 733], '(max-width: 850px) 100vw, 560px',
      'An older couple relaxing on their sofa at home, smiling and waving during a video call on a laptop.', '50% 40%'),
  ],
  credit: 'Photos: Centre for Ageing Better, Kampus Production and Yan Krukau on Pexels. The people shown are not customers.',
}

// <Photo> renders AVIF first, then WebP; `eager` is for the one above-the-fold image.
export function Photo({ p, eager = false, className }) {
  return (
    <picture>
      <source type="image/avif" srcSet={p.avif} sizes={p.sizes} />
      <source type="image/webp" srcSet={p.webp} sizes={p.sizes} />
      <img className={className} src={p.src} alt={p.alt} width={p.width} height={p.height}
        loading={eager ? 'eager' : 'lazy'} decoding="async"
        fetchpriority={eager ? 'high' : undefined} style={{ objectPosition: p.position }} data-photo />
    </picture>
  )
}
