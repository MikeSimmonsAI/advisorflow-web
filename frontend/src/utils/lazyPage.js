/**
 * lazyPage - a route component loaded on first visit instead of at sign-in.
 *
 * WHY. The whole app shipped as one 3.5 MB script (905 KB gzipped) that every
 * screen downloaded before drawing anything - Login and the phone screens
 * included - although an advisor never opens the owner console, the Sales
 * workspace, the Executive suite or (outside a wholesale workspace) the
 * Wholesale screens. Those areas now load when first visited.
 *
 * THE FAILURE THIS HANDLES. A tab left open across a deploy still holds the
 * old index, which names chunk files the new deploy no longer serves (the
 * static host answers index.html for them). The import then fails. The right
 * recovery is one full reload, which fetches the new index and its chunks.
 * The reload is attempted ONCE per 30 s (sessionStorage), so a chunk that is
 * genuinely broken surfaces as an error instead of a reload loop.
 */
import { lazy } from 'react'
import { isChunkLoadError, shouldReload } from './chunkReload'

export function lazyPage(loader) {
  return lazy(() => loader().catch((err) => {
    if (isChunkLoadError(err) && typeof window !== 'undefined' && shouldReload()) {
      window.location.reload()
      // Never resolves: the page is reloading.
      return new Promise(() => {})
    }
    throw err
  }))
}

export default lazyPage
