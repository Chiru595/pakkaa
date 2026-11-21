import { type FormEvent, useCallback, useEffect, useState } from 'react'

import { type Deal, DealApi, parseRupees, type ReviewItem, type SignRequest, type Term } from '../../lib/deal'
import { Badge, Button, ErrorText } from '../ui'
import ExplainSheet from './ExplainSheet'
import FairPriceMeter from './FairPriceMeter'

function TermRow({ term, roomId, onSaved, onExplain }: {
  term: Term
  roomId: string
  onSaved: (d: Deal) => void
  onExplain: (text: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function start() {
    setDraft(term.value == null ? '' : term.kind === 'money' ? String(Number(term.value) / 100) : String(term.value))
    setError(null)
    setEditing(true)
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    let value: string | number = draft.trim()
    if (term.kind === 'money') {
      const paise = parseRupees(value)
      if (paise == null) return setError('Enter an amount like 45000 or 45k')
      value = paise
    } else if (term.kind !== 'text') {
      if (!/^\d{1,3}$/.test(value)) return setError('Enter a whole number')
      value = Number(value)
    }
    setBusy(true)
    try {
      onSaved(await DealApi.setTerm(roomId, term.key, value))
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            {term.label}
            {term.required && term.value == null && <span className="ml-1 text-amber-600">required</span>}
          </p>
          {!editing && (
            <p className={`mt-0.5 break-words ${term.value == null ? 'text-slate-400' : 'text-ink'}`}>
              {term.display}
              {term.via === 'scribe' && <span className="ml-2 text-[11px] text-emerald-700">from chat</span>}
            </p>
          )}
        </div>
        {!editing && (
          <div className="flex shrink-0 gap-1">
            {term.kind === 'text' && term.value != null && (
              <button className="rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-slate-100" onClick={() => onExplain(String(term.value))}>
                Explain
              </button>
            )}
            <button className="rounded-lg px-2 py-1 text-xs font-semibold text-brand hover:bg-emerald-50" onClick={start}>
              {term.value == null ? 'Add' : 'Edit'}
            </button>
          </div>
        )}
      </div>
      {editing && (
        <form onSubmit={save} className="mt-2 space-y-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
            aria-label={term.label}
            inputMode={term.kind === 'text' ? 'text' : term.kind === 'money' ? 'decimal' : 'numeric'}
            maxLength={term.kind === 'text' ? (term.key === 'ip_transfer' || term.key === 'cancellation' ? 300 : 500) : 14}
            placeholder={term.kind === 'money' ? '45000 or 45k' : term.kind === 'percent' ? '0 to 100' : ''}
            className="w-full rounded-xl border border-slate-300 px-3 py-2 focus:border-brand focus:outline-none"
          />
          <ErrorText>{error}</ErrorText>
          <div className="flex gap-2">
            <Button type="submit" busy={busy} className="px-3 py-1.5">
              Save
            </Button>
            <Button type="button" variant="ghost" className="px-3 py-1.5" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </li>
  )
}

function SignBox({ deal, roomId, onSigned }: { deal: Deal; roomId: string; onSigned: (d: Deal) => void }) {
  const sheet = deal.term_sheet
  const [req, setReq] = useState<SignRequest | null>(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mine = sheet.signatures.some((s) => s.side === deal.my_side)

  async function request() {
    setBusy(true)
    setError(null)
    try {
      setReq(await DealApi.requestSign(roomId, sheet.version, sheet.hash))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start signing')
    } finally {
      setBusy(false)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!req) return
    setBusy(true)
    setError(null)
    try {
      onSigned(await DealApi.sign(roomId, req.version, req.hash, code))
      setReq(null)
      setCode('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign')
    } finally {
      setBusy(false)
    }
  }

  if (sheet.status === 'locked') {
    return (
      <div className="rounded-2xl bg-emerald-50 p-4 text-sm text-emerald-900">
        <p className="font-semibold">Locked v{sheet.version} by both sides</p>
        <p className="mt-1 break-all font-mono text-[11px]">SHA-256 {sheet.locked_hash}</p>
        <ul className="mt-2 space-y-0.5">
          {sheet.signatures.map((s) => (
            <li key={s.side}>
              {s.name} ({s.side === 'provider' ? 'freelancer' : 'client'}) signed with OTP
            </li>
          ))}
        </ul>
      </div>
    )
  }
  return (
    <div className="space-y-3 rounded-2xl border border-slate-200 p-4">
      <p className="text-sm text-slate-700">
        Both sides confirm with a one-time code. Any change after that needs both signatures again.
      </p>
      {sheet.signatures.map((s) => (
        <p key={s.side} className="text-sm text-emerald-700">
          ✓ {s.name} signed v{sheet.version}
        </p>
      ))}
      {mine ? (
        <p className="text-sm text-slate-500">You signed this version. Waiting for the other side.</p>
      ) : req ? (
        <form onSubmit={submit} className="space-y-2">
          {req.test_mode && req.test_code && (
            <p className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <strong>Test mode:</strong> your signing code is <span className="font-mono font-bold">{req.test_code}</span>
            </p>
          )}
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            inputMode="numeric"
            autoComplete="one-time-code"
            aria-label={`Code sent to ••${req.phone_last4}`}
            placeholder={`Code sent to ••${req.phone_last4}`}
            className="w-full rounded-xl border border-slate-300 px-3 py-2 tracking-widest focus:border-brand focus:outline-none"
          />
          <p className="break-all font-mono text-[11px] text-slate-500">Signing v{req.version} · SHA-256 {req.hash}</p>
          <Button type="submit" busy={busy} className="w-full" disabled={code.length !== 6}>
            Sign v{req.version}
          </Button>
        </form>
      ) : (
        <Button onClick={() => void request()} busy={busy} className="w-full">
          Sign v{sheet.version} with OTP
        </Button>
      )}
      <ErrorText>{error}</ErrorText>
    </div>
  )
}

function AdvanceBox({ deal, roomId, onChange }: { deal: Deal; roomId: string; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const link = deal.advance_link
  if (deal.term_sheet.status !== 'locked') return null

  async function create() {
    setBusy(true)
    setError(null)
    try {
      await DealApi.advanceLink(roomId)
      onChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the link')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2 rounded-2xl border border-slate-200 p-4">
      <div className="flex items-center justify-between">
        <p className="font-semibold text-ink">Advance {deal.term_sheet.advance_display}</p>
        <Badge tone="amber">Simulated</Badge>
      </div>
      {link ? (
        <>
          <p className="text-sm">
            Status:{' '}
            <span className={link.status === 'paid' ? 'font-semibold text-emerald-700' : 'text-slate-700'}>{link.status}</span>
          </p>
          {link.status === 'pending' && link.url && (
            <a href={link.url} target="_blank" rel="noreferrer" className="block text-sm font-semibold text-brand underline">
              {deal.my_side === 'client' ? 'Pay the advance' : 'Open payment link'}
            </a>
          )}
        </>
      ) : deal.my_side === 'provider' ? (
        <Button onClick={() => void create()} busy={busy} className="w-full">
          Request advance
        </Button>
      ) : (
        <p className="text-sm text-slate-500">The freelancer will send a payment link for the advance.</p>
      )}
      <ErrorText>{error}</ErrorText>
    </div>
  )
}

export function CounterpartyNotes({ notes, compact = false }: { notes: string[]; compact?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  if (!notes.length) return null
  const shown = compact && !expanded ? notes.slice(0, 1) : notes
  return (
    <section className="rounded-2xl border border-amber-300 bg-amber-50 px-3 py-2" aria-label="What your team remembers">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-amber-800">
        🔒 From your history · only your side sees this
      </h3>
      <ul className="mt-1 space-y-1 text-sm text-amber-950">
        {shown.map((n) => (
          <li key={n} className={compact && !expanded ? 'line-clamp-2' : ''}>
            {n}
          </li>
        ))}
      </ul>
      {compact && notes.length > 1 && (
        <button type="button" onClick={() => setExpanded((e) => !e)} className="mt-1 text-xs font-medium text-amber-800 underline">
          {expanded ? 'Show less' : `+${notes.length - 1} more`}
        </button>
      )}
    </section>
  )
}

function OutcomeBox({ deal, roomId, onDeal }: { deal: Deal; roomId: string; onDeal: (d: Deal) => void }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<'won' | 'lost' | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (deal.my_side !== 'provider') return null
  if (deal.outcome) {
    const o = deal.outcome
    return (
      <section className="rounded-2xl bg-slate-50 p-3 text-sm text-slate-700">
        <p>
          Outcome: <strong>{o.outcome === 'won' ? 'Won' : 'Lost'}</strong>
          {o.final_display && ` at ${o.final_display}`}
          {o.first_ask_display && ` (first ask ${o.first_ask_display})`}
        </p>
        {o.note && <p className="mt-1 text-slate-500">{o.note}</p>}
        <p className="mt-1 text-xs text-slate-500">Saved to your team's playbook for the next negotiation.</p>
      </section>
    )
  }
  async function save(outcome: 'won' | 'lost') {
    setBusy(outcome)
    setError(null)
    try {
      onDeal(await DealApi.outcome(roomId, outcome, note))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the outcome')
    } finally {
      setBusy(null)
    }
  }
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">How did it end?</h3>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={300}
        placeholder="Optional note for next time"
        aria-label="Outcome note"
        className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-brand focus:outline-none"
      />
      <div className="grid grid-cols-2 gap-2">
        <Button busy={busy === 'won'} disabled={deal.term_sheet.status !== 'locked'} onClick={() => void save('won')}>
          Won
        </Button>
        <Button variant="secondary" busy={busy === 'lost'} onClick={() => void save('lost')}>
          Lost
        </Button>
      </div>
      {deal.term_sheet.status !== 'locked' && <p className="text-xs text-slate-500">A deal is won once both sides lock the terms.</p>}
      <ErrorText>{error}</ErrorText>
    </section>
  )
}

export default function TermSheetPanel({ roomId, deal, onDeal, onClose, refreshKey }: {
  roomId: string
  deal: Deal
  onDeal: (d: Deal) => void
  onClose: () => void
  refreshKey: number
}) {
  const [review, setReview] = useState<ReviewItem[]>([])
  const [explain, setExplain] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const sheet = deal.term_sheet

  const reload = useCallback(async () => {
    try {
      onDeal(await DealApi.get(roomId))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not refresh')
    }
  }, [roomId, onDeal])

  useEffect(() => {
    DealApi.review(roomId).then(setReview).catch(() => setReview([]))
  }, [roomId, sheet.version, deal.proposals.length, refreshKey])

  async function decide(id: string, decision: 'accept' | 'reject') {
    setError(null)
    try {
      onDeal(await DealApi.decide(roomId, id, decision))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update')
    }
  }

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-white" role="dialog" aria-label="Term sheet">
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div>
          <h2 className="font-semibold text-ink">Term sheet v{sheet.version}</h2>
          <p className="font-mono text-[11px] text-slate-400">SHA-256 {sheet.hash.slice(0, 16)}…</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={sheet.status === 'locked' ? 'green' : 'slate'}>{sheet.status === 'locked' ? 'Locked' : 'Draft'}</Badge>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </header>
      <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
        <ErrorText>{error}</ErrorText>
        <CounterpartyNotes notes={deal.counterparty_notes} />
        {deal.proposals.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Scribe noticed in chat</h3>
            {deal.proposals.map((p) => (
              <div key={p.id} className="rounded-2xl border border-emerald-200 bg-emerald-50/60 p-3">
                <p className="text-sm">
                  <span className="font-semibold">{p.label}:</span> {p.display}
                </p>
                {p.quote && <p className="mt-1 text-xs italic text-slate-600">“{p.quote}”</p>}
                <div className="mt-2 flex items-center gap-2">
                  <Button className="px-3 py-1.5" onClick={() => void decide(p.id, 'accept')}>
                    Confirm
                  </Button>
                  <Button variant="ghost" className="px-3 py-1.5" onClick={() => void decide(p.id, 'reject')}>
                    Not right
                  </Button>
                  <span className="ml-auto text-[11px] text-slate-500">{p.confidence_pct}% sure</span>
                </div>
              </div>
            ))}
          </section>
        )}
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Agreed terms</h3>
          <ul className="divide-y divide-slate-100">
            {sheet.terms.map((t) => (
              <TermRow key={t.key} term={t} roomId={roomId} onSaved={onDeal} onExplain={setExplain} />
            ))}
          </ul>
          {sheet.advance_display && <p className="text-sm text-slate-600">Advance amount: {sheet.advance_display}</p>}
        </section>
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Fair-price meter</h3>
          <FairPriceMeter roomId={roomId} refreshKey={sheet.version + refreshKey} />
        </section>
        {sheet.status !== 'locked' && review.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Before you lock</h3>
            <ul className="space-y-1.5 text-sm">
              {review.map((r, i) => (
                <li key={i} className={r.level === 'missing' ? 'text-amber-800' : r.level === 'risk' ? 'text-red-700' : 'text-slate-600'}>
                  {r.level === 'missing' ? '○ ' : r.level === 'risk' ? '⚠ ' : 'ℹ '}
                  {r.message}
                </li>
              ))}
            </ul>
          </section>
        )}
        <SignBox deal={deal} roomId={roomId} onSigned={onDeal} />
        <AdvanceBox deal={deal} roomId={roomId} onChange={() => void reload()} />
        <OutcomeBox deal={deal} roomId={roomId} onDeal={onDeal} />
        <p className="pb-4 text-center text-[11px] text-slate-400">{deal.disclaimer}</p>
      </div>
      {explain !== null && <ExplainSheet roomId={roomId} initial={explain} onClose={() => setExplain(null)} />}
    </div>
  )
}
