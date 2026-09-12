/**
 * OnboardingStepShell — the white document every intake step renders inside.
 *
 * One place decides what a step looks like: the eyebrow, the title, the
 * explanation, the completion meter, and the two actions at the bottom. A step
 * component supplies only its fields.
 *
 * Save Draft and Save & Continue are the SAME WRITE. The difference is
 * navigation, not persistence — making "continue" the only thing that saves is
 * how a customer who closes the tab on the last step loses an afternoon.
 *
 * `saving` disables both actions while a write is in flight, and `error`
 * replaces the footnote rather than sitting somewhere else on the page: a
 * failed save has to be visible exactly where the person expected success.
 *
 * ===========================================================================
 * V2: GUIDED, NOT ADMINISTRATIVE
 * ===========================================================================
 *
 * The fields are the same fields. What changed is the reading order at the
 * top of the card — what this section is called, whether anything in it is
 * still outstanding, and what it is FOR, before the first label — and the line
 * above the buttons that says, once, that nothing here is final until they
 * submit. Twenty fields of company detail with no stated purpose is a form; the
 * same twenty with a reason attached is an implementation questionnaire.
 *
 * THE STATE CHIP REPORTS, IT DOES NOT DECORATE. "Required" appears when the
 * server says required fields in this section are still unanswered, and
 * "Complete" when the section is at 100%. In between there is no chip, because
 * a section nobody has touched and a section that is merely optional are not
 * the same thing and this component cannot tell them apart.
 */
export default function OnboardingStepShell({
  step, total, pct, children,
  onSaveDraft, onContinue, onBack,
  continueLabel = 'Save & Continue', saved,
  saving = false, error = null, savedAt = null, missing = [],
  preview = false, brandName = null,
}) {
  // IN A PREVIEW BOTH ACTIONS STILL WORK — they navigate, they acknowledge,
  // and they write nothing. What changes is the WORDING, because a button
  // labelled "Save" that does not save is the dishonest half of a read-only
  // screen. See LaunchPad.persist: the simulated action returns true so the
  // journey is walkable end to end, and the notice says nothing was kept.
  const draftLabel = preview ? 'Save Draft (preview)' : 'Save Draft'
  const goLabel = preview ? 'Continue (preview)' : continueLabel
  const outstanding = missing && missing.length

  return (
    <article className="lp-doc">
      <div className="lp-doc-h">
        <div className="lp-doc-htop">
          <div style={{ minWidth: 0, flex: '1 1 340px' }}>
            <div className="lp-doc-kick">
              <p className="lp-stepno">Section {step.n} of {total}</p>
              {outstanding
                ? <span className="lp-reqchip">Required</span>
                : pct >= 100
                  ? <span className="lp-reqchip opt">Complete</span>
                  : null}
            </div>
            <h2>{step.title}</h2>
            <p>{step.blurb}</p>
          </div>
          <div className="lp-meter">
            <b>{pct}%</b>
            <span>Complete</span>
            <div className="lp-bar"><i style={{ width: pct + '%' }} /></div>
          </div>
        </div>
      </div>

      <div className="lp-doc-b">{children}</div>

      {/* Said once, above the actions, where somebody hesitating over a
          half-answered field will read it. */}
      <div className="lp-doc-b" style={{ paddingTop: 0, paddingBottom: 0 }}>
        <div className="lp-note info" style={{ marginBottom: 20 }}>
          <div className="lp-nb">
            <b>Nothing here is final.</b>
            <p>
              Save a draft at any point and come back. Your onboarding only
              goes to {brandName || 'your implementation team'} for review when
              you submit it in the last section.
            </p>
          </div>
        </div>
      </div>

      <div className="lp-doc-f">
        {onBack
          ? <button type="button" className="lp-btn ghost" onClick={onBack}
                    disabled={saving}>
              Back
            </button>
          : null}
        <button type="button" className="lp-btn" onClick={onSaveDraft}
                disabled={saving}
                title={preview ? 'Preview mode — nothing is saved' : undefined}>
          {saving ? 'Saving…' : draftLabel}
        </button>
        {saved ? <span className="lp-flash">✓ Saved</span> : null}
        <span className="lp-fspace" />

        {/* One line, three possible truths, in order of what the person most
            needs to know: a failure, then what is still outstanding, then when
            it last saved. Never all three at once. */}
        {error
          ? <span className="lp-fnote" style={{ color: '#b91c1c', fontWeight: 600 }}>
              {error}
            </span>
          : outstanding
            ? <span className="lp-fnote">
                Still needed: {missing.map(m => m.label).join(', ')}
              </span>
            : savedAt
              ? <span className="lp-fnote">
                  Saved {new Date(savedAt).toLocaleString()}
                </span>
              : null}

        {onContinue
          ? <button type="button" className="lp-btn primary" onClick={onContinue}
                    disabled={saving}
                    title={preview
                      ? 'Preview mode — advances without saving' : undefined}>
              {saving ? 'Saving…' : goLabel}
            </button>
          : null}
      </div>
    </article>
  )
}
