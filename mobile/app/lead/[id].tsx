/**
 * LEAD DETAIL + CONVERSATION (advisor experience).
 *
 * This is the one place in the app where a message is genuinely SENT AND
 * LOGGED, because the tenant messaging stack exists and an advisor is a tenant
 * user. `POST /sms/send` writes a Message row, the timeline shows it, and the
 * cadence knows about it. Contrast the brand-sales side, where the same buttons
 * hand off to the phone and say so.
 *
 * The id comes from a route parameter — a deep link, a push tap, a list row.
 * `GET /leads/{id}` scopes to the caller's organisation regardless
 * (`lead_scope`), so an id belonging to another tenant ends in a refusal that
 * this screen renders as one rather than as an empty record.
 */

import React, { useCallback, useState } from 'react';
import { Alert, StyleSheet, Text, TextInput } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import { advisor } from '../../src/api/endpoints';
import { asList, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  Button, Card, Divider, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { ContactActions } from '../../src/components/ContactActions';
import { nameOf, relativeOf } from '../../src/format';
import { palette, radius, space, type as typography, HIT_SIZE } from '../../src/theme/tokens';
import type { Lead } from '../../src/api/types';
import { rowKey } from '../../src/keys';

export default function LeadDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const refresh = useScopedRefresh();
  const client = useQueryClient();

  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);

  const lead = useScopedQuery(['lead', id], () => advisor.lead(String(id)), { enabled: !!id });
  const timeline = useScopedQuery(['lead', id, 'timeline'],
    () => advisor.leadTimeline(String(id)), { enabled: !!id });

  const send = useCallback(async () => {
    const body = message.trim();
    if (!body || !id) return;
    setSending(true);
    try {
      await advisor.sendSms({ lead_id: String(id), message: body, body });
      setMessage('');
      void client.invalidateQueries({ queryKey: ['lead', id] });
      refresh();
    } catch (err) {
      // A text is NOT queued offline. A message the person believes went out
      // ninety minutes ago, arriving now, is worse than one that plainly did
      // not send — especially in this business, where the message may be to a
      // family that has just had a death.
      Alert.alert('Message not sent',
        (err as { detail?: string })?.detail
        ?? 'This did not send. Nothing was delivered — try again when you have signal.');
    } finally {
      setSending(false);
    }
  }, [message, id, client, refresh]);

  if (lead.isLoading) return <Screen><Loading label="Loading lead" /></Screen>;
  if (lead.isError) return <Screen><ErrorState error={lead.error} onRetry={refresh} /></Screen>;

  const l = (lead.data ?? {}) as Lead;
  const events = asList<Record<string, unknown>>(timeline.data, 'timeline', 'events', 'items');

  return (
    <Screen refreshing={lead.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title={nameOf(l as Record<string, unknown>, 'Lead')}
        subtitle={[l.status, l.tier].filter(Boolean).join(' · ') || undefined}
      />

      <Card>
        <KeyValue label="Phone" value={String(l.phone ?? '—')} />
        <KeyValue label="Email" value={String(l.email ?? '—')} />
        <KeyValue
          label="Last contact"
          value={l.last_contacted_at ? relativeOf(l.last_contacted_at) : 'Never'}
        />
        <Divider />
        <ContactActions
          phone={l.phone}
          email={l.email}
          personLabel={nameOf(l as Record<string, unknown>, 'this lead')}
        />
      </Card>

      <SectionHeader title="Send a text" />
      <Card>
        <TextInput
          value={message}
          onChangeText={setMessage}
          placeholder="Write a message"
          placeholderTextColor={palette.textFaint}
          multiline
          style={styles.input}
          accessibilityLabel="Message"
        />
        <Button
          label="Send text"
          onPress={() => void send()}
          disabled={!message.trim()}
          loading={sending}
        />
        <Text style={styles.note}>
          Sent through EvoSys Pro and recorded on this lead's timeline.
        </Text>
      </Card>

      <SectionHeader title="Timeline" />
      {timeline.isLoading ? <Loading label="" /> : null}
      {!events.length && !timeline.isLoading ? (
        <Text style={styles.note}>Nothing has happened on this lead yet.</Text>
      ) : null}
      {events.slice(0, 40).map((e, i) => (
        <Row
          key={rowKey([e.id], i)}
          title={String(e.title ?? e.type ?? e.event ?? 'Activity')}
          subtitle={typeof e.body === 'string' ? e.body
            : typeof e.message === 'string' ? e.message
              : typeof e.description === 'string' ? e.description : null}
          meta={relativeOf(e.created_at ?? e.occurred_at ?? e.timestamp)}
          right={e.direction
            ? <Pill label={String(e.direction)}
                    tone={String(e.direction) === 'inbound' ? 'accent' : 'neutral'} />
            : undefined}
        />
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: {
    minHeight: HIT_SIZE * 2,
    backgroundColor: palette.surfaceSunken,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    padding: space.lg,
    color: palette.text,
    ...typography.body,
    textAlignVertical: 'top',
  },
  note: { ...typography.caption, color: palette.textFaint },
});
