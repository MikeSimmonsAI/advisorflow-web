import './ProgramAccessPortal.css'
import { programCards } from './programAccessModel'

/**
 * Multi-program identity = one authorized chooser, not a customer CRM.
 * Caller supplies the server's /auth/my-contexts payload; this surface
 * never computes permissions or changes credentials.
 */

export default function ProgramAccessPortal({ contexts, onChoose }) {
  const { visible, others } = programCards(contexts)
  return (
    <main className="pa-portal">
      <div className="pa-shell">
        <header className="pa-head">
          <div className="pa-brand"><span className="pa-brand-mark">P</span><span>Program Access</span></div>
          <span className="pa-private">Authorized workspaces only</span>
        </header>
        <section className="pa-intro">
          <p className="pa-eyebrow">PROGRAM ADMINISTRATOR</p>
          <h1>Choose your business.</h1>
          <p>One login. Separate operations. Open a program to work inside its own dedicated experience.</p>
        </section>
        <section aria-label="Your programs" className="pa-grid">
          {visible.map((item, index) => (
            <button type="button" key={item.type + ':' + item.organization_id + ':' + (item.key || '')}
                    className={'pa-card pa-card--' + (item.is_program ? 'sci' : item.key === 'wholesale' ? 'wholesale' :
                      item.industry === 'energy' ? 'energy' : item.industry === 'roofing' || item.organization_slug === 'evosys-pro-roofing' ? 'roofing' : 'general')}
                    onClick={() => onChoose(item)}>
              <div className="pa-card-top">
                <span className="pa-card-symbol" aria-hidden="true">{item.is_program ? '✦' : item.key === 'wholesale' ? '⌂' : item.industry === 'energy' ? '⚡' : item.industry === 'roofing' || item.organization_slug === 'evosys-pro-roofing' ? '⌃' : '◈'}</span>
                <span className="pa-arrow" aria-hidden="true">↗</span>
              </div>
              <span className="pa-card-kind">{item.kind}</span>
              <strong>{item.display}</strong>
              <span className="pa-card-summary">{item.summary}</span>
              <span className="pa-card-open">Open program <span aria-hidden="true">→</span></span>
            </button>
          ))}
        </section>
        {others.length > 0 && (
          <section className="pa-others">
            <h2>Other authorized workspaces</h2>
            <div className="pa-others-list">{others.map(w =>
              <button type="button" key={w.organization_id} onClick={() => onChoose(w)}>
                <span>{w.organization_name}</span><span aria-hidden="true">→</span>
              </button>)}
            </div>
          </section>
        )}
        {!visible.length && !others.length && <p className="pa-empty">No workspaces have been assigned to this account.</p>}
        <footer className="pa-foot">Each program keeps its own data, workflow and permissions.</footer>
      </div>
    </main>
  )
}
