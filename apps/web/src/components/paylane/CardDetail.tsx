import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { endCall, onCallEvents, startVerifyCall } from '../../lib/call'
import { parseRupees } from '../../lib/deal'
import { type Brief, type Card, newIdempotencyKey, PayApi, STATE_LABEL, type VerifyStart } from '../../lib/paylane'
import { Badge, Button, ErrorText } from '../ui'

const RISK_TONE = { low: 'green', medium: 'amber', high: 'red' } as const

function VerifyCall({ card, onDone }: { card: Card; onDone: (c: Card) => void }) {
  const [start, setStart] = useState<VerifyStart | null>(null)
  const [callId, setCallId] = useState<string | null>(null)
  const [state, setState] = useState<'idle' | 'ringing' | 'connected' | 'ended' | 'failed'>('idle')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const startedAt = useRef<number | null>(null)

  useEffect(
    () =>
      onCallEvents((e) => {
        if (e.type === 'connected' && e.callId === callId) {
          startedAt.current = Date.now()
          setState('connected')
        }
        if (e.type === 'ended' && e.callId === callId) setState('ended')
      }),
    [callId],
  )

  async function begin() {
    setError(null)
    setBusy(true)
    try {
      const s = await PayApi.startVerify(card.id)
      setStart(s)
      setState('ringing')
      try {
        setCallId(await startVerifyCall(s.callee_chat_id, card.id))
      } catch (err) {
        setState('failed')
        setError(err instanceof Error ? err.message : 'The call could not start')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the verify call')
    } finally {
      setBusy(false)
    }
  }

  async function finish(outcome: 'confirmed' | 'suspicious') {
    if (!start) return
    setBusy(true)
    setError(null)
    try {
      await endCall(callId ?? undefined)
      const secs = startedAt.current ? Math.round((Date.now() - startedAt.current) / 1000) : 0
      onDone(await PayApi.completeVerify(card.id, start.call_id, outcome, notes.trim(), secs))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the result')
    } finally {
      setBusy(false)
    }
  }

  if (!start) {
    return (
      <div className="space-y-2 rounded-2xl border border-red-200 bg-red-50/60 p-3">
        <p className="text-sm text-red-900">High risk. Call the requester on their verified in-app account before anyone approves.</p>
        <Button onClick={() => void begin()} busy={busy} className="w-full">
          📞 Start verify call
        </Button>
        <ErrorText>{error}</ErrorText>
      </div>
    )
  }
  return (
    <div className="space-y-3 rounded-2xl border border-slate-200 p-3">
      <p className="text-sm font-semibold text-ink">
        Calling {start.callee_name} (in-app, ••{start.callee_phone_last4}) ·{' '}
        <span className="font-normal text-slate-500">{state}</span>
      </p>
      <p className="rounded-lg bg-amber-50 px-2 py-1 text-xs text-amber-900">⚠ {start.warning}</p>
      <ol className="list-decimal space-y-1.5 pl-5 text-sm">
        {start.questions.map((q) => (
          <li key={q.q}>
            {q.q}
            <span className="block text-xs text-slate-500">Expect: {q.expected}</span>
          </li>
        ))}
      </ol>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        maxLength={500}
        rows={2}
        placeholder="What did they say?"
        aria-label="Call notes"
        className="w-full rounded-xl border border-slate-300 p-2 text-sm"
      />
      <div className="grid grid-cols-2 gap-2">
        <Button onClick={() => void finish('confirmed')} busy={busy}>
          They confirmed
        </Button>
        <Button variant="danger" onClick={() => void finish('suspicious')} busy={busy}>
          Suspicious
        </Button>
      </div>
      <ErrorText>{error}</ErrorText>
    </div>
  )
}

function EditAmount({ card, onSaved }: { card: Card; onSaved: (c: Card) => void }) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState(String(card.amount_paise / 100))
  const [error, setError] = useState<string | null>(null)

  async function save(e: FormEvent) {
    e.preventDefault()
    const paise = parseRupees(value)
    if (paise == null) return setError('Enter an amount like 40000 or 40k')
    try {
      onSaved(await PayApi.edit(card.id, { amount_paise: paise }))
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    }
  }

  if (!open) {
    return (
      <button className="text-xs font-semibold text-brand" onClick={() => setOpen(true)}>
        Edit amount
      </button>
    )
  }
  return (
    <form onSubmit={save} className="flex flex-wrap items-center gap-2">
      <input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" maxLength={14} aria-label="Amount"
        className="w-32 rounded-lg border border-slate-300 px-2 py-1 text-sm" />
      <Button type="submit" className="px-3 py-1">Save</Button>
      <p className="w-full text-xs text-slate-500">Changing the amount resets every approval.</p>
      <ErrorText>{error}</ErrorText>
    </form>
  )
}

