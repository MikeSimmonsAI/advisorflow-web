/**
 * APPROVAL QUEUE — the single most valuable manager screen on a phone.
 *
 * A rep is sitting with a customer and cannot send a proposal because the price
 * is under the floor. The manager is in a car. Ninety seconds either way is the
 * difference between closing today and "let me get back to you", and this is
 * the screen that removes the laptop from that sentence.
 *
 * THE BREACH IS STATED IN PLAIN TERMS. "Asking $8,400, standard is $9,000 —
 * $600 under" is a decision a person can make at a traffic light. A raw record
 * with two currency fields is not, and a manager who cannot tell what they are
 * approving will approve it anyway, which is worse than a slow answer.
 *
 * BOTH ANSWERS ARE ONE TAP AND NEITHER IS THE DEFAULT. Approve is not styled as
 * the obvious action; a floor exists because somebody decided it should, and a
 * queue that makes yes easier than no stops being a control.
 *
 * ══ WHAT PASS 2 FOUND HERE, and it was not cosmetic ══
 *
 * THE APPROVE BUTTON DENIED. The screen posted
 * `{decision: 'approved', approved: true, note}`. The route reads exactly one
 * field — `approve = bool(body.get("approve"))` — which was absent, so it read
 * false. Every "Approve" tap was sent to the server as a REFUSAL. The empty
 * queue in Mike's screenshots is why nobody had hit it yet.
 *
 * AND EVERY FIGURE WOULD HAVE BEEN A DASH. The card read `requested_amount`,
 * `floor_amount`, `created_at` and `opportunity_name`. `request_out` sends
 * `base_amount`, `current_adjustment`, `requested_adjustment`,
 * `requested_total`, `requested_at` and `status_label`, and no name at all.
 * A manager would have been asked to approve a price with no prices on screen.
 *
 * The list shape was wrong too: the route returns `{pending, recent}`, and the
 * screen asked for `approvals`/`requests`/`items`, surviving only on `asList`'s
 * last-resort first-array rule — which is also why "recently decided" never
 * appeared.
 *
 * NO NEW APPROVAL ENGINE. Approving still calls the same
 * `/sales/manager/approvals/{id}/decide`, which applies the price through the
 * same `apply_pricing()` a manager could always call by hand.
 */

