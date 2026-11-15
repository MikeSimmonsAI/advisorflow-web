import { VOLT_SUPPORT_EMAIL } from '../../utils/voltDomain'
import { VOLT_IMAGES } from './voltImages'
/* VOLT MARKETING - public applicant portal and rep dashboard (#535).
 *
 * /volt                      open opportunities, Register & Apply, Sign In
 * /volt?project=aca         goes to that project's application (an old ?ref= is ignored:
 *                            referrals are not launching, nothing is attributed)
 * /volt/apply/:projectKey    register (or start a profile / a second project)
 * /volt/sign-in              applicant sign-in
 * /volt/me                   My Application (?application=<id> selects one)
 * /volt/me/profile           My Information
 * /volt/me/support           Support
 * /volt/me/applications/:id  one project application: draft, documents, submit
 *
 * Public navigation carries applicant actions only. Staff reach VOLT
 * Operations through Program Access, never through a link here. Every rule
 * (closed projects, one application per project, access) is enforced
 * on the server; this screen only makes the right action the easy one.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { setVoltToken, useVoltTitle, volt, voltToken } from './voltApi'
import ApplicationSteps, { DocRow } from './VoltApplication'
import { ApplyDialog, TalentDialog } from './VoltDialogs'
import MyApplication, { SupportPage } from './VoltMyApplication'
import './volt.css'

// Referrals are not launching (2026-11-06): any saved invite from an older
// visit is dropped, and an old ?ref= link is an ordinary visit.
try { sessionStorage.removeItem('af_volt_invite') } catch { /* private window */ }

export function Bolt() { return <span className="v-bolt" aria-hidden="true" /> }

export function Wordmark({ to = '/volt' }) {
  return (
    <Link className="v-brand" to={to} aria-label="VOLT Marketing home">
      <Bolt /><span className="v-word">VOLT</span> <span className="v-word-sub">Marketing</span>
    </Link>
  )
}

function Shell({ children, signedIn, onSignOut, onApply, showApply, onTalent }) {
  return (
    <div className={`volt${showApply ? ' volt--applybar' : ''}`}>
      <header className="v-head v-head--sticky">
        <div className="v-wrap">
          <Wordmark />
          <nav aria-label="Applicant">
            {showApply && <Link className="v-navlink" to="/volt#projects">Opportunities</Link>}
            {showApply && <Link className="v-navlink" to="/volt#why">Why VOLT</Link>}
            {signedIn ? (
              <>
                <Link className="v-btn v-btn--ghost v-btn--sm" to="/volt/me">My Application</Link>
                <button type="button" className="v-btn v-btn--ghost v-btn--sm" onClick={onSignOut}>Sign out</button>
              </>
            ) : (
              <Link className="v-btn v-btn--ghost v-btn--sm" to="/volt/sign-in">Sign In</Link>
            )}
            {showApply && <button type="button" className="v-btn v-btn--red v-btn--sm v-apply-head" onClick={onApply} data-apply="header">Apply Now</button>}
          </nav>
        </div>
      </header>
      <main className="v-wrap" style={{ flex: 1 }}>{children}</main>
      <footer className="v-foot">
        <div className="v-wrap"><span>VOLT Marketing · Vision · Ownership · Leadership · Tenacity<br /><span className="v-foot-note">Photos on this site are illustrative.</span></span>
          <span>{onTalent && <button type="button" className="v-btn v-btn--link v-foot-link" onClick={onTalent}>Get Future Opportunities</button>} Need help? <a href={`mailto:${VOLT_SUPPORT_EMAIL}`}>{VOLT_SUPPORT_EMAIL}</a></span></div>
      </footer>
      {showApply && (
        <div className="v-applybar" role="region" aria-label="Apply">
          <button type="button" className="v-btn v-btn--red" onClick={onApply} data-apply="bar">Apply Now</button>
        </div>
      )}
    </div>
  )
}

function HeroArt() {
  const rings = []
  for (let i = 0; i < 18; i += 1) {
    const r = 12 + i * 11
    rings.push(<ellipse key={i} cx="230" cy="125" rx={r * 1.5} ry={r * 0.82} transform="rotate(-28 230 125)" fill="none" stroke="#e01933" opacity=".22" />)
  }
  return (
    <div className="v-hero-art" aria-hidden="true">
      <svg viewBox="0 0 400 245" preserveAspectRatio="xMidYMid slice">
        {rings}
        <path d="M46 190 L100 161 155 172 209 110 262 113 315 60" fill="none" stroke="#e01933" strokeWidth="3" strokeDasharray="7 4" />
        <g fill="#e01933"><circle cx="46" cy="190" r="6" /><circle cx="209" cy="110" r="5" /><circle cx="315" cy="60" r="8" /></g>
      </svg>
    </div>
  )
}

