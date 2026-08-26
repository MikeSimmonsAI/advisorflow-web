/**
 * Demo Mode client.
 *
 * THE ENVIRONMENT ANSWER COMES FROM THE BACKEND, ALWAYS.
 *
 * Not from a query parameter, not from the hostname, not from a build flag.
 * Any of those can be forged or misconfigured by whoever is looking at the
 * screen, and the one thing this banner must never do is fail to appear on a
 * machine that really is wired to a live provider. `GET /demo/environment` is
 * answered by the same process that installed the firewall, so if it says
 * demo, the firewall is up; if it says production, there is nothing to warn
 * about.
 *
 * The probe is cached for the life of the page. A process cannot change which
 * environment it is while it is running, so re-asking on every render would be
 * a request per navigation for an answer that cannot have changed.
 */
import { api, API_BASE } from './client'

let _cache = null
let _inflight = null

/** { environment, demo_mode, banner }. Never throws — a failed probe is
 *  treated as production, which is the safe direction to be wrong in: the
 *  worst case is a demo without a banner, not a production box wearing one. */
export async function fetchEnvironment() {
  if (_cache) return _cache
  if (_inflight) return _inflight
  _inflight = fetch(`${API_BASE}/demo/environment`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      _cache = d || { environment: 'production', demo_mode: false, banner: null }
      return _cache
    })
    .catch(() => {
      _cache = { environment: 'production', demo_mode: false, banner: null }
      return _cache
    })
    .finally(() => { _inflight = null })
  return _inflight
}

/** Synchronous read for code that has already awaited the probe once. */
export function cachedEnvironment() {
  return _cache
}

export function isDemoEnvironment() {
  return !!(_cache && _cache.demo_mode)
}

// ── control surface ────────────────────────────────────────────────────────
//
// Every call below 404s outside the demo environment and 403s for anyone who
// is not a platform owner. The UI never has to guess: it hides the controls
// when the probe says production, and the server refuses them regardless.

export function demoState() {
  return api.get('/demo/state')
}

export function seedScenario(scenario) {
  return api.post('/demo/seed', { scenario })
}

export function advanceScenario(scenario, step) {
  return api.post('/demo/advance', step ? { scenario, step } : { scenario })
}

export function resetDemo() {
  return api.post('/demo/reset', {})
}
