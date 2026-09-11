/**
 * LaunchFooter — the line under the customer's launch page.
 *
 * Small, and it closes a real gap: a premium onboarding experience that simply
 * stops at the bottom of a form reads as an internal tool. The customer is
 * being asked for their company details, their website credentials and their
 * documents; a privacy link and a terms link are the least the page owes them,
 * and they belong where people look for them.
 *
 * THE LINKS ARE CONFIGURATION. A brand with its own privacy policy points at
 * it; a brand that has not set one gets the platform's routes. A link whose
 * href resolves to nothing is rendered as plain text rather than as an anchor
 * that goes nowhere — a dead link in a footer is how a customer decides the
 * company is not real.
 */
import { Mark } from './LaunchUI'

export default function LaunchFooter({ brand, presentation = {} }) {
  const footer = (presentation || {}).footer || {}
  const links = Array.isArray(footer.links) ? footer.links : []
  const copyright = footer.copyright
    || ('© ' + new Date().getFullYear() + ' ' + brand.name + '. All rights reserved.')

  return (
    <footer className="lp-foot">
      <div className="lp-foot-brand">
        <Mark src={brand.logoUrl} label={brand.name} size="s" />
        <span>
          <b>{brand.name}</b>
          {brand.tagline ? <span>{brand.tagline}</span> : null}
        </span>
      </div>

      <nav className="lp-foot-links">
        {links.map((link, i) => (
          link && link.href
            ? <a key={i} href={link.href}>{link.label}</a>
            : <span key={i}>{(link && link.label) || ''}</span>
        ))}
      </nav>

      <p className="lp-foot-note">{copyright}</p>
    </footer>
  )
}
