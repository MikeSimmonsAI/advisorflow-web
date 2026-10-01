/**
 * PWA PLUMBING: service-worker registration and the install prompt.
 *
 * The service worker (public/sw.js) caches the APP SHELL ONLY — the HTML entry
 * and the hashed /assets/ bundles. It never caches API responses, which carry
 * customer data and live on the API origin anyway. See sw.js for the rule.
 */
import { useEffect, useState } from 'react'

let _deferredPrompt = null
const _listeners = new Set()

function _notify() { _listeners.forEach(fn => { try { fn(!!_deferredPrompt) } catch { /* ignore */ } }) }

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    // Keep the browser's mini-infobar from appearing at a random moment; the
    // shell offers "Install app" where the person can choose it.
    e.preventDefault()
    _deferredPrompt = e
    _notify()
  })
  window.addEventListener('appinstalled', () => { _deferredPrompt = null; _notify() })
}

export function isStandalone() {
  if (typeof window === 'undefined') return false
  return !!((window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
            || window.navigator.standalone)
}

/** Register /sw.js once. Silently a no-op where unsupported or on http (non-localhost). */
export function registerServiceWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return Promise.resolve(null)
  return navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => null)
}

/** [canInstall, promptInstall] — canInstall only after the browser offered it. */
export function useInstallPrompt() {
  const [can, setCan] = useState(!!_deferredPrompt)
  useEffect(() => {
    _listeners.add(setCan)
    return () => { _listeners.delete(setCan) }
  }, [])
  async function prompt() {
    const e = _deferredPrompt
    if (!e) return 'unavailable'
    _deferredPrompt = null
    _notify()
    e.prompt()
    try { const choice = await e.userChoice; return choice && choice.outcome } catch { return 'dismissed' }
  }
  return [can && !isStandalone(), prompt]
}
