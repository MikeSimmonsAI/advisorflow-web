import { useEffect, useState } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import { getCurrentUser, refreshCurrentUser, logout, getBranding, clearBranding, applyBrandingCSS, applyBrandingDOM, fetchAndStoreBranding, getOrgContext, setOrgContext, clearOrgContext, clearBrandContext, api, stopKeepAlive, stopRefreshLoop } from '../api/client'
import { isManagerRole, roleOf, workspaceFeatures } from '../auth/workspaceAuthority'
import { enterCustomer as enterCustomerContext } from '../pages/god/enterCustomer'
import { detectTheme, shellTheme, shellThemeSource, productName, BRAND_CONFIG, THEMES } from '../theme.js'
import SignalPulse from './SignalPulse'
import NotificationBell from './NotificationBell'
import ProfileOnboarding from './ProfileOnboarding'
import GodReturnBar from './GodReturnBar'
import ContextSwitcher from './ContextSwitcher'
import WorkspaceAdminMenu from './WorkspaceAdminMenu'
// A render error inside one page unmounts React's whole tree, rail included.
// This keeps the failure inside the content area. See PageBoundary.
import PageBoundary from './PageBoundary'
import { isWholesalePath, WholesaleSearch, WholesaleEnvironment, WholesaleUser, useWholesaleCanvas, useWholesaleTitle, WholesaleProductLine } from './WholesaleShell'
// A VERTICAL'S OWN PRESENTATION. Nav labels, groups and skin for a workspace
// whose industry has one configured; null for everybody else, which is what
// keeps this from being a global redesign. See verticals/workspaceVertical.js.
import { verticalFor, navGroupsFor, brandLines } from '../verticals/workspaceVertical'
// The workspace's vocabulary cache, dropped alongside its branding cache.
// They answer the same question and must not be allowed to disagree.
import { clearTerminology } from '../terminology'
import '../styles/vertical-energy.css'
import '../styles/vertical-cleaning.css'
import './ContextSwitcher.css'
import './Layout.css'

// Detect which platform brand is running on this hostname — resolved once at module
// load time so it never changes mid-session.
const PLATFORM_THEME = detectTheme()
const PLATFORM_BRAND = BRAND_CONFIG[PLATFORM_THEME]

// ── NAVIGATION ───────────────────────────────────────────────────────────────
//
// Grouped by WHAT SOMEBODY IS DOING, because the flat list had grown to twenty
// items in two undifferentiated blocks and could not be used without scrolling
// and reading every label. The groups are:
//
//   WORKSPACE      the day's work on leads and the diary
//   ENGAGEMENT     the machinery that reaches out
//   OPERATIONS     the data behind it
//   ADMINISTRATION who may do what, and what it costs
//
// Nothing was removed and no route changed. Two labels did:
//
//   "Branding & Settings" -> "Organization"   — it wrapped onto two lines and
//     overlapped the item beneath it, and it sat next to a separate "Settings"
//     so the pair read as duplicates. They are not: /settings is the person's
//     own profile, sender and calendars; /org-settings is the business.
//
//   "Master Dashboard" -> "Team Performance"  — it sounds like platform
//     administration and is not. Every endpoint it calls (/admin/dashboard,
//     /admin/leads, /admin/leads/unassigned, /admin/dashboard/metrics) is
//     scoped to the caller's own organization: it is this org's advisors,
//     lead pool and funnel. The old name invited an org admin to expect
//     platform-wide reach and a platform owner to look for it in the wrong
//     place. God/platform functions live under Platform Admin, below.

