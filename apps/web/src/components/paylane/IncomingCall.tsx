import { useEffect, useState } from 'react'

import { acceptCall, endCall, type IncomingCall as Call, onCallEvents } from '../../lib/call'
import { Button } from '../ui'

export default function IncomingCall({ nameFor }: { nameFor: (chatId: string) => string }) {
  const [call, setCall] = useState<Call | null>(null)
  const [state, setState] = useState<'ringing' | 'connected'>('ringing')
  const [error, setError] = useState<string | null>(null)

  useEffect(
    () =>
      onCallEvents((e) => {
        if (e.type === 'incoming') {
          setCall(e.call)
          setState('ringing')
          setError(null)
        } else if (e.type === 'connected') setState('connected')
        else if (e.type === 'ended') setCall(null)
      }),
    [],
  )

  if (!call) return null
  return (
    <div className="absolute inset-x-3 top-3 z-40 rounded-2xl bg-ink p-4 text-white shadow-xl" role="alertdialog" aria-label="Incoming verify call">
      <p className="text-sm text-slate-300">{state === 'ringing' ? 'Incoming verify call' : 'On a verify call'}</p>
      <p className="text-lg font-semibold">{nameFor(call.fromChatId)}</p>
      <p className="mt-1 text-xs text-slate-300">
        They are checking a payment you requested. Never share an OTP, PIN or password on this call.
      </p>
      {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
      <div className="mt-3 flex gap-2">
        {state === 'ringing' && (
          <Button className="flex-1" onClick={() => acceptCall(call.callId).catch((e) => setError(e instanceof Error ? e.message : 'Could not answer'))}>
            Answer
          </Button>
        )}
        <Button variant="danger" className="flex-1" onClick={() => void endCall(call.callId).then(() => setCall(null))}>
          {state === 'ringing' ? 'Decline' : 'Hang up'}
        </Button>
      </div>
    </div>
  )
}
