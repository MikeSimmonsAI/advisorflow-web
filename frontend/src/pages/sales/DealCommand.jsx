/**
 * The command area at the top of an Opportunity, and the collapsible stage
 * section the rest of the page is built from.
 *
 * FIVE QUESTIONS, ANSWERED BEFORE ANY SCROLLING.
 *   1. Who is this customer?
 *   2. Where are we in the deal?
 *   3. What do I need to do next?
 *   4. What is still missing?
 *   5. What happens after that?
 *
 * ONE PRIMARY ACTION, CHOSEN BY STAGE. The old page offered every control it
 * had, all at once, which is the same as offering none: a rep still had to
 * work out what the next move was. `primaryAction` derives it from the deal's
 * own lifecycle state — the stages come from `/sales/me`, so this maps the
 * existing lifecycle rather than inventing a second one.
 */
import { Chip } from './parts'

/* Which section of the workspace a stage is currently living in. Stage keys
   are the server's (`app/models/sales_models.py`); the grouping is
   presentation only and adds no state to the record. */
export const SECTION_FOR_STAGE = {
  prospect: 'discovery',
  contacted: 'discovery',
  discovery: 'discovery',
  demo_build: 'demo',
  demo_proposal: 'proposal',
  closing: 'closing',
  won: 'billing',
  onboarding: 'billing',
  live: 'billing',
  lost: 'closing',
}

export const SECTION_ORDER = ['discovery', 'demo', 'proposal', 'closing', 'billing']

/**
 * The one thing to do next on this deal.
 *
 * Returns { label, action } where `action` is handled by the page: a stage
 * move, a section to open, or the meeting finder. Never more than one, and
 * never an action the stage cannot support.
 */
export function primaryAction(opp, closing) {
  const stage = opp.stage
  const discDone = !!(opp.discovery && opp.discovery.completed_at)
  const demo = opp.demo || {}
  const prop = closing && closing.proposal

  if (opp.status === 'lost') {
    return { label: 'Reopen this deal', action: { type: 'stage', stage: 'closing' } }
  }
  if (stage === 'prospect') {
    return { label: 'Log first contact', action: { type: 'stage', stage: 'contacted' } }
  }
  if (stage === 'contacted') {
    return { label: 'Start discovery', action: { type: 'stage', stage: 'discovery' } }
  }
  if (stage === 'discovery') {
    return discDone
      ? { label: 'Request demo', action: { type: 'stage', stage: 'demo_build' } }
      : { label: 'Continue discovery', action: { type: 'open', section: 'discovery' } }
  }
  if (stage === 'demo_build') {
    if (demo.status === 'ready' || demo.status === 'delivered') {
      return { label: 'Move to proposal', action: { type: 'stage', stage: 'demo_proposal' } }
    }
    // A ROUTE, NOT A SECTION. This used to expand the DEMO section on the page
    // the reader was already looking at, which is a disclosure rather than a
    // destination — "open demo build" scrolled you 200px and changed nothing.
    // It now goes where the work is actually done, which is the same place the
    // Demos to build queue sends you.
    return { label: 'Open demo build',
             action: { type: 'route', to: '/sales/demo-build/' + opp.id } }
  }
  if (stage === 'demo_proposal') {
    if (!prop) return { label: 'Prepare proposal', action: { type: 'open', section: 'proposal' } }
    if (!prop.sent_at) return { label: 'Send proposal', action: { type: 'open', section: 'proposal' } }
    return { label: 'Move to closing', action: { type: 'stage', stage: 'closing' } }
  }
  if (stage === 'closing') {
    return { label: 'Close won', action: { type: 'stage', stage: 'won' } }
  }
  if (stage === 'won' || stage === 'onboarding' || stage === 'live') {
    return { label: 'Open billing', action: { type: 'open', section: 'billing' } }
  }
  return { label: 'Book the next meeting', action: { type: 'meeting' } }
}

function Fact({ label, value, strong }) {
  return (
    <div className={'sw-cmd-fact' + (strong ? ' is-strong' : '')}>
      <span>{label}</span>
      <b>{value === null || value === undefined || value === '' ? '—' : value}</b>
    </div>
  )
}

export default function DealCommand({
  opp, closing, nextMeeting, alerts, saving, onAction, stageControl,
}) {
  const primary = primaryAction(opp, closing)
  const commercial = [
    opp.selected_package && opp.selected_package.name,
    opp.billing_option_label,
  ].filter(Boolean).join(' · ')

  return (
    <div className="sw-cmd">
      <div className="sw-cmd-top">
        <div className="sw-cmd-id">
          <div className="sw-chips" style={{ marginBottom: 6 }}>
            <Chip tone={opp.status === 'lost' ? 'red' : 'green'}>{opp.stage_label}</Chip>
            {opp.days_in_stage != null && <Chip>{opp.days_in_stage}d in stage</Chip>}
            {opp.owner_name && <Chip>{opp.owner_name}</Chip>}
          </div>
          <h2>{opp.company_name}</h2>
          <p>{[opp.contact_name, opp.phone, opp.email].filter(Boolean).join(' · ')
             || 'No contact details captured'}</p>
        </div>
        <div className="sw-cmd-act">
          <button className="sw-btn sw-primary" disabled={saving}
                  onClick={() => onAction(primary.action)}>
            {primary.label}
          </button>
          <button className="sw-btn" disabled={saving}
                  onClick={() => onAction({ type: 'meeting' })}>
            Find Team Time
          </button>
          {stageControl}
        </div>
      </div>

      {/* ── the alerts, only when there are any ─────────────────────────────
          An empty warning strip trains people to stop reading the strip. */}
      {alerts.length > 0 && (
        <div className="sw-cmd-alerts">
          {alerts.map((a, i) => (
            <span key={i} className={'sw-alert sw-' + (a.tone || 'amber')}>{a.text}</span>
          ))}
        </div>
      )}

      <div className="sw-cmd-facts">
        <Fact label="NEXT ACTION" strong value={opp.next_action} />
        <Fact label="NEXT MEETING" value={nextMeeting} />
        <Fact label="COMMERCIAL" value={commercial || 'Not set'} />
        <Fact label="INDUSTRY" value={opp.industry} />
      </div>
    </div>
  )
}

/**
 * One stage of the deal, as a section that knows whether it is behind, in
 * front of, or where the rep actually is.
 *
 * done     — collapsed to a one-line summary, reopenable
 * current  — expanded
 * upcoming — collapsed, still openable (a rep may prepare ahead; nothing here
 *            is a lock, because a lock would be a second lifecycle rule)
 */
export function StageSection({
  id, step, title, state, summary, badge, open, onToggle, children,
}) {
  return (
    <section className={'sw-sec is-' + state + (open ? ' is-open' : '')} id={'sec-' + id}>
      <button type="button" className="sw-sec-h" onClick={() => onToggle(id)}
              aria-expanded={open}>
        <span className="sw-sec-n">{state === 'done' ? '✓' : step}</span>
        <span className="sw-sec-t">
          <b>{title}</b>
          <small>{summary}</small>
        </span>
        {badge}
        <span className="sw-sec-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="sw-sec-b">{children}</div>}
    </section>
  )
}
