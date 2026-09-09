/**
 * LaunchHeader — the top bar.
 *
 * The ecosystem selector on the left is where a customer who belongs to more
 * than one brand relationship would switch between them. In Stage 1 it names
 * the one ecosystem this customer is in and is not a menu — an affordance that
 * opens an empty list is worse than one that clearly has nothing to open yet.
 *
 * Search and notifications are prototype furniture: present so the finished
 * shape can be judged, inert because there is nothing to search or notify.
 */
import { Mark, Ico } from './LaunchUI'

export default function LaunchHeader({ brand, customer }) {
  return (
    <header className="lp-top">
      <button type="button" className="lp-eco" disabled>
        <Mark src={brand.logoUrl} label={brand.name} size="s" />
        <span>
          <span className="lp-ecolabel">Ecosystem</span>
          <b>{brand.ecosystem}</b>
        </span>
      </button>

      <div className="lp-search">
        <span className="lp-si"><Ico name="search" size={15} /></span>
        <input type="search" placeholder="Search your workspace…"
          aria-label="Search your workspace" />
      </div>

      <div className="lp-topspace" />

      <button type="button" className="lp-iconbtn" aria-label="Notifications">
        <Ico name="bell" size={16} />
        <span className="lp-dot" />
      </button>

      <div className="lp-user">
        <div className="lp-un">
          <b>{customer.user.name}</b>
          <span>{customer.name}</span>
        </div>
        <div className="lp-avatar" aria-hidden="true">{customer.user.initials}</div>
      </div>
    </header>
  )
}
