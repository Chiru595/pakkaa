export interface OrgRef {
  org_id: string
  org_name: string
  role: Role
}

export type Role = 'owner' | 'finance' | 'member' | 'client_guest' | 'vendor_guest'
export type RoomType = 'deal' | 'paylane'

export interface User {
  id: string
  name: string
  phone_last4: string
  chat_id: string
  orgs: OrgRef[]
}

export interface RoomMember {
  user_id: string
  name: string
  side: 'provider' | 'client' | 'team' | 'vendor'
  role: Role | null
  phone_last4: string
  verified: boolean
}

export interface Room {
  id: string
  org_id: string
  type: RoomType
  title: string
  channel_id: string
  created_at: string
  my_side: RoomMember['side'] | null
  members: RoomMember[]
}

export interface OtpRequested {
  phone: string
  is_new_user: boolean
  needs_consent: boolean
  test_mode: boolean
  test_code: string | null
}

export interface InvitePreview {
  org_name: string
  room_title: string
  room_type: RoomType
  role: Role
  expires_at: string
}

export interface ChatMessageOut {
  id: string
  sender_chat_id: string
  kind: 'text' | 'card'
  text: string
  card: Record<string, unknown> | null
  sent_time: number
}

export interface ChatToken {
  token: string
  app_key: string
  area: string
  chat_user_id: string
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let accessToken: string | null = null
let onSignedOut: (() => void) | null = null

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function setSignedOutHandler(handler: () => void) {
  onSignedOut = handler
}

async function parseError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') message = body.detail
    else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, message)
}

let pendingRefresh: Promise<{ access_token: string; user: User } | null> | null = null

export function refreshSession(): Promise<{ access_token: string; user: User } | null> {
  if (!document.cookie.split('; ').includes('pk_session=1')) return Promise.resolve(null)
  pendingRefresh ??= (async () => {
    const res = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'same-origin' })
    if (!res.ok) return null
    const body = (await res.json()) as { access_token: string; user: User }
    accessToken = body.access_token
    return body
  })().finally(() => (pendingRefresh = null))
  return pendingRefresh
}

async function tryRefresh(): Promise<boolean> {
  return (await refreshSession()) !== null
}

export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const res = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (res.status === 401 && retry && accessToken) {
    if (await tryRefresh()) return api<T>(path, init, false)
    accessToken = null
    onSignedOut?.()
  }
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** POSTs and reads a server-sent event stream, passing each event to `onEvent` as it arrives. */
export async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal,
  retry = true,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' })
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const res = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'same-origin', signal })
  if (res.status === 401 && retry && (await tryRefresh())) return streamEvents(path, body, onEvent, signal, false)
  if (!res.ok || !res.body) throw await parseError(res)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      try {
        onEvent(event, data ? JSON.parse(data) : null)
      } catch {
        /* ignore malformed event */
      }
    }
  }
}

export const post = (body?: unknown): RequestInit => ({ method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const Api = {
  requestOtp: (phone: string) => api<OtpRequested>('/api/auth/otp/request', post({ phone })),
  verifyOtp: (body: { phone: string; code: string; name?: string; consent: boolean }) =>
    api<{ access_token: string; user: User }>('/api/auth/otp/verify', post(body)),
  logout: () => api<void>('/api/auth/logout', post()),
  me: () => api<User>('/api/me'),
  createOrg: (name: string) => api<{ id: string; name: string; role: Role }>('/api/orgs', post({ name })),
  rooms: (orgId: string) => api<Room[]>(`/api/orgs/${orgId}/rooms`),
  createRoom: (orgId: string, type: RoomType, title: string) =>
    api<Room>(`/api/orgs/${orgId}/rooms`, post({ type, title })),
  room: (roomId: string) => api<Room>(`/api/rooms/${roomId}`),
  joinRoom: (roomId: string) => api<Room>(`/api/rooms/${roomId}/join`, post()),
  createInvite: (roomId: string, role: Role) =>
    api<{ url: string; expires_at: string }>(`/api/rooms/${roomId}/invites`, post({ role })),
  previewInvite: (token: string) => api<InvitePreview>(`/api/invites/${token}`),
  acceptInvite: (token: string) => api<Room>(`/api/invites/${token}/accept`, post()),
  chatToken: () => api<ChatToken>('/api/chat/token', post()),
  messages: (roomId: string, before?: number) =>
    api<ChatMessageOut[]>(`/api/rooms/${roomId}/messages${before ? `?before=${before}` : ''}`),
  sendMessage: (roomId: string, text: string) => api<ChatMessageOut>(`/api/rooms/${roomId}/messages`, post({ text })),
}

export const TEAM_ROLES: Role[] = ['owner', 'finance', 'member']

export const ROLE_LABEL: Record<Role, string> = {
  owner: 'Owner',
  finance: 'Finance',
  member: 'Team',
  client_guest: 'Client',
  vendor_guest: 'Vendor',
}
