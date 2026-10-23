/**
 * EvoSys Pro Roofing - the PUBLIC page (/roofing).
 *
 * Positioning: lead CONVERSION of leads a roofing company already owns, never
 * lead generation. Every figure on this page is a labelled sample. The demo
 * form posts to the existing public intake
 * (POST /site-intake/evosyspro/demo-request) and reports exactly what the
 * server answered - it never claims success on an error.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { API_BASE } from '../../api/client'
import { BrandMark } from './RoofingShell'
import { Icon } from './ui'
import './roofing.css'
import './roofing-landing.css'

const PLATFORM_SLUG = 'evosyspro'

function useFont() {
  useEffect(() => {
    if (document.getElementById('rf-font')) return
    const l = document.createElement('link')
    l.id = 'rf-font'
    l.rel = 'stylesheet'
    l.href = 'https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap'
    document.head.appendChild(l)
  }, [])
}

function scrollTo(id) {
  const el = document.getElementById(id)
  if (!el) return
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

/* ── the hero illustration: a two-story gabled house at dusk ─────────────── */
function HouseAtDusk() {
  return (
    <svg className="rfl-art" viewBox="0 0 900 640" preserveAspectRatio="xMidYMax slice" role="img"
      aria-label="Illustration of a two-story home with a new shingle roof at dusk">
      <defs>
        <linearGradient id="rfl-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#0b1b33" />
          <stop offset=".45" stopColor="#23396a" />
          <stop offset=".72" stopColor="#7a5a7a" />
          <stop offset=".88" stopColor="#e08a4e" />
          <stop offset="1" stopColor="#f6b26b" />
        </linearGradient>
        <radialGradient id="rfl-sun" cx=".22" cy=".86" r=".5">
          <stop offset="0" stopColor="#ffd59a" stopOpacity=".85" />
          <stop offset="1" stopColor="#ffd59a" stopOpacity="0" />
        </radialGradient>
        <pattern id="rfl-shingle" width="28" height="14" patternUnits="userSpaceOnUse">
          <rect width="28" height="14" fill="#2b3a52" />
          <path d="M0 13.5h28M14 0v7M0 7h28M7 7v7M21 7v7" stroke="#1b273a" strokeWidth="1.2" />
          <path d="M0 6.5h28" stroke="#3a4d6b" strokeWidth=".8" opacity=".55" />
        </pattern>
        <pattern id="rfl-shingle-dark" width="28" height="14" patternUnits="userSpaceOnUse">
          <rect width="28" height="14" fill="#1f2c40" />
          <path d="M0 13.5h28M14 0v7M0 7h28M7 7v7M21 7v7" stroke="#141d2b" strokeWidth="1.2" />
        </pattern>
        <pattern id="rfl-siding" width="12" height="11" patternUnits="userSpaceOnUse">
          <rect width="12" height="11" fill="#d9dde3" />
          <path d="M0 10.5h12" stroke="#b9bfc9" strokeWidth="1.1" />
        </pattern>
        <pattern id="rfl-siding-shade" width="12" height="11" patternUnits="userSpaceOnUse">
          <rect width="12" height="11" fill="#aeb5c1" />
          <path d="M0 10.5h12" stroke="#959dab" strokeWidth="1.1" />
        </pattern>
        <linearGradient id="rfl-window" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffe1a3" />
          <stop offset="1" stopColor="#f5a94a" />
        </linearGradient>
        <radialGradient id="rfl-glow" cx=".5" cy=".5" r=".5">
          <stop offset="0" stopColor="#ffc871" stopOpacity=".55" />
          <stop offset="1" stopColor="#ffc871" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="rfl-lawn" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#24402f" />
          <stop offset="1" stopColor="#0f1e17" />
        </linearGradient>
        <linearGradient id="rfl-fade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#0b1b33" stopOpacity="1" />
          <stop offset=".28" stopColor="#0b1b33" stopOpacity=".55" />
          <stop offset=".55" stopColor="#0b1b33" stopOpacity="0" />
        </linearGradient>
      </defs>

      <rect width="900" height="640" fill="url(#rfl-sky)" />
      <rect width="900" height="640" fill="url(#rfl-sun)" />
      {/* stars */}
      {[[620, 40], [700, 90], [780, 30], [840, 120], [560, 110], [470, 60], [860, 60]].map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i % 2 ? 1.2 : 1.6} fill="#fff" opacity=".7" />
      ))}
      {/* distant tree line */}
      <path d="M0 470 Q40 440 80 462 T160 452 T250 466 T340 448 T430 460 T520 446 T610 462 T700 444 T800 458 T900 446 V520 H0Z" fill="#1a2b3f" opacity=".9" />

      {/* ── house ── */}
      <g>
        {/* garage wing (left, single story) */}
        <rect x="250" y="372" width="170" height="118" fill="url(#rfl-siding-shade)" />
        <polygon points="232,378 335,300 438,378" fill="url(#rfl-shingle-dark)" />
        <polyline points="232,378 335,300 438,378" fill="none" stroke="#0e1726" strokeWidth="5" strokeLinejoin="round" />
        <rect x="272" y="410" width="126" height="80" fill="#c7ccd5" />
        {[0, 1, 2, 3].map((i) => <rect key={i} x="276" y={414 + i * 19} width="118" height="15" fill="#d6dae1" stroke="#b3b9c4" />)}

        {/* main block */}
        <rect x="420" y="296" width="330" height="194" fill="url(#rfl-siding)" />
        <rect x="420" y="296" width="330" height="194" fill="#0b1b33" opacity=".08" />
        {/* main roof: hip-ish gable */}
        <polygon points="396,304 585,170 774,304" fill="url(#rfl-shingle)" />
        <polygon points="585,170 774,304 742,304 585,192" fill="#0b1b33" opacity=".22" />
        <polyline points="396,304 585,170 774,304" fill="none" stroke="#0e1726" strokeWidth="6" strokeLinejoin="round" />
        <rect x="396" y="302" width="378" height="8" fill="#e8ebf0" />

        {/* front gable bump-out */}
        <rect x="470" y="340" width="150" height="150" fill="url(#rfl-siding)" />
        <polygon points="452,346 545,262 638,346" fill="url(#rfl-shingle)" />
        <polygon points="545,262 638,346 616,346 545,282" fill="#0b1b33" opacity=".25" />
        <polyline points="452,346 545,262 638,346" fill="none" stroke="#0e1726" strokeWidth="5" strokeLinejoin="round" />
        <polygon points="478,340 545,282 612,340" fill="#eef0f3" />
        <circle cx="545" cy="318" r="12" fill="url(#rfl-window)" stroke="#f3f4f6" strokeWidth="3" />

        {/* chimney */}
        <rect x="670" y="196" width="34" height="72" fill="#5b4a43" />
        <rect x="664" y="190" width="46" height="10" fill="#3e322e" />

        {/* upper windows */}
        {[655, 700].map((x) => (
          <g key={x}>
            <circle cx={x + 16} cy={398} r="46" fill="url(#rfl-glow)" />
            <rect x={x} y="370" width="34" height="52" fill="url(#rfl-window)" stroke="#f3f4f6" strokeWidth="4" />
            <path d={`M${x + 17} 370v52M${x} 396h34`} stroke="#f3f4f6" strokeWidth="2.5" />
          </g>
        ))}
        {/* lower bump-out windows + door */}
        <circle cx="500" cy="430" r="52" fill="url(#rfl-glow)" />
        <rect x="484" y="398" width="40" height="58" fill="url(#rfl-window)" stroke="#f3f4f6" strokeWidth="4" />
        <path d="M504 398v58M484 427h40" stroke="#f3f4f6" strokeWidth="2.5" />
        <rect x="556" y="404" width="44" height="86" fill="#1c2a40" stroke="#f3f4f6" strokeWidth="4" />
        <rect x="562" y="410" width="32" height="22" fill="url(#rfl-window)" opacity=".9" />
        <circle cx="592" cy="450" r="2.6" fill="#f2c27a" />
        <rect x="438" y="398" width="24" height="52" fill="url(#rfl-window)" stroke="#f3f4f6" strokeWidth="3" opacity=".9" />

        {/* porch light glow */}
        <circle cx="548" cy="410" r="30" fill="url(#rfl-glow)" />
        {/* foundation & steps */}
        <rect x="250" y="488" width="500" height="10" fill="#8d95a3" />
        <rect x="548" y="490" width="60" height="8" fill="#aab1bd" />
        <rect x="540" y="497" width="76" height="8" fill="#9aa2af" />
      </g>

      {/* lawn & drive */}
      <path d="M0 500 H900 V640 H0Z" fill="url(#rfl-lawn)" />
      <path d="M272 500 L398 500 L470 640 L180 640Z" fill="#3a4250" opacity=".85" />
      <path d="M552 505 L612 505 L660 640 L600 640Z" fill="#4a5262" opacity=".7" />
      {/* shrubs */}
      {[[440, 498, 26], [480, 502, 20], [640, 500, 24], [700, 502, 28], [745, 500, 20]].map(([x, y, r], i) => (
        <ellipse key={i} cx={x} cy={y} rx={r} ry={r * .62} fill={i % 2 ? '#1f3a2a' : '#284a35'} />
      ))}
      {/* trees */}
      <g>
        <rect x="826" y="380" width="10" height="130" fill="#2a1f1a" />
        <path d="M831 250 C790 300 780 360 800 400 C815 430 850 430 862 400 C882 360 872 300 831 250Z" fill="#1c3527" />
        <path d="M831 280 C805 320 800 360 812 388" stroke="#2b4d38" strokeWidth="6" fill="none" opacity=".6" />
        <rect x="160" y="400" width="9" height="110" fill="#2a1f1a" />
        <path d="M165 300 C130 340 126 392 142 420 C156 444 180 444 192 420 C206 392 200 340 165 300Z" fill="#183022" />
      </g>
      {/* left fade so the headline stays readable */}
      <rect width="900" height="640" fill="url(#rfl-fade)" />
    </svg>
  )
}

