/**
 * EvoSys Pro design tokens.
 *
 * READ, NOT CHOSEN. Every colour here comes from
 * `app/services/brand_config.py::FROZEN_BRAND_DEFAULTS["evosyspro"]` — accent
 * #087cff, secondary #22a3ff, positive #19d67c, ground #040812. The backend is
 * the brand authority; this file mirrors it so a phone and a browser are
 * recognisably the same product, and a brand change lands in one place.
 *
 * The surfaces, borders and text ramps below are NOT in brand_config, because
 * brand_config deliberately stops at values and leaves "what the theme looks
 * like" to a stylesheet (its own comment says so). This is that stylesheet, for
 * a phone: a dark ground with lifted surfaces, sized for a hand in daylight
 * rather than a desktop in an office.
 */

export const palette = {
  // ── brand (from brand_config) ──
  accent: '#087cff',
  accentSoft: '#22a3ff',
  positive: '#19d67c',
  ground: '#040812',

  // ── surfaces: each step is a real elevation, not decoration ──
  surface: '#0b1220',        // cards on the ground
  surfaceRaised: '#111a2c',  // sheets, headers, pressed rows
  surfaceSunken: '#070d18',  // inset wells, inputs

  border: '#1c2740',
  borderStrong: '#2b3a5c',

  // ── text ramp ──
  // Three levels, no more. A fourth grey is how a dark UI ends up with body
  // copy nobody can read in a car park at noon.
  text: '#eaf1ff',
  textMuted: '#9fb0cc',
  textFaint: '#6b7c99',

  // ── status ──
  // Mapped to the backend severity vocabulary in src/vocab/severity.ts and
  // never invented per screen.
  warning: '#f5a623',
  danger: '#ff5c6c',
  neutral: '#7b8db0',

  overlay: 'rgba(4, 8, 18, 0.72)',
  transparent: 'transparent',
} as const;

/**
 * Spacing on a 4pt grid. Field use pushes toward the larger end of every
 * choice: a rep taps this standing up, in gloves, in sunlight.
 */
export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radius = {
  sm: 8,
  md: 12,
  lg: 18,
  pill: 999,
} as const;

/**
 * Type scale. `body` is 16 rather than 14 on purpose — 14 is a desktop size,
 * and a desktop size on a phone is the single most common reason a "mobile
 * app" reads as a shrunken web page.
 */
export const type = {
  display: { fontSize: 30, fontWeight: '700' as const, letterSpacing: -0.4 },
  title: { fontSize: 22, fontWeight: '700' as const, letterSpacing: -0.2 },
  heading: { fontSize: 18, fontWeight: '600' as const },
  body: { fontSize: 16, fontWeight: '400' as const },
  bodyStrong: { fontSize: 16, fontWeight: '600' as const },
  label: { fontSize: 13, fontWeight: '600' as const, letterSpacing: 0.3 },
  caption: { fontSize: 13, fontWeight: '400' as const },
  micro: { fontSize: 11, fontWeight: '600' as const, letterSpacing: 0.6 },
} as const;

/**
 * The minimum tappable square. 44 is Apple's floor and Android's is 48; the
 * larger one wins because a missed tap in the field costs more than a few
 * points of density.
 */
export const HIT_SIZE = 48;

export const shadow = {
  card: {
    shadowColor: '#000',
    shadowOpacity: 0.35,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 4,
  },
} as const;
