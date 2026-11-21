import { type FormEvent, useCallback, useEffect, useState } from 'react'

import { parseRupees, rupees } from '../../lib/deal'
import { type Card, type Control, type NewPayee, type ParsedDraft, PayApi, type Payee, STATE_LABEL } from '../../lib/paylane'
import { Badge, Button, ErrorText, Field } from '../ui'
import CardDetail from './CardDetail'

const RISK_TONE = { low: 'green', medium: 'amber', high: 'red' } as const

function NewCard({ roomId, payees, onCreated }: { roomId: string; payees: Payee[]; onCreated: (c: Card) => void }) {
  const [text, setText] = useState('')
  const [draft, setDraft] = useState<ParsedDraft | null>(null)
  const [payeeId, setPayeeId] = useState('')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [dealId, setDealId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function parse(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const d = await PayApi.parse(roomId, text.trim())
      setDraft(d)
      setPayeeId(d.payees[0]?.id ?? '')
      setAmount(d.amount_paise ? String(d.amount_paise / 100) : '')
      setReason(d.reason)
      setDealId(d.deals[0]?.room_id ?? '')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read that')
    } finally {
      setBusy(false)
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault()
    const paise = parseRupees(amount)
    if (!payeeId) return setError('Pick a payee from your verified list')
    if (paise == null) return setError('Enter an amount like 40000 or 40k')
    if (reason.trim().length < 2) return setError('Say what the payment is for')
    setBusy(true)
    setError(null)
    try {
      onCreated(await PayApi.create(roomId, { payee_id: payeeId, amount_paise: paise, reason: reason.trim(), deal_room_id: dealId || null }))
      setDraft(null)
      setText('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the card')
    } finally {
      setBusy(false)
    }
  }

  if (!draft) {
    return (
      <form onSubmit={parse} className="space-y-2">
        <Field id="nl" label="New payment" placeholder="pay Ravi 40k for the logo work" value={text}
          onChange={(e) => setText(e.target.value)} maxLength={400} minLength={3} required />
        <Button type="submit" busy={busy} className="w-full" disabled={text.trim().length < 3}>
          Prepare card
        </Button>
        <ErrorText>{error}</ErrorText>
      </form>
    )
  }
  return (
    <form onSubmit={confirm} className="space-y-3 rounded-2xl border border-slate-200 p-3">
      <p className="text-xs text-slate-500">{draft.used_ai ? 'Read by AI. Check every field.' : 'Check every field.'}</p>
      <label className="block text-sm">
        Payee
        <select value={payeeId} onChange={(e) => setPayeeId(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2">
          <option value="">Choose a verified payee…</option>
          {payees.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} · {p.masked}
            </option>
          ))}
        </select>
        {draft.payee_name && !draft.payees.length && (
          <span className="text-xs text-amber-700">“{draft.payee_name}” is not on your payee list.</span>
        )}
      </label>
      <label className="block text-sm">
        Amount (₹)
        <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" maxLength={14}
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        {parseRupees(amount) != null && <span className="text-xs text-slate-500">{rupees(parseRupees(amount))}</span>}
      </label>
      <label className="block text-sm">
        For
        <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300}
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
      </label>
      <label className="block text-sm">
        Linked deal
        <select value={dealId} onChange={(e) => setDealId(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2">
          <option value="">No deal</option>
          {draft.deals.map((d) => (
            <option key={d.room_id} value={d.room_id}>
              {d.title}
              {d.price_display ? ` · ${d.price_display}` : ''}
            </option>
          ))}
        </select>
      </label>
      <ErrorText>{error}</ErrorText>
      <div className="flex gap-2">
        <Button type="submit" busy={busy} className="flex-1">
          Raise card
        </Button>
        <Button type="button" variant="ghost" onClick={() => setDraft(null)}>
          Back
        </Button>
      </div>
    </form>
  )
}

function AddPayee({ orgId, onAdded }: { orgId: string; onAdded: () => void }) {
  const [form, setForm] = useState<NewPayee>({ name: '', method: 'upi' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof NewPayee) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }))

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const body: NewPayee = { name: form.name.trim(), method: form.method }
    if (form.gstin?.trim()) body.gstin = form.gstin.trim()
    if (form.method === 'upi') body.vpa = form.vpa?.trim()
    else Object.assign(body, { account_number: form.account_number?.trim(), ifsc: form.ifsc?.trim() })
    try {
      await PayApi.addPayee(orgId, body)
      setForm({ name: '', method: form.method })
      onAdded()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add the payee')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2 rounded-2xl bg-slate-50 p-3">
      <Field id="pn" label="Payee name" value={form.name} onChange={set('name')} maxLength={80} required />
      <div className="flex gap-2 text-sm" role="radiogroup" aria-label="Payment method">
        {(['upi', 'bank'] as const).map((m) => (
          <button type="button" key={m} role="radio" aria-checked={form.method === m} onClick={() => setForm((f) => ({ ...f, method: m }))}
            className={`rounded-full px-3 py-1 ${form.method === m ? 'bg-ink text-white' : 'bg-white ring-1 ring-slate-300'}`}>
            {m === 'upi' ? 'UPI' : 'Bank account'}
          </button>
        ))}
      </div>
      {form.method === 'upi' ? (
        <Field id="vpa" label="UPI ID" placeholder="name@okaxis" value={form.vpa ?? ''} onChange={set('vpa')} maxLength={300} required />
      ) : (
        <>
          <Field id="acc" label="Account number" inputMode="numeric" value={form.account_number ?? ''} onChange={set('account_number')} maxLength={22} required />
          <Field id="ifsc" label="IFSC" placeholder="HDFC0001234" value={form.ifsc ?? ''} onChange={set('ifsc')} maxLength={11} required />
        </>
      )}
      <Field id="gstin" label="GSTIN (optional)" placeholder="29ABCDE1234F1Z5" value={form.gstin ?? ''} onChange={set('gstin')} maxLength={15} />
      <ErrorText>{error}</ErrorText>
      <Button type="submit" busy={busy} className="w-full">
        Add payee
      </Button>
    </form>
  )
}

