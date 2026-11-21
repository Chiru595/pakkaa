import { useEffect, useState } from 'react'

import { api } from './api'

export interface DemoAccount {
  phone: string
  name: string
  role: string
  about: string
}

export interface DemoGuide {
  org_name: string
  accounts: DemoAccount[]
  steps: { title: string; who: string; actions: string[] }[]
  note: string
}

let cached: Promise<DemoGuide | null> | null = null

export function loadDemoGuide(): Promise<DemoGuide | null> {
  cached ??= api<DemoGuide>('/api/demo', {}, false).catch(() => null)
  return cached
}

export function useDemoGuide(): DemoGuide | null {
  const [guide, setGuide] = useState<DemoGuide | null>(null)
  useEffect(() => {
    let alive = true
    void loadDemoGuide().then((g) => alive && setGuide(g))
    return () => {
      alive = false
    }
  }, [])
  return guide
}
