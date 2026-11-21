import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router'

import { Button, ErrorText, Field, Logo, Screen } from '../components/ui'
import { Api, type OtpRequested } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useDemoGuide } from '../lib/demo'
import { digitsOnlyPhone, formatPhone } from '../lib/phone'

const RESEND_SECONDS = 30

export default function Login({ context }: { context?: string }) {
  const { signIn } = useAuth()
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState<OtpRequested | null>(null)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resendIn, setResendIn] = useState(0)
  const demo = useDemoGuide()

  useEffect(() => {
    if (resendIn <= 0) return
    const id = setTimeout(() => setResendIn((s) => s - 1), 1000)
    return () => clearTimeout(id)
  }, [resendIn])

  const phoneValid = /^[6-9]\d{9}$/.test(phone)

  async function sendCode() {
    setBusy(true)
    setError(null)
    try {
      setOtp(await Api.requestOtp(`+91${phone}`))
      setResendIn(RESEND_SECONDS)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send the code')
    } finally {
      setBusy(false)
    }
  }

  function requestCode(e: FormEvent) {
    e.preventDefault()
    if (!phoneValid) {
      setError('Enter a valid 10-digit Indian mobile number')
      return
    }
    void sendCode()
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
            <div className="space-y-1.5">
              <label htmlFor="phone" className="block text-sm font-medium text-ink">
                Mobile number
              </label>
              <div className="flex overflow-hidden rounded-xl border border-slate-300 bg-white focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/20">
                <span className="grid place-items-center border-r border-slate-200 bg-slate-50 px-3 text-base text-slate-600">
                  +91
                </span>
                <input
                  id="phone"
                  type="tel"
                  inputMode="numeric"
                  autoComplete="tel-national"
                  placeholder="98765 43210"
                  value={formatPhone(phone)}
                  onChange={(e) => setPhone(digitsOnlyPhone(e.target.value))}
                  required
                  aria-describedby="phone-hint"
                  className="min-w-0 flex-1 px-3 py-2.5 text-base tracking-wide text-ink placeholder:text-slate-400 focus:outline-none"
                />
              </div>
              <p id="phone-hint" className="text-xs text-slate-500">
                10-digit Indian mobile number. We send a one-time code to verify it.
              </p>
              {demo && (
                <div className="flex flex-wrap gap-1.5 pt-1" aria-label="Demo accounts">
                  {demo.accounts.slice(0, 5).map((acc) => (
                    <button
                      key={acc.phone}
                      type="button"
                      onClick={() => setPhone(acc.phone)}
                      className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-700 hover:border-brand"
                    >
                      {acc.name.split(' ')[0]} · {acc.role.replace('_guest', '')}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <ErrorText>{error}</ErrorText>
            <Button type="submit" className="w-full" busy={busy} disabled={!phoneValid}>
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
              label={`Code sent to +91 ${formatPhone(otp.phone.slice(-10))}`}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              required
            />
            {otp.is_new_user && (
              <Field
                id="name"
                label="Your name"
                autoComplete="name"
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
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
            <Button type="submit" className="w-full" busy={busy} disabled={code.length !== 6}>
              Verify and continue
            </Button>
            <div className="flex justify-between text-sm">
              <button
                type="button"
                className="text-slate-500 underline"
                onClick={() => {
                  setOtp(null)
                  setCode('')
                  setError(null)
                }}
              >
                Change number
              </button>
              <button
                type="button"
                className="text-brand underline disabled:text-slate-400 disabled:no-underline"
                disabled={resendIn > 0 || busy}
                onClick={() => {
                  setCode('')
                  void sendCode()
                }}
              >
                {resendIn > 0 ? `Resend in ${resendIn}s` : 'Resend code'}
              </button>
            </div>
          </form>
        )}
      </div>
      <p className="px-6 pb-6 text-center text-xs text-slate-400">Not legal or financial advice. Payments in this build are simulated.</p>
    </Screen>
  )
}
