/**
 * PreviewBanner — the strip that says nobody is really here.
 *
 * ===========================================================================
 * INTERNAL ONLY, AND STRUCTURALLY SO
 * ===========================================================================
 *
 * This component is rendered from exactly one place — `preview ? <PreviewBanner
 * …/> : null` in LaunchPad — and `preview` is true only on the staff route
 * that carries an organization id. The customer's own `/launch` route has no
 * id, so `preview` is false, so this cannot render on it. There is no flag, no
 * query string and no configuration row that turns it on for a customer: the
 * only way to see it is to be on the staff preview route, and the only way to
 * be on that route is to be staff who may preview that customer.
 *
 * That matters more than it looks. The failure mode of a "preview mode" that
 * is a setting rather than a route is a real customer, mid-onboarding, reading
 * a ribbon that tells them their answers are not being saved.
 *
 * ===========================================================================
 * WHY IT IS NOW QUIET
 * ===========================================================================
 *
 * The first version was loud on purpose — purple, two lines, a paragraph of
 * explanation — because the thing it had to prevent was an operator believing
 * a customer had seen this page. It succeeded and then overshot: it dominated
 * the customer portal it was framing, which made the portal impossible to
 * judge, which is the other half of what a preview is for.
 *
 * So V2 keeps every fact and drops the volume. One line, thirty pixels, the
 * two things an operator must not get wrong stated first — this is READ ONLY,
 * and nothing here was saved or sent — with the way back on the right. It
 * reads as internal chrome rather than as a developer banner, and it no longer
 * competes with the page underneath it.
 */
export default function PreviewBanner({ context, backTo = '/god/launches',
                                        backLabel = 'Back to Customer Launches' }) {
  if (!context) return null
  return (
    <div className="lp-preview" role="status">
      <span className="lp-preview-tag">Preview — Read Only</span>
      <span className="lp-preview-sep" aria-hidden="true" />
      <span className="lp-preview-words">
        <b>{context.organization_name}</b>
        {' — '}
        <span>
          nothing on this screen is saved or sent, and no invitation went out
          {context.answers_included === false
            ? '. Their typed answers are not shown here'
            : ''}.
        </span>
      </span>
      {context.viewed_by
        ? <span className="lp-preview-by">Viewed by {context.viewed_by}</span>
        : null}
      <a className="lp-preview-exit" href={backTo}>
        {backLabel}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M7 17L17 7M17 7H9M17 7v8" />
        </svg>
      </a>
    </div>
  )
}
