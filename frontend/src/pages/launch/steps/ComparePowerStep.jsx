/**
 * Step 4 — ComparePower Integration.
 *
 * THE CUSTOMER IS NOT BEING ASKED TO INTEGRATE ANYTHING. They are being asked
 * who to introduce us to. The note at the top says which side of the line each
 * party is on, because a customer who thinks this is their engineering task
 * will stall here for weeks.
 */
import { Group, Field, Text, Select, Area, Note, Collapse, Fields,
         YES_NO_PENDING } from '../LaunchUI'

export default function ComparePowerStep({ v, set, brand }) {
  return (
    <>
      <Note tone="info" icon="plug" title="Who does what">
        ComparePower provides the electricity plan and rate information your
        site will show. {brand.name} handles the integration end to end — the
        API work, the testing, the rate display and the ongoing maintenance.
        All we need from you is the relationship and the right names.
      </Note>

      <Group title="Partner Relationship"
        sub="Your existing contact at ComparePower — the commercial one, not their support desk.">
        <Field label="Partner Contact" span={6}>
          <Text value={v.cpContact} onChange={x => set('cpContact', x)} />
        </Field>
        <Field label="Affiliate / Partner ID" span={6}
          hint="If you already have one issued to you.">
          <Text value={v.cpPartnerId} onChange={x => set('cpPartnerId', x)} />
        </Field>
        <Field label="Contact Email" span={6}>
          <Text type="email" value={v.cpEmail} onChange={x => set('cpEmail', x)} />
        </Field>
        <Field label="Phone" span={6}>
          <Text type="tel" value={v.cpPhone} onChange={x => set('cpPhone', x)} />
        </Field>
      </Group>

      <Group title="Technical Handoff"
        sub="Who we talk to about the API itself. Often a different person from your commercial contact — an introduction email from you is usually all it takes.">
        <Field label="Technical / API Contact" span={6}>
          <Text value={v.cpTech} onChange={x => set('cpTech', x)}
            placeholder="Name and email" />
        </Field>
        <Field label="API Documentation URL or location" span={6}
          hint="A link, a shared drive folder, or 'they email a PDF' — whatever is true.">
          <Text value={v.cpDocsUrl} onChange={x => set('cpDocsUrl', x)} />
        </Field>
        <Field label="Sandbox Access" span={6}>
          <Select value={v.cpSandbox} onChange={x => set('cpSandbox', x)}
            options={YES_NO_PENDING} />
        </Field>
        <Field label="Production Access" span={6}>
          <Select value={v.cpProd} onChange={x => set('cpProd', x)}
            options={YES_NO_PENDING} />
        </Field>
      </Group>

      <Collapse title="Notes" meta="Terms, limits, history" open>
        <Fields>
          <Field label="Anything about this relationship we should know" span={12}
            hint="Rate limits, revenue share terms, plans you are excluded from, a previous integration that did not go well.">
            <Area rows={4} value={v.cpNotes} onChange={x => set('cpNotes', x)} />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
