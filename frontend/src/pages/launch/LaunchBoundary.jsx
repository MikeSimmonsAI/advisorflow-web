/**
 * LaunchBoundary — a launch screen must never fail as an empty coloured page.
 *
 * ===========================================================================
 * THE FAILURE THIS EXISTS TO MAKE IMPOSSIBLE
 * ===========================================================================
 *
 * One unguarded property read in the header — `customer.user.name`, null on
 * every internal preview — threw during render. React 18 unmounts the whole
 * tree when a render throws and nothing catches it, so the page became the
 * background colour and nothing else. No header, no banner, no message, no
 * clue. An operator checking a real customer's onboarding before inviting them
 * saw a blank screen and had no way to tell whether the customer was missing,
 * the route was wrong, the data was broken or the feature had never shipped.
 *
 * A blank screen is the worst possible failure mode here, because this surface
 * exists to be LOOKED at. The specific bug is fixed; this is the guarantee
 * that the next one costs an error card instead of an investigation.
 *
 * ===========================================================================
 * WHAT IT SAYS, AND WHAT IT REFUSES TO SAY
 * ===========================================================================
 *
 * It names WHAT failed and offers the two things a person actually wants — try
 * again, or go back to where they came from.
 *
 * It does NOT print the stack, the component tree or the error's own message
 * into the page. Those can carry ids, tokens and internal paths, and this
 * component renders on a CUSTOMER-facing surface as well as an internal one.
 * The detail goes to the console, where an operator with devtools can read it
 * and a customer is not shown it.
 */
import { Component } from 'react'

import LaunchStyles from './LaunchStyles'

export default class LaunchBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    // Deliberately stores no error object. Nothing below renders it, and a
    // field that exists is a field somebody later prints.
    return { failed: true }
  }

  componentDidCatch(error, info) {
    // The full detail, to the console only.
    try {
      console.error('[launch] render failed', error, info && info.componentStack)
    } catch (e) { /* never let reporting be the thing that breaks */ }
  }

  render() {
    if (!this.state.failed) return this.props.children

    const back = this.props.backTo || null

    return (
      <div className="lp-scope" data-surface="launch">
        <LaunchStyles />
        <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center',
                      padding: '48px 24px', background: 'var(--lp-void)' }}>
          <div className="lp-doc" style={{ maxWidth: 560, width: '100%' }}>
            <div className="lp-doc-h">
              <p className="lp-stepno">Something went wrong</p>
              <h2>{this.props.title || 'This page could not be displayed'}</h2>
              <p>
                {this.props.detail
                  || 'The page loaded but could not finish drawing. Nothing was '
                     + 'changed, saved or sent.'}
              </p>
            </div>
            <div className="lp-doc-f">
              <button type="button" className="lp-btn primary"
                      onClick={() => window.location.reload()}>
                Try again
              </button>
              {back ? (
                <a className="lp-btn" href={back}>{this.props.backLabel || 'Go back'}</a>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    )
  }
}
