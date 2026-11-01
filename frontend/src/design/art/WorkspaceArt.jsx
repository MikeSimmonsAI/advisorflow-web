import { useId } from 'react'

/**
 * Workspace hero artwork — local, self-contained SVG (no third-party image
 * URLs, nothing to fail to load). One scene per business identity; every scene
 * shares the same 400×180 frame so cards line up regardless of business.
 *
 * Decorative only: the card names the business in text, so the art is
 * aria-hidden and carries no meaning of its own.
 */

const PALETTES = {
  sci:       { sky: ['#eaf2ff', '#cfe0ff'], hill: '#b9d1fb', hill2: '#9fbff7', main: '#2f6fdb', deep: '#123a7a', light: '#ffffff', warm: '#ffd7a8' },
  energy:    { sky: ['#fff8e6', '#ffe6a8'], hill: '#f8d58a', hill2: '#f3c463', main: '#f2a516', deep: '#6b4300', light: '#ffffff', warm: '#fff1b8' },
  roofing:   { sky: ['#fff3ea', '#ffd9c2'], hill: '#ffc9a6', hill2: '#ffb284', main: '#ee6a24', deep: '#7a2f08', light: '#ffffff', warm: '#ffe3cf' },
  wholesale: { sky: ['#ebfaf2', '#c6efd9'], hill: '#a8e3c4', hill2: '#86d6ac', main: '#22a06b', deep: '#0b4d31', light: '#ffffff', warm: '#fff4cc' },
  insurance: { sky: ['#e8f8fa', '#c2ebf0'], hill: '#a3dfe7', hill2: '#82d0db', main: '#1595a6', deep: '#07434c', light: '#ffffff', warm: '#e9fbfd' },
  funeral:   { sky: ['#f4f0fc', '#ddd2f5'], hill: '#cbbcee', hill2: '#b6a3e6', main: '#7357b8', deep: '#34216a', light: '#ffffff', warm: '#ffe9c9' },
  office:    { sky: ['#f2f5f9', '#dde4ee'], hill: '#cfd8e5', hill2: '#bcc8d9', main: '#475569', deep: '#1e293b', light: '#ffffff', warm: '#e8eef7' },
  general:   { sky: ['#f1f4f9', '#dae2ee'], hill: '#cbd5e3', hill2: '#b8c4d6', main: '#5b6b85', deep: '#27324a', light: '#ffffff', warm: '#eef2f8' },
  cleaning:  { sky: ['#e9f7fe', '#c7ebfb'], hill: '#a9def6', hill2: '#86cff0', main: '#0e95d4', deep: '#0b4a6b', light: '#ffffff', warm: '#eafaff' },
  acquisition: { sky: ['#f4effe', '#e2d6fb'], hill: '#d2c2f8', hill2: '#b9a1f2', main: '#6d3fd9', deep: '#2a1765', light: '#ffffff', warm: '#efe7ff' },
  preview:   { sky: ['#f0f1ff', '#dcdffd'], hill: '#c8cdfb', hill2: '#b1b8f7', main: '#5b5fe0', deep: '#2b2f86', light: '#ffffff', warm: '#eef0ff' },
}

function Frame({ p, id, children }) {
  return (
    <svg viewBox="0 0 400 180" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false"
         className="ev-art" role="presentation">
      <defs>
        <linearGradient id={id + 'sky'} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={p.sky[0]} />
          <stop offset="1" stopColor={p.sky[1]} />
        </linearGradient>
        <radialGradient id={id + 'glow'} cx=".82" cy=".1" r=".55">
          <stop offset="0" stopColor="#fff" stopOpacity=".85" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="400" height="180" fill={`url(#${id}sky)`} />
      <rect width="400" height="180" fill={`url(#${id}glow)`} />
      <circle cx="40" cy="30" r="46" fill="#fff" opacity=".22" />
      <circle cx="372" cy="150" r="70" fill="#fff" opacity=".14" />
      {children}
    </svg>
  )
}

