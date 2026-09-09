/**
 * OWNER · CUSTOMERS — read, tap through, do nothing destructive.
 *
 * `GET /god/ops/customer-organizations` lists them; Customer Detail reads
 * Customer 360. Every lifecycle action that changes a customer's existence —
 * suspend, request cancellation, start offboarding, archive, permanent delete —
 * exists on the server, is authorised, and is deliberately never called from
 * this app. See the note on Owner Command for why a phone is the wrong place
 * for those.
 */

import React, { useState } from 'react';
import { StyleSheet, TextInput } from 'react-native';
import { router } from 'expo-router';

import { owner } from '../../src/api/endpoints';
import { asList, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';
import { palette, radius, space, type as typography, HIT_SIZE } from '../../src/theme/tokens';

export default function OwnerCustomers() {
  const refresh = useScopedRefresh();
  const [filter, setFilter] = useState('');

  const query = useScopedQuery(['owner', 'customers'], () => owner.customers({ limit: 300 }));

  const all = asList<Record<string, unknown>>(
    query.data, 'organizations', 'customers', 'items');

  // Filtering here is safe and correct: the whole list is already on the device
  // (limit 300 covers the platform today), so this narrows what is displayed
  // rather than pretending to search a larger set that was never fetched.
  const needle = filter.trim().toLowerCase();
  const rows = needle
    ? all.filter((c) => String(c.name ?? c.organization_name ?? '')
        .toLowerCase().includes(needle))
    : all;

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Customers" subtitle={`${all.length} on the platform`} />

      <TextInput
        value={filter}
        onChangeText={setFilter}
        placeholder="Filter by name"
        placeholderTextColor={palette.textFaint}
        autoCapitalize="none"
        autoCorrect={false}
        style={styles.input}
        accessibilityLabel="Filter customers"
      />

      {query.isLoading ? <Loading label="Loading customers" /> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={refresh} /> : null}

      {!query.isLoading && !rows.length ? (
        <EmptyState title={needle ? 'Nothing matched' : 'No customers yet'} />
      ) : null}

      {rows.map((c, i) => {
        const id = String(c.id ?? c.organization_id ?? i);
        const active = c.is_active !== false;
        return (
          <Row
            key={id}
            title={String(c.name ?? c.organization_name ?? 'Organization')}
            subtitle={[c.plan, c.platform_name].filter(Boolean).join(' · ') || null}
            meta={c.created_at ? `Since ${relativeOf(c.created_at)}` : null}
            onPress={() => router.push(`/customer/${id}` as never)}
            right={<Pill label={active ? 'Active' : 'Inactive'}
                         tone={active ? 'positive' : 'neutral'} />}
          />
        );
      })}
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: {
    minHeight: HIT_SIZE,
    backgroundColor: palette.surfaceSunken,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    paddingHorizontal: space.lg,
    color: palette.text,
    ...typography.body,
  },
});
