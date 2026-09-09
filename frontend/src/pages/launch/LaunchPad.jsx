/**
 * LaunchPad — the AdvisorFlow Launch Engine, customer-facing surface.
 *
 * ===========================================================================
 * WHAT THIS IS
 * ===========================================================================
 *
 *     ADVISORFLOW            the platform capability
 *          |
 *     LAUNCH ENGINE          onboarding, owned by the platform
 *          |
 *     WHITE-LABEL BRAND      EvoSys Pro today; BookaBoost next
 *          |
 *     CUSTOMER ORGANIZATION  Atlantis Light & Power
 *
 * The customer sees the BRAND. AdvisorFlow powers this and is credited once,
 * in the rail footer. Nothing in this component knows the words "EvoSys Pro"
 * or "Atlantis" — both arrive as props from launchConfig, which is the single
 * file Stage 2 replaces with a fetch.
 *
 * ===========================================================================
 * STAGE 1 — UI PROTOTYPE, AND ONLY THAT
 * ===========================================================================
 *
 * This page makes NO network requests. It creates no context, reads no auth
 * state, sends no header and touches no storage — not localStorage, not
 * sessionStorage. Every answer lives in the React state below for the life of
 * the tab and is gone on refresh, which is exactly what was asked for and is
 * also the only responsible place for the credential fields on Step 3 to live
 * until encryption exists.
 *
 * It is a LEAF. It imports nothing from api/, auth/ or context/, so it cannot
 * alter authorization behaviour anywhere in the application. The route in
 * App.jsx is a single unguarded entry beside the other customer-facing
 * surfaces; whether Stage 2 puts an invitation token in front of it is a Stage
 * 2 decision and nothing here presumes an answer.
 *
 * ===========================================================================
 * THE TWO PROGRESS AXES
 * ===========================================================================
 *
 * TOP    implementation lifecycle — seven phases, mostly the brand's work.
 * RIGHT  intake completion — eight sections, all the customer's.
 *
 * See LaunchProgress.jsx for why merging them would be wrong.
 */
import { useCallback, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import LaunchStyles from './LaunchStyles'
import LaunchSidebar from './LaunchSidebar'
import LaunchHeader from './LaunchHeader'
import LaunchHero from './LaunchHero'
import LaunchProgress from './LaunchProgress'
import OnboardingProgressPanel from './OnboardingProgressPanel'
import OnboardingStepShell from './OnboardingStepShell'
import WhatWeLaunch from './WhatWeLaunch'

import CompanyInformationStep from './steps/CompanyInformationStep'
import BrandingAssetsStep from './steps/BrandingAssetsStep'
import WebsiteAccessStep from './steps/WebsiteAccessStep'
import ComparePowerStep from './steps/ComparePowerStep'
import CurrentSystemsStep from './steps/CurrentSystemsStep'
import CustomerProcessStep from './steps/CustomerProcessStep'
import FilesDocumentsStep from './steps/FilesDocumentsStep'
import ReviewSubmitStep from './steps/ReviewSubmitStep'

import { MOCK_BRAND, MOCK_CUSTOMER, MOCK_ANSWERS, STEPS, STEP_KEYS, LIFECYCLE }
  from './launchConfig'

const STEP_COMPONENTS = {
  company:  CompanyInformationStep,
  branding: BrandingAssetsStep,
  website:  WebsiteAccessStep,
  compare:  ComparePowerStep,
  systems:  CurrentSystemsStep,
  process:  CustomerProcessStep,
  files:    FilesDocumentsStep,
  review:   ReviewSubmitStep,
}

export default function LaunchPad({ brand = MOCK_BRAND,
                                    customer = MOCK_CUSTOMER }) {
  const { stepKey } = useParams()
  const navigate = useNavigate()

  const active = STEP_KEYS.includes(stepKey) ? stepKey : 'company'
  const step = STEPS.find(s => s.key === active) || STEPS[0]
  const index = STEPS.indexOf(step)

  const [answers, setAnswers] = useState(MOCK_ANSWERS)
  const [railOpen, setRailOpen] = useState(false)
  const [saved, setSaved] = useState(false)

  const set = useCallback((key, value) => {
    setAnswers(a => ({ ...a, [key]: value }))
    setSaved(false)
  }, [])

  // The intake figure the ring and the document meter both show. One number,
  // computed once, so the two can never disagree on screen.
  const overallPct = useMemo(
    () => Math.round(STEPS.reduce((t, s) => t + s.pct, 0) / STEPS.length),
    [])

  const goTo = useCallback(key => {
    navigate('/launch/' + key)
    setRailOpen(false)
    setSaved(false)
    if (typeof window !== 'undefined') window.scrollTo({ top: 0 })
  }, [navigate])

  // STAGE 1: a draft "saves" by confirming and nothing else. Better a visible
  // no-op the reviewer can see than a silent one they assume is working.
  const saveDraft = useCallback(() => {
    setSaved(true)
    window.setTimeout(() => setSaved(false), 2600)
  }, [])

  const next = STEPS[index + 1]
  const prev = STEPS[index - 1]
  const StepBody = STEP_COMPONENTS[active]

  return (
    <div className="lp-scope" data-surface="launch">
      <LaunchStyles />
      <div className="lp-shell">
        <LaunchSidebar brand={brand} active="onboarding" open={railOpen}
          onToggle={() => setRailOpen(o => !o)} onSelect={() => setRailOpen(false)} />

        <div className="lp-body">
          <LaunchHeader brand={brand} customer={customer} />

          {/* The prototype says so, on the screen. Removed in Stage 2. */}
          <div className="lp-proto">
            <b>Stage 1 prototype</b>
            <span>
              Design review only — mock data, no persistence, nothing submitted.
            </span>
          </div>

          <main className="lp-main">
            <LaunchHero brand={brand} customer={customer} />
            <LaunchProgress phases={LIFECYCLE} intakePct={overallPct} />

            <div className="lp-work">
              <div style={{ minWidth: 0 }}>
                <OnboardingStepShell
                  step={step}
                  total={STEPS.length}
                  pct={overallPct}
                  saved={saved}
                  onSaveDraft={saveDraft}
                  onBack={prev ? () => goTo(prev.key) : null}
                  onContinue={next ? () => goTo(next.key) : null}
                  continueLabel="Save & Continue"
                >
                  <StepBody
                    v={answers}
                    set={set}
                    brand={brand}
                    customer={customer}
                    steps={STEPS}
                    onGoTo={goTo}
                  />
                </OnboardingStepShell>

                <WhatWeLaunch brand={brand} customer={customer} />
              </div>

              <aside className="lp-side">
                <OnboardingProgressPanel
                  steps={STEPS}
                  activeKey={active}
                  onSelect={goTo}
                  overallPct={overallPct}
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
