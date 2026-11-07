/**
 * Industry artwork for Program Access cards (UI-REDESIGN lane).
 *
 * Each scene is drawn for ONE kind of business so the cards read at a glance:
 *   gold      - gold, jewelry & coins: night-blue ridgelines, gold coins, bar, ring
 *   insurance - a family home under an umbrella of cover, policy and shield
 *   wholesale - a residential street, SOLD sign, keys and a property pin
 *   energy    - power lines at dusk, solar array, lit homes, a bolt
 *   cleaning  - a gleaming glass office building, squeegee and spray bottle
 *   funeral   - a quiet memorial garden at dawn: chapel, cypress trees, lilies
 *
 * Local, self-contained SVG in the shared 400x180 frame - no image URLs, no
 * logos, no stock photos. Decorative only (aria-hidden); the card names the
 * business in text. Presentation only: nothing here decides access.
 */

function Sky({ id, top, bottom, glow = '#ffffff', glowAt = [0.82, 0.1], glowOpacity = 0.5 }) {
  return (
    <>
      <defs>
        <linearGradient id={id + 'sky'} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={top} />
          <stop offset="1" stopColor={bottom} />
        </linearGradient>
        <radialGradient id={id + 'glow'} cx={glowAt[0]} cy={glowAt[1]} r=".6">
          <stop offset="0" stopColor={glow} stopOpacity={glowOpacity} />
          <stop offset="1" stopColor={glow} stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="400" height="180" fill={`url(#${id}sky)`} />
      <rect width="400" height="180" fill={`url(#${id}glow)`} />
    </>
  )
}

function Svg({ children }) {
  return (
    <svg viewBox="0 0 400 180" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false"
         className="ev-art" role="presentation">{children}</svg>
  )
}