const NAV_GROUPS = [
  {
    label: 'Workspace',
    items: [
      { to: '/', label: 'Overview', icon: 'grid' },
      // LAUNCH — the customer's onboarding. Shown ONLY while setup is still
      // open. A completed launch remains reviewable from Organization settings
      // for admins, but no longer sits in the normal operational rail.
      // `launchOnly` is answered by a single GET /launch/me on mount.
      { to: '/launch', label: 'Launch', icon: 'zap', launchOnly: true },
      { to: '/leads', label: 'Leads', icon: 'users', featureKey: 'leads' },
      // MY WORK — /workqueue. It has existed and worked for a long time with
      // no entry in this rail: the only ways in were a card on Overview and a
      // button on the sales MyDay page, so a rep who did not go through
      // Overview could not find the one screen in the product that tells them
      // what to do next. NO featureKey: it derives from leads the caller is
      // already entitled to and answers honestly when there is nothing to do.
      { to: '/workqueue', label: 'My Work', icon: 'check-square' },
      { to: '/replies', label: 'Replies', icon: 'message' },
      { to: '/activity', label: 'Activity', icon: 'send' },
      { to: '/availability', label: 'Availability', icon: 'calendar', featureKey: 'availability' },
      { to: '/fiber-capture', label: 'Fiber Lead', icon: 'zap', fiberOnly: true },
    ],
  },
  {
    label: 'Engagement',
    items: [
      { to: '/ai-hub', label: 'AI Hub', icon: 'cpu' },
      // NO featureKey and NO adminOnly, on purpose. /workforce/team is
      // require_tenant_user and answers honestly for a customer who has hired
      // nobody — an empty team is a real answer, and a hidden link would tell
      // an advisor the product does not exist rather than that it is off.
      // Whether an AI employee may actually act is decided at execution time
      // by activation + entitlement on the server; this link decides nothing.
      { to: '/ai-team', label: 'Your AI Team', icon: 'users' },
      // ADMIN ONLY, and NO featureKey. Hiring, configuring and asking for an
      // AI employee to be started are administrative acts, which is what the
      // routes behind this enforce. No feature key, for the reason the line
      // above gives: the workforce's own entitlement is answered per employee
      // by the server, and a key this platform has never heard of would hide
      // the screen for everyone.
      { to: '/ai-workforce', label: 'My AI Workforce', icon: 'package', adminOnly: true },
      // T9 — MANAGING the workforce rather than hiring it. NOT adminOnly and
      // NO featureKey, for the reason the two lines above give twice over: an
      // empty command centre is a real answer for a customer who has hired
      // nobody, and the person who has to clear a handoff or decide a review
      // is often not an org admin. Everything behind it is scoped to the
      // caller's own workspace by the route signatures, and every write is
      // additionally gated by require_not_observation on the server.
      { to: '/ai-workforce-command', label: 'Workforce Command', icon: 'activity' },
      { to: '/email-queue', label: 'Email Queue', icon: 'mail', featureKey: 'email' },
      { to: '/campaigns', label: 'Campaigns', icon: 'target', adminOnly: true, featureKey: 'campaigns' },
      // NO featureKey. `proposals` is not a key in app/services/entitlements.py,
      // and asking isFeatureEnabled() for a key the server has never heard of is
      // precisely the mistake documented in that file. `adminOnly` is the honest
      // test here because require_admin is what /proposals/* actually enforces.
      { to: '/proposals', label: 'Proposals', icon: 'file-text', adminOnly: true },
      { to: '/cadence', label: 'Cadence', icon: 'repeat', adminOnly: true, featureKey: 'cadences' },
      // `leads`, because that is all this page is. It calls /leads/ three
      // times — hot, warm and cold — and renders nothing else. Without a
      // feature key it stayed in the sidebar for a workspace without the lead
      // module and opened onto three refusals, which the page then swallowed
      // into "no leads in any temperature". The one surface still out of step
      // with the other four.
      { to: '/re-engagement', label: 'Re-engagement', icon: 'thermometer', featureKey: 'leads' },
      { to: '/compliance', label: 'DNC List', icon: 'shield-check', featureKey: 'compliance' },
    ],
  },
  // ── WHOLESALE REAL ESTATE ────────────────────────────────────────────────
  //
  // Its own group, because it is a whole operating mode rather than one more
  // screen: a workspace that runs it works here all day, and a workspace that
  // does not never sees the group at all.
  //
  // `featureKey` on every item and NO adminOnly on the first three. The
  // entitlement key is real — `wholesale_real_estate` is registered in
  // app/services/entitlements.py, which is the test the note above the
  // Proposals item says to apply before putting a key here. Settings is
  // adminOnly because the offer formula, the approval gates and the provider
  // configuration are administrative; the day-to-day screens are not, and
  // making them so would hide the product from the acquisitions person who
  // actually uses it.
  // Phase 7.2: ONE product, TWO operating worlds. EvoSense FINDS the deal
  // (acquisition); Wholesale Operations MOVES it (deals already in the
  // transaction workflow). There is no longer a "Command Center" in each:
  // Acquisition Command is EvoSense's, Deal Operations is the transaction
  // board. Contracts & Closing and Dispositions are focused views of the
  // deals that are actually in those stages - not new pages.
  {
    label: 'Acquisition · EvoSense',
    items: [
      { to: '/wholesale/evosense', label: 'Acquisition Command', icon: 'target', featureKey: 'wholesale_real_estate', end: true },
      { to: '/wholesale/evosense/inbox', label: 'Discovery Inbox', icon: 'search', featureKey: 'wholesale_real_estate' },
      { to: '/wholesale/evosense/strategies', label: 'Strategies', icon: 'zap', featureKey: 'wholesale_real_estate' },
      { to: '/wholesale/evosense/controls', label: 'Providers & Controls', icon: 'shield-check', featureKey: 'wholesale_real_estate' },
    ],
  },
  {
    label: 'Wholesale Operations',
    items: [
      { to: '/wholesale', label: 'Deal Operations', icon: 'activity', featureKey: 'wholesale_real_estate', end: true },
      { to: '/wholesale/properties', label: 'Properties', icon: 'home', featureKey: 'wholesale_real_estate' },
      { to: '/wholesale/buyers', label: 'Cash Buyers', icon: 'users', featureKey: 'wholesale_real_estate' },
      { to: '/wholesale/closing', label: 'Contracts & Closing', icon: 'file-text', featureKey: 'wholesale_real_estate' },
      { to: '/wholesale/dispositions', label: 'Dispositions', icon: 'send', featureKey: 'wholesale_real_estate' },
      { to: '/wholesale/settings', label: 'Wholesale Settings', icon: 'sliders', adminOnly: true, featureKey: 'wholesale_real_estate' },
    ],
  },
  {
    label: 'Operations',
    items: [
      { to: '/crm', label: 'CRM', icon: 'database', adminOnly: true, featureKey: 'crm' },
      { to: '/crm-connectors', label: 'CRM Connectors', icon: 'link', adminOnly: true, featureKey: 'crm_connectors' },
      { to: '/lead-cleanup', label: 'Lead Cleanup', icon: 'users', adminOnly: true, featureKey: 'lead_cleanup' },
      { to: '/admin', label: 'Team Performance', icon: 'shield', adminOnly: true, featureKey: 'master_dashboard' },
      { to: '/reports', label: 'Reports', icon: 'activity', adminOnly: true, featureKey: 'reports' },
      { to: '/import-batches', label: 'Lead Imports', icon: 'upload', adminOnly: true, featureKey: 'imports' },
    ],
  },
  {
    label: 'Administration',
    items: [
      { to: '/users', label: 'Users', icon: 'user-plus', adminOnly: true, featureKey: 'users' },
      { to: '/settings', label: 'My Settings', icon: 'settings' },
      { to: '/org-settings', label: 'Organization', icon: 'building', adminOnly: true, featureKey: 'branding_settings' },
      { to: '/tier-definitions', label: 'Tier Config', icon: 'layers', adminOnly: true, featureKey: 'tier_config' },
      { to: '/audit-log', label: 'Audit Log', icon: 'activity', adminOnly: true, featureKey: 'audit_log' },

      // ── ADMINISTRATION OF INFRASTRUCTURE, not use of a feature ───────────
      //
      // `capability` is a different test from `featureKey` and the difference
      // is the point of this whole change:
      //
      //   featureKey   does this customer USE the service?
      //   capability   may this organization ADMINISTER the infrastructure,
      //                AND is THIS person one of its named administrators?
      //
      // A2P used to sit above with `featureKey: 'a2p_10dlc'` — a key the
      // server had never heard of — plus `adminOnly`, so any org admin could
      // register the company's carrier brand. System Health and Billing had no
      // key at all, so no entitlement could switch them off.
      //
      // The list comes from GET /settings/my-capabilities, which calls the SAME
      // resolver the routes call. This is not access control: every route below
      // enforces its own capability. It stops the sidebar offering doors that
      // open onto a 403.
      { to: '/10dlc', label: 'A2P 10DLC', icon: 'shield-check', capability: 'a2p_10dlc' },
      { to: '/system-health', label: 'System Health', icon: 'activity', capability: 'platform_health' },
      { to: '/billing', label: 'Billing', icon: 'credit-card', capability: 'platform_billing' },
    ],
  },
  // ── HELP & SUPPORT ────────────────────────────────────────────────────
  //
  // NO adminOnly, NO featureKey, NO capability, and each omission is a
  // decision rather than an oversight:
  //
  //   adminOnly   the person who notices the product is broken is whoever
  //               was using it. Routing them through their administrator is
  //               how a fault takes two days to reach us.
  //   featureKey  a customer whose plan is missing a flag must still be able
  //               to tell us the product is not working. Support is not one
  //               of the things a package switches off.
  //   capability  nothing here administers infrastructure. Reading your own
  //               ticket is not a privileged act.
  //
  // Its own group at the bottom of the rail, because it is where somebody
  // looks when something has gone wrong rather than something they use as
  // part of the day.
  {
    label: 'Help',
    items: [
      { to: '/help', label: 'Help & Support', icon: 'life-buoy' },
    ],
  },
]

