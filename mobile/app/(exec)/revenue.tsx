/**
 * EXECUTIVE · REVENUE — current state, and an honest note about history.
 *
 * THERE IS NO TREND LINE ON THIS SCREEN, AND THAT IS THE POINT (Phase 0,
 * GAP-5). Every "snapshot" in this codebase is a value copied onto a record —
 * pricing on a proposal, the plan on a compensation entry. There is no time
 * series anywhere, so a chart drawn here would be a line through ONE point,
 * dressed up as a trend. Executives make decisions from trends; a fabricated
 * one is worse than none.
 *
 * So the screen shows what is true right now, says plainly that history is not
 * recorded yet, and names what would have to exist for a trend to be real.
 * When a revenue snapshot table lands, this screen gains a chart and loses the
 * note.
 *
 * Everything shown is portfolio-scoped by the server.
 */

import React from 'react';
import { StyleSheet, Text } from 'react-native';

import { executive } from '../../src/api/endpoints';
import {
  asCount, asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Row, Screen, ScreenTitle,
  SectionHeader, SeverityPill,
} from '../../src/components/ui';
import { palette, type as typography } from '../../src/theme/tokens';
import type { ExecutiveOrg } from '../../src/api/types';
import { rowKey } from '../../src/keys';

export default function ExecutiveRevenue() {
  const refresh = useScopedRefresh();
  const portfolio = useScopedQuery(['executive', 'portfolio'], () => executive.portfolio());
  const customers = useScopedQuery(['executive', 'customer-health'],
    () => executive.customerHealth());

  if (portfolio.isLoading) return <Screen><Loading label="Loading revenue" /></Screen>;
  if (portfolio.isError) {
    return <Screen><ErrorState error={portfolio.error} onRetry={refresh} /></Screen>;
  }

  const p = portfolio.data ?? {};
  const exceptions = asList<ExecutiveOrg>(
    pick(customers.data, 'at_risk', 'exceptions', 'organizations'), 'items');

  return (
    <Screen refreshing={portfolio.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Revenue" subtitle="Your portfolio, right now" />

      <Card>
        <KeyValue
          label="Recurring revenue"
          value={asMoney(pick(p, 'recurring_revenue', 'mrr', 'totals.recurring'))}
        />
        <KeyValue
          label="Organizations"
          value={asCount(pick(p, 'organization_count', 'counts.organizations'))}
        />
        <KeyValue
          label="Active"
          value={asCount(pick(p, 'active_count', 'counts.active'))}
        />
        <KeyValue
          label="At risk"
          value={asCount(pick(p, 'at_risk_count', 'counts.at_risk'))}
        />
      </Card>

      <Card>
        <Text style={styles.honest}>
          No chart, deliberately. The platform records what revenue IS, not what
          it was — there is no historical series to draw, and a trend line
          through a single point would be a decoration rather than information.
          When revenue history is recorded, this becomes a trend.
        </Text>
      </Card>

      <SectionHeader title="Exceptions" />
      {!exceptions.length ? (
        <EmptyState
          title="No revenue exceptions"
          body="Nothing in your portfolio is flagged for billing or payment."
        />
      ) : null}
      {exceptions.map((e, i) => (
        <Row
          key={rowKey([e.organization_id ?? e.id], i)}
          title={String(e.organization_name ?? e.name ?? 'Organization')}
          subtitle={typeof e.reason === 'string' ? e.reason : null}
          accent={palette.warning}
          right={<SeverityPill value={String(e.severity ?? e.health ?? '')} />}
        />
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  honest: { ...typography.caption, color: palette.textMuted },
});
