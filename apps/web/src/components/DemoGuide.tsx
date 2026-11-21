import { useEffect, useState } from 'react'
import { useLocation } from 'react-router'

import { ROLE_LABEL, type Role } from '../lib/api'
import { useDemoGuide } from '../lib/demo'
import { formatPhone } from '../lib/phone'
import { Button } from './ui'

const DONE_KEY = 'pakkaa.demo.done'

function readDone(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(DONE_KEY) ?? '{}') as Record<string, boolean>
  } catch {
    return {}
  }
}

export default function DemoGuide() {
  const guide = useDemoGuide()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)
  const [done, setDone] = useState<Record<string, boolean>>(readDone)
  const [copied, setCopied] = useState<string | null>(null)

  useEffect(() => {
    try {
      localStorage.setItem(DONE_KEY, JSON.stringify(done))
    } catch {
      /* storage unavailable */
    }
  }, [done])

  if (!guide || (pathname.startsWith('/rooms/') && !open)) return null

  async function copy(phone: string) {
    try {
      await navigator.clipboard.writeText(phone)
      setCopied(phone)
    } catch {
      setCopied(null)
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-24 right-3 z-40 rounded-full bg-ink px-3 py-2 text-xs font-semibold text-white shadow-lg sm:bottom-6"
        aria-haspopup="dialog"
      >
        Demo guide
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center" role="dialog" aria-modal="true" aria-label="Demo guide">
          <div className="flex max-h-[90dvh] w-full max-w-md flex-col rounded-t-3xl bg-white sm:rounded-3xl">
            <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <div>
                <h2 className="font-semibold text-ink">3-minute demo</h2>
                <p className="text-xs text-slate-500">{guide.org_name} · {guide.note}</p>
              </div>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Close
              </Button>
            </header>
            <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Demo accounts</h3>
                <ul className="divide-y divide-slate-100 text-sm">
                  {guide.accounts.map((a) => (
                    <li key={a.phone} className="flex items-center justify-between gap-2 py-2">
                      <span className="min-w-0">
                        <span className="block font-medium text-ink">
                          {a.name} <span className="text-xs font-normal text-slate-500">· {ROLE_LABEL[a.role as Role] ?? a.role}</span>
                        </span>
                        <span className="block truncate text-xs text-slate-500">{a.about}</span>
                      </span>
                      <button
                        type="button"
                        onClick={() => void copy(a.phone)}
                        className="shrink-0 rounded-lg bg-slate-100 px-2 py-1 font-mono text-xs text-ink"
                        aria-label={`Copy ${a.name}'s number`}
                      >
                        {copied === a.phone ? 'Copied' : formatPhone(a.phone)}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
              {guide.steps.map((s, i) => (
                <section key={s.title} className="space-y-2">
                  <h3 className="text-sm font-semibold text-ink">
                    {i + 1}. {s.title} <span className="font-normal text-slate-500">· {s.who}</span>
                  </h3>
                  <ul className="space-y-1.5">
                    {s.actions.map((a, j) => {
                      const id = `${i}.${j}`
                      return (
                        <li key={id}>
                          <label className="flex gap-2 text-sm text-slate-700">
                            <input
                              type="checkbox"
                              className="mt-0.5 size-4 shrink-0 accent-brand"
                              checked={!!done[id]}
                              onChange={(e) => setDone((d) => ({ ...d, [id]: e.target.checked }))}
                            />
                            <span className={done[id] ? 'text-slate-400 line-through' : ''}>{a}</span>
                          </label>
                        </li>
                      )
                    })}
                  </ul>
                </section>
              ))}
              <Button variant="secondary" className="w-full" onClick={() => setDone({})}>
                Reset checklist
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
