// EvoSys Pro Mobile install identity: the FIRST HTML for /app names its own
// manifest and EP icon; /m keeps the legacy one. (Safari reads these tags when
// someone taps Add to Home Screen.)
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { APP_HEAD, toAppHtml } from '../appEntry.mjs'

const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')

test('app.html = index.html with EvoSys Pro install tags only', () => {
  const index = read('../index.html')
  const app = toAppHtml(index)
  assert.match(app, /<link rel="manifest" href="\/manifest-evosys\.webmanifest">/)
  assert.match(app, /<link rel="apple-touch-icon" sizes="180x180" href="\/icons\/evosys-apple-180\.png">/)
  assert.match(app, /<meta name="apple-mobile-web-app-title" content="EvoSys">/)
  assert.doesNotMatch(app, /manifest\.webmanifest"/)
  assert.doesNotMatch(app, /icons\/icon\.svg/)
  // Nothing else changes: same scripts, same bundle entry.
  const strip = h => h.replace(/<link rel="manifest"[^>]*>|<link rel="apple-touch-icon"[^>]*>|<meta name="(theme-color|apple-mobile-web-app-title|application-name)"[^>]*>/g, '').replace(/\s+/g, ' ')
  assert.equal(strip(app), strip(index))
  assert.throws(() => toAppHtml('<html></html>'))
})

test('two manifests, two identities: /app (EvoSys Pro) and /m (legacy) ', () => {
  const evo = JSON.parse(read('../public/manifest-evosys.webmanifest'))
  assert.equal(evo.id, '/app'); assert.equal(evo.scope, '/app'); assert.equal(evo.start_url, '/app/programs')
  assert.equal(evo.display, 'standalone')
  assert.ok(evo.icons.some(i => i.src === '/icons/evosys-512.png'))
  const legacy = JSON.parse(read('../public/manifest.webmanifest'))
  assert.equal(legacy.id, '/m'); assert.equal(legacy.start_url, '/m')
  assert.equal(APP_HEAD.manifest, '/manifest-evosys.webmanifest')
})

test('Render serves app.html for /app and its deep links, before the catch-all; manifests typed', () => {
  const y = read('../../render.yaml')
  const routes = y.slice(y.indexOf('name: advisorflow-frontend'))
  const iApp = routes.indexOf('source: /app\n'), iDeep = routes.indexOf('source: /app/*'), iAll = routes.indexOf('source: /*')
  assert.ok(iApp > 0 && iDeep > iApp && iAll > iDeep, 'order: /app, /app/*, /*')
  assert.match(routes, /source: \/app\/\*\n\s+destination: \/app\.html/)
  assert.match(routes, /path: \/manifest-evosys\.webmanifest\n\s+name: Content-Type\n\s+value: application\/manifest\+json/)
})

test('service worker: v2 cache, /app offline shell is app.html, manifests network-first', () => {
  const src = read('../public/sw.js')
  const box = { self: {}, URL }
  vm.createContext(box); vm.runInContext(src, box)
  const sw = box.self.__mshell
  assert.equal(sw.CACHE, 'mshell-v2')
  assert.equal(sw.shellFor('/app'), '/app.html')
  assert.equal(sw.shellFor('/app/chat/1'), '/app.html')
  assert.equal(sw.shellFor('/m'), '/index.html')
  assert.equal(sw.shellFor('/apple'), '/index.html')
  assert.ok(sw.SHELL.includes('/app.html') && sw.SHELL.includes('/manifest-evosys.webmanifest'))
  assert.match(src, /webmanifest\|html\)\$\/\.test/)
})
