import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'

import { Badge, Button, ErrorText, Screen, Spinner, VerifiedBadge } from '../components/ui'
import { Api, ROLE_LABEL, type Room, TEAM_ROLES } from '../lib/api'
import { useAuth } from '../lib/auth'
import { type ChatLine, connectChat, loadHistory, mergeLines, onChatStatus, sendText, subscribe } from '../lib/chat'

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
            {m.verified ? <VerifiedBadge /> : <Badge tone="amber">Not verified yet</Badge>}
          </li>
        ))}
      </ul>
      {canInvite && (
        <div className="space-y-3 border-t border-slate-100 p-4">
          {invite ? (
            <>
              <p className="break-all rounded-xl bg-slate-50 p-3 font-mono text-xs text-slate-700">{invite}</p>
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
  const bottomRef = useRef<HTMLDivElement | null>(null)

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
        })
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
  }, [roomId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines])

  async function send(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || !room) return
    setSending(true)
    setDraft('')
    try {
      const line = await sendText(room.id, text)
      setLines((prev) => mergeLines(prev, [line]))
    } catch (err) {
      setDraft(text)
      setError(err instanceof Error ? err.message : 'Message not sent')
    } finally {
      setSending(false)
    }
  }

  const names = new Map(room?.members.map((m) => [`u${m.user_id}`, m]) ?? [])
  const mine = user ? `u${user.id}` : ''

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
              {room ? (room.type === 'deal' ? 'Deal Room' : 'Pay Lane') : ''} · {room?.members.length ?? 0} people
            </p>
          </div>
          <span className={`size-2.5 rounded-full ${connected ? 'bg-emerald-500' : 'bg-amber-400'}`} title={connected ? 'Live' : 'Connecting'} />
          {room && (
            <Button variant="secondary" className="px-3 py-1.5" onClick={() => setShowMembers(true)}>
              People
            </Button>
          )}
        </header>

        <section className="flex-1 space-y-2 overflow-y-auto bg-slate-50 px-3 py-4" aria-live="polite">
          {!room && !error && (
            <div className="grid h-full place-items-center text-slate-400">
              <Spinner />
            </div>
          )}
          {lines.map((l) => {
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
                  </span>
                )}
                <div
                  className={`max-w-[82%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-[15px] ${
                    isMine ? 'rounded-br-md bg-brand text-white' : 'rounded-bl-md bg-white text-ink shadow-sm'
                  }`}
                >
                  {l.text}
                </div>
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
        <form onSubmit={send} className="flex gap-2 border-t border-slate-100 bg-white p-3">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Message"
            aria-label="Message"
            disabled={!room}
            className="min-w-0 flex-1 rounded-full border border-slate-300 px-4 py-2.5 text-[15px] focus:border-brand focus:outline-none"
          />
          <Button type="submit" className="rounded-full" busy={sending} disabled={!room || !draft.trim()}>
            Send
          </Button>
        </form>
        {showMembers && room && <MembersSheet room={room} onClose={() => setShowMembers(false)} />}
      </div>
    </Screen>
  )
}
