/**
 * LaunchProgress — the implementation lifecycle tracker.
 *
 * TWO DIFFERENT AXES, DELIBERATELY NOT MERGED:
 *
 *   THIS (top)          where the whole IMPLEMENTATION stands — intake,
 *                       access, build, integrations, test, training, launch.
 *                       Seven phases, most of them EvoSys Pro's work, not the
 *                       customer's.
 *
 *   The right panel     how far the customer is through the INTAKE FORM.
 *                       Eight sections, all of them theirs.
 *
 * A customer who has filled in two of eight sections is 25% through their
 * form and roughly 3% through their implementation. Showing one number for
 * both would be wrong in whichever direction it was rounded, and it is the
 * reason this reads as a programme rather than a wizard.
 */
export default function LaunchProgress({ phases, intakePct }) {
  const doneCount = phases.filter(p => p.state === 'done').length
  const current = phases.find(p => p.state === 'now')
  return (
    <section className="lp-phases">
      <div className="lp-phases-h">
        <h2>Implementation Lifecycle</h2>
        <span>
          {current
            ? <>Currently in <b style={{ color: 'var(--lp-gold2)' }}>
                {current.label}</b> — {intakePct}% of your intake complete</>
            : <>{doneCount} of {phases.length} phases complete</>}
        </span>
      </div>
      <ol className="lp-phaserow">
        {phases.map((p, i) => (
          <li key={p.key} className={'lp-phase ' + p.state}>
            <span className="lp-pnum">
              {p.state === 'done' ? '✓' : i + 1}
            </span>
            <span className="lp-plabel">{p.label}</span>
            <span className="lp-pstate">
              {p.state === 'done' ? 'Complete'
                : p.state === 'now' ? 'In progress' : 'Upcoming'}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}
