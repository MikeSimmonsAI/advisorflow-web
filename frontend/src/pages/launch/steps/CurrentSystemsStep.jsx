/**
 * Step 5 — Team & Current Systems.
 *
 * What the customer runs on today. The purpose is not an inventory — it is
 * finding the thing that will break at 8am on go-live day because nobody
 * mentioned it. "None today" is a valid and useful answer to most of these,
 * so the placeholders say so.
 */
import { Group, Field, Text, Area, Note, Upload, Uploads, Collapse, Fields }
  from '../LaunchUI'

export default function CurrentSystemsStep({ v, set }) {
  return (
    <>
      <Note tone="warn" icon="info" title="'We don't have one' is a real answer">
        Most companies your size are running on two or three tools and a shared
        inbox. Telling us that plainly is far more useful than naming something
        you barely use — we build to what actually happens.
      </Note>

      <Group title="Ownership"
        sub="Who holds the keys today, and where customer enquiries currently land.">
        <Field label="Main system administrator" span={6} required
          hint="The person who can add users and reset passwords today.">
          <Text value={v.sysAdmin} onChange={x => set('sysAdmin', x)}
            placeholder="Name, role and email" />
        </Field>
        <Field label="Who receives customer / lead activity today" span={6} required>
          <Text value={v.leadRecipient} onChange={x => set('leadRecipient', x)}
            placeholder="A person, a shared inbox, a phone…" />
        </Field>
      </Group>

      <Group title="Current Stack"
        sub="The tools we either connect to or replace. Either way we need to know they are there.">
        <Field label="Email Provider" span={6}>
          <Text value={v.emailProvider} onChange={x => set('emailProvider', x)}
            placeholder="Google Workspace, Microsoft 365…" />
        </Field>
        <Field label="Phone / SMS Provider" span={6}>
          <Text value={v.smsProvider} onChange={x => set('smsProvider', x)}
            placeholder="RingCentral, Twilio, none today…" />
        </Field>
        <Field label="Calendar Provider" span={6}>
          <Text value={v.calendarProvider}
            onChange={x => set('calendarProvider', x)}
            placeholder="Google Calendar, Outlook…" />
        </Field>
        <Field label="CRM / Lead System" span={6}>
          <Text value={v.crm} onChange={x => set('crm', x)}
            placeholder="Salesforce, HubSpot, spreadsheets…" />
        </Field>
        <Field label="Forms / Lead Capture" span={6}>
          <Text value={v.forms} onChange={x => set('forms', x)}
            placeholder="Site contact form, Typeform, paper…" />
        </Field>
        <Field label="Other Critical Software" span={6}
          hint="Anything the business stops without.">
          <Text value={v.otherSoftware} onChange={x => set('otherSoftware', x)}
            placeholder="Billing, accounting, dispatch…" />
        </Field>
      </Group>

      <Group title="Initial Users"
        sub="Who gets an account on day one, and what each of them should be able to do. Send a list rather than typing it here if that is easier.">
        <Field label="Initial user list and roles" span={12}>
          <Area rows={5} value={v.userList} onChange={x => set('userList', x)}
            placeholder={'Dana Whitfield — dwhitfield@atlantislp.com — Admin\nRay Okonkwo — ray@atlantislp.com — Admin\nCustomer Care team (4) — agent access'} />
        </Field>
        <Field span={12} label="Or attach a list">
          <Uploads>
            <Upload title="User list" note="CSV or spreadsheet — name, email, role" />
          </Uploads>
        </Field>
      </Group>

      <Collapse title="Migration Notes" meta="Data to bring across, contracts to time around">
        <Fields>
          <Field label="Anything that has to move, or has to wait" span={12}>
            <Area rows={3} value={v.migrationNotes}
              onChange={x => set('migrationNotes', x)}
              placeholder="Existing customer records to import, a contract that renews in January, a system we cannot turn off until Q1…" />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
