import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'

import CopilotBar, { type Chosen } from '../components/deal/CopilotBar'
import TermSheetPanel, { CounterpartyNotes } from '../components/deal/TermSheetPanel'
import CardsPanel from '../components/paylane/CardsPanel'
import { Badge, Button, ErrorText, Screen, Spinner, VerifiedBadge } from '../components/ui'
import { Api, ROLE_LABEL, type Room, TEAM_ROLES } from '../lib/api'
import { useAuth } from '../lib/auth'
import { type ChatLine, connectChat, loadHistory, mergeLines, onChatStatus, sendText, subscribe } from '../lib/chat'
import { type Deal, DealApi, type PresendWarning } from '../lib/deal'

const DEAL_CARD_KINDS = new Set(['scribe', 'term_update', 'signature', 'payment_link', 'payment_status', 'outcome', 'memory_warning'])

function DealBar({ deal, onOpen }: { deal: Deal; onOpen: () => void }) {
  const ts = deal.term_sheet
  const val = (k: string) => ts.terms.find((t) => t.key === k)
  const price = val('price_paise')
  const adv = val('advance_pct')
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center gap-2 border-b border-slate-100 bg-emerald-50/60 px-4 py-2 text-left text-sm"
    >
      <span aria-hidden>📄</span>
      <span className="min-w-0 flex-1 truncate">
        <span className="font-semibold text-ink">Term sheet v{ts.version}</span>
        <span className="text-slate-600">
          {price?.value != null ? ` · ${price.display}` : ''}
          {adv?.value != null ? ` · ${adv.display} advance` : ''}
        </span>
      </span>
      {deal.proposals.length > 0 &&
        (ts.status === 'locked' ? (
          <Badge>
            {deal.proposals.length} suggested {deal.proposals.length === 1 ? 'change' : 'changes'}
          </Badge>
        ) : (
          <Badge tone="amber">{deal.proposals.length} to confirm</Badge>
        ))}
      <Badge tone={ts.status === 'locked' ? 'green' : 'slate'}>{ts.status === 'locked' ? 'Locked' : 'Draft'}</Badge>
    </button>
  )
}