// ── GOLD, JEWELRY & COINS ──────────────────────────────────────────────────
export function GoldScene({ id }) {
  const stars = [[28, 22, 1.2], [70, 40, .8], [118, 18, 1], [160, 46, .7], [214, 24, 1.1], [52, 64, .6], [250, 52, .7], [138, 70, .5]]
  const coin = (x, y, k) => (
    <g key={k} transform={`translate(${x} ${y})`}>
      <ellipse cx="0" cy="6" rx="26" ry="8" fill="#7a5a12" />
      <rect x="-26" y="0" width="52" height="6" fill={`url(#${id}goldSide)`} />
      <ellipse cx="0" cy="0" rx="26" ry="8" fill={`url(#${id}gold)`} />
      <ellipse cx="0" cy="0" rx="19" ry="5.6" fill="none" stroke="#a37d22" strokeWidth="1.1" opacity=".8" />
    </g>
  )
  return (
    <Svg>
      <Sky id={id} top="#071327" bottom="#1b365f" glow="#e6c879" glowAt={[0.78, 0.18]} glowOpacity={0.42} />
      <defs>
        <linearGradient id={id + 'gold'} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fbeec2" /><stop offset=".45" stopColor="#e2bf62" /><stop offset="1" stopColor="#a8801f" />
        </linearGradient>
        <linearGradient id={id + 'goldSide'} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#8a6a1f" /><stop offset=".5" stopColor="#d8b45a" /><stop offset="1" stopColor="#7a5a12" />
        </linearGradient>
        <linearGradient id={id + 'bar'} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#f6e3a1" /><stop offset="1" stopColor="#b48a2a" />
        </linearGradient>
        <linearGradient id={id + 'ridge'} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#dfbf6e" stopOpacity="0" /><stop offset=".5" stopColor="#dfbf6e" /><stop offset="1" stopColor="#dfbf6e" stopOpacity="0" />
        </linearGradient>
      </defs>
      {stars.map(([x, y, s], i) => <circle key={i} cx={x} cy={y} r={s} fill="#f3e2b3" opacity=".8" />)}
      {/* Blue Ridge layers */}
      <path d="M0 132 L40 104 L78 118 L128 82 L168 104 L214 74 L262 102 L300 86 L346 110 L400 92 V180 H0Z" fill="#2a4a78" opacity=".75" />
      <path d="M0 132 L40 104 L78 118 L128 82 L168 104 L214 74 L262 102 L300 86 L346 110 L400 92" fill="none" stroke={`url(#${id}ridge)`} strokeWidth="1.6" />
      <path d="M0 150 L56 124 L104 140 L160 112 L218 136 L270 118 L330 140 L400 122 V180 H0Z" fill="#1b365f" />
      <path d="M0 168 L70 148 L140 160 L210 144 L290 162 L360 150 L400 156 V180 H0Z" fill="#0d1f3d" />
      {/* gold bar */}
      <g transform="translate(300 118)">
        <path d="M-34 22 L-24 0 H24 L34 22 Z" fill={`url(#${id}bar)`} />
        <path d="M-24 0 H24 L20 6 H-20 Z" fill="#fff6d8" opacity=".55" />
        <path d="M-34 22 H34 V27 H-34Z" fill="#8a6a1f" />
        <rect x="-11" y="9" width="22" height="5" rx="1.5" fill="#a37d22" opacity=".55" />
      </g>
      {/* coin stack */}
      {[0, 1, 2, 3].map(i => coin(236, 146 - i * 8, 'c' + i))}
      {coin(266, 150, 'c9')}
      {/* ring with stone */}
      <g transform="translate(330 58)">
        <ellipse cx="0" cy="14" rx="20" ry="20" fill="none" stroke={`url(#${id}gold)`} strokeWidth="6" />
        <ellipse cx="0" cy="14" rx="20" ry="20" fill="none" stroke="#fff6d8" strokeWidth="1.2" opacity=".5" strokeDasharray="10 60" />
        <path d="M-9 -8 L-4 -15 H4 L9 -8 L0 2 Z" fill="#e8f4ff" />
        <path d="M-9 -8 H9 M-4 -15 L0 -8 L4 -15 M0 -8 L0 2" stroke="#9cc4e8" strokeWidth=".9" fill="none" />
        <path d="M-6 -4 L-12 -2 M6 -4 L12 -2" stroke="#e2bf62" strokeWidth="2.4" strokeLinecap="round" />
      </g>
      {/* sparkles */}
      {[[372, 34, 1], [296, 92, .7], [352, 100, .8], [210, 116, .6]].map(([x, y, s], i) => (
        <path key={i} transform={`translate(${x} ${y}) scale(${s})`} d="M0 -8 L1.8 -1.8 L8 0 L1.8 1.8 L0 8 L-1.8 1.8 L-8 0 L-1.8 -1.8Z" fill="#fbeec2" />
      ))}
    </Svg>
  )
}

