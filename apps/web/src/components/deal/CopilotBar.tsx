import { type FormEvent, useEffect, useRef, useState } from 'react'

import { DealApi, parseRupees, type PresendWarning, type Profile, type ReplyOption } from '../../lib/deal'
import { Button, ErrorText } from '../ui'

export interface Chosen {
  suggestionId: string
  index: number
  text: string
}

function LimitsForm({ roomId, side, onClose }: { roomId: string; side: string; onClose: () => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [walkAway, setWalkAway] = useState('')
  const [target, setTarget] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    DealApi.profile(roomId)
      .then((p) => {
        setProfile(p)
        setWalkAway(p.walk_away_paise ? String(p.walk_away_paise / 100) : '')
        setTarget(p.target_paise ? String(p.target_paise / 100) : '')
        setNotes(p.notes ?? '')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load'))
  }, [roomId])

  async function save(e: FormEvent) {
    e.preventDefault()
    const w = walkAway.trim() ? parseRupees(walkAway) : null
    const t = target.trim() ? parseRupees(target) : null
    if ((walkAway.trim() && w == null) || (target.trim() && t == null)) {
      setError('Enter amounts like 45000 or 45k')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await DealApi.saveProfile(roomId, { walk_away_paise: w, target_paise: t, notes: notes.trim() || null })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  const walkLabel = side === 'provider' ? 'Lowest price you will accept' : 'Highest you can pay'
  return (
    <form onSubmit={save} className="space-y-3 rounded-2xl bg-slate-50 p-3">
      <p className="text-xs text-slate-600">Private to you. The other side and their copilot never see this.</p>
      {!profile && !error && <p className="text-xs text-slate-400">Loading…</p>}
      <label className="block text-sm">
        {walkLabel}
        <input value={walkAway} onChange={(e) => setWalkAway(e.target.value)} inputMode="decimal" placeholder="45000"
          maxLength={14} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
      </label>
      <label className="block text-sm">
        Target price
        <input value={target} onChange={(e) => setTarget(e.target.value)} inputMode="decimal" placeholder="55000"
          maxLength={14} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
      </label>
      <label className="block text-sm">
        Private notes
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} rows={2}
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
      </label>
      <ErrorText>{error}</ErrorText>
      <div className="flex gap-2">
        <Button type="submit" busy={busy} className="px-3 py-1.5">Save</Button>
        <Button type="button" variant="ghost" className="px-3 py-1.5" onClick={onClose}>Cancel</Button>
      </div>
    </form>
  )
}

export default function CopilotBar({ roomId, side, trigger, warnings, onUse }: {
  roomId: string
  side: string
  trigger: { id: string; at: number } | null
  warnings: PresendWarning[]
  onUse: (chosen: Chosen) => void
}) {
  const [options, setOptions] = useState<ReplyOption[]>([])
  const [suggestionId, setSuggestionId] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(true)
  const [limits, setLimits] = useState(false)
  const abort = useRef<AbortController | null>(null)

  async function suggest(triggerId: string | null) {
    abort.current?.abort()
    const ctrl = new AbortController()
    abort.current = ctrl
    setOptions([])
    setSuggestionId(null)
    setError(null)
    setStreaming(true)
    setOpen(true)
    try {
      await DealApi.suggest(
        roomId,
        triggerId,
        (event, data) => {
          if (event === 'option') setOptions((prev) => [...prev, data as ReplyOption])
          else if (event === 'done') setSuggestionId((data as { suggestion_id: string }).suggestion_id)
          else if (event === 'error') setError((data as { message: string }).message)
        },
        ctrl.signal,
      )
    } catch (err) {
      if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : 'Copilot unavailable')
    } finally {
      if (abort.current === ctrl) setStreaming(false)
    }
  }

  useEffect(() => {
    if (trigger) void suggest(trigger.id)
    return () => abort.current?.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trigger?.at])

  return (
    <div className="border-t border-slate-100 bg-white px-3 pt-2">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1 text-xs font-semibold text-brand"
          aria-expanded={open}
        >
          ✨ Your copilot <span className="font-normal text-slate-400">(only you see this)</span>
        </button>
        <div className="ml-auto flex gap-1">
          <button type="button" className="rounded-lg px-2 py-1 text-xs text-slate-600 hover:bg-slate-100" onClick={() => setLimits((l) => !l)}>
            My limits
          </button>
          <button
            type="button"
            className="rounded-lg px-2 py-1 text-xs font-semibold text-brand hover:bg-emerald-50 disabled:text-slate-400"
            onClick={() => void suggest(null)}
            disabled={streaming}
          >
            {streaming ? 'Thinking…' : 'Suggest replies'}
          </button>
        </div>
      </div>
      {limits && (
        <div className="py-2">
          <LimitsForm roomId={roomId} side={side} onClose={() => setLimits(false)} />
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="mt-2 space-y-1" aria-live="polite">
          {warnings.map((w) => (
            <li key={w.code} className={`rounded-lg px-2 py-1 text-xs ${w.level === 'warn' ? 'bg-amber-50 text-amber-900' : 'bg-slate-50 text-slate-600'}`}>
              {w.level === 'warn' ? '⚠ ' : 'ℹ '}
              {w.message}
            </li>
          ))}
        </ul>
      )}
      {open && (options.length > 0 || streaming || error) && (
        <div className="flex max-h-56 flex-col gap-2 overflow-y-auto py-2" aria-live="polite">
          {options.map((o, i) => (
            <button
              type="button"
              key={i}
              onClick={() => suggestionId && onUse({ suggestionId, index: i, text: o.text })}
              disabled={!suggestionId}
              className="rounded-xl border border-slate-200 px-3 py-2 text-left hover:border-brand disabled:opacity-80"
            >
              <span className="text-[11px] font-semibold uppercase tracking-wide text-brand">{o.label}</span>
              <span className="block text-sm text-ink">{o.text}</span>
              <span className="block text-[11px] text-slate-500">{o.why}</span>
            </button>
          ))}
          {streaming && options.length < 3 && <div className="h-14 animate-pulse rounded-xl bg-slate-100" />}
          {error && <p className="text-xs text-slate-500">{error}</p>}
        </div>
      )}
    </div>
  )
}