function Loading({ label = 'Loading' }) {
  return <div className="v-center" role="status" aria-live="polite"><p className="v-mono">{label}…</p></div>
}

function Alert({ kind = 'err', children }) {
  if (!children) return null
  return <div className={`v-alert v-alert--${kind}`} role={kind === 'err' ? 'alert' : 'status'}>{children}</div>
}

function Home({ portal, signedIn, onApply, onTalent }) {
  const loc = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    // Header "Opportunities" / "Why VOLT" and hero "Explore Opportunities".
    const id = (loc.hash || '').slice(1)
    if (id) setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 30)
  }, [loc.hash])
  useEffect(() => {
    // An old project link lands on that project's ordinary application.
    const q = new URLSearchParams(loc.search)
    const p = q.get('project')
    if (p && portal.open_projects.some((x) => x.key === p)) navigate(`/volt/apply/${p}`, { replace: true })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <section className="v-bleed v-phero" aria-labelledby="v-home-title">
        <div className="v-phero-in">
          <article>
            <h1 id="v-home-title">Good people.<br />Big ambition.<br /><span className="v-red">Your next move.</span></h1>
            <p>Build your skills. Meet your people. Explore what’s next with VOLT.</p>
            <div className="v-row" style={{ justifyContent: 'flex-start' }}>
              <button type="button" className="v-btn v-btn--red v-btn--lg" onClick={onApply} data-apply="hero">Apply Now</button>
              <Link className="v-btn v-btn--ghost v-btn--lg" to="/volt#projects" data-explore-all>Explore Opportunities</Link>
            </div>
            <p className="v-small" style={{ marginTop: 14 }}>
              {signedIn ? <Link to="/volt/me">Go to My Application →</Link> : <>Already applied? <Link to="/volt/sign-in">Sign In</Link> to resume.</>}
            </p>
          </article>
          <Photo img={VOLT_IMAGES.hero} className="v-phero-photo" eager data-photo="hero" />
        </div>
        <p className="v-values-strip" aria-label="VOLT values: Vision, Ownership, Leadership, Tenacity">
          <span>Vision</span><i aria-hidden="true">/</i><span>Ownership</span><i aria-hidden="true">/</i><span>Leadership</span><i aria-hidden="true">/</i><span>Tenacity</span>
        </p>
      </section>

      <div className="v-section-head v-section-head--center" id="projects">
        <h2>Find your <span className="v-red">opportunity.</span></h2>
      </div>
      <div className="v-grid2 v-pcards">
        {orderedProjects(portal.open_projects).map((p) => {
          const pic = VOLT_IMAGES.cards[p.key]
          return (
            <article className={`v-pcard${pic ? '' : ' v-pcard--plain'}`} key={p.key} data-project={p.key}>
              {pic && <Photo img={pic} className="v-pcard-photo" data-photo={`card-${p.key}`} />}
              <div className="v-pcard-body">
                <span className="v-tag">● Recruiting</span>
                <h3>{p.name}</h3>
                <p>{CARD[p.key]?.line || CARD[p.key]?.text || p.summary}</p>
                <div className="v-row" style={{ justifyContent: 'flex-start' }}>
                  <Link className="v-btn v-btn--red" to={`/volt/apply/${p.key}`} data-apply-card={p.key}>{signedIn ? 'Apply / resume' : 'Apply Now'}</Link>
                  <Link className="v-btn v-btn--ghost" to={`/volt/projects/${p.key}`} data-explore={p.key}>Explore</Link>
                </div>
              </div>
            </article>
          )
        })}
      </div>

      <WhyVolt />

      <section className="v-bleed v-pbanner" aria-label="Work hard. Grow together. Enjoy the journey.">
        <Photo img={VOLT_IMAGES.banner} className="v-pbanner-photo" data-photo="banner" />
        <div className="v-pbanner-text">
          <p>Work hard.<br />Grow together.<br /><span className="v-red">Enjoy the journey.</span></p>
        </div>
      </section>
      <section className="v-bleed v-pcta" data-closing>
        <h2>Your next chapter starts here.</h2>
        <div className="v-row" style={{ justifyContent: 'center' }}>
          <button type="button" className="v-btn v-btn--white v-btn--lg" onClick={onApply} data-apply="closing">Apply Now</button>
          <button type="button" className="v-btn v-btn--outline-white v-btn--lg" onClick={onTalent} data-talent="section">Get Future Opportunities</button>
        </div>
      </section>
      {portal.past_projects.map((p) => (
        <section className="v-card v-past" key={p.key} data-project={p.key}>
          <div><span className="v-mono">Past project</span><h3>{p.name}</h3></div>
          <span className="v-tag v-tag--black">Recruiting closed</span>
        </section>
      ))}
    </>
  )
}