// ── INSURANCE ──────────────────────────────────────────────────────────────
export function InsuranceScene({ id }) {
  return (
    <Svg>
      <Sky id={id} top="#dff3f6" bottom="#a9dbe3" glow="#ffffff" glowAt={[0.2, 0.05]} glowOpacity={0.7} />
      {/* skyline */}
      <g fill="#8ccad4" opacity=".6">
        <rect x="16" y="78" width="22" height="70" /><rect x="42" y="62" width="18" height="86" /><rect x="64" y="88" width="26" height="60" />
        <rect x="318" y="70" width="20" height="78" /><rect x="342" y="56" width="24" height="92" /><rect x="370" y="84" width="22" height="64" />
      </g>
      <path d="M0 150 C 90 136 170 140 240 148 S 350 156 400 142 V180 H0Z" fill="#7fc4cf" />
      <path d="M0 164 C 100 156 200 160 280 166 S 370 170 400 162 V180 H0Z" fill="#5fb3c1" />
      {/* umbrella of cover */}
      <g transform="translate(200 54)">
        <path d="M-92 32 Q 0 -54 92 32 Q 69 18 46 32 Q 23 18 0 32 Q -23 18 -46 32 Q -69 18 -92 32Z" fill="#0d6f7d" />
        <path d="M-92 32 Q -69 18 -46 32 Q -23 18 0 32 Q -40 -30 -92 32Z" fill="#1595a6" />
        <path d="M0 32 Q 23 18 46 32 Q 69 18 92 32 Q 40 -30 0 32Z" fill="#1595a6" opacity=".8" />
        <rect x="-1.6" y="-30" width="3.2" height="12" rx="1.6" fill="#07434c" />
      </g>
      {/* home + family under it */}
      <g transform="translate(200 146)">
        <rect x="-38" y="-46" width="76" height="46" rx="3" fill="#ffffff" />
        <path d="M-46 -42 L0 -74 L46 -42Z" fill="#07434c" />
        <rect x="-8" y="-24" width="16" height="24" rx="2" fill="#1595a6" />
        <rect x="-30" y="-36" width="14" height="11" rx="1.5" fill="#d9f2f5" /><rect x="16" y="-36" width="14" height="11" rx="1.5" fill="#d9f2f5" />
      </g>
      <g fill="#07434c">
        <circle cx="270" cy="114" r="6" /><path d="M260 146 C 260 128 264 122 270 122 C 276 122 280 128 280 146Z" />
        <circle cx="290" cy="120" r="5" fill="#0d6f7d" /><path d="M282 146 C 282 132 286 127 290 127 C 294 127 298 132 298 146Z" fill="#0d6f7d" />
        <circle cx="281" cy="130" r="3.6" fill="#1595a6" /><path d="M275 146 C 275 138 278 135 281 135 C 284 135 287 138 287 146Z" fill="#1595a6" />
      </g>
      {/* policy card + shield */}
      <g transform="translate(70 70)">
        <rect width="62" height="72" rx="8" fill="#ffffff" />
        <rect x="10" y="12" width="30" height="5" rx="2.5" fill="#07434c" />
        <rect x="10" y="24" width="42" height="3" rx="1.5" fill="#cdeaee" /><rect x="10" y="31" width="36" height="3" rx="1.5" fill="#cdeaee" />
        <rect x="10" y="38" width="40" height="3" rx="1.5" fill="#cdeaee" />
        <path d="M31 48 L44 52 V58 C 44 64 38 68 31 70 C 24 68 18 64 18 58 V52Z" fill="#1595a6" />
        <path d="M25 58 l4 4 l8 -8" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </g>
    </Svg>
  )
}

