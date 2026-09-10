/**
 * Step 1 — Company Information.
 *
 * The legal and operating facts every later step depends on. Seeded with
 * believable placeholder values so the screen reads as a working one rather
 * than an empty template — see MOCK_ANSWERS in launchConfig.
 *
 * The two collapsed areas at the bottom are the overflow. A twenty-field form
 * that opens with "anything else?" teaches people to skim; asked last and
 * folded away, it gets answered by the people who actually have something.
 */
import { Group, Field, Text, Select, Area, Collapse, Fields } from '../LaunchUI'

const EMPLOYEES = [
  { value: '', label: 'Select…' },
  { value: '1-10', label: '1 – 10' },
  { value: '11-24', label: '11 – 24' },
  { value: '25-50', label: '25 – 50' },
  { value: '51-100', label: '51 – 100' },
  { value: '100+', label: 'More than 100' },
]

export default function CompanyInformationStep({ v, set }) {
  return (
    <>
      <Group title="Business Details"
        sub="How you appear on contracts and filings. If your legal name and the name customers know you by differ, give us both — they are used in different places.">
        <Field label="Legal Business Name" span={8} required>
          <Text value={v.legalName} onChange={x => set('legalName', x)}
            placeholder="As registered with the state" />
        </Field>
        <Field label="EIN / Tax ID" span={4} required>
          <Text value={v.ein} onChange={x => set('ein', x)} placeholder="00-0000000" />
        </Field>
        <Field label="DBA / Brand Name" span={6}
          hint="What customers call you. Used across the site and messaging.">
          <Text value={v.dba} onChange={x => set('dba', x)} />
        </Field>
        <Field label="Year Established" span={3}>
          <Text value={v.founded} onChange={x => set('founded', x)}
            placeholder="YYYY" inputMode="numeric" />
        </Field>
        <Field label="Number of Employees" span={3}>
          <Select value={v.employees} onChange={x => set('employees', x)}
            options={EMPLOYEES} />
        </Field>
      </Group>

      <Group title="Primary Contact"
        sub="The person we work with day to day through the build. They receive implementation updates and are who we call first.">
        <Field label="First Name" span={4} required>
          <Text value={v.contactFirst} onChange={x => set('contactFirst', x)} />
        </Field>
        <Field label="Last Name" span={4} required>
          <Text value={v.contactLast} onChange={x => set('contactLast', x)} />
        </Field>
        <Field label="Title" span={4}>
          <Text value={v.contactTitle} onChange={x => set('contactTitle', x)} />
        </Field>
        <Field label="Email" span={6} required>
          <Text type="email" value={v.contactEmail}
            onChange={x => set('contactEmail', x)} />
        </Field>
        <Field label="Mobile Phone" span={3} required>
          <Text type="tel" value={v.contactMobile}
            onChange={x => set('contactMobile', x)} />
        </Field>
        <Field label="Business Phone" span={3}>
          <Text type="tel" value={v.contactBusiness}
            onChange={x => set('contactBusiness', x)} />
        </Field>
      </Group>

      <Group title="Business Address">
        <Field label="Street Address" span={12} required>
          <Text value={v.address} onChange={x => set('address', x)} />
        </Field>
        <Field label="City" span={6} required>
          <Text value={v.city} onChange={x => set('city', x)} />
        </Field>
        <Field label="State" span={3} required>
          <Text value={v.state} onChange={x => set('state', x)} maxLength={2} />
        </Field>
        <Field label="ZIP" span={3} required>
          <Text value={v.zip} onChange={x => set('zip', x)} inputMode="numeric" />
        </Field>
        <Field label="Current Website" span={12}
          hint="We build alongside this and cut over on your go-live date. It stays up until then.">
          <Text type="url" value={v.website} onChange={x => set('website', x)}
            placeholder="https://" />
        </Field>
      </Group>

      <Group title="Service Territory"
        sub="Where you can actually sell. This drives what your site is allowed to show a visitor.">
        <Field label="Primary Service Territory" span={6} required>
          <Text value={v.territory} onChange={x => set('territory', x)} />
        </Field>
        <Field label="States Served" span={6} required>
          <Text value={v.statesServed} onChange={x => set('statesServed', x)}
            placeholder="TX, OK…" />
        </Field>
        <Field label="Markets or service areas" span={8}
          hint="The regions, territories or distribution areas you operate in.">
          <Text value={v.markets} onChange={x => set('markets', x)} />
        </Field>
        <Field label="Target Go-Live Date" span={4} required>
          <Text type="date" value={v.goLive} onChange={x => set('goLive', x)} />
        </Field>
      </Group>

      <Collapse title="Additional Company Information"
        meta="Certifications, filings, anything unusual">
        <Fields>
          <Field label="Anything else about the business we should know" span={12}
            hint="Certificate or licence numbers, regulator registrations, franchise or affiliate arrangements, seasonal patterns.">
            <Area rows={4} value={v.additionalInfo}
              onChange={x => set('additionalInfo', x)}
              placeholder="Optional" />
          </Field>
        </Fields>
      </Collapse>

      <Collapse title="Notes" meta="For your implementation team">
        <Fields>
          <Field label="Notes for the implementation team" span={12}>
            <Area rows={3} value={v.notes} onChange={x => set('notes', x)}
              placeholder="Questions, constraints, dates to avoid…" />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
