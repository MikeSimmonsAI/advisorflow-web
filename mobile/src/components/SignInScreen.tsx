/**
 * Sign in.
 *
 * A 401 HERE IS AN ANSWER, NOT AN EXPIRED SESSION. The client's global 401
 * handler exists to clear a dead credential and drop to this screen; firing it
 * FROM this screen would be a loop. `auth.login` therefore passes
 * `allowUnauthorized`, and the message shown is the server's own — "Incorrect
 * email or password", which is deliberately identical whether the account does
 * not exist, is on another brand's domain, or simply typed the wrong password.
 * Saying more here is how an attacker enumerates accounts.
 *
 * The 429 the server can return after ten failures is passed through as-is,
 * because "wait 15 minutes" is genuinely useful and a generic error is not.
 */

import React, { useState } from 'react';
import {
  KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useAuth } from '../auth/AuthContext';
import { Button } from './ui';
import { palette, radius, space, type as typography, HIT_SIZE } from '../theme/tokens';

export function SignInScreen() {
  const { signIn, error } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const canSubmit = email.trim().length > 3 && password.length > 0 && !busy;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    try {
      await signIn(email, password);
    } catch {
      /* the message is on the context; this screen just stops spinning */
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.screen}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.flex}
      >
        <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
          <View style={styles.brand}>
            <View style={styles.mark}><Text style={styles.markText}>E</Text></View>
            <Text style={styles.title}>EvoSys Pro</Text>
            <Text style={styles.subtitle}>Run your day from your phone.</Text>
          </View>

          <View style={styles.form}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              value={email}
              onChangeText={setEmail}
              placeholder="you@company.com"
              placeholderTextColor={palette.textFaint}
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="email"
              keyboardType="email-address"
              inputMode="email"
              style={styles.input}
              accessibilityLabel="Email"
            />

            <Text style={styles.label}>Password</Text>
            <TextInput
              value={password}
              onChangeText={setPassword}
              placeholder="Your password"
              placeholderTextColor={palette.textFaint}
              secureTextEntry
              autoCapitalize="none"
              autoComplete="current-password"
              style={styles.input}
              onSubmitEditing={() => void submit()}
              returnKeyType="go"
              accessibilityLabel="Password"
            />

            {error ? <Text style={styles.error}>{error}</Text> : null}

            <Button
              label="Sign in"
              onPress={() => void submit()}
              disabled={!canSubmit}
              loading={busy}
              style={{ marginTop: space.lg }}
            />
          </View>

          <Text style={styles.footnote}>
            Signing in here does not sign you out anywhere else. Your phone and
            your computer can both stay signed in.
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.ground },
  flex: { flex: 1 },
  body: { flexGrow: 1, justifyContent: 'center', padding: space.xl, gap: space.xl },

  brand: { alignItems: 'center', gap: space.sm },
  mark: {
    width: 60, height: 60, borderRadius: radius.lg,
    backgroundColor: palette.accent, alignItems: 'center', justifyContent: 'center',
  },
  markText: { color: '#fff', fontSize: 30, fontWeight: '700' },
  title: { ...typography.display, color: palette.text },
  subtitle: { ...typography.caption, color: palette.textMuted },

  form: { gap: space.sm },
  label: { ...typography.label, color: palette.textMuted, marginTop: space.md },
  input: {
    minHeight: HIT_SIZE,
    backgroundColor: palette.surfaceSunken,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    paddingHorizontal: space.lg,
    color: palette.text,
    ...typography.body,
  },
  error: { ...typography.caption, color: palette.danger, marginTop: space.md },
  footnote: {
    ...typography.caption, color: palette.textFaint, textAlign: 'center',
  },
});