const STATS = [
  { icon: 'send', value: '1,248', label: 'Leads contacted', sub: 'This month' },
  { icon: 'chat', value: '312', label: 'Homeowners responded', sub: '25% response rate' },
  { icon: 'calendar', value: '86', label: 'Inspections booked', sub: 'This month' },
  { icon: 'chart', value: '$542K', label: 'Estimated job value', sub: 'Open estimates' },
]

const FEATURES = [
  { icon: 'send', title: 'AI outreach & follow-up', text: 'Texts and emails go out on a schedule you approve, and stop the moment a homeowner replies or opts out.' },
  { icon: 'calendar', title: 'Book inspections automatically', text: 'Interested homeowners are offered real open times from your team’s calendar.' },
  { icon: 'pipeline', title: 'Track every opportunity', text: 'From first reply to inspection, estimate and signed job — one record per homeowner.' },
  { icon: 'dollar', title: 'Turn more leads into revenue', text: 'See which follow-up touch gets answers and where homeowners stall.' },
]

const STEPS = [
  { title: 'Upload your existing leads', text: 'Bring the spreadsheet, CRM export or old estimate list you already have.' },
  { title: 'EvoSys qualifies and organizes', text: 'Duplicates are merged, bad numbers flagged, and every contact sorted into ready, needs review or excluded.' },
  { title: 'AI starts eligible conversations', text: 'Only homeowners who can be contacted get a message — from your company, in your name.' },
  { title: 'Interested homeowners are offered inspection times', text: 'Replies are read and answered. A “yes” gets real openings, not a call-back promise.' },
  { title: 'Confirmed inspections land on your calendar', text: 'Booked against your team’s actual availability, with one alert to the right person.' },
  { title: 'Your team takes over and outcomes are tracked', text: 'Inspected, estimate sent, won or lost — and the conversion rate at every step.' },
]

