/**
 * Step 2 — Branding & Assets.
 *
 * STAGE 1: every tile here is VISUAL ONLY. Clicking one toggles its received
 * state so the screen can be reviewed with items in both conditions; no file
 * dialog opens and no storage exists behind it.
 */
import { Group, Field, Text, Area, Note, Upload, Uploads, Collapse, Fields }
  from '../LaunchUI'

const SWATCHES = [
  { name: 'Primary',   hex: '#0b3f6e' },
  { name: 'Secondary', hex: '#1d9bd1' },
  { name: 'Accent',    hex: '#f0a92b' },
  { name: 'Dark',      hex: '#12202e' },
]

export default function BrandingAssetsStep({ v, set }) {
  return (
    <>
      <Note tone="info" icon="info" title="Send what you have">
        Missing pieces are not a blocker — we will design around a gap and show
        you options. What we cannot do is guess at something you already own,
        so anything that exists is worth attaching even if it is old.
      </Note>

      <Group title="Logo & Vector Files"
        sub="A vector logo is what lets your mark stay sharp on a phone, a billboard and a favicon from one file. If you only have a PNG, send that.">
        <Field span={12} label="">
          <Uploads>
            <Upload title="Primary logo" note="PNG or JPG, highest resolution you have" />
            <Upload title="Vector logo" note="SVG, AI or EPS — preferred" />
            <Upload title="Logo on dark background" note="Reversed / white version" />
            <Upload title="Icon or favicon mark" note="Square, no wordmark" />
          </Uploads>
        </Field>
      </Group>

      <Group title="Brand Colors"
        sub="If you have exact values, give them to us. If not, we will pull them from your logo and confirm before anything is built.">
        <Field span={12} label="Current palette (from your existing site)">
          <div className="lp-swatches">
            {SWATCHES.map(s => (
              <div className="lp-swatch" key={s.name}>
                <i style={{ background: s.hex }} />
                <span className="lp-sw">
                  <b>{s.name}</b>
                  <span>{s.hex.toUpperCase()}</span>
                </span>
              </div>
            ))}
          </div>
          {/* This claimed the colours were "detected from" one specific
              customer's domain. Nothing detects them — they are placeholders —
              and asserting a detection that did not happen, against a hostname
              belonging to somebody else, is two wrong things in one line. */}
          <p className="lp-hint">
            Common starting points — correct them or add your own below.
          </p>
        </Field>
        <Field label="Corrections or additional colors" span={12} optional>
          <Text value={v.brandColors} onChange={x => set('brandColors', x)}
            placeholder="#0B3F6E, #F0A92B…" />
        </Field>
      </Group>

      <Group title="Typography"
        sub="Fonts you are licensed to use. If you are not sure, say so — we will pair something close that is licensed for the web.">
        <Field label="Heading font" span={6} optional>
          <Text value={v.fontHeading} onChange={x => set('fontHeading', x)}
            placeholder="e.g. Poppins" />
        </Field>
        <Field label="Body font" span={6} optional>
          <Text value={v.fontBody} onChange={x => set('fontBody', x)}
            placeholder="e.g. Source Sans" />
        </Field>
      </Group>

      <Group title="Photography, Proof & Collateral"
        sub="The material that makes the site yours rather than a template. Real photographs of real people outperform stock every time.">
        <Field span={12} label="">
          <Uploads>
            <Upload title="Team & facility photos" note="Originals, not web-sized" />
            <Upload title="Brochures & print collateral" note="PDF or InDesign" />
            <Upload title="Customer testimonials" note="With permission to publish" />
            <Upload title="Certifications & badges" note="Licences, accreditations, awards" />
            <Upload title="Existing brand guide" note="If one exists" />
            <Upload title="Anything else" note="Video, ads, social artwork" />
          </Uploads>
        </Field>
      </Group>

      <Collapse title="Brand Notes" meta="Tone, wording, things to avoid">
        <Fields>
          <Field label="How should your brand sound, and what should we avoid?"
            span={12}>
            <Area rows={4} value={v.brandNotes}
              onChange={x => set('brandNotes', x)}
              placeholder="Words you always or never use, claims you cannot make, competitors you do not want to resemble…" />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
