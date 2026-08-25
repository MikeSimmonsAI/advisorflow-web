/**
 * HierarchyTree — AdvisorFlow → Platform → Organization.
 *
 * Data: GET /god/platforms (id,name,slug,domain,org_count,lead_count,user_count)
 *       GET /god/orgs      (_enrich_org record, incl. real health_score)
 *
 * Platforms collapse, so this row shape still works at 20 orgs under EvoSys Pro.
 * Clicking an org row drills into it. Billing shows "no source" until the
 * invoice model exists — never a placeholder figure.
 */
import { useState } from 'react'
import { T, fmt, healthColor, lastActivityLabel } from './godTheme'
import { StatusBadge, orgStateBadge, NoSource, Dot } from './StatusBadge'

const COLS = 'minmax(230px,1.6fr) 92px 74px 84px 84px 82px 96px 118px'
const PLATFORM_DOT = { evosyspro: T.purple, bookaboost: T.blue, harmonyhustle: T.amber }

function Cell({ children, right, style }) {
  return <div style={{
    padding: '9px 10px', whiteSpace: 'nowrap', overflow: 'hidden',
    textOverflow: 'ellipsis', textAlign: right ? 'right' : 'left', ...style,
  }}>{children}</div>
}

export default function HierarchyTree({ platforms, orgs, stats, onOpenOrg, loading }) {
  const [collapsed, setCollapsed] = useState({})
  const toggle = (slug) => setCollapsed(c => ({ ...c, [slug]: !c[slug] }))

  const real = (orgs || []).filter(o => o.id !== 'org-god-platform')
  const byPlatform = {}
  ;(platforms || []).forEach(p => { byPlatform[p.id] = { ...p, orgs: [] } })
  const orphans = []
  real.forEach(o => {
    if (o.platform_id && byPlatform[o.platform_id]) byPlatform[o.platform_id].orgs.push(o)
    else orphans.push(o)
  })
  const groups = Object.values(byPlatform)
    .sort((a, b) => (b.lead_count || 0) - (a.lead_count || 0))

  return (
    <div className="gm-card" style={{ overflow: 'hidden', padding: 0 }}>
      <div className="gm-row gm-thead" style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center' }}>
        {['ACCOUNT', 'PLAN', 'USERS', 'LEADS', 'MSG 30D', 'HEALTH', 'BILLING', 'STATE'].map((h, i) => (
          <Cell key={h} right={i > 0 && i < 6}
            style={{ fontSize: 8, letterSpacing: '.11em', color: '#607a9b', fontWeight: 700 }}>{h}</Cell>
        ))}
      </div>

      {/* Level 0 — AdvisorFlow */}
      <div className="gm-row gm-lvl0" style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center' }}>
        <Cell style={{ color: T.gold, fontWeight: 700, fontSize: 12, letterSpacing: '.04em' }}>
          ⚡ ADVISORFLOW
        </Cell>
        <Cell right style={{ color: T.ghost }}>—</Cell>
        <Cell right>{loading ? '·' : fmt(stats?.total_users)}</Cell>
        <Cell right>{loading ? '·' : fmt(stats?.total_leads)}</Cell>
        <Cell right>{loading ? '·' : fmt(real.reduce((a, o) => a + (o.messages_30d || 0), 0))}</Cell>
        <Cell right style={{ color: T.ghost }}>—</Cell>
        <Cell right><NoSource /></Cell>
        <Cell><StatusBadge tone="gold">GOD</StatusBadge></Cell>
      </div>

      {groups.map(p => {
        const isOpen = !collapsed[p.slug]
        return (
          <div key={p.id}>
            <div className="gm-row gm-lvl1 gm-click"
              style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center' }}
              onClick={() => toggle(p.slug)} role="button" tabIndex={0}
              onKeyDown={e => { if (e.key === 'Enter') toggle(p.slug) }}
            >
              <Cell style={{ paddingLeft: 22, color: '#eaf4ff', fontWeight: 600, fontSize: 11 }}>
                <span style={{ display: 'inline-block', width: 12, color: '#4d668a', fontSize: 9 }}>
                  {isOpen ? '▾' : '▸'}
                </span>
                <span style={{ marginRight: 7 }}><Dot color={PLATFORM_DOT[p.slug] || T.blue} /></span>
                {p.name}
                {p.domain && <span style={{ color: T.ghost, fontSize: 10, marginLeft: 8 }}>· {p.domain}</span>}
              </Cell>
              <Cell right style={{ color: T.ghost }}>—</Cell>
              <Cell right>{fmt(p.user_count)}</Cell>
              <Cell right>{fmt(p.lead_count)}</Cell>
              <Cell right>{fmt(p.orgs.reduce((a, o) => a + (o.messages_30d || 0), 0))}</Cell>
              <Cell right style={{ color: T.ghost }}>—</Cell>
              <Cell right><NoSource /></Cell>
              <Cell>
                <StatusBadge tone={p.orgs.length ? 'ok' : 'off'}>
                  {p.orgs.length} ORG{p.orgs.length === 1 ? '' : 'S'}
                </StatusBadge>
              </Cell>
            </div>

            {isOpen && p.orgs.length === 0 && (
              <div className="gm-row gm-lvl2" style={{ padding: '10px 10px 10px 44px', color: T.ghost, fontSize: 11, fontStyle: 'italic' }}>
                No organizations on this platform yet.
              </div>
            )}

            {isOpen && p.orgs
              .sort((a, b) => (b.lead_count || 0) - (a.lead_count || 0))
              .map(o => (
                <div key={o.id} className="gm-row gm-lvl2 gm-click"
                  style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center' }}
                  onClick={() => onOpenOrg?.(o)} role="button" tabIndex={0}
                  onKeyDown={e => { if (e.key === 'Enter') onOpenOrg?.(o) }}
                  title={`Health ${o.health_score}/100 · last activity ${lastActivityLabel(o.last_activity)}`}
                >
                  <Cell style={{ paddingLeft: 46, color: '#a9bdd0', fontSize: 11 }}>{o.name}</Cell>
                  <Cell right style={{ color: o.plan === 'enterprise' ? T.gold : o.plan === 'trial' ? T.amber : T.text }}>
                    {o.plan || '—'}
                  </Cell>
                  <Cell right>{fmt(o.user_count)}</Cell>
                  <Cell right>{fmt(o.lead_count)}</Cell>
                  <Cell right style={{ color: (o.messages_30d || 0) === 0 && o.lead_count > 0 ? T.amber : T.text }}>
                    {fmt(o.messages_30d)}
                  </Cell>
                  <Cell right style={{ color: healthColor(o.health_score), fontWeight: 600 }}>
                    {o.health_score}
                  </Cell>
                  <Cell right><NoSource>—</NoSource></Cell>
                  <Cell>{orgStateBadge(o)}</Cell>
                </div>
              ))}
          </div>
        )
      })}

      {orphans.length > 0 && (
        <>
          <div className="gm-row gm-lvl1" style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center' }}>
            <Cell style={{ paddingLeft: 22, color: T.amber, fontWeight: 600, fontSize: 11 }}>
              <span style={{ display: 'inline-block', width: 12 }} />
              ⚠ Unassigned to any platform
            </Cell>
            <Cell right style={{ color: T.ghost }}>—</Cell><Cell right>—</Cell><Cell right>—</Cell>
            <Cell right>—</Cell><Cell right>—</Cell><Cell right><NoSource /></Cell>
            <Cell><StatusBadge tone="warn">{orphans.length}</StatusBadge></Cell>
          </div>
          {orphans.map(o => (
            <div key={o.id} className="gm-row gm-lvl2 gm-click"
              style={{ display: 'grid', gridTemplateColumns: COLS, alignItems: 'center' }}
              onClick={() => onOpenOrg?.(o)}>
              <Cell style={{ paddingLeft: 46, color: '#a9bdd0', fontSize: 11 }}>{o.name}</Cell>
              <Cell right>{o.plan || '—'}</Cell><Cell right>{fmt(o.user_count)}</Cell>
              <Cell right>{fmt(o.lead_count)}</Cell><Cell right>{fmt(o.messages_30d)}</Cell>
              <Cell right style={{ color: healthColor(o.health_score) }}>{o.health_score}</Cell>
              <Cell right><NoSource>—</NoSource></Cell><Cell>{orgStateBadge(o)}</Cell>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