const Hills = ({ p }) => (
  <>
    <path d="M0 140 C 70 118 140 124 210 136 S 340 150 400 128 V180 H0Z" fill={p.hill} />
    <path d="M0 158 C 90 146 170 150 250 160 S 360 166 400 154 V180 H0Z" fill={p.hill2} />
  </>
)

function Person({ x, y, s = 1, body, head = '#f1c9a5', hair }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      <path d="M-15 40 C -15 18 -9 10 0 10 C 9 10 15 18 15 40 Z" fill={body} />
      <circle cx="0" cy="0" r="9" fill={head} />
      {hair && <path d="M-9 -1 C -9 -10 9 -10 9 -1 C 6 -6 -6 -6 -9 -1Z" fill={hair} />}
    </g>
  )
}

function House({ x, y, w = 60, roof, wall = '#fff', door, win = '#dfe9f7', s = 1 }) {
  const h = w * 0.62
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      <rect x={-w / 2 + 6} y={-h} width={w - 12} height={h} rx="3" fill={wall} />
      <path d={`M${-w / 2 - 2} ${-h + 2} L0 ${-h - w * 0.42} L${w / 2 + 2} ${-h + 2} Z`} fill={roof} strokeLinejoin="round" />
      <rect x="-6" y={-h * 0.55} width="12" height={h * 0.55} rx="2" fill={door} />
      <rect x={-w / 2 + 12} y={-h * 0.78} width="10" height="9" rx="1.5" fill={win} />
      <rect x={w / 2 - 22} y={-h * 0.78} width="10" height="9" rx="1.5" fill={win} />
    </g>
  )
}

/* ---------------------------- scenes ---------------------------- */

function SciScene({ p, id }) {
  return (
    <Frame p={p} id={id}>
      {/* chapel / home of service in the distance */}
      <g opacity=".55">
        <rect x="300" y="86" width="56" height="44" rx="3" fill="#fff" />
        <path d="M296 88 L328 64 L360 88Z" fill={p.hill2} />
        <rect x="324" y="50" width="8" height="18" fill="#fff" />
        <rect x="321" y="55" width="14" height="4" fill="#fff" />
      </g>
      <Hills p={p} />
      {/* family */}
      <Person x={86} y={110} s={1.05} body={p.main} hair={p.deep} />
      <Person x={124} y={114} s={0.98} body={p.deep} head="#e7b48d" hair="#3b2a1e" />
      <Person x={106} y={128} s={0.62} body="#7fa8f0" hair="#3b2a1e" />
      {/* advisor */}
      <Person x={208} y={112} s={1.02} body="#ffffff" head="#d9a07a" hair={p.deep} />
      <rect x="196" y="128" width="24" height="16" rx="2" fill={p.main} opacity=".9" />
      {/* conversation bubble with heart */}
      <g transform="translate(150 40)">
        <rect width="54" height="34" rx="11" fill="#fff" />
        <path d="M14 33 L10 44 L24 33Z" fill="#fff" />
        <path d="M27 25 C 18 18 17 11 22 9 C 25 8 27 11 27 12 C 27 11 29 8 32 9 C 37 11 36 18 27 25Z" fill={p.main} />
      </g>
      {/* appointment card */}
      <g transform="translate(246 30)">
        <rect width="62" height="58" rx="9" fill="#fff" />
        <rect width="62" height="15" rx="9" fill={p.main} />
        <rect y="8" width="62" height="7" fill={p.main} />
        <circle cx="14" cy="4" r="0" />
        {[0, 1, 2].map(r => [0, 1, 2, 3].map(c => (
          <rect key={r + '-' + c} x={9 + c * 12} y={22 + r * 10} width="7" height="6" rx="1.5"
                fill={r === 1 && c === 2 ? p.main : '#dbe6f8'} />
        )))}
      </g>
      <circle cx="304" cy="84" r="10" fill={p.deep} />
      <path d="M299 84 l3.5 3.5 l6 -7" stroke="#fff" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </Frame>
  )
}

