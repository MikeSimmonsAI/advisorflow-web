/* A My Deal Room rail group's landing page: every tool in the group that this
 * person may open — read from the rail itself (MdrNavContext), so it can never
 * list a tool the rail hides. Elsewhere (BookaBoost) there is no such page;
 * it sends the person to Acquisition Command. */
import { Link, Navigate, useParams } from 'react-router-dom'
import { useMdrNav } from '../../../components/mdr/MdrShell'
import { MDR_GROUP_BLURB } from '../../../brand/wholesaleNav'
import { EvoApp, Hero, Empty, Panel } from '../ds/ds'

const DESC = {
  '/wholesale/evosense/inbox': 'Every property EvoSense has found, filterable by state, type and source.',
  '/wholesale/foreclosures': 'Trustee and tax sales by month, with notice verification and outcomes.',
  '/wholesale/portfolios': 'Import a seller’s property list, review it and build an offer package.',
  '/wholesale/properties': 'Properties already in the deal workflow.',
  '/wholesale/evosense/strategies': 'What EvoSense hunts for, how often and on what budget.',
  '/wholesale/evosense/inbox?ptype=multifamily': 'The Discovery Inbox, multifamily properties only.',
  '/wholesale/buyers': 'Find, qualify and match cash buyers.',
  '/wholesale/buyers/buy-boxes': 'What each buyer will purchase: areas, types, prices.',
  '/wholesale/deal-room': 'Your private listings, investors and holds.',
  '/wholesale/deal-room/investors': 'Invite and follow up with investors.',
  '/wholesale/dispositions': 'Deals being marketed to buyers.',
  '/wholesale': 'Every deal in the transaction workflow, by stage.',
  '/wholesale/appointments': 'Seller appointments and field visits.',
  '/wholesale/closing': 'Deals under contract through closing.',
  '/wholesale/funding': 'Transactional and gap funding partners.',
  '/wholesale/calculator': 'Run offer, repair and assignment numbers.',
  '/wholesale/board': 'Who is working what today.',
  '/wholesale/canvass': 'Door-knocking routes and results.',
  '/wholesale/callbacks': 'Seller callbacks that are due.',
  '/wholesale/team': 'Team members, roles and access.',
  '/wholesale/evosense/controls': 'Data providers, budgets, channels and pauses.',
  '/wholesale/exceptions': 'Records that need a person to resolve.',
  '/wholesale/pilot': 'Pilot switches for this workspace.',
  '/wholesale/settings': 'Offer formula, approval gates and workspace configuration.',
}

export default function GroupHub() {
  const { group } = useParams()
  const nav = useMdrNav()
  if (!nav) return <Navigate to="/wholesale/evosense" replace />
  const g = nav.groups.find((x) => x.key === group)
  return (
    <EvoApp world="acquisition">
      <Hero title={g ? g.label : 'Tools'} sub={MDR_GROUP_BLURB[group] || null} />
      {!g ? (
        <Panel><Empty title="Nothing here for your access">None of the tools in this group are part of your access in this workspace.</Empty></Panel>
      ) : (
        <div className="mdr-hub__grid" role="list">
          {g.items.map((item) => (
            <Link key={item.to} to={item.to} className="mdr-hub__tile" role="listitem" data-hub-route={item.to}>
              <span className="mdr-hub__icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
              </span>
              <span>
                <span className="mdr-hub__name">{item.label}</span>
                {DESC[item.to] ? <p className="mdr-hub__desc">{DESC[item.to]}</p> : null}
              </span>
            </Link>
          ))}
        </div>
      )}
    </EvoApp>
  )
}
