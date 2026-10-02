// lazyPage: a failed chunk load (tab open across a deploy) reloads ONCE per 30 s.
import assert from 'node:assert/strict'
import { isChunkLoadError, shouldReload } from '../../frontend/src/utils/chunkReload.js'
let n = 0
const ok = (v) => { assert.ok(v); n++ }
ok(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: https://x/assets/God-abc.js')))
ok(isChunkLoadError(new TypeError('Importing a module script failed.')))
ok(isChunkLoadError('error loading dynamically imported module'))
ok(!isChunkLoadError(new Error('Cannot read properties of undefined')))
ok(!isChunkLoadError(null))
const mem = { v: {}, getItem(k) { return this.v[k] ?? null }, setItem(k, x) { this.v[k] = x } }
ok(shouldReload(1_000_000, mem) === true)
ok(shouldReload(1_010_000, mem) === false)          // within 30 s: no loop
ok(shouldReload(1_031_000, mem) === true)           // later: one more try
ok(shouldReload(5, { getItem() { throw new Error('blocked') } }) === false)  // storage blocked: never loop
console.log(`chunkReload: ${n} passed`)
