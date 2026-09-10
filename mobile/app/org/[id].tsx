/**
 * EXECUTIVE · ORGANIZATION DETAIL.
 *
 * Reached from the portfolio, from a deep link, or from a push tap. In all
 * three cases the id is untrusted input and
 * `GET /executive/organizations/{org_id}` re-checks it against
 * `portfolio_authority()`. An org on the executive's own brand that is NOT
 * assigned to them refuses here exactly as it refuses everywhere else, and this
 * screen renders that refusal as what it is rather than as a broken page.
 *
 * Read-mostly by design: an executive looks at an organisation to understand
 * it, not to operate it. The tenant's own people run it, and the owner control
 * plane changes it.
 */

import React from 'react';
import { StyleSheet, Text } from 'react-native';
import { useLocalSearchParams } from 'expo-router';

import { executive } from '../../src/api/endpoints';
import { ApiError } from '../../src/api/client';
import {
  asCount, asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Row, Screen, ScreenTitle,
  SectionHeader, SeverityPill,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';
import { palette, type as typography } from '../../src/theme/tokens';
import { rowKey } from '../../src/keys';

export default function OrgDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const refresh = useScopedRefresh();

  const detail = useScopedQuery(['executive', 'org', id],
    () => executive.organization(String(id)), { enabled: !!id });
  const performance = useScopedQuery(['executive', 'org', id, 'performance'],
    () => executive.orgPerformance(String(id)), { enabled: !!id });

  if (detail.isLoading) return <Screen><Loading label="Loading organization" /></Screen>;

  if (detail.isError) {
    const err = detail.error;
    const refused = err instanceof ApiError && (err.status === 403 || err.status === 404);
    if (refused) {
      // The honest reading of a refusal on this route: it is not yours. Saying
      // "not found" would be technically true and useless; saying "error" would
      // invite a retry that will refuse identically.
      return (
        <Screen>
          <EmptyState
            title="Not in your portfolio"
            body="This organization has not been assigned to you. Executive access is per organization — being on the same brand does not include it."
          />
        </Screen>
      );
    }
    return <Screen><ErrorState error={err} onRetry={refresh} /></Screen>;
  }

  const d = detail.data ?? {};
  const perf = performance.data ?? {};
  const signals = asList<Record<string, unknown>>(
    pick(perf, 'signals', 'highlights', 'items'), 'items');

  return (
    <Screen refreshing={detail.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title={String(pick(d, 'name', 'organization_name') ?? 'Organization')}
        subtitle={String(pick(d, 'plan', 'status') ?? '')}
      />

      <Card>
        <KeyValue
          label="Health"
          value={<SeverityPill value={String(pick(d, 'severity', 'health') ?? '')} />}
        />
        <KeyValue label="Users" value={asCount(pick(d, 'user_count', 'counts.users'))} />
        <KeyValue label="Leads" value={asCount(pick(d, 'lead_count', 'counts.leads'))} />
        <KeyValue
          label="Recurring revenue"
          value={asMoney(pick(d, 'recurring_revenue', 'mrr', 'revenue.recurring'))}
        />
        <KeyValue
          label="Live since"
          value={pick(d, 'live_at', 'created_at')
            ? relativeOf(pick(d, 'live_at', 'created_at')) : '—'}
        />
      </Card>

      <SectionHeader title="What moved" />
      {!signals.length ? (
        <Text style={styles.note}>
          Nothing notable has changed for this organization.
        </Text>
      ) : null}
      {signals.map((s, i) => (
        <Row
          key={rowKey([s.key], i)}
          title={String(s.label ?? s.title ?? s.metric ?? 'Signal')}
          subtitle={typeof s.summary === 'string' ? s.summary
            : typeof s.description === 'string' ? s.description : null}
          accent={palette.accent}
          right={s.severity ? <SeverityPill value={String(s.severity)} /> : undefined}
        />
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint },
});