// Platform Admin — super admin only, always visible
const SUPER_ADMIN_NAV_ITEMS = [
  { to: '/provision-client', label: 'Provision Client', icon: 'user-plus' },
  { to: '/templates', label: 'Templates', icon: 'file-text' },
  { to: '/cadence-templates', label: 'Cadence Builder', icon: 'sliders' },
  { to: '/orgs', label: 'Org Manager', icon: 'building' },
]

function Icon({ name }) {
  const paths = {
    grid: <path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z" />,
    users: <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />,
    message: <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />,
    repeat: <path d="M17 1l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3" />,
    mail: <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6" />,
    zap: <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />,
    send: <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" />,
    settings: <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />,
    shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
    'shield-check': <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></>,
    'file-text': <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8" />,
    'user-plus': <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM20 8v6M23 11h-6" />,
    target: <><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" /></>,
    activity: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />,
    sliders: <><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></>,
    calendar: <><rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></>,
    cpu: <><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><line x1="9" y1="1" x2="9" y2="4" /><line x1="15" y1="1" x2="15" y2="4" /><line x1="9" y1="20" x2="9" y2="23" /><line x1="15" y1="20" x2="15" y2="23" /><line x1="20" y1="9" x2="23" y2="9" /><line x1="20" y1="14" x2="23" y2="14" /><line x1="1" y1="9" x2="4" y2="9" /><line x1="1" y1="14" x2="4" y2="14" /></>,
    phone: <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 13a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 3.6 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 9.91a16 16 0 0 0 6.08 6.08l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" />,
    sun: <><circle cx="12" cy="12" r="5" /><line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" /><line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" /><line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" /><line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" /></>,
    moon: <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />,
    link: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></>,
    building: <><rect x="2" y="7" width="20" height="15" rx="1" /><line x1="16" y1="22" x2="16" y2="7" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M7 22v-5h4v5" /><polyline points="2 7 2 5 22 5 22 7" /></>,
    layers: <><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /><polyline points="2 12 12 17 22 12" /></>,
    thermometer: <><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z" /></>,
    database: <><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" /><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" /></>,
    'credit-card': <><rect x="1" y="4" width="22" height="16" rx="2" ry="2" /><line x1="1" y1="10" x2="23" y2="10" /></>,
    search: <><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></>,
    'life-buoy': <><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="4" /><line x1="4.93" y1="4.93" x2="9.17" y2="9.17" /><line x1="14.83" y1="14.83" x2="19.07" y2="19.07" /><line x1="14.83" y1="9.17" x2="19.07" y2="4.93" /><line x1="4.93" y1="19.07" x2="9.17" y2="14.83" /></>,
    upload: <><polyline points="16 16 12 12 8 16" /><line x1="12" y1="12" x2="12" y2="21" /><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3" /></>,
    // ADDED WITH THE NAV ITEM THAT USES IT. A name this map does not hold
    // renders an EMPTY svg rather than failing, so a nav entry with an unknown
    // icon looks like a broken link and nothing anywhere says why.
    package: <><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /><polyline points="3.27 6.96 12 12.01 20.73 6.96" /><line x1="12" y1="22.08" x2="12" y2="12" /></>,
    // Added with the Wholesale nav group, per the note above: a name this map
    // does not hold renders an EMPTY svg, so the entry would look like a broken
    // link and nothing anywhere would say why.
    home: <><path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" /></>,
    // Added with the "My Work" nav item, per the note above.
    'check-square': <><polyline points="9 11 12 14 22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></>,
    // NAMED BY CONFIGURATION, SO THEY HAVE TO EXIST HERE.
    // `truck` and `refresh` are the icons config/workspace-views/*.json
    // already asks for, and `trending-up` is the vertical rail's Sales
    // Pipeline. A name this map does not hold renders an EMPTY svg — which is
    // what those two configured screens have been drawing.
    truck: <><rect x="1" y="3" width="15" height="13" /><polygon points="16 8 20 8 23 11 23 16 16 16 16 8" /><circle cx="5.5" cy="18.5" r="2.5" /><circle cx="18.5" cy="18.5" r="2.5" /></>,
    refresh: <><polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" /></>,
    'trending-up': <><polyline points="23 6 13.5 15.5 8.5 10.5 1 18" /><polyline points="17 6 23 6 23 12" /></>,
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  )
}

function LiveClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="top-bar-clock">
      <span className="top-bar-time">
        {now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </span>
      <span className="top-bar-date">
        {now.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
      </span>
    </div>
  )
}

function ThemeToggle({ branding }) {
  // A branded shell (brand domain, or a workspace whose platform has its own
  // theme) owns data-theme; only the BookaBoost default offers light/dark.
  const isBrandTheme = shellTheme(branding) !== THEMES.BOOKABOOST
  const [dark, setDark] = useState(() => {
    if (isBrandTheme) return true
    const saved = localStorage.getItem('af_theme')
    return saved !== 'light'
  })
  useEffect(() => {
    if (isBrandTheme) return
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
    localStorage.setItem('af_theme', dark ? 'dark' : 'light')
  }, [dark, isBrandTheme])
  if (isBrandTheme) return null
  return (
    <button className="theme-toggle" onClick={() => setDark(!dark)} title={dark ? 'Switch to light mode' : 'Switch to dark mode'}>
      <Icon name={dark ? 'sun' : 'moon'} />
    </button>
  )
}

