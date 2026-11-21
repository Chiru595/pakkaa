import {
  AreaCode,
  ConnectionStatusHandler,
  LogLevel,
  type Message,
  MessageHandler,
  NCEngine,
} from '@nexconn/chat'

export const CARD_MESSAGE_TYPE = 'PK:Card'
export const TEXT_MESSAGE_TYPE = 'RC:TxtMsg'

export type ChatStatus = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'

let initializedKey: string | null = null

function areaCode(area: string) {
  const code = (AreaCode as Record<string, (typeof AreaCode)[keyof typeof AreaCode]>)[area.toUpperCase()]
  if (code === undefined) throw new Error(`Unsupported Nexconn area ${area}`)
  return code
}

export function initNexconn(appKey: string, area: string) {
  if (initializedKey === appKey) return
  if (initializedKey) throw new Error('Nexconn is already initialized with another App Key')
  NCEngine.initialize({ appKey, areaCode: areaCode(area), logLevel: LogLevel.WARN })
  NCEngine.registerCustomMessages([{ messageType: CARD_MESSAGE_TYPE, isPersisted: true, isCounted: true }])
  initializedKey = appKey
}

export async function connectNexconn(token: string): Promise<string> {
  const result = await NCEngine.connect({ token })
  if (!result.isOk || !result.data) throw new Error(`Chat connection failed (code ${result.code})`)
  return result.data.userId
}

export function onMessages(id: string, handler: (messages: Message[]) => void): () => void {
  NCEngine.addMessageHandler(id, new MessageHandler({ onMessageReceived: ({ messages }) => handler(messages) }))
  return () => NCEngine.removeMessageHandler(id)
}

export function onConnectionStatus(id: string, handler: (connected: boolean) => void): () => void {
  NCEngine.addConnectionStatusHandler(
    id,
    new ConnectionStatusHandler({ onConnectionStatusChanged: ({ status }) => handler(status === 0) }),
  )
  return () => NCEngine.removeConnectionStatusHandler(id)
}
