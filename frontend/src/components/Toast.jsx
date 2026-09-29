import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'

// The application's own notice surface, replacing window.alert().
//
// alert() is a poor fit here for reasons that showed up in real use: it blocks
// the whole tab until dismissed, it cannot be styled or read by anything else,
// it renders a browser-chrome dialog that looks nothing like the product, and
// in an automated browser session it freezes the page entirely. It also throws
// away the distinction between "that worked" and "that failed" - every message
// arrives with the same weight and the same OK button.
//
// Usage:
//   const toast = useToast()
//   toast.error('Twilio is not configured for this organization.')
//   toast.success('Message sent.')
//
// Errors stay until dismissed, because an error the advisor did not read is an
// error they will hit again. Success notices clear themselves.

const ToastContext = createContext(null)

const AUTO_DISMISS_MS = { success: 4000, info: 5000, error: 0, warning: 0 }

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const nextId = useRef(1)

  const dismiss = useCallback((id) => {
    setToasts((all) => all.filter((t) => t.id !== id))
  }, [])

  const push = useCallback((kind, message, opts = {}) => {
    const text = typeof message === 'string' ? message : (message?.message || String(message))
    if (!text) return null
    const id = nextId.current++
    setToasts((all) => [...all, { id, kind, text, title: opts.title }])
    const ms = opts.durationMs ?? AUTO_DISMISS_MS[kind] ?? 5000
    if (ms > 0) setTimeout(() => dismiss(id), ms)
    return id
  }, [dismiss])

  const value = useMemo(() => ({
    success: (m, o) => push('success', m, o),
    error: (m, o) => push('error', m, o),
    warning: (m, o) => push('warning', m, o),
    info: (m, o) => push('info', m, o),
    dismiss,
  }), [push, dismiss])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div style={S.stack} aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <div key={t.id} style={{ ...S.toast, ...S.kind[t.kind] }} role={t.kind === 'error' ? 'alert' : 'status'}>
            <span style={S.icon}>{ICON[t.kind]}</span>
            <div style={S.body}>
              {t.title ? <div style={S.title}>{t.title}</div> : null}
              <div style={S.text}>{t.text}</div>
            </div>
            <button type="button" onClick={() => dismiss(t.id)} style={S.close} aria-label="Dismiss">×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

// Safe outside a provider: returns no-ops rather than throwing, so a component
// rendered in isolation (a test, a storybook page) does not crash on a notice.
export function useToast() {
  const ctx = useContext(ToastContext)
  return ctx || FALLBACK
}

const FALLBACK = {
  success: () => null, error: () => null,
  warning: () => null, info: () => null, dismiss: () => null,
}

const ICON = { success: '✓', error: '!', warning: '!', info: 'i' }

const S = {
  stack: {
    position: 'fixed', top: 16, right: 16, zIndex: 9999,
    display: 'flex', flexDirection: 'column', gap: 8,
    maxWidth: 'min(420px, calc(100vw - 32px))', pointerEvents: 'none',
  },
  toast: {
    pointerEvents: 'auto',
    display: 'flex', alignItems: 'flex-start', gap: 10,
    padding: '11px 12px', borderRadius: 10,
    border: '1px solid', boxShadow: '0 6px 24px rgba(16,32,64,0.16)',
    font: 'inherit', fontSize: 13.5, lineHeight: 1.5,
  },
  kind: {
    // Light-system pill tones (styles/appearance.css); the toasts were the
    // last dark slabs floating over every light page.
    success: { background: '#e7f6ef', borderColor: '#bfe6d4', color: '#0a6e4c' },
    error:   { background: '#fdecef', borderColor: '#f3c5d0', color: '#a8173c' },
    warning: { background: '#fdf3e1', borderColor: '#efd9ad', color: '#8a520a' },
    info:    { background: '#e8f0ff', borderColor: '#c6d7ff', color: '#1a4fd6' },
  },
  icon: {
    flex: '0 0 auto', width: 18, height: 18, borderRadius: 9,
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 12, fontWeight: 700, background: 'rgba(16,32,64,0.07)', marginTop: 1,
  },
  body: { flex: '1 1 auto', minWidth: 0 },
  title: { fontWeight: 700, marginBottom: 2 },
  text: { whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
  close: {
    flex: '0 0 auto', background: 'none', border: 'none', cursor: 'pointer',
    color: 'inherit', opacity: 0.65, fontSize: 18, lineHeight: 1, padding: '0 2px',
  },
}

export default ToastProvider
