/**
 * PageBoundary — one page failing must not take the application with it.
 *
 * ===========================================================================
 * THE FAILURE THIS EXISTS TO CONTAIN
 * ===========================================================================
 *
 * `crm-summary` returned `stage_counts` as a `{stage: count}` object and the
 * Reports page read it as an array — `(stage_counts || []).slice(0, 4)`. An
 * object is truthy, so the guard never fired and `.slice` threw during render.
 *
 * React 18 unmounts the ENTIRE tree when a render throws and nothing catches
 * it. Not the panel. Not the page. Everything — including the navigation rail,
 * which is the one thing a person needs in order to leave a broken page. The
 * application became a blank background colour, and stayed blank through every
 * subsequent client-side navigation, because there was no longer a tree to
 * navigate. Only a full browser reload rebuilt it, which is exactly the
 * "click Reports, get a blank page, refresh, it works" report that found this.
 *
 * The specific shape bug is fixed on both sides. This is the guarantee that
 * the next one costs a card inside the content area instead of the whole app.
 *
 * ===========================================================================
 * WHY IT IS KEYED ON THE ROUTE
 * ===========================================================================
 *
 * An error boundary latches. Without a reset it would hold the failed state
 * for the rest of the session, so a person who hit one bad page would see the
 * error card on every page afterwards — a worse bug than the one being
 * contained. The caller remounts this on pathname change, which clears it.
 *
 * ===========================================================================
 * WHAT IT WILL NOT DO
 * ===========================================================================
 *
 * It does not reload, redirect or retry by itself. A boundary that reloads is
 * a boundary that hides a render loop as a flickering page, and the failure
 * stops being reportable. The person decides.
 *
 * It does not print the error message, the stack or the component tree into
 * the page. Those carry ids and internal paths and this renders on customer
 * surfaces. The detail goes to the console, where an operator can read it and
 * a customer is not shown it.
 */
import { Component } from 'react'

export default class PageBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    // Stores no error object on purpose. Nothing below renders one, and a
    // field that exists is a field somebody later prints.
    return { failed: true }
  }

  componentDidCatch(error, info) {
    try {
      // eslint-disable-next-line no-console
      console.error('[page] render failed', error, info && info.componentStack)
    } catch (e) { /* never let reporting be the thing that breaks */ }
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <div className="panel" style={{ maxWidth: 560, margin: '48px auto', padding: '28px 26px' }}>
        <h2 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 17,
                     fontWeight: 700, color: 'var(--text-primary)' }}>
          This screen could not be displayed
        </h2>
        <p style={{ margin: '10px 0 0', fontSize: 13, lineHeight: 1.55,
                    color: 'var(--text-secondary)' }}>
          It loaded but could not finish drawing. Nothing was changed, saved or
          sent, and everything else in your account is unaffected — use the
          menu on the left to carry on.
        </p>
        <button
          type="button"
          onClick={() => this.setState({ failed: false })}
          style={{ marginTop: 18, border: '1px solid var(--border-strong)',
                   background: 'var(--bg-panel)', color: 'var(--text-primary)',
                   fontFamily: 'var(--font-body)', fontSize: 13, fontWeight: 600,
                   padding: '9px 15px', borderRadius: 'var(--radius-sm)',
                   cursor: 'pointer' }}
        >
          Try this screen again
        </button>
      </div>
    )
  }
}
