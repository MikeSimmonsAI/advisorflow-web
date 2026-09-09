/**
 * SIGNED-IN DEVICES.
 *
 * This screen could not exist before per-device sessions, because there was
 * only ever one and it was a column. It is worth having for two reasons that
 * have nothing to do with novelty:
 *
 *   1. IT MAKES THE NEW BEHAVIOUR LEGIBLE. "My phone and my laptop are both
 *      signed in" is a claim; a list with two rows on it is the evidence.
 *   2. IT IS THE LOST-PHONE BUTTON. Ending one device from another is the thing
 *      a person actually needs at the moment they need it, and it does not
 *      require an administrator.
 *
 * `GET /auth/sessions` is scoped to the caller in the query itself and never
 * returns a jti. Revoking filters on user_id in the same query, so naming
 * somebody else's session id returns 404 and confirms nothing.
 */

import React, { useCallback, useState } from 'react';
import { Alert, StyleSheet, Text } from 'react-native';

import { auth } from '../src/api/endpoints';
import { useAuth } from '../src/auth/AuthContext';
import { useScopedQuery, useScopedRefresh } from '../src/hooks/useApi';
import {
  Button, EmptyState, ErrorState, Loading, Pill, Row, Screen, ScreenTitle,
} from '../src/components/ui';
import { relativeOf, whenOf } from '../src/format';
import { palette, space, type as typography } from '../src/theme/tokens';
import type { DeviceSession } from '../src/api/types';

const CLIENT_LABEL: Record<string, string> = {
  ios: 'iPhone or iPad',
  android: 'Android device',
  web: 'Web browser',
  unknown: 'Device',
};

export default function Sessions() {
  const refresh = useScopedRefresh();
  const { signOutEverywhere } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);

  const query = useScopedQuery(['auth', 'sessions'], () => auth.sessions());

  const revoke = useCallback(async (session: DeviceSession) => {
    setBusy(session.id);
    try {
      await auth.revokeSession(session.id);
      refresh();
    } catch {
      Alert.alert('Could not sign that device out', 'Try again when you have signal.');
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const confirmRevoke = useCallback((session: DeviceSession) => {
    const name = session.device_name ?? CLIENT_LABEL[session.client] ?? 'that device';
    Alert.alert(
      `Sign out ${name}?`,
      session.is_current
        ? 'This is the device you are using. You will be signed out here.'
        : 'That device will be signed out on its next request. Nothing else changes.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Sign out', style: 'destructive', onPress: () => void revoke(session) },
      ],
    );
  }, [revoke]);

  if (query.isLoading) return <Screen><Loading label="Loading your devices" /></Screen>;
  if (query.isError) return <Screen><ErrorState error={query.error} onRetry={refresh} /></Screen>;

  const sessions = query.data?.sessions ?? [];

  return (
    <Screen refreshing={query.isFetching} onRefresh={refresh}>
      <ScreenTitle
        title="Signed-in devices"
        subtitle={`${sessions.length} ${sessions.length === 1 ? 'device' : 'devices'}`}
      />

      {!sessions.length ? (
        <EmptyState title="Nothing to show" body="No other device is signed in." />
      ) : null}

      {sessions.map((s) => (
        <Row
          key={s.id}
          title={s.device_name ?? CLIENT_LABEL[s.client] ?? 'Device'}
          subtitle={[
            CLIENT_LABEL[s.client] ?? s.client,
            s.app_version ? `v${s.app_version}` : null,
          ].filter(Boolean).join(' · ')}
          meta={[
            s.last_seen_at ? `Last used ${relativeOf(s.last_seen_at)}` : null,
            s.expires_at ? `Expires ${whenOf(s.expires_at)}` : null,
          ].filter(Boolean).join(' · ')}
          accent={s.is_current ? palette.accent : undefined}
          onPress={() => { if (busy !== s.id) confirmRevoke(s); }}
          right={s.is_current ? <Pill label="This device" tone="accent" /> : undefined}
        />
      ))}

      <Text style={styles.note}>
        Tap a device to sign it out. Signing out one device leaves the others
        alone.
      </Text>

      <Button
        label="Sign out everywhere"
        variant="danger"
        onPress={() => void signOutEverywhere()}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  note: { ...typography.caption, color: palette.textFaint, marginTop: space.sm },
});
