/**
 * EXECUTIVE · COMMAND — the portfolio, and what moved in it.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * SCOPE IS A GRANT, AND THE PHONE NEVER WIDENS IT
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Everything here comes from `/executive/*`, and every one of those endpoints
 * is narrowed server-side by `executive_authority.portfolio_authority()` — an
 * explicit per-organization assignment, not "every org on my brand". This
 * screen therefore has NO org filter, NO brand switcher and no notion of "all
 * customers": there is nothing for it to widen to. An executive with no
 * assignments sees an empty portfolio, which is the correct answer and is
 * rendered as an answer rather than as an error.
 *
 * NO GOD-LEVEL BLEED. Nothing on this screen reads a `/god/*` route, and the
 * owner's platform figures — total users, total leads, platform health — are
 * absent by construction rather than hidden by a flag.
 *
 * `/executive/command-center` returns exactly: platform_id, platform_name,
 * opportunities, active_customer_orgs, team_headcount, brand_sales_org_count.
 * `/executive/portfolio` adds summary, attention[] and attention_total. Those
 * are the fields read below — no others exist, and guessing at richer ones is
 * what produced a screen of zeros elsewhere in this app.
 */

import React from 'react';
import { router } from 'expo-router';

import { executive } from '../../src/api/endpoints';
import { asList, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { anyError, numField, textField } from '../../src/api/state';
import {
  AttentionItem, Metric, MetricGrid, SectionRetry, Skeleton,
} from '../../src/components/data';
import {
  EmptyState, Row, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { palette } from '../../src/theme/tokens';
import { rowKey } from '../../src/keys';

type Rec = Record<string, unknown>;

export default function ExecutiveCommand() {
  const refresh = useScopedRefresh();

  const cc = useScopedQuery(['exec', 'command-center'], () => executive.commandCenter());
  const pf = useScopedQuery(['exec', 'portfolio'], () => executive.portfolio());

  const platformName = textField(cc, 'platform_name');
  const customers = numField(cc, 'active_customer_orgs');
  const opportunities = numField(cc, 'opportunities');
  const team = numField(cc, 'team_headcount');
  const brands = numField(cc, 'brand_sales_org_count');
  const attentionTotal = numField(pf, 'attention_total');

  const attention = asList<Rec>(pick(pf.data, 'attention'), 'items');

  const failed = anyError(customers, opportunities, team, attentionTotal);

  return (
    <Screen refreshing={cc.isFetching || pf.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Command"
        subtitle={platformName.value ?? 'Your portfolio'}
      />

      <SectionRetry
        show={failed}
        onRetry={refresh}
        note="Some portfolio figures could not be loaded. They are not zero."
      />

      {cc.isLoading && pf.isLoading ? <Skeleton rows={3} /> : null}

      <MetricGrid>
        <Metric
          label="Customers"
          field={customers}
          onPress={() => router.push('/(exec)/organizations' as never)}
        />
        <Metric label="Open opportunities" field={opportunities} />
        <Metric
          label="Revenue"
          field={{ state: 'ready', value: 'View' }}
          onPress={() => router.push('/(exec)/revenue' as never)}
          hint="Recurring revenue and movement"
        />
        <Metric label="Sales team" field={team}
                hint={brands.value ? `${brands.value} brand${brands.value === 1 ? '' : 's'}` : undefined} />
      </MetricGrid>

      {/* ── WHAT NEEDS AN EXECUTIVE ───────────────────────────────────────── */}
      <SectionHeader
        title={attention.length ? `Needs attention · ${attention.length}` : 'Needs attention'}
      />

      {pf.isLoading ? <Skeleton rows={2} /> : null}

      {attention.map((a, i) => (
        <AttentionItem
          key={rowKey([pick(a, 'id')], i)}
          title={String(pick(a, 'name', 'organization_name') ?? 'Organization')}
          why={String(pick(a, 'reason', 'health_label', 'detail') ?? '') || null}
          action="Open organization"
          tone={String(pick(a, 'health') ?? '') === 'at_risk' ? 'danger' : 'warning'}
          onPress={() => {
            const id = pick(a, 'id', 'organization_id');
            if (id) router.push(`/org/${String(id)}` as never);
          }}
        />
      ))}

      {!pf.isLoading && !attention.length && !failed ? (
        <EmptyState
          title="Nothing flagged"
          body={customers.value === 0
            ? 'No organizations are assigned to you yet. Whoever manages executive access can assign them.'
            : 'Every organization in your portfolio is reporting normally.'}
        />
      ) : null}

      {/* ── QUICK ACCESS ──────────────────────────────────────────────────── */}
      <SectionHeader title="Quick access" />
      <Row
        title="Organizations"
        subtitle="Performance by customer"
        accent={palette.accentSoft}
        onPress={() => router.push('/(exec)/organizations' as never)}
      />
      <Row
        title="Revenue"
        subtitle="Recurring revenue and commercial movement"
        onPress={() => router.push('/(exec)/revenue' as never)}
      />
    </Screen>
  );
}