// ── WHOLESALE / REAL ESTATE ────────────────────────────────────────────────
export function PropertyScene({ id }) {
  const house = (x, y, w, h, roof, wall, k) => (
    <g key={k} transform={`translate(${x} ${y})`}>
      <rect x={-w / 2} y={-h} width={w} height={h} rx="2" fill={wall} />
      <path d={`M${-w / 2 - 6} ${-h + 2} L0 ${-h - w * 0.5} L${w / 2 + 6} ${-h + 2}Z`} fill={roof} />
      <rect x="-6" y={-h * 0.5} width="12" height={h * 0.5} rx="1.5" fill="#0b4d31" />
      <rect x={-w / 2 + 6} y={-h * 0.82} width="11" height="10" rx="1.5" fill="#fff6d6" />
      <rect x={w / 2 - 17} y={-h * 0.82} width="11" height="10" rx="1.5" fill="#fff6d6" />
    </g>
  )
  return (
    <Svg>
      <Sky id={id} top="#e4f6ec" bottom="#bfe8d2" glowAt={[0.85, 0.1]} glowOpacity={0.8} />
      <circle cx="344" cy="38" r="18" fill="#fff6cf" />
      {/* trees behind */}
      {[[38, 112, 20], [118, 104, 16], [282, 108, 18], [374, 114, 16]].map(([x, y, r], i) => (
        <g key={i}><rect x={x - 2} y={y} width="4" height="26" fill="#3d6b52" /><circle cx={x} cy={y} r={r} fill="#5fae84" opacity=".85" /></g>
      ))}
      {/* street */}
      <path d="M0 142 H400 V180 H0Z" fill="#9fd4b7" />
      <path d="M0 156 H400 V180 H0Z" fill="#6b8f7c" />
      <path d="M0 167 H400" stroke="#e8f5ee" strokeWidth="2" strokeDasharray="14 12" />
      {house(78, 142, 58, 40, '#22a06b', '#ffffff', 'h1')}
      {house(176, 142, 76, 52, '#0b4d31', '#f7f3ea', 'h2')}
      {house(270, 142, 54, 38, '#137a4f', '#ffffff', 'h3')}
      {/* SOLD sign */}
      <g transform="translate(222 108)">
        <rect x="-1.5" y="0" width="3" height="34" fill="#5b4a2e" />
        <rect x="-2" y="2" width="30" height="3" fill="#5b4a2e" />
        <rect x="4" y="6" width="26" height="15" rx="2" fill="#c0392b" />
        <text x="17" y="17" textAnchor="middle" fontSize="8.5" fontWeight="800" fill="#fff" fontFamily="Inter, system-ui, sans-serif">SOLD</text>
      </g>
      {/* key tag + map pin */}
      <g transform="translate(324 66)">
        <circle cx="0" cy="0" r="9" fill="none" stroke="#0b4d31" strokeWidth="3.4" />
        <path d="M7 6 L26 25 M18 17 L23 12 M22 21 L26 17" stroke="#0b4d31" strokeWidth="3.4" strokeLinecap="round" />
      </g>
      <g transform="translate(176 50)">
        <path d="M0 26 C -14 10 -14 -2 0 -6 C 14 -2 14 10 0 26Z" fill="#22a06b" />
        <circle cx="0" cy="4" r="4.6" fill="#ffffff" />
      </g>
    </Svg>
  )
}

// ── ENERGY ─────────────────────────────────────────────────────────────────
export function PowerScene({ id }) {
  const tower = (x, s, k) => (
    <g key={k} transform={`translate(${x} 150) scale(${s})`} stroke="#2b1d00" strokeWidth="2" fill="none" strokeLinejoin="round">
      <path d="M-16 0 L0 -104 L16 0" /><path d="M-11 -30 L11 -30 M-7 -58 L7 -58 M-4 -80 L4 -80" />
      <path d="M-11 -30 L7 -58 M11 -30 L-7 -58 M-7 -58 L4 -80 M7 -58 L-4 -80" strokeWidth="1.2" />
      <path d="M-26 -88 L26 -88 M-20 -70 L20 -70" strokeWidth="2.6" />
    </g>
  )
  return (
    <Svg>
      <Sky id={id} top="#1d2f55" bottom="#f5b335" glow="#ffd56b" glowAt={[0.5, 1.05]} glowOpacity={0.75} />
      <circle cx="200" cy="146" r="40" fill="#ffd56b" opacity=".55" />
      {/* wires */}
      <g stroke="#2b1d00" strokeWidth="1.1" fill="none" opacity=".8">
        <path d="M46 62 Q 120 86 194 66 Q 270 86 352 62" /><path d="M50 80 Q 124 104 198 84 Q 272 104 348 80" />
        <path d="M0 70 Q 20 66 46 62" /><path d="M352 62 Q 380 60 400 64" />
      </g>
      {tower(60, 1, 't1')}{tower(196, 0.92, 't2')}{tower(340, 1, 't3')}
      {/* ground, lit homes */}
      <path d="M0 150 C 80 140 160 146 240 150 S 350 156 400 146 V180 H0Z" fill="#3b2a07" />
      {[[108, 150, 30], [146, 152, 24], [258, 150, 28], [296, 152, 22]].map(([x, y, w], i) => (
        <g key={i} transform={`translate(${x} ${y})`}>
          <rect x={-w / 2} y={-w * 0.62} width={w} height={w * 0.62} fill="#241800" />
          <path d={`M${-w / 2 - 3} ${-w * 0.6} L0 ${-w * 1.05} L${w / 2 + 3} ${-w * 0.6}Z`} fill="#1a1100" />
          <rect x={-w / 4} y={-w * 0.45} width={w / 5} height={w / 6} fill="#ffd56b" />
          <rect x={w / 10} y={-w * 0.45} width={w / 5} height={w / 6} fill="#ffe9a3" />
        </g>
      ))}
      {/* solar array */}
      <g transform="translate(20 152)">
        {[0, 1, 2].map(i => (
          <g key={i} transform={`translate(${i * 26} 0)`}>
            <path d="M0 0 L8 -14 H30 L22 0Z" fill="#1d3f7a" stroke="#9cc4ff" strokeWidth=".7" />
            <path d="M4 -7 H26 M11 0 L19 -14" stroke="#9cc4ff" strokeWidth=".6" />
          </g>
        ))}
      </g>
      {/* bolt medallion */}
      <g transform="translate(366 24)">
        <circle r="16" fill="#f2a516" stroke="#fff3c4" strokeWidth="2" />
        <path d="M3 -10 L-6 2 H0 L-3 11 L7 -2 H1 Z" fill="#ffffff" />
      </g>
    </Svg>
  )
}

