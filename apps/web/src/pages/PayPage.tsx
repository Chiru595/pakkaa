import { useEffect, useState } from 'react'
import { useParams } from 'react-router'

import { Badge, Button, ErrorText, Logo, Screen, Spinner } from '../components/ui'
import { DealApi, type PayPage as Page } from '../lib/deal'

export default function PayPage() {
  const { token = '' } = useParams()
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'paid' | 'failed' | null>(null)

  useEffect(() => {
    DealApi.payPage(token)
      .then(setPage)
      .catch((e) => setError(e instanceof Error ? e.message : 'Payment link not found'))
  }, [token])

  async function pay(outcome: 'paid' | 'failed') {
    setBusy(outcome)
    setError(null)
    try {
      setPage(await DealApi.simulatePay(token, outcome))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Payment failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Screen>
      <div className="flex flex-1 flex-col justify-center gap-5 px-6 py-10">
        <Logo />
        {!page && !error && (
          <div className="grid place-items-center text-slate-400">
            <Spinner />
          </div>
        )}
        <ErrorText>{error}</ErrorText>
        {page && (
          <>
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <p className="text-sm text-slate-500">Pay {page.org_name}</p>
                {page.simulated && <Badge tone="amber">Simulated payment</Badge>}
              </div>
              <p className="text-4xl font-extrabold text-ink">{page.amount_display}</p>
              <p className="text-slate-600">
                {page.purpose === 'advance' ? 'Advance' : 'Payment'} for “{page.room_title}”
              </p>
            </div>
            {page.status === 'pending' ? (
              <div className="space-y-2">
                <Button className="w-full" busy={busy === 'paid'} disabled={busy !== null} onClick={() => void pay('paid')}>
                  Pay {page.amount_display} (simulated)
                </Button>
                <Button variant="secondary" className="w-full" busy={busy === 'failed'} disabled={busy !== null} onClick={() => void pay('failed')}>
                  Simulate a failed payment
                </Button>
              </div>
            ) : (
              <p className={`rounded-2xl p-4 text-center font-semibold ${page.status === 'paid' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>
                {page.status === 'paid' ? 'Paid. Thank you!' : 'This payment failed.'}
              </p>
            )}
            <p className="text-center text-xs text-slate-400">
              No money moves in this build. A real gateway would show this page and confirm the payment with a signed
              callback.
            </p>
          </>
        )}
      </div>
    </Screen>
  )
}
