import type { ExpoConfig } from 'expo/config';

/**
 * EvoSys Pro — native app configuration.
 *
 * THE API BASE IS NOT DEFAULTED TO PRODUCTION. `EXPO_PUBLIC_API_URL` decides,
 * and a build without it points at localhost. A debug build that silently
 * reaches live customer data is a worse failure than one that cannot start.
 *
 * DEEP LINKS ARE NAVIGATION, NOT AUTHORISATION. The associated domains below
 * let `https://app.evosyspro.live/leads/123` open in the app when it is
 * installed and in the browser when it is not — which is what keeps the ~12
 * `FRONTEND_URL` call sites in the backend working unchanged. What arrives is a
 * RECORD ID and nothing more: the app fetches it through the normal authorised
 * endpoint and the server decides. Possession of an id grants nothing.
 *
 * PUBLIC TOKEN LINKS STAY ON THE WEB. `/book`, `/deal-room` and `/survey` are
 * for prospects, who do not have this app, so they are deliberately absent from
 * the link handling in `src/deeplinks.ts`.
 */

const apiBaseUrl = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

const config: ExpoConfig = {
  name: 'EvoSys Pro',
  slug: 'evosyspro-mobile',
  version: '1.0.0',
  orientation: 'portrait',
  scheme: 'evosyspro',
  // No `newArchEnabled`: the New Architecture is the default and the only
  // option in SDK 57, and the key no longer exists in the config type.
  userInterfaceStyle: 'dark',
  // The splash screen moved out of app config in SDK 57 and belongs to the
  // expo-splash-screen plugin. Left unset rather than guessed: the ground
  // colour is already in the theme tokens, and an asset that does not exist
  // yet is a design task rather than a config line.
  backgroundColor: '#040812',
  ios: {
    supportsTablet: false,
    bundleIdentifier: 'live.evosyspro.mobile',
    associatedDomains: [
      'applinks:app.evosyspro.live',
      'applinks:app.bookaboost.live',
    ],
    infoPlist: {
      // Face ID is a LOCK on an already-issued credential, not a credential.
      NSFaceIDUsageDescription:
        'Unlock EvoSys Pro without retyping your password.',
      NSCameraUsageDescription:
        'Attach a photo to a lead, an appointment or a proposal.',
      NSPhotoLibraryUsageDescription:
        'Attach an existing photo to a lead, an appointment or a proposal.',
    },
  },
  android: {
    package: 'live.evosyspro.mobile',
    adaptiveIcon: { backgroundColor: '#040812' },
    intentFilters: [
      {
        action: 'VIEW',
        autoVerify: true,
        data: [
          { scheme: 'https', host: 'app.evosyspro.live' },
          { scheme: 'https', host: 'app.bookaboost.live' },
        ],
        category: ['BROWSABLE', 'DEFAULT'],
      },
    ],
    // MINIMAL PERMISSIONS, ON PURPOSE. Camera is here because photo capture is
    // a real field workflow. Location, contacts, calendar and microphone are
    // NOT, even though Expo offers an API for each — a permission requested
    // because it was available is a permission that costs trust at install
    // time and returns nothing.
    permissions: ['CAMERA', 'READ_MEDIA_IMAGES', 'POST_NOTIFICATIONS'],
  },
  plugins: [
    'expo-router',
    'expo-secure-store',
    [
      'expo-local-authentication',
      { faceIDPermission: 'Unlock EvoSys Pro without retyping your password.' },
    ],
    [
      'expo-image-picker',
      {
        photosPermission: 'Attach an existing photo to a lead or proposal.',
        cameraPermission: 'Take a photo to attach to a lead or proposal.',
      },
    ],
    'expo-notifications',
  ],
  extra: {
    apiBaseUrl,
    eas: { projectId: process.env.EAS_PROJECT_ID ?? undefined },
  },
  experiments: { typedRoutes: false },
};

export default config;
