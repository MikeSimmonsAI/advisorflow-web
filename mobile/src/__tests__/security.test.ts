/**
 * MOBILE SECURITY RULES THAT ARE EASY TO BREAK BY ACCIDENT.
 *
 * Each of these guards a specific shortcut:
 *   - reaching for AsyncStorage because it autocompletes first
 *   - trusting an id because it arrived through a link that looked official
 *   - claiming a device handoff is logged when it is not
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';

import { secureStore, SECURE_KEYS } from '../auth/secureStore';
import { isWebOnlyPath, routeForRecord, routeForUrl } from '../deeplinks';
import { isFullyLogged, contactDisclosure } from '../comms/communications';

describe('credentials at rest', () => {
  beforeEach(async () => {
    jest.clearAllMocks();
    await secureStore.clearSession();
  });

  it('writes the token to the keychain and never to AsyncStorage', async () => {
    await secureStore.setToken('a.jwt.value');
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
      SECURE_KEYS.token, 'a.jwt.value', expect.anything());
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
    expect(await secureStore.getToken()).toBe('a.jwt.value');
  });

  it('pins the keychain item to this device and to an unlocked state', async () => {
    await secureStore.setToken('a.jwt.value');
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
      expect.any(String), expect.any(String),
      expect.objectContaining({
        keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
      }));
  });

  it('clears the token, the biometric preference and the remembered context on sign-out', async () => {
    await secureStore.setToken('a.jwt.value');
    await secureStore.setBiometricPreference(true);
    await secureStore.setLastContext('sales:bso-1');

    await secureStore.clearSession();

    expect(await secureStore.getToken()).toBeNull();
    expect(await secureStore.getBiometricPreference()).toBe(false);
    expect(await secureStore.getLastContext()).toBeNull();
  });

  it('keeps the device id across sign-out, because it identifies the install', async () => {
    const before = await secureStore.getDeviceId();
    await secureStore.clearSession();
    expect(await secureStore.getDeviceId()).toBe(before);
  });

  it('returns a stable device id rather than a fresh one each call', async () => {
    const a = await secureStore.getDeviceId();
    const b = await secureStore.getDeviceId();
    expect(a).toBe(b);
  });

  it('survives a keychain that refuses to read', async () => {
    (SecureStore.getItemAsync as jest.Mock).mockRejectedValueOnce(new Error('locked'));
    await expect(secureStore.getToken()).resolves.toBeNull();
  });
});

describe('deep links name a record and grant nothing', () => {
  it('maps the record types the app can open', () => {
    expect(routeForRecord('lead', 'abc123')).toBe('/lead/abc123');
    expect(routeForRecord('opportunity', 'opp-1')).toBe('/opportunity/opp-1');
    expect(routeForRecord('appointment', 'appt_9')).toBe('/appointment/appt_9');
    expect(routeForRecord('proposal', 'p-1')).toBe('/proposal/p-1');
  });

  it('refuses an id that would change the shape of the path', () => {
    expect(routeForRecord('lead', '../../org/secret')).toBeNull();
    expect(routeForRecord('lead', 'a/b')).toBeNull();
    expect(routeForRecord('lead', '')).toBeNull();
    expect(routeForRecord('lead', 'x'.repeat(200))).toBeNull();
  });

  it('refuses a record type it does not know', () => {
    expect(routeForRecord('admin', 'x')).toBeNull();
    expect(routeForRecord(undefined, 'x')).toBeNull();
  });

  it('opens the web paths the backend already sends in emails', () => {
    expect(routeForUrl('https://app.evosyspro.live/leads/lead-7')).toBe('/lead/lead-7');
    expect(routeForUrl('https://app.evosyspro.live/sales/opportunities/o-2'))
      .toBe('/opportunity/o-2');
  });

  it('leaves prospect-facing links to the browser', () => {
    // A prospect does not have this app. Claiming these would send them to an
    // app store instead of to the page that works.
    expect(isWebOnlyPath('/book/abc')).toBe(true);
    expect(isWebOnlyPath('/deal-room/tok')).toBe(true);
    expect(isWebOnlyPath('/survey/tok')).toBe(true);
    expect(routeForUrl('https://app.evosyspro.live/deal-room/tok')).toBeNull();
  });

  it('returns null for an unrecognised path rather than a 404 inside the app', () => {
    expect(routeForUrl('https://app.evosyspro.live/settings/billing')).toBeNull();
    expect(routeForUrl('not a url')).toBeNull();
  });
});

describe('communications tell the truth about themselves', () => {
  it('does not claim a device handoff is logged', () => {
    expect(isFullyLogged).toBe(false);
  });

  it('says what is recorded, and says less when there is no deal', () => {
    const withDeal = contactDisclosure(true);
    const without = contactDisclosure(false);
    expect(withDeal).toMatch(/records that it happened/i);
    expect(without).toMatch(/nothing is recorded/i);
  });
});
