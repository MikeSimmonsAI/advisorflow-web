/* The visible half of a failed or refreshing load (see wsRecordHook): an alert
 * with an accessible retry, the server's support code only when it sent one, a
 * plain "may be out of date" when old data is still showing, and a polite
 * status line while a refresh is running. Renders nothing when all is well. */
export default function RecoveryNote({ what, rec, view, onRetry, effect = '' }) {
  if (view === 'error' || view === 'stale') {
    const code = rec.supportCode ? ` (support code ${rec.supportCode})` : ''
    return (
      <div className="evo-alert evo-alert--warn" role="alert">
        {what} could not be {view === 'stale' ? 'refreshed' : 'loaded'}: {rec.error}{code}.
        {view === 'stale' ? ' What is shown was loaded earlier and may be out of date.' : ''}
        {effect ? ` ${effect}` : ''}
        {' '}<button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={onRetry}>Try again</button>
      </div>
    )
  }
  return <p className="evo-sr" role="status">{view === 'refreshing' ? `Refreshing ${what.toLowerCase()}.` : ''}</p>
}
