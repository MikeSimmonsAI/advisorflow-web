/**
 * ExecutiveOrgObservation — Read-only operational view of a single customer org.
 *
 * Executive Suite → Organizations → View → Executive Observation Console
 *
 * AUTH: Every endpoint is require_brand_executive, platform-isolation enforced
 * on the server. A 403 means the org doesn't belong to this executive's brand.
 *
 * WHAT IS SHOWN: org overview, operating snapshot, team roster, activity timeline.
 * WHAT IS NOT SHOWN: message content, lead PII, credentials, write controls.
 *
 * DESIGN: Light, consistent with the existing Executive Suite palette.
 * Do not import God Mode dark styles.
 */

import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../../api/client'

// ── Data hooks ───────────────────────────────────────────────────────────────

function useFetch(path) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    if (!path) return
    setState({ loading: true, data: null, error: null })
    api.get(path)
      .then(d => setState({ loading: false, data: d, error: null }))
      .catch(e => setState({ loading: false, data: null, error: e?.message || 'Failed to load.' }))
  }, [path])
  return state
}

// ── Utilities ────────────────────────────────────────────────────────────────

const HEALTH_STYLE = {
  healthy:    { bg: '#d1fae5', color: '#065f46', label: 'Healthy' },
  watch:      { bg: '#fef3c7', color: '#92400e', label: 'Watch' },
  at_risk:    { bg: '#fed7aa', color: '#9a3412', label: 'At Risk' },
  inactive:   { bg: '#f3f4f6', color: '#374151', label: 'Inactive' },
  onboarding: { bg: '#dbeafe', color: '#1e40af', label: 'Onboarding' },
}

const EVENT_META = {
  lead_imported:      { icon: '↗', label: 'Lead Imported',       color: '#2563eb' },
  message_sent:       { icon: '→', label: 'Outbound Message',    color: '#059669' },
  reply_received:     { icon: '←', label: 'Inbound Reply',       color: '#7c3aed' },
  appointment_booked: { icon: '✓', label: 'Appointment Booked',  color: '#d97706' },
}

const ROLE_LABEL = {
  advisor:     'Advisor',
  org_admin:   'Admin',
  super_admin: 'Super Admin',
}

function fmtDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return '—' }
}

function fmtDateTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    })
  } catch { return '—' }
}

function daysAgo(iso) {
  if (!iso) return null
  try {
    const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
    if (d === 0) return 'today'
    if (d === 1) return 'yesterday'
    return `${d} days ago`
  } catch { return null }
}

// ── Sub-components ───────────────────────────────────────────────────────────

function HealthBadge({ status }) {
  const s = HEALTH_STYLE[status] || { bg: '#f3f4f6', color: '#374151', label: status || 'Unknown' }
  return (
    <span style={{
      background: s.bg, color: s.color,
      padding: '3px 10px', borderRadius: 20, fontSize: 12, fontWeight: 700,
      letterSpacing: '0.04em', textTransform: 'uppercase',
    }}>
      {s.label}
    </span>
  )
}

function Card({ title, children }) {
  return (
    <div style={styles.card}>
      <h2 style={styles.cardTitle}>{title}</h2>
      {children}
    </div>
  )
}

function StatRow({ label, value, muted }) {
  return (
    <div style={styles.statRow}>
      <span style={styles.statLabel}>{label}</span>
      <span style={muted ? styles.statValueMuted : styles.statValue}>{value ?? '—'}</span>
    </div>
  )
}

function Skeleton() {
  return <div style={styles.skeleton} />
}

// ── Status breakdown pills ────────────────────────────────────────────────────

const STATUS_DISPLAY = {
  new:             { label: 'New',             color: '#2563eb', bg: '#dbeafe' },
  hot:             { label: 'Hot',             color: '#dc2626', bg: '#fee2e2' },
  contacted:       { label: 'Contacted',       color: '#7c3aed', bg: '#ede9fe' },
  booked:          { label: 'Booked',          color: '#059669', bg: '#d1fae5' },
  lost:            { label: 'Lost',            color: '#6b7280', bg: '#f3f4f6' },
  do_not_contact:  { label: 'Do Not Contact',  color: '#9a3412', bg: '#fef3c7' },
}

