/**
 * AdvisorFlow Command Center V3 — god_admin only.
 * Renders the full V3 HTML design via iframe.
 */
export default function GodCommandCenter() {
  return (
    <iframe
      src="/god-v3.html"
      title="AdvisorFlow Command Center V3"
      style={{ width: '100%', height: '100vh', border: 'none', display: 'block' }}
    />
  )
}
