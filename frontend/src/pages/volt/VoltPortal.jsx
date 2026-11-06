/* VOLT MARKETING - public applicant portal and rep dashboard (#535).
 *
 * /volt                      open opportunities, Register & Apply, Sign In
 * /volt?ref=REP-1042&project=aca   a rep's invite (suggests; never credits on its own)
 * /volt/apply/:projectKey    register (or start a profile / a second project)
 * /volt/sign-in              applicant sign-in
 * /volt/me                   rep dashboard
 * /volt/me/applications/:id  one project application: draft, documents, submit
 *
 * Public navigation carries applicant actions only. Staff reach VOLT
 * Operations through Program Access, never through a link here. Every rule
 * (referral credit, closed projects, one application per project) is enforced
 * on the server; this screen only makes the right action the easy one.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { fmtDate, sessionTime, setVoltToken, uploadKey, volt, voltToken } from './voltApi'
import './volt.css'

const INVITE_KEY = 'af_volt_invite'

function readInvite() {
  try { return JSON.parse(sessionStorage.getItem(INVITE_KEY) || 'null') } catch { return null }
}
function keepInvite(v) {
  try { if (v) sessionStorage.setItem(INVITE_KEY, JSON.stringify(v)); else sessionStorage.removeItem(INVITE_KEY) } catch { /* private */ }
}

export function Bolt() { return <span className="v-bolt" aria-hidden="true" /> }

export function Wordmark({ to = '/volt' }) {
  return (
    <Link className="v-brand" to={to} aria-label="VOLT Marketing home">
      <Bolt /><span className="v-word">VOLT</span> <span className="v-word-sub">Marketing</span>
    </Link>
  )
}

