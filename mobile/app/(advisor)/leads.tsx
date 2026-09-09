/**
 * ADVISOR LEADS — the tenant `/leads` list, paged for a phone.
 *
 * `page_size` is 25 here and the route's own default is 500 (max 2000). That
 * default is right for a desktop table and absurd on a cellular connection: a
 * rep in a car park would wait for five hundred records to render twenty-five
 * of them. No backend change was needed — the parameter already existed.
 *
 * Search is server-side for the same reason. Filtering a page of 25 locally
 * would search a page, not a book, and would quietly find nothing while looking
 * like it worked.
 */

import React, { useState } from 'react';
import { StyleSheet, TextInput } from 'react-native';
import { router } from 'expo-router';

import { advisor } from '../../src/api/endpoints';
import { asList, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle,
} from '../../src/components/ui';
import { nameOf, relativeOf } from '../../src/format';
import { palette, radius, space, type as typography, HIT_SIZE } from '../../src/theme/tokens';
import type { Lead } from '../../src/api/types';

export default function AdvisorLeads() {
  const refresh = useScopedRefresh();
  const [search, setSearch] = useState('');
  const [applied, setApplied] = useState('');

  const query = useScopedQuery(['advisor', 'leads', applied], () =>
    advisor.leads({ page_size: 25, search: applied || undefined }));

  const rows = asList<Lead>(query.data, 'leads', 'items', 'results');

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Leads" />

      <TextInput
        value={search}
        onChangeText={setSearch}
        onSubmitEditing={() => setApplied(search.trim())}
        placeholder="Search by name, phone or email"
        placeholderTextColor={palette.textFaint}
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="search"
        style={styles.input}
        accessibilityLabel="Search leads"
      />

      {query.isLoading ? <Loading label="Loading leads" /> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={refresh} /> : null}

      {!query.isLoading && !query.isError && !rows.length ? (
        <EmptyState
          title={applied ? 'Nothing matched' : 'No leads yet'}
          body={applied ? 'Try a different name, phone number or email.' : undefined}
        />
      ) : null}

      {rows.map((l) => (
        <Row
          key={String(l.id)}
          title={nameOf(l as Record<string, unknown>, 'Lead')}
          subtitle={[l.phone, l.email].filter(Boolean).join(' · ') || null}
          meta={l.last_contacted_at
            ? `Last contact ${relativeOf(l.last_contacted_at)}` : 'Never contacted'}
          onPress={() => router.push(`/lead/${l.id}` as never)}
          right={l.status ? <Pill label={String(l.status)} tone="neutral" /> : undefined}
        />
      ))}
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
