import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect } from 'react'
import Layout from './components/Layout'
import Login from './pages/Login'
import Onboarding from './pages/Onboarding'
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
import { getCurrentUser, startKeepAlive, startRefreshLoop } from './api/client'

function isAuthenticated() {
  return !!localStorage.getItem('bookaboost_token')
}

function mustChangePassword() {
  const user = getCurrentUser()
  return !!user?.must_change_password
}

function ProtectedRoute({ children, requireAdmin = false, requireSuperAdmin = false }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  if (mustChangePassword()) return <Navigate to="/change-password" replace />

  // Previously every route used the same generic check (authenticated or
  // not), so admin-only pages were only hidden from the sidebar nav - a
  // regular advisor typing /audit-log or /users directly into the URL
  // bar would still load that page's shell, even though every actual
  // data call on it would 403 from the backend. This adds real
  // client-side enforcement to match what the backend already requires,
  // redirecting instead of rendering a broken/empty admin page.
  const user = getCurrentUser()
  const role = user?.role
  if (requireSuperAdmin && role !== 'super_admin' && role !== 'god_admin') return <Navigate to="/" replace />
  if (requireAdmin && role !== 'org_admin' && role !== 'super_admin' && role !== 'god_admin') return <Navigate to="/" replace />

  return <Layout>{children}</Layout>
}

function GodRoute({ children }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  if (mustChangePassword()) return <Navigate to="/change-password" replace />
  const user = getCurrentUser()
  if (user?.role !== 'god_admin') return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  // Restart keep-alive if the user reloads the page while already authenticated.
  // The normal start happens in Login.jsx after successful login.
  useEffect(() => {
    if (isAuthenticated()) {
      startKeepAlive()
      startRefreshLoop()
    }
  }, [])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={isAuthenticated() ? <Navigate to="/" replace /> : <Login />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/setup-integrations" element={<SetupIntegrations />} />
        <Route path="/cadence-templates" element={<ProtectedRoute requireAdmin><CadenceTemplates /></ProtectedRoute>} />
        <Route path="/org-settings" element={<ProtectedRoute requireAdmin><OrgSettings /></ProtectedRoute>} />
        <Route
          path="/change-password"
          element={isAuthenticated() ? <ChangePassword forced={mustChangePassword()} /> : <Navigate to="/login" replace />}
        />
        <Route path="/" element={<ProtectedRoute><Overview /></ProtectedRoute>} />
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
        <Route path="/compliance" element={<ProtectedRoute><Compliance /></ProtectedRoute>} />
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
        <Route path="/god" element={<GodRoute><GodCommandCenter /></GodRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