// ── Homepage project cards (approved demo copy) ─────────────────────────────
const CARD = {
  // ACA Healthcare Outreach (Mike, 2026-11-06): outreach and enrollment
  // assistance, not insurance sales; no insurance license required.
  aca: { short: 'ACA', line: 'Connect people with coverage. No license required.',
    text: 'Help connect eligible people with ACA health coverage and guide them toward enrollment assistance. No insurance license required for this outreach role.',
    facts: ['Healthcare outreach', 'No license required'] },
  fiber: { short: 'Fiber', line: 'Door-to-door. On the road. Room to grow.', text: 'A traveling door-to-door fiber sales opportunity. Tell us where you’re based and when you can travel.',
    facts: ['Door-to-door sales', 'Traveling gig'] },
}

// ── People imagery (src/pages/volt/voltImages.js) ──────────────────────────
// Intrinsic width/height reserve the space; only the hero loads eagerly.
function Photo({ img, className, eager = false, ...rest }) {
  if (!img) return null
  return (
    <img className={className} src={img.src} srcSet={img.srcSet} sizes={img.sizes} width={img.width} height={img.height}
         alt={img.alt} loading={eager ? 'eager' : 'lazy'} decoding="async" fetchpriority={eager ? 'high' : undefined}
         style={{ objectPosition: img.position }} {...rest} />
  )
}

// The approved mockup leads with Traveling Fiber Sales, then ACA.
const CARD_ORDER = ['fiber', 'aca']
function orderedProjects(list) {
  return [...list].sort((a, b) => {
    const ia = CARD_ORDER.indexOf(a.key), ib = CARD_ORDER.indexOf(b.key)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
  })
}

// ── Why build your future with VOLT? (homepage, Mike 2026-11-06) ───────────
// Company-wide reasons only. The Fiber reasons (Great markets; Industry-leading
// people and leadership; Training and development) belong to the Fiber Explore
// page and are not repeated here; nothing here implies every opportunity
// involves travel or door-to-door sales.
const WHY_VOLT = [
  ['01', 'Find your opportunity.', 'Explore different projects that fit your ambition and strengths.'],
  ['02', 'Build toward freedom.', 'Develop experience and confidence that give you more control over your future.'],
  ['03', 'Grow around driven people.', 'Surround yourself with people who push you to improve and take your next step.'],
]

function WhyVolt() {
  return (
    <section className="v-why v-bleed" id="why" data-why-volt aria-labelledby="v-why-title">
      <div className="v-why-in">
      <h2 id="v-why-title">Why build your future with <span className="v-red">VOLT?</span></h2>
      <ol className="v-why-grid">
        {WHY_VOLT.map(([n, t, d]) => (
          <li key={n}><span className="v-why-n" aria-hidden="true">{n}</span><div><h3>{t}</h3><p>{d}</p></div></li>
        ))}
      </ol>
      <p className="v-why-values"><span>Vision</span> / <span>Ownership</span> / <span>Leadership</span> / <span>Tenacity</span></p>
      <p className="v-why-own">Ownership means freedom, independence and control over your future.</p>
      <div className="v-why-photos">
        {VOLT_IMAGES.reasons.map((r, i) => <Photo key={r.src} img={r} className="v-why-photo" data-photo={`reason-${i + 1}`} />)}
      </div>
      </div>
    </section>
  )
}

// ── What VOLT stands for (both Explore pages; approved demo) ─────────────────
const VOLT_VALUES = [
  ['V', 'Vision', 'Know what you’re building and where you want to go.'],
  ['O', 'Ownership', 'Own your future. Build toward greater freedom, independence, and control over the direction of your life.'],
  ['L', 'Leadership', 'Set the example and help others develop.'],
  ['T', 'Tenacity', 'Stay disciplined, work through setbacks, and keep improving.'],
]