export default function Layout({ children }) {
  const [user, setUser] = useState(() => getCurrentUser())
  const navigate = useNavigate()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // DOES THIS ORGANIZATION HAVE AN OPEN LAUNCH? One cheap call, once per
  // mount, answered by the server's implementation status rather than guessed
  // from a percentage, role or plan name. `null` = not yet known and the nav
  // item stays hidden; a 404 is the normal answer for many orgs and is not an
  // error. `live` is the authoritative completed state.
  const [launchNavState, setLaunchNavState] = useState(null)
  useEffect(() => {
    let alive = true
    api.get('/launch/me', { skipRedirect: true })
      .then(d => {
        if (!alive) return
        const status = d?.implementation?.status
        setLaunchNavState({ exists: true, completed: status === 'live' })
      })
      .catch(() => { if (alive) setLaunchNavState({ exists: false, completed: false }) })
    return () => { alive = false }
  }, [])
  // Non-admin regular advisors start collapsed; admins start expanded
  const isAdmin = user?.role === 'org_admin' || user?.role === 'super_admin' || user?.role === 'god_admin'
  // Remembered per browser so the choice survives a reload. Falls back to the
  // old behaviour (advisors start collapsed, admins expanded) on first visit,
  // and tolerates storage being unavailable in private mode.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      const saved = localStorage.getItem('af_sidebar_collapsed')
      if (saved === '1') return true
      if (saved === '0') return false
    } catch { /* storage blocked */ }
    return !isAdmin
  })
  useEffect(() => {
    try { localStorage.setItem('af_sidebar_collapsed', sidebarCollapsed ? '1' : '0') }
    catch { /* storage blocked */ }
  }, [sidebarCollapsed])
  const [profilePhoto, setProfilePhoto] = useState(null)
  const [logoFailed, setLogoFailed] = useState(false)
  const isSuperAdmin = user?.role === 'super_admin'
  const isGodAdmin = user?.role === 'god_admin'
  const isElevated = isSuperAdmin || isGodAdmin
  const [orgContext, setOrgCtx] = useState(() => isElevated ? getOrgContext() : null)
  const [branding, setBranding] = useState(() => getBranding())
  const [allOrgs, setAllOrgs] = useState([])
  const [orgPickerOpen, setOrgPickerOpen] = useState(false)

  // THE SHARED ANSWER, not this file's own copy of it.
  //
  // This read `isElevated ? null : featuresOf(branding)`, which is a THIRD
  // rule: an operator saw every module of every customer even while standing
  // inside one whose modules were switched off. Atlantis Light & Power has no
  // modules at all and customer-view still drew Leads, Campaigns, CRM, Imports
  // and the rest — the exact screen this work exists to stop drawing, shown to
  // the one person who would be asked to explain it.
  //
  // `workspaceFeatures` is now that rule, and the dashboard and the route
  // guard evaluate the same expression. See auth/workspaceRules.js.
  const enabledFeatures = workspaceFeatures(branding, user, orgContext)
  const isFeatureEnabled = (key) => !key || enabledFeatures === null || enabledFeatures.includes(key)

  // WHAT THIS PERSON MAY ADMINISTER, ANSWERED BY THE SERVER.
  //
  // Starts as null meaning "not answered yet", which renders as NO capability
  // items rather than as all of them. A sidebar that shows A2P for a moment
  // before the answer arrives has already told an org admin the door exists.
  const [myCaps, setMyCaps] = useState(null)
  useEffect(() => {
    let cancelled = false
    api.get('/settings/my-capabilities')
      .then(d => { if (!cancelled) setMyCaps(Array.isArray(d?.capabilities) ? d.capabilities : []) })
      // A failed call means "not entitled" for rendering purposes. It cannot
      // grant anything, and the routes refuse independently either way.
      .catch(() => { if (!cancelled) setMyCaps([]) })
    return () => { cancelled = true }
  }, [orgContext])
  const hasCapability = (key) => !key || (myCaps !== null && myCaps.includes(key))

  // WHAT THIS WORKSPACE CALLS ITS OWN SCREENS. A vertical's workflow views
  // are configuration - a row on the organization, or its industry's default
  // - so the rail cannot know them from a static array. One cheap call,
  // re-run when the workspace changes, because the whole point of keying the
  // terminology cache by workspace is that one customer's vocabulary must not
  // appear in another's rail.
  //
  // `[]` while unknown, not a spinner: an item that appears a moment late is
  // better than one that flashes and vanishes, which is the same rule the
  // Launch item above follows. A failure is silent for the same reason a 404
  // on /launch/me is - most organizations legitimately have none.
  const [configuredViews, setConfiguredViews] = useState([])
  useEffect(() => {
    let alive = true
    api.get('/workspace-views', { skipRedirect: true })
      .then(d => { if (alive) setConfiguredViews(Array.isArray(d?.views) ? d.views : []) })
      .catch(() => { if (alive) setConfiguredViews([]) })
    return () => { alive = false }
  }, [orgContext])

  // ── THIS WORKSPACE'S OWN PRESENTATION ──────────────────────────────────
  //
  // Resolved from the ACTIVE workspace's industry, which `GET /branding/org`
  // answers — so an operator standing inside a customer gets the customer's
  // vertical and stepping back out returns the platform rail on the next
  // render. `null` for every workspace without one configured, and every
  // line below that consumes it falls through to the platform behaviour.
  const vertical = verticalFor(branding)

  // PHASE 7.3 — THE FOCUSED WHOLESALE SHELL. Inside /wholesale (and never
  // inside a configured vertical, whose rail is its own) the rail shows the
  // two wholesale worlds and folds every other platform screen under
  // "EvoSys Platform". Nothing is removed: the same items, the same
  // visibility rules, one click further away. See WholesaleShell.jsx.
  const inWholesale = !vertical && isWholesalePath(location.pathname)
  const [platformOpen, setPlatformOpen] = useState(false)
  useWholesaleCanvas(inWholesale)

  // WHICH WORKSPACE THE ROUTED PAGE BELONGS TO.
  //
  // The organization the SERVER resolved, not the one stored locally: it is
  // the same value every screen below renders from, so a page keyed on it
  // cannot disagree with the rail beside it. The org context is in the key as
  // well for the moment before the branding answer lands, when the two are
  // briefly the only evidence of a switch. Used by the boundary around
  // `children` at the bottom of this file — the comment there says why.
  const workspaceKey = [branding?.organization_id || '',
                        orgContext?.orgId || ''].join('|')

  // The skin is CSS keyed on an attribute, not a stylesheet swap: one
  // attribute on <html> re-points the design tokens that index.css already
  // defines, so panels, tables, badges and empty states follow without any
  // of them being rewritten. Removed on unmount and whenever the workspace
  // stops being one of these — a stale skin after a switch would paint one
  // customer's workspace in another's colours.
  useEffect(() => {
    const root = document.documentElement
    if (vertical) root.setAttribute('data-workspace-vertical', vertical.skin)
    else root.removeAttribute('data-workspace-vertical')
    return () => { root.removeAttribute('data-workspace-vertical') }
  }, [vertical])

  function handleExitOrg() {
    clearOrgContext()
    clearBranding()
    // The vocabulary cache describes the same workspace the branding cache
    // does. Dropping one and keeping the other is how a customer's company
    // name outlived the customer. See terminology.js.
    clearTerminology()
    setOrgCtx(null)
    window.location.href = '/'
  }

  // For god_admin: fetch all orgs so they can switch into any org's view
  useEffect(() => {
    if (!isGodAdmin) return
    api.get('/god/orgs?limit=200').then(data => {
      const list = Array.isArray(data) ? data : (data?.orgs || [])
      setAllOrgs(list)
    }).catch(() => {})
  }, [isGodAdmin])

  // Close org-picker when clicking outside
  useEffect(() => {
    if (!orgPickerOpen) return
    function handleOutsideClick(e) {
      if (!e.target.closest('.god-org-picker')) setOrgPickerOpen(false)
    }
    document.addEventListener('mousedown', handleOutsideClick)
    return () => document.removeEventListener('mousedown', handleOutsideClick)
  }, [orgPickerOpen])

  async function handleOrgSelect(org) {
    setOrgPickerOpen(false)
    if (!org) {
      clearOrgContext()
      clearBrandContext()
      clearBranding()
      clearTerminology()
      setOrgCtx(null)
      window.location.href = '/god'
    } else {
      // Use the shared entry helper so brand context is also set from the
      // server's resolved platform — prevents stale X-Brand-Override when
      // switching between customers that belong to different brands.
      try {
        await enterCustomerContext(org.id, org.name)
        setOrgCtx({ orgId: org.id, orgName: org.name })
        window.location.href = '/god/customer-app'
      } catch (_) {
        // Fall back to local context only if the server call fails
        setOrgContext(org.id, org.name)
        clearBranding()
        clearTerminology()
        setOrgCtx({ orgId: org.id, orgName: org.name })
        window.location.href = '/god/customer-app'
      }
    }
  }

  // THE OPERATOR INSIDE A CUSTOMER NEEDS THIS CALL TOO.
  //
  // This returned early for every elevated user, so `af_branding` stayed at
  // whatever login left behind — `{enabled_features: null, workspace_role:
  // null, organization_id: null}` — for the whole session. null means
  // "legacy-open", so customer-view could not have hidden a module even after
  // the sidebar started asking: there was nothing to ask. The fetch is what
  // makes the answer exist, and `X-Org-Override` travels with it on
  // customer-class paths, so the server resolves the ENTERED customer and
  // returns THEIR allow-list.
  //
  // The theme is NOT applied for an operator. Customer-view should report the
  // customer's entitlements, not repaint God Mode in the customer's colours —
  // the header, the logo and the banner already say whose workspace this is.
  useEffect(() => {
    if (isElevated && !orgContext) return
    const stored = getBranding()
    if (stored && !isElevated) { applyBrandingCSS(stored); applyBrandingDOM(stored) }
    fetchAndStoreBranding({ applyTheme: !isElevated }).then(b => { if (b) setBranding(b) })
  }, [isElevated, orgContext, location.pathname])

  useEffect(() => {
    refreshCurrentUser().then(p => {
      if (p?.profile_photo_url) setProfilePhoto(p.profile_photo_url)
      setUser(getCurrentUser())
    }).catch(() => {})
  }, [])

  function closeSidebar() { if (window.innerWidth <= 1024) setSidebarOpen(false) }

  async function handleLogout() {
    stopKeepAlive()
    stopRefreshLoop()
    await logout()
    window.location.href = '/login'
  }

  // The brand of THIS WORKSPACE's shell: the host's brand on a brand domain,
  // otherwise the platform the organization belongs to (Phase 7.2 - this is
  // what stopped an EvoSys Pro workspace rendering as BookaBoost locally).
  const SHELL_BRAND = BRAND_CONFIG[shellTheme(branding)] || PLATFORM_BRAND
  // THE BRAND-RESOLUTION GATE READS THIS. Which theme the shell resolved and
  // WHERE it came from ('host' | 'workspace' | 'default'), stamped on the
  // layout root so every acceptance run can prove the authenticated workspace's
  // platform - not a hostname default - decided the brand (brand_gate.py).
  const shellResolution = shellThemeSource(branding)
  // The commercial product name this brand gives the Wholesale module
  // (EvoSysPro -> "EvoSys Wholesale"); the neutral module name otherwise.
  const WHOLESALE_PRODUCT = productName(branding, 'wholesale') || 'Wholesale'
  useWholesaleTitle(inWholesale, WHOLESALE_PRODUCT)
  const brandName = isGodAdmin ? 'AdvisorFlow' : (branding?.brand_name || SHELL_BRAND.displayName)
  // WHAT THE WORKSPACE ITSELF IS CALLED, which is not the same question as
  // "what brand is this app". Used only by the vertical wordmark, where the
  // answer must be the CUSTOMER even when an operator is the one looking —
  // `orgContext.orgName` is the customer an operator entered, and a
  // customer's own staff have no orgContext, so their branding row answers.
  const workspaceName = orgContext?.orgName || branding?.brand_name || ''
  const logoUrl = vertical
    // The customer's own mark, operator or not. Falling back to the platform
    // logo here would put the white-label product's badge on the customer's
    // rail, which is the one thing this presentation exists to prevent.
    ? (branding?.brand_logo_url || null)
    : isElevated
      ? (PLATFORM_BRAND.logoUrl || null)
      : (branding?.brand_logo_url || SHELL_BRAND.logoUrl || null)

  // Reset logo failure state when the URL changes (e.g. org switch)
  useEffect(() => { setLogoFailed(false) }, [logoUrl])

  return (
    <div className={`layout ${sidebarOpen ? 'layout--sidebar-open' : ''}${inWholesale ? ' layout--wholesale' : ''}`}
         data-brand-theme={shellResolution.theme} data-brand-source={shellResolution.source}
         data-brand-platform={branding?.platform?.slug || ''} data-org-id={branding?.organization_id || ''}
         data-product={inWholesale ? WHOLESALE_PRODUCT : undefined}>
      <button type="button" className="mobile-menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open navigation menu">
        <span /><span /><span />
      </button>
      <button type="button" className="sidebar-backdrop" onClick={closeSidebar} aria-label="Close navigation menu" />

      <aside className={`sidebar${sidebarCollapsed ? ' sidebar--collapsed' : ''}`} style={{ width: sidebarCollapsed ? 60 : undefined, minWidth: sidebarCollapsed ? 60 : undefined, transition: 'width 0.2s, min-width 0.2s', ...(isGodAdmin && !vertical ? { borderRight: '1px solid rgba(245,158,11,0.3)', background: 'linear-gradient(180deg, rgba(245,158,11,0.06) 0%, transparent 120px)' } : {}) }}>
        <div className="sidebar-brand" style={{ position: 'relative', ...(isGodAdmin && !vertical ? { borderBottom: '1px solid rgba(245,158,11,0.25)' } : {}) }}>
          {/* THE WORKSPACE'S OWN WORDMARK COMES FIRST, INCLUDING FOR AN
              OPERATOR. Standing inside a customer of a configured vertical,
              the rail names the CUSTOMER — the platform's own brand block
              would tell their staff (and anybody looking over a shoulder in a
              demo) which white-label product they are actually inside. The
              operator's way back is unaffected: every one of those actions is
              in WorkspaceAdminMenu at the end of the top bar. */}
          {vertical && !sidebarCollapsed ? (
            <div className="vertical-brand">
              {logoUrl && !logoFailed ? (
                <img className="vertical-brand-logo" src={logoUrl} alt=""
                     onError={() => setLogoFailed(true)} />
              ) : (
                <span className="vertical-brand-glyph" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" strokeWidth="2"
                       strokeLinecap="round" strokeLinejoin="round">
                    <polygon points="12 3 21 12 12 21 3 12" />
                  </svg>
                </span>
              )}
              <span className="vertical-brand-text">
                {brandLines(workspaceName).map((line, i) => (
                  <span key={i} className={i === 0 ? 'vertical-brand-line1' : 'vertical-brand-line2'}>
                    {line}
                  </span>
                ))}
              </span>
            </div>
          ) : vertical && sidebarCollapsed ? (
            <span className="vertical-brand-glyph" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round">
                <polygon points="12 3 21 12 12 21 3 12" />
              </svg>
            </span>
          ) : isGodAdmin ? (
            sidebarCollapsed ? (
              <span style={{ fontSize: 22, lineHeight: 1 }}>⚡</span>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 20, lineHeight: 1 }}>⚡</span>
                  <span className="brand-mark" style={{ color: '#f59e0b', letterSpacing: '0.04em' }}>AdvisorFlow</span>
                </div>
                <span style={{ fontSize: 10, color: '#b45309', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', paddingLeft: 28 }}>God Mode</span>
              </div>
            )
          ) : logoUrl && !logoFailed ? (
            sidebarCollapsed ? null : (
              <span className={inWholesale ? 'wsx-brand' : undefined} style={inWholesale ? undefined : { display: 'contents' }}>
                <img
                  src={logoUrl}
                  alt={brandName}
                  style={{ height: 72, maxWidth: 180, objectFit: 'contain', borderRadius: 6, display: 'block', margin: '0 auto' }}
                  onError={() => setLogoFailed(true)}
                />
                {inWholesale ? <WholesaleProductLine name={WHOLESALE_PRODUCT} /> : null}
              </span>
            )
          ) : (
            sidebarCollapsed ? (
              <SignalPulse color="blue" size={9} />
            ) : (
              inWholesale
                ? <span className="wsx-brand"><span className="brand-mark">{brandName}</span><WholesaleProductLine name={WHOLESALE_PRODUCT} /></span>
                : <><SignalPulse color="blue" size={9} /><span className="brand-mark">{brandName}</span></>
            )
          )}
          <button type="button" className="sidebar-close-btn" onClick={closeSidebar} aria-label="Close">×</button>
          <button
            type="button"
            onClick={() => setSidebarCollapsed(c => !c)}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            style={{
              position: 'absolute', right: 6, bottom: 6,
              background: 'none', border: 'none', color: '#777', cursor: 'pointer',
              fontSize: 18, padding: '2px 5px', borderRadius: 4, lineHeight: 1,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            {sidebarCollapsed ? '›' : '‹'}
          </button>
        </div>

        <nav className="sidebar-nav">
          {/* God admin: Command Center + org switcher.
              NOT INSIDE A VERTICAL WORKSPACE. The customer's rail should begin
              with the customer's first screen; an amber Command Center entry
              and a platform workspace selector above it are the two items that
              made this read as somebody's admin console with the customer
              loaded into it. Both actions are in WorkspaceAdminMenu, in the
              top bar, one click away — and Atlantis's own staff never had
              either, because this block was already god-only. */}
          {isGodAdmin && !vertical && (
            <>
              <NavLink to="/god"
                className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                style={({ isActive }) => ({
                  color: isActive ? '#f59e0b' : '#d97706',
                  background: isActive ? 'rgba(245,158,11,0.12)' : 'transparent',
                  borderLeft: isActive ? '3px solid #f59e0b' : '3px solid transparent',
                  fontWeight: 600,
                })}
                onClick={closeSidebar}
                title="Command Center"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
                {!sidebarCollapsed && 'Command Center'}
              </NavLink>

              {/* Org switcher — lets god_admin enter any org's regular app view */}
              {!sidebarCollapsed && <div className="god-org-picker" style={{ position: 'relative', padding: '6px 10px' }}>
                <button
                  type="button"
                  onClick={() => setOrgPickerOpen(o => !o)}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    gap: 6, padding: '7px 10px',
                    background: orgPickerOpen ? 'rgba(245,158,11,0.15)' : 'rgba(245,158,11,0.08)',
                    border: '1px solid rgba(245,158,11,0.3)', borderRadius: 6,
                    color: orgContext ? '#fbbf24' : '#92400e',
                    fontSize: 12, fontWeight: 600, cursor: 'pointer', letterSpacing: '0.02em',
                  }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
                    <span style={{ fontSize: 13 }}>{orgContext ? '👁' : '🌐'}</span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {orgContext ? orgContext.orgName : 'All Orgs (God View)'}
                    </span>
                  </span>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                    style={{ flexShrink: 0, transform: orgPickerOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>

                {orgPickerOpen && (
                  <div style={{
                    position: 'absolute', top: 'calc(100% - 2px)', left: 10, right: 10, zIndex: 200,
                    background: 'var(--surface-2, #1a1a2e)', border: '1px solid rgba(245,158,11,0.35)',
                    borderRadius: 6, boxShadow: '0 8px 24px rgba(0,0,0,0.5)', maxHeight: 260, overflowY: 'auto', fontSize: 12,
                  }}>
                    <button type="button" onClick={() => handleOrgSelect(null)} style={{
                      width: '100%', textAlign: 'left', padding: '9px 12px',
                      background: !orgContext ? 'rgba(245,158,11,0.15)' : 'transparent',
                      color: !orgContext ? '#fbbf24' : '#a3a3a3',
                      border: 'none', borderBottom: '1px solid rgba(245,158,11,0.15)', cursor: 'pointer',
                      fontWeight: !orgContext ? 700 : 500, display: 'flex', alignItems: 'center', gap: 6,
                    }}>
                      <span>🌐</span> All Orgs (God View)
                      {!orgContext && <span style={{ marginLeft: 'auto', color: '#f59e0b' }}>✓</span>}
                    </button>
                    {allOrgs.length === 0 && (
                      <div style={{ padding: '10px 12px', color: '#6b7280', fontStyle: 'italic' }}>Loading orgs…</div>
                    )}
                    {allOrgs.map(org => (
                      <button key={org.id} type="button" onClick={() => handleOrgSelect(org)} style={{
                        width: '100%', textAlign: 'left', padding: '8px 12px',
                        background: orgContext?.orgId === org.id ? 'rgba(245,158,11,0.12)' : 'transparent',
                        color: orgContext?.orgId === org.id ? '#fbbf24' : '#d1d5db',
                        border: 'none', borderBottom: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer',
                        fontWeight: orgContext?.orgId === org.id ? 600 : 400,
                        display: 'flex', alignItems: 'center', gap: 6,
                      }}>
                        <span style={{ fontSize: 11, opacity: 0.6 }}>🏢</span>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{org.name}</span>
                        {orgContext?.orgId === org.id && <span style={{ marginLeft: 'auto', color: '#f59e0b', flexShrink: 0 }}>✓</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>}

              {/* Lead Scraper removed from sidebar — accessible via God Command Center at /god */}
            </>
          )}

          {/* Grouped navigation. Visibility rules are UNCHANGED — an item that
              needed org_admin still needs it, and an item behind a feature flag
              is still behind it. Grouping decides only where a visible item
              sits. A group whose items are all hidden renders nothing at all,
              so a plain advisor does not see four empty headings. */}
          {(() => {
            // THE ROLE IN THIS WORKSPACE, not the one on the user row.
            //
            // This read `user.role` out of localStorage, which is ONE VALUE
            // FOR A WHOLE HUMAN. A person who administers customer A and is
            // an ordinary user of customer B was drawn an administrator's
            // sidebar inside B — Users, Reports, Imports, Cadence, Audit Log,
            // Tier Config, Organization — for a company they hold no
            // administrative role in. The server now states the role for the
            // ACTIVE workspace on `/branding/org`; this renders what it says
            // and falls back to the user row only for a deployment that has
            // not refreshed its cached branding yet.
            const workspaceRole = roleOf(branding, user)
            const isOrgAdmin = isManagerRole(workspaceRole) || isGodAdmin
            const visible = (item) => {
              if (item.fiberOnly && !(branding && branding.industry === 'fiber')) return false
              // Only orgs with an open implementation see Launch. `null`
              // means the answer has not arrived yet, and the item stays
              // hidden until it does — a nav entry that appears a second late
              // is far better than one that flashes and vanishes.
              if (item.launchOnly &&
                  (!launchNavState?.exists || launchNavState.completed)) return false
              // A capability item is NEVER shown on the strength of a role.
              // That is the whole difference: `adminOnly` asks who you are,
              // `capability` asks what the server says you may administer.
              if (item.capability) return hasCapability(item.capability)
              if (item.adminOnly && !isOrgAdmin) return false
              if (item.featureKey !== undefined && !isFeatureEnabled(item.featureKey)) return false
              return true
            }
            // THE WORKSPACE'S OWN SCREENS, folded into the rail beside the
            // platform's. They are appended to their named group rather than
            // replacing anything: a customer who calls their lead list
            // "Prospects" still gets Leads, Work Queue and the rest, because
            // the configured view is another way of looking at those records
            // and not a substitute for them.
            //
            // A group name the platform does not already use becomes its own
            // section, in the order the views were configured.
            //
            // A VERTICAL REPLACES THE RAIL, IT DOES NOT ADD TO IT. Its groups
            // already name every screen its operators use, in their own
            // words, and every entry points at a route that already exists —
            // so appending the platform's twenty items beneath would put two
            // names on the same door. `visible()` above is applied to the
            // vertical's items unchanged, which is what stops a rename from
            // opening something a role or a plan had closed.
            const verticalGroups = navGroupsFor(vertical, configuredViews)
            if (verticalGroups) {
              return verticalGroups.map((group) => {
                const items = group.items.filter(visible)
                if (items.length === 0) return null
                return (
                  <div key={group.label} className="nav-section">
                    {!sidebarCollapsed && <div className="nav-section-label">{group.label}</div>}
                    {sidebarCollapsed && <div className="nav-divider" />}
                    {items.map((item) => (
                      <NavLink key={item.to} to={item.to} end={item.to === '/' || !!item.end}
                        className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                        onClick={closeSidebar}
                        title={item.label}
                      >
                        <Icon name={item.icon} />{!sidebarCollapsed && item.label}
                      </NavLink>
                    ))}
                  </div>
                )
              })
            }
            const groups = NAV_GROUPS.map((group) => ({ ...group, items: [...group.items] }))
            configuredViews.forEach((configured) => {
              const label = configured.group || 'Workspace'
              let bucket = groups.find((group) => group.label === label)
              if (!bucket) {
                bucket = { label, items: [] }
                // After Workspace, before Engagement - where a person looks
                // for the thing they do all day.
                groups.splice(1, 0, bucket)
              }
              bucket.items.push({
                to: `/view/${configured.key}`,
                label: configured.label,
                icon: configured.icon,
              })
            })
            const renderItem = (item) => (
              <NavLink key={item.to} to={item.to} end={item.to === '/' || !!item.end}
                className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                onClick={closeSidebar}
                title={item.label}
              >
                <Icon name={item.icon} />{!sidebarCollapsed && item.label}
              </NavLink>
            )
            if (inWholesale) {
              const isWs = (group) => group.items.some((item) => isWholesalePath(item.to))
              const wsGroups = groups.filter(isWs)
              const platformGroups = groups.filter((group) => !isWs(group))
                .map((group) => ({ ...group, items: group.items.filter(visible) }))
                .filter((group) => group.items.length > 0)
              return (
                <>
                  {wsGroups.map((group) => {
                    const items = group.items.filter(visible)
                    if (items.length === 0) return null
                    return (
                      <div key={group.label} className="nav-section">
                        {!sidebarCollapsed && <div className="nav-section-label">{group.label}</div>}
                        {sidebarCollapsed && <div className="nav-divider" />}
                        {items.map(renderItem)}
                      </div>
                    )
                  })}
                  {platformGroups.length > 0 && (
                    <div className="wsx-platform">
                      <button type="button" className="wsx-platform__toggle" aria-expanded={platformOpen}
                              aria-controls="wsx-platform-list" onClick={() => setPlatformOpen((o) => !o)}
                              title="Every other EvoSys screen: leads, replies, AI team, compliance, settings">
                        {sidebarCollapsed ? '⋯' : (
                          <span>EvoSys Platform<span className="wsx-platform__hint">Leads, AI team, compliance &amp; more</span></span>
                        )}
                        {!sidebarCollapsed && <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true"><polyline points="6 9 12 15 18 9" /></svg>}
                      </button>
                      {platformOpen && (
                        <div className="wsx-platform__list" id="wsx-platform-list">
                          {platformGroups.map((group) => (
                            <div key={group.label}>
                              {!sidebarCollapsed && <div className="wsx-platform__group">{group.label}</div>}
                              {group.items.map(renderItem)}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )
            }
            return groups.map((group) => {
              const items = group.items.filter(visible)
              if (items.length === 0) return null
              return (
                <div key={group.label} className="nav-section">
                  {!sidebarCollapsed && <div className="nav-section-label">{group.label}</div>}
                  {sidebarCollapsed && <div className="nav-divider" />}
                  {items.map((item) => (
                    <NavLink key={item.to} to={item.to} end={item.to === '/' || !!item.end}
                      className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                      onClick={closeSidebar}
                      title={item.label}
                    >
                      <Icon name={item.icon} />{!sidebarCollapsed && item.label}
                    </NavLink>
                  ))}
                </div>
              )
            })
          })()}

          {/* PLATFORM ADMIN IS NOT PART OF A CUSTOMER'S WORKSPACE.
              Provision Client, Templates, Cadence Builder and Org Manager are
              platform-scope tools — none of them is about the customer whose
              rail this is, and four of them under a customer's own wordmark
              is the platform hierarchy leaking into the very screen that is
              meant to be theirs. They are one click away the whole time:
              Command Center is still the first item above, and the operator's
              way back sits over the content. Only a configured vertical hides
              them; every other workspace's rail is unchanged. */}
          {(user?.role === 'super_admin' || isGodAdmin) && !vertical && (
            <>
              <div className="nav-divider" />
              {!sidebarCollapsed && <div className="nav-section-label" style={isGodAdmin ? { color: '#b45309' } : {}}>Platform Admin</div>}
              {SUPER_ADMIN_NAV_ITEMS.map((item) => (
                <NavLink key={item.to} to={item.to}
                  className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                  onClick={closeSidebar}
                  title={sidebarCollapsed ? item.label : undefined}
                >
                  <Icon name={item.icon} />{!sidebarCollapsed && item.label}
                </NavLink>
              ))}
            </>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip" title={sidebarCollapsed ? (user?.full_name || 'Unknown') : undefined}>
            <div className="user-avatar">
              {profilePhoto
                ? <img src={profilePhoto} alt={user?.full_name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '50%' }} />
                : (user?.full_name || '?')[0]
              }
            </div>
            {!sidebarCollapsed && (
              <div>
                <div className="user-name">{user?.full_name || 'Unknown'}</div>
                <div className="user-role">{user?.role?.replace('_', ' ')}</div>
              </div>
            )}
          </div>
          {/* The workspace's own brand's website - on a non-brand host (localhost)
              the hostname default would have linked an EvoSysPro workspace to
              another brand's site. */}
          {!sidebarCollapsed && SHELL_BRAND.websiteUrl && (
            <a
              href={SHELL_BRAND.websiteUrl}
              className="back-to-website-btn"
              target="_blank"
              rel="noopener noreferrer"
            >
              ↗ Back to website
            </a>
          )}
          {!sidebarCollapsed && <button className="logout-btn" onClick={handleLogout}>Sign out</button>}
        </div>
      </aside>

      <div className="content-area">
        {/* god_admin only. God Mode can launch into this app; without a way
            back, that was a one-way trip needing a retyped URL or a re-login.
            INSIDE A VERTICAL WORKSPACE IT IS FOLDED INTO WorkspaceAdminMenu
            instead: a full-width amber strip above the customer's own product
            is the platform announcing itself on their home screen, and the
            same action lives in the one control at the end of the top bar. */}
        {!vertical && <GodReturnBar context="the customer app" />}
        <header className="top-bar">
          {inWholesale ? <WholesaleSearch /> : <LiveClock />}
          <div className="top-bar-right">
            {inWholesale && <WholesaleEnvironment />}
            {/* The way BACK OUT of a customer workspace. Renders nothing for a
                customer's own staff - they have no back office to return to,
                and offering the button would advertise a door that refuses
                them. It appears only for somebody the server confirms holds
                brand-sales access as well.
                In a vertical workspace this is one of the four actions inside
                WorkspaceAdminMenu, so it is not also drawn as its own button. */}
            {vertical ? <WorkspaceAdminMenu /> : <ContextSwitcher current="workspace" />}
            {/* The Wholesale product is one light design (the approved
                board), so its screens do not offer the dark/light switch. */}
            {!inWholesale && <ThemeToggle branding={isElevated ? null : branding} />}
            <NotificationBell />
            {inWholesale && <WholesaleUser user={user} photo={profilePhoto} />}
          </div>
        </header>
        {/* THE SECOND CONTEXT BANNER WAS REMOVED, NOT THE FIRST.
            Three strips used to stack here: GodReturnBar above ("viewing the
            customer app", with the way back), this one, and ContextBanner
            inside the page ("VIEWING: <customer> · <brand> customer", with Exit
            customer). The middle one said the same thing as the third and read
            it from LOCALSTORAGE — which is the exact source ContextBanner
            exists in order not to trust, because a stale tab keeps naming a
            customer the server has already stopped applying.

            So the duplicate went and the two that do different jobs stayed: one
            gets you back to the control plane, the other tells you — on the
            server's authority — whose records you are about to change.
            `handleExitOrg` is still wired to the org switcher in the rail. */}
        {/* ONE PAGE FAILING MUST NOT TAKE THE RAIL WITH IT. Keyed on the
            pathname so the boundary resets on navigation — an error boundary
            latches, and one that never clears would show its card on every
            page after the first failure. See PageBoundary.

            AND KEYED ON THE ACTIVE WORKSPACE, WHICH IS A SEPARATE FAILURE.
            `children` is an element this component RECEIVES, so when Layout
            re-renders from its own state — which is what happens when the
            branding answer lands — React compares the same element object to
            itself and skips the subtree entirely. The rail, the skin and the
            vocabulary all updated; the page in the middle of them did not,
            and went on showing the previous context until a manual browser
            refresh. Naming the organization in the key makes a workspace
            change a REMOUNT rather than an update, so the routed page cannot
            outlive the customer it was rendered for. The pathname alone could
            not catch it: entering a customer from God Mode and switching from
            one customer to another both land on the same path. */}
        <main className="main-content">
          <PageBoundary key={location.pathname + '\u0000' + workspaceKey}>
            {children}
          </PageBoundary>
        </main>
      </div>
      <ProfileOnboarding />
    </div>
  )
}