function EnergyScene({ p, id }) {
  return (
    <Frame p={p} id={id}>
      <circle cx="330" cy="44" r="22" fill="#fff6d6" />
      <circle cx="330" cy="44" r="14" fill="#ffd56b" />
      <Hills p={p} />
      {/* transmission tower */}
      <g stroke={p.deep} strokeWidth="2.2" fill="none" strokeLinejoin="round">
        <path d="M70 150 L86 40 L102 150" />
        <path d="M74 122 L98 122 M78 96 L94 96 M82 70 L90 70" />
        <path d="M74 122 L94 96 M98 122 L78 96 M78 96 L90 70 M94 96 L82 70" strokeWidth="1.4" />
        <path d="M60 56 L112 56 M66 74 L106 74" strokeWidth="2.6" />
      </g>
      {/* power lines to the homes */}
      <g stroke={p.deep} strokeWidth="1.3" fill="none" opacity=".75">
        <path d="M112 56 Q 170 82 226 92" />
        <path d="M106 74 Q 180 100 300 102" />
        <path d="M60 56 Q 30 70 0 72" />
      </g>
      <rect x="224" y="90" width="4" height="40" fill={p.deep} />
      <House x={262} y={142} w={54} roof={p.main} door={p.deep} win="#ffe9a3" />
      <House x={322} y={146} w={46} roof={p.deep} door={p.main} win="#ffe9a3" />
      {/* bolt badge */}
      <g transform="translate(150 22)">
        <circle cx="22" cy="22" r="22" fill={p.main} />
        <path d="M25 8 L13 25 H22 L18 37 L31 19 H22 Z" fill="#fff" />
      </g>
      {/* enrollment card */}
      <g transform="translate(178 104)">
        <rect width="46" height="32" rx="6" fill="#fff" />
        <rect x="7" y="8" width="20" height="4" rx="2" fill={p.main} />
        <rect x="7" y="16" width="32" height="3" rx="1.5" fill="#f1e2bd" />
        <rect x="7" y="22" width="26" height="3" rx="1.5" fill="#f1e2bd" />
      </g>
    </Frame>
  )
}

function RoofingScene({ p, id }) {
  const shingles = []
  for (let r = 0; r < 4; r++) shingles.push(
    <path key={r} d={`M${130 + r * 14} ${96 - r * 15} L${270 - r * 14} ${96 - r * 15}`} stroke={p.deep} strokeOpacity=".28" strokeWidth="1.6" />)
  return (
    <Frame p={p} id={id}>
      <Hills p={p} />
      {/* big house, prominent roof */}
      <rect x="140" y="92" width="120" height="58" rx="3" fill="#fff" />
      <path d="M118 100 L200 34 L282 100 Z" fill={p.main} strokeLinejoin="round" />
      {shingles}
      <rect x="232" y="44" width="14" height="30" fill={p.deep} />
      <rect x="190" y="116" width="20" height="34" rx="2" fill={p.deep} />
      <rect x="152" y="108" width="22" height="18" rx="2" fill="#fde3d2" />
      <rect x="226" y="108" width="22" height="18" rx="2" fill="#fde3d2" />
      {/* ladder */}
      <g stroke={p.deep} strokeWidth="2.4" strokeLinecap="round">
        <path d="M108 152 L132 82 M124 154 L146 86" />
        <path d="M112 140 L127 144 M117 126 L132 130 M122 112 L137 116 M127 98 L141 102" strokeWidth="1.8" />
      </g>
      {/* inspector with clipboard */}
      <Person x={318} y={112} s={1.05} body={p.deep} head="#d9a07a" hair="#2a1d14" />
      <rect x="300" y="126" width="16" height="20" rx="2" fill="#fff" />
      <rect x="303" y="131" width="10" height="2" fill={p.main} />
      <rect x="303" y="136" width="8" height="2" fill={p.main} />
      {/* inspection passed badge */}
      <g transform="translate(296 34)">
        <rect width="64" height="30" rx="15" fill="#fff" />
        <circle cx="15" cy="15" r="9" fill={p.main} />
        <path d="M10.5 15 l3 3 l6 -6.5" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <rect x="29" y="11" width="26" height="3.5" rx="1.75" fill={p.deep} opacity=".7" />
        <rect x="29" y="17.5" width="18" height="3" rx="1.5" fill={p.deep} opacity=".35" />
      </g>
    </Frame>
  )
}

