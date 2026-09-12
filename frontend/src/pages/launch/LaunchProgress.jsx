/**
 * LaunchProgress — the implementation lifecycle tracker.
 *
 * TWO DIFFERENT AXES, DELIBERATELY NOT MERGED:
 *
 *   THIS (top)          where the whole IMPLEMENTATION stands — intake,
 *                       access, build, integrations, test, training, launch.
 *                       Seven phases, most of them the delivering brand's
 *                       work, not the customer's.
 *
 *   The right panel     how far the customer is through the INTAKE FORM.
 *                       Eight sections, all of them theirs.
 *
 * A customer who has filled in two of eight sections is 25% through their
 * form and roughly 3% through their implementation. Showing one number for
 * both would be wrong in whichever direction it was rounded, and it is the
 * reason this reads as a programme rather than a wizard.
 *
 * THE LABELS ARE CONFIGURATION. `phases` is the configured journey when the
 * customer's experience resolves one, and the lifecycle otherwise. The STATES
 * come from the implementation either way — a stage is "in progress" because
 * the record says so, never because the label was written that way — so a
 * brand renaming a stage for one industry changes the words and nothing else.
 *
 * ===========================================================================
 * V2: STILL SEVEN, STILL HORIZONTAL, EASIER TO READ
 * ===========================================================================
 *
 * The direction was explicit that this must not become seven heavy cards, so
 * it did not. What changed is the hierarchy inside each stage:
 *
 *     a connector line and node per stage, so progress reads left to right
 *     THE STATE FIRST, as a word — complete, in progress, upcoming
 *     the stage name, weighted by state rather than coloured into a rainbow
 *     THE OWNER, because "is this waiting on me?" is the question people have
 *
 * The owner is configuration (`stage.owner`: customer | provider | both) and
 * renders only when the configured journey says so. A stage whose journey row
 * is silent about ownership gets no owner line rather than an asserted one —
 * telling a customer their launch is waiting on them when it is not is worse
 * than telling them nothing.
 *
 * `onSelect` makes a stage INSPECTABLE, not actionable. Clicking reveals what
 * the stage involves; it cannot advance the implementation, because a customer
 * deciding they are in Training does not make it so. Where no handler is
 * supplied the stages render as plain labels — the rule about never showing an
 * active-looking control that does nothing, applied in the other direction.
 */
function ownerLabel(owner, brandName) {
  if (owner === 'customer') return 'You'
  if (owner === 'provider') return brandName || 'Your launch team'
  if (owner === 'both') return 'Together'
  return null
}

export default function LaunchProgress({ phases, intakePct, title,
                                         brandName = null,
                                         onSelect = null, openKey = null }) {
  const doneCount = phases.filter(p => p.state === 'done').length
  const current = phases.find(p => p.state === 'now')
  const currentIndex = phases.findIndex(p => p.state === 'now')
  const open = openKey ? phases.find(p => p.key === openKey) : null

  return (
    <section className="lp-phases">
      <div className="lp-phases-h">
        <h2>{title || 'Your onboarding journey'}</h2>
        <span>
          {current
            ? <>Currently in <b>{current.label}
                {current.sublabel ? ' ' + current.sublabel : ''}</b>
                {' '}— {intakePct}% of your intake complete</>
            : <>{doneCount} of {phases.length} stages complete</>}
        </span>
        <span className="lp-phasesof">
          {currentIndex >= 0
            ? 'STAGE ' + (currentIndex + 1) + ' OF ' + phases.length
            : phases.length + ' STAGES'}
        </span>
      </div>

      <ol className="lp-phaserow">
        {phases.map((p, i) => {
          const owner = ownerLabel(p.owner, brandName)
          const body = (
            <>
              <span className="lp-pline">
                <span className="lp-pnum">
                  {p.state === 'done' ? '✓' : i + 1}
                </span>
              </span>
              <span className="lp-pstate">
                {p.state === 'done' ? 'Complete'
                  : p.state === 'now' ? 'In progress' : 'Upcoming'}
              </span>
              <span className="lp-plabel">
                {p.label}
                {p.sublabel ? <span className="lp-psub"> {p.sublabel}</span> : null}
              </span>
              {owner ? <span className="lp-powner">{owner}</span> : null}
            </>
          )
          const cls = 'lp-phase ' + p.state
            + (openKey === p.key ? ' open' : '')
          return (
            <li key={p.key} className={cls}>
              {onSelect ? (
                <button type="button" className="lp-phasebtn"
                        aria-expanded={openKey === p.key}
                        onClick={() => onSelect(openKey === p.key ? null : p.key)}>
                  {body}
                </button>
              ) : body}
            </li>
          )
        })}
      </ol>

      {/* WHAT THIS STAGE ACTUALLY MEANS, in the customer's own terms. It says
          where the stage stands and nothing about when it will finish — this
          module reports what HAS happened, never a date nobody committed to. */}
      {open ? (
        <div className="lp-phaseinfo">
          <b>{open.label}{open.sublabel ? ' · ' + open.sublabel : ''}</b>
          <span>
            {open.state === 'done'
              ? 'This stage is complete.'
              : open.state === 'now'
                ? 'This is where your launch stands right now.'
                : 'Not started yet — it begins once the stages before it are done.'}
            {ownerLabel(open.owner, brandName)
              ? ' Owner: ' + ownerLabel(open.owner, brandName) + '.'
              : ''}
          </span>
        </div>
      ) : null}
    </section>
  )
}
