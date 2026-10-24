/**
 * Register this install for push, and route a tap.
 *
 * NOTHING HERE IMPORTS expo-notifications STATICALLY. That import is what
 * crashed Expo Go on Android at launch (SDK 53 removed remote push from Expo
 * Go, and reaching the module there kills the app rather than warning). The
 * module now arrives through `loadNotifications()`, which returns null in any
 * runtime that cannot take it — so in Expo Go the require never runs and the
 * app boots normally with push reporting itself unsupported.
 *
 * Every other runtime is unchanged: development build, internal preview build
 * and store build all get permissions, a token, backend registration and the
 * tap handler exactly as before.
 *
 * PERMISSION IS ASKED ONCE, AND NOT AT LAUNCH. A permission prompt on first
 * open, before the person has seen a single appointment, is the prompt people
 * decline — and on iOS a decline is close to final. Registration therefore runs
 * only once there is a signed-in session and an active experience.
 *
 * A TAP FETCHES; IT NEVER TRUSTS. The payload carries a record type and an id
 * and nothing else (`app/services/push_service.py` enforces that server-side).
 * Tapping navigates to a detail screen, and that screen fetches the record
 * through the normal authorised endpoint. Authority is checked when the
 * notification is created and a phone can receive it minutes later, after a
 * membership changed — so the id in a payload grants nothing.
 */

import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import * as Device from 'expo-device';
import { router } from 'expo-router';

import { devices } from '../api/endpoints';
import { useAuth } from '../auth/AuthContext';
import { useExperience } from '../experience/ExperienceContext';
import { secureStore } from '../auth/secureStore';
import { routeForRecord } from '../deeplinks';
import { loadNotifications, pushSupport } from '../push/runtime';

export function usePushRegistration(): void {
  const { status } = useAuth();
  const { active } = useExperience();
  const registeredFor = useRef<string | null>(null);

  // ── registration ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (status !== 'signed_in' || !active) return undefined;
    if (registeredFor.current === active.key) return undefined;

    // The guard, before anything native is touched. In Expo Go this is where
    // the effect ends — no import, no permission prompt, no crash.
    const Notifications = loadNotifications();
    if (!Notifications) return undefined;

    let cancelled = false;
    void (async () => {
      try {
        // A simulator has no push token. Not an error, and not worth a prompt.
        if (!Device.isDevice) return;

        const existing = await Notifications.getPermissionsAsync();
        let granted = existing.status === 'granted';
        if (!granted && existing.status === 'undetermined') {
          const asked = await Notifications.requestPermissionsAsync();
          granted = asked.status === 'granted';
        }
        // A person who said no is not asked again by this app. The OS settings
        // are where that decision gets changed.
        if (!granted || cancelled) return;

        if (Platform.OS === 'android') {
          await Notifications.setNotificationChannelAsync('default', {
            name: 'EvoSys Pro',
            importance: Notifications.AndroidImportance.DEFAULT,
          });
        }

        const token = await Notifications.getExpoPushTokenAsync();
        if (cancelled || !token?.data) return;

        const deviceId = await secureStore.getDeviceId();
        await devices.register({
          token: token.data,
          platform: Platform.OS === 'ios' ? 'ios' : 'android',
          device_id: deviceId,
          device_name: Device.deviceName ?? undefined,
          // The experience this install is in, so the server can decide what
          // NOT to send. It is never an input to what may be READ.
          active_context: active.kind,
          active_scope_id:
            active.organizationId ?? active.brandSalesOrgId ?? active.platformId,
        });
        registeredFor.current = active.key;
      } catch {
        // Push is a convenience. Failing to register must never be visible as
        // an error, and must never block the app.
      }
    })();
    return () => { cancelled = true; };
  }, [status, active]);

  // ── taps ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    const Notifications = loadNotifications();
    if (!Notifications) return undefined;

    const sub = Notifications.addNotificationResponseReceivedListener((response) => {
      const data = response?.notification?.request?.content?.data as
        | { record_type?: string; record_id?: string }
        | undefined;
      const target = routeForRecord(data?.record_type, data?.record_id);
      if (target) router.push(target as never);
    });
    return () => sub.remove();
  }, []);
}

/** What the More screen shows about push on this device. */
export function usePushEnvironment() {
  return pushSupport();
}