function WholesaleScene({ p, id }) {
  return (
    <Frame p={p} id={id}>
      {/* rising deal-flow line */}
      <path d="M20 112 L80 96 L130 104 L190 70 L240 78 L300 42" stroke={p.main} strokeWidth="3" fill="none"
            strokeLinecap="round" strokeLinejoin="round" opacity=".55" />
      <path d="M288 40 L302 41 L298 54" stroke={p.main} strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" opacity=".55" />
      <Hills p={p} />
      <House x={70} y={146} w={50} roof={p.hill2} door={p.deep} win="#e2f4ea" />
      <House x={152} y={142} w={70} roof={p.main} door={p.deep} win="#fff4cc" />
      <House x={236} y={148} w={46} roof={p.deep} door={p.main} win="#e2f4ea" />
      {/* key tag on the featured property */}
      <g transform="translate(176 54)">
        <path d="M0 10 L14 0 H50 V20 H14 Z" fill="#fff" />
        <circle cx="10" cy="10" r="3" fill={p.main} />
        <rect x="20" y="7" width="24" height="6" rx="3" fill={p.main} />
      </g>
      {/* contract with check */}
      <g transform="translate(286 74)">
        <rect width="58" height="72" rx="7" fill="#fff" />
        <rect x="10" y="12" width="30" height="5" rx="2.5" fill={p.deep} />
        <rect x="10" y="24" width="38" height="3" rx="1.5" fill="#cfe9db" />
        <rect x="10" y="31" width="34" height="3" rx="1.5" fill="#cfe9db" />
        <rect x="10" y="38" width="36" height="3" rx="1.5" fill="#cfe9db" />
        <path d="M10 58 q 6 -8 12 0 t 12 0" stroke={p.deep} strokeWidth="1.6" fill="none" />
        <circle cx="48" cy="60" r="11" fill={p.main} />
        <path d="M43.5 60 l3 3 l6 -6.5" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      {/* coin */}
      <g transform="translate(360 34)">
        <circle r="15" fill="#ffd56b" />
        <circle r="10.5" fill="none" stroke="#c99a1f" strokeWidth="1.4" />
        <text y="5" textAnchor="middle" fontSize="14" fontWeight="800" fill="#8a6408" fontFamily="Inter, system-ui, sans-serif">$</text>
      </g>
    </Frame>
  )
}

function InsuranceScene({ p, id }) {
  return (
    <Frame p={p} id={id}>
      <Hills p={p} />
      {/* policy documents */}
      <g transform="translate(64 50)">
        <rect x="10" y="-8" width="70" height="88" rx="8" fill="#fff" opacity=".6" />
        <rect width="70" height="88" rx="8" fill="#fff" />
        <rect x="11" y="14" width="34" height="5" rx="2.5" fill={p.deep} />
        <rect x="11" y="27" width="48" height="3" rx="1.5" fill="#cdeaee" />
        <rect x="11" y="34" width="44" height="3" rx="1.5" fill="#cdeaee" />
        <rect x="11" y="41" width="46" height="3" rx="1.5" fill="#cdeaee" />
        <rect x="11" y="62" width="26" height="12" rx="6" fill={p.main} opacity=".85" />
      </g>
      {/* central shield */}
      <g transform="translate(200 28)">
        <path d="M0 8 L40 -6 L80 8 V44 C 80 72 58 90 40 98 C 22 90 0 72 0 44 Z" fill={p.main} />
        <path d="M10 14 L40 4 L70 14 V44 C 70 66 54 80 40 87 Z" fill="#fff" opacity=".16" />
        <path d="M24 46 l12 12 l22 -24" stroke="#fff" strokeWidth="6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      {/* protected home + advisor */}
      <House x={330} y={150} w={50} roof={p.deep} door={p.main} win="#e9fbfd" />
      <path d="M296 104 Q 330 64 364 104" stroke={p.main} strokeWidth="4" fill="none" strokeLinecap="round" opacity=".7" />
      <Person x={168} y={118} s={0.95} body={p.deep} head="#e7b48d" hair="#3b2a1e" />
    </Frame>
  )
}

