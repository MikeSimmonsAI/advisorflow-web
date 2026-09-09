/**
 * OWNER · CRITICAL ISSUES.
 *
 * Two sources, both already actionable server-side:
 *   `GET /god/ops/queues`           — the exception queues, each resolvable
 *                                     from a real screen (§37)
 *   `GET /god/ops/implementations`  — the ones that are blocked or overdue
 *
 * A queue with a count of zero is not rendered. A screen listing eight empty
 * queues reads as noise and trains the owner to ignore it; a screen with two
 * rows on it means two things are wrong.
 *
 * NOTHING HERE RESOLVES ANYTHING. The phone shows what is broken and where it
 * is; the fix — provisioning, billing configuration, an implementation
 * decision — happens on a computer with the full context in front of you.
 */

import React from 'react';
import { StyleSheet, Text } from 'react-native';

import { owner } from '../../src/api/endpoints';
import { asCount, asList, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  Card, EmptyState, ErrorState, Loading, Row, Screen, ScreenTitle,
  SectionHeader, SeverityPill,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';
import { palette, type as typography } from '../../src/theme/tokens';
import type { OwnerQueue } from '../../src/api/types';

export default function OwnerIssues() {
  const refresh = useScopedRefresh();

  const queues = useScopedQuery(['owner', 'queues'], () => owner.queues());
  const blocked = useScopedQuery(['owner', 'implementations', 'blocked'],
    () => owner.implementations({ blocked: true, limit: 50 }));
  const overdue = useScopedQuery(['owner', 'implementations', 'overdue'],
    () => owner.implementations({ overdue: true, limit: 50 }));

  if (queues.isLoading) return <Screen><Loading label="Checking the platform" /></Screen>;
  if (queues.isError) return <Screen><ErrorState error={queues.error} onRetry={refresh} /></Screen>;

  const queueRows = asList<OwnerQueue>(queues.data, 'queues', 'items')
    .filter((q) => asCount(q.count) > 0);
  const blockedRows = asList<Record<string, unknown>>(
    blocked.data, 'implementations', 'items');
  const overdueRows = asList<Record<string, unknown>>(
    overdue.data, 'implementations', 'items');

  const clean = !queueRows.length && !blockedRows.length && !overdueRows.length;

  return (
    <Screen refreshing={queues.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Critical issues" />

      {clean ? (
        <EmptyState
          title="Nothing needs you"
          body="No exception queue has anything in it, and no implementation is blocked or overdue."
        />
      ) : null}

      {queueRows.length ? (
        <>
          <SectionHeader title="Exception queues" />
          {queueRows.map((q, i) => (
            <Row
              key={String(q.key ?? i)}
              title={String(q.label ?? q.title ?? q.key ?? 'Queue')}
              subtitle={typeof q.description === 'string' ? q.description : null}
              meta={`${asCount(q.count)} waiting`}
              accent={palette.warning}
              right={q.severity ? <SeverityPill value={String(q.severity)} /> : undefined}
            />
          ))}
        </>
      ) : null}

      {blockedRows.length ? (
        <>
          <SectionHeader title={`Blocked implementations · ${blockedRows.length}`} />
          {blockedRows.map((r, i) => (
            <Row
              key={String(r.id ?? i)}
              title={String(r.organization_name ?? r.customer_name ?? 'Implementation')}
              subtitle={typeof r.blocked_reason === 'string' ? r.blocked_reason
                : typeof r.status === 'string' ? r.status : null}
              meta={r.updated_at ? `Updated ${relativeOf(r.updated_at)}` : null}
              accent={palette.danger}
            />
          ))}
        </>
      ) : null}

      {overdueRows.length ? (
        <>
          <SectionHeader title={`Overdue implementations · ${overdueRows.length}`} />
          {overdueRows.map((r, i) => (
            <Row
              key={String(r.id ?? i)}
              title={String(r.organization_name ?? r.customer_name ?? 'Implementation')}
              subtitle={typeof r.status === 'string' ? r.status : null}
              meta={r.due_at ? `Due ${relativeOf(r.due_at)}` : null}
              accent={palette.warning}
            />
          ))}
        </>
      ) : null}

      <Card>
        <Text style={styles.note}>
          Resolving any of these happens on a computer. This screen tells you
          what is wrong and how urgent it is; it does not hand you the buttons
          that end a customer&apos;s account while you are walking.
        </Text>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint },
});
