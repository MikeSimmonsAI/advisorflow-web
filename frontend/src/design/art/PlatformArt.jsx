import { useId } from 'react'

/**
 * Hero illustration: separate businesses, each in its own tile, joined to one
 * secured platform. Purely symbolic and decorative (aria-hidden) — it shows
 * the idea "one login, separate environments", not this person's access.
 */
const TILES = [
  { x: 54,  y: 46,  c: '#2f6fdb', soft: '#e8f0fe', g: 'sci' },
  { x: 380, y: 34,  c: '#f2a516', soft: '#fff5de', g: 'energy' },
  { x: 418, y: 196, c: '#ee6a24', soft: '#fff0e6', g: 'roofing' },
  { x: 290, y: 292, c: '#22a06b', soft: '#e5f7ee', g: 'wholesale' },
  { x: 30,  y: 222, c: '#1595a6', soft: '#e2f5f7', g: 'insurance' },
]
const HUB = { x: 260, y: 186 }

function Glyph({ g, c }) {
  switch (g) {
    case 'sci': return (<g fill={c}><circle cx="-7" cy="-6" r="5" /><circle cx="8" cy="-6" r="5" /><path d="M-16 12 C -16 0 2 0 2 12Z" /><path d="M-1 12 C -1 2 17 2 17 12Z" opacity=".7" /></g>)
    case 'energy': return <path d="M3 -16 L-9 3 H0 L-4 16 L10 -4 H1 Z" fill={c} />
    case 'roofing': return (<g><path d="M-16 0 L0 -14 L16 0Z" fill={c} /><rect x="-11" y="0" width="22" height="13" rx="1.5" fill={c} opacity=".55" /></g>)
    case 'wholesale': return (<g><rect x="-14" y="-12" width="28" height="24" rx="4" fill={c} opacity=".25" /><path d="M-10 6 L-3 -1 L3 4 L11 -6" stroke={c} strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></g>)
    case 'insurance': return (<g><path d="M0 -15 L13 -10 V0 C 13 9 6 14 0 16 C -6 14 -13 9 -13 0 V-10Z" fill={c} /><path d="M-5 0 l4 4 l7 -8" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></g>)
    default: return null
  }
}

export default function PlatformArt() {
  const id = 'pa' + useId().replace(/[^a-zA-Z0-9]/g, '')
  return (
    <svg viewBox="0 0 520 380" className="ev-platform-art" aria-hidden="true" focusable="false" role="presentation">
      <defs>
        <radialGradient id={id + 'halo'} cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#2f6fdb" stopOpacity=".22" />
          <stop offset="1" stopColor="#2f6fdb" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={id + 'hub'} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2a6be0" />
          <stop offset="1" stopColor="#123a7a" />
        </linearGradient>
        <filter id={id + 'sh'} x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="10" stdDeviation="12" floodColor="#0f1f3a" floodOpacity=".14" />
        </filter>
      </defs>

      {/* orbit rings */}
      <circle cx={HUB.x} cy={HUB.y} r="150" fill={`url(#${id}halo)`} />
      <circle cx={HUB.x} cy={HUB.y} r="118" fill="none" stroke="#c9d8f2" strokeDasharray="3 7" />
      <circle cx={HUB.x} cy={HUB.y} r="168" fill="none" stroke="#dfe8f7" />

      {/* connections */}
      {TILES.map(t => {
        const cx = t.x + 36, cy = t.y + 30
        const mx = (cx + HUB.x) / 2, my = (cy + HUB.y) / 2 - 18
        return (
          <g key={'l' + t.g}>
            <path d={`M${cx} ${cy} Q ${mx} ${my} ${HUB.x} ${HUB.y}`} stroke={t.c} strokeOpacity=".55"
                  strokeWidth="2" strokeDasharray="6 6" fill="none" />
            <circle cx={mx * 0.5 + (cx + HUB.x) / 4} cy={my * 0.5 + (cy + HUB.y) / 4} r="3.5" fill={t.c} />
          </g>
        )
      })}

      {/* business tiles: each its own environment */}
      {TILES.map(t => (
        <g key={t.g} transform={`translate(${t.x} ${t.y})`} filter={`url(#${id}sh)`}>
          <rect width="72" height="60" rx="16" fill="#fff" />
          <rect x="0" y="0" width="72" height="5" rx="2.5" fill={t.c} />
          <rect x="14" y="13" width="44" height="34" rx="11" fill={t.soft} />
          <g transform="translate(36 30)"><Glyph g={t.g} c={t.c} /></g>
        </g>
      ))}

      {/* the one platform, secured */}
      <g filter={`url(#${id}sh)`}>
        <rect x={HUB.x - 58} y={HUB.y - 58} width="116" height="116" rx="30" fill={`url(#${id}hub)`} />
      </g>
      <rect x={HUB.x - 46} y={HUB.y - 46} width="92" height="92" rx="22" fill="none" stroke="#fff" strokeOpacity=".22" />
      <g transform={`translate(${HUB.x} ${HUB.y - 4})`}>
        <path d="M-12 -6 V-14 a12 12 0 0 1 24 0 V-6" stroke="#fff" strokeWidth="5" fill="none" strokeLinecap="round" />
        <rect x="-20" y="-7" width="40" height="32" rx="8" fill="#fff" />
        <circle cx="0" cy="6" r="4.5" fill="#123a7a" />
        <rect x="-1.8" y="7" width="3.6" height="9" rx="1.8" fill="#123a7a" />
      </g>
    </svg>
  )
}
