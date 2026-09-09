/**
 * The biometric lock.
 *
 * The session is still valid behind this screen — the token is in the Keychain
 * and the server would accept it. This is a door, not a credential check, which
 * is why "Sign out instead" is always available: a person who cannot present a
 * face is not locked out of their account, only out of this shortcut.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useAuth } from '../auth/AuthContext';
import { biometrics } from '../auth/biometrics';
import { Button } from './ui';
import { palette, space, type as typography } from '../theme/tokens';

export function LockScreen() {
  const { unlock, signOut } = useAuth();
  const [method, setMethod] = useState('Biometric unlock');
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => { void biometrics.describe().then(setMethod); }, []);

  const attempt = useCallback(async () => {
    setBusy(true);
    const ok = await unlock();
    setBusy(false);
    setFailed(!ok);
  }, [unlock]);

  // Prompt once on mount, so the common case is "open app, glance, in".
  useEffect(() => { void attempt(); }, [attempt]);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.body}>
        <Text style={styles.title}>EvoSys Pro is locked</Text>
        <Text style={styles.subtitle}>
          {failed
            ? 'That did not unlock. Try again, or sign out and use your password.'
            : `Use ${method} to continue.`}
        </Text>
        <Button label={`Unlock with ${method}`} onPress={() => void attempt()} loading={busy} />
        <Button label="Sign out instead" variant="ghost" onPress={() => void signOut()} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.ground },
  body: { flex: 1, justifyContent: 'center', padding: space.xl, gap: space.md },
  title: { ...typography.title, color: palette.text, textAlign: 'center' },
  subtitle: {
    ...typography.caption, color: palette.textMuted, textAlign: 'center',
    marginBottom: space.lg,
  },
});
