import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Layout from './components/Layout'
import DemoBanner from './components/DemoBanner'
import ContextBanner from './components/ContextBanner'
import DemoConsole from './pages/DemoConsole'
import Login from './pages/Login'
import Onboarding from './pages/Onboarding'
// Public customer-facing surfaces. DealRoom is the Checkpoint 4 sales deal
// room; PortalAccess/PortalViewer are the pre-existing customer portal pages
// that were built but never routed until now.
import DealRoom from './pages/portal/DealRoom'
import PortalAccess from './pages/portal/PortalAccess'
import DemoSite from './pages/portal/DemoSite'
import PortalViewer from './pages/portal/PortalViewer'
// Family-facing pages on the organization's own branded domain. The
// public-identity resolver emits https://<branded-host>/book/:token and
// /survey/:token; without these two routes those links 404.
import BookingPage from './pages/public/BookingPage'
import SurveyPage from './pages/public/SurveyPage'
import AppointmentConfirmPage from './pages/public/AppointmentConfirmPage'
// The app's own notice surface. Wraps the router so any page can raise a
// notice without blocking the tab the way window.alert() does.
import { ToastProvider } from './components/Toast'
import CadenceTemplates from './pages/CadenceTemplates'
import OrgSettings from './pages/OrgSettings'
import ChangePassword from './pages/ChangePassword'
import Overview from './pages/Overview'
import Leads from './pages/Leads'
import LeadDetail from './pages/LeadDetail'
import Replies from './pages/Replies'
import Cadence from './pages/Cadence'
import EmailQueue from './pages/EmailQueue'
import Activity from './pages/Activity'
import WorkQueue from './pages/WorkQueue'
import AutoSendQueue from './pages/AutoSendQueue'
import Reports from './pages/Reports'
import CampaignBuilder from './pages/CampaignBuilder'
import Admin from './pages/Admin'
import Users from './pages/Users'
import UserDetail from './pages/UserDetail'
import Compliance from './pages/Compliance'
import AuditLog from './pages/AuditLog'
import SystemHealth from './pages/SystemHealth'
import LeadCleanup from './pages/LeadCleanup'
import Settings from './pages/Settings'
import Templates from './pages/Templates'
import ProvisionClient from './pages/ProvisionClient'
import Pipeline from './pages/Pipeline'
import AIHub from './pages/AIHub'
import Availability from './pages/Availability'
import CRMIntegration from './pages/CRMIntegration'
import CRM from './pages/CRM'
import TierDefinitions from './pages/TierDefinitions'
import DLCRegistration from './pages/DLCRegistration'
import FiberLeadCapture from './pages/FiberLeadCapture'
import OrgManager from './pages/OrgManager'
import ReEngagement from './pages/ReEngagement'
import SetupIntegrations from './pages/SetupIntegrations'
import GodCommandCenter from './pages/GodCommandCenter'
import Billing from './pages/Billing'
import GodShell from './pages/GodShell'
import GodOrganizations from './pages/GodOrganizations'
// Checkpoint 6 — God Mode operations. Separate files from the Command Center
// so the whole Checkpoint 6 surface can be read as one thing.
import GodSalesOps from './pages/GodSalesOps'
import GodBrandDetail from './pages/GodBrandDetail'
import GodProvision from './pages/GodProvision'
// Platform separation + customer provisioning.
import PlatformOverview from './pages/god/PlatformOverview'
import CustomerCreate from './pages/god/CustomerCreate'
import CustomerDetail from './pages/god/CustomerDetail'
import GodImplementations from './pages/GodImplementations'
import GodImplementationDetail from './pages/GodImplementationDetail'
import GodCustomers from './pages/GodCustomers'
import GodControlAudit from './pages/GodControlAudit'
import SalesImplementations from './pages/SalesImplementations'
import Activate from './pages/Activate'
import LeadScraper from './pages/LeadScraper'
import MyDay from './pages/sales/MyDay'
import MyPipeline from './pages/sales/MyPipeline'
import ManagerCommand from './pages/sales/ManagerCommand'
import OpportunityDetail from './pages/sales/OpportunityDetail'
import MyAvailability from './pages/sales/MyAvailability'
import TeamAvailability from './pages/sales/TeamAvailability'
import TeamCalendar from './pages/sales/TeamCalendar'
import TeamProposals from './pages/sales/TeamProposals'
import Salespeople from './pages/sales/Salespeople'
import Prospects from './pages/sales/Prospects'
import GodUsers from './pages/god/GodUsers'
import { getCurrentUser, startKeepAlive, startRefreshLoop, getOrgContext } from './api/client'
import { exitCustomer } from './pages/god/enterCustomer'

