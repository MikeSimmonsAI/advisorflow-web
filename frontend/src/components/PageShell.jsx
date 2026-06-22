import '../styles/shared.css'

export default function PageShell({ eyebrow, title, subtitle, action, children, className = '' }) {
  return (
    <section className={`page-shell ${className}`.trim()}>
      <header className="page-shell__header panel">
        <div>
          {eyebrow ? <p className="page-shell__eyebrow">{eyebrow}</p> : null}
          <h1 className="page-shell__title">{title}</h1>
          {subtitle ? <p className="page-shell__subtitle">{subtitle}</p> : null}
        </div>
        {action ? <div className="page-shell__action">{action}</div> : null}
      </header>
      {children}
    </section>
  )
}
