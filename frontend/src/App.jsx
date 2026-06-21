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
        <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
        <Route path="/templates" element={<ProtectedRoute><Templates /></ProtectedRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
