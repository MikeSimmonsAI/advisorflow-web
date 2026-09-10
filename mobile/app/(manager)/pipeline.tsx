/**
 * TEAM PIPELINE.
 *
 * `GET /sales/manager/pipeline-projection` does the projection. Stage
 * probabilities live in `compensation_models` and are applied server-side —
 * this screen does not multiply anything by anything. A weighted forecast
 * computed on a phone would be a second forecast, and the two would disagree in
 * the exact meeting where it mattered.
 *
 * TWO DEFECTS FIXED IN PASS 2, both from asking the payload for keys it does
 * not have:
 *
 *   "BY STAGE" WAS EMPTY. The screen read `by_stage` / `stages`. The projection
 *   payload has neither. It has `deals`, each carrying `stage` and
 *   `fixed_contract_value`, so the grouping is done in `manager/present.ts`
 *   from the same rows the headline total is summed from — a breakdown whose
 *   parts do not add up to the number above it is worse than no breakdown.
 *
 *   "WEIGHTED —". The screen read `weighted_value` / `totals.weighted` /
 *   `projected_value`; the field is `weighted_pipeline_value`, and it is null
 *   BY DESIGN when nobody has configured stage probabilities (zero would read
 *   as "this pipeline is worth nothing"). A dash says the same nothing. It now
 *   says which of the two situations this is.
 *
 * Deal rows were also keyed on `d.id`, which the projection row does not carry;
 * every row was keyed "undefined". They key on `opportunity_id` now.
 */

import React, { useState } from 'react';
import { StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';

import { manager } from '../../src/api/endpoints';
import {
  asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import {
  pipelineDealCards, stageGroups, weightedDisplay,
} from '../../src/manager/present';
import { OPEN_STAGES } from '../../src/vocab';
import { palette, space, type as typography } from '../../src/theme/tokens';

type Rec = Record<string, unknown>;

export default function Pipeline() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();
  // TAPPING A STAGE FILTERS THE LIST BELOW IT. The alternative considered was
  // a route with a `stage` query parameter, which would have been a button
  // that navigates to the screen you are already on — a dead control dressed
  // as a live one.
  const [stageFilter, setStageFilter] = useState<string | null>(null);

  const query = useScopedQuery(['manager', 'pipeline', brand],
    () => manager.pipeline({ brand_sales_org_id: brand, include_deals: true }));

  if (query.isLoading) return <Screen><Loading label="Loading pipeline" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const data = (query.data ?? {}) as Rec;
  const rawDeals = asList<Rec>(pick(data, 'deals'), 'deals', 'items');
  const groups = stageGroups(rawDeals, OPEN_STAGES);
  const allDeals = pipelineDealCards(rawDeals);
  const deals = stageFilter
    ? pipelineDealCards(rawDeals.filter((d) => d.stage === stageFilter))
    : allDeals;

  const total = pick(data, 'pipeline_total_fixed_contract_value', 'pipeline_value');
  const weighted = weightedDisplay(data);
  const incomplete = Number(pick(data, 'pricing_incomplete_count') ?? 0);

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Pipeline" subtitle={exp.detail ?? undefined} />

      <Card>
        <KeyValue label="Open pipeline" value={asMoney(total)} />
        {/* NEVER AN UNEXPLAINED DASH. Either the server produced a weighted
            figure, or it said it could not — and the reason goes on screen. */}
        <KeyValue
          label="Weighted"
          value={weighted.value !== null
            ? asMoney(weighted.value)
            : 'Not configured'}
        />
        <Text style={styles.note}>{weighted.note}</Text>
        {incomplete > 0 ? (
          <Text style={styles.warn}>
            {incomplete === 1
              ? '1 deal has incomplete pricing, so its recurring value is excluded. '
              : `${incomplete} deals have incomplete pricing, so their recurring value is excluded. `}
            The total above is a floor, not a ceiling.
          </Text>
        ) : null}
      </Card>

      {groups.length ? (
        <>
          <SectionHeader title="By stage" />
          {groups.map((g) => (
            <Row
              key={g.key}
              title={g.label}
              subtitle={stageFilter === g.stage ? 'Showing only these deals' : null}
              meta={`${g.count} ${g.count === 1 ? 'deal' : 'deals'}`}
              accent={stageFilter === g.stage ? palette.accent : undefined}
              onPress={() => setStageFilter(stageFilter === g.stage ? null : g.stage)}
              right={<Pill label={asMoney(g.value)} tone="accent" />}
            />
          ))}
        </>
      ) : null}

      {deals.length ? (
        <>
          <SectionHeader
            title={stageFilter
              ? `Deals · ${deals.length} · tap the stage again to clear`
              : `Deals · ${deals.length}`}
          />
          {deals.slice(0, 40).map((d) => (
            <Row
              key={d.key}
              title={d.company}
              subtitle={[
                d.stage,
                d.probabilityPct !== null ? `${d.probabilityPct}% likely` : null,
                d.incompleteReason,
              ].filter(Boolean).join(' · ') || null}
              onPress={d.opportunityId
                ? () => router.push(`/opportunity/${d.opportunityId}` as never)
                : undefined}
              right={d.value !== null
                ? <Pill label={asMoney(d.value)} tone="accent" />
                : undefined}
            />
          ))}
        </>
      ) : (
        <EmptyState title="No open deals" body="Nothing is in the pipeline right now." />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint, marginTop: space.sm },
  warn: { ...typography.caption, color: palette.warning, marginTop: space.sm },
});
