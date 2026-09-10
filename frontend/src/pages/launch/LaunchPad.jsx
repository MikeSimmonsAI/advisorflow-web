/**
 * LaunchPad — the Launch Engine, customer-facing surface.
 *
 * ===========================================================================
 * WHAT THIS IS
 * ===========================================================================
 *
 *     ADVISORFLOW            the platform capability
 *          |
 *     LAUNCH ENGINE          onboarding, owned by the platform
 *          |
 *     WHITE-LABEL BRAND      whichever brand owns the relationship
 *          |
 *     CUSTOMER ORGANIZATION  the customer being onboarded
 *
 * The customer sees the BRAND. AdvisorFlow powers this and is credited once,
 * in the rail footer. Nothing in this component knows any brand's or
 * customer's name — both arrive from GET /launch/me, which reads them from the
 * Platform and Organization rows behind the signed-in session.
 *
 * ===========================================================================
 * WHERE THE DATA COMES FROM, AND WHY NOT FROM HERE
 * ===========================================================================
 *
 * The org is NEVER in the URL. `/launch` takes no id and this component sends
 * none: the server resolves the workspace from the session. A `/launch/:orgId`
 * route would be a customer-enumeration endpoint wearing a feature's clothes.
 *
 * Completion percentages come from the server too. A percentage computed in
 * the browser is one devtools can set to 100, and "required" enforced only in
 * React is not required.
 *
 * NOTHING IS KEPT IN localStorage. The draft lives in component state between
 * keystrokes and in the database the moment it saves; there is no third copy
 * to go stale, and no credential sitting in a browser store.
 *
 * ===========================================================================
 * THE TWO PROGRESS AXES
 * ===========================================================================
 *
 * TOP    implementation lifecycle — where the whole project stands.
 * RIGHT  intake completion — the eight sections, all the customer's.
 *
 * See LaunchProgress.jsx for why merging them would be wrong.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api } from '../../api/client'

import LaunchStyles from './LaunchStyles'
import LaunchSidebar from './LaunchSidebar'
import LaunchHeader from './LaunchHeader'
import LaunchHero from './LaunchHero'
import LaunchProgress from './LaunchProgress'
import OnboardingProgressPanel from './OnboardingProgressPanel'
import OnboardingStepShell from './OnboardingStepShell'
import WhatWeLaunch from './WhatWeLaunch'
import DeliveryPanel from './DeliveryPanel'

import CompanyInformationStep from './steps/CompanyInformationStep'
import BrandingAssetsStep from './steps/BrandingAssetsStep'
import WebsiteAccessStep from './steps/WebsiteAccessStep'
import LeadSourcesStep from './steps/LeadSourcesStep'
import CurrentSystemsStep from './steps/CurrentSystemsStep'
import CustomerProcessStep from './steps/CustomerProcessStep'
import FilesDocumentsStep from './steps/FilesDocumentsStep'
import ReviewSubmitStep from './steps/ReviewSubmitStep'

const STEP_COMPONENTS = {
  company: CompanyInformationStep,
  branding: BrandingAssetsStep,
  website: WebsiteAccessStep,
  compare: LeadSourcesStep,
  systems: CurrentSystemsStep,
  process: CustomerProcessStep,
  files: FilesDocumentsStep,
  review: ReviewSubmitStep,
}

function Centered({ children }) {
  return (
    <div className="lp-scope" data-surface="launch">
      <LaunchStyles />
      <div style={{ minHeight: '60vh', display: 'grid', placeItems: 'center',
                    padding: '48px 24px' }}>
        <div style={{ maxWidth: 520, textAlign: 'center' }}>{children}</div>
      </div>
    </div>
  )
}

export default function LaunchPad() {
  const { stepKey } = useParams()
  const navigate = useNavigate()

  const [launch, setLaunch] = useState(null)
  const [loadErr, setLoadErr] = useState(null)
  const [loading, setLoading] = useState(true)

  const [answers, setAnswers] = useState({})
  const [secretsSet, setSecretsSet] = useState([])
  const [files, setFiles] = useState([])
  const [stepMeta, setStepMeta] = useState(null)

  const [railOpen, setRailOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveErr, setSaveErr] = useState(null)

  const steps = launch?.overview?.steps || []
  const stepKeys = useMemo(() => steps.map(s => s.key), [steps])
  const active = stepKeys.includes(stepKey) ? stepKey : (stepKeys[0] || 'company')
  const step = steps.find(s => s.key === active) || null
  const index = steps.findIndex(s => s.key === active)

  // A ref, not state: the unload guard has to read the CURRENT value at the
  // moment the browser asks, and a closure captured at mount would forever
  // report the value from mount.
  const dirtyRef = useRef(false)

  const reloadLaunch = useCallback(async () => {
    const d = await api.get('/launch/me')
    setLaunch(d)
    return d
  }, [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    api.get('/launch/me')
      .then(d => { if (alive) { setLaunch(d); setLoadErr(null) } })
      .catch(e => { if (alive) setLoadErr(e?.detail || 'Could not load your launch.') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  // Load the active step's saved answers. This is what makes refresh and
  // "come back tomorrow" work — the server is the only source of truth, so a
  // reload is a fetch rather than a recovery.
  useEffect(() => {
    if (!launch) return undefined
    let alive = true
    setSaved(false)
    setSaveErr(null)
    api.get('/launch/me/steps/' + active)
      .then(d => {
        if (!alive) return
        setAnswers(d.answers || {})
        setSecretsSet(d.secrets_set || [])
        setStepMeta(d)
        dirtyRef.current = false
      })
      .catch(e => { if (alive) setSaveErr(e?.detail || 'Could not load this section.') })
    return () => { alive = false }
  }, [active, launch])

  const loadFiles = useCallback(() => {
    api.get('/launch/me/files')
      .then(d => setFiles(d.files || []))
      .catch(() => setFiles([]))
  }, [])

  useEffect(() => { if (launch) loadFiles() }, [launch, loadFiles])

  // An honest unsaved-work guard: it fires only when something actually
  // changed since the last save, so it never cries wolf on a page the person
  // merely looked at.
  useEffect(() => {
    const onBeforeUnload = e => {
      if (!dirtyRef.current) return undefined
      e.preventDefault()
      e.returnValue = ''
      return ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [])

  const set = useCallback((key, value) => {
    setAnswers(a => ({ ...a, [key]: value }))
    dirtyRef.current = true
    setSaved(false)
  }, [])

  const persist = useCallback(async () => {
    setSaving(true)
    setSaveErr(null)
    try {
      const res = await api.put('/launch/me/steps/' + active, { answers })
      setStepMeta(res)
      setSecretsSet(res.secrets_set || [])
      // A stored credential must not linger in the field after the write.
      if ((res.secrets_set || []).length) {
        setAnswers(a => {
          const nextAnswers = { ...a }
          for (const k of res.secrets_set) delete nextAnswers[k]
          return nextAnswers
        })
      }
      dirtyRef.current = false
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2600)
      await reloadLaunch()
      return true
    } catch (e) {
      setSaveErr(e?.detail || 'Could not save. Your answers are still on screen.')
      return false
    } finally {
      setSaving(false)
    }
  }, [active, answers, reloadLaunch])

  const goTo = useCallback(key => {
    navigate('/launch/' + key)
    setRailOpen(false)
    if (typeof window !== 'undefined') window.scrollTo({ top: 0 })
  }, [navigate])

  const saveAndGo = useCallback(async key => {
    const ok = await persist()
    if (ok) goTo(key)
  }, [persist, goTo])

  const uploadFile = useCallback(async (file, label) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('step_key', 'files')
    if (label) fd.append('label', label)
    const row = await api.upload('/launch/me/files', fd)
    loadFiles()
    reloadLaunch()
    return row
  }, [loadFiles, reloadLaunch])

  const removeFile = useCallback(async id => {
    await api.delete('/launch/me/files/' + id)
    loadFiles()
    reloadLaunch()
  }, [loadFiles, reloadLaunch])

  const submit = useCallback(async () => {
    await persist()
    const res = await api.post('/launch/me/submit', {})
    await reloadLaunch()
    return res
  }, [persist, reloadLaunch])

  if (loading) {
    return <Centered><p style={{ color: '#64748b' }}>Loading your launch…</p></Centered>
  }
  if (loadErr) {
    return (
      <Centered>
        <h2 style={{ margin: '0 0 8px', fontSize: 20 }}>Your launch is not ready yet</h2>
        <p style={{ color: '#64748b', lineHeight: 1.6 }}>{loadErr}</p>
      </Centered>
    )
  }
  if (!launch || !step) {
    return <Centered><p style={{ color: '#64748b' }}>Nothing to show yet.</p></Centered>
  }

  const brand = launch.brand
  const customer = launch.customer
  const overall = launch.overview.overall_pct
  const StepBody = STEP_COMPONENTS[active]
  const next = steps[index + 1]
  const prev = steps[index - 1]
  const submitted = !!(launch.submission && !launch.submission.reviewed_at)
  const reviewed = !!(launch.submission && launch.submission.reviewed_at)

  // The one intake status the whole surface agrees on, derived from the same
  // two facts the backend derives it from (a submission exists; it has been
  // reviewed) plus progress. See present.js — the staff list uses the same
  // vocabulary, so "Submitted — needs review" means one thing on this platform.
  const intakeStatusKey = submitted ? 'submitted'
    : reviewed ? 'reviewed'
      : overall > 0 ? 'in_progress' : 'not_started'

  return (
    <div className="lp-scope" data-surface="launch">
      <LaunchStyles />
      <div className="lp-shell">
        <LaunchSidebar brand={brand} active="onboarding" open={railOpen}
          onToggle={() => setRailOpen(o => !o)} onSelect={() => setRailOpen(false)} />

        <div className="lp-body">
          <LaunchHeader brand={brand} customer={customer} />

          {/* WHAT HAPPENS NEXT, not just that something happened. A locked
              form with no explanation reads as a bug; a locked form that says
              when it was sent, who has it and how it reopens reads as a
              process. */}
          {submitted ? (
            <div className="lp-proto">
              <b>Submitted</b>
              <span>
                Sent{launch.submission?.submitted_at
                  ? ' on ' + new Date(launch.submission.submitted_at).toLocaleString()
                  : ''}
                {launch.submission?.signed_name
                  ? ' by ' + launch.submission.signed_name : ''}.
                {' '}Your {brand.name} implementation team has it and is reviewing
                it now. Editing is locked while they do — they will reopen it if
                anything needs changing, and you will be able to edit again
                straight away.
              </span>
            </div>
          ) : reviewed ? (
            <div className="lp-proto">
              <b>Reviewed</b>
              <span>
                Your {brand.name} team has reviewed your intake. You can still
                make changes if anything needs correcting.
              </span>
            </div>
          ) : null}

          <main className="lp-main">
            <LaunchHero brand={brand} customer={customer}
                        implementation={launch.implementation}
                        state={intakeStatusKey} />
            <LaunchProgress phases={launch.lifecycle}
                            currentStatus={launch.implementation?.status}
                            intakePct={overall} />

            <div className="lp-work">
              <div style={{ minWidth: 0 }}>
                <OnboardingStepShell
                  step={step}
                  total={steps.length}
                  pct={step.pct}
                  saved={saved}
                  saving={saving}
                  error={saveErr}
                  savedAt={stepMeta?.updated_at}
                  missing={step.missing}
                  onSaveDraft={persist}
                  onBack={prev ? () => goTo(prev.key) : null}
                  onContinue={next ? () => saveAndGo(next.key) : null}
                  continueLabel="Save & Continue"
                >
                  <StepBody
                    v={answers}
                    set={set}
                    brand={brand}
                    customer={customer}
                    steps={steps}
                    onGoTo={goTo}
                    secretsSet={secretsSet}
                    files={files}
                    onUpload={uploadFile}
                    onRemoveFile={removeFile}
                    onSubmit={submit}
                    blockers={launch.blockers}
                    overview={launch.overview}
                    submission={launch.submission}
                    readOnly={submitted}
                  />
                </OnboardingStepShell>

                {/* ABOVE the deliverables, deliberately. "What we will build"
                    is the promise; this is what is actually happening and what
                    the customer owes us today, and the live thing outranks the
                    brochure once a launch is under way. It renders nothing at
                    all until there is a programme to report. */}
                <DeliveryPanel brand={brand} />

                <WhatWeLaunch brand={brand} customer={customer} />
              </div>

              <aside className="lp-side">
                <OnboardingProgressPanel
                  steps={steps}
                  activeKey={active}
                  onSelect={goTo}
                  overallPct={overall}
                  brand={brand}
                />
              </aside>
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}
