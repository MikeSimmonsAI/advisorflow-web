/**
 * Pure helpers for lazyPage (no React import, so a plain `node` test can load
 * them). See lazyPage.js for why a failed chunk load reloads once.
 */
const KEY = 'lazyPage.reloadedAt'

export function isChunkLoadError(err) {
  const msg = String((err && (err.message || err)) || '')
  return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Unable to preload CSS/i.test(msg)
}

export function shouldReload(now = Date.now(), storage = globalThis.sessionStorage) {
  try {
    const last = Number(storage?.getItem(KEY) || 0)
    if (now - last < 30000) return false
    storage?.setItem(KEY, String(now))
    return true
  } catch {
    return false
  }
}
