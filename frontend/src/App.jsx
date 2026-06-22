import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Login from './pages/Login'
import ChangePassword from './pages/ChangePassword'
import Overview from './pages/Overview'
import Leads from './pages/Leads'
import LeadDetail from './pages/LeadDetail'
import Replies from './pages/Replies'
import Cadence from './pages/Cadence'
import EmailQueue from './pages/EmailQueue'
import Admin from './pages/Admin'
import Users from './pages/Users'
import UserDetail from './pages/UserDetail'
import Compliance from './pages/Compliance'
import AuditLog from './pages/AuditLog'
import SystemHealth from './pages/SystemHealth'
import Campaigns from './pages/Campaigns'
import LeadCleanup from './pages/LeadCleanup'
import Settings from './pages/Settings'
import Templates from './pages/Templates'
import { getCurrentUser } from './api/client'

function isAuthenticated() {
  return !!localStorage.getItem('advisorflow_token')
}

function mustChangePassword() {
  const user = getCurrentUser()
  return !!user?.must_change_password
}

function ProtectedRoute({ children }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  if (mustChangePassword()) return <Navigate to="/change-password" replace />
  return <Layout>{children}</Layout>
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={isAuthenticated() ? <Navigate to="/" replace /> : <Login />} />
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
        <Route path="/admin" element={<ProtectedRoute><Admin /></ProtectedRoute>} />
        <Route path="/users" element={<ProtectedRoute><Users /></ProtectedRoute>} />
        <Route path="/users/:userId" element={<ProtectedRoute><UserDetail /></ProtectedRoute>} />
        <Route path="/compliance" element={<ProtectedRoute><Compliance /></ProtectedRoute>} />
        <Route path="/audit-log" element={<ProtectedRoute><AuditLog /></ProtectedRoute>} />
        <Route path="/system-health" element={<ProtectedRoute><SystemHealth /></ProtectedRoute>} />
        <Route path="/campaigns" element={<ProtectedRoute><Campaigns /></ProtectedRoute>} />
        <Route path="/lead-cleanup" element={<ProtectedRoute><LeadCleanup /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
        <Route path="/templates" element={<ProtectedRoute><Templates /></ProtectedRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