function StatusPills({ byStatus }) {
  if (!byStatus || Object.keys(byStatus).length === 0) {
    return <p style={styles.noData}>No leads on record.</p>
  }
  const sorted = Object.entries(byStatus).sort((a, b) => b[1] - a[1])
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 4 }}>
      {sorted.map(([status, count]) => {
        const s = STATUS_DISPLAY[status] || { label: status, color: '#374151', bg: '#f3f4f6' }
        return (
          <span key={status} style={{
            background: s.bg, color: s.color,
            padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 600,
          }}>
            {s.label}: {count}
          </span>
        )
      })}
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function ExecutiveOrgObservation() {
  const { orgId } = useParams()

  const detail   = useFetch(orgId ? `/executive/organizations/${orgId}` : null)
  const leads    = useFetch(orgId ? `/executive/organizations/${orgId}/leads/summary` : null)
  const team     = useFetch(orgId ? `/executive/organizations/${orgId}/team` : null)
  const activity = useFetch(orgId ? `/executive/organizations/${orgId}/activity` : null)

  const org = detail.data

  return (
    <div style={styles.page}>

      {/* breadcrumb nav */}
      <div style={styles.navBar}>
        <Link to="/executive/organizations" style={styles.backLink}>← Organizations</Link>
        <Link to="/executive/command-center" style={styles.navLink}>Executive Command Center</Link>
      </div>

      {/* page header */}
      <div style={styles.header}>
        {detail.loading ? (
          <Skeleton />
        ) : detail.error ? (
          <p style={styles.errorText}>{detail.error}</p>
        ) : org ? (
          <>
            <p style={styles.platformLabel}>{org.platform_name}</p>
            <h1 style={styles.orgName}>{org.name}</h1>
            <p style={styles.observationLabel}>EXECUTIVE OBSERVATION</p>
            <p style={styles.observationSub}>Read-only operational visibility</p>
          </>
        ) : null}
      </div>

      {/* ── 1. ORGANIZATION OVERVIEW ───────────────────────────────────────── */}
      <Card title="Organization Overview">
        {detail.loading ? <Skeleton /> : detail.error ? (
          <p style={styles.errorText}>{detail.error}</p>
        ) : org ? (
          <>
            <StatRow label="Status" value={
              org.is_active
                ? <span style={styles.activeChip}>Active</span>
                : <span style={styles.suspendedChip}>Suspended</span>
            } />
            <StatRow label="Provisioned" value={fmtDate(org.provisioned_at)} />
            <StatRow label="Organization Age" value={
              org.organization_age_days != null
                ? `${org.organization_age_days} day${org.organization_age_days !== 1 ? 's' : ''}`
                : '—'
            } />
            <div style={styles.statRow}>
              <span style={styles.statLabel}>Health Classification</span>
              <span><HealthBadge status={org.health} /></span>
            </div>
            <StatRow label="Last Operational Activity" value={
              org.last_operational_activity
                ? `${fmtDate(org.last_operational_activity)} (${daysAgo(org.last_operational_activity)})`
                : 'No activity recorded'
            } muted={!org.last_operational_activity} />
            {org.health_reason && (
              <p style={styles.healthReason}>{org.health_reason}</p>
            )}
          </>
        ) : null}
      </Card>

      {/* ── 2. OPERATING SNAPSHOT ─────────────────────────────────────────── */}
      <Card title="Operating Snapshot">
        <div style={styles.snapshotGrid}>
          <div style={styles.snapshotSection}>
            <p style={styles.snapshotSectionLabel}>Leads</p>
            {leads.loading ? <Skeleton /> : leads.error ? (
              <p style={styles.errorText}>{leads.error}</p>
            ) : leads.data ? (
              <>
                <p style={styles.bigNumber}>{leads.data.total_leads ?? '—'}</p>
                <p style={styles.bigNumberSub}>Total leads</p>
                <StatRow label="Added (last 30 days)" value={leads.data.leads_last_30_days ?? '—'} />
                <div style={{ marginTop: 12 }}>
                  <p style={styles.snapshotSectionLabel}>By Status</p>
                  <StatusPills byStatus={leads.data.by_status} />
                </div>
              </>
            ) : null}
          </div>
          <div style={styles.snapshotSection}>
            <p style={styles.snapshotSectionLabel}>Activity</p>
            {detail.loading ? <Skeleton /> : org ? (
              <>
                <StatRow label="Last Outbound Message" value={
                  org.last_outbound_message
                    ? `${fmtDate(org.last_outbound_message)} (${daysAgo(org.last_outbound_message)})`
                    : 'None recorded'
                } muted={!org.last_outbound_message} />
                <StatRow label="Last Inbound Reply" value={
                  org.last_inbound_reply
                    ? `${fmtDate(org.last_inbound_reply)} (${daysAgo(org.last_inbound_reply)})`
                    : 'None recorded'
                } muted={!org.last_inbound_reply} />
                <StatRow label="Last Appointment Booked" value={
                  org.last_booking
                    ? `${fmtDate(org.last_booking)} (${daysAgo(org.last_booking)})`
                    : 'None recorded'
                } muted={!org.last_booking} />
                <StatRow label="Active Users" value={org.active_users ?? '—'} />
              </>
            ) : null}
          </div>
        </div>
      </Card>

      {/* ── 3. TEAM ───────────────────────────────────────────────────────── */}
      <Card title="Team">
        {team.loading ? <Skeleton /> : team.error ? (
          <p style={styles.errorText}>{team.error}</p>
        ) : team.data ? (
          <>
            <div style={styles.teamSummary}>
              <div style={styles.teamStat}>
                <span style={styles.teamStatNum}>{team.data.total_users}</span>
                <span style={styles.teamStatLabel}>Total Users</span>
              </div>
              <div style={styles.teamStat}>
                <span style={styles.teamStatNum}>{team.data.active_users}</span>
                <span style={styles.teamStatLabel}>Active</span>
              </div>
              <div style={styles.teamStat}>
                <span style={styles.teamStatNum}>{team.data.advisor_count}</span>
                <span style={styles.teamStatLabel}>Advisors</span>
              </div>
            </div>
            {team.data.members && team.data.members.length > 0 ? (
              <div style={styles.memberTable}>
                <div style={styles.memberHeader}>
                  <span style={{ flex: 2 }}>Name</span>
                  <span style={{ flex: 3 }}>Email</span>
                  <span style={{ flex: 1 }}>Role</span>
                  <span style={{ flex: 1, textAlign: 'right' }}>Status</span>
                </div>
                {team.data.members.map((m, i) => (
                  <div key={i} style={styles.memberRow}>
                    <span style={{ flex: 2, fontWeight: 600 }}>{m.name}</span>
                    <span style={{ flex: 3, color: '#6b7280', fontSize: 13 }}>{m.email}</span>
                    <span style={{ flex: 1, color: '#374151', fontSize: 13 }}>
                      {ROLE_LABEL[m.role] || m.role}
                    </span>
                    <span style={{ flex: 1, textAlign: 'right' }}>
                      {m.is_active
                        ? <span style={styles.activeSmall}>Active</span>
                        : <span style={styles.inactiveSmall}>Inactive</span>}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p style={styles.noData}>No users found in this organization.</p>
            )}
          </>
        ) : null}
      </Card>

      {/* ── 4. RECENT ACTIVITY ────────────────────────────────────────────── */}
      <Card title="Recent Activity">
        {activity.loading ? <Skeleton /> : activity.error ? (
          <p style={styles.errorText}>{activity.error}</p>
        ) : activity.data ? (
          activity.data.events && activity.data.events.length > 0 ? (
            <div style={styles.timeline}>
              {activity.data.events.map((e, i) => {
                const meta = EVENT_META[e.type] || { icon: '·', label: e.type, color: '#6b7280' }
                return (
                  <div key={i} style={styles.timelineItem}>
                    <span style={{ ...styles.timelineIcon, color: meta.color }}>{meta.icon}</span>
                    <div style={styles.timelineBody}>
                      <span style={styles.timelineLabel}>{meta.label}</span>
                      <span style={styles.timelineTs}>{fmtDateTime(e.timestamp)}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <p style={styles.noData}>No operational activity recorded.</p>
          )
        ) : null}
      </Card>

    </div>
  )
}

const styles = {
  page: { padding: '40px', maxWidth: 960 },
  backRow: { display: 'flex', alignItems: 'center', gap: 20, marginBottom: 28 },
  backLink: { color: '#2563eb', fontSize: 13, fontWeight: 600, textDecoration: 'none' },
  navLink: { color: '#6b7280', fontSize: 13, textDecoration: 'none' },
  header: { marginBottom: 32 },
  eyebrow: { fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', color: '#2563eb',
             textTransform: 'uppercase', marginBottom: 4 },
  orgName: { fontSize: 28, fontWeight: 800, color: '#1a1f36', margin: '0 0 4px' },
  subLine: { fontSize: 14, color: '#6b7280', margin: 0 },
  card: { background: '#fff', border: '1px solid #e8ecf4', borderRadius: 12,
          padding: '24px 28px', marginBottom: 20 },
  cardTitle: { fontSize: 13, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase',
               letterSpacing: '0.07em', marginBottom: 16 },
  statsGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 16 },
  statBox: { background: '#f9fafb', borderRadius: 8, padding: '14px 16px' },
  statLabel: { fontSize: 11, color: '#9ca3af', fontWeight: 600, textTransform: 'uppercase',
               letterSpacing: '0.06em', marginBottom: 4 },
  statValue: { fontSize: 22, fontWeight: 800, color: '#1a1f36' },
  statSub: { fontSize: 12, color: '#6b7280', marginTop: 2 },
  healthRow: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 },
  healthReason: { fontSize: 13, color: '#6b7280', fontStyle: 'italic', marginTop: 4 },
  badge: { display: 'inline-block', padding: '3px 10px', borderRadius: 20,
           fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' },
  leadsGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 12, marginBottom: 16 },
  leadsStatBox: { background: '#f9fafb', borderRadius: 8, padding: '12px 14px', textAlign: 'center' },
  leadsStatVal: { fontSize: 20, fontWeight: 800, color: '#1a1f36' },
  leadsStatLbl: { fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: 2 },
  statusRow: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  pill: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 12px',
          background: '#f3f4f6', borderRadius: 20, fontSize: 12, color: '#374151' },
  pillCount: { fontWeight: 700, color: '#1a1f36' },
  memberHeader: { display: 'flex', padding: '0 0 8px', borderBottom: '1px solid #e8ecf4',
                  marginBottom: 4, fontSize: 11, fontWeight: 700, color: '#9ca3af',
                  textTransform: 'uppercase', letterSpacing: '0.06em' },
  memberRow: { display: 'flex', alignItems: 'center', padding: '10px 0',
               borderBottom: '1px solid #f5f6fa', fontSize: 14 },
  teamStats: { display: 'flex', gap: 24, marginBottom: 20 },
  teamStat: { textAlign: 'center' },
  teamStatVal: { fontSize: 20, fontWeight: 800, color: '#1a1f36' },
  teamStatLbl: { fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em' },
  activeSmall: { background: '#d1fae5', color: '#065f46', fontSize: 11, fontWeight: 700,
                 padding: '2px 8px', borderRadius: 10, textTransform: 'uppercase' },
  inactiveSmall: { background: '#f3f4f6', color: '#6b7280', fontSize: 11, fontWeight: 700,
                   padding: '2px 8px', borderRadius: 10, textTransform: 'uppercase' },
  timeline: { display: 'flex', flexDirection: 'column', gap: 2 },
  timelineItem: { display: 'flex', alignItems: 'center', gap: 14, padding: '10px 0',
                  borderBottom: '1px solid #f5f6fa' },
  timelineIcon: { fontSize: 18, width: 28, textAlign: 'center', flexShrink: 0 },
  timelineBody: { display: 'flex', flex: 1, justifyContent: 'space-between', alignItems: 'center' },
  timelineLabel: { fontSize: 13, color: '#374151', fontWeight: 500 },
  timelineTs: { fontSize: 12, color: '#9ca3af' },
  noData: { color: '#9ca3af', fontSize: 14, margin: 0 },
  errorText: { color: '#ef4444', fontSize: 14, margin: 0 },
  skeleton: { height: 60, background: 'linear-gradient(90deg,#f3f4f6 25%,#e5e7eb 50%,#f3f4f6 75%)',
              backgroundSize: '200% 100%', borderRadius: 8,
              animation: 'shimmer 1.4s infinite linear' },
}
