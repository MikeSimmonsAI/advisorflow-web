/* Contextual hero imagery (Phase 7.3).
 *
 * PHOTOGRAPHY FIRST. Each page banner uses a licensed stock photograph
 * (Unsplash License; see ./photos/CREDITS.md), bundled with the app so it works
 * offline and in local review. They are generic scenes — a skyline, a street of
 * homes, a bridge, a signing — never a picture of a specific property. Property
 * pages use the deal's own uploaded photo or an honest placeholder instead.
 *
 * The drawn SVG scenes below remain as the fallback for any scene without a
 * photograph.
 *
 * ── original notes on the drawn fallback ──
 *
 * The approved board puts a photographic, page-specific scene behind every
 * primary page heading: a city skyline for Acquisition Command, a street of
 * homes for the Discovery Inbox, a bridge at sunset for Strategies, and so on.
 *
 * These are DRAWN scenes, not photographs, on purpose:
 *   - IMAGE TRUTH. A stock photo of a house behind "1418 Cedar Springs Rd"
 *     would read as a picture of that house. An illustration never can.
 *   - LICENSING. Nothing here is copied from anyone; it is procedural SVG.
 *   - WEIGHT + OFFLINE. No network fetch, no layout shift, identical in the
 *     local review environment and in production.
 *
 * Every scene is composed for a 1200x320 banner whose left 45% sits under the
 * hero's text overlay, so the detail lives on the right. They are decorative
 * (aria-hidden) — the hero's heading carries the meaning.
 */