function isAuthenticated() {
  // Check both keys: af_token is the current key, bookaboost_token is the legacy key.
  return !!(localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token'))
}

function mustChangePassword() {
  const user = getCurrentUser()
  return !!user?.must_change_password
}

function ProtectedRoute({ children, requireAdmin = false, requireSuperAdmin = false, requireGodAdmin = false }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  if (mustChangePassword()) return <Navigate to="/change-password" replace />
  const user = getCurrentUser()
  const role = user?.role
  if (requireGodAdmin && role !== 'god_admin') return <Navigate to="/" replace />
  if (requireSuperAdmin && role !== 'super_admin' && role !== 'god_admin') return <Navigate to="/" replace />
  if (requireAdmin && role !== 'org_admin' && role !== 'super_admin' && role !== 'god_admin') return <Navigate to="/" replace />
  // The context banner wraps every tenant screen, not just a chosen few. The
  // owner is most likely to forget which customer they entered on the ordinary
  // pages — the leads list, a lead detail — which is exactly where a banner
  // that only appeared on special screens would be missing.
  return <Layout><ContextBanner />{children}</Layout>
}

/**
 * SalesRoute — the Sales Workspace frame, deliberately NOT the tenant Layout.
 *
 * A brand-sales user has no customer organization, so wrapping them in Layout
 * would hand them a nav pointing at tenant data they must never see. The real
 * authorization is server-side in app/services/sales_access.py; SalesShell
 * calls /sales/me and shows a plain refusal if the server says no.
 */
function SalesRoute({ children }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  if (mustChangePassword()) return <Navigate to="/change-password" replace />
  return <>{children}</>
}

/**
 * Where "/" goes.
 *
 * A user with no organization_id is a brand-sales user: the tenant Overview is
 * not merely the wrong home for them, it is a screen belonging to the other
 * domain. Send them to their own workspace instead. god_admin keeps the tenant
 * home because Mike legitimately operates across both.
 */
function HomeRedirect() {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  if (mustChangePassword()) return <Navigate to="/change-password" replace />
  const user = getCurrentUser()
  if (user && user.role !== 'god_admin' && !user.organization_id) {
    return <Navigate to="/sales" replace />
  }
  return <ProtectedRoute><Overview /></ProtectedRoute>
}

function GodRoute({ children }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  if (mustChangePassword()) return <Navigate to="/change-password" replace />
  const user = getCurrentUser()
  if (user?.role !== 'god_admin') return <Navigate to="/" replace />
  return <>{children}</>
}

/**
 * GodModeLayout — the God Mode frame.
 *
 * THE BANNER NOW REFLECTS THE REAL CONTEXT, NOT A LOCAL FLAG. It used to hold
 * an `orgSession` in React state, set by a POST /god/orgs/{id}/impersonate call
 * that established no context on the server or on the client — so the banner
 * appeared for a tenancy the API was not actually applying, and it vanished the
 * moment the page reloaded even though nothing had been exited.
 *
 * It now reads the same org context that puts `X-Org-Override` on every
 * request. Its purpose is the case where the owner walks back into God Mode
 * while still inside a customer: the whole control plane says so, and offers
 * one click to leave.
 */
function GodModeLayout({ children }) {
  const [ctx, setCtx] = useState(() => getOrgContext())

  // Re-read on focus: the context can be cleared from the tenant app's own
  // banner in another tab, and a God rail still claiming a customer would be
  // saying something untrue about what the server will do next.
  useEffect(() => {
    const sync = () => setCtx(getOrgContext())
    window.addEventListener('focus', sync)
    return () => window.removeEventListener('focus', sync)
  }, [])

  async function handleExitOrgSession() {
    await exitCustomer()
    setCtx(null)
  }

  return (
    <GodShell
      orgSession={ctx ? { org_id: ctx.orgId, org_name: ctx.orgName } : null}
      onExitOrgSession={handleExitOrgSession}
    >
      {children}
    </GodShell>
  )
}

export default function App() {
  useEffect(() => {
    if (isAuthenticated()) {
      startKeepAlive()      // ping every 14 min to prevent Render cold starts
      startRefreshLoop()    // refresh JWT every 30 min so session never expires
    }
  }, [])

  return (
    <ToastProvider>
    <BrowserRouter>
      {/* DEMO MODE BANNER — above every shell, inside the router because it
          links to the console. Renders nothing at all unless the BACKEND says
          this process is the demo environment, so a production bundle has no
          demo affordance in its DOM to find. */}
      <DemoBanner />
      <Routes>
        <Route path="/login" element={isAuthenticated() ? <Navigate to="/" replace /> : <Login />} />
        <Route path="/onboarding" element={<Onboarding />} />
        {/* Public by necessity: the invited customer has no account yet. */}
        <Route path="/activate" element={<Activate />} />
        <Route path="/setup-integrations" element={<SetupIntegrations />} />
        {/* ── PUBLIC customer surfaces ── no ProtectedRoute, by design ──
            A customer has no account. The token in the URL is the whole
            authorization, and the pages below never call an authenticated
            endpoint.

            NOTE: /portal/access/:token and /portal/view/:id were BUILT but
            never routed — App.jsx had no entry for either, so every magic link
            ever emailed hit the catch-all and silently redirected to "/". The
            backend was always correct; the door was missing. */}
        <Route path="/deal-room/:token" element={<DealRoom />} />
        <Route path="/demo/:token" element={<DemoSite />} />
        <Route path="/portal/access/:token" element={<PortalAccess />} />
        <Route path="/portal/view/:proposalId" element={<PortalViewer />} />
        {/* The family's booking and feedback pages, on the customer's own
            branded host. These are the routes the public-identity resolver
            has been emitting; they reuse the existing booking/survey
            endpoints rather than introducing a second system. */}
        <Route path="/book/:token" element={<BookingPage />} />
        <Route path="/survey/:token" element={<SurveyPage />} />
        {/* Brand-sales meeting confirmation. Same token, same redeem logic as
            the backend HTML page — this one just lives on the brand's host so
            the prospect is not emailed an infrastructure URL. */}
        <Route path="/appointments/confirm/:token" element={<AppointmentConfirmPage />} />
        <Route path="/cadence-templates" element={<ProtectedRoute requireAdmin><CadenceTemplates /></ProtectedRoute>} />
        <Route path="/org-settings" element={<ProtectedRoute requireAdmin><OrgSettings /></ProtectedRoute>} />
        <Route path="/change-password"
          element={isAuthenticated() ? <ChangePassword forced={mustChangePassword()} /> : <Navigate to="/login" replace />} />
        {/* Demo Console. Registered in every build; the page itself asks the
            backend which environment answered and renders a plain "not
            available" panel outside the demo — and every control endpoint it
            would call 404s there regardless. Authentication is required, and
            the page additionally refuses anyone who is not a platform owner,
            because Demo Mode does not relax permissions. */}
        <Route path="/demo" element={
          isAuthenticated() ? <DemoConsole /> : <Navigate to="/login" replace />} />
        <Route path="/" element={<HomeRedirect />} />
        {/* ── Sales Workspace ── brand-sales members; guarded server-side ── */}
        {/* ── MY WORK ── every signed-in brand-sales member ── */}
        <Route path="/sales" element={<SalesRoute><MyDay /></SalesRoute>} />
        {/* One component, two scopes. See the header of MyPipeline.jsx: the
            team board is not a second page, it is this page without the
            personal narrowing. */}
        <Route path="/sales/pipeline" element={<SalesRoute><MyPipeline scope="mine" /></SalesRoute>} />
        <Route path="/sales/prospects" element={<SalesRoute><Prospects /></SalesRoute>} />
        <Route path="/sales/availability" element={<SalesRoute><MyAvailability /></SalesRoute>} />
        <Route path="/sales/onboarding" element={<SalesRoute><SalesImplementations /></SalesRoute>} />
        <Route path="/sales/opportunities/:oppId" element={<SalesRoute><OpportunityDetail /></SalesRoute>} />
        <Route path="/sales/team" element={<SalesRoute><TeamAvailability /></SalesRoute>} />

        {/* ── MY TEAM ── manager surfaces ──
            SalesRoute only checks that someone is signed in. Every route below
            is gated server-side — /sales/manager/* by require_sales_manager,
            the team calendar by a scope check inside the endpoint — so a rep
            who types one of these URLs gets a refusal from the API, or their
            own data, never somebody else's. The nav hides them; the server
            enforces them. Those are two different jobs and both are done. */}
        <Route path="/sales/manager" element={<SalesRoute><ManagerCommand /></SalesRoute>} />
        <Route path="/sales/calendar" element={<SalesRoute><TeamCalendar /></SalesRoute>} />
        <Route path="/sales/team-pipeline" element={<SalesRoute><MyPipeline scope="team" /></SalesRoute>} />
        <Route path="/sales/proposals" element={<SalesRoute><TeamProposals /></SalesRoute>} />
        <Route path="/sales/salespeople" element={<SalesRoute><Salespeople /></SalesRoute>} />
        <Route path="/leads" element={<ProtectedRoute><Leads /></ProtectedRoute>} />
        <Route path="/leads/:leadId" element={<ProtectedRoute><LeadDetail /></ProtectedRoute>} />
        <Route path="/replies" element={<ProtectedRoute><Replies /></ProtectedRoute>} />
        <Route path="/cadence" element={<ProtectedRoute><Cadence /></ProtectedRoute>} />
        <Route path="/email-queue" element={<ProtectedRoute><EmailQueue /></ProtectedRoute>} />
        <Route path="/activity" element={<ProtectedRoute><Activity /></ProtectedRoute>} />
        <Route path="/workqueue" element={<ProtectedRoute><WorkQueue /></ProtectedRoute>} />
        <Route path="/auto-send" element={<ProtectedRoute><AutoSendQueue /></ProtectedRoute>} />
        <Route path="/reports" element={<ProtectedRoute requireAdmin><Reports /></ProtectedRoute>} />
        <Route path="/campaigns" element={<ProtectedRoute requireAdmin><CampaignBuilder /></ProtectedRoute>} />
        <Route path="/admin" element={<ProtectedRoute requireAdmin><Admin /></ProtectedRoute>} />
        <Route path="/users" element={<ProtectedRoute requireAdmin><Users /></ProtectedRoute>} />
        <Route path="/users/:userId" element={<ProtectedRoute requireAdmin><UserDetail /></ProtectedRoute>} />
        <Route path="/compliance" element={<ProtectedRoute requireAdmin><Compliance /></ProtectedRoute>} />
        <Route path="/audit-log" element={<ProtectedRoute requireAdmin><AuditLog /></ProtectedRoute>} />
        <Route path="/system-health" element={<ProtectedRoute><SystemHealth /></ProtectedRoute>} />
        <Route path="/lead-cleanup" element={<ProtectedRoute requireAdmin><LeadCleanup /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
        <Route path="/templates" element={<ProtectedRoute requireAdmin><Templates /></ProtectedRoute>} />
        <Route path="/provision-client" element={<ProtectedRoute requireSuperAdmin><ProvisionClient /></ProtectedRoute>} />
        <Route path="/pipeline" element={<ProtectedRoute><Pipeline /></ProtectedRoute>} />
        <Route path="/ai-hub" element={<ProtectedRoute><AIHub /></ProtectedRoute>} />
        <Route path="/availability" element={<ProtectedRoute requireAdmin><Availability /></ProtectedRoute>} />
        <Route path="/crm" element={<ProtectedRoute requireAdmin><CRM /></ProtectedRoute>} />
        <Route path="/crm-connectors" element={<ProtectedRoute requireAdmin><CRMIntegration /></ProtectedRoute>} />
        <Route path="/tier-definitions" element={<ProtectedRoute requireAdmin><TierDefinitions /></ProtectedRoute>} />
        <Route path="/10dlc" element={<ProtectedRoute requireAdmin><DLCRegistration /></ProtectedRoute>} />
        <Route path="/fiber-capture" element={<ProtectedRoute><FiberLeadCapture /></ProtectedRoute>} />
        <Route path="/re-engagement" element={<ProtectedRoute><ReEngagement /></ProtectedRoute>} />
        <Route path="/orgs" element={<ProtectedRoute requireSuperAdmin><OrgManager /></ProtectedRoute>} />
        <Route path="/billing" element={<ProtectedRoute requireAdmin><Billing /></ProtectedRoute>} />
        <Route path="/scraper" element={<ProtectedRoute requireGodAdmin><LeadScraper /></ProtectedRoute>} />
        {/* ── God Mode routes ── */}
        <Route path="/god" element={<GodRoute><GodModeLayout><GodCommandCenter /></GodModeLayout></GodRoute>} />
        <Route path="/god/organizations" element={<GodRoute><GodModeLayout><GodOrganizations /></GodModeLayout></GodRoute>} />
        <Route path="/god/sales-operations" element={<GodRoute><GodModeLayout><GodSalesOps /></GodModeLayout></GodRoute>} />
        <Route path="/god/brands/:brandId" element={<GodRoute><GodModeLayout><GodBrandDetail /></GodModeLayout></GodRoute>} />
        <Route path="/god/provision/:oppId" element={<GodRoute><GodModeLayout><GodProvision /></GodModeLayout></GodRoute>} />
        <Route path="/god/implementations" element={<GodRoute><GodModeLayout><GodImplementations /></GodModeLayout></GodRoute>} />
        <Route path="/god/implementations/:implId" element={<GodRoute><GodModeLayout><GodImplementationDetail /></GodModeLayout></GodRoute>} />
        <Route path="/god/customers" element={<GodRoute><GodModeLayout><GodCustomers /></GodModeLayout></GodRoute>} />
        {/* Platform overview is where the owner lands with NO customer selected.
            "new" is a static segment so React Router ranks it above :orgId. */}
        <Route path="/god/platform" element={<GodRoute><GodModeLayout><PlatformOverview /></GodModeLayout></GodRoute>} />
        <Route path="/god/customers/new" element={<GodRoute><GodModeLayout><CustomerCreate /></GodModeLayout></GodRoute>} />
        <Route path="/god/customers/:orgId" element={<GodRoute><GodModeLayout><CustomerDetail /></GodModeLayout></GodRoute>} />
        <Route path="/god/audit" element={<GodRoute><GodModeLayout><GodControlAudit /></GodModeLayout></GodRoute>} />
        {/* Users & Identity. One row per human, every context on that row —
            see the header of GodUsers.jsx. */}
        <Route path="/god/users-all" element={<GodRoute><GodModeLayout><GodUsers /></GodModeLayout></GodRoute>} />
        <Route path="/god/*" element={<GodRoute><GodModeLayout><GodCommandCenter /></GodModeLayout></GodRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
    </ToastProvider>
  )
}
