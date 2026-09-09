/**
 * OnboardingStepShell — the white document every intake step renders inside.
 *
 * One place decides what a step looks like: the eyebrow, the title, the
 * explanation, the completion meter, and the two actions at the bottom. A step
 * component supplies only its fields.
 *
 * STAGE 1: Save Draft and Save & Continue are local. Draft flashes a
 * confirmation and does nothing else; Continue advances to the next step.
 * Neither touches the network, and nothing survives a refresh.
 */
export default function OnboardingStepShell({
  step, total, pct, children,
  onSaveDraft, onContinue, onBack,
  continueLabel = 'Save & Continue', saved,
}) {
  return (
    <article className="lp-doc">
      <div className="lp-doc-h">
        <div className="lp-doc-htop">
          <div style={{ minWidth: 0, flex: '1 1 340px' }}>
            <p className="lp-stepno">Step {step.n} of {total}</p>
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

      <div className="lp-doc-f">
        {onBack
          ? <button type="button" className="lp-btn ghost" onClick={onBack}>
              Back
            </button>
          : null}
        <button type="button" className="lp-btn" onClick={onSaveDraft}>
          Save Draft
        </button>
        {saved ? <span className="lp-flash">✓ Draft saved</span> : null}
        <span className="lp-fspace" />
        <span className="lp-fnote">Prototype — nothing is submitted yet</span>
        {onContinue
          ? <button type="button" className="lp-btn primary" onClick={onContinue}>
              {continueLabel}
            </button>
          : null}
      </div>
    </article>
  )
}
