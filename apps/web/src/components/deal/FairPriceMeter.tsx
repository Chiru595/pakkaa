import { useEffect, useState } from 'react'

import { DealApi, type FairPrice, rupees } from '../../lib/deal'

const TONE: Record<string, string> = {
  below_range: 'text-amber-700',
  lower_half: 'text-emerald-700',
  upper_half: 'text-emerald-700',
  above_range: 'text-sky-700',
}

export default function FairPriceMeter({ roomId, refreshKey }: { roomId: string; refreshKey: number }) {
  const [data, setData] = useState<FairPrice | null>(null)
  const [category, setCategory] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    DealApi.fairPrice(roomId, category)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : 'Could not load the meter'))
    return () => {
      live = false
    }
  }, [roomId, category, refreshKey])

  if (error) return <p className="text-sm text-slate-500">{error}</p>
  if (!data) return <p className="text-sm text-slate-400">Loading fair-price meter…</p>

  const band = data.band
  const lo = band ? band.p25 * 0.5 : 0
  const hi = band ? band.p75 * 1.5 : 1
  const pct = (v: number) => `${Math.min(100, Math.max(0, ((v - lo) / (hi - lo)) * 100))}%`

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <label className="text-sm text-slate-600" htmlFor="fp-cat">
          Category
        </label>
        <select
          id="fp-cat"
          value={category ?? data.category ?? ''}
          onChange={(e) => setCategory(e.target.value || undefined)}
          className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm"
        >
          <option value="">Choose…</option>
          {Object.entries(data.categories).map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {band && (
        <div aria-label="Rate band" className="relative mt-6 h-3 rounded-full bg-gradient-to-r from-amber-200 via-emerald-200 to-sky-200">
          {(['p25', 'p50', 'p75'] as const).map((k) => (
            <span key={k} className="absolute top-0 h-3 w-px bg-slate-500" style={{ left: pct(band[k]) }}>
              <span className="absolute -top-5 -translate-x-1/2 whitespace-nowrap text-[10px] text-slate-500">
                {rupees(band[k])}
              </span>
            </span>
          ))}
          {data.offer_paise && (
            <span
              className="absolute -top-1 size-5 -translate-x-1/2 rounded-full border-2 border-white bg-ink shadow"
              style={{ left: pct(data.offer_paise) }}
              title={`Offer ${rupees(data.offer_paise)}`}
            />
          )}
        </div>
      )}
      <p className={`text-sm font-semibold ${data.position ? TONE[data.position] : 'text-slate-600'}`}>
        {data.offer_paise ? `${rupees(data.offer_paise)}: ` : ''}
        {data.verdict}
      </p>
      {data.comparisons.map((c) => (
        <p key={c.label} className="text-sm text-slate-600">
          {c.label}: median {rupees(c.median_paise)} ({c.count})
        </p>
      ))}
      <p className="text-[11px] leading-snug text-slate-400">
        Bands: {data.source} As of {data.as_of}. {data.disclaimer}
      </p>
    </div>
  )
}
