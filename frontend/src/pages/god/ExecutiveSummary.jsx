/**
 * EXECUTIVE SUMMARY — the top band of the God Mode Command Center.
 *
 * Replaces PlatformHealthStrip. Same job, the approved tile treatment, and one
 * rule that has not changed: EVERY NUMBER HERE HAS A NAMED BACKEND SOURCE, and
 * a tile with no source says "no source" rather than showing a plausible figure.
 *
 * ── Sources ────────────────────────────────────────────────────────────────
 *   PLATFORMS      GET /god/stats            total_platforms
 *   ORGANIZATIONS  GET /god/orgs             counted here, pseudo-org excluded
 *   USERS          GET /god/stats            total_users / total_admins
 *   LEADS          GET /god/stats            total_leads / new_leads_30d
 *   MESSAGES 30D   GET /god/orgs             sum of messages_30d
 *   APPOINTMENTS   GET /admin/dashboard/funnel   booked / sold  (god = all orgs)
 *   MRR            no source. There is no invoices table and /billing/all
 *                  returns no amounts. Deliberately blank.
 *   ALERTS         derived from the Owner Action Queue on this same page
 *
 * Tiles are buttons only where there is somewhere real to go.
 */
import { fmt } from './godTheme'
import { NoSource } from './StatusBadge'

const PSEUDO = 'org-god-platform'

function Stat({ label, value, sub, tone = '', onClick, title }) {
  const clickable = typeof onClick === 'function'
  const Tag = clickable ? 'button' : 'div'
  return (
    <Tag
      type={clickable ? 'button' : undefined}
      className={`gm-stat ${tone} ${clickable ? 'gm-click' : ''}`}
      onClick={onClick}
      title={title}
    >
      <span className="gm-k">{label}</span>
      <span className="gm-v">{value}</span>
      {sub ? <span className="gm-s">{sub}</span> : null}
    </Tag>
  )
}

export default function ExecutiveSummary({
  stats, orgs, funnel, criticalCount, loading, onGo,
}) {
  const real       = (orgs || []).filter(o => o.id !== PSEUDO)
  const activeOrgs = real.filter(o => o.is_active).length
  const messages30 = real.reduce((a, o) => a + (o.messages_30d || 0), 0)
  const dash       = (v) => (loading ? '·' : v)

  return (
    <div className="gm-stats">
      <Stat
        label="PLATFORMS"
        value={dash(fmt(stats?.total_platforms))}
        sub="white-label brands"
        onClick={() => onGo('/god/platform')}
        title="GET /god/stats → total_platforms"
      />
      <Stat
        label="ORGANIZATIONS"
        value={dash(fmt(real.length))}
        sub={loading ? '' : `${activeOrgs} active · ${real.length - activeOrgs} suspended`}
        onClick={() => onGo('/god/organizations')}
        title="GET /god/orgs — the platform's own pseudo-organization is excluded"
      />
      <Stat
        label="USERS"
        value={dash(fmt(stats?.total_users))}
        sub={loading ? '' : `${fmt(stats?.total_admins)} with admin authority`}
        onClick={() => onGo('/god/users-all')}
        title="GET /god/stats → total_users / total_admins"
      />
      <Stat
        label="LEADS"
        value={dash(fmt(stats?.total_leads))}
        sub={loading ? '' : `+${fmt(stats?.new_leads_30d)} in the last 30 days`}
        onClick={() => onGo('/god/organizations')}
        title="GET /god/stats → total_leads / new_leads_30d"
      />
      <Stat
        label="MESSAGES · 30D"
        value={dash(fmt(messages30))}
        sub={loading ? '' : `across ${real.filter(o => (o.messages_30d || 0) > 0).length} sending orgs`}
        title="Sum of messages_30d across every organization (GET /god/orgs)"
      />
      <Stat
        label="APPOINTMENTS"
        value={funnel ? dash(fmt(funnel.booked)) : <NoSource />}
        sub={funnel ? `${fmt(funnel.sold)} recorded as sold` : 'funnel API unreachable'}
        onClick={() => onGo('/god/organizations')}
        title="GET /admin/dashboard/funnel → booked / sold, across every organization"
      />
      <Stat
        label="MRR"
        tone="gm-warn"
        value={<NoSource />}
        sub="no invoices table — see Revenue & Accounts"
        title="There is no monetary source. /billing/all returns no amounts, and no invoice or payment table exists."
      />
      <Stat
        label="OWNER ACTIONS"
        tone={criticalCount ? 'gm-crit' : ''}
        value={dash(fmt(criticalCount))}
        sub={criticalCount ? 'need a decision from you' : 'nothing critical open'}
        onClick={() => onGo('#owner-action-queue')}
        title="Derived from the Owner Action Queue below — real conditions only"
      />
    </div>
  )
}
