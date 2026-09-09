/**
 * EXECUTIVE · ORGANIZATIONS — the portfolio, and only the portfolio.
 *
 * `GET /executive/organizations` returns what `portfolio_authority()` says this
 * executive may see. If that list is empty, the list is empty; the app does not
 * fall back to "everything on the brand", because that fallback is precisely
 * the bug the portfolio model was built to prevent.
 *
 * Severity is rendered through `normaliseSeverity`, which turns anything
 * unrecognised into `unavailable` rather than `healthy`. A screen that cannot
 * reach the truth must not draw a green tick next to a customer's name.
 */

import React from 'react';
import { router } from 'expo-router';

import { executive } from '../../src/api/endpoints';
import { asList, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  EmptyState, ErrorState, Loading, Row, Screen, ScreenTitle, SeverityPill,
} from '../../src/components/ui';
import { normaliseSeverity } from '../../src/vocab';
import { palette } from '../../src/theme/tokens';
import type { ExecutiveOrg } from '../../src/api/types';

export default function ExecutiveOrganizations() {
  const refresh = useScopedRefresh();
  const query = useScopedQuery(['executive', 'organizations'],
    () => executive.organizations());

  if (query.isLoading) return <Screen><Loading label="Loading organizations" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const orgs = asList<ExecutiveOrg>(query.data, 'organizations', 'items');

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Organizations"
        subtitle={`${orgs.length} assigned to you`}
      />

      {!orgs.length ? (
        <EmptyState
          title="Nothing assigned"
          body="Executive access is per organization. Until one is assigned to you, there is nothing here — being on the same brand does not grant access."
        />
      ) : null}

      {orgs.map((o, i) => {
        const id = String(o.organization_id ?? o.id ?? i);
        const severity = normaliseSeverity(String(o.severity ?? o.health ?? ''));
        return (
          <Row
            key={id}
            title={String(o.organization_name ?? o.name ?? 'Organization')}
            subtitle={typeof o.plan === 'string' ? o.plan
              : typeof o.status === 'string' ? o.status : null}
            accent={severity === 'action_required' ? palette.danger
              : severity === 'attention' ? palette.warning : undefined}
            onPress={() => router.push(`/org/${id}` as never)}
            right={<SeverityPill value={severity} />}
          />
        );
      })}
    </Screen>
  );
}