// ── COMMERCIAL CLEANING ────────────────────────────────────────────────────
export function CleanScene({ id }) {
  const spark = (x, y, s, k) => (
    <path key={k} transform={`translate(${x} ${y}) scale(${s})`} d="M0 -9 L2.2 -2.2 L9 0 L2.2 2.2 L0 9 L-2.2 2.2 L-9 0 L-2.2 -2.2Z" fill="#ffffff" />
  )
  return (
    <Svg>
      <Sky id={id} top="#e8f7fe" bottom="#b9e3f6" glowAt={[0.72, 0.1]} glowOpacity={0.9} />
      <defs>
        <linearGradient id={id + 'glass'} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#d9f1fc" /><stop offset=".5" stopColor="#8fd0ef" /><stop offset="1" stopColor="#4fa9d6" />
        </linearGradient>
      </defs>
      {/* glass office tower */}
      <g transform="translate(140 18)">
        <rect width="124" height="150" rx="4" fill="#0b4a6b" />
        {[...Array(6)].map((_, r) => [...Array(4)].map((__, c) => (
          <rect key={r + '-' + c} x={8 + c * 29} y={8 + r * 23} width="24" height="18" rx="1.5" fill={`url(#${id}glass)`} opacity={c === 1 && r < 3 ? 1 : 0.9} />
        )))}
        <path d="M30 8 L52 8 L18 150 L-4 150Z" fill="#ffffff" opacity=".18" />
        <path d="M78 8 L92 8 L66 150 L52 150Z" fill="#ffffff" opacity=".12" />
      </g>
      <path d="M0 160 H400 V180 H0Z" fill="#8ccbe8" />
      {/* squeegee on a pole, wiping */}
      <g transform="translate(204 64) rotate(-18)">
        <rect x="-1.5" y="0" width="3" height="56" rx="1.5" fill="#0b4a6b" />
        <rect x="-16" y="-4" width="32" height="6" rx="3" fill="#0e95d4" />
      </g>
      <path d="M176 52 Q 204 40 234 54" stroke="#ffffff" strokeWidth="5" strokeLinecap="round" fill="none" opacity=".85" />
      {/* spray bottle + bucket */}
      <g transform="translate(86 104)">
        <rect x="0" y="16" width="24" height="40" rx="7" fill="#0e95d4" />
        <rect x="5" y="4" width="14" height="14" rx="3" fill="#0b4a6b" />
        <path d="M19 8 L36 4 L36 12 Z" fill="#0b4a6b" />
        <circle cx="44" cy="4" r="2.6" fill="#fff" /><circle cx="50" cy="9" r="2" fill="#fff" /><circle cx="48" cy="-1" r="1.6" fill="#fff" />
      </g>
      <g transform="translate(316 128)">
        <path d="M-20 0 H20 L16 32 H-16Z" fill="#0e95d4" />
        <path d="M-20 0 Q 0 -22 20 0" stroke="#0b4a6b" strokeWidth="2.4" fill="none" />
        <ellipse cx="0" cy="0" rx="20" ry="4" fill="#d9f1fc" />
      </g>
      {spark(126, 40, 0.8, 's1')}{spark(282, 34, 1, 's2')}{spark(250, 100, .7, 's3')}{spark(356, 64, .9, 's4')}{spark(176, 128, .6, 's5')}
    </Svg>
  )
}

