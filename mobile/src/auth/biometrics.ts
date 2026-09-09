/**
 * Face ID / fingerprint as a LOCK, never as a credential.
 *
 * The token in the Keychain is what authenticates; biometrics decide whether
 * this app will use it right now. That distinction matters: a failed biometric
 * check does not sign anybody out and does not touch the session — it just
 * keeps the app closed. Someone who cannot present a face falls back to their
 * password, which is the same credential the server has always accepted.
 *
 * The app NEVER blocks on a device that has no enrolled biometric. A rep with a
 * cracked Face ID sensor must still be able to work.
 */

import * as LocalAuthentication from 'expo-local-authentication';

export const biometrics = {
  /** Hardware present AND something actually enrolled. Hardware alone prompts
   *  a dialog nobody can satisfy. */
  async isAvailable(): Promise<boolean> {
    try {
      const [hasHardware, enrolled] = await Promise.all([
        LocalAuthentication.hasHardwareAsync(),
        LocalAuthentication.isEnrolledAsync(),
      ]);
      return hasHardware && enrolled;
    } catch {
      return false;
    }
  },

  async describe(): Promise<string> {
    try {
      const types = await LocalAuthentication.supportedAuthenticationTypesAsync();
      if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) {
        return 'Face ID';
      }
      if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) {
        return 'Fingerprint';
      }
      return 'Biometric unlock';
    } catch {
      return 'Biometric unlock';
    }
  },

  async authenticate(prompt: string): Promise<boolean> {
    try {
      const res = await LocalAuthentication.authenticateAsync({
        promptMessage: prompt,
        // The device passcode stays available on purpose. Disabling it turns a
        // wet thumb into a locked-out rep standing at a graveside.
        disableDeviceFallback: false,
        cancelLabel: 'Use password',
      });
      return res.success;
    } catch {
      return false;
    }
  },
};
