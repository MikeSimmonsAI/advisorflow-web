import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { useEffect, useState, cloneElement } from 'react'
import Layout from './components/Layout'
import DemoBanner from './components/DemoBanner'
import DemoConsole from './pages/DemoConsole'
import Login from './pages/Login'
import Onboarding from './pages/Onboarding'
// Public customer-facing surfaces. DealRoom is the Checkpoint 4 sales deal
// room; PortalAccess/PortalViewer are the pre-existing customer portal pages
// that were built but never routed until now.
import DealRoom from './pages/portal/DealRoom'
import PortalAccess from './pages/portal/PortalAccess'
import PortalViewer from './pages/portal/PortalViewer'
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
import LeadScraper from './pages/LeadScraper'
import MyDay from './pages/sales/MyDay'
import MyPipeline from './pages/sales/MyPipeline'
import ManagerCommand from './pages/sales/ManagerCommand'
import OpportunityDetail from './pages/sales/OpportunityDetail'
import MyAvailability from './pages/sales/MyAvailability'
import TeamAvailability from './pages/sales/TeamAvailability'
import { getCurrentUser, startKeepAlive, startRefreshLoop, api } from './api/client'

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
  return <Layout>{children}</Layout>
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

function GodModeLayout({ children }) {
  const [orgSession, setOrgSession] = useState(null)

  async function handleExitOrgSession() {
    if (orgSession) {
      try { await api.post(`/god/orgs/${orgSession.org_id}/exit-session`) }
      catch (e) { /* best effort */ }
    }
    setOrgSession(null)
  }

  const childrenWithProps = children && typeof children.type === 'function'
    ? cloneElement(children, { onEnterOrg: setOrgSession })
    : children

  return (
    <GodShell orgSession={orgSession} onExitOrgSession={handleExitOrgSession}>
      {childrenWithProps}
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
    <BrowserRouter>
      {/* DEMO MODE BANNER — above every shell, inside the router because it
          links to the console. Renders nothing at all unless the BACKEND says
          this process is the demo environment, so a production bundle has no
          demo affordance in its DOM to find. */}
      <DemoBanner />
      <Routes>
        <Route path="/login" element={isAuthenticated() ? <Navigate to="/" replace /> : <Login />} />
        <Route path="/onboarding" element={<Onboarding />} />
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
        <Route path="/portal/access/:token" element={<PortalAccess />} />
        <Route path="/portal/view/:proposalId" element={<PortalViewer />} />
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
        <Route path="/sales" element={<SalesRoute><MyDay /></SalesRoute>} />
        <Route path="/sales/pipeline" element={<SalesRoute><MyPipeline /></SalesRoute>} />
        {/* Manager workspace. SalesRoute only checks that someone is signed in;
            /sales/manager/* is gated by require_sales_manager server-side, so a
            rep who reaches this URL gets a refusal from the API, not a screen. */}
        <Route path="/sales/manager" element={<SalesRoute><ManagerCommand /></SalesRoute>} />
        <Route path="/sales/opportunities/:oppId" element={<SalesRoute><OpportunityDetail /></SalesRoute>} />
        <Route path="/sales/team" element={<SalesRoute><TeamAvailability /></SalesRoute>} />
        <Route path="/sales/availability" element={<SalesRoute><MyAvailability /></SalesRoute>} />
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
        <Route path="/god/*" element={<GodRoute><GodModeLayout><GodCommandCenter /></GodModeLayout></GodRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
