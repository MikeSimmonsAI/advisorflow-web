/**
 * TEAM — one row per rep, and what each of them is carrying.
 *
 * `GET /sales/manager/overview` already assembles this; the phone renders it.
 * Tapping a rep opens `GET /sales/manager/reps/{id}`, which is the same detail
 * the desktop shows.
 *
 * WHAT THE MONEY BADGE ACTUALLY IS — the question that produced this pass.
 * The card showed a bare dollar chip: "$1,497", "$2,000,000", "$0". Three
 * numbers with no noun, next to three people's names, which reads as what each
 * of them has SOLD. It is not. `manager_workspace.rep_rollup` computes it as:
 *
 *     "pipeline_value": sum(deal_value for open opportunities owned by this rep)
 *
 * — OPEN PIPELINE. Nothing in it is closed, won, earned or paid, and the $0 is
 * a rep with no open deals, not a rep who has sold nothing. It is now labelled
 * "Open pipeline" wherever it appears, and the header says it once more, in
 * words, so nobody has to remember.
 *
 * The other two defects were the same class of thing: `role` was rendered raw,
 * so the subtitle read "sales_rep"; and the meta line asked for
 * `open_opportunities` / `appointments_today`, which the rollup calls
 * `open_deals` / `meetings_today`, so it drew nothing at all.
 *
 * `require_sales_manager` guards both endpoints. A rep who somehow reached this
 * screen — by editing the app, by a deep link — gets 403 from the server, which
 * is why the tab set being wrong would be an inconvenience rather than a breach.
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
import { repCards } from '../../src/manager/present';
import { palette, space, type as typography } from '../../src/theme/tokens';

type Rec = Record<string, unknown>;

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

  const reps = repCards(asList<Rec>(
    pick(overview.data, 'reps'), 'reps', 'members', 'items'));

  return (
    <Screen refreshing={overview.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Team"
        subtitle={`${reps.length} ${reps.length === 1 ? 'person' : 'people'}`}
      />

      {reps.length ? (
        <Text style={styles.legend}>
          The figure on each row is that person&apos;s OPEN PIPELINE — the value
          of the deals they own that have not closed. It is not revenue and
          nothing in it has been earned.
        </Text>
      ) : null}

      {!reps.length ? (
        <EmptyState
          title="No reps yet"
          body="Nobody has been added to this brand's sales team."
        />
      ) : null}

      {reps.map((r) => {
        const open = openRep === r.userId;
        return (
          <React.Fragment key={r.key}>
            <Row
              title={r.name}
              subtitle={r.role}
              meta={[
                r.openDeals !== null
                  ? `${r.openDeals} open ${r.openDeals === 1 ? 'deal' : 'deals'}`
                  : null,
                r.needsAttention ? `${r.needsAttention} need attention` : null,
                r.overdueActions ? `${r.overdueActions} overdue` : null,
                r.meetingsToday
                  ? `${r.meetingsToday} ${r.meetingsToday === 1 ? 'meeting' : 'meetings'} today`
                  : null,
                r.lastActivity ? `active ${r.lastActivity}` : null,
              ].filter(Boolean).join(' · ') || null}
              accent={open ? palette.accent
                : r.needsAttention ? palette.warning : undefined}
              onPress={r.userId
                ? () => setOpenRep(open ? null : r.userId)
                : undefined}
              right={r.openPipeline !== null
                // Labelled, not a naked number. `tone="neutral"` too: an
                // accent-coloured money chip reads as an achievement.
                ? <Pill label={`${asMoney(r.openPipeline)} open`} tone="neutral" />
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
                        value={asCount(pick(detail.data, 'open_opportunities',
                                            'open_deals', 'counts.open'))}
                      />
                      <KeyValue
                        label="Open pipeline"
                        value={asMoney(pick(detail.data, 'pipeline_value',
                                            'totals.pipeline'))}
                      />
                      <KeyValue
                        label="Deals won"
                        value={asCount(pick(detail.data, 'won_count', 'counts.won'))}
                      />
                      <KeyValue
                        label="Meetings booked"
                        value={asCount(pick(detail.data, 'appointments',
                                            'counts.appointments'))}
                      />
                      <Text style={styles.note}>
                        Open pipeline is unclosed business on today&apos;s terms.
                      </Text>
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
  note: { ...typography.caption, color: palette.textFaint, marginTop: space.sm },
  legend: { ...typography.caption, color: palette.textMuted },
});
