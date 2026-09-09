/**
 * MORE — the fifth tab in every experience, and the same one in each.
 *
 * It holds the things that are true about the PERSON and the DEVICE rather than
 * about the work: who you are signed in as, which experience you are in, what
 * this phone is registered for, and how to leave. Duplicating it per experience
 * would be five places for "sign out" to drift.
 *
 * Two sign-out buttons, and the difference is stated in words rather than left
 * to be discovered. Before per-device sessions there was only one behaviour and
 * it was the destructive one: signing out of the phone ended the desktop
 * session too. Now that both are possible, which one you get should not be a
 * surprise.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, Switch, Text, View } from 'react-native';
import { router } from 'expo-router';

import { useAuth } from '../auth/AuthContext';
import { useExperience } from '../experience/ExperienceContext';
import { biometrics } from '../auth/biometrics';
import { secureStore } from '../auth/secureStore';
import { devices } from '../api/endpoints';
import { outbox, type QueuedItem } from '../offline/queue';
import { isFullyLogged } from '../comms/communications';
import {
  Button, Card, Divider, KeyValue, Row, Screen, ScreenTitle, SectionHeader,
} from './ui';
import { palette, space, type as typography } from '../theme/tokens';

export function MoreScreen() {
  const { fullName, signOut, signOutEverywhere } = useAuth();
  const { active, experiences } = useExperience();

  const [bioAvailable, setBioAvailable] = useState(false);
  const [bioLabel, setBioLabel] = useState('Biometric unlock');
  const [bioOn, setBioOn] = useState(false);
  const [pushEnabled, setPushEnabled] = useState<boolean | null>(null);
  const [uploads, setUploads] = useState<{ durable: boolean; reason: string | null } | null>(null);
  const [queued, setQueued] = useState<QueuedItem[]>([]);

  useEffect(() => {
    void (async () => {
      const available = await biometrics.isAvailable();
      setBioAvailable(available);
      if (available) setBioLabel(await biometrics.describe());
      setBioOn(await secureStore.getBiometricPreference());
    })();
    return outbox.subscribe(setQueued);
  }, []);

  useEffect(() => {
    // Both of these are informational and both are allowed to fail quietly —
    // this screen must open on a phone with no signal.
    void devices.list().then((r) => setPushEnabled(!!r.push_enabled)).catch(() => setPushEnabled(null));
    void devices.uploadCapability()
      .then((r) => setUploads({ durable: r.durable, reason: r.reason }))
      .catch(() => setUploads(null));
  }, []);

  const toggleBiometrics = useCallback(async (next: boolean) => {
    setBioOn(next);
    await secureStore.setBiometricPreference(next);
  }, []);

  const confirmSignOutEverywhere = useCallback(() => {
    Alert.alert(
      'Sign out on every device?',
      'This ends your session here, on your computer, and anywhere else you are signed in. Use it if a device was lost.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Sign out everywhere',
          style: 'destructive',
          onPress: () => void signOutEverywhere(),
        },
      ],
    );
  }, [signOutEverywhere]);

  return (
    <Screen>
      <ScreenTitle title="More" subtitle={fullName ?? undefined} />

      <SectionHeader title="Experience" />
      <Card>
        <KeyValue label="Signed in as" value={fullName ?? '—'} />
        <KeyValue label="Current" value={active ? `${active.label}${active.detail ? ` · ${active.detail}` : ''}` : '—'} />
        <Divider />
        <Button
          label={experiences.length > 1 ? 'Switch experience' : 'View experiences'}
          variant="secondary"
          onPress={() => router.push('/switch' as never)}
        />
      </Card>

      <SectionHeader title="This device" />
      {bioAvailable ? (
        <Card>
          <View style={styles.switchRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.switchLabel}>{bioLabel}</Text>
              <Text style={styles.switchHint}>
                Ask for {bioLabel.toLowerCase()} when the app has been closed for
                a couple of minutes. Your password still works.
              </Text>
            </View>
            <Switch
              value={bioOn}
              onValueChange={(v) => void toggleBiometrics(v)}
              trackColor={{ true: palette.accent, false: palette.border }}
            />
          </View>
        </Card>
      ) : null}

      <Row
        title="Signed-in devices"
        subtitle="See everywhere you are signed in, and sign out one of them"
        onPress={() => router.push('/sessions' as never)}
      />

      <Card>
        <KeyValue
          label="Push notifications"
          value={pushEnabled === null ? 'Unknown'
            : pushEnabled ? 'On for this account' : 'Not switched on yet'}
        />
        <KeyValue
          label="Photo attachments"
          value={uploads === null ? 'Unknown'
            : uploads.durable ? 'Available' : 'Unavailable'}
        />
        {uploads && !uploads.durable && uploads.reason ? (
          <Text style={styles.note}>{uploads.reason}</Text>
        ) : null}
        {!isFullyLogged ? (
          <Text style={styles.note}>
            Calls and texts are placed by your phone. EvoSys Pro records that
            they happened against the deal, not what was said.
          </Text>
        ) : null}
      </Card>

      {queued.length ? (
        <>
          <SectionHeader title={`Waiting to send · ${queued.length}`} />
          {queued.map((q) => (
            <Row
              key={q.id}
              title={q.label}
              subtitle={q.lastError ?? 'Not sent yet'}
              meta={`Queued ${new Date(q.createdAt).toLocaleTimeString()}`}
              accent={palette.warning}
            />
          ))}
          <Button
            label="Try sending now"
            variant="secondary"
            onPress={() => void outbox.flush()}
          />
        </>
      ) : null}

      <SectionHeader title="Account" />
      <Button label="Sign out of this device" variant="secondary" onPress={() => void signOut()} />
      <Text style={styles.note}>
        Your computer and any other device stay signed in.
      </Text>
      <Button label="Sign out everywhere" variant="danger" onPress={confirmSignOutEverywhere} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: space.lg },
  switchLabel: { ...typography.bodyStrong, color: palette.text },
  switchHint: { ...typography.caption, color: palette.textMuted, marginTop: 2 },
  note: { ...typography.caption, color: palette.textFaint },
});
