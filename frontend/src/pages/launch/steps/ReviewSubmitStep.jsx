/**
 * Step 8 — Review & Submit.
 *
 * Two jobs, in this order:
 *
 *   1. SAY WHAT IS MISSING, SPECIFICALLY. "Files & Documents — 2 items
 *      missing" is actionable; a red X is not. Each incomplete row names what
 *      it is waiting on and offers the jump to fix it.
 *
 *   2. TAKE THE SIGN-OFF. The signature block below is a VISUAL PROTOTYPE.
 *      There is no signature capture, no canvas drawing, no storage and no
 *      submission behind it in Stage 1 — the Submit button is disabled until
 *      the affirmation is ticked purely so the finished interaction can be
 *      judged, and then it does nothing.
 */
import { useState } from 'react'
import { Group, Field, Text, Note, Ico } from '../LaunchUI'

/** Stage 1 mock section status. Stage 2 computes this from the answers. */
const MISSING = {
  files: '2 items missing — sample customer data, SMS/email consent language',
}

export default function ReviewSubmitStep({ v, set, steps, onGoTo, customer }) {
  const [mode, setMode] = useState('type')
  const affirmed = !!v.sigAffirm
  const reviewable = steps.filter(s => s.key !== 'review')

  return (
    <>
      <Note tone="info" icon="clip" title="One last look">
        Nothing here has been sent yet. Check each section, fix anything marked
        outstanding, then sign off at the bottom to hand this to the
        implementation team.
      </Note>

      <Group title="Section Status">
        <Field span={12} label="">
          <div className="lp-review">
            {reviewable.map(s => {
              const complete = s.pct >= 100
              const missing = MISSING[s.key]
              const cls = 'lp-rrow ' + (complete ? 'ok' : missing ? 'miss'
                : s.pct > 0 ? 'miss' : '')
              return (
                <div className={cls} key={s.key}>
                  <span className="lp-rmark">{complete ? '✓' : '!'}</span>
                  <span className="lp-rname">
                    <b>{s.label}</b>
                    <span>
                      {complete ? 'Complete'
                        : missing ? missing
                        : s.pct > 0 ? 'Started — not yet complete'
                        : 'Not started'}
                    </span>
                  </span>
                  <span className="lp-rpct">
                    {complete ? '100%' : s.pct + '%'}
                  </span>
                  {/* "Complete" as a button label beside a 0% figure reads as
                      a status, not an action. The verb changes with the
                      state so the row never says "0% Complete". */}
                  <button type="button" className="lp-btn small"
                    onClick={() => onGoTo(s.key)}>
                    {complete ? 'Review' : s.pct > 0 ? 'Continue' : 'Start'}
                  </button>
                </div>
              )
            })}
          </div>
        </Field>
      </Group>

      <Group title="Acknowledgement"
        sub="Signed by someone authorised to confirm this information on behalf of the company.">
        <Field label="Full Legal Name" span={6} required>
          <Text value={v.sigName} onChange={x => set('sigName', x)} />
        </Field>
        <Field label="Title" span={6} required>
          <Text value={v.sigTitle} onChange={x => set('sigTitle', x)} />
        </Field>
        <Field label="Company" span={8} required>
          <Text value={v.sigCompany} onChange={x => set('sigCompany', x)} />
        </Field>
        <Field label="Date" span={4} required>
          <Text type="date" value={v.sigDate} onChange={x => set('sigDate', x)} />
        </Field>

        <Field span={12} label="Signature">
          <div className="lp-sigtabs" role="tablist">
            <button type="button" role="tab" aria-selected={mode === 'type'}
              className={'lp-sigtab' + (mode === 'type' ? ' on' : '')}
              onClick={() => setMode('type')}>Type Signature</button>
            <button type="button" role="tab" aria-selected={mode === 'draw'}
              className={'lp-sigtab' + (mode === 'draw' ? ' on' : '')}
              onClick={() => setMode('draw')}>Draw Signature</button>
          </div>

          {mode === 'type'
            ? <input className="lp-sigtype" value={v.sigTyped ?? ''}
                onChange={e => set('sigTyped', e.target.value)}
                placeholder="Type your full name"
                aria-label="Typed signature" />
            : <div className="lp-sigpad">
                <span>Signature capture arrives with the signed submission —
                  Stage 2</span>
              </div>}
          <p className="lp-hint">
            Prototype only. No signature is captured, stored or transmitted in
            this build.
          </p>
        </Field>
      </Group>

      <label className={'lp-check' + (affirmed ? ' on' : '')}>
        <input type="checkbox" checked={affirmed}
          onChange={e => set('sigAffirm', e.target.checked)} />
        <span>
          I confirm that the information provided in this onboarding intake is
          accurate to the best of my knowledge, and that I am authorised to
          provide it on behalf of {customer.name}.
        </span>
      </label>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap',
                    margin: '20px 0 6px', alignItems: 'center' }}>
        <button type="button" className="lp-btn primary" disabled={!affirmed}>
          <Ico name="check" size={15} stroke />
          Submit Onboarding
        </button>
        <span className="lp-hint" style={{ margin: 0 }}>
          {affirmed
            ? 'Disabled in this prototype — submission arrives in Stage 2.'
            : 'Tick the confirmation above to enable submission.'}
        </span>
      </div>
    </>
  )
}