function VoltValues() {
  return (
    <section className="v-values" data-volt-meaning aria-labelledby="v-values-title">
      <span className="v-eyebrow">The name is the standard</span>
      <h2 id="v-values-title">What VOLT stands for.</h2>
      <div className="v-valuegrid">
        {VOLT_VALUES.map(([l, w, d]) => (
          <article key={l} data-letter={l}><b aria-hidden="true">{l}</b><h3>{w}</h3><p>{d}</p></article>
        ))}
      </div>
    </section>
  )
}

const PROJECT_DETAIL = {
  aca: 'Help connect eligible people with ACA health coverage and guide them toward enrollment assistance. No insurance license required for this outreach role; our team reviews every application.',
  fiber: 'A traveling door-to-door fiber sales opportunity. The application asks about travel readiness, door-to-door experience and coachability; our team reviews every application.',
}

// ── Explore pages (approved demo, 2026-11-06) ────────────────────────────────
// Confirmed terms only. No pay, housing, expense, assignment-length, licensing
// sponsorship or employment claims; no statistics, named records or
// testimonials. Applicants apply to VOLT Marketing.
function ExploreHero({ p, eyebrow, title, text, applyLabel }) {
  return (
    <section className="v-hero v-explore-hero">
      <article>
        <span className="v-eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{text}</p>
        {p.recruiting
          ? <Link className="v-btn v-btn--red v-btn--lg" to={`/volt/apply/${p.key}`} data-apply="project">{applyLabel}</Link>
          : <p>This project is no longer accepting applications.</p>}
      </article>
      <HeroArt />
    </section>
  )
}

