/**
 * SETTINGS & SECURITY › Overview — one page that says what each settings and
 * security tool is for, with the live security, background-job and connection
 * status from /god/platform-health. Every tool keeps its own page and URL; this
 * page only explains them and links to them.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Page, Card, HealthRow, Loading, ErrorState, ActionLink } from './ui/GodKit'

const TOOLS = [
  { group: 'Security', items: [
    { label: 'Audit log', to: '/god/audit', what: 'Every sensitive action, who took it and when.' },
    { label: 'Access check', to: '/god/diagnostics/user-access', what: 'Pick a person and see exactly what they can open.' },
    { label: 'Executive access', to: '/god/executive-access', what: 'Which customers each executive oversees.' },
    { label: 'People & access', to: '/god/access', what: 'Change a person’s brands, workspaces, demo and training.' },
  ] },
  { group: 'Operations', items: [
    { label: 'Background jobs', to: '/god/diagnostics/job-runs', what: 'Run history for the scheduled automations, including the AI worker.' },
    { label: 'Maintenance', to: '/god/maintenance', what: 'Dry-run cleanups and audits. Nothing is sent to customers.' },
    { label: 'Support', to: '/god/support', what: 'Every brand’s support queue, incidents and the daily brief.' },
  ] },
  { group: 'Connection checks (read-only)', items: [
    { label: 'Texting check', to: '/god/diagnostics/twilio', what: 'Whether delivery receipts are set up and arriving. Changes nothing.' },
    { label: 'Email check', to: '/god/diagnostics/email', what: 'Reply mailboxes, inbound replies and sender status.' },
    { label: 'Voice', to: '/god/voice', what: 'Voice agent mappings and attempt rules.' },
    { label: 'Lead rules check', to: '/god/diagnostics/qualification', what: 'Who may be contacted on each channel, and why not.' },
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
    <Page eyebrow="Settings & Security" title="Overview"
      sub="What each tool is for, and how security, background jobs and connections look right now.">
      <Card title="Right now" note={health?.as_of ? 'Checked by the server just now' : null}>
        {err ? <ErrorState onRetry={load}>Status could not be loaded: {err}</ErrorState>
          : !health ? <Loading />
            : <ul className="gk-list">{rows.map(r => (
              <HealthRow key={r.key} label={r.label} status={r.status} statusLabel={r.severity_label}
                headline={r.headline} to={r.to} />))}</ul>}
      </Card>
      <div className="gk-three" style={{ marginTop: 14 }}>
        {TOOLS.map(g => (
          <Card key={g.group} title={g.group}>
            <ul className="gk-list">
              {g.items.map(t => (
                <li key={t.to} className="gk-row">
                  <div className="gk-row-main">
                    <div className="gk-row-title">{t.label}</div>
                    <div className="gk-row-sub">{t.what}</div>
                  </div>
                  <ActionLink to={t.to}>Open</ActionLink>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </Page>
  )
}
