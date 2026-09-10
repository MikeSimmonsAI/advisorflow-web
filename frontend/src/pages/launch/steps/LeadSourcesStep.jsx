/**
 * Step 4 — Lead Sources & Integrations.
 *
 * THE CUSTOMER IS NOT BEING ASKED TO INTEGRATE ANYTHING. They are being asked
 * who to introduce us to. The note at the top says which side of the line each
 * party is on, because a customer who thinks this is their engineering task
 * will stall here for weeks.
 *
 * WHITE-LABEL: no vertical and no named partner appears in rendered copy. The
 * labels here mirror the server STEP_SCHEMA for `compare` exactly, so the
 * wizard, the staff review and the mobile app read the same words. The stored
 * keys (`cpContact`, `cpEmail`, ...) are unchanged — no migration.
 */
import { Group, Field, Text, Select, Area, Note, Collapse, Fields,
         YES_NO_PENDING } from '../LaunchUI'

export default function LeadSourcesStep({ v, set, brand }) {
  return (
    <>
      <Note tone="info" icon="plug" title="Who does what">
        Your lead sources and partners supply the listings, rates or referrals
        your site will show. {brand.name} handles the integration end to end —
        the API work, the testing, the display and the ongoing maintenance.
        All we need from you is the relationship and the right names.
      </Note>

      <Group title="Partner Relationship"
        sub="Your existing contact at the marketplace, comparison site or partner that sends you business — the commercial one, not their support desk.">
        <Field label="Main lead source or partner" span={6}
          hint="The marketplace, comparison site or partner that sends you business.">
          <Text value={v.cpContact} onChange={x => set('cpContact', x)} />
        </Field>
        <Field label="Partner / affiliate ID" span={6}
          hint="If you already have one issued to you.">
          <Text value={v.cpPartnerId} onChange={x => set('cpPartnerId', x)} />
        </Field>
        <Field label="Partner contact email" span={6}>
          <Text type="email" value={v.cpEmail} onChange={x => set('cpEmail', x)} />
        </Field>
        <Field label="Partner contact phone" span={6}>
          <Text type="tel" value={v.cpPhone} onChange={x => set('cpPhone', x)} />
        </Field>
      </Group>

      <Group title="Technical Handoff"
        sub="Who we talk to about the API itself. Often a different person from your commercial contact — an introduction email from you is usually all it takes.">
        <Field label="Their technical contact" span={6}>
          <Text value={v.cpTech} onChange={x => set('cpTech', x)}
            placeholder="Name and email" />
        </Field>
        <Field label="API documentation URL" span={6}
          hint="A link, a shared drive folder, or 'they email a PDF' — whatever is true.">
          <Text value={v.cpDocsUrl} onChange={x => set('cpDocsUrl', x)} />
        </Field>
        <Field label="Sandbox access" span={6}>
          <Select value={v.cpSandbox} onChange={x => set('cpSandbox', x)}
            options={YES_NO_PENDING} />
        </Field>
        <Field label="Production access" span={6}>
          <Select value={v.cpProd} onChange={x => set('cpProd', x)}
            options={YES_NO_PENDING} />
        </Field>
      </Group>

      <Collapse title="Notes" meta="Terms, limits, history" open>
        <Fields>
          <Field label="Anything about this relationship we should know" span={12}
            hint="Rate limits, revenue share terms, listings you are excluded from, a previous integration that did not go well.">
            <Area rows={4} value={v.cpNotes} onChange={x => set('cpNotes', x)} />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
