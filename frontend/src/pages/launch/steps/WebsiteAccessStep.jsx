/**
 * Step 3 — Website & Hosting Access.
 *
 * ===========================================================================
 * CREDENTIALS: WHAT STAGE 1 DOES AND DOES NOT DO
 * ===========================================================================
 *
 * For the first few customers we are deliberately collecting hosting and
 * registrar credentials rather than walking each one through delegated access.
 * That decision is Mike's and it is above this file.
 *
 * WHAT THIS FILE DOES: renders the fields, marks them as credentials, and
 * tells the customer plainly what will happen to them.
 *
 * WHAT THIS FILE DOES NOT DO, AND MUST NOT UNTIL STAGE 2:
 *   - persist anything, anywhere
 *   - send anything to the API
 *   - write to localStorage or sessionStorage
 *   - carry a seeded credential in mock data (MOCK_ANSWERS keeps these empty
 *     and must keep keeping them empty)
 *
 * The values live in the page's React state for the life of the tab and are
 * gone on refresh. Encryption at rest, transport, and who may ever read one
 * back are Stage 2 decisions and none of them are prejudged here.
 */
import { Group, Field, Text, Note, Collapse, Area, Fields } from '../LaunchUI'

export default function WebsiteAccessStep({ v, set, customer }) {
  // Prefer what they typed on step 1; fall back to nothing rather than to a
  // guess. An invented hostname in a subtitle reads as a fact we already know.
  const siteHost = (() => {
    const raw = (v.website || '').trim()
    if (!raw) return null
    try { return new URL(raw.includes('://') ? raw : 'https://' + raw).hostname }
    catch { return raw }
  })()

  return (
    <>
      <Note tone="secure" icon="shield" title="Credentials will be securely encrypted when submitted">
        We need this access to build alongside your live site and to cut the
        domain over on launch day without downtime. Access is used by your
        named implementation team only, and you can change every password the
        moment we go live — we will remind you to.
      </Note>

      {/* The customer's own site, from their answers — not a literal domain.
          This named one specific customer's hostname, which is wrong on every
          other customer's screen. */}
      <Group title="Web Hosting"
        sub={'Where the current ' + (siteHost || 'website') + ' is served from.'}>
        <Field label="Current Web Hosting Provider" span={6} required>
          <Text value={v.hostProvider} onChange={x => set('hostProvider', x)}
            placeholder="SiteGround, GoDaddy, WP Engine…" />
        </Field>
        <Field label="Current Website Platform / CMS" span={6}>
          <Text value={v.cms} onChange={x => set('cms', x)}
            placeholder="WordPress, Wix, Squarespace, custom…" />
        </Field>
        <Field label="Hosting Login URL" span={12}>
          <Text type="url" value={v.hostLoginUrl}
            onChange={x => set('hostLoginUrl', x)} placeholder="https://" />
        </Field>
        <Field label="Hosting Account Username / Email" span={6}
          hint="Credential — see the notice above.">
          <Text value={v.hostUser} onChange={x => set('hostUser', x)}
            autoComplete="off" />
        </Field>
        <Field label="Hosting Password" span={6}
          hint="Credential — see the notice above.">
          <Text type="password" value={v.hostPass}
            onChange={x => set('hostPass', x)} autoComplete="new-password" />
        </Field>
      </Group>

      <Group title="Domain & DNS"
        sub="Where the domain itself is registered. This is often a different company from your host, and it is the one that matters on cutover day.">
        <Field label="Domain Registrar" span={6} required>
          <Text value={v.registrar} onChange={x => set('registrar', x)}
            placeholder="GoDaddy, Namecheap, Network Solutions…" />
        </Field>
        <Field label="DNS / Registrar Login URL" span={6}>
          <Text type="url" value={v.dnsUrl} onChange={x => set('dnsUrl', x)}
            placeholder="https://" />
        </Field>
        <Field label="Domain Username / Email" span={6}
          hint="Credential — see the notice above.">
          <Text value={v.registrarUser}
            onChange={x => set('registrarUser', x)} autoComplete="off" />
        </Field>
        <Field label="Domain Password" span={6}
          hint="Credential — see the notice above.">
          <Text type="password" value={v.registrarPass}
            onChange={x => set('registrarPass', x)} autoComplete="new-password" />
        </Field>
      </Group>

      <Group title="Technical Contact"
        sub="Whoever currently maintains the site — in-house, an agency, or the nephew who set it up in 2019. We will copy them rather than surprise them.">
        <Field label="Technical Contact" span={12}
          hint="Name, company and email. Leave blank if that is you.">
          <Text value={v.techContact} onChange={x => set('techContact', x)} />
        </Field>
      </Group>

      <Collapse title="Access Notes"
        meta="MFA, IP restrictions, anything that will trip us up">
        <Fields>
          <Field label="Anything we should know before we log in?" span={12}>
            <Area rows={3} value={v.accessNotes}
              onChange={x => set('accessNotes', x)}
              placeholder="Two-factor on a phone that belongs to someone else, an account that locks after failed attempts, a maintenance window…" />
          </Field>
        </Fields>
      </Collapse>
    </>
  )
}