export default function CardDetail({ cardId, onClose, onChanged }: { cardId: string; onClose: () => void; onChanged: () => void }) {
  const [card, setCard] = useState<Card | null>(null)
  const [brief, setBrief] = useState<Brief | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const payoutKey = useRef(newIdempotencyKey())

  useEffect(() => {
    PayApi.card(cardId).then(setCard).catch((e) => setError(e instanceof Error ? e.message : 'Not found'))
    PayApi.brief(cardId).then(setBrief).catch(() => setBrief(null))
  }, [cardId])

  function update(c: Card) {
    setCard(c)
    onChanged()
    PayApi.brief(c.id).then(setBrief).catch(() => undefined)
  }

  async function run(name: string, fn: () => Promise<Card>) {
    setBusy(name)
    setError(null)
    try {
      update(await fn())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="absolute inset-0 z-30 flex flex-col bg-white" role="dialog" aria-label="Payment card">
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold text-ink">Card #{card?.number ?? ''}</h2>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <ErrorText>{error}</ErrorText>
        {card && (
          <>
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={card.state === 'paid' ? 'green' : card.state === 'unverified' || card.state === 'rejected' ? 'red' : 'slate'}>
                  {STATE_LABEL[card.state]}
                </Badge>
                <Badge tone={RISK_TONE[card.risk_level]}>
                  {card.risk_level} risk · {card.risk_score}
                </Badge>
                {card.version > 1 && <Badge>v{card.version}</Badge>}
              </div>
              <p className="text-3xl font-extrabold text-ink">{card.amount_display}</p>
              <p className="text-sm text-slate-600">{card.reason}</p>
              {card.viewer.can_edit && <EditAmount card={card} onSaved={update} />}
            </div>

            <section className="space-y-1 rounded-2xl bg-slate-50 p-3 text-sm">
              <p>
                <span className="text-slate-500">To </span>
                {card.payee ? (
                  <>
                    <strong>{card.payee.name}</strong> · {card.payee.masked}
                    {card.payee.gstin && <span className="block text-xs text-slate-500">GSTIN {card.payee.gstin}</span>}
                  </>
                ) : (
                  <strong className="text-red-700">“{card.payee_name_claimed ?? 'unknown'}” (not a verified payee)</strong>
                )}
              </p>
              <p>
                <span className="text-slate-500">Requested by </span>
                <strong>{card.requester?.name ?? 'unknown'}</strong>{' '}
                {card.requester?.verified ? <Badge tone="green">Verified team</Badge> : <Badge tone="red">Not verified</Badge>}
                {card.requester && card.requester.name_collisions.length > 0 && (
                  <span className="mt-1 block text-xs font-semibold text-red-700">
                    ⚠ Same name as {card.requester.name_collisions[0].name} ({card.requester.name_collisions[0].role}), a
                    different account
                  </span>
                )}
              </p>
              {card.deal && (
                <p>
                  <span className="text-slate-500">Deal </span>
                  <Link className="font-semibold text-brand underline" to={`/rooms/${card.deal.room_id}`}>
                    {card.deal.title}
                  </Link>
                  {card.deal.price_display && <span className="text-slate-500"> · agreed {card.deal.price_display}</span>}
                </p>
              )}
            </section>

            {brief && (
              <section className="space-y-1">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Approver brief</h3>
                <ul className="space-y-1 text-sm text-ink">
                  {brief.lines.map((l) => (
                    <li key={l}>• {l}</li>
                  ))}
                </ul>
                <p className="text-[11px] text-slate-400">{brief.disclaimer}</p>
              </section>
            )}

            {card.signals.length > 0 && (
              <section className="space-y-1">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Risk rules fired</h3>
                <ul className="space-y-1 text-sm">
                  {card.signals.map((s) => (
                    <li key={s.rule_id} className="flex justify-between gap-2">
                      <span>{s.message}</span>
                      <span className="shrink-0 text-xs text-slate-400">+{s.weight}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section className="space-y-1 text-sm">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Approvals {card.approvals.length} of {card.required_approvals}
                {card.owner_approval_required ? ' (owner required)' : ''}
              </h3>
              {card.approvals.map((a) => (
                <p key={a.name}>✓ {a.name} ({a.role})</p>
              ))}
              {card.verify_call && (
                <p className="text-slate-600">
                  Verify call: <strong>{card.verify_call.outcome}</strong>
                  {card.verify_call.summary ? ` · “${card.verify_call.summary}”` : ''}
                </p>
              )}
            </section>

            {card.viewer.blocked_reason && <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{card.viewer.blocked_reason}</p>}
            {card.viewer.can_verify && !card.verify_call && <VerifyCall card={card} onDone={update} />}

            <div className="grid gap-2">
              {card.viewer.can_approve && (
                <Button busy={busy === 'approve'} onClick={() => void run('approve', () => PayApi.approve(card.id, card.version))}>
                  Approve {card.amount_display}
                </Button>
              )}
              {card.viewer.can_payout && (
                <Button busy={busy === 'payout'} onClick={() => void run('payout', () => PayApi.payout(card.id, payoutKey.current))}>
                  Pay {card.amount_display} (simulated)
                </Button>
              )}
              {card.viewer.can_reject && (
                <Button variant="secondary" busy={busy === 'reject'} onClick={() => void run('reject', () => PayApi.reject(card.id))}>
                  {card.state === 'unverified' ? 'Dismiss draft' : 'Reject'}
                </Button>
              )}
            </div>
            {card.payout && (
              <p className="rounded-2xl bg-emerald-50 p-3 text-sm text-emerald-900">
                Paid · ref {card.payout.provider_ref}
                {card.payout.simulated && <Badge tone="amber">Simulated</Badge>}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
