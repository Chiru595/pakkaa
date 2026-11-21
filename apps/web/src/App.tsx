import { BaseChannel, GroupChannel, type Message, SendTextMessageParams } from '@nexconn/chat'
import { type FormEvent, useEffect, useRef, useState } from 'react'

import {
  CARD_MESSAGE_TYPE,
  type ChatStatus,
  connectNexconn,
  initNexconn,
  onConnectionStatus,
  onMessages,
  TEXT_MESSAGE_TYPE,
} from './lib/nexconn'

interface HelloSession {
  user_id: string
  name: string
  token: string
  app_key: string
  area: string
  channel_id: string
}

interface ChatLine {
  id: string
  sender: string
  kind: 'text' | 'card'
  text: string
  sentTime: number
}

function toLine(m: Message): ChatLine | null {
  if (m.messageType === TEXT_MESSAGE_TYPE) {
    const c = m.content as { text?: string; content?: string }
    return { id: m.messageId, sender: m.senderUserId, kind: 'text', text: c.text ?? c.content ?? '', sentTime: m.sentTime }
  }
  if (m.messageType === CARD_MESSAGE_TYPE) {
    const c = m.content as { title?: string }
    return { id: m.messageId, sender: m.senderUserId, kind: 'card', text: c.title ?? 'Card', sentTime: m.sentTime }
  }
  return null
}

function merge(existing: ChatLine[], incoming: ChatLine[]): ChatLine[] {
  const byId = new Map(existing.map((l) => [l.id, l]))
  for (const l of incoming) byId.set(l.id, l)
  return [...byId.values()].sort((a, b) => a.sentTime - b.sentTime)
}

export default function App() {
  const [name, setName] = useState('')
  const [session, setSession] = useState<HelloSession | null>(null)
  const [status, setStatus] = useState<ChatStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [lines, setLines] = useState<ChatLine[]>([])
  const [draft, setDraft] = useState('')
  const channelRef = useRef<GroupChannel | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines])

  useEffect(() => {
    if (!session) return
    const offMessages = onMessages('hello', (messages) => {
      const mine = messages
        .filter((m) => m.channelIdentifier.channelId === session.channel_id)
        .map(toLine)
        .filter((l): l is ChatLine => l !== null)
      setLines((prev) => merge(prev, mine))
    })
    const offStatus = onConnectionStatus('hello', (connected) => setStatus(connected ? 'connected' : 'disconnected'))
    return () => {
      offMessages()
      offStatus()
    }
  }, [session])

  async function join(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setStatus('connecting')
    try {
      const res = await fetch('/api/dev/hello', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() }),
      })
      if (!res.ok) throw new Error(`Sign-in failed (${res.status})`)
      const s = (await res.json()) as HelloSession
      initNexconn(s.app_key, s.area)
      setSession(s)
      await connectNexconn(s.token)
      const channel = new GroupChannel(s.channel_id)
      channelRef.current = channel
      const history = await BaseChannel.createMessagesQuery({
        channelIdentifier: channel.identifier,
        startTime: Date.now(),
        pageSize: 30,
        isAscending: false,
      }).loadNextPage()
      if (history.isOk && history.data) {
        setLines((prev) => merge(prev, history.data!.data.map(toLine).filter((l): l is ChatLine => l !== null)))
      }
      setStatus('connected')
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : 'Something went wrong')
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    const channel = channelRef.current
    if (!text || !channel || !session) return
    setDraft('')
    const result = await channel.sendMessage(new SendTextMessageParams({ text }))
    if (result.isOk && result.data) {
      const line = toLine(result.data)
      if (line) setLines((prev) => merge(prev, [line]))
    } else {
      setError(`Message not sent (code ${result.code})`)
      setDraft(text)
    }
  }

  if (!session) {
    return (
      <main className="mx-auto flex min-h-full max-w-md flex-col justify-center px-4">
        <h1 className="text-3xl font-bold text-brand-ink">Pakkaa</h1>
        <p className="mt-1 text-slate-600">Deal pakkaa! Payment pakkaa!</p>
        <form onSubmit={join} className="mt-8 space-y-3">
          <label className="block text-sm font-medium" htmlFor="name">
            Your name
          </label>
          <input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={40}
            required
            className="w-full rounded-lg border border-slate-300 px-3 py-2 focus:border-brand focus:outline-none"
          />
          <button
            type="submit"
            disabled={status === 'connecting'}
            className="w-full rounded-lg bg-brand px-4 py-2 font-semibold text-white disabled:opacity-60"
          >
            {status === 'connecting' ? 'Joining…' : 'Join hello room'}
          </button>
          {error && <p className="text-sm text-red-600">{error}</p>}
        </form>
      </main>
    )
  }

  return (
    <main className="mx-auto flex h-full max-w-md flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div>
          <h1 className="font-semibold text-brand-ink">Hello room</h1>
          <p className="text-xs text-slate-500">Signed in as {session.name}</p>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            status === 'connected' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'
          }`}
        >
          {status}
        </span>
      </header>
      <section className="flex-1 space-y-2 overflow-y-auto px-4 py-3" aria-live="polite">
        {lines.map((l) =>
          l.kind === 'card' ? (
            <div key={l.id} className="text-center text-xs text-slate-500">
              {l.text}
            </div>
          ) : (
            <div key={l.id} className={`flex ${l.sender === session.user_id ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                  l.sender === session.user_id ? 'bg-brand text-white' : 'bg-white shadow-sm'
                }`}
              >
                {l.text}
              </div>
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </section>
      {error && <p className="px-4 text-sm text-red-600">{error}</p>}
      <form onSubmit={send} className="flex gap-2 border-t border-slate-200 bg-white p-3">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Message"
          aria-label="Message"
          className="flex-1 rounded-full border border-slate-300 px-4 py-2 focus:border-brand focus:outline-none"
        />
        <button type="submit" className="rounded-full bg-brand px-4 py-2 font-semibold text-white">
          Send
        </button>
      </form>
    </main>
  )
}
