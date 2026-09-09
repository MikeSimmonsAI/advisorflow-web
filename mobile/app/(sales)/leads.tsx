/**
 * LEADS — the rep's book, on a phone.
 *
 * In the brand-sales experience a "lead" is an OPPORTUNITY: `/leads` belongs to
 * the customer tenant stack and a brand salesperson has no tenant
 * (`organization_id` is NULL by positive architectural assertion, and
 * `require_tenant_user` refuses them by name). So this tab lists
 * `/sales/opportunities`, filtered by stage.
 *
 * The advisor experience has the other meaning and its own screen —
 * `app/(advisor)/leads.tsx` — which really does call `/leads`. Two experiences,
 * two nouns, two screens, one word. Collapsing them into one screen with a flag
 * is how the wrong query gets sent from the right-looking tab.
 *
 * `include_lost` is off by default and is a deliberate toggle rather than a
 * setting: a rep scrolling their book wants the live ones, and a lost deal in
 * the list is a deal they will re-read before remembering why it is there.
 */

import React, { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';

import { sales } from '../../src/api/endpoints';
import { asList, asMoney, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle,
} from '../../src/components/ui';
import { nameOf, relativeOf } from '../../src/format';
import { OPEN_STAGES, stageLabel } from '../../src/vocab';
import { palette, radius, space, type as typography } from '../../src/theme/tokens';
import type { Opportunity } from '../../src/api/types';

const FILTERS: Array<{ key: string; label: string; stage?: string }> = [
  { key: 'all', label: 'Open' },
  ...OPEN_STAGES.map((s) => ({ key: s, label: stageLabel(s), stage: s })),
  { key: 'won', label: 'Won', stage: 'won' },
];

export default function Leads() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();
  const [filter, setFilter] = useState('all');

  const selected = FILTERS.find((f) => f.key === filter);

  const query = useScopedQuery(['opportunities', brand, filter], () =>
    sales.opportunities({
      brand_sales_org_id: brand,
      stage: selected?.stage,
      include_lost: false,
    }));

  const rows = useMemo(
    () => asList<Opportunity>(query.data, 'opportunities', 'items'),
    [query.data],
  );

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Leads" subtitle="Your deals, newest activity first" />

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
      >
        {FILTERS.map((f) => {
          const on = f.key === filter;
          return (
            <Pressable
              key={f.key}
              onPress={() => setFilter(f.key)}
              style={[styles.chip, on && styles.chipOn]}
              accessibilityRole="button"
              accessibilityState={{ selected: on }}
            >
              <Text style={[styles.chipText, on && styles.chipTextOn]}>{f.label}</Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {query.isLoading ? <Loading label="Loading your deals" /> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={refresh} /> : null}

      {!query.isLoading && !query.isError && !rows.length ? (
        <EmptyState
          title={selected?.stage ? `Nothing in ${selected.label}` : 'No open deals'}
          body="New opportunities you create, and anything assigned to you, will show up here."
        />
      ) : null}

      {rows.map((o) => (
        <Row
          key={String(o.id)}
          title={nameOf(o, 'Opportunity')}
          subtitle={[stageLabel(o.stage), o.contact_name].filter(Boolean).join(' · ') || null}
          meta={o.updated_at ? `Updated ${relativeOf(o.updated_at)}` : null}
          onPress={() => router.push(`/opportunity/${o.id}` as never)}
          right={o.amount != null ? <Pill label={asMoney(o.amount)} tone="accent" /> : undefined}
        />
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  filters: { gap: space.sm, paddingVertical: space.xs, paddingRight: space.lg },
  chip: {
    paddingHorizontal: space.lg,
    paddingVertical: 10,
    borderRadius: radius.pill,
    backgroundColor: palette.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  chipOn: { backgroundColor: 'rgba(8,124,255,0.18)', borderColor: palette.accent },
  chipText: { ...typography.label, color: palette.textMuted },
  chipTextOn: { color: palette.accentSoft },
});
