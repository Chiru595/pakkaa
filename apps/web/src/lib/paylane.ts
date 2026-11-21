import { api, post } from './api'

export interface PayeeRef {
  id: string
  name: string
  masked: string
  gstin: string | null
  details_changed_at: string | null
}

export interface Payee extends PayeeRef {
  method: 'upi' | 'bank'
  ifsc: string | null
  created_at: string
}

export interface PersonRef {
  id: string
  name: string
  role: string | null
  phone_last4: string
  verified: boolean
  name_collisions: { user_id: string; name: string; role: string }[]
}

export interface Card {
  id: string
  number: number
  room_id: string
  state: 'unverified' | 'pending_approval' | 'approved' | 'paid' | 'rejected' | 'dismissed'
  version: number
  amount_paise: number
  amount_display: string
  reason: string
  source: string
  payee: PayeeRef | null
  payee_name_claimed: string | null
  deal: { room_id: string; title: string; price_display: string | null; locked: boolean } | null
  requester: PersonRef | null
  risk_score: number
  risk_level: 'low' | 'medium' | 'high'
  signals: { rule_id: string; weight: number; message: string; evidence: Record<string, unknown> }[]
  required_approvals: number
  owner_approval_required: boolean
  approvals: { name: string; role: string; at: string }[]
  verify_call_required: boolean
  verify_call: { outcome: string | null; summary: string | null; ended_at: string | null } | null
  payout: { provider: string; provider_ref: string; status: string; at: string; simulated: boolean } | null
  viewer: {
    can_approve: boolean
    can_edit: boolean
    can_payout: boolean
    can_verify: boolean
    can_reject: boolean
    blocked_reason: string | null
  }
  created_at: string
}

export interface ParsedDraft {
  payee_name: string
  amount_paise: number | null
  amount_display: string | null
  reason: string
  payees: PayeeRef[]
  deals: { room_id: string; title: string; price_display: string }[]
  used_ai: boolean
}

export interface Control {
  frozen: boolean
  frozen_by: string | null
  frozen_at: string | null
  reason: string | null
  unfreeze_votes: number
  voted: boolean
}

export interface Brief {
  lines: string[]
  evidence: { label: string; ref: string }[]
  ai_worded: boolean
  disclaimer: string
}

export interface VerifyStart {
  call_id: string
  callee_chat_id: string
  callee_name: string
  callee_phone_last4: string
  questions: { q: string; expected: string }[]
  warning: string
}

export interface AskAnswer {
  answer: string
  facts: { id: string; text: string; link: string | null }[]
  ai_worded: boolean
  disclaimer: string
}

export interface NewPayee {
  name: string
  method: 'upi' | 'bank'
  vpa?: string
  account_number?: string
  ifsc?: string
  gstin?: string
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const PayApi = {
  cards: (roomId: string) => api<Card[]>(`/api/rooms/${roomId}/cards`),
  card: (cardId: string) => api<Card>(`/api/cards/${cardId}`),
  parse: (roomId: string, text: string) => api<ParsedDraft>(`/api/rooms/${roomId}/cards/parse`, post({ text })),
  create: (roomId: string, body: { payee_id: string; amount_paise: number; reason: string; deal_room_id?: string | null }) =>
    api<Card>(`/api/rooms/${roomId}/cards`, post(body)),
  edit: (cardId: string, body: { amount_paise?: number; payee_id?: string; reason?: string }) =>
    api<Card>(`/api/cards/${cardId}`, json('PATCH', body)),
  approve: (cardId: string, version: number) => api<Card>(`/api/cards/${cardId}/approve`, post({ version })),
  reject: (cardId: string, note = '') => api<Card>(`/api/cards/${cardId}/reject`, post({ note })),
  brief: (cardId: string) => api<Brief>(`/api/cards/${cardId}/brief`),
  startVerify: (cardId: string) => api<VerifyStart>(`/api/cards/${cardId}/verify-call`, post()),
  completeVerify: (cardId: string, callId: string, outcome: 'confirmed' | 'suspicious', notes: string, duration_s: number) =>
    api<Card>(`/api/cards/${cardId}/verify-call/${callId}/complete`, post({ outcome, notes, duration_s })),
  payout: (cardId: string, idempotency_key: string) => api<Card>(`/api/cards/${cardId}/payout`, post({ idempotency_key })),
  control: (orgId: string) => api<Control>(`/api/orgs/${orgId}/control`),
  freeze: (orgId: string, reason: string) => api<Control>(`/api/orgs/${orgId}/freeze`, post({ reason })),
  unfreeze: (orgId: string) => api<Control>(`/api/orgs/${orgId}/unfreeze`, post()),
  payees: (orgId: string) => api<Payee[]>(`/api/orgs/${orgId}/payees`),
  addPayee: (orgId: string, body: NewPayee) => api<Payee>(`/api/orgs/${orgId}/payees`, post(body)),
  changeDetails: (orgId: string, payeeId: string, body: Omit<NewPayee, 'name' | 'gstin'>) =>
    api<Payee>(`/api/orgs/${orgId}/payees/${payeeId}/details`, json('PUT', body)),
  ask: (orgId: string, question: string) => api<AskAnswer>(`/api/orgs/${orgId}/ask`, post({ question })),
}

export function newIdempotencyKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return 'po_' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

export const STATE_LABEL: Record<Card['state'], string> = {
  unverified: 'Unverified (from chat)',
  pending_approval: 'Waiting for approval',
  approved: 'Approved',
  paid: 'Paid',
  rejected: 'Rejected',
  dismissed: 'Dismissed',
}
