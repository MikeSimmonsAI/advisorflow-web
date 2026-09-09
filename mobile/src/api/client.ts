/**
 * The one place a request leaves this app.
 *
 * Mirrors the web client's shape (`frontend/src/api/client.js`) so the two
 * clients stay recognisably the same thing, with three additions a phone needs:
 *
 *  1. DEVICE HEADERS on every request, so the server can label this install's
 *     session row and replace it rather than stacking a new one on each launch.
 *  2. A SINGLE 401 HANDLER. One place decides that the session is gone, clears
 *     secure storage and drops to sign-in. Scattered 401 handling is how an app
 *     ends up retrying a dead session in a loop against a server that has
 *     already said no.
 *  3. AN OFFLINE-SHAPED ERROR. A request that never left the phone is a
 *     different thing from one the server refused, and the UI has to be able to
 *     tell them apart to decide between "retry" and "you may not do that".
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * --------------------------------
 * It does not decide authority. `X-Workspace-Id` is sent ONLY when the active
 * experience is a customer workspace, mirroring `routeAuthority.js` — an
 * executive or platform screen must never send an org override, and the switch
 * that sets it lives in one place (setRequestContext) rather than at call
 * sites.
 */

import Constants from 'expo-constants';
import { Platform } from 'react-native';

import { secureStore } from '../auth/secureStore';

// ── configuration ───────────────────────────────────────────────────────────
//
// The API base comes from app.config.ts `extra`, which reads EXPO_PUBLIC_API_URL
// at build time. No default to production: a debug build accidentally pointed at
// live customer data is a worse outcome than a build that refuses to start.

const extra = (Constants.expoConfig?.extra ?? {}) as Record<string, string>;
export const API_BASE: string = extra.apiBaseUrl ?? 'http://localhost:8000';
export const APP_VERSION: string = Constants.expoConfig?.version ?? '0.0.0';

export class ApiError extends Error {
  status: number;
  detail: string;
  /** True when the request never reached the server. */
  offline: boolean;

  constructor(status: number, detail: string, offline = false) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.offline = offline;
  }

  /** The server refused this specific record — not a transport problem, and
   *  never something to retry automatically. */
  get isAuthorization(): boolean {
    return this.status === 403 || this.status === 404;
  }
}

// ── request context ─────────────────────────────────────────────────────────

type RequestContext = {
  /** Set ONLY inside a customer workspace. */
  workspaceOrgId?: string | null;
  /** god_admin only, mirroring the web console. */
  brandOverride?: string | null;
};

let context: RequestContext = {};
let onUnauthorized: (() => void) | null = null;

/**
 * Called by the experience switcher, and by nothing else.
 *
 * Passing the whole context in one call rather than exposing setters is
 * deliberate: leaving a stale `workspaceOrgId` behind while switching into an
 * executive experience is precisely the cross-tenant bug the switcher exists to
 * prevent, and a partial setter makes that easy to write by accident.
 */
export function setRequestContext(next: RequestContext): void {
  context = { ...next };
}

export function getRequestContext(): RequestContext {
  return { ...context };
}

/** Registered once by AuthProvider. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

// ── headers ─────────────────────────────────────────────────────────────────

async function buildHeaders(extraHeaders?: Record<string, string>) {
  const token = await secureStore.getToken();
  const deviceId = await secureStore.getDeviceId();

  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-Client-Platform': Platform.OS === 'ios' ? 'ios' : Platform.OS === 'android' ? 'android' : 'web',
    'X-Device-Id': deviceId,
    'X-App-Version': APP_VERSION,
    ...(extraHeaders ?? {}),
  };

  const deviceName = Constants.deviceName;
  if (deviceName) headers['X-Device-Name'] = deviceName;
  if (token) headers.Authorization = `Bearer ${token}`;

  // THE WORKSPACE HEADER IS CONDITIONAL AND THAT IS THE POINT.
  // Sending it from an executive or platform screen would ask the server to
  // scope a brand-wide read to one customer org — at best wrong data, at worst
  // a refusal that reads as a permissions bug.
  if (context.workspaceOrgId) headers['X-Workspace-Id'] = context.workspaceOrgId;
  if (context.brandOverride) headers['X-Brand-Override'] = context.brandOverride;

  return headers;
}

// ── the request ─────────────────────────────────────────────────────────────

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  /** Form-encoded, for /auth/login which is an OAuth2 password form. */
  form?: Record<string, string>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Skip the global 401 handler — used by the sign-in screen, where a 401 is
   *  an answer ("wrong password") rather than an expired session. */
  allowUnauthorized?: boolean;
  timeoutMs?: number;
};

