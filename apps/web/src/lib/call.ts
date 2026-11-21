import {
  NCCallEngine,
  NCCallMediaType,
  type NCCallReceivedEvent,
  type NCCallSession,
  NCCallType,
} from '@nexconn/call'

export interface IncomingCall {
  callId: string
  fromChatId: string
  cardId: string | null
}

type Listener = (event: { type: 'incoming'; call: IncomingCall } | { type: 'connected' | 'ended'; callId: string }) => void

let engine: NCCallEngine | null = null
const listeners = new Set<Listener>()

/** Must run before NCEngine.initialize. */
export function installCalls() {
  NCCallEngine.install()
}

/** Runs once after NCEngine.initialize. */
export function initCalls() {
  if (engine) return
  engine = NCCallEngine.initialize({})
  engine.setCallEventHandler({
    onCallReceived(event: NCCallReceivedEvent) {
      let cardId: string | null = null
      try {
        cardId = event.extra ? (JSON.parse(event.extra).card_id ?? null) : null
      } catch {
        cardId = null
      }
      const call = { callId: event.session.getCallId(), fromChatId: event.session.getCallerUserId(), cardId }
      listeners.forEach((l) => l({ type: 'incoming', call }))
    },
    onCallConnected({ session }: { session: NCCallSession }) {
      listeners.forEach((l) => l({ type: 'connected', callId: session.getCallId() }))
    },
    onCallEnded({ session }: { session: NCCallSession }) {
      listeners.forEach((l) => l({ type: 'ended', callId: session.getCallId() }))
    },
  } as Parameters<NCCallEngine['setCallEventHandler']>[0])
}

export function onCallEvents(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function requireEngine(): NCCallEngine {
  if (!engine) throw new Error('Calling is not ready yet. Open a room first.')
  return engine
}

/** Audio-only call to a teammate's in-app account. The callee id comes from the server, never from a message. */
export async function startVerifyCall(calleeChatId: string, cardId: string): Promise<string> {
  const res = await requireEngine().startCall({
    calleeIds: [calleeChatId],
    callType: NCCallType.SINGLE,
    mediaType: NCCallMediaType.AUDIO,
    extra: JSON.stringify({ card_id: cardId, purpose: 'verify' }),
  })
  if (res.code !== 0 || !res.callId) throw new Error(`Call could not start (code ${res.code}). Is calling enabled?`)
  return res.callId
}

export async function acceptCall(callId: string) {
  const res = await requireEngine().acceptCall({ callId })
  if (res.code !== 0) throw new Error(`Could not answer (code ${res.code})`)
}

export async function endCall(callId?: string) {
  if (!engine) return
  await engine.endCall(callId ? { callId } : undefined).catch(() => undefined)
}
