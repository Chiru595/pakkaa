import { api, post, streamEvents } from './api'

export type TermKey =
  | 'scope'
  | 'deliverables'
  | 'price_paise'
  | 'advance_pct'
  | 'timeline_days'
  | 'revisions'
  | 'payment_due_days'
  | 'ip_transfer'
  | 'cancellation'

export interface Term {
  key: TermKey
  label: string
  kind: 'text' | 'money' | 'percent' | 'days' | 'count'
  required: boolean
  value: string | number | null
  display: string
  via: string | null
  source_message_ids: string[]
}

export interface Signature {
  side: string
  name: string
  signed_at: string
}

export interface TermSheet {
  id: string
  version: number
  status: 'draft' | 'locked'
  category: string | null
  hash: string
  locked_hash: string | null
  locked_at: string | null
  terms: Term[]
  advance_paise: number | null
  advance_display: string | null
  signatures: Signature[]
}

export interface Proposal {
  id: string
  term_key: TermKey
  label: string
  value: string | number
  display: string
  quote: string
  confidence_pct: number
  source_message_id: string | null
}

export interface PaymentLink {
  id: string
  amount_paise: number
  amount_display: string
  status: 'pending' | 'paid' | 'failed'
  provider: string
  url: string | null
  created_at: string
  paid_at: string | null
}

export interface Deal {
  room_id: string
  my_side: 'provider' | 'client'
  term_sheet: TermSheet
  proposals: Proposal[]
  advance_link: PaymentLink | null
  disclaimer: string
  counterparty_notes: string[]
  outcome: DealOutcome | null
}

export interface DealOutcome {
  outcome: 'won' | 'lost'
  first_ask_display: string | null
  final_display: string | null
  note: string | null
  recorded_at: string
}

export interface Profile {
  side: string
  walk_away_paise: number | null
  target_paise: number | null
  notes: string | null
}

export interface FairPrice {
  category: string | null
  category_label: string | null
  categories: Record<string, string>
  offer_paise: number | null
  band: { p25: number; p50: number; p75: number } | null
  position: 'below_range' | 'lower_half' | 'upper_half' | 'above_range' | null
  verdict: string
  comparisons: { label: string; median_paise: number; count: number }[]
  source: string
  as_of: string
  disclaimer: string
}

export interface ReviewItem {
  level: 'missing' | 'risk' | 'info'
  term_key: TermKey | null
  message: string
}

export interface ReplyOption {
  tactic: string
  label: string
  text: string
  why: string
}

export interface PresendWarning {
  code: string
  level: 'warn' | 'info'
  message: string
}

export interface Explanation {
  meaning: string
  standard: 'standard' | 'negotiable' | 'unusual' | 'risky'
  why: string
  suggestion: string
  language: string
  disclaimer: string
}

export interface SignRequest {
  version: number
  hash: string
  phone_last4: string
  test_mode: boolean
  test_code: string | null
}

export interface PayPage {
  org_name: string
  room_title: string
  purpose: string
  amount_paise: number
  amount_display: string
  status: 'pending' | 'paid' | 'failed'
  provider: string
  simulated: boolean
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const DealApi = {
  get: (roomId: string) => api<Deal>(`/api/rooms/${roomId}/deal`),
  setTerm: (roomId: string, key: TermKey, value: string | number) =>
    api<Deal>(`/api/rooms/${roomId}/term-sheet`, json('PATCH', { key, value })),
  decide: (roomId: string, proposalId: string, decision: 'accept' | 'reject') =>
    api<Deal>(`/api/rooms/${roomId}/proposals/${proposalId}/${decision}`, post()),
  profile: (roomId: string) => api<Profile>(`/api/rooms/${roomId}/copilot/profile`),
  saveProfile: (roomId: string, body: Omit<Profile, 'side'>) =>
    api<Profile>(`/api/rooms/${roomId}/copilot/profile`, json('PUT', body)),
  fairPrice: (roomId: string, category?: string) =>
    api<FairPrice>(`/api/rooms/${roomId}/fair-price${category ? `?category=${encodeURIComponent(category)}` : ''}`),
  review: (roomId: string) => api<ReviewItem[]>(`/api/rooms/${roomId}/term-sheet/review`),
  check: (roomId: string, draft: string) => api<PresendWarning[]>(`/api/rooms/${roomId}/copilot/check`, post({ draft })),
  explain: (roomId: string, text: string, language: 'en' | 'hi' | 'hinglish') =>
    api<Explanation>(`/api/rooms/${roomId}/explain`, post({ text, language })),
  feedback: (roomId: string, suggestionId: string, chosen_index: number | null, outcome: 'sent' | 'edited' | 'ignored') =>
    api<void>(`/api/rooms/${roomId}/copilot/suggestions/${suggestionId}/feedback`, post({ chosen_index, outcome })),
  suggest: (
    roomId: string,
    triggerMessageId: string | null,
    onEvent: (event: string, data: unknown) => void,
    signal?: AbortSignal,
  ) => streamEvents(`/api/rooms/${roomId}/copilot/suggest`, { trigger_message_id: triggerMessageId }, onEvent, signal),
  requestSign: (roomId: string, version: number, hash: string) =>
    api<SignRequest>(`/api/rooms/${roomId}/term-sheet/sign/request`, post({ version, hash })),
  sign: (roomId: string, version: number, hash: string, code: string) =>
    api<Deal>(`/api/rooms/${roomId}/term-sheet/sign`, post({ version, hash, code })),
  advanceLink: (roomId: string) => api<PaymentLink>(`/api/rooms/${roomId}/advance-link`, post()),
  outcome: (roomId: string, outcome: 'won' | 'lost', note: string) =>
    api<Deal>(`/api/rooms/${roomId}/outcome`, post({ outcome, note: note.trim() || undefined })),
  payPage: (token: string) => api<PayPage>(`/api/pay/${token}`),
  simulatePay: (token: string, outcome: 'paid' | 'failed') => api<PayPage>(`/api/pay/${token}/simulate`, post({ outcome })),
}

export function rupees(paise: number | null | undefined): string {
  if (paise == null) return ''
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(
    paise / 100,
  )
}

/** Parses a rupee amount typed by a person ("45000", "45,000", "45k", "1.2 lakh") into paise. */
export function parseRupees(input: string): number | null {
  const m = input.trim().toLowerCase().replace(/[₹,\s]|rs\.?/g, '').match(/^(\d+(?:\.\d{1,2})?)(k|l|lakh|lac|cr)?$/)
  if (!m) return null
  const mult = { k: 1_000, l: 100_000, lakh: 100_000, lac: 100_000, cr: 10_000_000 }[m[2] ?? ''] ?? 1
  const paise = Math.round(parseFloat(m[1]) * mult * 100)
  return paise > 0 && paise <= 10_000_000_000 ? paise : null
}
