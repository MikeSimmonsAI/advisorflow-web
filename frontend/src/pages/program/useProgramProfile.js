/**
 * WHO THIS PROGRAM IS (Issue #507) - the server's /program/status `profile`.
 *
 * The program shell was written for SCI and said "SCI" everywhere. Restland
 * Cemetery and Funeral Home runs the same shell with ONE location, so every
 * label comes from here and every location picker / cross-location rollup
 * checks `single`. Presentation only: the server still scopes every request
 * to the selected workspace and decides every send.
 *
 * Until the answer arrives the SCI wording is used, so SCI's screens look
 * exactly as they always have.
 */
import { useEffect, useState } from 'react'
import { api, getWorkspaceContext } from '../../api/client'

export const SCI_PROFILE = Object.freeze({
  is_sci: true, display_name: 'Service Corporation International', short_name: 'SCI',
  live_label: 'SCI Live', menu_label: 'SCI menu', pilot_label: 'PILOT WORKSPACE',
  footer: 'SCI outreach proof of concept · EvoSys Pro-operated · Not corporate implementation',
  location_count: null, single_location: false, location: null, timezone: 'America/Chicago',
  sci_only_panels: true, visit_label: 'In-person visit (60 min)',
})

const cache = new Map()   // workspace id -> profile
const waiting = new Map() // workspace id -> promise

/** Pure: the profile to draw for a server answer (null/absent -> SCI wording). */
export function profileFrom(status) {
  const p = status && status.profile
  return p && typeof p === 'object' ? { ...SCI_PROFILE, ...p } : SCI_PROFILE
}

function key() { return getWorkspaceContext() || '_home' }

export function loadProgramProfile() {
  const k = key()
  if (cache.has(k)) return Promise.resolve(cache.get(k))
  if (!waiting.has(k)) {
    waiting.set(k, api.get('/program/status')
      .then(s => { const p = profileFrom(s); cache.set(k, p); return p })
      .catch(() => SCI_PROFILE)
      .finally(() => waiting.delete(k)))
  }
  return waiting.get(k)
}

export function useProgramProfile() {
  const [p, setP] = useState(() => cache.get(key()) || SCI_PROFILE)
  useEffect(() => {
    let alive = true
    loadProgramProfile().then(x => { if (alive) setP(x) })
    return () => { alive = false }
  }, [])
  return p
}

/** For tests and after a workspace switch. */
export function resetProgramProfile() { cache.clear(); waiting.clear() }
