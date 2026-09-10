/**
 * SALES · PIPELINE — my deals, by stage, in one thumb-reachable list.
 *
 * This tab replaced Pay in the bar. Compensation is important but it is a thing
 * a rep checks weekly; the pipeline is the thing they work hourly, and it had
 * no home at all — My Day linked to it and there was nowhere to land.
 * Compensation moved to More (see HIDDEN_ROUTES) rather than being removed.
 *
 * ONE ENDPOINT, WHICH ALREADY KNOWS THE STAGES. `/sales/opportunities` returns
 * `stages` (key + label + count) alongside the rows, so the filter bar is the
 * server's own vocabulary rather than a copy of it kept in the app. A stage
 * renamed on the platform renames here with no release.
 *
 * SCOPE IS THE SERVER'S. No `owner_user_id` is sent: `/sales/opportunities`
 * already narrows to the caller unless they are a manager, and asking for
 * somebody else's deals from a phone is not a thing this screen can do.
 */

import React, { useMemo, useState } from 'react';
import { router } from 'expo-router';

import { sales } from '../../src/api/endpoints';
import { asList, asMoney, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { listField } from '../../src/api/state';
import { FilterChips, SectionRetry, Skeleton } from '../../src/components/data';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  EmptyState, Pill, Row, Screen, ScreenTitle,
} from '../../src/components/ui';
import { nameOf, relativeOf, whenOf } from '../../src/format';
import { stageLabel } from '../../src/vocab';
import { palette } from '../../src/theme/tokens';
import type { Opportunity } from '../../src/api/types';

export default function SalesPipeline() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();
  const [stage, setStage] = useState<string>('all');

  const query = useScopedQuery(['pipeline', brand], () =>
    sales.opportunities({ brand_sales_org_id: brand }));

  const opps = listField<Opportunity>(query, 'opportunities', 'items');
  const rows = opps.value ?? [];

  // The server's stage vocabulary, with its own counts.
  const stages = useMemo(() => {
    const raw = asList<Record<string, unknown>>(pick(query.data, 'stages'), 'items');
    const total = rows.length;
    return [
      { key: 'all', label: 'All', count: total },
      ...raw
        .map((s) => ({
          key: String(pick(s, 'key') ?? ''),
          label: String(pick(s, 'label') ?? pick(s, 'key') ?? ''),
          count: Number(pick(s, 'count') ?? 0),
        }))
        .filter((s) => s.key && s.count > 0),
    ];
  }, [query.data, rows.length]);

  const shown = useMemo(() => {
    const base = stage === 'all' ? rows : rows.filter((o) => String(o.stage) === stage);
    // Oldest movement first: the deal nobody has touched is the one at risk.
    return [...base].sort((a, b) => {
      const at = new Date(String(pick(a as Record<string, unknown>,
        'stage_changed_at', 'updated_at') ?? 0)).getTime();
      const bt = new Date(String(pick(b as Record<string, unknown>,
        'stage_changed_at', 'updated_at') ?? 0)).getTime();
      return at - bt;
    });
  }, [rows, stage]);

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Pipeline"
        subtitle={opps.state === 'ready' || opps.state === 'zero'
          ? `${rows.length} open deal${rows.length === 1 ? '' : 's'}`
          : undefined}
      />

      <SectionRetry
        show={opps.state === 'error'}
        onRetry={refresh}
        note={opps.reason ?? 'Your pipeline could not be loaded.'}
      />

      {stages.length > 1 ? (
        <FilterChips value={stage} onChange={setStage} options={stages} />
      ) : null}

      {opps.state === 'loading' ? <Skeleton rows={4} /> : null}

      {opps.state !== 'loading' && !shown.length ? (
        <EmptyState
          title={stage === 'all' ? 'No open deals' : 'Nothing in this stage'}
          body={stage === 'all'
            ? 'When a deal is created it will appear here.'
            : undefined}
        />
      ) : null}

      {shown.map((o) => {
        const rec = o as unknown as Record<string, unknown>;
        const moved = pick(rec, 'stage_changed_at', 'updated_at');
        return (
          <Row
            key={String(o.id)}
            title={nameOf(rec, 'Opportunity')}
            subtitle={stageLabel(o.stage)}
            meta={[
              moved ? `Moved ${relativeOf(moved)}` : null,
              o.expected_close_date ? `Close ${whenOf(o.expected_close_date)}` : null,
            ].filter(Boolean).join(' · ') || null}
            accent={pick(rec, 'needs_action') ? palette.warning : undefined}
            onPress={() => router.push(`/opportunity/${o.id}` as never)}
            right={o.amount != null
              ? <Pill label={asMoney(o.amount)} tone="accent" />
              : undefined}
          />
        );
      })}
    </Screen>
  );
}
