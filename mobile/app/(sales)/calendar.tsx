/**
 * CALENDAR — agenda first, because a phone is an agenda.
 *
 * Day and week live behind a swipe; MONTH IS DELIBERATELY ABSENT. A month grid
 * on a phone gives each day a target smaller than a fingertip and tells a rep
 * nothing they can act on. Agenda answers the actual question — "what is next,
 * and where do I have to be" — in the order it will happen.
 *
 * NO SECOND SCHEDULING ENGINE. Everything here is `sales_scheduling_router`:
 * the same availability, the same appointments, the same
 * `APPOINTMENT_STATUSES`, the same external busy blocks. The tenant stack
 * (`availability_router` + `calendar_router`) is a different calendar for a
 * different experience and is never mixed in — that is the advisor tab's job.
 *
 * External busy time is DISPLAYED AND NEVER EDITED. It comes from somebody's
 * Google or Microsoft calendar through `external_busy`, and a phone that let a
 * rep "delete" a block it does not own would be lying about what it did.
 */

import React, { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { scheduling } from '../../src/api/endpoints';
import { asList, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { addDays, dayOf, isoDate, timeOf } from '../../src/format';
import { APPOINTMENT_STATUS_LABELS } from '../../src/vocab';
import { palette, radius, space, type as typography } from '../../src/theme/tokens';
import type { Appointment } from '../../src/api/types';

type Range = { key: string; label: string; days: number };

const RANGES: Range[] = [
  { key: 'today', label: 'Today', days: 1 },
  { key: 'week', label: 'Next 7 days', days: 7 },
  { key: 'fortnight', label: 'Next 14 days', days: 14 },
];

export default function Calendar() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();
  const [rangeKey, setRangeKey] = useState('week');
  const [scope, setScope] = useState<'mine' | 'team'>('mine');

  const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[1];
  const from = useMemo(() => isoDate(new Date()), []);
  const to = useMemo(() => isoDate(addDays(new Date(), range.days - 1)), [range.days]);

  const query = useScopedQuery(['appointments', brand, rangeKey, scope], () =>
    scheduling.appointments({
      brand_sales_org_id: brand, date_from: from, date_to: to, scope,
    }));

  const appointments = asList<Appointment>(query.data, 'appointments', 'items');

  /** Group by calendar day so the agenda reads as days, not as a flat list of
   *  forty rows with no shape. */
  const grouped = useMemo(() => {
    const map = new Map<string, Appointment[]>();
    const sorted = [...appointments].sort(
      (a, b) => String(a.starts_at ?? '').localeCompare(String(b.starts_at ?? '')),
    );
    for (const appt of sorted) {
      const key = dayOf(appt.starts_at);
      const list = map.get(key) ?? [];
      list.push(appt);
      map.set(key, list);
    }
    return Array.from(map.entries());
  }, [appointments]);

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Calendar" subtitle="Agenda" />

      <View style={styles.controls}>
        {RANGES.map((r) => (
          <Pressable
            key={r.key}
            onPress={() => setRangeKey(r.key)}
            style={[styles.chip, r.key === rangeKey && styles.chipOn]}
            accessibilityRole="button"
            accessibilityState={{ selected: r.key === rangeKey }}
          >
            <Text style={[styles.chipText, r.key === rangeKey && styles.chipTextOn]}>
              {r.label}
            </Text>
          </Pressable>
        ))}
      </View>

      <View style={styles.controls}>
        {(['mine', 'team'] as const).map((s) => (
          <Pressable
            key={s}
            onPress={() => setScope(s)}
            style={[styles.chip, s === scope && styles.chipOn]}
            accessibilityRole="button"
            accessibilityState={{ selected: s === scope }}
          >
            <Text style={[styles.chipText, s === scope && styles.chipTextOn]}>
              {s === 'mine' ? 'Mine' : 'Team'}
            </Text>
          </Pressable>
        ))}
      </View>

      {query.isLoading ? <Loading label="Loading your calendar" /> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={refresh} /> : null}

      {!query.isLoading && !query.isError && !grouped.length ? (
        <EmptyState
          title="Nothing booked"
          body={`No appointments in the ${range.label.toLowerCase()}.`}
        />
      ) : null}

      {grouped.map(([day, items]) => (
        <View key={day} style={{ gap: space.sm }}>
          <SectionHeader title={day} />
          {items.map((a) => (
            <Row
              key={String(a.id)}
              title={String(a.title ?? a.meeting_type ?? 'Appointment')}
              subtitle={a.prospect_name ?? a.opportunity_name ?? null}
              meta={[
                `${timeOf(a.starts_at)}–${timeOf(a.ends_at)}`,
                a.location,
              ].filter(Boolean).join(' · ')}
              accent={a.status === 'cancelled' ? palette.neutral : palette.accent}
              onPress={() => router.push(`/appointment/${a.id}` as never)}
              right={
                a.status && a.status !== 'scheduled'
                  ? <Pill
                      label={APPOINTMENT_STATUS_LABELS[String(a.status)] ?? String(a.status)}
                      tone={a.status === 'completed' ? 'positive'
                        : a.status === 'no_show' ? 'danger' : 'neutral'}
                    />
                  : a.confirmation_status === 'confirmed'
                    ? <Pill label="Confirmed" tone="positive" />
                    : <Pill label="Unconfirmed" tone="warning" />
              }
            />
          ))}
        </View>
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  controls: { flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' },
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
