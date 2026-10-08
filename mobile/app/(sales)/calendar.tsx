/**
 * CALENDAR — agenda first, because a phone is an agenda.
 *
 * Day and week live behind a swipe; MONTH IS DELIBERATELY ABSENT. A month grid
 * on a phone gives each day a target smaller than a fingertip and tells a rep
 * nothing they can act on. Agenda answers the actual question — "what is next,
 * and where do I have to be" — in the order it will happen.
 *
 * ONE FEED. Appointments, tasks and activity all come from
 * GET /sales/calendar/events, the same read the web grids use.
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
import { useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { addDays, isoDate } from '../../src/format';
import { APPOINTMENT_STATUS_LABELS } from '../../src/vocab';
import { palette, radius, space, type as typography } from '../../src/theme/tokens';

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

  const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[1];
  const from = useMemo(() => isoDate(new Date()), []);
  const to = useMemo(() => isoDate(addDays(new Date(), range.days - 1)), [range.days]);

  // ONE source: the shared event feed, the same endpoint the web grids read.
  // The server narrows a seller to their own events and a manager to the brand;
  // this screen only displays. There is no second appointment read.
  const query = useScopedQuery(['calendar-events', brand, rangeKey], () =>
    scheduling.calendarEvents({ brand_sales_org_id: brand, date_from: from, date_to: to }));
  const feed = (query.data ?? {}) as {
    events?: Array<Record<string, any>>; unscheduled?: Array<Record<string, any>>;
    unavailable_sources?: string[]; truncated?: boolean; scope?: string;
  };
  const events = feed.events ?? [];
  const partial = Boolean(feed.unavailable_sources?.length || feed.truncated);

  /** Group by the event's OWN local date (server-derived), so a late-evening
   *  meeting is never moved to the next day by the phone's timezone. */
  const grouped = useMemo(() => {
    const map = new Map<string, Array<Record<string, any>>>();
    for (const e of events) {
      const key = String(e.local_date ?? 'unknown');
      const list = map.get(key) ?? [];
      list.push(e);
      map.set(key, list);
    }
    return Array.from(map.entries()).sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0));
  }, [events]);

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

      {query.isLoading ? <Loading label="Loading your calendar" /> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={refresh} /> : null}

      {partial ? (
        <Text style={styles.chipText}>
          Partial data{feed.unavailable_sources?.length
            ? `: ${feed.unavailable_sources.join(', ')} unavailable` : ': list truncated'}.
        </Text>
      ) : null}

      {!query.isLoading && !query.isError && !partial
        && !grouped.length && !(feed.unscheduled ?? []).length ? (
        <EmptyState
          title="Nothing scheduled"
          body={`No appointments, tasks or activity in the ${range.label.toLowerCase()}.`}
        />
      ) : null}

      {grouped.map(([day, items]) => (
        <View key={day} style={{ gap: space.sm }}>
          <SectionHeader title={day} />
          {items.map((e) => {
            const isAppt = e.type === 'appointment';
            return (
              <Row
                key={String(e.id)}
                title={String(e.title ?? (isAppt ? 'Appointment' : 'Untitled'))}
                subtitle={e.company ?? null}
                meta={[
                  e.all_day ? 'All day' : String(e.starts_at_local ?? '').slice(11, 16),
                  e.type,
                  e.appointment?.location,
                ].filter(Boolean).join(' · ')}
                accent={e.bucket === 'cancelled' ? palette.neutral : palette.accent}
                onPress={isAppt
                  ? () => router.push(`/appointment/${e.source_id}` as never) : undefined}
                right={e.bucket !== 'scheduled'
                  ? <Pill label={APPOINTMENT_STATUS_LABELS[String(e.status)] ?? String(e.bucket)}
                      tone={e.bucket === 'completed' ? 'positive' : 'neutral'} />
                  : e.appointment?.confirmation_status === 'confirmed'
                    ? <Pill label="Confirmed" tone="positive" />
                    : <Pill label="Unconfirmed" tone="warning" />}
              />
            );
          })}
        </View>
      ))}
      {(feed.unscheduled ?? []).map((e) => (
        <Row key={String(e.id)} title={String(e.title ?? 'Untitled')}
             subtitle={e.company ?? null} meta="Unscheduled" accent={palette.neutral} />
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
