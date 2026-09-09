/**
 * Step 7 — Files & Documents.
 *
 * The material the build cannot start without, grouped by why we need it
 * rather than by file type. Compliance language is last and separate because
 * it is the group most likely to need somebody else in the company.
 *
 * Uploads are real: each tile stores its file against this customer's
 * implementation, org-isolated, and reports what is actually held.
 */
import { Group, Field, Note, Upload, Uploads, Area, Collapse, Fields }
  from '../LaunchUI'

export default function FilesDocumentsStep({ v, set, files, onUpload, onRemoveFile }) {
  const up = { files, onUpload, onRemove: onRemoveFile }
  return (
    <>
      <Note tone="info" icon="folder" title="Two items are still outstanding">
        You can submit your intake before every file arrives — we will chase
        the rest. The two that genuinely hold up the build are your sample
        customer data and your SMS/email consent language.
      </Note>

      <Group title="Brand & Website"
        sub="Duplicates from Branding & Assets are fine — anything here is enough.">
        <Field span={12} label="">
          <Uploads>
            <Upload {...up} title="Logo & branding assets" note="Logos, colors, fonts" />
            <Upload {...up} title="Website materials" note="Copy, page list, sitemap" />
            <Upload {...up} title="Existing customer communication templates"
              note="The emails and letters you send today" />
          </Uploads>
        </Field>
      </Group>

      <Group title="Integration & Data"
        sub="What the build and the migration are shaped around.">
        <Field span={12} label="">
          <Uploads>
            <Upload {...up} title="ComparePower documentation" note="API docs, agreements" />
            <Upload {...up} title="Initial user list" note="Name, email, role" />
            <Upload {...up} title="Sample customer data" note="A handful of records, any format"
              tag="Required" />
            <Upload {...up} title="Sample lead data" note="Recent enquiries as they arrive today" />
            <Upload {...up} title="Sample electricity bill" note="One redacted example" />
          </Uploads>
        </Field>
      </Group>

      <Group title="Compliance & Disclosure"
        sub="What we are legally required to show, and what you are permitted to send. If these live with a lawyer or a compliance officer, loop them in now rather than at launch.">
        <Field span={12} label="">
          <Uploads>
            <Upload {...up} title="Privacy policy" note="Current published version" />
            <Upload {...up} title="Terms & disclosures" note="Including any PUC-required language" />
            <Upload {...up} title="SMS / email consent language" note="How you capture opt-in today"
              tag="Required" />
          </Uploads>
        </Field>
      </Group>

      <Collapse title="File Notes" meta="Anything redacted, partial or on its way">
        <Fields>
          <Field label="Notes on what you have sent" span={12}>
            <Area rows={3} value={v.fileNotes}
              onChange={x => set('fileNotes', x)}
              placeholder="Account numbers redacted from the bill sample; consent wording is being reviewed by counsel and lands next week…" />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
