/**
 * The /m route tree. App.jsx mounts it with ONE line (see the S6 snippet):
 *
 *     <Route path="/m/*" element={<MobileApp />} />
 *
 * Every screen is behind MobileShell, which sends an unauthenticated visitor
 * to /m/login. The server enforces every permission on every call.
 */
import { Routes, Route, Navigate } from 'react-router-dom'
import MobileShell from './MobileShell'
import { MobileLogin, MobileHome, MobileConversations, MobileThread, MobileTasks,
         MobileAppointments, MobileContact, MobileNotifications, MobileMore,
         MobileWorkspaces } from './MobileScreens'
import { MobileProspect } from './MobileAgency'

export default function MobileApp() {
  return (
    <Routes>
      <Route path="login" element={<MobileLogin />} />
      <Route element={<MobileShell />}>
        <Route index element={<MobileHome />} />
        <Route path="conversations" element={<MobileConversations />} />
        <Route path="conversations/:leadId" element={<MobileThread />} />
        <Route path="tasks" element={<MobileTasks />} />
        <Route path="appointments" element={<MobileAppointments />} />
        <Route path="contacts/:leadId" element={<MobileContact />} />
        <Route path="prospects/:leadId" element={<MobileProspect />} />
        <Route path="notifications" element={<MobileNotifications />} />
        <Route path="workspaces" element={<MobileWorkspaces />} />
        <Route path="more" element={<MobileMore />} />
        <Route path="*" element={<Navigate to="/m" replace />} />
      </Route>
    </Routes>
  )
}
