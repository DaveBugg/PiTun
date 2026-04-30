import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Login } from '@/pages/Login'
import { Dashboard } from '@/pages/Dashboard'
import { Nodes } from '@/pages/Nodes'
import { Routing } from '@/pages/Routing'
import { Subscriptions } from '@/pages/Subscriptions'
import { GeoData } from '@/pages/GeoData'
import { DNS } from '@/pages/DNS'
import { Logs } from '@/pages/Logs'
import { KnowledgeBase } from '@/pages/KnowledgeBase'
import { Balancers } from '@/pages/Balancers'
import { NodeCircles } from '@/pages/NodeCircles'
import { Devices } from '@/pages/Devices'
import { Diagnostics } from '@/pages/Diagnostics'
import { Settings } from '@/pages/Settings'

function isTokenValid(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.exp * 1000 > Date.now()
  } catch {
    return false
  }
}

function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('pitun_token')
  if (!token || !isTokenValid(token)) {
    localStorage.removeItem('pitun_token')
    return <Login />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <AuthGuard>
            <Layout />
          </AuthGuard>
        }
      >
        <Route path="/"              element={<Dashboard />} />
        <Route path="/nodes"         element={<Nodes />} />
        <Route path="/routing"       element={<Routing />} />
        <Route path="/balancers"     element={<Balancers />} />
        <Route path="/circles"       element={<NodeCircles />} />
        <Route path="/subscriptions" element={<Subscriptions />} />
        <Route path="/devices"       element={<Devices />} />
        <Route path="/dns"           element={<DNS />} />
        <Route path="/geodata"       element={<GeoData />} />
        <Route path="/logs"          element={<Logs />} />
        <Route path="/diagnostics"   element={<Diagnostics />} />
        <Route path="/settings"      element={<Settings />} />
        <Route path="/kb"            element={<KnowledgeBase />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
