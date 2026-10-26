import WorkspaceArt from './art/WorkspaceArt'
import './workspaceCard.css'

/**
 * Reusable branded workspace card (UI-REDESIGN design system).
 *
 * Presentation only: it renders an item it is handed and calls onOpen. It
 * never decides whether the item is allowed — callers pass only entries the
 * server authorized.
 *
 * Accessibility: one real <button> per card ("Open workspace"), named for the
 * business; its hit area is stretched over the whole card so the card is
 * clickable without nesting interactive elements or adding extra tab stops.
 */
export default function WorkspaceCard({ item, onOpen, layout = 'grid', actionLabel = 'Open workspace', href }) {
  const variant = item.variant || 'general'
  const facts = item.facts || []
  const name = item.display
  return (
    <article className={'ev-wcard ev-wcard--' + layout} data-accent={variant} data-variant={variant}>
      <div className="ev-wcard-art">
        <WorkspaceArt variant={variant} />
        <span className="ev-wcard-badge" aria-hidden="true">{item.symbol}</span>
      </div>
      <div className="ev-wcard-body">
        <p className="ev-wcard-kind">{item.kind}</p>
        <h3 className="ev-wcard-name">{name}</h3>
        {item.orgLabel && <p className="ev-wcard-org">{item.orgLabel}</p>}
        <p className="ev-wcard-summary">{item.summary}</p>
        {facts.length > 0 && (
          <ul className="ev-wcard-facts" aria-label={name + ' focus areas'}>
            {facts.map(f => <li key={f}>{f}</li>)}
          </ul>
        )}
        <div className="ev-wcard-foot">
          {href ? (
            // A REAL LINK, not window.open: a script-opened tab is a pop-up,
            // and browsers (and the desktop app's browser) block pop-ups - so
            // the button looked clickable and did nothing.
            <a className="ev-btn ev-btn--primary ev-wcard-open" href={href}
               aria-label={actionLabel + ': ' + name + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')}>
              {actionLabel}
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                <path d="M3 8h9M8.5 4l4 4-4 4" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          ) : (
            <button type="button" className="ev-btn ev-btn--primary ev-wcard-open"
                    aria-label={actionLabel + ': ' + name + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')}
                    onClick={() => onOpen(item)}>
              {actionLabel}
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                <path d="M3 8h9M8.5 4l4 4-4 4" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </article>
  )
}