function Pillar({ id, eyebrow, title, children }) {
  return (
    <section className="v-pillar" data-pillar={id}>
      <span className="v-eyebrow">{eyebrow}</span>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

function ExploreClose({ p, title, text, applyLabel, onTalent }) {
  return (
    <section className="v-talent-band v-talent-band--stack">
      {title.eyebrow && <span className="v-eyebrow">{title.eyebrow}</span>}
      <h2>{title.h2}</h2>
      {text.map((t) => <p key={t}>{t}</p>)}
      <div className="v-row" style={{ justifyContent: 'flex-start' }}>
        {p.recruiting && <Link className="v-btn v-btn--red" to={`/volt/apply/${p.key}`} data-apply="close">{applyLabel}</Link>}
        {onTalent && <button type="button" className="v-btn v-btn--ghost" onClick={onTalent} data-talent="opportunity">Get Future Opportunities</button>}
      </div>
    </section>
  )
}

function FiberOpportunity({ p, onTalent }) {
  return (
    <div className="v-opp" data-opportunity="fiber">
      <ExploreHero p={p} eyebrow="Traveling T-Mobile Fiber D2D Sales"
        title={<>Great markets.<br />Industry leaders.<br /><span className="v-red">Serious training.</span></>}
        text="Take your ambition to the doors. Build your sales career around great markets, record-breaking professionals, and training that carries into the field."
        applyLabel="Apply for Traveling Fiber Sales →" />
      <div className="v-page">
        <div className="v-section-head v-section-head--stack" data-reasons>
          <h2>Three reasons to sell fiber with VOLT.</h2>
          <p className="v-muted">Great Markets. Industry-Leading People and Leadership. Training and Development.</p>
        </div>
        <Pillar id="markets" eyebrow="Reason 1 · Great Markets" title="Put your effort in the right place.">
          <p>Good selling starts before the first knock. Knowing where fiber is available, understanding the neighborhood, and having clear territory direction gives every conversation a purpose.</p>
          <p className="v-muted">Our focus is matching a prepared team with a clear market plan. Your recruiter will walk you through the available assignment.</p>
        </Pillar>
        <Pillar id="people" eyebrow="Reason 2 · Industry-Leading People and Leadership" title="The best in the industry work here.">
          <p data-best>Work alongside professionals breaking industry records. Learn from their preparation, discipline, and execution, with leadership that sets clear expectations and holds you accountable.</p>
          <p className="v-muted">Bring the ambition to build your own future and the tenacity to improve. This is where ambition meets a high standard of work.</p>
        </Pillar>
        <Pillar id="training" eyebrow="Reason 3 · Training and Development" title={<>Learn it. Practice it.<br />Take it to the doors.</>}>
          <p>Understand the product. Learn how to open a conversation, listen to a homeowner, explain the offer, work through objections respectfully, and complete an accurate order.</p>
          <p className="v-muted">Role-play, feedback, and consistent practice connect what you learn with what you do in the field.</p>
        </Pillar>
        <ExploreClose p={p} onTalent={onTalent} applyLabel="Apply Now →"
          title={{ eyebrow: 'The work', h2: 'Travel. Prepare. Knock. Improve.' }}
          text={['You’ll travel to an assigned market, prepare with the team, meet homeowners door to door, explain fiber service, and follow through accurately. Coachability, persistence, integrity, and professionalism matter here.',
            'This opportunity requires travel. Discuss assignment length, transportation, housing, compensation, and expenses with your recruiter before committing.']} />
        <VoltValues />
        <Link to="/volt" className="v-btn v-btn--line v-btn--sm">← All opportunities</Link>
      </div>
    </div>
  )
}

function AcaOpportunity({ p, onTalent }) {
  // Outreach, not sales (Mike, 2026-11-06). Accurate about coverage: no
  // promise of free care; eligibility, plans and costs vary. Outreach workers
  // are not described as licensed or certified enrollment assisters.
  return (
    <div className="v-opp" data-opportunity="aca">
      <ExploreHero p={p} eyebrow="ACA Healthcare Outreach"
        title={<>Connect people<br /><span className="v-red">with coverage.</span></>}
        text="Help connect eligible people with ACA health coverage and guide them toward enrollment assistance. No insurance license required for this outreach role."
        applyLabel="Apply for ACA Outreach →" />
      <div className="v-page">
        <div className="v-section-head v-section-head--stack">
          <h2>Explore ACA Healthcare Outreach with VOLT.</h2>
          <p className="v-muted">Community outreach built around clear conversations, accurate information and real follow-through.</p>
        </div>
        <Pillar id="work" eyebrow="The work" title="Reach out. Explain next steps. Connect people with help.">
          <p>Help eligible individuals explore health coverage options, including $0-premium plans where available. Share accurate information, answer general questions about next steps, and connect people with enrollment assistance.</p>
          <p className="v-muted">Eligibility, plan availability and costs vary by person and location. This is an outreach role: you don't sell insurance, recommend plans or complete enrollments.</p>
        </Pillar>
        <Pillar id="development" eyebrow="Leadership & development" title="Grow your skills with a driven team.">
          <p>Learn to start conversations, listen, explain next steps clearly and keep accurate records, with direct feedback from leadership and the people working beside you.</p>
        </Pillar>
        <Pillar id="who" eyebrow="Who should apply" title={<>No insurance license required.<br />Bring your people skills.</>}>
          <p>Community outreach, customer service or experience helping people access resources helps, and so does speaking more than one language. No experience yet? Tell us why this work interests you.</p>
        </Pillar>
        <VoltValues />
        <ExploreClose p={p} onTalent={onTalent} applyLabel="Apply for ACA Outreach →"
          title={{ h2: 'Take the next step.' }}
          text={['Apply for ACA Healthcare Outreach, or leave your details for future projects.']} />
        <Link to="/volt" className="v-btn v-btn--line v-btn--sm">← All opportunities</Link>
      </div>
    </div>
  )
}

function ProjectPage({ portal, onTalent }) {
  const { projectKey } = useParams()
  const p = [...portal.open_projects, ...portal.past_projects].find((x) => x.key === projectKey)
  if (!p) return <Navigate to="/volt" replace />
  if (p.key === 'fiber') return <FiberOpportunity p={p} onTalent={onTalent} />
  if (p.key === 'aca') return <AcaOpportunity p={p} onTalent={onTalent} />
  return (
    <div className="v-page" style={{ maxWidth: 720 }}>
      <Link to="/volt" className="v-btn v-btn--line v-btn--sm">← All opportunities</Link>
      <div className="v-row" style={{ marginTop: 16 }}><span className="v-icon" aria-hidden="true">{p.icon}</span>
        <span className={`v-tag ${p.recruiting ? '' : 'v-tag--black'}`}>{p.recruiting ? '● Recruiting' : 'Recruiting closed'}</span></div>
      <h1 style={{ fontSize: 'clamp(32px,5vw,48px)', letterSpacing: '-.05em', margin: '10px 0' }}>{p.name}</h1>
      <p style={{ fontSize: 18 }}>{p.summary}.</p>
      {PROJECT_DETAIL[p.key] && <p className="v-muted" style={{ marginTop: 10 }}>{PROJECT_DETAIL[p.key]}</p>}
      {p.recruiting
        ? <Link className="v-btn v-btn--red v-btn--lg" to={`/volt/apply/${p.key}`} style={{ marginTop: 18 }} data-apply="project">Apply Now</Link>
        : <p style={{ marginTop: 18 }}>This project is no longer accepting applications.</p>}
    </div>
  )
}

// ── Apply (register / start profile / second project) ───────────────────────
function ProfileFields({ v, set, errs, withEmail, withPassword }) {
  const f = (name, label, props = {}) => (
    <label htmlFor={`v-${name}`}>{label}
      <input id={`v-${name}`} name={name} value={v[name] || ''} aria-invalid={errs[name] ? 'true' : undefined}
             aria-describedby={errs[name] ? `v-${name}-e` : undefined} onChange={(e) => set({ ...v, [name]: e.target.value })} {...props} />
      {errs[name] && <span className="v-ferr" id={`v-${name}-e`}>{errs[name]}</span>}
    </label>
  )
  return (
    <>
      <div className="v-split">
        {f('first_name', 'First Name', { autoComplete: 'given-name', maxLength: 70, required: true })}
        {f('last_name', 'Last Name', { autoComplete: 'family-name', maxLength: 70, required: true })}
      </div>
      {withEmail && f('email', 'Email', { type: 'email', autoComplete: 'email', inputMode: 'email', required: true })}
      <div className="v-split">
        {f('phone', 'Phone Number', { type: 'tel', autoComplete: 'tel', inputMode: 'tel', placeholder: '(555) 010-0100', maxLength: 24, required: true })}
        {f('date_of_birth', 'Date of Birth', { type: 'date', autoComplete: 'bday', max: new Date().toLocaleDateString('sv-SE'), required: true })}
      </div>
      {withPassword && f('password', 'Create a password', { type: 'password', autoComplete: 'new-password', minLength: 8, required: true })}
    </>
  )
}

function Apply({ portal, me, reload }) {
  const { projectKey } = useParams()
  const navigate = useNavigate()
  const project = [...portal.open_projects, ...portal.past_projects].find((p) => p.key === projectKey)
  const signedIn = !!voltToken()
  const existing = me?.applications?.find((a) => a.project?.key === projectKey)
  const [v, setV] = useState({})
  const [errs, setErrs] = useState({})
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [exists, setExists] = useState(false)

  useEffect(() => { if (existing) navigate(`/volt/me/applications/${existing.id}`, { replace: true }) }, [existing, navigate])
  if (!project) return <div className="v-page"><Alert>That project was not found. <Link to="/volt">See open projects</Link></Alert></div>
  if (!project.recruiting) {
    return (
      <div className="v-page"><div className="v-panel"><span className="v-mono">Past project</span><h2>{project.name}</h2>
        <p style={{ margin: '10px 0' }}><span className="v-tag v-tag--black">Recruiting closed</span></p>
        <p>This project is no longer accepting applications.</p><Link className="v-btn v-btn--red" to="/volt" style={{ marginTop: 12 }}>See open projects</Link></div></div>
    )
  }
  if (existing) return <Loading />
  const hasProfile = !!me?.has_profile

  async function submit(e) {
    e.preventDefault()
    setErr(''); setErrs({}); setExists(false)
    setBusy(true)
    try {
      let appId
      if (hasProfile) {
        const a = await volt.startApplication({ project_key: projectKey })
        appId = a.id
      } else if (signedIn) {
        const r = await volt.createProfile({ ...v, project_key: projectKey })
        appId = r.application_id
      } else {
        const r = await volt.register({ ...v, project_key: projectKey })
        setVoltToken(r.access_token)
        appId = r.application_id
      }
      await reload()
      navigate(`/volt/me/applications/${appId}`)
    } catch (x) {
      if (x.detail?.code === 'account_exists') setExists(true)
      setErrs(x.detail?.fields || {})
      setErr(x.message)
    } finally { setBusy(false) }
  }
  return (
    <div className="v-page" style={{ maxWidth: 640 }}>
      <span className="v-mono">{project.short_name}</span>
      <h2 style={{ fontSize: 30, letterSpacing: '-.04em', margin: '4px 0 6px' }}>{hasProfile ? 'Apply to ' + project.name : 'Register & Apply'}</h2>
      {hasProfile
        ? <p className="v-muted">You will use your existing VOLT profile.</p>
        : <p className="v-muted">One profile for every VOLT opportunity. Each application gets its own reference.</p>}
      <form className="v-form v-panel" onSubmit={submit} noValidate style={{ marginTop: 14 }}>
        {!hasProfile && <ProfileFields v={v} set={setV} errs={errs} withEmail={!signedIn} withPassword={!signedIn} />}
        {exists ? (
          <Alert>This email already has an account. <Link to={`/volt/sign-in?next=/volt/apply/${projectKey}`}>Sign in to continue</Link> or <Link to="/forgot-password?next=/volt/sign-in">reset your password</Link>.</Alert>
        ) : <Alert>{err && !Object.keys(errs).length ? err : (Object.keys(errs).length ? 'Please fix the highlighted fields.' : '')}</Alert>}
        <button className="v-btn v-btn--red" type="submit" disabled={busy}>{busy ? 'Saving…' : hasProfile ? 'Start application →' : signedIn ? 'Create profile & start →' : 'Create account & start →'}</button>
        {!signedIn && <p className="v-small">Already registered? <Link to={`/volt/sign-in?next=/volt/apply/${projectKey}`}>Sign in</Link></p>}
      </form>
    </div>
  )
}

// ── Sign in ─────────────────────────────────────────────────────────────────
function SignIn({ reload }) {
  const navigate = useNavigate()
  const loc = useLocation()
  const next = new URLSearchParams(loc.search).get('next')
  const [v, setV] = useState({ email: '', password: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  async function go(e) {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const r = await volt.login(v)
      setVoltToken(r.access_token)
      await reload()
      const safeNext = next && next.startsWith('/volt/') ? next : null
      if (safeNext) navigate(safeNext)
      else navigate(r.has_profile ? '/volt/me' : '/volt#projects')
    } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="v-page" style={{ maxWidth: 460 }}>
      <h2 style={{ fontSize: 30, letterSpacing: '-.04em', marginBottom: 12 }}>Sign In</h2>
      <form className="v-form v-panel" onSubmit={go}>
        <label htmlFor="v-si-email">Email<input id="v-si-email" type="email" autoComplete="email" required value={v.email} onChange={(e) => setV({ ...v, email: e.target.value })} /></label>
        <label htmlFor="v-si-pass">Password<input id="v-si-pass" type="password" autoComplete="current-password" required value={v.password} onChange={(e) => setV({ ...v, password: e.target.value })} /></label>
        <Alert>{err}</Alert>
        <button className="v-btn v-btn--red" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign In →'}</button>
        <p className="v-small"><Link to="/forgot-password?next=/volt/sign-in">Forgot your password?</Link> · New here? <Link to="/volt#projects">Register & Apply</Link></p>
      </form>
    </div>
  )
}

function ProfilePage({ me, reload }) {
  const [v, setV] = useState(() => ({ first_name: me?.profile?.first_name, last_name: me?.profile?.last_name, phone: me?.profile?.phone, date_of_birth: me?.profile?.date_of_birth }))
  const [errs, setErrs] = useState({})
  const [msg, setMsg] = useState('')
  if (!me?.has_profile) return <Navigate to="/volt" replace />
  async function save(e) {
    e.preventDefault(); setErrs({}); setMsg('')
    try { await volt.updateProfile(v); await reload(); setMsg('Profile saved.') } catch (x) { setErrs(x.detail?.fields || {}); setMsg(x.message) }
  }
  return (
    <div className="v-page" style={{ maxWidth: 640 }}>
      <Link to="/volt/me" className="v-btn v-btn--line v-btn--sm">← My Application</Link>
      <h2 style={{ fontSize: 30, letterSpacing: '-.04em', margin: '14px 0 4px' }}>My information</h2>
      <p className="v-muted">{me.profile.email}</p>
      <form className="v-form v-panel" onSubmit={save} noValidate style={{ marginTop: 12 }}>
        <ProfileFields v={v} set={setV} errs={errs} />
        <Alert kind={Object.keys(errs).length ? 'err' : 'ok'}>{msg}</Alert>
        <button className="v-btn v-btn--red" type="submit">Save profile</button>
      </form>
      <section className="v-panel">
        <h3>Profile documents</h3>
        {me.profile_documents.length === 0 ? <p className="v-muted">No profile documents requested.</p>
          : me.profile_documents.map((d) => <DocRow key={d.key} d={d} applicationId={null} editable onDone={reload} />)}
      </section>
    </div>
  )
}

// ── Root ────────────────────────────────────────────────────────────────────
const AUTO_TALENT_MS = 10000
let autoTalentDone = false   // module scope: once per page load
export default function VoltPortal() {
  const [portal, setPortal] = useState(null)
  const [me, setMe] = useState(null)
  const [err, setErr] = useState('')
  const [ready, setReady] = useState(false)
  const navigate = useNavigate()
  const loc = useLocation()

  const reload = useCallback(async () => {
    if (!voltToken()) { setMe(null); return null }
    try { const m = await volt.me(); setMe(m); return m } catch (x) { if (x.status === 401 || x.status === 403) { setVoltToken(null); setMe(null) } return null }
  }, [])
  useEffect(() => {
    Promise.all([volt.portal().then(setPortal), reload()]).catch((x) => setErr(x.message)).finally(() => setReady(true))
  }, [reload])
  useVoltTitle()
  // Returning reps land in their dashboard.
  useEffect(() => {
    if (ready && me?.has_profile && loc.pathname === '/volt' && !loc.search) navigate('/volt/me', { replace: true })
  }, [ready]) // eslint-disable-line react-hooks/exhaustive-deps

  async function signOut() {
    await volt.logout()
    setVoltToken(null); setMe(null); navigate('/volt')
  }
  // Apply Now / Get Future Opportunities. The persistent Apply action lives on
  // the public and dashboard screens only - never over an application's own
  // Back / Next / Submit controls, sign-in or registration.
  const [dialog, setDialog] = useState(null)
  const path = loc.pathname.replace(/\/+$/, '') || '/volt'
  const projectMatch = path.match(/^\/volt\/projects\/([^/]+)$/)
  const showApply = path === '/volt' || !!projectMatch
  function openApply() {
    const key = projectMatch && portal?.open_projects.some((x) => x.key === projectMatch[1]) ? projectMatch[1] : null
    if (key) { navigate(`/volt/apply/${key}`); return }        // on a project page: that application
    setDialog('apply')
  }
  const [talentSource, setTalentSource] = useState('portal')
  function openTalent(source = 'portal') {
    autoTalentDone = true                       // a manual open counts as shown
    setTalentSource(typeof source === 'string' ? source : 'portal')
    setDialog('talent')
  }
  // Get Future Opportunities opens on its own ~10 s after the homepage is
  // entered, once per page load. It waits while another dialog is open and is
  // never shown anywhere but the homepage (so never during an application).
  const [autoDue, setAutoDue] = useState(false)
  useEffect(() => {
    if (!ready || !portal || path !== '/volt' || autoTalentDone) return undefined
    const t = setTimeout(() => setAutoDue(true), AUTO_TALENT_MS)
    return () => clearTimeout(t)
  }, [ready, portal, path])
  useEffect(() => {
    if (!autoDue) return
    if (path !== '/volt' || autoTalentDone) { setAutoDue(false); return }
    if (dialog) return                          // deferred until that dialog closes
    setAutoDue(false)
    openTalent('auto_popup')
  }, [autoDue, dialog, path]) // eslint-disable-line react-hooks/exhaustive-deps
  const signedIn = !!me
  if (!ready) return <Shell><Loading /></Shell>
  if (!portal) return <Shell><div className="v-page"><Alert>{err || 'The recruiting portal is unavailable right now.'}</Alert></div></Shell>
  const needAuth = (el) => (voltToken() ? el : <Navigate to={`/volt/sign-in?next=${encodeURIComponent(loc.pathname)}`} replace />)
  return (
    <Shell signedIn={signedIn} onSignOut={signOut} showApply={showApply} onApply={openApply} onTalent={openTalent}>
      <Routes>
        <Route index element={<Home portal={portal} signedIn={signedIn} onApply={openApply} onTalent={openTalent} />} />
        <Route path="projects/:projectKey" element={<ProjectPage portal={portal} onTalent={openTalent} />} />
        <Route path="apply/:projectKey" element={<Apply portal={portal} me={me} reload={reload} />} />
        <Route path="sign-in" element={<SignIn reload={reload} />} />
        <Route path="me" element={needAuth(<MyApplication me={me} reload={reload} portal={portal} />)} />
        <Route path="me/profile" element={needAuth(<ProfilePage me={me} reload={reload} />)} />
        <Route path="me/support" element={needAuth(<SupportPage me={me} />)} />
        <Route path="me/applications/:id" element={needAuth(<ApplicationSteps reload={reload} />)} />
        <Route path="*" element={<Navigate to="/volt" replace />} />
      </Routes>
      {dialog === 'apply' && (
        <ApplyDialog projects={portal.open_projects} onClose={() => setDialog(null)} onTalent={() => openTalent()}
                     onPick={(k) => { setDialog(null); navigate(`/volt/apply/${k}`) }} />
      )}
      {dialog === 'talent' && (
        <TalentDialog projects={portal.open_projects} source={talentSource}
                      onClose={() => setDialog(null)}
                      onBrowse={() => setDialog('apply')} />
      )}
    </Shell>
  )
}
