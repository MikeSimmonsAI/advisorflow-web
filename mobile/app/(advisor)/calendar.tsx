/**
 * ADVISOR CALENDAR — the TENANT scheduling stack, not the brand-sales one.
 *
 * `calendar_router` / `availability_router` for a customer workspace;
 * `sales_scheduling_router` for brand sales. Two stacks, deliberately unmerged
 * (Phase 0 §21), and the ACTIVE EXPERIENCE is what decides which one a calendar
 * tab talks to. Merging them would be the single fastest way to show a funeral
 * home's bookings inside a software company's sales pipeline.
 *
 * That is why this file exists at all rather than the sales calendar taking a
 * flag: a flag is one edit away from being wrong.
 */

import React, { useMemo } from 'react';
import { View } from 'react-native';

import { advisor } from '../../src/api/endpoints';
import { asList, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { addDays, dayOf, isoDate, timeOf } from '../../src/format';
import { palette, space } from '../../src/theme/tokens';

export default function AdvisorCalendar() {
  const exp = useActiveExperience();
  const refresh = useScopedRefresh();

  const from = useMemo(() => isoDate(new Date()), []);
  const to = useMemo(() => isoDate(addDays(new Date(), 13)), []);

  const query = useScopedQuery(['advisor', 'calendar', from, to],
    () => advisor.calendarEvents({ date_from: from, date_to: to, start: from, end: to }));

  const events = asList<Record<string, unknown>>(query.data, 'events', 'items', 'appointments');

  const grouped = useMemo(() => {
    const map = new Map<string, Array<Record<string, unknown>>>();
    const sorted = [...events].sort((a, b) =>
      String(a.starts_at ?? a.start ?? '').localeCompare(String(b.starts_at ?? b.start ?? '')));
    for (const e of sorted) {
      const key = dayOf(e.starts_at ?? e.start ?? e.scheduled_at);
      const list = map.get(key) ?? [];
      list.push(e);
      map.set(key, list);
    }
    return Array.from(map.entries());
  }, [events]);

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Calendar" subtitle={exp.label} />

      {query.isLoading ? <Loading label="Loading your calendar" /> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={refresh} /> : null}

      {!query.isLoading && !query.isError && !grouped.length ? (
        <EmptyState title="Nothing booked" body="No appointments in the next two weeks." />
      ) : null}

      {grouped.map(([day, items]) => (
        <View key={day} style={{ gap: space.sm }}>
          <SectionHeader title={day} />
          {items.map((e, i) => (
            <Row
              key={String(e.id ?? i)}
              title={String(e.title ?? e.summary ?? e.lead_name ?? 'Appointment')}
              subtitle={typeof e.location === 'string' ? e.location : null}
              meta={timeOf(e.starts_at ?? e.start ?? e.scheduled_at)}
              accent={palette.accent}
              right={e.status ? <Pill label={String(e.status)} tone="neutral" /> : undefined}
            />
          ))}
        </View>
      ))}
    </Screen>
  );
}
