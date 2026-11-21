import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { Api, refreshSession, setAccessToken, setSignedOutHandler, type User } from './api'
import { disconnectChat } from './chat'

interface AuthState {
  user: User | null
  ready: boolean
  signIn: (accessToken: string, user: User) => void
  signOut: () => Promise<void>
  reload: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setSignedOutHandler(() => setUser(null))
    refreshSession()
      .then((r) => setUser(r?.user ?? null))
      .finally(() => setReady(true))
  }, [])

  const signIn = useCallback((token: string, u: User) => {
    setAccessToken(token)
    setUser(u)
  }, [])

  const signOut = useCallback(async () => {
    await Api.logout().catch(() => undefined)
    await disconnectChat()
    setAccessToken(null)
    setUser(null)
  }, [])

  const reload = useCallback(async () => setUser(await Api.me()), [])

  const value = useMemo(() => ({ user, ready, signIn, signOut, reload }), [user, ready, signIn, signOut, reload])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
