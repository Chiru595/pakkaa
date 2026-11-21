import { type FormEvent, useState } from 'react'
import { Link } from 'react-router'

import { Button, ErrorText, Field, Logo, Screen } from '../components/ui'
import { Api, type OtpRequested } from '../lib/api'
import { useAuth } from '../lib/auth'

export default function Login({ context }: { context?: string }) {
  const { signIn } = useAuth()
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState<OtpRequested | null>(null)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function requestCode(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setOtp(await Api.requestOtp(phone))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send the code')
    } finally {
      setBusy(false)
    }
  }

  async function verify(e: FormEvent) {
    e.preventDefault()
    if (!otp) return
    setBusy(true)
    setError(null)
    try {
      const res = await Api.verifyOtp({ phone: otp.phone, code, name: name.trim() || undefined, consent })
      signIn(res.access_token, res.user)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not verify the code')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen>
      <div className="flex flex-1 flex-col justify-center px-6 py-10">
        <Logo size="lg" />
        <p className="mt-2 text-lg font-semibold text-brand">Deal pakkaa! Payment pakkaa!</p>
        <p className="mt-1 text-sm text-slate-600">
          {context ?? 'Where freelancers, clients and small teams settle deals and move money safely.'}
        </p>

        {!otp ? (
          <form onSubmit={requestCode} className="mt-8 space-y-4">
            <Field
              id="phone"
              label="Mobile number"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="98765 43210"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              required
            />
            <ErrorText>{error}</ErrorText>
            <Button type="submit" className="w-full" busy={busy}>
              Send code
            </Button>
          </form>
        ) : (
          <form onSubmit={verify} className="mt-8 space-y-4">
            {otp.test_mode && otp.test_code && (
              <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                <strong>Test mode:</strong> no SMS is sent. Your code is{' '}
                <span className="font-mono font-bold tracking-widest">{otp.test_code}</span>
              </div>
            )}
            <Field
              id="code"
              label={`Code sent to ••${otp.phone.slice(-4)}`}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              required
            />
            {otp.is_new_user && (
              <Field id="name" label="Your name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} required />
            )}
            {otp.needs_consent && (
              <label className="flex gap-3 rounded-xl bg-slate-50 p-3 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  className="mt-0.5 size-4 accent-brand"
                  required
                />
                <span>
                  I agree that Pakkaa processes my name, phone number and the messages and payment details I share in
                  rooms, to run deals and payments and to show AI suggestions. Read the{' '}
                  <Link to="/privacy" className="font-medium text-brand underline" target="_blank">
                    privacy notice
                  </Link>
                  .
                </span>
              </label>
            )}
            <ErrorText>{error}</ErrorText>
            <Button type="submit" className="w-full" busy={busy}>
              Verify and continue
            </Button>
            <button
              type="button"
              className="w-full text-sm text-slate-500 underline"
              onClick={() => {
                setOtp(null)
                setCode('')
              }}
            >
              Use a different number
            </button>
          </form>
        )}
      </div>
      <p className="px-6 pb-6 text-center text-xs text-slate-400">Not legal or financial advice. Payments in this build are simulated.</p>
    </Screen>
  )
}