const DEFAULT_TIMEOUT = 20000;

/**
 * GATEWAY STATUSES — the edge answered, the app server did not.
 *
 * These do NOT come from our backend. They come from the proxy in front of it,
 * and their bodies are the proxy's own plain text, e.g.
 *
 *     upstream connect error or disconnect/reset before headers.
 *     reset reason: connection termination
 *
 * That is what a redeploy looks like from outside: the old instance is
 * terminated, the new one has not accepted a listener yet, and for roughly
 * twenty to forty seconds every request gets this. This backend is deployed
 * many times a day, so a phone that launches during one of those windows and
 * treats it as a permanent failure is a phone that appears broken on a server
 * that is fine.
 *
 * A browser survives it because a person presses reload. The app has to do
 * that for itself.
 */
const GATEWAY_STATUSES = new Set([502, 503, 504]);
const GATEWAY_RETRIES = 2;
const GATEWAY_BACKOFF_MS = [1200, 3500];

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export async function request<T = unknown>(path: string, opts: RequestOptions = {}, attempt = 0): Promise<T> {
  const { method = 'GET', body, form, allowUnauthorized, timeoutMs = DEFAULT_TIMEOUT } = opts;

  const headers = await buildHeaders(opts.headers);
  let payload: string | undefined;

  if (form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    payload = Object.entries(form)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&');
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  // A field connection does not fail, it hangs. Without an explicit timeout a
  // spinner in a basement runs until the person kills the app.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  if (opts.signal) {
    opts.signal.addEventListener('abort', () => controller.abort());
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: payload,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    throw new ApiError(0, 'No connection. This will retry when you are back online.', true);
  }
  clearTimeout(timer);

  // A REDEPLOY IS NOT A FAILED REQUEST. Ride it out before telling anybody
  // anything: two retries over ~5s covers the window without turning a genuine
  // outage into a spinner that never ends. The body is deliberately not read
  // here — it belongs to the proxy, not to this API, and parsing it would be
  // treating another system's error page as our own response.
  if (GATEWAY_STATUSES.has(response.status)) {
    if (attempt < GATEWAY_RETRIES) {
      await sleep(GATEWAY_BACKOFF_MS[attempt] ?? 3500);
      return request<T>(path, opts, attempt + 1);
    }
    // `offline: true` on purpose. From the caller's side this is the same
    // situation as a dropped connection — nothing was processed, the request
    // is safe to repeat, and the UI should offer retry rather than accuse the
    // person of doing something they may not do.
    throw new ApiError(
      response.status,
      'The server is restarting. Please try again in a moment.',
      true,
    );
  }

  if (response.status === 401 && !allowUnauthorized) {
    // ONE handler, called once. It clears secure storage and routes to
    // sign-in; nothing here retries, because the server has already said the
    // credential is dead and asking again with the same one is noise.
    onUnauthorized?.();
    throw new ApiError(401, 'Your session has ended. Please sign in again.');
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const parsed = await response.json();
      if (typeof parsed?.detail === 'string') detail = parsed.detail;
      else if (Array.isArray(parsed?.detail)) detail = parsed.detail[0]?.msg ?? detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    // A 2xx WHOSE BODY IS NOT JSON DID NOT COME FROM THIS API.
    //
    // This used to `return text as unknown as T` — handing the raw body to the
    // caller as though it had parsed. Every endpoint here returns JSON, so the
    // only things that arrive this way are somebody else's: a proxy's plain
    // text, a captive-portal login page, an HTML error from an edge that
    // answered 200. Screens then rendered that text as if it were data, which
    // is how a proxy's "upstream connect error / connection termination" ends
    // up displayed inside the app as though the app had said it.
    //
    // Failing here costs nothing real and makes the wrong-response case
    // visible instead of decorative.
    throw new ApiError(
      response.status,
      'The server returned an unexpected response. Please try again in a moment.',
      true,
    );
  }
}

export const api = {
  get: <T = unknown>(path: string, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'GET' }),
  post: <T = unknown>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  patch: <T = unknown>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'PATCH', body }),
  put: <T = unknown>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'PUT', body }),
  del: <T = unknown>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'DELETE', body }),
};

/** Query-string helper that omits empty values rather than sending `?stage=`. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const pairs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return pairs.length ? `?${pairs.join('&')}` : '';
}