function PaymentCard({ line }: { line: ChatLine }) {
  const c = line.card ?? {}
  return (
    <div className="mx-auto w-full max-w-[85%] rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink">{line.text}</p>
        {Boolean(c.simulated) && <Badge tone="amber">Simulated</Badge>}
      </div>
      {typeof c.url === 'string' && (
        <a href={c.url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm font-semibold text-brand underline">
          Open payment link
        </a>
      )}
    </div>
  )
}

function timeLabel(ms: number): string {
  const d = new Date(ms)
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleString([], { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
}

function MembersSheet({ room, onClose }: { room: Room; onClose: () => void }) {
  const { user } = useAuth()
  const myRole = user?.orgs.find((o) => o.org_id === room.org_id)?.role
  const [invite, setInvite] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const canInvite = myRole ? TEAM_ROLES.includes(myRole) : false

  async function createInvite() {
    setError(null)
    try {
      const res = await Api.createInvite(room.id, room.type === 'deal' ? 'client_guest' : 'vendor_guest')
      setInvite(res.url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the invite')
    }
  }

  async function copy() {
    if (!invite) return
    try {
      await navigator.clipboard.writeText(invite)
      setCopied(true)
    } catch {
      setCopied(false)
      setError('Copy is blocked in this browser. Select the link above and copy it.')
    }
  }

  return (
    <div className="absolute inset-0 z-10 flex flex-col bg-white">
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold text-ink">People in this room</h2>
        <Button variant="ghost" onClick={onClose}>
          Done
        </Button>
      </header>
      <ul className="flex-1 divide-y divide-slate-100 overflow-y-auto px-4">
        {room.members.map((m) => (
          <li key={m.user_id} className="flex items-center justify-between py-3">
            <div>
              <p className="font-medium text-ink">{m.name}</p>
              <p className="text-xs text-slate-500">
                {m.role ? ROLE_LABEL[m.role] : m.side} · phone ••{m.phone_last4}
              </p>
            </div>
            <span className="flex flex-col items-end gap-1">
              {m.verified ? <VerifiedBadge /> : <Badge tone="amber">Not verified yet</Badge>}
              {room.members.filter((x) => x.name.trim().toLowerCase() === m.name.trim().toLowerCase()).length > 1 && (
                <Badge tone="red">Same name as another member</Badge>
              )}
            </span>
          </li>
        ))}
      </ul>
      {canInvite && (
        <div className="space-y-3 border-t border-slate-100 p-4">
          {invite ? (
            <>
              <p className="select-all break-all rounded-xl bg-slate-50 p-3 font-mono text-xs text-slate-700">{invite}</p>
              <Button className="w-full" onClick={() => void copy()}>
                {copied ? 'Copied' : 'Copy invite link'}
              </Button>
              <p className="text-xs text-slate-500">Single use, expires in 72 hours. They verify their phone before joining.</p>
            </>
          ) : (
            <Button className="w-full" onClick={() => void createInvite()}>
              Invite {room.type === 'deal' ? 'your client' : 'a vendor'}
            </Button>
          )}
          <ErrorText>{error}</ErrorText>
        </div>
      )}
    </div>
  )
}

export default function RoomPage() {
  const { roomId = '' } = useParams()
  const { user } = useAuth()
  const [room, setRoom] = useState<Room | null>(null)
  const [lines, setLines] = useState<ChatLine[]>([])
  const [connected, setConnected] = useState(false)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showMembers, setShowMembers] = useState(false)
  const [deal, setDeal] = useState<Deal | null>(null)
  const [showSheet, setShowSheet] = useState(false)
  const [dealRefresh, setDealRefresh] = useState(0)
  const [trigger, setTrigger] = useState<{ id: string; at: number } | null>(null)
  const [warnings, setWarnings] = useState<PresendWarning[]>([])
  const [chosen, setChosen] = useState<Chosen | null>(null)
  const [showCards, setShowCards] = useState(false)
  const [focusCard, setFocusCard] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const myChatId = user ? `u${user.id}` : ''
  const myChatIdRef = useRef(myChatId)
  useEffect(() => {
    myChatIdRef.current = myChatId
  }, [myChatId])
  const isDeal = room?.type === 'deal' && (room.my_side === 'provider' || room.my_side === 'client')
  const myRole = room ? user?.orgs.find((o) => o.org_id === room.org_id)?.role : undefined
  const isTeamLane = room?.type === 'paylane' && !!myRole && TEAM_ROLES.includes(myRole)

  const reloadDeal = useCallback(async () => {
    try {
      setDeal(await DealApi.get(roomId))
      setDealRefresh((n) => n + 1)
    } catch {
      /* the panel keeps its last state */
    }
  }, [roomId])

  useEffect(() => {
    let cancelled = false
    let unsubscribe: (() => void) | undefined
    ;(async () => {
      try {
        let r = await Api.room(roomId)
        if (!r.my_side) r = await Api.joinRoom(roomId)
        if (cancelled) return
        setRoom(r)
        unsubscribe = subscribe(r.channel_id, (incoming) => {
          setLines((prev) => mergeLines(prev, incoming))
          if (incoming.some((l) => l.kind === 'card' && l.card?.kind === 'system')) {
            Api.room(roomId).then((fresh) => !cancelled && setRoom(fresh)).catch(() => undefined)
          }
          if (r.type === 'deal' && incoming.some((l) => l.kind === 'card' && DEAL_CARD_KINDS.has(String(l.card?.kind)))) {
            void reloadDeal()
          }
          const theirs = incoming.filter((l) => l.kind === 'text' && l.sender !== myChatIdRef.current)
          if (r.type === 'deal' && theirs.length) setTrigger({ id: theirs[theirs.length - 1].id, at: Date.now() })
        })
        if (r.type === 'deal' && (r.my_side === 'provider' || r.my_side === 'client')) {
          DealApi.get(r.id).then((d) => !cancelled && setDeal(d)).catch(() => undefined)
        }
        await connectChat()
        if (cancelled) return
        setConnected(true)
        const history = await loadHistory(r.id)
        if (!cancelled) setLines((prev) => mergeLines(prev, history))
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not open the room')
      }
    })()
    const offStatus = onChatStatus(setConnected)
    return () => {
      cancelled = true
      unsubscribe?.()
      offStatus()
    }
  }, [roomId, reloadDeal])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines])

  useEffect(() => {
    if (!isDeal) return
    const text = draft.trim()
    const id = setTimeout(
      () => {
        if (text.length < 3) setWarnings([])
        else DealApi.check(roomId, text).then(setWarnings).catch(() => setWarnings([]))
      },
      text.length < 3 ? 0 : 400,
    )
    return () => clearTimeout(id)
  }, [draft, isDeal, roomId])

  async function send(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || !room) return
    setSending(true)
    setDraft('')
    try {
      const line = await sendText(room.id, text)
      setLines((prev) => mergeLines(prev, [line]))
      setWarnings([])
      if (chosen) {
        const outcome = text === chosen.text ? 'sent' : 'edited'
        void DealApi.feedback(room.id, chosen.suggestionId, chosen.index, outcome).catch(() => undefined)
        setChosen(null)
      }
    } catch (err) {
      setDraft(text)
      setError(err instanceof Error ? err.message : 'Message not sent')
    } finally {
      setSending(false)
    }
  }

  const names = new Map(room?.members.map((m) => [`u${m.user_id}`, m]) ?? [])
  const normal = (s: string) => s.normalize('NFKD').toLowerCase().replace(/[^a-z]/g, '')
  const nameCount = new Map<string, number>()
  room?.members.forEach((m) => nameCount.set(normal(m.name), (nameCount.get(normal(m.name)) ?? 0) + 1))
  const mine = myChatId

  return (
    <Screen>
      <div className="relative flex h-dvh flex-col sm:h-[calc(100dvh-2rem)]">
        <header className="flex items-center gap-3 border-b border-slate-100 px-3 py-3">
          <Link to="/" className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label="Back to rooms">
            ←
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-semibold text-ink">{room?.title ?? 'Opening…'}</h1>
            <p className="text-xs text-slate-500">
              {room ? (room.type === 'deal' ? 'Deal Room' : 'Pay Lane') : ''} · {room?.members.length ?? 0}{' '}
              {room?.members.length === 1 ? 'person' : 'people'}
            </p>
          </div>
          <span className={`size-2.5 rounded-full ${connected ? 'bg-emerald-500' : 'bg-amber-400'}`} title={connected ? 'Live' : 'Connecting'} />
          {room && (
            <Button variant="secondary" className="px-3 py-1.5" onClick={() => setShowMembers(true)}>
              People
            </Button>
          )}
        </header>
        {isDeal && deal && <DealBar deal={deal} onOpen={() => setShowSheet(true)} />}
        {isDeal && deal && deal.counterparty_notes.length > 0 && (
          <div className="border-b border-amber-200 px-3 py-2">
            <CounterpartyNotes notes={deal.counterparty_notes} compact />
          </div>
        )}
        {isTeamLane && (
          <button
            type="button"
            onClick={() => {
              setFocusCard(null)
              setShowCards(true)
            }}
            className="flex w-full items-center gap-2 border-b border-slate-100 bg-sky-50/70 px-4 py-2 text-left text-sm"
          >
            <span aria-hidden>💸</span>
            <span className="flex-1 font-semibold text-ink">Payments, payees and kill switch</span>
            <span className="text-xs text-slate-500">Open</span>
          </button>
        )}

        <section className="flex-1 space-y-2 overflow-y-auto bg-slate-50 px-3 py-4" aria-live="polite">
          {!room && !error && (
            <div className="grid h-full place-items-center text-slate-400">
              <Spinner />
            </div>
          )}
          {lines.map((l) => {
            if (l.kind === 'card' && (l.card?.kind === 'payment_link' || l.card?.kind === 'payment_status')) {
              return <PaymentCard key={l.id} line={l} />
            }
            if (l.kind === 'card' && l.card?.kind === 'payment_card' && isTeamLane) {
              return (
                <button
                  key={l.id}
                  type="button"
                  onClick={() => {
                    setFocusCard(String(l.card?.card_id ?? ''))
                    setShowCards(true)
                  }}
                  className="mx-auto block w-full max-w-[90%] rounded-2xl border border-slate-200 bg-white px-3 py-2 text-left text-sm shadow-sm hover:border-brand"
                >
                  💸 {l.text}
                </button>
              )
            }
            if (l.kind === 'card' && l.card?.kind === 'memory_warning') {
              if (deal && deal.counterparty_notes.length > 0) {
                return (
                  <p key={l.id} role="note" className="mx-auto w-fit rounded-full bg-amber-50 px-3 py-1 text-center text-xs text-amber-900 ring-1 ring-amber-200">
                    🔒 Private heads-up pinned above · only your side sees it
                  </p>
                )
              }
              return (
                <p key={l.id} role="note" className="mx-auto max-w-[90%] rounded-2xl border border-amber-300 bg-amber-50 px-3 py-2 text-center text-xs text-amber-900">
                  🔒 {l.text}
                </p>
              )
            }
            if (l.kind === 'card') {
              return (
                <p key={l.id} className="mx-auto w-fit rounded-full bg-white px-3 py-1 text-center text-xs text-slate-500 shadow-sm">
                  {l.text}
                </p>
              )
            }
            const isMine = l.sender === mine
            const who = names.get(l.sender)
            return (
              <div key={l.id} className={`flex flex-col ${isMine ? 'items-end' : 'items-start'}`}>
                {!isMine && (
                  <span className="mb-0.5 flex items-center gap-1 px-1 text-xs text-slate-500">
                    {who?.name ?? 'Unknown'}
                    {who?.verified && <span className="text-emerald-600" title="Verified phone">✓</span>}
                    {who?.role && <span>· {ROLE_LABEL[who.role]}</span>}
                    {who && (nameCount.get(normal(who.name)) ?? 0) > 1 && (
                      <span className="font-semibold text-red-700" title="Another person in this room has the same name">
                        ⚠ same name as another member
                      </span>
                    )}
                  </span>
                )}
                <div
                  className={`max-w-[82%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-[15px] ${
                    isMine ? 'rounded-br-md bg-brand text-white' : 'rounded-bl-md bg-white text-ink shadow-sm'
                  }`}
                >
                  {l.text}
                </div>
                <time className="mt-0.5 px-1 text-[11px] text-slate-400" dateTime={new Date(l.sentTime).toISOString()}>
                  {timeLabel(l.sentTime)}
                </time>
              </div>
            )
          })}
          <div ref={bottomRef} />
        </section>

        {error && (
          <div className="px-3 pt-2">
            <ErrorText>{error}</ErrorText>
          </div>
        )}
        {isDeal && room?.my_side && (
          <CopilotBar
            roomId={roomId}
            side={room.my_side}
            trigger={trigger}
            warnings={warnings}
            onUse={(c) => {
              setChosen(c)
              setDraft(c.text)
            }}
          />
        )}
        <form onSubmit={send} className="flex gap-2 border-t border-slate-100 bg-white p-3">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Message"
            aria-label="Message"
            maxLength={4000}
            disabled={!room}
            className="min-w-0 flex-1 rounded-full border border-slate-300 px-4 py-2.5 text-[15px] focus:border-brand focus:outline-none"
          />
          <Button type="submit" className="rounded-full" busy={sending} disabled={!room || !draft.trim()}>
            Send
          </Button>
        </form>
        {showMembers && room && <MembersSheet room={room} onClose={() => setShowMembers(false)} />}
        {showCards && room && myRole && (
          <CardsPanel roomId={room.id} orgId={room.org_id} role={myRole} focusCardId={focusCard} onClose={() => setShowCards(false)} />
        )}
        {showSheet && deal && (
          <TermSheetPanel
            roomId={roomId}
            deal={deal}
            onDeal={setDeal}
            onClose={() => setShowSheet(false)}
            refreshKey={dealRefresh}
          />
        )}
      </div>
    </Screen>
  )
}