function FuneralScene({ p, id }) {
  return (
    <Frame p={p} id={id}>
      <circle cx="300" cy="60" r="30" fill={p.warm} opacity=".9" />
      <Hills p={p} />
      {/* garden gateway */}
      <g fill="#fff">
        <rect x="150" y="70" width="12" height="78" rx="2" />
        <rect x="238" y="70" width="12" height="78" rx="2" />
        <path d="M150 74 Q 200 30 250 74 V82 Q 200 42 150 82Z" />
      </g>
      <path d="M180 180 Q 196 150 200 132 Q 204 150 220 180Z" fill="#fff" opacity=".55" />
      {/* trees */}
      <g>
        <rect x="80" y="104" width="6" height="40" fill={p.deep} opacity=".7" />
        <circle cx="83" cy="96" r="24" fill={p.main} opacity=".85" />
        <circle cx="68" cy="108" r="14" fill={p.main} opacity=".7" />
        <rect x="324" y="112" width="5" height="32" fill={p.deep} opacity=".7" />
        <circle cx="326" cy="104" r="18" fill={p.main} opacity=".7" />
      </g>
      {/* records card */}
      <g transform="translate(26 30)">
        <rect width="54" height="40" rx="8" fill="#fff" />
        <rect x="9" y="10" width="24" height="4" rx="2" fill={p.main} />
        <rect x="9" y="19" width="36" height="3" rx="1.5" fill="#e3dcf3" />
        <rect x="9" y="26" width="30" height="3" rx="1.5" fill="#e3dcf3" />
      </g>
    </Frame>
  )
}

function OfficeScene({ p, id }) {
  return (
    <Frame p={p} id={id}>
      <Hills p={p} />
      <g transform="translate(70 30)">
        <rect width="170" height="110" rx="12" fill="#fff" />
        <rect x="14" y="14" width="60" height="6" rx="3" fill={p.deep} />
        {[38, 56, 30, 70, 48].map((h, i) => (
          <rect key={i} x={16 + i * 22} y={96 - h} width="14" height={h} rx="3" fill={i === 3 ? p.main : '#d4dbe6'} />
        ))}
        <rect x="128" y="34" width="30" height="62" rx="6" fill="#eef2f7" />
        <circle cx="143" cy="52" r="9" fill="none" stroke={p.main} strokeWidth="4" strokeDasharray="38 60" />
      </g>
      <g transform="translate(262 60)">
        <rect width="80" height="56" rx="10" fill={p.deep} />
        <rect x="28" y="-10" width="24" height="12" rx="4" fill="none" stroke={p.deep} strokeWidth="5" />
        <rect y="22" width="80" height="6" fill="#fff" opacity=".18" />
      </g>
    </Frame>
  )
}

function GeneralScene({ p, id }) {
  const nodes = [[90, 70], [160, 50], [230, 82], [300, 56], [130, 118], [260, 124]]
  return (
    <Frame p={p} id={id}>
      <Hills p={p} />
      <g stroke={p.main} strokeWidth="2" opacity=".55">
        <path d="M90 70 L160 50 L230 82 L300 56 M160 50 L130 118 L230 82 L260 124 L300 56" fill="none" />
      </g>
      {nodes.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={i === 2 ? 14 : 9} fill={i === 2 ? p.main : '#fff'} stroke={p.main} strokeWidth="2" />)}
    </Frame>
  )
}

