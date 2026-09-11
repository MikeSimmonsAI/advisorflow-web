/**
 * LaunchSidebar — the customer's rail.
 *
 * THE BRAND IS AT THE TOP, ADVISORFLOW IS AT THE BOTTOM. The customer is
 * onboarding with a white-label brand; the platform underneath is credited
 * once in the footer, the way infrastructure is credited, and never presented
 * as the thing the customer bought.
 *
 * The help card, the artwork and the three-line tagline at the foot are
 * `experience.presentation`, resolved per brand and per customer on the
 * server. A rail with no configuration renders without them and still looks
 * finished — nothing here is load-bearing on a settings row existing.
 *
 * Items that do not resolve to a screen yet say SOON rather than silently
 * doing nothing. A labelled placeholder is honest; an unlabelled one is a
 * broken link the customer blames themselves for.
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
                                        open, onToggle, onSelect,
                                        presentation = {} }) {
  const p = presentation || {}
  const help = p.help || {}
  const railArt = p.rail_image_url || null
  const taglineLines = Array.isArray(p.rail_tagline)
    ? p.rail_tagline
    : (p.rail_tagline ? String(p.rail_tagline).split('\n') : [])

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

        {/* The help affordance the customer reaches for while stuck in a form,
            placed where they are looking rather than at the bottom of a rail
            they have already scrolled past. */}
        <div className="lp-railhelp">
          <span className="lp-rh-ico"><Ico name="chat" size={16} /></span>
          <span className="lp-rh-words">
            <b>{help.title || 'Need Help?'}</b>
            <span>{help.body || ('Chat with the ' + brand.name + ' team')}</span>
          </span>
          <span className="lp-rh-caret">›</span>
        </div>

        <div className="lp-railfill" />

        {taglineLines.length ? (
          <div className="lp-rail-tag">
            {taglineLines.map((line, i) => <span key={i}>{line}</span>)}
          </div>
        ) : null}

        <div className="lp-railfoot">
          <b>Need a hand?</b>
          {brand.supportEmail}
          <br />
          {brand.supportPhone}
          <div style={{ marginTop: 10, opacity: .8 }}>
            Powered by {brand.poweredBy}
          </div>
        </div>
      </div>
    </aside>
  )
}
