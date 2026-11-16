import WorkspaceArt from './art/WorkspaceArt'
import './workspaceCard.css'
// The same key the mobile app uses, so desktop and mobile lists can be compared.
import { programKey } from '../pages/program/programAccessModel'

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
export default function WorkspaceCard({ item, onOpen, layout = 'grid', actionLabel: defaultLabel = 'Open workspace', href, compact = false, pinned, onTogglePin }) {
  const variant = item.variant || 'general'
  // A workspace may name its own action ("Open agency"); the default stays.
  const actionLabel = item.actionLabel || defaultLabel
  if (compact) {
    return <CompactCard item={item} onOpen={onOpen} layout={layout} actionLabel={actionLabel}
                        href={href} pinned={pinned} onTogglePin={onTogglePin} />
  }
  const facts = item.facts || []
  const name = item.display
  return (
    <article className={'ev-wcard ev-wcard--' + layout} data-accent={variant} data-variant={variant} data-program-key={programKey(item)}>
      <div className="ev-wcard-art">
        <WorkspaceArt variant={variant} />
        <span className="ev-wcard-badge" aria-hidden="true">{item.symbol}</span>
        {item.badge && <span className="ev-wcard-tag">{item.badge}</span>}
      </div>
      <div className="ev-wcard-body">
        <p className="ev-wcard-kind">{item.kind}</p>
        <h3 className="ev-wcard-name">{name}</h3>
        {item.subtitle && <p className="ev-wcard-sub">{item.subtitle}</p>}
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

/**
 * COMPACT launcher card (Program Access, Nov 2026): small mark, name, one
 * line. The whole card opens the destination: the single real link/button is
 * stretched over the card (one tab stop, Enter/Space work). The pin control is
 * a separate button that sits above that stretched area, so it never opens
 * the workspace by accident.
 */
function CompactCard({ item, onOpen, layout, actionLabel, href, pinned, onTogglePin }) {
  const variant = item.variant || 'general'
  const name = item.display
  const line = item.orgLabel ? item.orgLabel + ' — ' + (item.summary || '') : (item.summary || '')
  const label = actionLabel + ': ' + name + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')
  return (
    <article className={'ev-ccard ev-ccard--' + layout} data-accent={variant} data-variant={variant} data-program-key={programKey(item)}>
      <span className="ev-ccard-mark" aria-hidden="true">{item.symbol}</span>
      <div className="ev-ccard-text">
        <h3 className="ev-ccard-name">
          {href
            ? <a className="ev-ccard-open" href={href} aria-label={label}>{name}</a>
            : <button type="button" className="ev-ccard-open" aria-label={label}
                      onClick={() => onOpen(item)}>{name}</button>}
        </h3>
        <p className="ev-ccard-line" title={line}>{line}</p>
      </div>
      {item.badge && <span className="ev-ccard-tag">{item.badge}</span>}
      {onTogglePin && (
        <button type="button" className={'ev-ccard-pin' + (pinned ? ' is-pinned' : '')}
                aria-pressed={!!pinned}
                aria-label={(pinned ? 'Unpin ' : 'Pin ') + name + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')}
                title={pinned ? 'Unpin' : 'Pin to top'}
                onClick={() => onTogglePin(item)}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
            <path d="M9 3h6l-1 6 4 4v2h-5v6l-1 1-1-1v-6H6v-2l4-4z" fill={pinned ? 'currentColor' : 'none'}
                  stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
          </svg>
        </button>
      )}
    </article>
  )
}
