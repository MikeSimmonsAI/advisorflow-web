/**
 * TEAM — one row per rep, and what each of them is carrying.
 *
 * `GET /sales/manager/overview` already assembles this; the phone renders it.
 * Tapping a rep opens `GET /sales/manager/reps/{id}`, which is the same detail
 * the desktop shows.
 *
 * `require_sales_manager` guards both. A rep who somehow reached this screen —
 * by editing the app, by a deep link — gets 403 from the server, which is why
 * the tab set being wrong would be an inconvenience rather than a breach.
 */

import React, { useState } from 'react';
import { StyleSheet, Text } from 'react-native';

import { manager } from '../../src/api/endpoints';
import {
  asCount, asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle,
} from '../../src/components/ui';
import { palette, type as typography } from '../../src/theme/tokens';

export default function Team() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();
  const [openRep, setOpenRep] = useState<string | null>(null);

  const overview = useScopedQuery(['manager', 'overview', brand],
    () => manager.overview({ brand_sales_org_id: brand }));

  const detail = useScopedQuery(['manager', 'rep', openRep, brand],
    () => manager.repDetail(String(openRep), brand),
    { enabled: !!openRep });

  if (overview.isLoading) return <Screen><Loading label="Loading your team" /></Screen>;
  if (overview.isError) return <Screen><ErrorState error={overview.error} onRetry={refresh} /></Screen>;

  const reps = asList<Record<string, unknown>>(
    pick(overview.data, 'reps', 'team', 'members'), 'reps', 'members', 'items');

  return (
    <Screen refreshing={overview.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Team" subtitle={`${reps.length} ${reps.length === 1 ? 'rep' : 'reps'}`} />

      {!reps.length ? (
        <EmptyState
          title="No reps yet"
          body="Nobody has been added to this brand's sales team."
        />
      ) : null}

      {reps.map((r, i) => {
        const id = String(r.user_id ?? r.id ?? i);
        const open = openRep === id;
        return (
          <React.Fragment key={id}>
            <Row
              title={String(r.full_name ?? r.name ?? 'Rep')}
              subtitle={String(r.role ?? '')}
              meta={[
                r.open_opportunities != null ? `${asCount(r.open_opportunities)} open` : null,
                r.appointments_today != null ? `${asCount(r.appointments_today)} today` : null,
              ].filter(Boolean).join(' · ') || null}
              accent={open ? palette.accent : undefined}
              onPress={() => setOpenRep(open ? null : id)}
              right={r.pipeline_value != null
                ? <Pill label={asMoney(r.pipeline_value)} tone="accent" />
                : undefined}
            />
            {open ? (
              detail.isLoading ? <Loading label="" /> : (
                <Card>
                  {detail.isError ? (
                    <Text style={styles.note}>Could not load this rep right now.</Text>
                  ) : (
                    <>
                      <KeyValue
                        label="Open deals"
                        value={asCount(pick(detail.data, 'open_opportunities', 'counts.open'))}
                      />
                      <KeyValue
                        label="Pipeline"
                        value={asMoney(pick(detail.data, 'pipeline_value', 'totals.pipeline'))}
                      />
                      <KeyValue
                        label="Won this period"
                        value={asCount(pick(detail.data, 'won_count', 'counts.won'))}
                      />
                      <KeyValue
                        label="Appointments booked"
                        value={asCount(pick(detail.data, 'appointments', 'counts.appointments'))}
                      />
                    </>
                  )}
                </Card>
              )
            ) : null}
          </React.Fragment>
        );
      })}
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint },
});