// ── FUNERAL & MEMORIAL SERVICES ────────────────────────────────────────────
export function MemorialScene({ id }) {
  const cypress = (x, h, k) => (
    <g key={k} transform={`translate(${x} 146)`}>
      <rect x="-1.6" y="-8" width="3.2" height="10" fill="#3a3122" />
      <path d={`M0 ${-h} C 9 ${-h * 0.7} 10 ${-h * 0.25} 0 -6 C -10 ${-h * 0.25} -9 ${-h * 0.7} 0 ${-h}Z`} fill="#285347" />
    </g>
  )
  return (
    <Svg>
      <Sky id={id} top="#eef1ea" bottom="#d8e3d4" glow="#f6e7c4" glowAt={[0.5, 0.42]} glowOpacity={0.95} />
      <circle cx="200" cy="80" r="34" fill="#f8ecd0" opacity=".9" />
      {/* rolling garden */}
      <path d="M0 132 C 80 116 150 122 220 130 S 340 140 400 124 V180 H0Z" fill="#a9c2a6" />
      <path d="M0 152 C 90 140 180 146 260 154 S 360 160 400 148 V180 H0Z" fill="#7fa384" />
      {/* chapel */}
      <g transform="translate(200 132)">
        <rect x="-30" y="-44" width="60" height="44" rx="2" fill="#fffefa" />
        <path d="M-36 -42 L0 -66 L36 -42Z" fill="#285347" />
        <rect x="-6" y="-92" width="12" height="28" fill="#fffefa" />
        <path d="M-9 -90 L0 -102 L9 -90Z" fill="#285347" />
        <path d="M0 -112 V-102 M-4 -108 H4" stroke="#c3a66a" strokeWidth="2" strokeLinecap="round" />
        <path d="M-8 0 V-20 Q 0 -30 8 -20 V0Z" fill="#35694f" />
        <circle cx="0" cy="-54" r="5" fill="none" stroke="#c3a66a" strokeWidth="1.6" />
      </g>
      {/* path */}
      <path d="M186 180 Q 194 160 196 134 H204 Q 206 160 214 180Z" fill="#f3efe3" opacity=".9" />
      {cypress(118, 70, 'c1')}{cypress(140, 54, 'c2')}{cypress(262, 62, 'c3')}{cypress(284, 76, 'c4')}
      {/* lilies */}
      {[[58, 150], [74, 156], [330, 152], [348, 158]].map(([x, y], i) => (
        <g key={i} transform={`translate(${x} ${y})`}>
          <path d="M0 0 V-14" stroke="#4a6b4f" strokeWidth="1.6" />
          <path d="M0 -14 C -6 -18 -7 -24 -2 -24 C 0 -20 0 -20 0 -14 C 0 -20 0 -20 2 -24 C 7 -24 6 -18 0 -14Z" fill="#fffefa" />
        </g>
      ))}
      {/* candle */}
      <g transform="translate(40 118)">
        <rect x="-5" y="0" width="10" height="22" rx="2" fill="#fffefa" />
        <path d="M0 -10 C 4 -5 3 -1 0 0 C -3 -1 -4 -5 0 -10Z" fill="#e6b45a" />
      </g>
    </Svg>
  )
}

