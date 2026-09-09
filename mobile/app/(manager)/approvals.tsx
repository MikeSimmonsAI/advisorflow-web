/**
 * APPROVAL QUEUE — the single most valuable manager screen on a phone.
 *
 * A rep is sitting with a customer and cannot send a proposal because the price
 * is under the floor. The manager is in a car. Ninety seconds either way is the
 * difference between closing today and "let me get back to you", and this is
 * the screen that removes the laptop from that sentence.
 *
 * THE BREACH IS STATED IN PLAIN TERMS. "Asking $8,400, floor is $9,000 — $600
 * under" is a decision a person can make at a traffic light. A raw record with
 * two currency fields is not, and a manager who cannot tell what they are
 * approving will approve it anyway, which is worse than a slow answer.
 *
 * BOTH ANSWERS ARE ONE TAP AND NEITHER IS THE DEFAULT. Approve is not styled as
 * the obvious action; a floor exists because somebody decided it should, and a
 * queue that makes yes easier than no stops being a control.
 */

import React, { useCallback, useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import { useQueryClient } from '@tanstack/react-query';

import { manager } from '../../src/api/endpoints';
import { asList, asMoney, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Button, Card, EmptyState, ErrorState, KeyValue, Loading, Screen, ScreenTitle,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';
import { palette, space, type as typography } from '../../src/theme/tokens';
import type { ApprovalRequest } from '../../src/api/types';

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
      await manager.decide(id, {
        decision: approved ? 'approved' : 'rejected',
        approved,
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

  const confirm = useCallback((req: ApprovalRequest, approved: boolean) => {
    const id = String(req.id);
    Alert.alert(
      approved ? 'Approve this price?' : 'Decline this price?',
      approved
        ? 'The rep can send the proposal immediately at the price they asked for.'
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

  const all = asList<ApprovalRequest>(query.data, 'approvals', 'requests', 'items');
  const pending = all.filter((a) => !a.status || String(a.status).toLowerCase() === 'pending');
  const decided = all.filter((a) => a.status && String(a.status).toLowerCase() !== 'pending');

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

      {pending.map((a) => {
        const asked = a.requested_amount;
        const floor = a.floor_amount;
        const gap = (asked != null && floor != null)
          ? Number(floor) - Number(asked) : null;
        return (
          <Card key={String(a.id)} accent={palette.warning}>
            <Text style={styles.title}>{String(a.opportunity_name ?? 'Pricing request')}</Text>
            {a.requested_by_name ? (
              <Text style={styles.sub}>
                {a.requested_by_name} · asked {relativeOf(a.created_at)}
              </Text>
            ) : null}

            <View style={styles.numbers}>
              <KeyValue label="They want to charge" value={asMoney(asked)} />
              <KeyValue label="Your floor" value={asMoney(floor)} />
              {gap != null && gap > 0 ? (
                <Text style={styles.breach}>{asMoney(gap)} under the floor</Text>
              ) : null}
            </View>

            {a.reason ? <Text style={styles.reason}>{String(a.reason)}</Text> : null}

            <View style={styles.row}>
              <Button
                label="Decline"
                variant="secondary"
                loading={busy === String(a.id)}
                onPress={() => confirm(a, false)}
                style={styles.flex}
              />
              <Button
                label="Approve"
                variant="secondary"
                loading={busy === String(a.id)}
                onPress={() => confirm(a, true)}
                style={styles.flex}
              />
            </View>
          </Card>
        );
      })}

      {decided.length ? (
        <>
          <Text style={styles.recent}>RECENTLY DECIDED</Text>
          {decided.slice(0, 8).map((a) => (
            <Card key={String(a.id)}>
              <Text style={styles.title}>{String(a.opportunity_name ?? 'Pricing request')}</Text>
              <Text style={styles.sub}>
                {String(a.status)} · {relativeOf(a.created_at)}
              </Text>
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
