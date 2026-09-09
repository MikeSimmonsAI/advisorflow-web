/**
 * LaunchSidebar — the customer's rail.
 *
 * THE BRAND IS AT THE TOP, ADVISORFLOW IS AT THE BOTTOM. Atlantis is
 * onboarding with EvoSys Pro; the platform underneath is credited once in the
 * footer, the way infrastructure is credited, and never presented as the thing
 * the customer bought.
 *
 * STAGE 1: only Onboarding resolves to a screen. The rest are the shape of the
 * finished product and say so with a SOON tag rather than silently doing
 * nothing — the God rail's rule about dead nav items applies here too, and a
 * labelled placeholder in a prototype is honest where an unlabelled one is not.
 */
import { Mark, Ico } from './LaunchUI'

const NAV = [
  { key: 'launchpad', label: 'Launch Pad',       icon: 'rocket', live: true },
  { key: 'dashboard', label: 'Dashboard',        icon: 'grid'   },
  { key: 'onboarding',label: 'Onboarding',       icon: 'clip',   live: true },
  { key: 'workspace', label: 'My Workspace',     icon: 'layers' },
  { key: 'files',     label: 'Files & Documents',icon: 'folder' },
  { key: 'team',      label: 'Team & Users',     icon: 'users'  },
  { key: 'integrations', label: 'Integrations',  icon: 'plug'   },
  { key: 'support',   label: 'Support',          icon: 'life'   },
]

export default function LaunchSidebar({ brand, active = 'onboarding',
                                        open, onToggle, onSelect }) {
  return (
    <aside className={'lp-rail' + (open ? ' open' : '')}>
      <div className="lp-railbrand">
        <Mark src={brand.logoUrl} label={brand.name} size="s" />
        <div className="lp-bn">
          <b>{brand.name}</b>
          <span>Client Launch Pad</span>
        </div>
        <button type="button" className="lp-railtoggle" onClick={onToggle}
          aria-expanded={!!open}>
          {open ? 'Close' : 'Menu'}
        </button>
      </div>

      <div className="lp-navwrap">
        <div className="lp-navgroup">Your Ecosystem</div>
        <nav className="lp-nav">
          {NAV.map(item => (
            <button key={item.key} type="button"
              className={'lp-navitem' + (item.key === active ? ' on' : '')}
              aria-current={item.key === active ? 'page' : undefined}
              onClick={() => onSelect && onSelect(item.key)}>
              <span className="lp-ni"><Ico name={item.icon} size={15} /></span>
              {item.label}
              {item.live ? null : <span className="lp-soon">Soon</span>}
            </button>
          ))}
        </nav>
      </div>

      <div className="lp-railfill" />
      <div className="lp-railfoot">
        <b>Need a hand?</b>
        {brand.supportEmail}
        <br />
        {brand.supportPhone}
        <div style={{ marginTop: 10, opacity: .8 }}>
          Powered by {brand.poweredBy}
        </div>
      </div>
    </aside>
  )
}
