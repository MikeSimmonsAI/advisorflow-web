/**
 * Step 8 — Review & Submit.
 *
 * Two jobs, in this order:
 *
 *   1. SAY WHAT IS MISSING, SPECIFICALLY. "Files & Documents — 2 items
 *      missing" is actionable; a red X is not. Each incomplete row names what
 *      it is waiting on and offers the jump to fix it.
 *
 *   2. TAKE THE SIGN-OFF. Typed name, title and company, plus an explicit
 *      affirmation, all of which are stored with the submission snapshot.
 *
 * THE MISSING LIST IS THE SERVER'S, NOT A CONSTANT. It was a hard-coded object
 * naming two files; it is now `step.missing` from the same arithmetic that
 * decides whether Submit is allowed at all. A review screen whose "what is
 * outstanding" comes from a different source than the submit gate will one day
 * show a green page beside a refusal, and the customer will be right to be
 * angry about it.
 */
import { useState } from 'react'
import { Group, Field, Text, Note, Ico } from '../LaunchUI'

export default function ReviewSubmitStep({
  v, set, steps, onGoTo, customer, blockers = [], onSubmit,
  submission, readOnly = false,
}) {
  const [mode, setMode] = useState('type')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [done, setDone] = useState(null)
  const affirmed = !!v.sigAffirm
  const reviewable = steps.filter(s => s.key !== 'review')

  // Everything still outstanding anywhere, grouped by the step that owns it.
  const missingByStep = {}
  for (const b of blockers) {
    if (b.step_key === 'review') continue
    ;(missingByStep[b.step_key] = missingByStep[b.step_key] || []).push(b.label)
  }

  const submit = async () => {
    setBusy(true); setErr(null)
    try {
      const res = await onSubmit()
      setDone(res)
    } catch (e) {
      const d = e?.detail
      setErr(typeof d === 'string' ? d
        : d?.message || 'Could not submit. Nothing was sent.')
    } finally {
      setBusy(false)
    }
  }

  if (submission && !submission.reviewed_at) {
    return (
      <Note tone="info" icon="check" title="Submitted">
        Your intake was submitted{submission.submitted_at
          ? ' on ' + new Date(submission.submitted_at).toLocaleString() : ''}
        {submission.signed_name ? ' by ' + submission.signed_name : ''}. The
        implementation team has it. They will reopen this if anything needs
        changing.
      </Note>
    )
  }

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
              const items = missingByStep[s.key] || []
              const missing = items.length
                ? items.length + (items.length === 1 ? ' item' : ' items')
                  + ' missing — ' + items.join(', ')
                : null
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
                <span>Drawn signatures are not captured. Type your name
                  instead — it is stored with the submission.</span>
              </div>}
          <p className="lp-hint">
            Your typed name, title and company are recorded with the submission,
            together with the date and time you sent it. This is an
            acknowledgement of accuracy, not a legal e-signature.
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
        <button type="button" className="lp-btn primary"
          disabled={!affirmed || busy || readOnly || blockers.length > 0}
          onClick={submit}>
          <Ico name="check" size={15} stroke />
          {busy ? 'Submitting…' : 'Submit Onboarding'}
        </button>

        {/* THE BUTTON NEVER REFUSES SILENTLY. Whenever it is disabled this
            line says which of the three reasons it is, and an outstanding
            item names the section to go and fix. */}
        <span className="lp-hint" style={{ margin: 0 }}>
          {done
            ? 'Submitted. Thank you — the implementation team has it.'
            : err
              ? <span style={{ color: '#b91c1c', fontWeight: 600 }}>{err}</span>
              : blockers.length
                ? blockers.length + ' item' + (blockers.length === 1 ? '' : 's')
                  + ' still outstanding above.'
                : !affirmed
                  ? 'Tick the confirmation above to enable submission.'
                  : 'Everything is complete. This hands your intake to the team.'}
        </span>
      </div>
    </>
  )
}
