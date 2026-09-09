/**
 * PROPOSAL — preview, and the two buttons that change the world.
 *
 * PUBLISH AND SEND ARE SEPARATE, AND SEND IS A SECOND DELIBERATE TAP. Sending a
 * proposal puts a number in front of a customer under the company's name; it is
 * not an autosave and it is not undoable by tapping again.
 *
 * BELOW-FLOOR PRICING DOES NOT ROUTE TO SEND. `pricing_policy_models` decides
 * what a rep may discount, and a proposal under the floor goes to
 * `POST /sales/proposals/{id}/pricing-request` for a manager to decide. This
 * screen SAYS SO BEFORE THE REP TRIES, rather than letting them tap Send and
 * receive a refusal — a rep who has already told the customer "it's on its way"
 * is in a worse position than one who was told up front.
 *
 * The pricing floor itself is never computed here. The server marks the
 * proposal; the phone reads the mark.
 */

import React, { useCallback, useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import { proposals } from '../../src/api/endpoints';
import { asList, asMoney, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  Button, Card, Divider, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { relativeOf } from '../../src/format';
import { palette, space, type as typography } from '../../src/theme/tokens';
import type { Proposal } from '../../src/api/types';

export default function ProposalDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const refresh = useScopedRefresh();
  const client = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);

  const query = useScopedQuery(['proposal', id], () => proposals.get(String(id)),
    { enabled: !!id });
  const activity = useScopedQuery(['proposal', id, 'activity'],
    () => proposals.activity(String(id)), { enabled: !!id });

  const act = useCallback(async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
      void client.invalidateQueries({ queryKey: ['proposal', id] });
      refresh();
    } catch (err) {
      Alert.alert('That did not go through',
        (err as { detail?: string })?.detail ?? 'Try again when you have signal.');
    } finally {
      setBusy(null);
    }
  }, [client, id, refresh]);

  if (query.isLoading) return <Screen><Loading label="Loading proposal" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const p = (query.data ?? {}) as Proposal;
  const blocks = asList<Record<string, unknown>>(p.blocks, 'blocks');
  const events = asList<Record<string, unknown>>(activity.data, 'activity', 'events', 'items');

  const belowFloor = !!p.is_below_floor;
  const requestState = p.pricing_request_status ? String(p.pricing_request_status) : null;
  const approved = requestState === 'approved';
  const published = !!p.published_at;
  const sent = !!p.sent_at;

  // The one rule this screen enforces in the UI, restated from the server:
  // below the floor, Send is not the next step — a pricing request is.
  const sendBlocked = belowFloor && !approved;

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title={String(p.title ?? `Proposal v${p.version ?? 1}`)}
        subtitle={String(p.status ?? (sent ? 'Sent' : published ? 'Published' : 'Draft'))}
      />

      <Card accent={sendBlocked ? palette.warning : undefined}>
        <KeyValue label="Total" value={p.total != null ? asMoney(p.total) : '—'} />
        <KeyValue label="Package" value={String(p.package_name ?? '—')} />
        <KeyValue label="Version" value={String(p.version ?? 1)} />
        {p.published_at ? (
          <KeyValue label="Published" value={relativeOf(p.published_at)} />
        ) : null}
        {p.sent_at ? <KeyValue label="Sent" value={relativeOf(p.sent_at)} /> : null}

        {belowFloor ? (
          <>
            <Divider />
            <View style={styles.pills}>
              <Pill label="Below the pricing floor" tone="warning" />
              {requestState ? (
                <Pill
                  label={`Approval: ${requestState}`}
                  tone={approved ? 'positive' : requestState === 'rejected' ? 'danger' : 'neutral'}
                />
              ) : null}
            </View>
            <Text style={styles.note}>
              {approved
                ? 'A manager approved this price, so it can be sent.'
                : 'This price is below what you can approve on your own. Ask for approval — sending is blocked until a manager decides.'}
            </Text>
          </>
        ) : null}
      </Card>

      <SectionHeader title="What the customer sees" />
      {blocks.length ? (
        blocks.map((b, i) => (
          <Card key={String(b.id ?? i)}>
            <Text style={styles.blockTitle}>
              {String(b.title ?? b.heading ?? b.type ?? `Section ${i + 1}`)}
            </Text>
            {b.body || b.content ? (
              <Text style={styles.blockBody}>{String(b.body ?? b.content)}</Text>
            ) : null}
          </Card>
        ))
      ) : (
        <Text style={styles.note}>
          This proposal has no content sections yet. Add them from a computer —
          the phone builds the deal, not the document.
        </Text>
      )}

      <SectionHeader title="Actions" />
      {!published ? (
        <Button
          label="Publish"
          loading={busy === 'publish'}
          onPress={() => void act('publish', () => proposals.publish(String(id)))}
        />
      ) : null}

      {sendBlocked ? (
        <Button
          label={requestState === 'pending' ? 'Approval requested' : 'Ask for approval'}
          variant="secondary"
          disabled={requestState === 'pending'}
          loading={busy === 'request'}
          onPress={() => void act('request', () =>
            proposals.requestPricingApproval(String(id),
              { reason: 'Requested from mobile' }))}
        />
      ) : (
        <Button
          label={sent ? 'Send again' : 'Send to the customer'}
          disabled={!published}
          loading={busy === 'send'}
          onPress={() => Alert.alert(
            sent ? 'Send this again?' : 'Send this proposal?',
            'The customer receives it immediately, under your company name.',
            [
              { text: 'Not yet', style: 'cancel' },
              {
                text: 'Send',
                onPress: () => void act('send', () => proposals.send(String(id), {})),
              },
            ],
          )}
        />
      )}

      {!published ? (
        <Text style={styles.note}>Publish before sending.</Text>
      ) : null}

      {p.opportunity_id ? (
        <Row
          title="Open the deal"
          onPress={() => router.push(`/opportunity/${p.opportunity_id}` as never)}
        />
      ) : null}

      {events.length ? (
        <>
          <SectionHeader title="Activity" />
          {events.slice(0, 12).map((e, i) => (
            <Row
              key={String(e.id ?? i)}
              title={String(e.event ?? e.type ?? 'Event')}
              meta={relativeOf(e.created_at ?? e.occurred_at)}
            />
          ))}
        </>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  pills: { flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' },
  note: { ...typography.caption, color: palette.textFaint },
  blockTitle: { ...typography.bodyStrong, color: palette.text },
  blockBody: { ...typography.body, color: palette.textMuted },
});
