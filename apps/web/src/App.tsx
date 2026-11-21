import { BrowserRouter, Navigate, Route, Routes } from 'react-router'

import { Screen, Spinner } from './components/ui'
import { AuthProvider, useAuth } from './lib/auth'
import Home from './pages/Home'
import JoinPage from './pages/JoinPage'
import Login from './pages/Login'
import Privacy from './pages/Privacy'
import RoomPage from './pages/RoomPage'

function Gate({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth()
  if (!ready) {
    return (
      <Screen>
        <div className="grid flex-1 place-items-center text-slate-400">
          <Spinner />
        </div>
      </Screen>
    )
  }
  return user ? <>{children}</> : <Login />
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/join/:token" element={<JoinPage />} />
          <Route path="/rooms/:roomId" element={<Gate><RoomPage /></Gate>} />
          <Route path="/" element={<Gate><Home /></Gate>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