export default function CardsPanel({ roomId, orgId, role, focusCardId, onClose }: {
  roomId: string
  orgId: string
  role: string
  focusCardId: string | null
  onClose: () => void
}) {
  const [cards, setCards] = useState<Card[] | null>(null)
  const [payees, setPayees] = useState<Payee[]>([])
  const [control, setControl] = useState<Control | null>(null)
  const [open, setOpen] = useState<string | null>(focusCardId)
  const [tab, setTab] = useState<'cards' | 'payees'>('cards')
  const [error, setError] = useState<string | null>(null)
  const canEditPayees = role === 'owner' || role === 'finance'

  const load = useCallback(async () => {
    try {
      const [c, p, ctl] = await Promise.all([PayApi.cards(roomId), PayApi.payees(orgId), PayApi.control(orgId)])
      setCards(c)
      setPayees(p)
      setControl(ctl)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load payments')
    }
  }, [roomId, orgId])

  useEffect(() => {
    void load()
  }, [load])

  async function toggleFreeze() {
    setError(null)
    try {
      if (control?.frozen) setControl(await PayApi.unfreeze(orgId))
      else {
        const reason = window.prompt('Why are you freezing all payments?')?.trim()
        if (!reason || reason.length < 3) return
        setControl(await PayApi.freeze(orgId, reason))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the freeze')
    }
  }

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-white" role="dialog" aria-label="Payment cards">
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div className="flex gap-1" role="tablist">
          {(['cards', 'payees'] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`rounded-full px-3 py-1 text-sm ${tab === t ? 'bg-ink text-white' : 'text-slate-600'}`}>
              {t === 'cards' ? 'Payments' : 'Payees'}
            </button>
          ))}
        </div>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </header>
      {control && (
        <div className={`flex items-center justify-between gap-2 px-4 py-2 text-sm ${control.frozen ? 'bg-sky-100 text-sky-900' : 'bg-slate-50 text-slate-600'}`}>
          <span>
            {control.frozen
              ? `🧊 Frozen: ${control.reason ?? ''} · unfreeze votes ${control.unfreeze_votes}/2`
              : 'Kill switch ready. Anyone on the team can freeze all payments.'}
          </span>
          <button className="shrink-0 font-semibold underline disabled:text-slate-400" onClick={() => void toggleFreeze()}
            disabled={control.frozen && (!canEditPayees || control.voted)}>
            {control.frozen ? (control.voted ? 'Voted' : 'Vote unfreeze') : 'Freeze'}
          </button>
        </div>
      )}
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <ErrorText>{error}</ErrorText>
        {tab === 'cards' ? (
          <>
            <NewCard roomId={roomId} payees={payees} onCreated={(c) => { void load(); setOpen(c.id) }} />
            {cards === null ? (
              <p className="text-sm text-slate-400">Loading…</p>
            ) : cards.length === 0 ? (
              <p className="text-sm text-slate-500">No payment cards yet.</p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {cards.map((c) => (
                  <li key={c.id}>
                    <button className="flex w-full items-center gap-3 py-3 text-left" onClick={() => setOpen(c.id)}>
                      <span className="w-10 text-xs text-slate-400">#{c.number}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-ink">
                          {c.amount_display} → {c.payee?.name ?? c.payee_name_claimed ?? '?'}
                        </span>
                        <span className="text-xs text-slate-500">{STATE_LABEL[c.state]}</span>
                      </span>
                      <Badge tone={RISK_TONE[c.risk_level]}>{c.risk_level}</Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            <ul className="divide-y divide-slate-100">
              {payees.map((p) => (
                <li key={p.id} className="py-2 text-sm">
                  <p className="font-medium text-ink">{p.name}</p>
                  <p className="text-xs text-slate-500">
                    {p.masked}
                    {p.gstin ? ` · GSTIN ${p.gstin}` : ''}
                    {p.details_changed_at ? ' · details changed recently' : ''}
                  </p>
                </li>
              ))}
              {payees.length === 0 && <li className="py-2 text-sm text-slate-500">No payees yet.</li>}
            </ul>
            {canEditPayees ? <AddPayee orgId={orgId} onAdded={() => void load()} /> : (
              <p className="text-xs text-slate-500">Only owners and finance can add payees or change bank details.</p>
            )}
          </>
        )}
        <p className="text-center text-[11px] text-slate-400">Payouts are simulated. Risk checks are guidance, not a guarantee.</p>
      </div>
      {open && <CardDetail cardId={open} onClose={() => setOpen(null)} onChanged={() => void load()} />}
    </div>
  )
}
