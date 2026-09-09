/**
 * WhatWeLaunch — the implementation direction, stated to the customer.
 *
 * THE CUSTOMER IS NEVER ASKED "WHAT AUTOMATION DO YOU WANT?". They bought a
 * result, not a specification exercise, and the brand is the technology expert
 * in the relationship. So this is not a menu and nothing in it is selectable —
 * it is the answer, shown while they fill the form, so that twenty fields of
 * company detail have a visible purpose attached to them.
 *
 * It renders on the light document as a plain informational band rather than
 * as another card competing with the form.
 */
import { DELIVERABLES } from './launchConfig'

export default function WhatWeLaunch({ brand, customer }) {
  return (
    <article className="lp-doc" style={{ marginTop: 20 }}>
      <div className="lp-doc-h">
        <p className="lp-stepno">What {brand.name} will build and launch</p>
        <h2>Your system, on go-live day</h2>
        <p>
          You do not need to specify any of this. It is what {brand.name}
          {' '}delivers for {customer.name} as part of this implementation —
          built from the answers you are giving us now, tested with your team,
          and handed over working.
        </p>
      </div>
      <div className="lp-doc-b" style={{ paddingBottom: 24 }}>
        <div className="lp-deliver">
          {DELIVERABLES.map((d, i) => (
            <div className="lp-ditem" key={d.t}>
              <span className="lp-dnum">{i + 1}</span>
              <span>
                <b>{d.t}</b>
                <span>{d.d}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </article>
  )
}
