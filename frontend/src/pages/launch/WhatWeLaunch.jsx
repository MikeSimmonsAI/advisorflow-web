/**
 * WhatWeLaunch — the implementation plan, stated to the customer.
 *
 * THE CUSTOMER IS NEVER ASKED "WHAT AUTOMATION DO YOU WANT?". They bought a
 * result, not a specification exercise, and the brand is the technology expert
 * in the relationship. So this is not a menu and nothing in it is selectable —
 * it is the answer, shown while they fill the form, so that twenty fields of
 * company detail have a visible purpose attached to them.
 *
 * ===========================================================================
 * V2: A PLAN IN THREE PARTS, NOT A GRID OF NINE
 * ===========================================================================
 *
 * The nine deliverables are unchanged. What changed is that they are no longer
 * nine equal tiles, because nine equal tiles is the shape of a pricing page —
 * and a customer who has already signed reads a feature grid as a pitch aimed
 * at somebody else.
 *
 * Grouped into three parts, each stamped with the journey stages it covers and
 * the intake sections that configure it, the same nine read as the programme
 * the customer is currently inside. It also answers, without being asked, the
 * question every long form provokes: why are you asking me all this.
 *
 * It renders as a band on the light canvas rather than as another white card
 * competing with the form.
 */
import { DELIVERABLES, DELIVERABLE_GROUPS } from './launchConfig'

export default function WhatWeLaunch({ brand, customer }) {
  return (
    <section className="lp-band">
      <div className="lp-band-h">
        <h2>What {brand.name} will build for {customer.name}</h2>
        <p>Configured from the answers you give above — you do not need to specify any of it.</p>
      </div>

      <div className="lp-plan">
        {DELIVERABLE_GROUPS.map(group => (
          <article className="lp-bcard" key={group.title}>
            <p className="lp-bk">
              {group.kicker}
              <span className="lp-bs">{group.stages}</span>
            </p>
            <h4>{group.title}</h4>
            <p>{group.blurb}</p>
            <div className="lp-bl">
              {group.items.map(index => {
                const d = DELIVERABLES[index]
                if (!d) return null
                return (
                  <div className="lp-bi" key={d.t}>
                    <span className="lp-bn">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="lp-bt">
                      <b>{d.t}</b>
                      <span>{d.d}</span>
                    </span>
                  </div>
                )
              })}
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