function DemoForm() {
  const [f, setF] = useState({ name: '', company: '', email: '', phone: '', leads: '', message: '' })
  const [state, setState] = useState({ phase: 'idle', text: '' })
  const submissionId = useMemo(() => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `rf-${Date.now()}-${Math.random().toString(36).slice(2)}`), [])
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    if (!f.name.trim()) return setState({ phase: 'error', text: 'Please enter your name.' })
    if (!f.email.trim() && !f.phone.trim()) return setState({ phase: 'error', text: 'Please enter an email address or a phone number so we can reach you.' })
    setState({ phase: 'sending', text: '' })
    const message = [
      f.leads ? `Existing leads: ${f.leads}` : null,
      f.message.trim() || null,
    ].filter(Boolean).join('\n\n') || null
    try {
      const res = await fetch(`${API_BASE}/site-intake/${PLATFORM_SLUG}/demo-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: f.name.trim(), company: f.company.trim() || null, email: f.email.trim() || null,
          phone: f.phone.trim() || null, industry: 'Roofing', message,
          existing_leads: f.leads || null, product: 'EvoSys Pro Roofing',
          page_url: window.location.href, referrer: document.referrer || null,
          submitted_at: new Date().toISOString(), user_agent: navigator.userAgent, submission_id: submissionId,
        }),
      })
      let body = null
      try { body = await res.json() } catch { body = null }
      if (res.ok && body && body.success) {
        setState({ phase: 'done', text: 'Request received. Our team will contact you to schedule your demo.' })
      } else {
        const d = body && body.detail
        setState({ phase: 'error', text: typeof d === 'string' ? d : `We couldn’t send your request (error ${res.status}). Please try again or email support@evosyspro.live.` })
      }
    } catch {
      setState({ phase: 'error', text: 'We couldn’t reach the server. Check your connection and try again, or email support@evosyspro.live.' })
    }
  }

  if (state.phase === 'done') {
    return (
      <div className="rfl-form rfl-form--done" role="status">
        <span className="rfl-done-icon"><Icon name="check" size={26} /></span>
        <h3>Request received</h3>
        <p>{state.text}</p>
      </div>
    )
  }
  return (
    <form className="rfl-form" onSubmit={submit} noValidate>
      <div className="rfl-form__grid">
        <label className="rf-label">Your name<input className="rf-input" value={f.name} onChange={set('name')} autoComplete="name" required /></label>
        <label className="rf-label">Roofing company<input className="rf-input" value={f.company} onChange={set('company')} autoComplete="organization" /></label>
        <label className="rf-label">Email<input className="rf-input" type="email" value={f.email} onChange={set('email')} autoComplete="email" /></label>
        <label className="rf-label">Phone<input className="rf-input" type="tel" value={f.phone} onChange={set('phone')} autoComplete="tel" /></label>
        <label className="rf-label rfl-form__wide">How many existing leads do you have?
          <select className="rf-select" value={f.leads} onChange={set('leads')}>
            <option value="">Choose one</option>
            <option>Under 500</option>
            <option>500 – 2,000</option>
            <option>2,000 – 10,000</option>
            <option>More than 10,000</option>
          </select>
        </label>
        <label className="rf-label rfl-form__wide">Anything we should know?
          <textarea className="rf-textarea" value={f.message} onChange={set('message')} placeholder="Where your leads come from, what follow-up looks like today…" />
        </label>
      </div>
      {state.phase === 'error' && <p className="rfl-form__err" role="alert">{state.text}</p>}
      <button className="rf-btn rf-btn--primary rfl-form__submit" disabled={state.phase === 'sending'}>
        {state.phase === 'sending' ? 'Sending…' : 'Request my demo'}
      </button>
      <p className="rfl-form__fine">We’ll use this only to contact you about EvoSys Pro.</p>
    </form>
  )
}

function MissionPreview() {
  const lanes = [
    { label: 'Human needed', tone: 'red', items: [['Robert White', '“Do you work with insurance claims?”']] },
    { label: 'Inspection requested', tone: 'orange', items: [['James Davis', '“Yes, when can someone come out?”'], ['Sarah Lee', '“What’s the soonest you can come?”']] },
    { label: 'Booked', tone: 'green', items: [['Brian Carter', 'Tue 10:00 AM · Roof inspection']] },
  ]
  return (
    <div className="rfl-preview" aria-label="Sample Mission Control screen">
      <div className="rfl-preview__bar">
        <BrandMark className="rfl-preview__mark" />
        <span>Mission Control</span>
        <span className="rf-sample">Sample data</span>
      </div>
      <div className="rfl-preview__hero">
        <b>Your AI team is working your roofing leads.</b>
        <span>38 follow-ups running, 4 replies today</span>
      </div>
      <div className="rfl-preview__ribbon">
        {[['1,248', 'contacted'], ['25%', 'responded'], ['86', 'inspections confirmed']].map(([v, l]) => (
          <div key={l}><b>{v}</b><span>{l}</span></div>
        ))}
      </div>
      <div className="rfl-preview__lanes">
        {lanes.map((ln) => (
          <div key={ln.label} className="rfl-preview__lane">
            <span className={`rf-pill rf-pill--${ln.tone}`}>{ln.label}</span>
            {ln.items.map(([nm, t]) => (
              <div key={nm} className="rfl-preview__card"><b>{nm}</b><span>{t}</span></div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function RoofingLanding() {
  useFont()
  useEffect(() => { document.title = 'EvoSys Pro Roofing — Turn roofing leads into booked inspections' }, [])
  // Arriving at /roofing#demo (e.g. "Book a live demo" from the demo) lands on the form.
  useEffect(() => { const id = (window.location.hash || '').slice(1); if (id) setTimeout(() => scrollTo(id), 150) }, [])
  return (
    <div className="rf rfl">
      <header className="rfl-hero">
        <HouseAtDusk />
        <nav className="rfl-nav" aria-label="Main">
          <a className="rfl-logo" href="/roofing" aria-label="EvoSys Pro Roofing home">
            <BrandMark />
            <span><span className="rfl-logo__name">EvoSys <span>Pro</span></span><span className="rfl-logo__sub">ROOFING</span></span>
          </a>
          <div className="rfl-nav__links">
            <button type="button" onClick={() => scrollTo('how')}>How it works</button>
            <button type="button" onClick={() => scrollTo('features')}>Features</button>
            <button type="button" onClick={() => scrollTo('preview')}>Product</button>
            <a href="/login">Sign in</a>
          </div>
          <div className="rfl-nav__ctas">
            <Link className="rf-btn rf-btn--on-dark rf-btn--sm rfl-nav__explore" to="/roofing/demo">Explore the demo</Link>
            <button type="button" className="rf-btn rf-btn--primary rf-btn--sm" onClick={() => scrollTo('demo')}>Book a demo</button>
          </div>
        </nav>

        <div className="rfl-hero__inner">
          <div className="rfl-hero__copy">
            <p className="rfl-kicker">AI-powered roofing operations</p>
            <h1 className="rfl-h1">Turn Roofing Leads<br /><span>Into Booked Inspections.</span></h1>
            <p className="rfl-sub">Already have leads? EvoSys Pro helps you turn them into conversations, scheduled inspections, and new business through AI-powered outreach and follow-up.</p>
            <div className="rfl-ctas">
              <Link className="rf-btn rf-btn--primary rfl-cta" to="/roofing/demo"><Icon name="play" size={18} />Explore the Roofing Demo</Link>
              <button type="button" className="rf-btn rf-btn--on-dark rfl-cta" onClick={() => scrollTo('demo')}>Book a demo</button>
            </div>
          </div>
          <div className="rfl-stats" aria-label="Sample results">
            <span className="rf-sample rfl-stats__tag">Sample results</span>
            {STATS.map((s, i) => (
              <div key={s.label} className={`rfl-stat rfl-stat--${i}`}>
                <span className="rfl-stat__icon"><Icon name={s.icon} size={20} /></span>
                <div><b>{s.value}</b><span>{s.label}</span><em>{s.sub}</em></div>
              </div>
            ))}
          </div>
        </div>
      </header>

      <section id="features" className="rfl-features" aria-label="What EvoSys Pro does">
        {FEATURES.map((f) => (
          <div key={f.title} className="rfl-feature">
            <Icon name={f.icon} size={26} />
            <h3>{f.title}</h3>
            <p>{f.text}</p>
          </div>
        ))}
      </section>

      <section className="rfl-position">
        <div className="rfl-wrap rfl-position__inner">
          <h2>Built for the leads you already own.</h2>
          <p>EvoSys Pro doesn’t sell you leads or scrape homeowners. It works the contacts sitting in your spreadsheets, CRM exports and old estimates — the people who already asked about their roof — and turns them into conversations and booked inspections.</p>
        </div>
      </section>

      <section id="how" className="rfl-how">
        <div className="rfl-wrap">
          <h2 className="rfl-h2">How it works</h2>
          <p className="rfl-lede">From the list you upload to the job you win.</p>
          <ol className="rfl-steps">
            {STEPS.map((s, i) => (
              <li key={s.title}>
                <span className="rfl-steps__n" aria-hidden="true">{i + 1}</span>
                <div><h3>{s.title}</h3><p>{s.text}</p></div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="preview" className="rfl-product">
        <div className="rfl-wrap rfl-product__inner">
          <div className="rfl-product__copy">
            <h2 className="rfl-h2">See what your AI team is doing — the moment you log in.</h2>
            <p className="rfl-lede">Mission Control shows who the AI is working, who replied, which inspections are confirmed, and what needs a person. Every percentage shows what it’s measured against.</p>
            <ul className="rfl-checks">
              <li><Icon name="check" size={18} />New replies first — no hunting through message history</li>
              <li><Icon name="check" size={18} />Take over any conversation with one click</li>
              <li><Icon name="check" size={18} />Response rate by touch, so you know which follow-up works</li>
            </ul>
          </div>
          <MissionPreview />
        </div>
        <div className="rfl-wrap rfl-choose">
          <div className="rfl-choose__card">
            <h3>Explore it yourself</h3>
            <p>Click through the real back office with a sample roofing company - Mission Control, AI conversations, inspections with photos, reports and the pipeline. No sign-up.</p>
            <Link className="rf-btn rf-btn--primary" to="/roofing/demo"><Icon name="play" size={18} />Explore the Roofing Demo</Link>
          </div>
          <div className="rfl-choose__card">
            <h3>See it with our team</h3>
            <p>Book a live walkthrough and we’ll show you how EvoSys Pro would work your own lead list.</p>
            <button type="button" className="rf-btn rf-btn--ghost" onClick={() => scrollTo('demo')}>Book a live demo</button>
          </div>
        </div>
      </section>

      <section id="demo" className="rfl-demo">
        <div className="rfl-wrap rfl-demo__inner">
          <div className="rfl-demo__copy">
            <h2 className="rfl-h2">Get a demo with your own leads in mind.</h2>
            <p className="rfl-lede">Tell us a little about your lead list. We’ll show you how EvoSys Pro would work it.</p>
          </div>
          <DemoForm />
        </div>
      </section>

      <footer className="rfl-foot">
        <div className="rfl-wrap rfl-foot__inner">
          <span className="rfl-logo rfl-logo--dark"><BrandMark /><span><span className="rfl-logo__name">EvoSys <span>Pro</span></span><span className="rfl-logo__sub">ROOFING</span></span></span>
          <span>Figures shown on this page are sample results for illustration.</span>
          <a href="mailto:support@evosyspro.live">support@evosyspro.live</a>
        </div>
      </footer>
    </div>
  )
}
