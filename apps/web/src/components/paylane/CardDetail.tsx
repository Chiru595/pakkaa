import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { ROLE_LABEL, type Role } from '../../lib/api'
import { parseRupees } from '../../lib/deal'
import { type Brief, type Card, newIdempotencyKey, PayApi, STATE_LABEL, type StepUpRequest } from '../../lib/paylane'
import { Badge, Button, ErrorText } from '../ui'

const RISK_TONE = { low: 'green', medium: 'amber', high: 'red' } as const

function StepUp({ card, onDone }: { card: Card; onDone: (c: Card) => void }) {
  const [req, setReq] = useState<StepUpRequest | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function send() {
    setError(null)
    setBusy(true)
    try {
      setReq(await PayApi.stepUpRequest(card.id, card.version, card.card_hash))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send the code')
    } finally {
      setBusy(false)
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault()
    if (!req) return
    setError(null)
    setBusy(true)
    try {
      onDone(await PayApi.stepUp(card.id, req.version, req.hash, code))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not confirm')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-2 rounded-2xl border border-red-200 bg-red-50/60 p-3" aria-label="Step-up check">
      <h3 className="text-sm font-semibold text-red-900">Step-up check: confirm this exact card</h3>
      <p className="text-sm text-red-900">
        {card.signals.some((s) => s.rule_id === 'bank_changed') ? "The payee's bank details changed recently. " : 'High risk. '}
        Confirm <strong>{card.amount_display}</strong> to{' '}
        <strong>{card.payee ? `${card.payee.name} · ${card.payee.masked}` : 'this payee'}</strong> with the one-time code
        sent to your verified phone. Approvers can act only after this.
      </p>
      {!req ? (
        <Button onClick={() => void send()} busy={busy} className="w-full">
          Send code to my phone
        </Button>
      ) : (
        <form onSubmit={confirm} className="space-y-2">
          {req.test_mode && req.test_code && (
            <p className="rounded-lg border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900">
              <strong>Test mode:</strong> no SMS is sent. Your code is{' '}
              <span className="font-mono font-bold tracking-widest">{req.test_code}</span>
            </p>
          )}
          <label className="block space-y-1 text-sm">
            <span className="font-medium text-ink">Code sent to ••{req.phone_last4}</span>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              aria-label="Step-up code"
              className="w-full rounded-xl border border-slate-300 px-3 py-2 tracking-widest focus:border-brand focus:outline-none"
            />
          </label>
          <Button type="submit" busy={busy} disabled={code.length !== 6} className="w-full">
            Confirm {card.amount_display}
          </Button>
        </form>
      )}
      <ErrorText>{error}</ErrorText>
    </section>
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
  const [overrideReason, setOverrideReason] = useState('')
  const [fraud, setFraud] = useState(false)

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
                {card.requester && !card.requester.verified && card.requester.name_collisions.length > 0 && (
                  <span className="mt-1 block text-xs font-semibold text-red-700">
                    ⚠ Same name as {card.requester.name_collisions[0].name} (
                    {ROLE_LABEL[card.requester.name_collisions[0].role as Role] ?? card.requester.name_collisions[0].role}), a
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
                <p key={a.name}>✓ {a.name} ({ROLE_LABEL[a.role as Role] ?? a.role})</p>
              ))}
              {card.step_up_required && card.step_up_done && (
                <p className="text-emerald-800">
                  ✓ Step-up check: {card.requester?.name ?? 'the requester'} confirmed this exact card with an OTP
                  {card.step_up_at ? ` · ${new Date(card.step_up_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : ''}
                </p>
              )}
              {card.step_up_required && !card.step_up_done && card.state === 'pending_approval' && !card.viewer.can_step_up && (
                <p className="text-slate-600">Step-up check: waiting for {card.requester?.name ?? 'the requester'} to confirm with an OTP</p>
              )}
            </section>

            {card.viewer.blocked_reason && !card.viewer.can_step_up && (
              <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{card.viewer.blocked_reason}</p>
            )}
            {card.viewer.can_step_up && <StepUp card={card} onDone={update} />}

            <div className="grid gap-2">
              {card.viewer.can_approve && card.risk_level === 'high' && (
                <label className="block space-y-1 text-sm">
                  <span className="font-medium text-ink">Why approve despite high risk?</span>
                  <textarea
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    maxLength={300}
                    rows={2}
                    placeholder="e.g. Confirmed the new bank details with Kabir in person"
                    className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm focus:border-brand focus:outline-none"
                  />
                  <span className="text-xs text-slate-500">Kept with the card so the team can learn from overrides.</span>
                </label>
              )}
              {card.viewer.can_approve && (
                <Button
                  busy={busy === 'approve'}
                  disabled={card.risk_level === 'high' && overrideReason.trim().length < 10}
                  onClick={() => void run('approve', () => PayApi.approve(card.id, card.version, overrideReason.trim()))}
                >
                  Approve {card.amount_display}
                </Button>
              )}
              {card.viewer.can_payout && (
                <Button busy={busy === 'payout'} onClick={() => void run('payout', () => PayApi.payout(card.id, payoutKey.current))}>
                  Pay {card.amount_display} (simulated)
                </Button>
              )}
              {card.viewer.can_reject && card.state !== 'unverified' && card.viewer.is_approver && (
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input type="checkbox" checked={fraud} onChange={(e) => setFraud(e.target.checked)} className="size-4 accent-red-600" />
                  Suspected fraud (Pakkaa tightens its risk checks)
                </label>
              )}
              {card.viewer.can_reject && (
                <Button
                  variant={fraud ? 'danger' : 'secondary'}
                  busy={busy === 'reject'}
                  onClick={() => void run('reject', () => PayApi.reject(card.id, '', fraud))}
                >
                  {card.state === 'unverified' ? 'Dismiss draft' : fraud ? 'Reject as fraud' : 'Reject'}
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
