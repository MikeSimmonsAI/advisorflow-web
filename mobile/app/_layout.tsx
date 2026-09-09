/**
 * The root. Providers, the auth gate, and nothing else.
 *
 * ORDER MATTERS AND IT IS NOT ARBITRARY:
 *   QueryClientProvider  — the cache the experience switcher has to be able to
 *                          drop, so it must be OUTSIDE ExperienceProvider
 *   AuthProvider         — owns the token and the single 401 handler
 *   ExperienceProvider   — reads contexts from auth, owns the request scope
 *
 * The gate below renders exactly one of four things and never both: loading,
 * the biometric lock, sign-in, or the app. A gate that renders the app "just
 * for a moment" while it checks is a gate that leaks a screenshot of somebody
 * else's pipeline into the app switcher.
 */

import React from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { View, StyleSheet } from 'react-native';

import { AuthProvider, useAuth } from '../src/auth/AuthContext';
import { ExperienceProvider } from '../src/experience/ExperienceContext';
import { LockScreen } from '../src/components/LockScreen';
import { SignInScreen } from '../src/components/SignInScreen';
import { Loading } from '../src/components/ui';
import { palette } from '../src/theme/tokens';
import { usePushRegistration } from '../src/hooks/usePushRegistration';
import { useDeepLinks } from '../src/hooks/useDeepLinks';
import { useOutbox } from '../src/hooks/useOutbox';

/**
 * Query defaults chosen for a phone on a cellular connection in a basement.
 *
 * `retry` refuses to retry an authorisation refusal. A 403 is the server's
 * ANSWER, not a transport hiccup: retrying it three times turns one clean "you
 * may not see this" into four identical refusals and a spinner. A 401 is
 * already handled globally and never reaches here.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 10 * 60_000,
      retry: (failureCount, error) => {
        const status = (error as { status?: number })?.status ?? 0;
        if (status === 401 || status === 403 || status === 404) return false;
        return failureCount < 2;
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      refetchOnWindowFocus: false,
    },
    mutations: { retry: 0 },
  },
});

function Gate() {
  const { status } = useAuth();

  // All three are no-ops until there is a session; they are mounted here so a
  // notification tap, a deep link or a queued note that arrives during a cold
  // start is held rather than dropped.
  usePushRegistration();
  useDeepLinks();
  useOutbox();

  if (status === 'loading') {
    return <View style={styles.fill}><Loading label="Starting up" /></View>;
  }
  if (status === 'locked') return <LockScreen />;
  if (status === 'signed_out') return <SignInScreen />;

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: palette.ground },
        headerTintColor: palette.text,
        headerTitleStyle: { color: palette.text },
        contentStyle: { backgroundColor: palette.ground },
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="index" options={{ headerShown: false }} />
      <Stack.Screen name="(sales)" options={{ headerShown: false }} />
      <Stack.Screen name="(manager)" options={{ headerShown: false }} />
      <Stack.Screen name="(advisor)" options={{ headerShown: false }} />
      <Stack.Screen name="(exec)" options={{ headerShown: false }} />
      <Stack.Screen name="(owner)" options={{ headerShown: false }} />
      <Stack.Screen name="switch" options={{ title: 'Switch experience', presentation: 'modal' }} />
      <Stack.Screen name="sessions" options={{ title: 'Signed-in devices' }} />
      <Stack.Screen name="lead/[id]" options={{ title: 'Lead' }} />
      <Stack.Screen name="opportunity/[id]" options={{ title: 'Opportunity' }} />
      <Stack.Screen name="appointment/[id]" options={{ title: 'Appointment' }} />
      <Stack.Screen name="proposal/[id]" options={{ title: 'Proposal' }} />
      <Stack.Screen name="proposal/new" options={{ title: 'New proposal' }} />
      <Stack.Screen name="org/[id]" options={{ title: 'Organization' }} />
      <Stack.Screen name="customer/[id]" options={{ title: 'Customer' }} />
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ExperienceProvider>
            <StatusBar style="light" />
            <Gate />
          </ExperienceProvider>
        </AuthProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: palette.ground, justifyContent: 'center' },
});
