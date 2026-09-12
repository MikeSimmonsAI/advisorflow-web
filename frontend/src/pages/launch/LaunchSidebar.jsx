/**
 * LaunchSidebar — the customer's rail.
 *
 * THE CUSTOMER'S OWN NAME IS AT THE TOP. This is their launch portal, so the
 * plate reads as their identity with the workflow named under it; the brand
 * that delivers it is credited once at the foot, the way a provider is
 * credited, and the platform underneath both is not named here at all.
 *
 * ===========================================================================
 * V2: THE SOON CHIPS ARE GONE
 * ===========================================================================
 *
 * Five of eight items carried one, which turned the customer's only navigation
 * into a list of things they cannot have yet — and made the one workflow they
 * are actually here for compete with seven neighbours for attention.
 *
 * The same information now reads as WEIGHT rather than as a label: the live
 * workflow is lit and carries its own progress count, everything else is
 * quieter, and the two items that were never customer-facing surfaces in the
 * first place (a second dashboard, a workspace that does not exist until they
 * go live) are simply not in the launch rail. Nothing became a link that goes
 * nowhere — an item here still routes through `onSelect`, and the shell
 * decides what a key resolves to.
 *
 * The help card, the artwork and the three-line tagline at the foot are
 * `experience.presentation`, resolved per brand and per customer on the
 * server. A rail with no configuration renders without them and still looks
 * finished — nothing here is load-bearing on a settings row existing.
 */
import { Mark, Ico } from './LaunchUI'
import { supportBlock, supportLine } from './present'

/**
 * THE LAUNCH RAIL, NOT THE PRODUCT RAIL. These are the six places a customer
 * goes during an implementation; the workspace they are being given is a
 * different surface with its own navigation, and mixing the two is what made
 * this read as "generic software with a customer dropped into it".
 */
const NAV = [
  { key: 'launchpad',    label: 'Launch Pad',        icon: 'rocket' },
  { key: 'onboarding',   label: 'Onboarding',        icon: 'clip', live: true },
  { key: 'files',        label: 'Files & Documents', icon: 'folder' },
  { key: 'team',         label: 'Team & Users',      icon: 'users' },
  { key: 'integrations', label: 'Integrations',      icon: 'plug' },
]

const HELP_NAV = [
  { key: 'support', label: 'Support', icon: 'life' },
]

export default function LaunchSidebar({ brand, customer, active = 'onboarding',
                                        open, onToggle, onSelect,
                                        presentation = {},
                                        sectionsComplete = null,
                                        sectionsTotal = null }) {
  const p = presentation || {}
  const railArt = p.rail_image_url || null
  const taglineLines = Array.isArray(p.rail_tagline)
    ? p.rail_tagline
    : (p.rail_tagline ? String(p.rail_tagline).split('\n') : [])
  const support = supportBlock(p)

  const count = (sectionsTotal !== null && sectionsTotal !== undefined
                 && sectionsComplete !== null && sectionsComplete !== undefined)
    ? sectionsComplete + ' / ' + sectionsTotal
    : null

  const item = (nav) => (
    <button key={nav.key} type="button"
      className={'lp-navitem' + (nav.key === active ? ' on'
        : nav.live ? '' : ' quiet')}
      aria-current={nav.key === active ? 'page' : undefined}
      onClick={() => onSelect && onSelect(nav.key)}>
      <span className="lp-ni"><Ico name={nav.icon} size={15} /></span>
      {nav.label}
      {nav.key === active && count
        ? <span className="lp-navcount">{count}</span> : null}
    </button>
  )

  return (
    <aside className={'lp-rail' + (open ? ' open' : '')}>
      {railArt ? (
        <div className="lp-rail-art" aria-hidden="true">
          <img src={railArt} alt="" />
          <span className="lp-rail-scrim" />
        </div>
      ) : null}

      <div className="lp-rail-in">
        <div className="lp-railbrand">
          <Mark src={(customer && customer.logoUrl) || p.hero_logo_url || null}
                label={(customer && customer.name) || brand.name} size="s" />
          <div className="lp-bn">
            <b>{(customer && customer.name) || brand.name}</b>
            <span>Launch Portal</span>
          </div>
          <button type="button" className="lp-railtoggle" onClick={onToggle}
            aria-expanded={!!open}>
            {open ? 'Close' : 'Menu'}
          </button>
        </div>

        <div className="lp-navwrap">
          <div className="lp-navgroup">Your launch</div>
          <nav className="lp-nav">{NAV.map(item)}</nav>
          <div className="lp-navgroup">Help</div>
          <nav className="lp-nav">{HELP_NAV.map(item)}</nav>
        </div>

        <div className="lp-railfill" />

        {taglineLines.length ? (
          <div className="lp-rail-tag">
            {taglineLines.map((line, i) => <span key={i}>{line}</span>)}
          </div>
        ) : null}

        {/* ONE support affordance in the rail, at the foot. The response line
            is whatever the configuration actually promises — see present.js:
            with nothing configured it commits the brand to helping and to no
            clock, and there is no timeframe written in this file. */}
        <div className="lp-railhelp">
          <span className="lp-rh-words">
            <b>{support.title}</b>
            <span>{supportLine(p, brand.name)}</span>
          </span>
        </div>

        <div className="lp-railfoot">
          Launch portal delivered by <b>{brand.name}</b>
        </div>
      </div>
    </aside>
  )
}