function rng(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const W = 1200
const H = 320

function Sky({ id, stops, sun }) {
  return (
    <>
      <defs>
        <linearGradient id={`${id}-sky`} x1="0" y1="0" x2="0" y2="1">
          {stops.map(([o, c]) => <stop key={o} offset={o} stopColor={c} />)}
        </linearGradient>
        {sun ? (
          <radialGradient id={`${id}-sun`} cx={sun.x / W} cy={sun.y / H} r="0.55">
            <stop offset="0" stopColor={sun.color} stopOpacity="0.95" />
            <stop offset="0.12" stopColor={sun.color} stopOpacity="0.55" />
            <stop offset="0.5" stopColor={sun.color} stopOpacity="0.12" />
            <stop offset="1" stopColor={sun.color} stopOpacity="0" />
          </radialGradient>
        ) : null}
      </defs>
      <rect width={W} height={H} fill={`url(#${id}-sky)`} />
      {sun ? <rect width={W} height={H} fill={`url(#${id}-sun)`} /> : null}
      {sun && sun.disc ? <circle cx={sun.x} cy={sun.y} r={sun.disc} fill="#fff4dc" opacity="0.9" /> : null}
    </>
  )
}

function Clouds({ seed, y = 60, color = '#ffffff', opacity = 0.12, n = 6 }) {
  const r = rng(seed)
  const out = []
  for (let i = 0; i < n; i++) {
    const cx = 420 + r() * 780, cy = y + r() * 70, w = 90 + r() * 180
    out.push(<ellipse key={i} cx={cx} cy={cy} rx={w} ry={6 + r() * 9} fill={color} opacity={opacity * (0.5 + r())} />)
  }
  return <g>{out}</g>
}

/** A tower with lit windows. */
function Tower({ x, w, h, base, fill, r, lit = 0.35, glow = '#ffd58a', crown }) {
  const top = base - h
  const wins = []
  const cols = Math.max(2, Math.floor(w / 9))
  const rows = Math.floor((h - 14) / 11)
  const gx = w / cols
  for (let c = 0; c < cols; c++) {
    for (let k = 0; k < rows; k++) {
      if (r() < lit) {
        wins.push(<rect key={`${c}-${k}`} x={x + c * gx + gx * 0.28} y={top + 10 + k * 11} width={gx * 0.44} height="5"
                        fill={glow} opacity={0.35 + r() * 0.6} />)
      }
    }
  }
  return (
    <g>
      {crown === 'spire' ? <path d={`M${x + w / 2 - 2} ${top} L${x + w / 2} ${top - 34} L${x + w / 2 + 2} ${top} Z`} fill={fill} /> : null}
      {crown === 'step' ? <rect x={x + w * 0.2} y={top - 16} width={w * 0.6} height="16" fill={fill} /> : null}
      {crown === 'slant' ? <path d={`M${x} ${top} L${x + w} ${top - 22} L${x + w} ${top} Z`} fill={fill} /> : null}
      <rect x={x} y={top} width={w} height={h} fill={fill} />
      {wins}
    </g>
  )
}

function Skyline({ seed, base, from = 470, to = 1200, minH = 60, maxH = 210, fill, lit, glow, gap = 3 }) {
  const r = rng(seed)
  const out = []
  let x = from
  const crowns = [null, null, 'spire', 'step', 'slant', null]
  while (x < to) {
    const w = 26 + r() * 46
    const h = minH + Math.pow(r(), 1.3) * (maxH - minH)
    out.push(<Tower key={x} x={x} w={w} h={h} base={base} fill={fill} r={r} lit={lit} glow={glow}
                    crown={crowns[Math.floor(r() * crowns.length)]} />)
    x += w + gap + r() * 6
  }
  return <g>{out}</g>
}

/** A two-storey house elevation with warm windows. */
function House({ x, base, w = 120, h = 70, roof = 40, body = '#2a3550', roofFill = '#1a2238', glow = '#ffcf7a', r, lit = 0.8, porch }) {
  const top = base - h
  const wins = []
  const cols = w > 110 ? 3 : 2
  for (let row = 0; row < 2; row++) {
    for (let c = 0; c < cols; c++) {
      const wx = x + (w / (cols + 1)) * (c + 1) - 9
      const wy = top + 12 + row * (h / 2)
      if (row === 1 && c === Math.floor(cols / 2)) continue
      const on = r() < lit
      wins.push(<rect key={`${row}-${c}`} x={wx} y={wy} width="18" height="16" rx="1.5"
                      fill={on ? glow : '#3b4866'} opacity={on ? 0.92 : 0.8} />)
    }
  }
  const doorX = x + w / 2 - 8
  return (
    <g>
      {on_glow(x, base, w, glow)}
      <rect x={x} y={top} width={w} height={h} fill={body} />
      <path d={`M${x - 8} ${top + 2} L${x + w / 2} ${top - roof} L${x + w + 8} ${top + 2} Z`} fill={roofFill} />
      <rect x={x + w * 0.7} y={top - roof * 0.75} width="10" height={roof * 0.5} fill={roofFill} />
      {wins}
      <rect x={doorX} y={base - 24} width="16" height="24" fill={glow} opacity="0.75" />
      {porch ? <rect x={x - 4} y={base - 30} width={w + 8} height="4" fill={roofFill} /> : null}
    </g>
  )
}
function on_glow(x, base, w, glow) {
  return <ellipse cx={x + w / 2} cy={base + 4} rx={w * 0.7} ry="10" fill={glow} opacity="0.12" />
}

function Tree({ x, base, s = 1, fill = '#14233a' }) {
  return (
    <g fill={fill}>
      <rect x={x - 2 * s} y={base - 18 * s} width={4 * s} height={18 * s} />
      <circle cx={x} cy={base - 34 * s} r={20 * s} />
      <circle cx={x - 13 * s} cy={base - 24 * s} r={14 * s} />
      <circle cx={x + 13 * s} cy={base - 25 * s} r={15 * s} />
    </g>
  )
}

function Water({ id, y, top = '#2b3a66', bottom = '#101a33', shimmer = '#ffcf8a', seed = 3 }) {
  const r = rng(seed)
  const lines = []
  for (let i = 0; i < 26; i++) {
    const ly = y + 6 + r() * (H - y - 8)
    const lx = 520 + r() * 640
    lines.push(<rect key={i} x={lx} y={ly} width={20 + r() * 90} height="1.6" fill={shimmer} opacity={0.12 + r() * 0.3} />)
  }
  return (
    <g>
      <defs>
        <linearGradient id={`${id}-water`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={top} />
          <stop offset="1" stopColor={bottom} />
        </linearGradient>
      </defs>
      <rect x="0" y={y} width={W} height={H - y} fill={`url(#${id}-water)`} />
      {lines}
    </g>
  )
}

// ── the scenes ──────────────────────────────────────────────────────────────

const SCENES = {
  /** Acquisition Command — a city at dusk: the market EvoSense is hunting. */
  skyline: (id) => (
    <>
      <Sky id={id} stops={[[0, '#16244d'], [0.45, '#4b3f7a'], [0.78, '#c7707a'], [1, '#f7b26a']]}
           sun={{ x: 930, y: 250, color: '#ffb067', disc: 0 }} />
      <Clouds seed={11} y={50} color="#ffd9c0" opacity={0.14} />
      <Skyline seed={7} base={262} from={440} minH={50} maxH={150} fill="#3a3a6a" lit={0.12} glow="#ffcf8a" />
      <Skyline seed={19} base={262} from={500} minH={70} maxH={225} fill="#141c3a" lit={0.38} glow="#ffd58a" gap={2} />
      <Water id={id} y={262} top="#2a2f5c" bottom="#0e1530" seed={5} />
    </>
  ),

  /** Discovery Inbox — a street of homes at dusk: every record is a house. */
  street: (id) => {
    const r = rng(23)
    return (
      <>
        <Sky id={id} stops={[[0, '#1b2e5e'], [0.55, '#5a5f9a'], [0.85, '#e79a7a'], [1, '#f6c089']]}
             sun={{ x: 1050, y: 240, color: '#ffc27d' }} />
        <Clouds seed={4} y={40} color="#ffe2cc" opacity={0.12} />
        <Skyline seed={31} base={240} from={600} minH={30} maxH={80} fill="#48507e" lit={0.05} glow="#ffd58a" />
        <rect x="0" y="236" width={W} height={H - 236} fill="#1b2a2c" />
        <rect x="0" y="236" width={W} height="10" fill="#26383a" />
        <Tree x={560} base={240} s={1.1} fill="#172a2f" />
        <House x={600} base={240} w={132} h={78} roof={44} body="#2b3552" roofFill="#1b2238" r={r} porch />
        <Tree x={760} base={240} s={1.3} fill="#15262b" />
        <House x={790} base={240} w={150} h={84} roof={50} body="#39425f" roofFill="#222a45" r={r} />
        <Tree x={965} base={240} s={1.0} fill="#172a2f" />
        <House x={995} base={240} w={124} h={74} roof={42} body="#2f3a58" roofFill="#1e2640" r={r} porch />
        <Tree x={1150} base={240} s={1.4} fill="#14252a" />
        <rect x="0" y="266" width={W} height="54" fill="#141d26" />
        {[640, 860, 1080].map((x) => <g key={x}><rect x={x} y="226" width="3" height="42" fill="#0e1419" /><circle cx={x + 1.5} cy="226" r="5" fill="#ffe0a0" opacity=".9" /><circle cx={x + 1.5} cy="226" r="18" fill="#ffe0a0" opacity=".12" /></g>)}
      </>
    )
  },

  /** Strategies — a bridge at sunset: the plan that gets you across. */
  bridge: (id) => {
    const cables = []
    for (let i = 0; i <= 26; i++) {
      const x = 560 + i * 24
      const q = (a, c, b, t) => (1 - t) * (1 - t) * a + 2 * (1 - t) * t * c + t * t * b
      const y = x < 700 ? q(210, 150, 60, (x - 560) / 140)
        : x > 1060 ? q(60, 150, 206, (x - 1060) / 140)
        : q(60, 214, 60, (x - 700) / 360)
      cables.push(<line key={i} x1={x} y1={y} x2={x} y2="232" stroke="#1a1a33" strokeWidth="1.2" opacity=".8" />)
    }
    return (
      <>
        <Sky id={id} stops={[[0, '#233262'], [0.4, '#7a5c8e'], [0.72, '#f08a5d'], [1, '#ffcf87']]}
             sun={{ x: 880, y: 222, color: '#ffc36e', disc: 26 }} />
        <Clouds seed={8} y={70} color="#ffd8b0" opacity={0.18} />
        <path d="M430 236 Q 600 214 760 230 T 1200 222 L1200 240 L430 240 Z" fill="#3b3560" opacity=".7" />
        <Water id={id} y={236} top="#8a5a6a" bottom="#1d2240" shimmer="#ffd79a" seed={9} />
        <path d="M560 210 Q 630 150 700 60" stroke="#1a1a33" strokeWidth="2.5" fill="none" />
        <path d="M700 60 Q 880 214 1060 60" stroke="#1a1a33" strokeWidth="3" fill="none" />
        <path d="M1060 60 Q 1130 150 1200 206" stroke="#1a1a33" strokeWidth="2.5" fill="none" />
        {cables}
        <rect x="692" y="50" width="16" height="190" fill="#15152b" />
        <rect x="1052" y="50" width="16" height="190" fill="#15152b" />
        <rect x="688" y="96" width="24" height="6" fill="#15152b" />
        <rect x="1048" y="96" width="24" height="6" fill="#15152b" />
        <rect x="480" y="228" width="720" height="9" fill="#15152b" />
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => <circle key={i} cx={540 + i * 90} cy="226" r="1.8" fill="#ffe0a0" />)}
      </>
    )
  },

  /** Providers & Controls — the data network: sources, links, controls. */
  network: (id) => {
    const r = rng(41)
    const nodes = Array.from({ length: 34 }, () => [520 + r() * 680, 20 + r() * 280])
    const links = []
    nodes.forEach(([x, y], i) => {
      nodes.forEach(([x2, y2], j) => {
        if (j > i && Math.hypot(x - x2, y - y2) < 120) links.push(<line key={`${i}-${j}`} x1={x} y1={y} x2={x2} y2={y2} stroke="#6fc3ff" strokeWidth="1" opacity=".22" />)
      })
    })
    const lat = [0.25, 0.5, 0.75].map((k) => <ellipse key={k} cx="930" cy="160" rx="140" ry={140 * Math.abs(0.5 - k) * 2 || 1} fill="none" stroke="#7cc8ff" strokeOpacity=".28" />)
    const lon = [0.2, 0.45, 0.7, 1].map((k) => <ellipse key={k} cx="930" cy="160" rx={140 * k} ry="140" fill="none" stroke="#7cc8ff" strokeOpacity=".22" />)
    return (
      <>
        <Sky id={id} stops={[[0, '#0a1d44'], [0.6, '#0f2e66'], [1, '#12407e']]} sun={{ x: 930, y: 160, color: '#3aa8ff' }} />
        <circle cx="930" cy="160" r="140" fill="#1b4b8f" opacity=".35" />
        {lat}{lon}
        <line x1="790" y1="160" x2="1070" y2="160" stroke="#7cc8ff" strokeOpacity=".3" />
        {links}
        {nodes.map(([x, y], i) => <g key={i}><circle cx={x} cy={y} r={i % 5 === 0 ? 3.5 : 2} fill="#bfe6ff" /><circle cx={x} cy={y} r="9" fill="#5cb8ff" opacity={i % 5 === 0 ? 0.25 : 0.08} /></g>)}
      </>
    )
  },

  /** Deal Operations — a finished home at twilight: what a closed deal looks like. */
  estate: (id) => (
    <>
      <Sky id={id} stops={[[0, '#1a2b58'], [0.5, '#56578e'], [0.8, '#d88a74'], [1, '#f1b37d']]}
           sun={{ x: 1020, y: 230, color: '#ffbd7a' }} />
      <Clouds seed={17} y={40} color="#ffe0c8" opacity={0.12} />
      <Tree x={600} base={246} s={1.8} fill="#1a2b33" />
      <Tree x={1170} base={246} s={2.1} fill="#162730" />
      <g>
        <rect x="700" y="120" width="360" height="126" fill="#2c3550" />
        <rect x="660" y="112" width="250" height="10" fill="#1a2036" />
        <rect x="880" y="86" width="210" height="10" fill="#1a2036" />
        <rect x="900" y="96" width="170" height="30" fill="#2c3550" />
        <rect x="720" y="140" width="80" height="96" fill="#ffc977" opacity=".9" />
        <rect x="810" y="140" width="60" height="96" fill="#ffd894" opacity=".85" />
        <rect x="900" y="140" width="140" height="44" fill="#ffcf82" opacity=".8" />
        <rect x="900" y="194" width="60" height="52" fill="#ffc46e" opacity=".9" />
        <rect x="975" y="194" width="65" height="52" fill="#3c4766" />
        {[760, 840, 970].map((x) => <rect key={x} x={x} y="140" width="2" height="96" fill="#2c3550" />)}
      </g>
      <rect x="0" y="246" width={W} height={H - 246} fill="#18262a" />
      <rect x="640" y="256" width="460" height="22" fill="#3d6d8f" opacity=".55" />
      <rect x="640" y="256" width="460" height="22" fill="#ffc977" opacity=".12" />
    </>
  ),

  /** Property workspace — a parcel map. Never a picture of "this" house. */
  parcel: (id) => {
    const r = rng(53)
    const lots = []
    for (let bx = 0; bx < 7; bx++) {
      for (let by = 0; by < 3; by++) {
        const x0 = 470 + bx * 110, y0 = 16 + by * 104
        for (let k = 0; k < 4; k++) {
          const lx = x0 + (k % 2) * 44 + 4, ly = y0 + Math.floor(k / 2) * 42 + 4
          lots.push(<rect key={`${bx}${by}${k}`} x={lx} y={ly} width="38" height="36" rx="2" fill="#2a4a5a" opacity={0.55 + r() * 0.3} />)
          lots.push(<rect key={`h${bx}${by}${k}`} x={lx + 10} y={ly + 9} width="17" height="15" rx="1" fill="#6a8aa0" opacity=".55" />)
        }
      }
    }
    return (
      <>
        <rect width={W} height={H} fill="#16303f" />
        {lots}
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => <rect key={'v' + i} x={463 + i * 110} y="0" width="8" height={H} fill="#e6d5b0" opacity=".22" />)}
        {[0, 1, 2, 3].map((i) => <rect key={'h' + i} x="440" y={9 + i * 104} width={W} height="8" fill="#e6d5b0" opacity=".22" />)}
        <circle cx="1000" cy="80" r="40" fill="#2f6b4b" opacity=".5" />
        <circle cx="620" cy="250" r="30" fill="#2f6b4b" opacity=".45" />
        <rect x="914" y="122" width="38" height="36" rx="2" fill="none" stroke="#ffd166" strokeWidth="3" />
        <rect x="914" y="122" width="38" height="36" rx="2" fill="#ffd166" opacity=".22" />
      </>
    )
  },

  /** Cash Buyers — capital and relationships across a skyline at golden hour. */
  capital: (id) => (
    <>
      <Sky id={id} stops={[[0, '#1f355f'], [0.5, '#6b6a8f'], [0.82, '#e3a46c'], [1, '#f9d08b']]}
           sun={{ x: 1000, y: 200, color: '#ffd07a' }} />
      <Skyline seed={61} base={290} from={480} minH={40} maxH={120} fill="#5a5d86" lit={0.06} />
      <Skyline seed={67} base={290} from={520} minH={60} maxH={230} fill="#1a2445" lit={0.3} glow="#ffdd9a" gap={4} />
      {[[600, 190, 760, 120], [760, 120, 940, 90], [940, 90, 1110, 140], [700, 210, 1000, 170]].map(([a, b, c, d], i) => (
        <path key={i} d={`M${a} ${b} Q ${(a + c) / 2} ${Math.min(b, d) - 60} ${c} ${d}`} stroke="#ffe3a8" strokeWidth="1.6" fill="none" strokeDasharray="4 5" opacity=".75" />
      ))}
      {[[600, 190], [760, 120], [940, 90], [1110, 140], [700, 210], [1000, 170]].map(([x, y], i) => <g key={i}><circle cx={x} cy={y} r="4.5" fill="#fff1c9" /><circle cx={x} cy={y} r="13" fill="#ffd98a" opacity=".25" /></g>)}
      <rect x="0" y="290" width={W} height="30" fill="#131b33" />
    </>
  ),

  /** Contracts & Closing — a signed agreement on a desk. */
  contract: (id) => {
    const lines = []
    for (let i = 0; i < 11; i++) lines.push(<rect key={i} x="40" y={52 + i * 16} width={i % 4 === 3 ? 150 : 250} height="4" rx="2" fill="#9aa6bd" opacity=".55" />)
    return (
      <>
        <defs>
          <linearGradient id={`${id}-desk`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#2a1f1a" />
            <stop offset="1" stopColor="#4a3326" />
          </linearGradient>
          <radialGradient id={`${id}-lamp`} cx=".8" cy=".2" r=".7">
            <stop offset="0" stopColor="#ffd9a0" stopOpacity=".5" />
            <stop offset="1" stopColor="#ffd9a0" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect width={W} height={H} fill={`url(#${id}-desk)`} />
        {Array.from({ length: 14 }, (_, i) => <rect key={i} x="0" y={i * 24} width={W} height="1" fill="#000" opacity=".12" />)}
        <rect width={W} height={H} fill={`url(#${id}-lamp)`} />
        <g transform="translate(700 20) rotate(-6)">
          <rect x="6" y="8" width="330" height="330" fill="#000" opacity=".25" />
          <rect width="330" height="330" fill="#f7f4ec" />
          <rect x="40" y="26" width="160" height="8" rx="3" fill="#1f2a44" />
          {lines}
          <path d="M44 252 c 20 -26 34 18 52 -8 s 30 -20 44 6 s 24 -6 40 -12" stroke="#1d3d8f" strokeWidth="2.4" fill="none" />
          <rect x="40" y="262" width="170" height="1.5" fill="#1f2a44" />
        </g>
        <g transform="translate(980 196) rotate(-34)">
          <rect x="0" y="0" width="190" height="11" rx="5.5" fill="#101320" />
          <rect x="120" y="0" width="30" height="11" fill="#c9a24a" />
          <path d="M190 0 L 214 5.5 L 190 11 Z" fill="#c9a24a" />
        </g>
      </>
    )
  },

  /** Dispositions — homes on a warm evening: the deal handed to its buyer. */
  sold: (id) => {
    const r = rng(71)
    return (
      <>
        <Sky id={id} stops={[[0, '#26396b'], [0.5, '#8a6b8c'], [0.8, '#f0a167'], [1, '#ffd48f']]}
             sun={{ x: 1080, y: 238, color: '#ffc471', disc: 20 }} />
        <Clouds seed={29} y={60} color="#ffe0c0" opacity={0.16} />
        <rect x="0" y="240" width={W} height={H - 240} fill="#26352c" />
        <Tree x={560} base={244} s={1.2} fill="#1c2c2a" />
        <House x={600} base={244} w={130} h={76} roof={44} body="#3a3f5e" roofFill="#252a44" r={r} porch />
        <House x={780} base={244} w={146} h={82} roof={48} body="#44496a" roofFill="#2a2f4b" r={r} />
        <Tree x={960} base={244} s={1.5} fill="#1b2b29" />
        <House x={990} base={244} w={128} h={74} roof={44} body="#3b4263" roofFill="#262c47" r={r} porch />
        <g transform="translate(740 212)">
          <rect x="0" y="0" width="4" height="56" fill="#f2efe8" />
          <rect x="4" y="4" width="54" height="30" fill="#f2efe8" />
          <rect x="8" y="8" width="46" height="22" fill="#c8323f" />
          <rect x="14" y="17" width="34" height="4" rx="2" fill="#fff" />
        </g>
      </>
    )
  },

  /** Properties — the portfolio from above. */
  aerial: (id) => {
    const r = rng(83)
    const roofs = ['#8a5a44', '#6d6f7a', '#9a6b4a', '#5a6272', '#7b4f3d', '#a58a6a']
    const out = []
    for (let row = 0; row < 4; row++) {
      for (let col = 0; col < 10; col++) {
        const x = 480 + col * 74 + (row % 2) * 12, y = 14 + row * 78
        out.push(<rect key={`l${row}${col}`} x={x - 6} y={y - 6} width="66" height="60" fill="#3f6b3f" opacity={0.6 + r() * 0.3} />)
        const rw = 30 + r() * 16, rh = 24 + r() * 12
        out.push(<g key={`h${row}${col}`}><rect x={x + 6} y={y + 6} width={rw} height={rh} fill={roofs[Math.floor(r() * roofs.length)]} /><rect x={x + 6} y={y + 6 + rh / 2 - 1} width={rw} height="2" fill="#000" opacity=".25" /></g>)
        if (r() > 0.55) out.push(<circle key={`t${row}${col}`} cx={x + 50} cy={y + 40} r={8 + r() * 6} fill="#2d5a31" />)
        if (r() > 0.85) out.push(<rect key={`p${row}${col}`} x={x + 40} y={y + 8} width="14" height="10" rx="3" fill="#5fb7d9" />)
      }
    }
    return (
      <>
        <rect width={W} height={H} fill="#4a6b45" />
        {out}
        {[0, 1, 2, 3, 4].map((i) => <rect key={i} x="440" y={i * 78 + 2} width={W} height="6" fill="#c7c3b8" opacity=".75" />)}
      </>
    )
  },

  /** Wholesale Settings — the machine room. */
  gears: (id) => {
    const gear = (cx, cy, rad, teeth, op) => {
      const pts = []
      for (let i = 0; i < teeth * 2; i++) {
        const a = (Math.PI * i) / teeth
        const rr = i % 2 === 0 ? rad : rad * 0.84
        pts.push(`${cx + rr * Math.cos(a)},${cy + rr * Math.sin(a)}`)
      }
      return (
        <g key={cx} opacity={op}>
          <polygon points={pts.join(' ')} fill="none" stroke="#9fd0ff" strokeWidth="2" />
          <circle cx={cx} cy={cy} r={rad * 0.55} fill="none" stroke="#9fd0ff" strokeWidth="2" />
          <circle cx={cx} cy={cy} r={rad * 0.16} fill="#9fd0ff" />
        </g>
      )
    }
    return (
      <>
        <Sky id={id} stops={[[0, '#0c1e40'], [1, '#17396b']]} sun={{ x: 960, y: 150, color: '#3b8fff' }} />
        {gear(900, 150, 110, 16, 0.55)}
        {gear(1080, 70, 62, 11, 0.4)}
        {gear(1070, 260, 70, 12, 0.35)}
        {gear(740, 250, 50, 10, 0.3)}
      </>
    )
  },

  /** AI — the working team. */
  ai: (id) => {
    const r = rng(97)
    const layers = [4, 6, 6, 3]
    const pts = layers.map((n, li) => Array.from({ length: n }, (_, k) => [640 + li * 160, 160 + (k - (n - 1) / 2) * 44]))
    const links = []
    for (let li = 0; li < pts.length - 1; li++) {
      pts[li].forEach(([x, y], a) => pts[li + 1].forEach(([x2, y2], b) => links.push(
        <line key={`${li}${a}${b}`} x1={x} y1={y} x2={x2} y2={y2} stroke="#8fd0ff" strokeWidth="1" opacity={0.08 + r() * 0.22} />)))
    }
    return (
      <>
        <Sky id={id} stops={[[0, '#0b1a3a'], [1, '#1a2f66']]} sun={{ x: 880, y: 160, color: '#7a6cff' }} />
        {links}
        {pts.flat().map(([x, y], i) => <g key={i}><circle cx={x} cy={y} r="6" fill="#dff2ff" /><circle cx={x} cy={y} r="16" fill="#86c8ff" opacity=".18" /></g>)}
      </>
    )
  },

  /** Seller Portal — a home, warmly lit. */
  home: (id) => {
    const r = rng(101)
    return (
      <>
        <Sky id={id} stops={[[0, '#2b4270'], [0.55, '#9a7f97'], [0.85, '#f2b27c'], [1, '#ffd79a']]}
             sun={{ x: 1100, y: 220, color: '#ffcf85' }} />
        <rect x="0" y="244" width={W} height={H - 244} fill="#2e4431" />
        <Tree x={640} base={248} s={1.9} fill="#223a2f" />
        <House x={740} base={248} w={220} h={110} roof={70} body="#4a4c68" roofFill="#2c2e48" r={r} lit={1} porch />
        <Tree x={1030} base={248} s={1.6} fill="#1f352b" />
        <path d="M830 248 L 810 320 L 890 320 L 870 248 Z" fill="#b8a98f" opacity=".5" />
      </>
    )
  },

  /** Investor Deal Room — the opportunity, and the return. */
  invest: (id) => (
    <>
      <Sky id={id} stops={[[0, '#132a55'], [0.6, '#2d4f86'], [1, '#6d8fc0']]} sun={{ x: 1000, y: 110, color: '#9ad0ff' }} />
      <Skyline seed={113} base={320} from={470} minH={60} maxH={200} fill="#122246" lit={0.3} glow="#cfe7ff" gap={4} />
      <path d="M520 250 L 640 214 L 740 226 L 850 170 L 960 182 L 1080 110 L 1180 80" stroke="#6ff0b8" strokeWidth="3" fill="none" />
      {[[640, 214], [850, 170], [1080, 110], [1180, 80]].map(([x, y], i) => <circle key={i} cx={x} cy={y} r="5" fill="#dfffee" />)}
    </>
  ),
}

export const SCENE_NAMES = Object.keys(SCENES)

// Page-banner photographs only. The old house*.jpg "representative property"
// photos are deliberately NOT bundled: no property is ever shown with a
// picture of a different house (see PropertyThumb).
const PHOTO_FILES = import.meta.glob(['./photos/*.jpg', '!./photos/house*.jpg'], { eager: true, import: 'default' })
export const PHOTOS = Object.fromEntries(Object.entries(PHOTO_FILES)
  .map(([path, url]) => [path.replace('./photos/', '').replace('.jpg', ''), url]))

/** `drawn`: force the illustrated scene even when a photo exists. Pages about
 * ONE property use it, so no photograph ever sits behind a specific address. */
export function Scene({ name, id, drawn }) {
  if (PHOTOS[name] && !drawn) {
    return <img className="evo-scene-photo" src={PHOTOS[name]} alt="" aria-hidden="true" decoding="async" />
  }
  const draw = SCENES[name] || SCENES.skyline
  const sid = `evo-scene-${id || name}`
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMaxYMid slice" aria-hidden="true" focusable="false">
      {draw(sid)}
    </svg>
  )
}
