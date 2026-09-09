/**
 * TEAM PIPELINE.
 *
 * `GET /sales/manager/pipeline-projection` does the projection. Stage
 * probabilities live in `compensation_models` and are applied server-side —
 * this screen does not multiply anything by anything. A weighted forecast
 * computed on a phone would be a second forecast, and the two would disagree in
 * the exact meeting where it mattered.
 *
 * Stages are rendered in the canonical order from `OPPORTUNITY_STAGES` so the
 * shape of the funnel reads the same here as on the desktop.
 */

import React from 'react';
import { StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';

import { manager } from '../../src/api/endpoints';
import {
  asCount, asList, asMoney, pick, useScopedQuery, useScopedRefresh,
} from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { nameOf } from '../../src/format';
import { OPEN_STAGES, stageLabel } from '../../src/vocab';
import { palette, space, type as typography } from '../../src/theme/tokens';
import type { Opportunity } from '../../src/api/types';

export default function Pipeline() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();

  const query = useScopedQuery(['manager', 'pipeline', brand],
    () => manager.pipeline({ brand_sales_org_id: brand, include_deals: true }));

  if (query.isLoading) return <Screen><Loading label="Loading pipeline" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const data = query.data ?? {};
  const byStage = (pick<Record<string, unknown>>(data, 'by_stage', 'stages') ?? {}) as
    Record<string, unknown>;
  const deals = asList<Opportunity>(pick(data, 'deals', 'opportunities'), 'deals', 'items');

  const total = pick(data, 'total_value', 'totals.value', 'pipeline_value');
  const weighted = pick(data, 'weighted_value', 'totals.weighted', 'projected_value');

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle title="Pipeline" subtitle={exp.detail ?? undefined} />

      <Card>
        <KeyValue label="Open pipeline" value={asMoney(total)} />
        <KeyValue label="Weighted" value={asMoney(weighted)} />
        <Text style={styles.note}>
          Weighted uses the platform&apos;s stage probabilities. Nothing on this
          screen is calculated on the phone.
        </Text>
      </Card>

      <SectionHeader title="By stage" />
      {OPEN_STAGES.map((stage) => {
        const entry = byStage[stage] as Record<string, unknown> | number | undefined;
        if (entry === undefined) return null;
        const count = typeof entry === 'number' ? entry : asCount(pick(entry, 'count'));
        const value = typeof entry === 'number' ? undefined : pick(entry, 'value', 'total');
        return (
          <Row
            key={stage}
            title={stageLabel(stage)}
            meta={`${count} ${count === 1 ? 'deal' : 'deals'}`}
            right={value != null ? <Pill label={asMoney(value)} tone="accent" /> : undefined}
          />
        );
      })}

      {deals.length ? (
        <>
          <SectionHeader title={`Deals · ${deals.length}`} />
          {deals.slice(0, 40).map((d) => (
            <Row
              key={String(d.id)}
              title={nameOf(d, 'Opportunity')}
              subtitle={[stageLabel(d.stage), d.owner_name].filter(Boolean).join(' · ') || null}
              onPress={() => router.push(`/opportunity/${d.id}` as never)}
              right={d.amount != null ? <Pill label={asMoney(d.amount)} tone="accent" /> : undefined}
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
});
