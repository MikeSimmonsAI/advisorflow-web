/**
 * PreviewBanner — the strip that says nobody is really here.
 *
 * WHY THIS IS LOUD
 * ================
 * An operator opening a customer's onboarding needs to be certain, at a
 * glance, of three things they would otherwise have to assume:
 *
 *   the customer has NOT been invited by this page existing,
 *   nothing on this screen was created by opening it,
 *   and the answers shown are not the customer's typed answers.
 *
 * A subtle indicator gets missed, and the failure mode of missing it is an
 * operator telling a customer "you're at 40%" from a screen the customer has
 * never opened, or believing an invitation went out because a page rendered.
 *
 * It is fixed to the top of the shell rather than placed in the flow, so it
 * survives scrolling to the bottom of a long form.
 */
export default function PreviewBanner({ context }) {
  if (!context) return null
  return (
    <div className="lp-preview" role="status">
      <span className="lp-preview-tag">Preview</span>
      <span className="lp-preview-words">
        <b>{context.organization_name}</b>
        <span>
          This is the customer&rsquo;s own onboarding experience, read-only.
          No invitation was sent, nothing was created by opening it
          {context.answers_included === false
            ? ', and their typed answers are not shown here'
            : ''}.
        </span>
      </span>
      {context.viewed_by
        ? <span className="lp-preview-by">Viewed by {context.viewed_by}</span>
        : null}
    </div>
  )
}
