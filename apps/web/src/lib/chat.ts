import {
  AreaCode,
  ConnectionStatusHandler,
  LogLevel,
  type Message,
  MessageHandler,
  NCEngine,
} from '@nexconn/chat'

import { Api, type ChatMessageOut } from './api'

export const CARD_MESSAGE_TYPE = 'PK:Card'
export const TEXT_MESSAGE_TYPE = 'RC:TxtMsg'

export interface ChatLine {
  id: string
  sender: string
  kind: 'text' | 'card'
  text: string
  card?: Record<string, unknown>
  sentTime: number
}

type Listener = (lines: ChatLine[]) => void
type StatusListener = (connected: boolean) => void

let initializedKey: string | null = null
let connecting: Promise<string> | null = null
let connectedUser: string | null = null
const listeners = new Map<string, Set<Listener>>()
const statusListeners = new Set<StatusListener>()

function areaCode(area: string) {
  const code = (AreaCode as Record<string, (typeof AreaCode)[keyof typeof AreaCode]>)[area.toUpperCase()]
  if (code === undefined) throw new Error(`Unsupported chat region ${area}`)
  return code
}

export function toLine(m: Message): ChatLine | null {
  if (m.messageType === TEXT_MESSAGE_TYPE) {
    const c = m.content as { text?: string; content?: string }
    return { id: m.messageId, sender: m.senderUserId, kind: 'text', text: c.text ?? c.content ?? '', sentTime: m.sentTime }
  }
  if (m.messageType === CARD_MESSAGE_TYPE) {
    const c = m.content as Record<string, unknown>
    return {
      id: m.messageId,
      sender: m.senderUserId,
      kind: 'card',
      text: String(c.title ?? 'Card'),
      card: c,
      sentTime: m.sentTime,
    }
  }
  return null
}

function dispatch(messages: Message[]) {
  const byChannel = new Map<string, ChatLine[]>()
  for (const m of messages) {
    const line = toLine(m)
    if (!line) continue
    const id = m.channelIdentifier.channelId
    byChannel.set(id, [...(byChannel.get(id) ?? []), line])
  }
  for (const [id, lines] of byChannel) listeners.get(id)?.forEach((l) => l(lines))
}

export async function connectChat(): Promise<string> {
  if (connectedUser) return connectedUser
  connecting ??= (async () => {
    const t = await Api.chatToken()
    if (initializedKey !== t.app_key) {
      NCEngine.initialize({ appKey: t.app_key, areaCode: areaCode(t.area), logLevel: LogLevel.ERROR })
      NCEngine.registerCustomMessages([{ messageType: CARD_MESSAGE_TYPE, isPersisted: true, isCounted: true }])
      NCEngine.addMessageHandler('pakkaa', new MessageHandler({ onMessageReceived: ({ messages }) => dispatch(messages) }))
      NCEngine.addConnectionStatusHandler(
        'pakkaa',
        new ConnectionStatusHandler({
          onConnectionStatusChanged: ({ status }) => statusListeners.forEach((l) => l(status === 0)),
        }),
      )
      initializedKey = t.app_key
    }
    const result = await NCEngine.connect({ token: t.token })
    if (!result.isOk || !result.data) throw new Error(`Chat connection failed (code ${result.code})`)
    connectedUser = result.data.userId
    return connectedUser
  })().finally(() => (connecting = null))
  return connecting
}

export async function disconnectChat() {
  if (!connectedUser) return
  connectedUser = null
  await NCEngine.disconnect().catch(() => undefined)
}

export function subscribe(channelId: string, listener: Listener): () => void {
  const set = listeners.get(channelId) ?? new Set<Listener>()
  set.add(listener)
  listeners.set(channelId, set)
  return () => set.delete(listener)
}

export function onChatStatus(listener: StatusListener): () => void {
  statusListeners.add(listener)
  return () => statusListeners.delete(listener)
}

function fromApi(m: ChatMessageOut): ChatLine {
  return {
    id: m.id,
    sender: m.sender_chat_id,
    kind: m.kind,
    text: m.text,
    card: m.card ?? undefined,
    sentTime: m.sent_time,
  }
}

export async function loadHistory(roomId: string): Promise<ChatLine[]> {
  return (await Api.messages(roomId)).map(fromApi)
}

export async function sendText(roomId: string, text: string): Promise<ChatLine> {
  return fromApi(await Api.sendMessage(roomId, text))
}

export function mergeLines(existing: ChatLine[], incoming: ChatLine[]): ChatLine[] {
  const byId = new Map(existing.map((l) => [l.id, l]))
  for (const l of incoming) byId.set(l.id, l)
  return [...byId.values()].sort((a, b) => a.sentTime - b.sentTime)
}
