/**
 * Step 6 — Customer Process. THE MOST IMPORTANT SECTION IN THE INTAKE.
 *
 * Everything the automation does is a reproduction of what this customer
 * already does by hand. Get this wrong and the system is fast at the wrong
 * thing.
 *
 * THE FLOW DIAGRAM IS SHOWN BEFORE THE QUESTIONS ARE ASKED. Somebody who runs
 * a business does not think in stages until they are shown the stages; the
 * five boxes at the top are what turn "we just call them back" into six
 * specific answers.
 */
import { Group, Field, Text, Area, Select, Note, Ico, Fields, Collapse }
  from '../LaunchUI'

// The customer's own name, not a literal. This read "Atlantis receives it" —
// which is correct for exactly one customer and wrong, visibly and
// embarrassingly, for every other one that ever opens this screen.
const flowFor = name => ([
  { k: 'Stage 1', t: 'Customer submits information' },
  { k: 'Stage 2', t: (name || 'You') + ' receive' + (name ? 's' : '') + ' it' },
  { k: 'Stage 3', t: 'Team reviews / contacts customer' },
  { k: 'Stage 4', t: 'Quote, booking or enrolment action' },
  { k: 'Stage 5', t: 'Follow-up and completion' },
])

const RESPONSE = [
  { value: '', label: 'Select…' },
  { value: 'minutes',   label: 'Within minutes' },
  { value: 'hour',      label: 'Within an hour' },
  { value: 'same_day',  label: 'Same business day' },
  { value: 'next_day',  label: 'Next business day' },
  { value: 'varies',    label: 'It varies / no standard today' },
]

export default function CustomerProcessStep({ v, set, customer }) {
  return (
    <>
      <Note tone="warn" icon="info"
        title="What happens after a customer gives you their information?">
        This is the section the whole build turns on. Describe what actually
        happens today — including the parts that are inconsistent or done from
        someone's phone. We are reproducing your process, then removing the
        waiting from it. We are not replacing your judgement.
      </Note>

      <Group title="The Journey Today"
        sub={'Roughly how an enquiry moves through ' + customer.name + ' right now. Use it to frame your answers below.'} >
        <Field span={12} label="">
          <div className="lp-flow">
            {flowFor(customer?.name).map(s => (
              <div className="lp-fstep" key={s.k}>
                <div className="lp-fbox">
                  <b>{s.k}</b>
                  <span>{s.t}</span>
                </div>
                <span className="lp-farrow">
                  <Ico name="arrow" size={16} stroke />
                </span>
              </div>
            ))}
          </div>
        </Field>
      </Group>

      <Group title="Your Process, In Your Words">
        <Field label="Describe your current process in detail" span={12} required
          hint="Write it the way you would explain it to a new hire on their first morning. Length is welcome here.">
          <Area rows={7} value={v.processDetail}
            onChange={x => set('processDetail', x)}
            placeholder="A customer fills in the form on our site. It emails the care inbox. Whoever is on that inbox that morning…" />
        </Field>
      </Group>

      <Group title="Who, and How Fast">
        <Field label="First person or team to receive customer information"
          span={6} required>
          <Text value={v.firstReceiver} onChange={x => set('firstReceiver', x)} />
        </Field>
        <Field label="Response-time expectation" span={6} required
          hint="What you promise a customer today — not what you wish you promised.">
          <Select value={v.responseTime} onChange={x => set('responseTime', x)}
            options={RESPONSE} />
        </Field>
        <Field label="Who reviews options or pricing with the customer" span={6} required>
          <Text value={v.rateReviewer} onChange={x => set('rateReviewer', x)} />
        </Field>
        <Field label="Who helps the customer sign up" span={6} required>
          <Text value={v.enrollmentHelper}
            onChange={x => set('enrollmentHelper', x)} />
        </Field>
      </Group>

      <Group title="Endings"
        sub="Both of them. The system needs to know when to stop as clearly as it knows when to start.">
        <Field label="What marks a customer complete?" span={12} required
          hint="The specific event — signup confirmed, first invoice issued, contract signed.">
          <Area rows={3} value={v.completionMarker}
            onChange={x => set('completionMarker', x)} />
        </Field>
        <Field label="What happens if the customer does not respond?" span={12}
          required
          hint="How many attempts, over how long, on which channels, and what you do with them after that.">
          <Area rows={4} value={v.noResponse}
            onChange={x => set('noResponse', x)}
            placeholder="Today: two calls and an email over about a week, then they sit in the inbox…" />
        </Field>
      </Group>

      <Collapse title="Exceptions & Edge Cases"
        meta="The ones that eat your team's day">
        <Fields>
          <Field label="Which situations break the normal process?" span={12}>
            <Area rows={4} value={v.processExceptions}
              onChange={x => set('processExceptions', x)}
              placeholder="Commercial accounts, customers with a prior balance, moves versus switches, anyone outside your service territory…" />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
