/**
 * WHERE A CREDENTIAL LIVES ON A PHONE.
 *
 * `expo-secure-store` — Keychain on iOS, Keystore on Android. Hardware-backed,
 * wiped on sign-out, and not readable by a file browser on a rooted device the
 * way AsyncStorage is.
 *
 * ASYNCSTORAGE IS NOT AN OPTION FOR ANY OF THESE KEYS. It is a plain unencrypted
 * file. A stolen, rooted or backed-up phone hands the whole file over, and a
 * JWT in it is a live session for up to 24 hours. `src/__tests__` asserts that
 * no token key is ever written through AsyncStorage, because this is exactly
 * the sort of rule that gets broken by someone reaching for the import that
 * autocompletes first.
 *
 * BUSINESS DATA DOES NOT LIVE HERE EITHER. SecureStore has a small value limit
 * and is slow relative to a cache; it holds credentials and preferences, and
 * the query cache holds data (in memory, dropped on sign-out and on experience
 * switch — see src/api/client.ts).
 */

import * as SecureStore from 'expo-secure-store';

const K_TOKEN = 'evosys.auth.token';
const K_DEVICE_ID = 'evosys.device.id';
const K_BIOMETRIC = 'evosys.auth.biometric';
const K_LAST_CONTEXT = 'evosys.experience.last';

/** Every key this module owns. Sign-out clears all of them except the device
 *  id, which identifies the INSTALL rather than the person — keeping it is what
 *  lets the server replace this device's old session instead of stacking up a
 *  new one on every sign-in. */
const CLEAR_ON_SIGN_OUT = [K_TOKEN, K_BIOMETRIC, K_LAST_CONTEXT];

async function get(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key);
  } catch {
    // A keychain read can fail on a device that is locked at cold start. That
    // is "no credential right now", not a crash — the app drops to sign-in.
    return null;
  }
}

async function set(key: string, value: string): Promise<void> {
  await SecureStore.setItemAsync(key, value, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

async function remove(key: string): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(key);
  } catch {
    /* deleting something that is not there is success */
  }
}

export const secureStore = {
  getToken: () => get(K_TOKEN),
  setToken: (t: string) => set(K_TOKEN, t),
  clearToken: () => remove(K_TOKEN),

  /**
   * A stable identifier for THIS INSTALL, generated once and kept.
   *
   * It is not a fingerprint and it is not an authorisation input — the server
   * uses it only to replace this install's previous session row so a phone that
   * signs in every few weeks does not leave a trail of live credentials nobody
   * can see. Deleting the app forgets it, which is the right behaviour: a fresh
   * install is a new device as far as the session list is concerned.
   */
  async getDeviceId(): Promise<string> {
    const existing = await get(K_DEVICE_ID);
    if (existing) return existing;
    const generated = `dev_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
    await set(K_DEVICE_ID, generated);
    return generated;
  },

  getBiometricPreference: async (): Promise<boolean> =>
    (await get(K_BIOMETRIC)) === '1',
  setBiometricPreference: (on: boolean) => set(K_BIOMETRIC, on ? '1' : '0'),

  getLastContext: () => get(K_LAST_CONTEXT),
  setLastContext: (value: string) => set(K_LAST_CONTEXT, value),

  /** Sign-out and 401 both land here. */
  async clearSession(): Promise<void> {
    await Promise.all(CLEAR_ON_SIGN_OUT.map(remove));
  },
};

export const SECURE_KEYS = {
  token: K_TOKEN,
  deviceId: K_DEVICE_ID,
  biometric: K_BIOMETRIC,
  lastContext: K_LAST_CONTEXT,
  clearedOnSignOut: CLEAR_ON_SIGN_OUT,
};
