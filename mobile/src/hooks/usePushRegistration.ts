/**
 * Register this install for push, and route a tap.
 *
 * PERMISSION IS ASKED ONCE, AND NOT AT LAUNCH. A permission prompt on first
 * open, before the person has seen a single appointment, is the prompt people
 * decline — and on iOS a decline is close to final. So registration runs only
 * after there is a signed-in session and an active experience, which means the
 * person has already seen the app do something.
 *
 * A TAP FETCHES; IT NEVER TRUSTS. The payload carries a record type and an id
 * and nothing else (`app/services/push_service.py` enforces that server-side).
 * Tapping navigates to a detail screen, and that screen fetches the record
 * through the normal authorised endpoint. A notification for a record the
 * person may no longer see ends in a clean refusal, which is correct: authority
 * is checked when the notification is created, and a phone can receive it
 * minutes later, after a membership changed.
 */

import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { router } from 'expo-router';

import { devices } from '../api/endpoints';
import { useAuth } from '../auth/AuthContext';
import { useExperience } from '../experience/ExperienceContext';
import { secureStore } from '../auth/secureStore';
import { routeForRecord } from '../deeplinks';

export function usePushRegistration(): void {
  const { status } = useAuth();
  const { active } = useExperience();
  const registeredFor = useRef<string | null>(null);

  // ── registration ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (status !== 'signed_in' || !active) return;
    if (registeredFor.current === active.key) return;

    let cancelled = false;
    void (async () => {
      try {
        // A simulator has no push token. Not an error and not worth a prompt.
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
