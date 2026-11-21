import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import { Button, ErrorText, Logo, Screen, Spinner } from '../components/ui'
import { Api, type InvitePreview, ROLE_LABEL } from '../lib/api'
import { useAuth } from '../lib/auth'
import Login from './Login'

export default function JoinPage() {
  const { token = '' } = useParams()
  const { user, reload } = useAuth()
  const navigate = useNavigate()
  const [preview, setPreview] = useState<InvitePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    Api.previewInvite(token)
      .then(setPreview)
      .catch((err) => setError(err instanceof Error ? err.message : 'This invite link is not valid'))
  }, [token])

  async function accept() {
    setBusy(true)
    setError(null)
    try {
      const room = await Api.acceptInvite(token)
      await reload()
      navigate(`/rooms/${room.id}`, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not join')
      setBusy(false)
    }
  }

  if (error && !preview) {
    return (
      <Screen>
        <div className="flex flex-1 flex-col justify-center gap-4 px-6">
          <Logo />
          <ErrorText>{error}</ErrorText>
          <p className="text-sm text-slate-600">Ask the person who invited you for a new link.</p>
        </div>
      </Screen>
    )
  }
  if (!preview) {
    return (
      <Screen>
        <div className="grid flex-1 place-items-center text-slate-400">
          <Spinner />
        </div>
      </Screen>
    )
  }
  const what = `${preview.org_name} invited you to "${preview.room_title}" as their ${ROLE_LABEL[preview.role].toLowerCase()}.`
  if (!user) return <Login context={`${what} Verify your phone to join.`} />

  return (
    <Screen>
      <div className="flex flex-1 flex-col justify-center gap-5 px-6">
        <Logo />
        <h1 className="text-2xl font-bold text-ink">{preview.room_title}</h1>
        <p className="text-slate-600">{what}</p>
        <ul className="space-y-2 rounded-2xl bg-slate-50 p-4 text-sm text-slate-700">
          <li>You will see only this room, never their other deals.</li>
          <li>Everyone in the room sees your name and the last 4 digits of your verified phone.</li>
          <li>Your AI copilot is private to you; the other side never sees it.</li>
        </ul>
        <ErrorText>{error}</ErrorText>
        <Button onClick={() => void accept()} busy={busy}>
          Join as {user.name}
        </Button>
      </div>
    </Screen>
  )
}
