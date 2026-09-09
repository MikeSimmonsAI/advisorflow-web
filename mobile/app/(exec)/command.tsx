/**
 * EXECUTIVE COMMAND.
 *
 * THE ONE RULE THIS EXPERIENCE EXISTS TO HONOUR:
 * ----------------------------------------------
 * NORMAL EXECUTIVE ACCESS IS NOT BRAND-WIDE. An executive sees the
 * organisations EXPLICITLY ASSIGNED to them through
 * `executive_authority.portfolio_authority()`. Sharing a brand with an
 * organisation does not grant access to it. An executive with zero assignments
 * has a portfolio of zero organisations — not "all of them", not "their brand's
 * ones", zero — and this screen renders that as an honest empty state rather
 * than as an error, because it is the correct answer and not a fault.
 *
 * The phone does none of that reasoning. Every number here comes from
 * `/executive/*`, which resolves the portfolio server-side. There is no
 * client-side portfolio, no "same platform" shortcut, and no place a mistake
 * here could widen anyone's scope.
 *
 * God/owner behaviour stays separate: the owner has their own experience, and
 * `require_brand_executive` handles god through explicit brand selection rather
 * than through this screen.
 */

import React from 'react';
import { StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';

import { executive } from '../../src/api/endpoints';
import {
  asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Row, Screen, ScreenTitle,
  SectionHeader, SeverityPill,
} from '../../src/components/ui';
import { normaliseSeverity } from '../../src/vocab';
import { palette, space, type as typography } from '../../src/theme/tokens';
import type { ExecutiveOrg } from '../../src/api/types';

export default function ExecutiveCommand() {
  const exp = useActiveExperience();
  const refresh = useScopedRefresh();

  const command = useScopedQuery(['executive', 'command'], () => executive.commandCenter());
  const health = useScopedQuery(['executive', 'health'], () => executive.portfolioHealth('all'));

  if (command.isLoading && health.isLoading) {
    return <Screen><Loading label="Loading your portfolio" /></Screen>;
  }
  if (command.isError && health.isError) {
    return <Screen><ErrorState error={command.error} onRetry={refresh} /></Screen>;
  }

  const orgs = asList<ExecutiveOrg>(
    pick(health.data, 'organizations', 'orgs', 'portfolio'), 'organizations', 'items');

  const needsAttention = orgs.filter(
    (o) => normaliseSeverity(String(o.severity ?? o.health ?? '')) !== 'healthy');

  const c = command.data ?? {};

  return (
    <Screen refreshing={command.isFetching || health.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Command" subtitle={exp.detail ?? 'Executive'} />

      {!orgs.length ? (
        <EmptyState
          title="No organizations assigned"
          body="Executive access covers the organizations explicitly assigned to you. Nothing has been assigned yet, so there is nothing to show — this is not an error."
        />
      ) : null}

      {orgs.length ? (
        <Card>
          <KeyValue label="Organizations" value={orgs.length} />
          <KeyValue label="Needing attention" value={needsAttention.length} />
          <KeyValue
            label="Recurring revenue"
            value={asMoney(pick(c, 'recurring_revenue', 'revenue.recurring', 'mrr'))}
          />
          <Text style={styles.note}>
            Your portfolio, not your brand. Organizations on the same brand that
            are not assigned to you do not appear anywhere in this app.
          </Text>
        </Card>
      ) : null}

      {needsAttention.length ? (
        <>
          <SectionHeader title={`Moved · ${needsAttention.length}`} />
          {needsAttention.map((o, i) => {
            const id = String(o.organization_id ?? o.id ?? i);
            return (
              <Row
                key={id}
                title={String(o.organization_name ?? o.name ?? 'Organization')}
                subtitle={typeof o.reason === 'string' ? o.reason
                  : typeof o.summary === 'string' ? o.summary : null}
                accent={palette.warning}
                onPress={() => router.push(`/org/${id}` as never)}
                right={<SeverityPill value={String(o.severity ?? o.health ?? '')} />}
              />
            );
          })}
        </>
      ) : orgs.length ? (
        <EmptyState
          title="Everything is steady"
          body="No organization in your portfolio has moved enough to need you."
        />
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint, marginTop: space.sm },
});
