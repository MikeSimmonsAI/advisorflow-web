import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { useEffect, useState, cloneElement } from 'react'
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
import GodShell from './pages/GodShell'
import GodOrganizations from './pages/GodOrganizations'
import { getCurrentUser, startKeepAlive, api } from './api/client'

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
    if (isAuthenticated()) startKeepAlive()
  }, [])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={isAuthenticated() ? <Navigate to="/" replace /> : <Login />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/setup-integrations" element={<SetupIntegrations />} />
        <Route path="/cadence-templates" element={<ProtectedRoute requireAdmin><CadenceTemplates /></ProtectedRoute>} />
        <Route path="/org-settings" element={<ProtectedRoute requireAdmin><OrgSettings /></ProtectedRoute>} />
        <Route path="/change-password"
          element={isAuthenticated() ? <ChangePassword forced={mustChangePassword()} /> : <Navigate to="/login" replace />} />
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
        {/* ── God Mode routes ── */}
        <Route path="/god" element={<GodRoute><GodModeLayout><GodCommandCenter /></GodModeLayout></GodRoute>} />
        <Route path="/god/organizations" element={<GodRoute><GodModeLayout><GodOrganizations /></GodModeLayout></GodRoute>} />
        <Route path="/god/*" element={<GodRoute><GodModeLayout><GodCommandCenter /></GodModeLayout></GodRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
