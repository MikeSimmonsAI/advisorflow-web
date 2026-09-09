/**
 * Jest environment for the mobile app.
 *
 * The native modules below are mocked because they need a device, not because
 * their behaviour is unimportant — `expo-secure-store` in particular is mocked
 * with a REAL in-memory store rather than a no-op, so the tests that assert a
 * token is written to SecureStore and never to AsyncStorage are asserting
 * against something that actually records what it was asked to keep.
 *
 * The `mock` prefixes on the two stores are required, not stylistic: babel-jest
 * hoists `jest.mock` factories above every other statement in the file, so a
 * factory that closed over an ordinary `const` would reference it before it
 * existed. Jest allows exactly one escape hatch — a variable whose name starts
 * with `mock` — and that is why these are named this way.
 */

/* eslint-env jest */

const mockSecureMemory = new Map();
const mockAsyncMemory = new Map();

jest.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'whenUnlockedThisDeviceOnly',
  getItemAsync: jest.fn(async (k) => (mockSecureMemory.has(k) ? mockSecureMemory.get(k) : null)),
  setItemAsync: jest.fn(async (k, v) => { mockSecureMemory.set(k, v); }),
  deleteItemAsync: jest.fn(async (k) => { mockSecureMemory.delete(k); }),
}));

jest.mock('expo-local-authentication', () => ({
  hasHardwareAsync: jest.fn(async () => false),
  isEnrolledAsync: jest.fn(async () => false),
  supportedAuthenticationTypesAsync: jest.fn(async () => []),
  authenticateAsync: jest.fn(async () => ({ success: true })),
  AuthenticationType: { FINGERPRINT: 1, FACIAL_RECOGNITION: 2 },
}));

jest.mock('expo-notifications', () => ({
  getPermissionsAsync: jest.fn(async () => ({ status: 'undetermined' })),
  requestPermissionsAsync: jest.fn(async () => ({ status: 'denied' })),
  getExpoPushTokenAsync: jest.fn(async () => ({ data: 'ExponentPushToken[test]' })),
  setNotificationHandler: jest.fn(),
  addNotificationResponseReceivedListener: jest.fn(() => ({ remove: jest.fn() })),
  setNotificationChannelAsync: jest.fn(async () => undefined),
  AndroidImportance: { DEFAULT: 3, HIGH: 4 },
}));

jest.mock('expo-device', () => ({ isDevice: false, deviceName: 'Test Device' }));

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: {
    expoConfig: { version: '1.0.0-test', extra: { apiBaseUrl: 'https://api.test' } },
    deviceName: 'Test Device',
  },
}));

// AsyncStorage is mocked so that a test can PROVE nothing wrote a credential
// through it. The assertion lives in src/__tests__/security.test.ts.
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(async (k) => (mockAsyncMemory.has(k) ? mockAsyncMemory.get(k) : null)),
    setItem: jest.fn(async (k, v) => { mockAsyncMemory.set(k, v); }),
    removeItem: jest.fn(async (k) => { mockAsyncMemory.delete(k); }),
    multiRemove: jest.fn(async (keys) => { keys.forEach((k) => mockAsyncMemory.delete(k)); }),
    getAllKeys: jest.fn(async () => Array.from(mockAsyncMemory.keys())),
  },
}));

global.fetch = global.fetch || jest.fn();
