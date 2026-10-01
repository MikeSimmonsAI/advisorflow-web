// Web push client wiring: row states and the server routes the More screen calls.
import assert from 'node:assert/strict'
import { pushRowState, pushApi, PUSH_STATUS, PUSH_ROUTES, pushStatus } from '../../frontend/src/mobile/push.js'

let passed = 0
function check(name, fn) { fn(); passed += 1 }

check('row states', () => {
  assert.deepEqual(pushRowState(PUSH_STATUS.UNCONFIGURED), { label: 'Not configured', action: null })
  assert.equal(pushRowState(PUSH_STATUS.AVAILABLE).action, 'enable')
  assert.equal(pushRowState(PUSH_STATUS.SUBSCRIBED).label, 'Enabled')
  assert.equal(pushRowState(PUSH_STATUS.UNSUPPORTED).action, null)
  assert.equal(pushRowState(PUSH_STATUS.DENIED).action, null)
})

check('server config shape decides availability', () => {
  const base = { hasServiceWorker: true, hasPushManager: true, permission: 'default' }
  assert.equal(pushStatus({ ...base, serverConfig: { configured: false, enabled: false, vapid_public_key: null } }), PUSH_STATUS.UNCONFIGURED)
  assert.equal(pushStatus({ ...base, serverConfig: { configured: true, enabled: true, vapid_public_key: 'BPk' } }), PUSH_STATUS.AVAILABLE)
})

check('pushApi hits /push/* routes', async () => {
  const calls = []
  const fake = { get: p => calls.push(['GET', p]), post: (p, b) => calls.push(['POST', p, b]), delete: p => calls.push(['DELETE', p]) }
  const a = pushApi(fake)
  a.getConfig(); a.postSubscription({ endpoint: 'x' }); a.deleteByHash('ab12'); a.status(); a.test()
  assert.deepEqual(calls.map(c => c[0] + ' ' + c[1]), [
    'GET ' + PUSH_ROUTES.config, 'POST /push/subscribe', 'DELETE /push/subscribe?endpoint_sha256=ab12',
    'GET /push/status', 'POST /push/test'])
})

console.log(`pushOct1: ${passed} passed`)