// COMMERCIAL CLEANING: an office building with clean, sparkling windows, a
// cleaner with a mop and a spray bottle - a business, not a generic network.
function CleaningScene({ p, id }) {
  const spark = (x, y, s = 1) => (
    <path key={x + '-' + y} transform={`translate(${x} ${y}) scale(${s})`}
          d="M0 -9 L2.2 -2.2 L9 0 L2.2 2.2 L0 9 L-2.2 2.2 L-9 0 L-2.2 -2.2Z" fill="#fff" />
  )
  return (
    <Frame p={p} id={id}>
      <Hills p={p} />
      <g transform="translate(150 26)">
        <rect width="110" height="122" rx="8" fill="#fff" />
        <rect width="110" height="16" rx="8" fill={p.main} />
        {[0, 1, 2].map(r => [0, 1, 2].map(c => (
          <rect key={r + '-' + c} x={12 + c * 32} y={26 + r * 28} width="22" height="18" rx="3"
                fill={(r + c) % 2 ? '#d9f1fc' : '#bfe6f8'} />
        )))}
        <rect x="43" y="100" width="24" height="22" rx="3" fill={p.deep} />
      </g>
      {spark(176, 38, 0.8)}{spark(240, 66, 1)}{spark(208, 94, 0.7)}
      <g transform="translate(300 76)">
        <rect x="-3" y="-22" width="6" height="66" rx="3" fill={p.deep} transform="rotate(14)" />
        <rect x="-4" y="40" width="34" height="9" rx="4" fill={p.main} transform="rotate(14)" />
      </g>
      <Person x={318} y={98} s={0.95} body={p.main} hair={p.deep} />
      <g transform="translate(86 92)">
        <rect x="0" y="16" width="26" height="40" rx="7" fill={p.main} />
        <rect x="6" y="4" width="14" height="14" rx="3" fill={p.deep} />
        <path d="M20 8 L38 4 L38 12 Z" fill={p.deep} />
        <circle cx="46" cy="4" r="2.5" fill="#fff" /><circle cx="52" cy="9" r="2" fill="#fff" /><circle cx="50" cy="-1" r="1.6" fill="#fff" />
      </g>
      {spark(60, 46, 0.9)}{spark(352, 40, 0.8)}
    </Frame>
  )
}

// PREVIEW: a browser window showing a mockup - something you can open and look
// at, clearly not a live business.
function PreviewScene({ p, id }) {
  return (
    <Frame p={p} id={id}>
      <Hills p={p} />
      <g transform="translate(92 28)">
        <rect width="216" height="122" rx="12" fill="#fff" />
        <rect width="216" height="20" rx="10" fill={p.deep} />
        <circle cx="14" cy="10" r="3.5" fill="#ff8a8a" /><circle cx="26" cy="10" r="3.5" fill="#ffd36e" /><circle cx="38" cy="10" r="3.5" fill="#7ee0a1" />
        <rect x="16" y="34" width="92" height="9" rx="4" fill={p.main} />
        <rect x="16" y="50" width="120" height="5" rx="2.5" fill="#dfe2fb" />
        <rect x="16" y="61" width="104" height="5" rx="2.5" fill="#dfe2fb" />
        <rect x="16" y="78" width="54" height="16" rx="8" fill={p.main} />
        <rect x="140" y="34" width="60" height="70" rx="8" fill="#eef0ff" />
        <path d="M156 88 L170 66 L182 80 L190 72 L198 88 Z" fill={p.main} opacity=".55" />
        <circle cx="186" cy="52" r="7" fill="#ffd36e" />
      </g>
      <g transform="translate(286 112)">
        <circle r="20" fill={p.main} />
        <path d="M-9 0 C -4 -8 4 -8 9 0 C 4 8 -4 8 -9 0 Z" fill="#fff" />
        <circle r="3.5" fill={p.deep} />
      </g>
    </Frame>
  )
}

const SCENES = {
  sci: SciScene, energy: EnergyScene, roofing: RoofingScene, wholesale: WholesaleScene,
  insurance: InsuranceScene, funeral: FuneralScene, office: OfficeScene, general: GeneralScene,
  cleaning: CleaningScene, acquisition: CleaningScene, preview: PreviewScene,
}

export const ART_VARIANTS = Object.keys(SCENES)

export default function WorkspaceArt({ variant }) {
  const rid = 'wa' + useId().replace(/[^a-zA-Z0-9]/g, '')
  const key = SCENES[variant] ? variant : 'general'
  const Scene = SCENES[key]
  return <Scene p={PALETTES[key]} id={rid} />
}