import React, { useCallback, useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import { manager } from '../../src/api/endpoints';
import { asList, asMoney, pick, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Button, Card, EmptyState, ErrorState, KeyValue, Loading, Screen, ScreenTitle,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';
import { humanText } from '../../src/vocab';
import { rowKey } from '../../src/keys';
import { palette, space, type as typography } from '../../src/theme/tokens';

type Rec = Record<string, unknown>;

const numOf = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};

export default function Approvals() {
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;
  const refresh = useScopedRefresh();
  const client = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);

  const query = useScopedQuery(['manager', 'approvals', brand],
    () => manager.approvals(brand));

  const decide = useCallback(async (id: string, approved: boolean) => {
    setBusy(id);
    try {
      // `approve` IS THE FIELD THE ROUTE READS. Nothing else in this body is
      // looked at, so nothing else is sent — a payload with three plausible
      // spellings of the same decision is how the wrong one gets read.
      await manager.decide(id, {
        approve: approved,
        note: approved ? 'Approved from mobile' : 'Declined from mobile',
      });
      void client.invalidateQueries({ queryKey: ['manager'] });
      refresh();
    } catch (err) {
      Alert.alert('That did not go through',
        (err as { detail?: string })?.detail ?? 'Try again when you have signal.');
    } finally {
      setBusy(null);
    }
  }, [client, refresh]);

  const confirm = useCallback((id: string, approved: boolean) => {
    Alert.alert(
      approved ? 'Approve this price?' : 'Decline this price?',
      approved
        ? 'The price is applied and the rep can send the proposal immediately.'
        : 'The rep is told, and the proposal stays blocked at that price.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: approved ? 'Approve' : 'Decline',
          style: approved ? 'default' : 'destructive',
          onPress: () => void decide(id, approved),
        },
      ],
    );
  }, [decide]);

  if (query.isLoading) return <Screen><Loading label="Loading approvals" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const data = (query.data ?? {}) as Rec;
  const pending = asList<Rec>(pick(data, 'pending'), 'pending', 'approvals', 'items');
  const decided = asList<Rec>(pick(data, 'recent'), 'recent');

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Approvals"
        subtitle={pending.length ? `${pending.length} waiting on you` : 'Nothing waiting'}
      />

      {!pending.length ? (
        <EmptyState
          title="Nobody is blocked"
          body="When a rep asks for a price below the floor, it appears here."
        />
      ) : null}

      {pending.map((a, i) => {
        const id = String(a.id ?? '');
        const standard = numOf(a.base_amount);
        const asked = numOf(a.requested_total);
        const adjustment = numOf(a.requested_adjustment);
        const already = numOf(a.current_adjustment);
        // The gap is the server's own arithmetic re-stated, not a second
        // calculation: requested_total = base_amount + requested_adjustment.
        const under = (adjustment !== null && adjustment < 0) ? Math.abs(adjustment)
          : (standard !== null && asked !== null && asked < standard) ? standard - asked
          : null;
        const opportunityId = a.opportunity_id ? String(a.opportunity_id) : null;

        return (
          <Card key={rowKey([a.id, a.opportunity_id], i)} accent={palette.warning}>
            <Text style={styles.title}>Price approval requested</Text>
            <Text style={styles.sub}>
              {[
                humanText(String(a.requested_by_name ?? ''), '') || null,
                a.requested_at ? `asked ${relativeOf(a.requested_at)}` : null,
              ].filter(Boolean).join(' · ') || 'Requested by a member of your team'}
            </Text>

            <View style={styles.numbers}>
              <KeyValue
                label="Standard price"
                value={standard !== null ? asMoney(standard) : 'Not recorded'}
              />
              {already !== null && already !== 0 ? (
                <KeyValue label="Already approved" value={asMoney(standard! + already)} />
              ) : null}
              <KeyValue
                label="They want to charge"
                value={asked !== null ? asMoney(asked) : 'Not recorded'}
              />
              {under !== null && under > 0 ? (
                <Text style={styles.breach}>{asMoney(under)} below standard</Text>
              ) : null}
            </View>

            {a.reason ? <Text style={styles.reason}>{String(a.reason)}</Text> : null}

            <View style={styles.row}>
              <Button
                label="Decline"
                variant="secondary"
                loading={busy === id}
                onPress={() => confirm(id, false)}
                style={styles.flex}
              />
              <Button
                label="Approve"
                variant="secondary"
                loading={busy === id}
                onPress={() => confirm(id, true)}
                style={styles.flex}
              />
            </View>

            {/* A THIRD OPTION THAT IS NOT A DECISION. Sometimes the answer is
                "I need to see the deal first", and a queue with only yes and
                no makes that a reason to close the app. */}
            {opportunityId ? (
              <Button
                label="Open the deal first"
                variant="ghost"
                onPress={() => router.push(`/opportunity/${opportunityId}` as never)}
              />
            ) : null}
          </Card>
        );
      })}

      {decided.length ? (
        <>
          <Text style={styles.recent}>RECENTLY DECIDED</Text>
          {decided.slice(0, 8).map((a, i) => (
            <Card key={rowKey([a.id, a.opportunity_id], i)}>
              <Text style={styles.title}>
                {asMoney(numOf(a.requested_total))} requested
              </Text>
              <Text style={styles.sub}>
                {[
                  // `status_label` is the server's phrase — "Approved",
                  // "Denied" — never the raw column.
                  humanText(String(a.status_label ?? ''), 'Decided'),
                  a.decided_by_name ? `by ${String(a.decided_by_name)}` : null,
                  a.decided_at ? relativeOf(a.decided_at) : null,
                ].filter(Boolean).join(' · ')}
              </Text>
              {a.decision_note ? (
                <Text style={styles.reason}>{String(a.decision_note)}</Text>
              ) : null}
            </Card>
          ))}
        </>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: { ...typography.bodyStrong, color: palette.text },
  sub: { ...typography.caption, color: palette.textMuted },
  numbers: { marginTop: space.sm },
  breach: { ...typography.bodyStrong, color: palette.warning, marginTop: space.xs },
  reason: { ...typography.caption, color: palette.textMuted, fontStyle: 'italic' },
  row: { flexDirection: 'row', gap: space.sm, marginTop: space.md },
  flex: { flex: 1 },
  recent: { ...typography.micro, color: palette.textFaint, marginTop: space.xl },
});
