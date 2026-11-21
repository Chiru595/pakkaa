import { type FormEvent, useState } from 'react'

import { DealApi, type Explanation } from '../../lib/deal'
import { Badge, Button, ErrorText } from '../ui'

const LANGS = [
  ['en', 'English'],
  ['hi', 'हिन्दी'],
  ['hinglish', 'Hinglish'],
] as const

const TONE = { standard: 'green', negotiable: 'blue', unusual: 'amber', risky: 'red' } as const

export default function ExplainSheet({ roomId, initial, onClose }: { roomId: string; initial: string; onClose: () => void }) {
  const [text, setText] = useState(initial)
  const [language, setLanguage] = useState<'en' | 'hi' | 'hinglish'>('en')
  const [result, setResult] = useState<Explanation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setResult(await DealApi.explain(roomId, text.trim(), language))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not explain this clause')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="absolute inset-0 z-30 flex flex-col bg-white" role="dialog" aria-label="Clause explainer">
      <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold text-ink">What does this clause mean?</h2>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </header>
      <form onSubmit={submit} className="flex-1 space-y-4 overflow-y-auto p-4">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={1500}
          rows={4}
          required
          minLength={3}
          aria-label="Clause text"
          className="w-full rounded-xl border border-slate-300 p-3 text-sm focus:border-brand focus:outline-none"
        />
        <div className="flex gap-2" role="radiogroup" aria-label="Language">
          {LANGS.map(([k, label]) => (
            <button
              type="button"
              key={k}
              role="radio"
              aria-checked={language === k}
              onClick={() => setLanguage(k)}
              className={`rounded-full px-3 py-1.5 text-sm ${language === k ? 'bg-ink text-white' : 'bg-slate-100 text-slate-700'}`}
            >
              {label}
            </button>
          ))}
        </div>
        <Button type="submit" busy={busy} className="w-full" disabled={text.trim().length < 3}>
          Explain
        </Button>
        <ErrorText>{error}</ErrorText>
        {result && (
          <div className="space-y-2 rounded-2xl bg-slate-50 p-4 text-sm text-slate-800">
            <Badge tone={TONE[result.standard]}>{result.standard}</Badge>
            <p>{result.meaning}</p>
            <p className="text-slate-600">{result.why}</p>
            {result.suggestion && <p className="text-slate-600">Fairer wording: {result.suggestion}</p>}
            <p className="text-[11px] text-slate-400">{result.disclaimer}</p>
          </div>
        )}
      </form>
    </div>
  )
}
