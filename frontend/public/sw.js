/*
 * App-shell service worker for the mobile/PWA shell.
 *
 * CACHES THE APP SHELL AND NOTHING ELSE.
 *   - Same-origin GET of the HTML entry (navigations) and of hashed build
 *     assets under /assets/, plus the manifest and icons.
 *   - NEVER: any other origin (the API lives on its own origin), any request
 *     carrying an Authorization header, any non-GET, anything else on this
 *     origin (e.g. /auth, /communications if ever proxied). Those go straight
 *     to the network and are never written to Cache Storage, because API
 *     responses carry customer data and a shared device must not keep them.
 *
 * Navigations are network-first (fresh deploys win) with the cached shell as
 * the offline fallback; hashed assets are cache-first (their names change on
 * every build). Old caches are deleted on activate.
 *
 * `shouldCache` is exported for tests via self.__mshell when present.
 */
var CACHE = 'mshell-v1';
var SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icons/icon.svg'];

function shouldCache(url, origin, method, hasAuth) {
  if (method !== 'GET' || hasAuth) return false;
  var u;
  try { u = new URL(url); } catch (e) { return false; }
  if (u.origin !== origin) return false;
  if (u.search && u.pathname.indexOf('/assets/') !== 0) return false;
  if (u.pathname.indexOf('/assets/') === 0) return true;
  if (u.pathname.indexOf('/icons/') === 0) return true;
  return SHELL.indexOf(u.pathname) !== -1;
}

function isNavigation(req) {
  return req.mode === 'navigate';
}

if (typeof self !== 'undefined') {
  self.__mshell = { shouldCache: shouldCache, CACHE: CACHE, SHELL: SHELL };
}

if (typeof self !== 'undefined' && self.addEventListener) {
  self.addEventListener('install', function (event) {
    event.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).catch(function () {}));
    self.skipWaiting();
  });

  self.addEventListener('activate', function (event) {
    event.waitUntil(caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k.indexOf('mshell-') === 0 && k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); }));
  });

  self.addEventListener('fetch', function (event) {
    var req = event.request;
    var origin = self.location.origin;
    var hasAuth = !!(req.headers && req.headers.get && req.headers.get('Authorization'));
    if (req.method !== 'GET' || hasAuth) return;
    var sameOrigin = req.url.indexOf(origin + '/') === 0;
    if (!sameOrigin) return;  // the API origin is never touched

    if (isNavigation(req)) {
      // SPA route (/m/..., /leads, ...): network first, shell as offline fallback.
      // The navigation RESPONSE is not stored — only the precached shell is used.
      event.respondWith(fetch(req).catch(function () {
        return caches.match('/index.html').then(function (r) { return r || caches.match('/'); });
      }));
      return;
    }
    if (!shouldCache(req.url, origin, req.method, hasAuth)) return;
    event.respondWith(caches.match(req).then(function (hit) {
      if (hit) return hit;
      return fetch(req).then(function (res) {
        if (res && res.ok && res.type === 'basic') {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return res;
      });
    }));
  });

  // PUSH-READY: displays a data-minimal payload (see src/mobile/push.js for
  // the server contract). No server sends pushes yet.
  self.addEventListener('push', function (event) {
    var data = {};
    try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
    var title = data.title || 'New activity';
    event.waitUntil(self.registration.showNotification(title, {
      body: 'Open the app to view details.',
      icon: '/icons/icon.svg', badge: '/icons/icon.svg',
      data: { url: typeof data.url === 'string' && data.url.indexOf('/m') === 0 ? data.url : '/m' },
    }));
  });

  self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    var url = (event.notification.data && event.notification.data.url) || '/m';
    event.waitUntil(self.clients.matchAll({ type: 'window' }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        if ('focus' in list[i]) { list[i].navigate(url); return list[i].focus(); }
      }
      return self.clients.openWindow(url);
    }));
  });
}
