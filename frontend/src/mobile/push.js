/**
 * PUSH-READY NOTIFICATIONS — CLIENT ABSTRACTION + SERVER CONTRACT.
 *
 * The server side is app/routers/push_router.py + app/services/web_push_service.py.
 * Nothing in this module sends a push; the server does, and only when it holds
 * VAPID keys and the web push library (GET /push/config says which). Without
 * them every screen gets an honest "not configured", never a fake toggle.
 *
 * ── SERVER CONTRACT ─────────────────────────────────────────────────────────
 *
 *   GET  /push/config      (no auth)
 *        -> 200 { configured, enabled, vapid_public_key|null, reason|null }
 *        enabled=false / failure  => client reports status 'unconfigured'.
 *
 *   POST /push/subscribe   body { endpoint, keys: { p256dh, auth }, user_agent, platform: 'web' }
 *        Auth: bearer token. Owned by the SIGNED-IN USER, recorded with the
 *        current workspace. An endpoint already held by another user moves.
 *        -> 201 { id, configured, subscription }
 *
 *   DELETE /push/subscribe?endpoint_sha256=<hex> | ?id=<id>  (own rows only; else 404)
 *   GET  /push/status      -> { configured, active_subscriptions, subscriptions[], recent_events[] }
 *   POST /push/test        -> test alert to the caller's own subscriptions; 409 when
 *                             not configured or nothing subscribed.
 *
 *   PAYLOAD the service worker receives (data-minimal by design — lock
 *   screens are public):
 *        { title: "<generic, no customer PII>", url: "/m/...",
 *          notification_id, event_id, workspace_id, type }
 *   It never contains message bodies, phone numbers, or names; the app
 *   fetches details after the person opens it and is authenticated.
 *
 * ── CLIENT ─────────────────────────────────────────────────────────────────
 */

export const PUSH_STATUS = {
  UNSUPPORTED: 'unsupported',     // browser has no Push API / service worker
  UNCONFIGURED: 'unconfigured',   // server has no push config (today: always)
  DENIED: 'denied',               // the person blocked notifications
  AVAILABLE: 'available',         // could subscribe
  SUBSCRIBED: 'subscribed',
}

/** Pure: decide the status from capability facts. Unit-tested. */
export function pushStatus({ hasServiceWorker, hasPushManager, permission, serverConfig, subscribed }) {
  if (!hasServiceWorker || !hasPushManager) return PUSH_STATUS.UNSUPPORTED
  if (!serverConfig || serverConfig.enabled !== true || !serverConfig.vapid_public_key) return PUSH_STATUS.UNCONFIGURED
  if (permission === 'denied') return PUSH_STATUS.DENIED
  if (subscribed) return PUSH_STATUS.SUBSCRIBED
  return PUSH_STATUS.AVAILABLE
}

/** Pure: the subscription body the contract above expects. */
export function subscriptionBody(subscription, userAgent = '') {
  const json = subscription && typeof subscription.toJSON === 'function' ? subscription.toJSON() : subscription
  if (!json || !json.endpoint || !json.keys) return null
  return { endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
           user_agent: String(userAgent).slice(0, 300), platform: 'web' }
}

/**
 * Browser side: read the current status. `getConfig` is injected so this
 * module needs no API import (the screen passes api.get). A failing config
 * read is 'unconfigured' — never 'available'.
 */
export async function readPushStatus(getConfig) {
  const nav = typeof navigator !== 'undefined' ? navigator : {}
  const hasServiceWorker = 'serviceWorker' in nav
  const hasPushManager = typeof window !== 'undefined' && 'PushManager' in window
  const permission = typeof Notification !== 'undefined' ? Notification.permission : 'default'
  let serverConfig = null
  if (hasServiceWorker && hasPushManager && getConfig) {
    try { serverConfig = await getConfig() } catch { serverConfig = null }
  }
  let subscribed = false
  if (serverConfig && serverConfig.enabled && hasServiceWorker) {
    try {
      const reg = await nav.serviceWorker.getRegistration()
      subscribed = !!(reg && reg.pushManager && await reg.pushManager.getSubscription())
    } catch { subscribed = false }
  }
  return pushStatus({ hasServiceWorker, hasPushManager, permission, serverConfig, subscribed })
}

/**
 * Subscribe when — and only when — the server publishes a key. Posting is
 * injected (`postSubscription`) so this module needs no API import.
 */
export async function subscribePush({ getConfig, postSubscription } = {}) {
  const status = await readPushStatus(getConfig)
  if (status !== PUSH_STATUS.AVAILABLE) return { ok: false, status }
  const cfg = await getConfig()
  const perm = await Notification.requestPermission()
  if (perm !== 'granted') return { ok: false, status: PUSH_STATUS.DENIED }
  const reg = await navigator.serviceWorker.ready
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public_key) })
  const body = subscriptionBody(sub, navigator.userAgent)
  if (postSubscription) await postSubscription(body)
  return { ok: true, status: PUSH_STATUS.SUBSCRIBED, body }
}

export function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = typeof atob === 'function' ? atob(base64) : Buffer.from(base64, 'base64').toString('binary')
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i)
  return out
}

/** The server routes, in one place. `api` is the app's api client. */
export const PUSH_ROUTES = {
  config: '/push/config', subscribe: '/push/subscribe', status: '/push/status', test: '/push/test',
}

export function pushApi(api) {
  return {
    getConfig: () => api.get(PUSH_ROUTES.config),
    postSubscription: body => api.post(PUSH_ROUTES.subscribe, body),
    deleteByHash: hex => api.delete(PUSH_ROUTES.subscribe + '?endpoint_sha256=' + encodeURIComponent(hex)),
    status: () => api.get(PUSH_ROUTES.status),
    test: () => api.post(PUSH_ROUTES.test, {}),
  }
}

/** Pure: label + action for the More screen row. Unit-tested. */
export function pushRowState(status) {
  switch (status) {
    case PUSH_STATUS.UNSUPPORTED: return { label: 'Not supported on this browser', action: null }
    case PUSH_STATUS.UNCONFIGURED: return { label: 'Not configured', action: null }
    case PUSH_STATUS.DENIED: return { label: 'Blocked in browser settings', action: null }
    case PUSH_STATUS.AVAILABLE: return { label: 'Off', action: 'enable' }
    case PUSH_STATUS.SUBSCRIBED: return { label: 'Enabled', action: 'disable' }
    default: return { label: 'Checking…', action: null }
  }
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('')
}

/** Turn push off on this browser and revoke the server row (own rows only). */
export async function unsubscribePush({ deleteByHash } = {}) {
  const reg = await navigator.serviceWorker.getRegistration()
  const sub = reg && reg.pushManager ? await reg.pushManager.getSubscription() : null
  if (!sub) return { ok: true }
  const endpoint = sub.endpoint
  await sub.unsubscribe()
  if (deleteByHash) {
    try { await deleteByHash(await sha256Hex(endpoint)) } catch { /* a stale server row is revoked on its first 404/410 */ }
  }
  return { ok: true }
}
