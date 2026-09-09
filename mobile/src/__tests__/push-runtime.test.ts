/**
 * THE EXPO GO GUARD.
 *
 * This is a test for a crash, not for a feature. Expo Go on Android SDK 53+
 * takes the whole app down when `expo-notifications` is reached, and the first
 * build of this app reached it from a top-level import in the root layout — so
 * scanning the QR code produced a crash instead of a sign-in screen.
 *
 * Two assertions carry the weight, and they pull in opposite directions:
 *
 *   1. In Expo Go the module is NEVER REQUIRED — not required-and-ignored.
 *      Evaluating it IS the crash, so `loadedSpy` must never fire.
 *   2. In every other runtime it IS required, unchanged. A guard that quietly
 *      disabled push everywhere would satisfy (1) and ship a broken product.
 *
 * Everything runs inside `jest.isolateModules` with `jest.doMock`, because
 * `pushSupport()` memoises (every render asks it) and because an isolated
 * registry re-evaluates the expo-constants mock — mutating the outer import
 * would leave the module under test reading a different object entirely.
 */

type Env = 'storeClient' | 'bare' | 'standalone' | undefined;

type Ctx = {
  runtime: typeof import('../push/runtime');
  /** Fires if and only if expo-notifications was actually evaluated. */
  loadedSpy: jest.Mock;
};

function withRuntime(
  opts: { env?: Env; appOwnership?: string | null; notificationsThrows?: boolean },
  fn: (ctx: Ctx) => void,
): void {
  jest.isolateModules(() => {
    const loadedSpy = jest.fn();

    jest.doMock('expo-constants', () => ({
      __esModule: true,
      ExecutionEnvironment: {
        Bare: 'bare', Standalone: 'standalone', StoreClient: 'storeClient',
      },
      default: {
        executionEnvironment: opts.env,
        appOwnership: opts.appOwnership ?? null,
        expoConfig: { version: '1.0.0-test', extra: { apiBaseUrl: 'https://api.test' } },
        deviceName: 'Test Device',
      },
    }));

    jest.doMock('expo-notifications', () => {
      loadedSpy();
      if (opts.notificationsThrows) throw new Error('native module missing');
      return {
        getPermissionsAsync: jest.fn(),
        requestPermissionsAsync: jest.fn(),
        getExpoPushTokenAsync: jest.fn(),
        setNotificationChannelAsync: jest.fn(),
        addNotificationResponseReceivedListener: jest.fn(() => ({ remove: jest.fn() })),
        AndroidImportance: { DEFAULT: 3 },
      };
    });

    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    const runtime = require('../push/runtime') as typeof import('../push/runtime');
    fn({ runtime, loadedSpy });
  });
}

describe('Expo Go', () => {
  it('is detected from executionEnvironment', () => {
    withRuntime({ env: 'storeClient' }, ({ runtime }) => {
      expect(runtime.isExpoGo()).toBe(true);
    });
  });

  it('reports push as unsupported rather than as switched off', () => {
    withRuntime({ env: 'storeClient' }, ({ runtime }) => {
      const support = runtime.pushSupport();
      expect(support.supported).toBe(false);
      expect(support.environment).toBe('expo_go');
      // "Push is unavailable" reads as a bug. The reason names the fix.
      expect(support.reason).toMatch(/Expo Go/i);
      expect(support.reason).toMatch(/preview build/i);
    });
  });

  it('NEVER evaluates expo-notifications', () => {
    // The crash, in one assertion. The mock factory throws nothing here — it
    // simply records that it ran, and in Expo Go it must not run at all.
    withRuntime({ env: 'storeClient' }, ({ runtime, loadedSpy }) => {
      expect(runtime.loadNotifications()).toBeNull();
      expect(loadedSpy).not.toHaveBeenCalled();
    });
  });

  it('is still detected from the legacy appOwnership signal alone', () => {
    withRuntime({ env: undefined, appOwnership: 'expo' }, ({ runtime, loadedSpy }) => {
      expect(runtime.isExpoGo()).toBe(true);
      expect(runtime.loadNotifications()).toBeNull();
      expect(loadedSpy).not.toHaveBeenCalled();
    });
  });

  it('does not throw when expo-constants reports nothing at all', () => {
    withRuntime({ env: undefined, appOwnership: null }, ({ runtime }) => {
      expect(() => runtime.pushSupport()).not.toThrow();
      expect(runtime.isExpoGo()).toBe(false);
    });
  });
});

describe('a real build', () => {
  it('keeps push supported in a development build', () => {
    withRuntime({ env: 'bare' }, ({ runtime }) => {
      const support = runtime.pushSupport();
      expect(support.supported).toBe(true);
      expect(support.environment).toBe('dev_build');
      expect(support.reason).toBeNull();
    });
  });

  it('keeps push supported in a preview or store build', () => {
    withRuntime({ env: 'standalone' }, ({ runtime }) => {
      const support = runtime.pushSupport();
      expect(support.supported).toBe(true);
      expect(support.environment).toBe('store_build');
    });
  });

  it('DOES evaluate expo-notifications there', () => {
    // The other half of the guard. Without this test, deleting push entirely
    // would pass every Expo Go assertion above.
    withRuntime({ env: 'bare' }, ({ runtime, loadedSpy }) => {
      const loaded = runtime.loadNotifications();
      expect(loadedSpy).toHaveBeenCalled();
      expect(loaded).not.toBeNull();
      expect(typeof loaded?.getExpoPushTokenAsync).toBe('function');
    });
  });

  it('degrades to null rather than throwing when the native module is missing', () => {
    withRuntime({ env: 'bare', notificationsThrows: true }, ({ runtime }) => {
      expect(() => runtime.loadNotifications()).not.toThrow();
      expect(runtime.loadNotifications()).toBeNull();
    });
  });
});

describe('the answer is stable', () => {
  it('memoises, because every render asks', () => {
    withRuntime({ env: 'bare' }, ({ runtime }) => {
      expect(runtime.pushSupport()).toBe(runtime.pushSupport());
    });
  });

  it('can be reset, for tests only', () => {
    withRuntime({ env: 'bare' }, ({ runtime }) => {
      const first = runtime.pushSupport();
      expect(first.supported).toBe(true);
      runtime.__resetPushSupportCache();
      expect(runtime.pushSupport()).not.toBe(first);
      expect(runtime.pushSupport().supported).toBe(true);
    });
  });
});