function Shell({ children, signedIn, onSignOut }) {
  return (
    <div className="volt">
      <header className="v-head">
        <div className="v-wrap">
          <Wordmark />
          <nav aria-label="Applicant">
            {signedIn ? (
              <>
                <Link className="v-btn v-btn--ghost v-btn--sm" to="/volt/me">My dashboard</Link>
                <button type="button" className="v-btn v-btn--ghost v-btn--sm" onClick={onSignOut}>Sign out</button>
              </>
            ) : (
              <Link className="v-btn v-btn--ghost v-btn--sm" to="/volt/sign-in">Sign In</Link>
            )}
          </nav>
        </div>
      </header>
      <main className="v-wrap" style={{ flex: 1 }}>{children}</main>
      <footer className="v-foot">
        <div className="v-wrap"><span>VOLT Marketing · Recruiting portal</span><span>Need help? Contact your recruiter.</span></div>
      </footer>
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

// ── Click-to-confirm recruiter picker ───────────────────────────────────────
//
// Credit is the SELECTED record's id, nothing else. Editing the text after a
// selection clears the selection; text without a selection blocks submit with
// a prompt to pick a match or clear the field. A blank field is direct signup.
export function ReferralPicker({ value, onChange, signedIn, testMode, invite, error, onTextChange }) {
  const [text, setText] = useState(value ? value.display_name : '')
  const [results, setResults] = useState([])
  const [status, setStatus] = useState('')
  const [active, setActive] = useState(-1)
  const seq = useRef(0)
  const [inviteAnswered, setInviteAnswered] = useState(false)

  useEffect(() => { onTextChange && onTextChange(text) }, [text]) // eslint-disable-line react-hooks/exhaustive-deps

  const search = useCallback((q) => {
    const n = ++seq.current
    if (q.replace(/[\s'’]/g, '').length < 2) { setResults([]); setStatus(q.trim() ? 'Type at least 2 letters.' : ''); return }
    setStatus('Searching…')
    const p = signedIn ? volt.searchSignedIn(q) : volt.searchPublic(q, testMode)
    p.then((r) => {
      if (n !== seq.current) return
      setResults(r.results || [])
      setActive(-1)
      setStatus((r.results || []).length ? 'Select your recruiter below.' : 'No matching recruiter. Check the spelling, or clear the field to continue without one.')
    }).catch(() => { if (n === seq.current) setStatus('Search is unavailable right now. You can clear the field and continue.') })
  }, [signedIn, testMode])

  useEffect(() => {
    if (value) return undefined
    const t = setTimeout(() => search(text), 220)
    return () => clearTimeout(t)
  }, [text, value, search])

  function pick(r, source = 'search') {
    onChange({ ...r, source })
    setText(r.display_name)
    setResults([])
    setStatus('')
  }
  function edit(v) {
    setText(v)
    if (value) onChange(null)        // editing after a selection clears it
  }
  function key(e) {
    if (!results.length) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(results.length - 1, a + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(results[active]) }
    else if (e.key === 'Escape') { setResults([]) }
  }
  const inv = invite && invite.status === 'valid' && invite.recruiter && !inviteAnswered && !value
  return (
    <div>
      {inv && (
        <div className="v-alert v-alert--info" style={{ marginBottom: 8 }}>
          <p style={{ marginBottom: 8 }}>Your invite link is from <b>{invite.recruiter.display_name} · {invite.recruiter.rep_code}</b>. Did they refer you?</p>
          <div className="v-row" style={{ justifyContent: 'flex-start' }}>
            <button type="button" className="v-btn v-btn--red v-btn--sm" onClick={() => { setInviteAnswered(true); pick(invite.recruiter, 'invite') }}>
              Yes, {invite.recruiter.display_name} referred me
            </button>
            <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setInviteAnswered(true)}>No</button>
          </div>
        </div>
      )}
      {invite && invite.status !== 'valid' && invite.status !== 'none' && (
        <Alert kind="info">That invite link is {invite.status === 'inactive' ? 'no longer active' : 'not valid'}. You can still search for your recruiter or leave this blank.</Alert>
      )}
      <label htmlFor="v-ref">Who referred you? <span className="v-muted">(optional)</span></label>
      <input id="v-ref" role="combobox" aria-expanded={results.length > 0} aria-controls="v-ref-list" aria-autocomplete="list"
             aria-activedescendant={active >= 0 ? `v-ref-opt-${active}` : undefined} autoComplete="off"
             aria-invalid={error ? 'true' : undefined} placeholder="Search by first or last name"
             value={text} onChange={(e) => edit(e.target.value)} onKeyDown={key} />
      {error && <span className="v-ferr">{error}</span>}
      {value ? (
        <div className="v-selected" aria-live="polite">
          <span>✓ Selected: <b>{value.display_name} · {value.rep_code}</b></span>
          <button type="button" className="v-btn v-btn--ghost v-btn--sm" onClick={() => { onChange(null); setText('') }}>Clear</button>
        </div>
      ) : (
        <>
          <p className="v-muted v-small" aria-live="polite">{status}</p>
          {results.length > 0 && (
            <div className="v-matches" id="v-ref-list" role="listbox" aria-label="Matching recruiters">
              {results.map((r, i) => (
                <button type="button" key={r.rep_id} id={`v-ref-opt-${i}`} role="option" aria-selected={i === active}
                        className="v-match" onClick={() => pick(r)}>
                  <b>{r.display_name}</b><span className="v-mono">{r.rep_code}</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** The rule the server also enforces: text with no confirmed selection is not allowed. */
function referralProblem(text, selected) {
  return text.trim() && !selected ? 'Select a matching recruiter from the list, or clear the field.' : null
}

// ── Home ────────────────────────────────────────────────────────────────────
function Home({ portal, signedIn }) {
  const loc = useLocation()
  useEffect(() => {
    const q = new URLSearchParams(loc.search)
    const ref = q.get('ref'); const project = q.get('project')
    if (ref || project) keepInvite({ ref: ref || '', project: project || '' })
  }, [loc.search])
  const invite = readInvite()
  const navigate = useNavigate()
  useEffect(() => {
    // A project invite lands on that project.
    const q = new URLSearchParams(loc.search)
    const p = q.get('project')
    if (p && portal.open_projects.some((x) => x.key === p)) navigate(`/volt/apply/${p}`, { replace: true })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <section className="v-hero">
        <article>
          <span className="v-eyebrow">Rep recruiting</span>
          <h1>Join a<br />project team.</h1>
          <p>We are recruiting for ACA health insurance and fiber internet outreach. Choose a project to start your application.</p>
          <div className="v-row" style={{ justifyContent: 'flex-start' }}>
            <a className="v-btn v-btn--red" href="#projects">See open projects ↓</a>
            {!signedIn && <Link className="v-btn v-btn--ghost" to="/volt/sign-in">Sign In</Link>}
          </div>
          {invite?.ref && <p className="v-small" style={{ marginTop: 12 }}>Invite code {invite.ref} saved for your application.</p>}
        </article>
        <HeroArt />
      </section>
      <div className="v-section-head" id="projects">
        <h2>Open opportunities</h2>
        <span className="v-mono">{String(portal.open_projects.length).padStart(2, '0')} recruiting</span>
      </div>
      <div className="v-grid2">
        {portal.open_projects.map((p) => (
          <article className="v-card v-card--project" key={p.key} data-project={p.key}>
            <div className="v-row"><span className="v-icon" aria-hidden="true">{p.icon}</span><span className="v-tag">● Recruiting</span></div>
            <h3>{p.name}</h3>
            <p className="v-muted">{p.summary}</p>
            <Link className="v-btn v-btn--red" to={`/volt/apply/${p.key}`}>{signedIn ? 'Apply / view application' : 'Register & Apply'} →</Link>
          </article>
        ))}
      </div>
      {portal.past_projects.map((p) => (
        <section className="v-card v-past" key={p.key} data-project={p.key}>
          <div><span className="v-mono">Past project</span><h3>{p.name}</h3></div>
          <span className="v-tag v-tag--black">Recruiting closed</span>
        </section>
      ))}
      <p className="v-muted v-small" style={{ margin: '14px 0 30px' }}>Already applied? <Link to="/volt/sign-in">Sign in</Link> to resume where you left off.</p>
    </>
  )
}

// ── Apply (register / start profile / second project) ───────────────────────
function useInvitePreview(projectKey) {
  const [invite, setInvite] = useState(null)
  useEffect(() => {
    const saved = readInvite()
    if (!saved?.ref) { setInvite({ status: 'none' }); return }
    volt.invite(saved.ref, saved.project || projectKey).then(setInvite).catch(() => setInvite({ status: 'none' }))
  }, [projectKey])
  return invite
}

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
  const invite = useInvitePreview(projectKey)
  const signedIn = !!voltToken()
  const existing = me?.applications?.find((a) => a.project?.key === projectKey)
  const [v, setV] = useState({})
  const [errs, setErrs] = useState({})
  const [err, setErr] = useState('')
  const [referrer, setReferrer] = useState(null)
  const [refText, setRefText] = useState('')
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
  const testMode = /@example\.test$/i.test(v.email || '') || /@example\.test$/i.test(me?.profile?.email || me?.account?.email || '')

  async function submit(e) {
    e.preventDefault()
    setErr(''); setErrs({}); setExists(false)
    const problem = referralProblem(refText, referrer)
    if (problem) { setErrs({ referrer: problem }); document.getElementById('v-ref')?.focus(); return }
    const ref = referrer ? { referrer_rep_id: referrer.rep_id, referral_source: referrer.source } : {}
    setBusy(true)
    try {
      let appId
      if (hasProfile) {
        const a = await volt.startApplication({ project_key: projectKey, ...ref })
        appId = a.id
      } else if (signedIn) {
        const r = await volt.createProfile({ ...v, project_key: projectKey, ...ref })
        appId = r.application_id
      } else {
        const r = await volt.register({ ...v, project_key: projectKey, ...ref })
        setVoltToken(r.access_token)
        appId = r.application_id
      }
      keepInvite(null)
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
        ? <p className="v-muted">You will use your existing profile and rep ID <b>{me.profile.rep_code}</b>.</p>
        : <p className="v-muted">One profile for every VOLT project. You will get a permanent rep ID.</p>}
      <form className="v-form v-panel" onSubmit={submit} noValidate style={{ marginTop: 14 }}>
        {!hasProfile && <ProfileFields v={v} set={setV} errs={errs} withEmail={!signedIn} withPassword={!signedIn} />}
        <ReferralPicker value={referrer} onChange={(r) => {
          setReferrer(r)
          // A resolved referral problem takes its banner with it.
          setErrs((x) => { const n = { ...x }; delete n.referrer; return n })
          setErr('')
        }}
                        onTextChange={(t) => { setRefText(t); if (!t.trim()) setErrs((x) => { const n = { ...x }; delete n.referrer; return n }) }} signedIn={signedIn} testMode={testMode} invite={invite} error={errs.referrer} />
        {exists ? (
          <Alert>This email already has an account. <Link to={`/volt/sign-in?next=/volt/apply/${projectKey}`}>Sign in to continue</Link> or <Link to="/forgot-password">reset your password</Link>.</Alert>
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
      const inv = readInvite()
      const safeNext = next && next.startsWith('/volt/') ? next : null
      if (safeNext) navigate(safeNext)
      else if (inv?.project) navigate(`/volt/apply/${inv.project}`)
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
        <p className="v-small"><Link to="/forgot-password">Forgot your password?</Link> · New here? <Link to="/volt#projects">Register & Apply</Link></p>
      </form>
    </div>
  )
}

// ── Rep dashboard ───────────────────────────────────────────────────────────
const STAGES = ['draft', 'submitted', 'needs_update', 'approved', 'started']
const STAGE_LABEL = { draft: 'Draft', submitted: 'Submitted', needs_update: 'Needs update', approved: 'Approved', started: 'Started' }

function Steps({ status }) {
  const order = ['draft', 'submitted', 'approved', 'started']
  const at = status === 'needs_update' ? 1 : order.indexOf(status)
  return (
    <div className="v-steps" aria-label={`Stage: ${STAGE_LABEL[status]}`}>
      {order.map((s, i) => (
        <span key={s} className={`v-step ${i < at ? 'done' : ''} ${i === at ? 'now' : ''}`}>
          {i === at && status === 'needs_update' ? 'Needs update' : STAGE_LABEL[s]}
        </span>
      ))}
    </div>
  )
}

function CopyField({ value, label }) {
  const [done, setDone] = useState(false)
  return (
    <div>
      <span className="v-label">{label}</span>
      <div className="v-copy">
        <input readOnly value={value} onFocus={(e) => e.target.select()} aria-label={label} />
        <button type="button" className="v-btn v-btn--black v-btn--sm" onClick={() => { navigator.clipboard?.writeText(value); setDone(true); setTimeout(() => setDone(false), 1500) }}>
          {done ? 'Copied' : 'Copy'}
        </button>
      </div>
    </div>
  )
}

function Leaderboard({ projects }) {
  const [pid, setPid] = useState(projects[0]?.id || '')
  const [board, setBoard] = useState(null)
  useEffect(() => {
    let live = true
    volt.leaderboard(pid).then((b) => { if (live) setBoard(b) }).catch(() => { if (live) setBoard({ rows: [] }) })
    return () => { live = false }
  }, [pid])
  return (
    <section className="v-panel">
      <div className="v-row"><h3>Recruiter leaderboard</h3>
        <select aria-label="Leaderboard project" value={pid} onChange={(e) => setPid(e.target.value)} style={{ width: 'auto' }}>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.short_name}</option>)}
          <option value="">All projects</option>
        </select>
      </div>
      <p className="v-muted v-small">{board?.rule}</p>
      {!board ? <p className="v-muted">Loading…</p> : board.rows.length === 0 ? <p className="v-muted">No referred reps have started here yet.</p> : (
        <ol className="v-board">
          {board.rows.slice(0, 10).map((r) => (
            <li key={r.rep_id}><span><b>{String(r.rank).padStart(2, '0')}</b> {r.display_name} <span className="v-mono">{r.rep_code}</span></span>
              <span>{r.starts} started</span></li>
          ))}
        </ol>
      )}
    </section>
  )
}

function Dashboard({ me, portal }) {
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const [inviteProject, setInviteProject] = useState(portal.open_projects[0]?.key || '')
  if (!me?.has_profile) return <Navigate to="/volt#projects" replace />
  const p = me.profile
  const open = portal.open_projects
  const allProjects = [...open, ...portal.past_projects].map((x) => ({ ...x, id: x.id }))
  const notApplied = open.filter((x) => !me.applications.some((a) => a.project?.key === x.key))
  return (
    <div className="v-page">
      <span className="v-mono">Rep dashboard · {p.rep_code}</span>
      <div className="v-row" style={{ margin: '4px 0 14px' }}>
        <h2 style={{ fontSize: 32, letterSpacing: '-.04em' }}>Welcome, {p.first_name}.</h2>
        <span className="v-tag v-tag--black" title="Your permanent rep ID">Rep ID {p.rep_code}</span>
      </div>
      <div className="v-grid2">
        <section className="v-panel">
          <h3>My applications</h3>
          {me.applications.map((a) => (
            <div key={a.id} className="v-doc" data-application={a.id}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <b>{a.project?.name}</b>
                <Steps status={a.status} />
                <p className={a.next_action.kind === 'applicant' ? '' : 'v-muted'} style={{ fontSize: 14 }}>
                  <b>{a.next_action.label}.</b> {a.next_action.detail}
                </p>
                {a.referrer && <p className="v-small v-muted">Referred by {a.referrer.display_name} · {a.referrer.rep_code}</p>}
              </div>
              <Link className={`v-btn v-btn--sm ${a.editable ? 'v-btn--red' : 'v-btn--line'}`} to={`/volt/me/applications/${a.id}`}>
                {a.status === 'draft' ? 'Resume' : a.status === 'needs_update' ? 'Update' : 'Open'} →
              </Link>
            </div>
          ))}
          {notApplied.map((x) => (
            <div key={x.key} className="v-doc"><span>{x.name}</span><Link className="v-btn v-btn--line v-btn--sm" to={`/volt/apply/${x.key}`}>Apply →</Link></div>
          ))}
        </section>
        <section className="v-panel">
          <h3>Upcoming Sessions</h3>
          {me.sessions.length === 0 ? <p className="v-muted">No sessions scheduled for you yet.</p> : me.sessions.map((s) => (
            <div key={s.id} className="v-doc" data-session={s.id}>
              <div>
                <b>{s.kind === 'interview' ? 'Interview' : 'Orientation'}{s.title ? ` · ${s.title}` : ''}</b>
                <p className="v-small">{s.project}</p>
                <p className="v-small">{sessionTime(s.starts_at, s.timezone)} · {s.duration_minutes} min</p>
                {s.instructions && <p className="v-small v-muted">{s.instructions}</p>}
              </div>
              {s.status === 'canceled' ? <span className="v-tag v-tag--black">Canceled</span>
                : s.join_url ? <a className="v-btn v-btn--red v-btn--sm" href={s.join_url} target="_blank" rel="noopener noreferrer">Join Zoom</a> : null}
            </div>
          ))}
          <h3 style={{ marginTop: 16 }}>Document requests</h3>
          {me.document_requests.length === 0 ? <p className="v-muted">Nothing requested right now.</p> : me.document_requests.map((d) => (
            <div key={`${d.scope}-${d.key}`} className="v-doc">
              <span><b>{d.label}</b> <span className="v-small v-muted">{d.scope}</span>{d.reason && <span className="v-ferr">Replace: {d.reason}</span>}</span>
              <Link className="v-btn v-btn--red v-btn--sm" to={d.application_id ? `/volt/me/applications/${d.application_id}` : '/volt/me/profile'}>{d.state === 'replace' ? 'Replace' : 'Upload'}</Link>
            </div>
          ))}
        </section>
      </div>
      <div className="v-grid2">
        <section className="v-panel">
          <h3>My referrals</h3>
          <p style={{ marginBottom: 10 }}><b style={{ fontSize: 28 }}>{me.referrals.direct_recruits}</b> direct recruits</p>
          <CopyField label="Referral code" value={me.referrals.code} />
          <div style={{ height: 10 }} />
          <CopyField label="Referral link" value={`${origin}${me.referrals.link_path}`} />
          <div style={{ height: 10 }} />
          <label htmlFor="v-invite-p">Project invite</label>
          <select id="v-invite-p" value={inviteProject} onChange={(e) => setInviteProject(e.target.value)}>
            {open.map((x) => <option key={x.key} value={x.key}>{x.name}</option>)}
          </select>
          <div style={{ height: 6 }} />
          <CopyField label="Project invite link" value={`${origin}${me.referrals.link_path}&project=${inviteProject}`} />
          {me.original_recruiter && <p className="v-small v-muted" style={{ marginTop: 10 }}>You were recruited by {me.original_recruiter.display_name} · {me.original_recruiter.rep_code}</p>}
        </section>
        <Leaderboard projects={allProjects} />
      </div>
      <p className="v-small"><Link to="/volt/me/profile">Profile & profile documents →</Link></p>
    </div>
  )
}

// ── Documents ───────────────────────────────────────────────────────────────
const STATE_LABEL = { requested: 'Requested', under_review: 'Under review', accepted: 'Accepted', replace: 'Replace' }

function DocRow({ d, applicationId, editable, onDone }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const key = useRef(uploadKey())
  async function choose(e) {
    const f = e.target.files && e.target.files[0]
    if (!f) return
    if (f.size > 10 * 1024 * 1024) { setErr('That file is too large. The limit is 10 MB.'); return }
    setBusy(true); setErr('')
    const form = new FormData()
    form.append('file', f)
    form.append('doc_key', d.key)
    form.append('upload_key', key.current)
    if (applicationId) form.append('application_id', applicationId)
    try {
      await volt.upload(form)
      key.current = uploadKey()          // a new choice is a new upload; a retry reuses the key
      await onDone()
    } catch (x) { setErr(x.message + (x.status === 0 ? ' Your file was not saved; choose it again to retry.' : '')) } finally { setBusy(false); e.target.value = '' }
  }
  async function view() {
    try { const b = await volt.file(d.document.id); window.open(URL.createObjectURL(b), '_blank', 'noopener') } catch (x) { setErr(x.message) }
  }
  const canUpload = editable && (d.state === 'requested' || d.state === 'replace' || d.state === 'under_review')
  return (
    <div className="v-doc" data-doc={d.key}>
      <div style={{ flex: 1, minWidth: 200 }}>
        <b>{d.label}</b> <span className={`v-tag ${d.state === 'accepted' ? 'v-tag--black' : d.state === 'requested' ? 'v-tag--line' : ''}`}>{STATE_LABEL[d.state]}</span>
        {d.document && <p className="v-small v-muted">{d.document.filename} · version {d.document.version} · {fmtDate(d.document.uploaded_at)}</p>}
        {d.reason && <p className="v-ferr">Reviewer: {d.reason}</p>}
        {err && <p className="v-ferr" role="alert">{err}</p>}
      </div>
      <div className="v-row">
        {d.document && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={view}>View</button>}
        {canUpload && (
          <label className="v-btn v-btn--red v-btn--sm" style={{ margin: 0 }}>
            {busy ? 'Uploading…' : d.state === 'requested' ? 'Upload' : 'Replace'}
            <input type="file" accept=".pdf,.doc,.docx,image/*" onChange={choose} disabled={busy} style={{ display: 'none' }} />
          </label>
        )}
      </div>
    </div>
  )
}

function ApplicationPage({ reload }) {
  const { id } = useParams()
  const [a, setA] = useState(null)
  const [err, setErr] = useState('')
  const [answer, setAnswer] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const [missing, setMissing] = useState([])
  const timer = useRef(null)
  const load = useCallback(() => volt.application(id).then((x) => { setA(x); setAnswer(x.answers?.experience || '') }).catch((x) => setErr(x.message)), [id])
  useEffect(() => { load() }, [load])
  if (err && !a) return <div className="v-page"><Alert>{err}</Alert></div>
  if (!a) return <Loading />
  function type(v) {
    setAnswer(v); setSaved('Saving…')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      volt.saveAnswers(a.id, { experience: v }).then(() => setSaved('Draft saved')).catch((x) => setSaved(x.message))
    }, 600)
  }
  async function submit() {
    setBusy(true); setErr(''); setMissing([])
    try {
      clearTimeout(timer.current)
      await volt.saveAnswers(a.id, { experience: answer })
      const r = await volt.submit(a.id)
      setA({ ...a, ...r })
      await reload()
    } catch (x) { setErr(x.message); setMissing(x.detail?.missing || []) } finally { setBusy(false) }
  }
  const docsAll = [...a.documents, ...(a.profile_documents || [])]
  return (
    <div className="v-page" style={{ maxWidth: 760 }}>
      <Link to="/volt/me" className="v-btn v-btn--line v-btn--sm">← My dashboard</Link>
      <p className="v-mono" style={{ marginTop: 14 }}>{a.project.short_name}</p>
      <h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>{a.project.name}</h2>
      <Steps status={a.status} />
      <Alert kind={a.next_action.kind === 'applicant' ? 'info' : 'ok'}><b>{a.next_action.label}.</b> {a.next_action.detail}</Alert>
      {a.status === 'needs_update' && a.return_reason && <div style={{ height: 8 }} />}
      <section className="v-panel" style={{ marginTop: 12 }}>
        <h3>Project application</h3>
        <label htmlFor="v-answer">{a.project.question}
          <textarea id="v-answer" value={answer} onChange={(e) => type(e.target.value)} disabled={!a.editable} maxLength={4000} />
        </label>
        <p className="v-small v-muted" aria-live="polite">{a.editable ? saved || 'Your answers save automatically as a draft.' : 'Submitted answers are locked while our team reviews them.'}</p>
      </section>
      <section className="v-panel">
        <h3>Documents</h3>
        {docsAll.length === 0 ? <p className="v-muted">No documents requested for this project.</p> : (
          <>
            {a.documents.map((d) => <DocRow key={'a' + d.key} d={d} applicationId={a.id} editable={a.editable} onDone={load} />)}
            {(a.profile_documents || []).map((d) => <DocRow key={'p' + d.key} d={d} applicationId={null} editable onDone={load} />)}
          </>
        )}
        <p className="v-small v-muted" style={{ marginTop: 8 }}>PDF, Word or photo, up to 10 MB. Files are private to you and our recruiting staff.</p>
      </section>
      {a.referrer && <p className="v-small" style={{ marginBottom: 10 }}>Referred by <b>{a.referrer.display_name} · {a.referrer.rep_code}</b></p>}
      {missing.length > 0 && <Alert>Finish these first: {missing.join('; ')}</Alert>}
      {err && !missing.length && <Alert>{err}</Alert>}
      {a.editable && <button type="button" className="v-btn v-btn--red" onClick={submit} disabled={busy} style={{ marginTop: 10 }}>{busy ? 'Submitting…' : a.status === 'needs_update' ? 'Resubmit application →' : 'Submit application →'}</button>}
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
      <Link to="/volt/me" className="v-btn v-btn--line v-btn--sm">← My dashboard</Link>
      <h2 style={{ fontSize: 30, letterSpacing: '-.04em', margin: '14px 0 4px' }}>My profile</h2>
      <p className="v-muted">Rep ID {me.profile.rep_code} · {me.profile.email}</p>
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
  useEffect(() => { document.title = 'VOLT Marketing | Recruiting' }, [])
  // Returning reps land in their dashboard.
  useEffect(() => {
    if (ready && me?.has_profile && loc.pathname === '/volt' && !loc.search) navigate('/volt/me', { replace: true })
  }, [ready]) // eslint-disable-line react-hooks/exhaustive-deps

  async function signOut() {
    await volt.logout()
    setVoltToken(null); setMe(null); navigate('/volt')
  }
  const signedIn = !!me
  if (!ready) return <Shell><Loading /></Shell>
  if (!portal) return <Shell><div className="v-page"><Alert>{err || 'The recruiting portal is unavailable right now.'}</Alert></div></Shell>
  const needAuth = (el) => (voltToken() ? el : <Navigate to={`/volt/sign-in?next=${encodeURIComponent(loc.pathname)}`} replace />)
  return (
    <Shell signedIn={signedIn} onSignOut={signOut}>
      <Routes>
        <Route index element={<Home portal={portal} signedIn={signedIn} />} />
        <Route path="apply/:projectKey" element={<Apply portal={portal} me={me} reload={reload} />} />
        <Route path="sign-in" element={<SignIn reload={reload} />} />
        <Route path="me" element={needAuth(<Dashboard me={me} portal={portal} />)} />
        <Route path="me/profile" element={needAuth(<ProfilePage me={me} reload={reload} />)} />
        <Route path="me/applications/:id" element={needAuth(<ApplicationPage reload={reload} />)} />
        <Route path="*" element={<Navigate to="/volt" replace />} />
      </Routes>
    </Shell>
  )
}

export { STAGES, STAGE_LABEL }
