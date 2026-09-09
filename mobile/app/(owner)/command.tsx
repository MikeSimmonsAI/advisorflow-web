/**
 * OWNER COMMAND — six things worth waking up for, and nothing else.
 *
 * The desktop God sidebar is enormous and correctly so; it is a control plane.
 * Cramming it onto a phone would produce a menu nobody can navigate and a set
 * of destructive buttons within thumb's reach of each other.
 *
 * SO THE PHONE IS DELIBERATELY NARROW, and the narrowing is by consequence
 * rather than by convenience: NOTHING DESTRUCTIVE IS REACHABLE FROM MOBILE. No
 * suspend, no cancellation, no offboarding, no permanent delete, no role
 * change, no impersonation. Those routes exist, are authorised, and are simply
 * never called from this app. A phone is the wrong place to end a customer —
 * one mis-tap while walking, and a live business is offboarded.
 *
 * What IS here: what broke, who needs attention, and a fast way into another
 * authorized experience.
 */

import React from 'react';
import { StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';

import { owner } from '../../src/api/endpoints';
import {
  asCount, asList, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import { useExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Row, Screen,
  ScreenTitle, SectionHeader, SeverityPill,
} from '../../src/components/ui';
import { normaliseSeverity } from '../../src/vocab';
import { palette, space, type as typography } from '../../src/theme/tokens';
import type { OwnerQueue } from '../../src/api/types';

export default function OwnerCommand() {
  const refresh = useScopedRefresh();
  const { experiences } = useExperience();

  const stats = useScopedQuery(['owner', 'stats'], () => owner.stats());
  const health = useScopedQuery(['owner', 'platform-health'], () => owner.platformHealth());
  const queues = useScopedQuery(['owner', 'queues'], () => owner.queues());

  if (stats.isLoading && queues.isLoading) {
    return <Screen><Loading label="Loading the platform" /></Screen>;
  }

  const queueRows = asList<OwnerQueue>(queues.data, 'queues', 'items')
    .filter((q) => asCount(q.count) > 0);

  const s = stats.data ?? {};
  const h = health.data ?? {};
  const overall = normaliseSeverity(
    String(pick(h, 'severity', 'status', 'overall') ?? ''));

  return (
    <Screen refreshing={stats.isFetching || queues.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Owner Command" subtitle="What needs you, and nothing else" />

      {stats.isError && queues.isError ? (
        <ErrorState error={stats.error} onRetry={refresh} />
      ) : null}

      <Card>
        <KeyValue label="Platform" value={<SeverityPill value={overall} />} />
        <KeyValue label="Customers" value={asCount(pick(s, 'organizations', 'counts.orgs', 'org_count'))} />
        <KeyValue label="Users" value={asCount(pick(s, 'users', 'counts.users', 'user_count'))} />
        <KeyValue label="Leads" value={asCount(pick(s, 'leads', 'counts.leads', 'lead_count'))} />
      </Card>

      <SectionHeader title="Exception queues" />
      {!queueRows.length ? (
        <EmptyState
          title="Nothing is on fire"
          body="Every exception queue is empty. Go and do something else."
        />
      ) : null}
      {queueRows.map((q, i) => (
        <Row
          key={String(q.key ?? i)}
          title={String(q.label ?? q.title ?? q.key ?? 'Queue')}
          meta={`${asCount(q.count)} ${asCount(q.count) === 1 ? 'item' : 'items'}`}
          accent={palette.warning}
          onPress={() => router.push('/(owner)/issues' as never)}
          right={q.severity ? <SeverityPill value={String(q.severity)} /> : undefined}
        />
      ))}

      {experiences.length > 1 ? (
        <>
          <SectionHeader title="Your other experiences" />
          {experiences.filter((e) => e.kind !== 'owner').map((e) => (
            <Row
              key={e.key}
              title={e.label}
              subtitle={e.detail}
              onPress={() => router.push('/switch' as never)}
            />
          ))}
        </>
      ) : null}

      <Text style={styles.note}>
        Cancelling, suspending, offboarding and deleting a customer are not
        available on mobile. Those live on a computer, on purpose.
      </Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint, marginTop: space.lg },
});
