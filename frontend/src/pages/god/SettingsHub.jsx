/**
 * SETTINGS & SECURITY › Overview — one page that says what each settings and
 * security tool is for, with the live security, background-job and connection
 * status from /god/platform-health. Every tool keeps its own page and URL; this
 * page only explains them and links to them.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Page, Card, HealthRow, Loading, ErrorState, Section, ToolCard } from './ui/GodKit'

const TOOLS = [
  { group: 'Security', items: [
    { label: 'Audit log', icon: 'list', to: '/god/audit', what: 'Every sensitive action, who took it and when.' },
    { label: 'Access check', icon: 'user', to: '/god/diagnostics/user-access', what: 'Pick a person and see exactly what they can open.' },
    { label: 'Executive access', icon: 'briefcase', to: '/god/executive-access', what: 'Which customers each executive oversees.' },
    { label: 'People & access', icon: 'user', to: '/god/access', what: 'Change a person’s brands, workspaces, demo and training.' },
  ] },
  { group: 'Operations', items: [
    { label: 'Background jobs', icon: 'settings', to: '/god/diagnostics/job-runs', what: 'Run history for the scheduled automations, including the AI worker.' },
    { label: 'Maintenance', icon: 'settings', to: '/god/maintenance', what: 'Dry-run cleanups and audits. Nothing is sent to customers.' },
    { label: 'Support', icon: 'user', to: '/god/support', what: 'Every brand’s support queue, incidents and the daily brief.' },
  ] },
  { group: 'Connection checks (read-only)', items: [
    { label: 'Texting check', icon: 'check', to: '/god/diagnostics/twilio', what: 'Whether delivery receipts are set up and arriving. Changes nothing.' },
    { label: 'Email check', icon: 'check', to: '/god/diagnostics/email', what: 'Reply mailboxes, inbound replies and sender status.' },
    { label: 'Voice', icon: 'check', to: '/god/voice', what: 'Voice agent mappings and attempt rules.' },
    { label: 'Lead rules check', icon: 'shield', to: '/god/diagnostics/qualification', what: 'Who may be contacted on each channel, and why not.' },
  ] },
]

export default function SettingsHub () {
  const [health, setHealth] = useState(null)
  const [err, setErr] = useState(null)
  const load = () => {
    setErr(null)
    api.get('/god/platform-health', { noOrgContext: true }).then(setHealth).catch(e => setErr(e?.message || 'error'))
  }
  useEffect(load, [])
  const rows = (health?.sections || []).filter(s => ['security', 'jobs', 'integrations'].includes(s.key))
  return (
    <Page eyebrow="Overview" title="Settings & Security"
      sub="Security, background jobs and connections right now — and what each tool is for.">
      <Card title="Status right now" note={health?.as_of ? 'Checked by the server just now' : null}>
        {err ? <ErrorState onRetry={load}>Status could not be loaded: {err}</ErrorState>
          : !health ? <Loading />
            : <ul className="gk-list">{rows.map(r => (
              <HealthRow key={r.key} label={r.label} status={r.status} statusLabel={r.severity_label}
                headline={r.headline} to={r.to} />))}</ul>}
      </Card>
      {TOOLS.map(g => (
        <Section key={g.group} title={g.group}>
          <div className="gk-tiles">
            {g.items.map(t => (
              <ToolCard key={t.to} icon={t.icon} title={t.label} description={t.what} to={t.to} />
            ))}
          </div>
        </Section>
      ))}
    </Page>
  )
}
