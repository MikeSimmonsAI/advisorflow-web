/**
 * APPOINTMENT DETAIL — the screen a rep opens in a car outside the building.
 *
 * It answers four things in order: when, where, who, and what do I do when it
 * is over. Directions and the join link are one tap because that is the reason
 * this screen gets opened at all.
 *
 * OUTCOMES WRITE THE CANONICAL VOCABULARY. Completed / cancelled / no-show are
 * `APPOINTMENT_STATUSES` from `scheduling_models.py`, and confirmation states
 * are `CONFIRMATION_STATUSES`. There is no mobile-only "went well" flag: a
 * second outcome model would be invisible to every report the platform already
 * has.
 *
 * DIRECTIONS OPEN THE PHONE'S MAP. Deliberately platform-native — Apple Maps on
 * iOS, whatever handles geo: on Android — rather than a web map inside the app.
 * A rep needs turn-by-turn with their car's audio, not a picture of a map.
 */

import React, { useCallback, useState } from 'react';
import { Alert, Linking, Platform, StyleSheet, Text, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import { scheduling } from '../../src/api/endpoints';
import { useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  Button, Card, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { ContactActions } from '../../src/components/ContactActions';
import { dayOf, timeOf, whenOf } from '../../src/format';
import {
  APPOINTMENT_STATUS_LABELS, CONFIRMATION_LABELS,
} from '../../src/vocab';
import { palette, space, type as typography } from '../../src/theme/tokens';
import type { Appointment } from '../../src/api/types';

export default function AppointmentDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const refresh = useScopedRefresh();
  const client = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);

  const query = useScopedQuery(['appointment', id],
    () => scheduling.appointment(String(id)), { enabled: !!id });

  const act = useCallback(async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
      void client.invalidateQueries({ queryKey: ['appointment', id] });
      refresh();
    } catch (err) {
      Alert.alert('That did not go through',
        (err as { detail?: string })?.detail ?? 'Try again when you have signal.');
    } finally {
      setBusy(null);
    }
  }, [client, id, refresh]);

  const openDirections = useCallback((location: string) => {
    const q = encodeURIComponent(location);
    const url = Platform.select({
      ios: `maps:0,0?q=${q}`,
      android: `geo:0,0?q=${q}`,
      default: `https://maps.google.com/?q=${q}`,
    }) as string;
    void Linking.openURL(url).catch(() => {
      void Linking.openURL(`https://maps.google.com/?q=${q}`);
    });
  }, []);

  if (query.isLoading) return <Screen><Loading label="Loading appointment" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const a = (query.data ?? {}) as Appointment;
  const status = String(a.status ?? 'scheduled');
  const isOpen = status === 'scheduled';

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title={String(a.title ?? a.meeting_type ?? 'Appointment')}
        subtitle={whenOf(a.starts_at)}
      />

      <Card accent={isOpen ? palette.accent : palette.neutral}>
        <View style={styles.pills}>
          <Pill
            label={APPOINTMENT_STATUS_LABELS[status] ?? status}
            tone={status === 'completed' ? 'positive'
              : status === 'no_show' ? 'danger'
                : status === 'cancelled' ? 'neutral' : 'accent'}
          />
          {a.confirmation_status ? (
            <Pill
              label={CONFIRMATION_LABELS[String(a.confirmation_status)]
                ?? String(a.confirmation_status)}
              tone={a.confirmation_status === 'confirmed' ? 'positive'
                : a.confirmation_status === 'declined' ? 'danger' : 'warning'}
            />
          ) : null}
        </View>
        <KeyValue label="Date" value={dayOf(a.starts_at)} />
        <KeyValue label="Time" value={`${timeOf(a.starts_at)} – ${timeOf(a.ends_at)}`} />
        {a.location ? <KeyValue label="Where" value={String(a.location)} /> : null}
        {a.prospect_name ? <KeyValue label="Who" value={String(a.prospect_name)} /> : null}
      </Card>

      {a.location || a.meeting_url ? (
        <View style={styles.row}>
          {a.location ? (
            <Button
              label="Directions"
              variant="secondary"
              onPress={() => openDirections(String(a.location))}
              style={styles.flex}
            />
          ) : null}
          {a.meeting_url ? (
            <Button
              label="Join"
              onPress={() => void Linking.openURL(String(a.meeting_url))}
              style={styles.flex}
            />
          ) : null}
        </View>
      ) : null}

      <SectionHeader title="Reach them" />
      <Card>
        <ContactActions
          phone={a.prospect_phone as string | undefined}
          email={a.prospect_email as string | undefined}
          opportunityId={(a.opportunity_id as string | undefined) ?? null}
          personLabel={a.prospect_name as string | undefined}
          onLogged={refresh}
        />
      </Card>

      {a.opportunity_id ? (
        <>
          <SectionHeader title="Deal" />
          <Row
            title={String(a.opportunity_name ?? 'Opportunity')}
            subtitle="Open the deal"
            onPress={() => router.push(`/opportunity/${a.opportunity_id}` as never)}
          />
        </>
      ) : null}

      {isOpen ? (
        <>
          <SectionHeader title="Confirmation" />
          <Card>
            <Text style={styles.note}>
              Record what the prospect told you. This writes the same
              confirmation status the desktop and the prospect link write.
            </Text>
            <View style={styles.row}>
              <Button
                label="Confirmed"
                variant="secondary"
                loading={busy === 'confirmed'}
                onPress={() => void act('confirmed', () =>
                  scheduling.setConfirmation(String(id),
                    { confirmation_status: 'confirmed', source: 'staff_manual' }))}
                style={styles.flex}
              />
              <Button
                label="Declined"
                variant="secondary"
                loading={busy === 'declined'}
                onPress={() => void act('declined', () =>
                  scheduling.setConfirmation(String(id),
                    { confirmation_status: 'declined', source: 'staff_manual' }))}
                style={styles.flex}
              />
            </View>
            <Button
              label="Resend the invitation"
              variant="ghost"
              loading={busy === 'resend'}
              onPress={() => void act('resend', () => scheduling.resendInvitation(String(id)))}
            />
          </Card>

          <SectionHeader title="Outcome" />
          <Card>
            <Button
              label="Completed"
              loading={busy === 'completed'}
              onPress={() => void act('completed', () =>
                scheduling.setConfirmation(String(id), { status: 'completed' }))}
            />
            <Button
              label="No show"
              variant="secondary"
              loading={busy === 'no_show'}
              onPress={() => void act('no_show', () =>
                scheduling.setConfirmation(String(id), { status: 'no_show' }))}
            />
            <Button
              label="Cancel this appointment"
              variant="danger"
              loading={busy === 'cancel'}
              onPress={() => Alert.alert(
                'Cancel this appointment?',
                'The prospect is notified the same way as from the desktop.',
                [
                  { text: 'Keep it', style: 'cancel' },
                  {
                    text: 'Cancel it',
                    style: 'destructive',
                    onPress: () => void act('cancel', () =>
                      scheduling.cancel(String(id), { reason: 'Cancelled from mobile' })),
                  },
                ],
              )}
            />
          </Card>
        </>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  pills: { flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' },
  row: { flexDirection: 'row', gap: space.sm },
  flex: { flex: 1 },
  note: { ...typography.caption, color: palette.textFaint },
});
