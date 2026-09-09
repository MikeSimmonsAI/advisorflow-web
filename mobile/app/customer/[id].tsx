/**
 * OWNER · CUSTOMER DETAIL (Customer 360, read-only on mobile).
 *
 * `GET /god/customer-360/customers/{org_id}` is the same record the desktop
 * shows. What the desktop ALSO shows, and this screen deliberately does not,
 * is the row of lifecycle actions beside it: request cancellation, start
 * offboarding, complete cancellation, archive, permanent delete.
 *
 * Those are the six most consequential buttons in the platform and they are all
 * one tap from each other. On a phone, held one-handed, walking — no. The
 * routes exist and are authorised; this app simply never calls them, and the
 * screen says so rather than leaving the owner to wonder whether the feature is
 * missing or the page is broken.
 */

import React from 'react';
import { StyleSheet, Text } from 'react-native';
import { useLocalSearchParams } from 'expo-router';

import { owner } from '../../src/api/endpoints';
import {
  asCount, asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import {
  Card, ErrorState, KeyValue, Loading, Row, Screen, ScreenTitle, SectionHeader,
  SeverityPill,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';
import { palette, type as typography } from '../../src/theme/tokens';

export default function CustomerDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const refresh = useScopedRefresh();

  const query = useScopedQuery(['owner', 'customer', id],
    () => owner.customer360(String(id)), { enabled: !!id });

  if (query.isLoading) return <Screen><Loading label="Loading customer" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const c = query.data ?? {};
  const org = (pick<Record<string, unknown>>(c, 'organization', 'customer') ?? c) as
    Record<string, unknown>;
  const events = asList<Record<string, unknown>>(
    pick(c, 'lifecycle_events', 'events', 'history'), 'items');

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title={String(org.name ?? org.organization_name ?? 'Customer')}
        subtitle={String(org.plan ?? org.billing_status ?? '')}
      />

      <Card>
        <KeyValue
          label="Health"
          value={<SeverityPill value={String(pick(c, 'severity', 'health') ?? '')} />}
        />
        <KeyValue label="Status" value={String(org.billing_status ?? org.status ?? '—')} />
        <KeyValue label="Users" value={asCount(pick(c, 'user_count', 'counts.users'))} />
        <KeyValue label="Leads" value={asCount(pick(c, 'lead_count', 'counts.leads'))} />
        <KeyValue
          label="Recurring revenue"
          value={asMoney(pick(c, 'recurring_revenue', 'mrr', 'revenue.recurring'))}
        />
        <KeyValue
          label="Customer since"
          value={org.created_at ? relativeOf(org.created_at) : '—'}
        />
      </Card>

      {events.length ? (
        <>
          <SectionHeader title="Lifecycle" />
          {events.slice(0, 20).map((e, i) => (
            <Row
              key={String(e.id ?? i)}
              title={String(e.event ?? e.type ?? 'Event')}
              subtitle={typeof e.reason === 'string' ? e.reason
                : typeof e.note === 'string' ? e.note : null}
              meta={relativeOf(e.created_at ?? e.occurred_at)}
            />
          ))}
        </>
      ) : null}

      <Card>
        <Text style={styles.note}>
          Cancelling, offboarding, archiving and deleting this customer are not
          available on mobile — deliberately. Those actions end a live business
          and belong on a computer with the full picture in front of you.
        </Text>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint },
});
