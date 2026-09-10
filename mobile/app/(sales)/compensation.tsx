/**
 * COMPENSATION — the rep's own, and only their own.
 *
 * One endpoint: `GET /sales/compensation/me`, guarded by `require_sales_member`
 * and taking NO parameters. That is not an oversight in the API and it is not
 * something to work around: there is no way to ask this route about another
 * person, so there is no way for this screen to show one. The team-wide ledger,
 * the payables run and the settlement tools all exist and are all deliberately
 * absent from the phone.
 *
 * NO SHADOW COMPENSATION MATHS. Not a total, not a projection, not a "what if
 * this deal closes" number. Commission is computed by `compensation_ledger`
 * against a plan with rules, caps and stage probabilities; a phone that added
 * up the numbers it happened to receive would produce a second answer, and the
 * second answer is the one the rep would remember on payday.
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { compensation } from '../../src/api/endpoints';
import { asList, asMoney, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  Card, EmptyState, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { dayOf, relativeOf } from '../../src/format';
import { palette, space, type as typography } from '../../src/theme/tokens';
import { rowKey } from '../../src/keys';

export default function Compensation() {
  const refresh = useScopedRefresh();
  const query = useScopedQuery(['compensation', 'me'], () => compensation.me());

  if (query.isLoading) return <Screen><Loading label="Loading your compensation" /></Screen>;
  if (query.isError) {
    // A rep whose brand has no compensation plan configured gets a refusal from
    // the router rather than a zero. Saying "not set up" is the honest reading;
    // rendering $0 would be a number the rep would believe.
    return (
      <Screen>
        <ScreenTitle title="Pay" />
        <ErrorState error={query.error} onRetry={refresh} />
      </Screen>
    );
  }

  const data = query.data ?? {};
  const plan = pick<string>(data, 'plan_name', 'plan.name');
  const entries = asList<Record<string, unknown>>(
    pick(data, 'entries', 'ledger', 'items'), 'entries', 'items');

  const earned = pick(data, 'earned_total', 'totals.earned', 'earned');
  const pending = pick(data, 'pending_total', 'totals.pending', 'pending');
  const paid = pick(data, 'paid_total', 'totals.paid', 'paid');

  const hasTotals = earned !== undefined || pending !== undefined || paid !== undefined;

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Pay"
        subtitle={plan ? `Plan: ${plan}` : 'Your compensation'}
      />

      {hasTotals ? (
        <Card>
          <KeyValue label="Earned" value={asMoney(earned)} />
          <KeyValue label="Pending collection" value={asMoney(pending)} />
          <KeyValue label="Paid" value={asMoney(paid)} />
          <Text style={styles.note}>
            These figures come from the platform ledger. Nothing on this screen
            is calculated on your phone.
          </Text>
        </Card>
      ) : null}

      <SectionHeader title="Entries" />
      {!entries.length ? (
        <EmptyState
          title="No entries yet"
          body="A commission entry appears here once a deal is won and a payment is recorded against it."
        />
      ) : null}

      {entries.map((e, i) => {
        const status = String(e.status ?? e.state ?? '');
        return (
          <Row
            key={rowKey([e.id], i)}
            title={String(e.opportunity_name ?? e.description ?? 'Commission')}
            subtitle={typeof e.compensation_kind === 'string' ? e.compensation_kind : null}
            meta={e.created_at ? `${dayOf(e.created_at)} · ${relativeOf(e.created_at)}` : null}
            right={
              <View style={styles.right}>
                <Text style={styles.amount}>{asMoney(e.amount ?? e.value)}</Text>
                {status ? (
                  <Pill
                    label={status}
                    tone={status.includes('paid') ? 'positive'
                      : status.includes('due') ? 'accent' : 'neutral'}
                  />
                ) : null}
              </View>
            }
          />
        );
      })}
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint, marginTop: space.sm },
  amount: { ...typography.bodyStrong, color: palette.text },
  right: { alignItems: 'flex-end', gap: space.xs },
});
