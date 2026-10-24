/**
 * EvoSys Pro Roofing - PUBLIC INTERACTIVE DEMO (/roofing/demo/*).
 *
 * A separate, anonymous shell that looks like the real Roofing back office
 * (same CSS, same components) but runs ONLY on the fictional fixtures in
 * ./fixtures.js. It never uses RoofingShell, ProtectedRoute, the API client,
 * a token or a workspace: there is nothing here that can read or change a
 * real record, send a message or book a slot. Every action that would do
 * something in a live company shows a clearly labelled preview instead.
 */
import { Suspense, useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { BrandMark } from '../RoofingShell'
import { Avatar, Icon, Loading } from '../ui'
import { COMPANY, DEMO_BASE } from './fixtures'
import { BOOK_DEMO, PreviewCtx } from './demoCtx'
import { DemoLeads, DemoMission, DemoPerformance, DemoPipeline, DemoQueue, DemoSetup } from './DemoScreens'
import { DemoConversations, DemoHomeowner } from './DemoPeople'
import { DemoInspection, DemoInspections, DemoReport } from './DemoInspect'
import '../roofing.css'
import '../roofing-ops.css'
import '../roofing-mission.css'
import '../roofing-conv.css'
import '../roofing-inspect.css'
import './demo.css'

const NAV = [
  { to: DEMO_BASE, label: 'Mission Control', icon: 'home', end: true },
  { to: `${DEMO_BASE}/queue`, label: 'AI Work Queue', icon: 'spark' },
  { to: `${DEMO_BASE}/conversations`, label: 'Conversations', icon: 'chat', count: 4 },
  { to: `${DEMO_BASE}/inspections`, label: 'Inspections', icon: 'calendar', count: 2 },
  { to: `${DEMO_BASE}/performance`, label: 'Performance', icon: 'chart' },
  { to: `${DEMO_BASE}/pipeline`, label: 'Pipeline', icon: 'pipeline' },
  { to: `${DEMO_BASE}/leads`, label: 'Leads & Data', icon: 'users' },
  { to: `${DEMO_BASE}/setup`, label: 'Roofing Setup', icon: 'settings' },
]


function PreviewDialog({ msg, onClose }) {
  if (!msg) return null
  return (
    <div className="dm-preview" role="dialog" aria-modal="true" aria-labelledby="dm-preview-title" onClick={onClose}>
      <div className="dm-preview__card rf-panel" onClick={(e) => e.stopPropagation()}>
        <span className="dm-preview__tag">Demo preview</span>
        <h2 id="dm-preview-title" className="rf-panel__title">{msg.title}</h2>
        <p>{msg.body}</p>
        <p className="rf-muted dm-preview__fine">This is a sample company. Nothing was sent, saved or booked.</p>
        <div className="dm-preview__btns">
          <a className="rf-btn rf-btn--ghost" href={BOOK_DEMO}>Book a live demo</a>
          <button className="rf-btn rf-btn--primary" onClick={onClose} autoFocus>Got it</button>
        </div>
      </div>
    </div>
  )
}

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

// ── guided walkthrough ────────────────────────────────────────────────────
export const TOUR = [
  { title: 'A homeowner is added or imported', text: 'Upload your old leads, or add one person who just called.', to: `${DEMO_BASE}/leads` },
  { title: 'AI follows up', text: 'The AI texts and emails the homeowners you’re allowed to contact.', to: `${DEMO_BASE}/queue` },
  { title: 'The homeowner answers', text: 'Replies land in Conversations - newest and most urgent first.', to: `${DEMO_BASE}/conversations?lead=h3` },
  { title: 'An inspection is booked', text: 'The homeowner picks a time and it’s on the calendar.', to: `${DEMO_BASE}/inspections` },
  { title: 'The roof is documented', text: 'On site, the inspector checks the roof, damage and notes on a phone.', to: `${DEMO_BASE}/inspections/i-progress` },
  { title: 'Photos and findings become a report', text: 'A clean, printable report with every photo.', to: `${DEMO_BASE}/inspections/i-1006/report` },
  { title: 'An estimate is recorded', text: 'Sales records the estimate and follows up.', to: `${DEMO_BASE}/homeowners/h5` },
  { title: 'The homeowner accepts', text: 'One tap records the decision.', to: `${DEMO_BASE}/homeowners/h7` },
  { title: 'It becomes a won job', text: 'The pipeline shows every job from first text to signed.', to: `${DEMO_BASE}/pipeline` },
]

function Tour() {
  const nav = useNavigate()
  return (
    <main className="rf-page dm-tour">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">How it works - in 9 steps</h1>
          <p className="rf-lede">Follow one sample homeowner from a lead to a won roofing job. Each step opens the real screen.</p>
        </div>
      </div>
      <ol className="dm-steps">
        {TOUR.map((s, i) => (
          <li key={s.title}>
            <button type="button" className="dm-step rf-panel" onClick={() => nav(s.to)}>
              <span className="dm-step__n">{i + 1}</span>
              <span className="dm-step__txt"><b>{s.title}</b><span>{s.text}</span></span>
              <span className="dm-step__go">Show me <Icon name="arrow" size={16} /></span>
            </button>
          </li>
        ))}
      </ol>
      <div className="dm-tour__foot">
        <Link className="rf-btn rf-btn--primary" to={TOUR[0].to}><Icon name="play" size={16} />Start with step 1</Link>
        <Link className="rf-btn rf-btn--ghost" to={DEMO_BASE}>Explore freely</Link>
        <a className="rf-btn rf-btn--ghost" href={BOOK_DEMO}>Book a live demo</a>
        <a className="rf-btn rf-btn--ghost" href="/roofing">Roofing homepage</a>
      </div>
    </main>
  )
}

/** Small "you are on step N" bar when a visitor arrives from the tour. */
function TourBar() {
  const loc = useLocation()
  const here = loc.pathname + loc.search
  const i = TOUR.findIndex((s) => s.to === here)
  if (i < 0) return null
  const next = TOUR[i + 1]
  return (
    <div className="dm-tourbar" role="note">
      <span className="dm-tourbar__n">Step {i + 1} of {TOUR.length}</span>
      <span className="dm-tourbar__t"><b>{TOUR[i].title}.</b> {TOUR[i].text}</span>
      <span className="dm-tourbar__btns">
        {i > 0 && <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={TOUR[i - 1].to}>Back</Link>}
        {next ? <Link className="rf-btn rf-btn--primary rf-btn--sm" to={next.to}>Next step</Link>
          : <a className="rf-btn rf-btn--primary rf-btn--sm" href={BOOK_DEMO}>Book a live demo</a>}
        <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${DEMO_BASE}/tour`}>All steps</Link>
      </span>
    </div>
  )
}

function NotFound() {
  return (
    <main className="rf-page">
      <section className="rf-panel" style={{ padding: 24 }}>
        <h1 className="rf-h1">That screen isn’t part of the demo</h1>
        <p className="rf-lede">Pick a screen on the left, or start from Mission Control.</p>
        <Link className="rf-btn rf-btn--primary" to={DEMO_BASE} style={{ marginTop: 14 }}>Go to Mission Control</Link>
      </section>
    </main>
  )
}

export default function DemoApp() {
  useFont()
  const loc = useLocation()
  const [open, setOpen] = useState(false)
  const [msg, setMsg] = useState(null)
  useEffect(() => { setOpen(false); window.scrollTo(0, 0) }, [loc.pathname])
  useEffect(() => { document.title = 'EvoSys Pro Roofing - Interactive Demo' }, [])
  const preview = (title, body) => setMsg({ title, body })

  return (
    <PreviewCtx.Provider value={preview}>
      <div className="rf rf-app dm">
        {open && <div className="rf-scrim" onClick={() => setOpen(false)} />}
        <aside className={`rf-side ${open ? 'open' : ''}`} aria-label="Demo navigation">
          <a className="rf-brand" href="/roofing" aria-label="EvoSys Pro Roofing homepage">
            <BrandMark />
            <span>
              <span className="rf-brand__name">EvoSys <span>Pro</span></span>
              <span className="rf-brand__sub">ROOFING</span>
            </span>
          </a>
          <div className="rf-company">
            <b>{COMPANY.name}</b>
            <span className="rf-sample">Sample company</span>
          </div>
          <nav className="rf-nav">
            {NAV.map((it) => (
              <NavLink key={it.to} to={it.to} end={it.end}>
                <Icon name={it.icon} size={19} />
                {it.label}
                {it.count ? <span className="rf-nav__count">{it.count}</span> : null}
              </NavLink>
            ))}
          </nav>
          <div className="dm-side__cta">
            <Link className="rf-btn rf-btn--primary" to={`${DEMO_BASE}/tour`}><Icon name="play" size={16} />Start here</Link>
            <a className="rf-btn rf-btn--on-dark" href={BOOK_DEMO}>Book a live demo</a>
          </div>
          <div className="rf-side__foot">
            <a href="/roofing"><Icon name="roof" size={16} />Roofing homepage</a>
          </div>
        </aside>
        <div className="rf-main">
          <header className="rf-top">
            <button className="rf-btn rf-btn--ghost rf-btn--sm rf-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation">
              <Icon name="menu" size={18} />
            </button>
            <span className="rf-top__title">EvoSys Pro Roofing <span className="dm-top__sub">- Interactive Demo</span></span>
            <span className="dm-badge" title="Every name, number and photo here is fictional"><span className="dm-hide-sm">Sample data · interactive demonstration</span><span className="dm-show-sm">Sample demo</span></span>
            <div className="rf-top__right">
              <Link className="rf-btn rf-btn--ghost rf-btn--sm dm-hide-sm" to={`${DEMO_BASE}/tour`}><Icon name="play" size={15} />Start here</Link>
              <a className="rf-btn rf-btn--primary rf-btn--sm" href={BOOK_DEMO}><span className="dm-hide-xs">Book a live demo</span><span className="dm-show-xs">Book demo</span></a>
              <span className="rf-me dm-hide-sm">
                <Avatar name="Dana Whitaker" size={34} />
                <div><b>Dana Whitaker</b><span>Owner (sample)</span></div>
              </span>
            </div>
          </header>
          <TourBar />
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route index element={<DemoMission />} />
              <Route path="tour" element={<Tour />} />
              <Route path="queue" element={<DemoQueue />} />
              <Route path="conversations" element={<DemoConversations />} />
              <Route path="homeowners/:id" element={<DemoHomeowner />} />
              <Route path="inspections" element={<DemoInspections />} />
              <Route path="inspections/:id" element={<DemoInspection />} />
              <Route path="inspections/:id/report" element={<DemoReport />} />
              <Route path="performance" element={<DemoPerformance />} />
              <Route path="pipeline" element={<DemoPipeline />} />
              <Route path="leads" element={<DemoLeads />} />
              <Route path="setup" element={<DemoSetup />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        </div>
        <PreviewDialog msg={msg} onClose={() => setMsg(null)} />
      </div>
    </PreviewCtx.Provider>
  )
}
