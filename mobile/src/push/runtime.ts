/**
 * WHETHER THIS RUNTIME CAN DO REMOTE PUSH AT ALL — decided before any native
 * push code is allowed to execute.
 *
 * THE CRASH THIS EXISTS TO PREVENT
 * --------------------------------
 * Expo removed remote push notification support from Expo Go in SDK 53. On
 * Android it does not degrade politely: reaching `expo-notifications` there
 * takes the app down at launch, before a single screen paints. The first
 * version of `usePushRegistration` imported the module at the top of the file,
 * so the import ran during the very first render pass of the root layout —
 * which is why scanning the QR code produced a crash rather than a sign-in
 * screen.
 *
 * So the module is NEVER imported statically. `loadNotifications()` requires it
 * lazily, and only after `pushSupport()` has said this runtime can take it. In
 * Expo Go the require never happens, so there is nothing to throw.
 *
 * WHAT THIS IS NOT
 * ----------------
 * Push is not removed and it is not stubbed out globally. A development build,
 * an internal preview build and a store build all take exactly the path they
 * took before: permissions, an Expo push token, backend registration, and a tap
 * handler. The only runtime that gets the no-op is the one that would otherwise
 * crash — and it reports itself as `unsupported` with a reason, so the More
 * screen can say "not available in Expo Go" rather than silently implying push
 * is switched off server-side.
 *
 * WHY STRING COMPARISONS AND NOT ONLY THE ENUM
 * --------------------------------------------
 * `Constants.executionEnvironment` is the authority: `storeClient` is Expo Go,
 * `bare` is a development build, `standalone` is a preview or store build. The
 * enum from expo-constants is used when it is there, and the literals are the
 * fallback — a detector that threw because an enum was undefined would
 * reintroduce the exact class of failure it exists to prevent.
 */

import Constants, { ExecutionEnvironment } from 'expo-constants';
import { Platform } from 'react-native';

export type PushSupport = {
  /** True only when remote push can safely be initialised in this runtime. */
  supported: boolean;
  /** expo_go | dev_build | store_build | web | unknown */
  environment: string;
  /** Plain words for a person. Null when supported. */
  reason: string | null;
};

const EXPO_GO = 'storeClient';
const DEV_BUILD = 'bare';
const STORE_BUILD = 'standalone';

function executionEnvironment(): string {
  // Wrapped because this file is the one place in the app that must not throw.
  try {
    return String(Constants?.executionEnvironment ?? '');
  } catch {
    return '';
  }
}

/** Expo Go, by either of the two signals Expo SDKs have used for it. */
export function isExpoGo(): boolean {
  const env = executionEnvironment();
  if (env) {
    const storeClient = (ExecutionEnvironment && ExecutionEnvironment.StoreClient) || EXPO_GO;
    return env === storeClient;
  }
  // Older SDKs only had appOwnership; 'expo' there means Expo Go.
  try {
    return (Constants as unknown as { appOwnership?: string })?.appOwnership === 'expo';
  } catch {
    return false;
  }
}

let cached: PushSupport | null = null;

export function pushSupport(): PushSupport {
  if (cached) return cached;

  if (Platform.OS === 'web') {
    cached = {
      supported: false,
      environment: 'web',
      reason: 'Push notifications are only available in the mobile app.',
    };
    return cached;
  }

  if (isExpoGo()) {
    cached = {
      supported: false,
      environment: 'expo_go',
      // Named specifically. "Push is unavailable" would leave the reader
      // wondering whether the feature is broken; this says which build to use.
      reason:
        'Expo Go cannot receive push notifications. Everything else works here — '
        + 'install the Android preview build to test notifications.',
    };
    return cached;
  }

  const env = executionEnvironment();
  cached = {
    supported: true,
    environment: env === DEV_BUILD ? 'dev_build'
      : env === STORE_BUILD ? 'store_build'
        : 'unknown',
    reason: null,
  };
  return cached;
}

/** Test seam. Nothing in the app calls this. */
export function __resetPushSupportCache(): void {
  cached = null;
}

/**
 * The expo-notifications module, or null.
 *
 * Lazily required so the module is never evaluated in a runtime that cannot
 * take it, and wrapped so a native module missing for any other reason degrades
 * to "no push" rather than to a crash.
 */
export function loadNotifications(): typeof import('expo-notifications') | null {
  if (!pushSupport().supported) return null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    return require('expo-notifications') as typeof import('expo-notifications');
  } catch {
    return null;
  }
}
