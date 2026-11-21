import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Badge, Button, ErrorText, Field, Logo, Screen, VerifiedBadge } from '../components/ui'
import { Api, ROLE_LABEL, type Room, type RoomType, TEAM_ROLES, type TeamMember } from '../lib/api'
import { type AskAnswer, PayApi } from '../lib/paylane'
import { useAuth } from '../lib/auth'
import { digitsOnlyPhone, formatPhone } from '../lib/phone'

function CreateOrg() {
  const { reload } = useAuth()
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await Api.createOrg(name.trim())
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the workspace')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 px-6 py-8">
      <h2 className="text-xl font-bold text-ink">Set up your workspace</h2>
      <p className="text-sm text-slate-600">Your business name. Clients and vendors will see it when you invite them.</p>
      <Field
        id="org"
        label="Business name"
        placeholder="Acme Studio"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        minLength={2}
        maxLength={120}
      />
      <ErrorText>{error}</ErrorText>
      <Button type="submit" className="w-full" busy={busy}>
        Create workspace
      </Button>
    </form>
  )
}

function NewRoom({ orgId, onCreated }: { orgId: string; onCreated: (room: Room) => void }) {
  const [type, setType] = useState<RoomType | null>(null)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!type) return
    setBusy(true)
    setError(null)
    try {
      onCreated(await Api.createRoom(orgId, type, title.trim()))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the room')
    } finally {
      setBusy(false)
    }
  }

  if (!type) {
    return (
      <div className="grid grid-cols-2 gap-3">
        <Button variant="secondary" onClick={() => setType('deal')}>
          + Deal Room
        </Button>
        <Button variant="secondary" onClick={() => setType('paylane')}>
          + Pay Lane
        </Button>
      </div>
    )
  }
  return (
    <form onSubmit={submit} className="space-y-3 rounded-2xl bg-slate-50 p-4">
      <Field
        id="title"
        label={type === 'deal' ? 'What is the deal about?' : 'Name this Pay Lane'}
        placeholder={type === 'deal' ? 'Logo and brand kit for Ravi Foods' : 'Acme payouts'}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        required
        minLength={2}
        maxLength={120}
        autoFocus
      />
      <ErrorText>{error}</ErrorText>
      <div className="flex gap-2">
        <Button type="submit" busy={busy} className="flex-1">
          Create
        </Button>
        <Button type="button" variant="ghost" onClick={() => setType(null)}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function TeamSection({ orgId }: { orgId: string }) {
  const [members, setMembers] = useState<TeamMember[] | null>(null)
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [role, setRole] = useState<'finance' | 'member'>('finance')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [added, setAdded] = useState<string | null>(null)

  const load = useCallback(() => {
    Api.members(orgId)
      .then(setMembers)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load the team'))
  }, [orgId])

  useEffect(() => {
    load()
  }, [load])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setAdded(null)
    try {
      const m = await Api.addTeammate(orgId, { name: name.trim(), phone: `+91${phone}`, role })
      setAdded(`${m.name} added as ${ROLE_LABEL[m.role]}. They sign in with +91 ${formatPhone(phone)} to join.`)
      setName('')
      setPhone('')
      setOpen(false)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add the teammate')
    } finally {
      setBusy(false)
    }
  }

  const team = members?.filter((m) => TEAM_ROLES.includes(m.role)) ?? []
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between px-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Team</h2>
        {!open && (
          <button type="button" className="text-sm font-semibold text-brand" onClick={() => setOpen(true)}>
            + Add teammate
          </button>
        )}
      </div>
      {members === null ? (
        <p className="px-2 text-sm text-slate-400">Loading…</p>
      ) : (
        <ul className="divide-y divide-slate-100 px-2">
          {team.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between py-2 text-sm">
              <span>
                <span className="font-medium text-ink">{m.name}</span>{' '}
                <span className="text-slate-500">
                  · {ROLE_LABEL[m.role]} · ••{m.phone_last4}
                </span>
              </span>
              {m.verified ? <VerifiedBadge /> : <Badge tone="amber">Not signed in yet</Badge>}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <form onSubmit={submit} className="space-y-3 rounded-2xl bg-slate-50 p-4">
          <Field id="mate-name" label="Name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
          <Field
            id="mate-phone"
            label="Mobile number (+91)"
            type="tel"
            inputMode="numeric"
            placeholder="98765 43210"
            value={formatPhone(phone)}
            onChange={(e) => setPhone(digitsOnlyPhone(e.target.value))}
            required
          />
          <label className="block space-y-1.5 text-sm font-medium text-ink">
            Role
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as 'finance' | 'member')}
              className="block w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-base"
            >
              <option value="finance">Finance (can approve payments)</option>
              <option value="member">Team member (can raise payments)</option>
            </select>
          </label>
          <div className="flex gap-2">
            <Button type="submit" busy={busy} className="flex-1" disabled={!/^[6-9]\d{9}$/.test(phone) || !name.trim()}>
              Add teammate
            </Button>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {added && <p className="px-2 text-sm text-emerald-700">{added}</p>}
      <ErrorText>{error}</ErrorText>
    </section>
  )
}

function AskBox({ orgId }: { orgId: string }) {
  const [q, setQ] = useState('')
  const [answer, setAnswer] = useState<AskAnswer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function ask(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setAnswer(await PayApi.ask(orgId, q.trim()))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not answer')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-2 rounded-2xl bg-slate-50 p-3">
      <form onSubmit={ask} className="flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          maxLength={300}
          minLength={3}
          placeholder="Ask Pakkaa: have we paid GSTIN 27AAPFU0939F1ZV?"
          aria-label="Ask Pakkaa"
          className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3 py-2 text-sm"
        />
        <Button type="submit" busy={busy} disabled={q.trim().length < 3} className="px-3 py-2">
          Ask
        </Button>
      </form>
      <ErrorText>{error}</ErrorText>
      {answer && (
        <div className="space-y-1 text-sm">
          <p className="text-ink">{answer.answer}</p>
          <details className="text-xs text-slate-500">
            <summary>Sources ({answer.facts.length})</summary>
            <ul className="mt-1 space-y-0.5">
              {answer.facts.map((f) => (
                <li key={f.id}>
                  [{f.id}] {f.text}
                </li>
              ))}
            </ul>
          </details>
          <p className="text-[11px] text-slate-400">{answer.disclaimer}</p>
        </div>
      )}
    </section>
  )
}

function RoomRow({ room }: { room: Room }) {
  return (
    <Link to={`/rooms/${room.id}`} className="flex items-center gap-3 rounded-2xl px-3 py-3 hover:bg-slate-50">
      <span
        className={`grid size-10 place-items-center rounded-xl text-lg ${room.type === 'deal' ? 'bg-emerald-100' : 'bg-sky-100'}`}
        aria-hidden
      >
        {room.type === 'deal' ? '🤝' : '💸'}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-ink">{room.title}</span>
        <span className="text-xs text-slate-500">{room.type === 'deal' ? 'Deal Room · money in' : 'Pay Lane · money out'}</span>
      </span>
    </Link>
  )
}

const ORG_KEY = 'pakkaa.org'

function readStoredOrg(): string | null {
  try {
    return localStorage.getItem(ORG_KEY)
  } catch {
    return null
  }
}

export default function Home() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [orgId, setOrgId] = useState<string | null>(readStoredOrg)
  const org =
    user?.orgs.find((o) => o.org_id === orgId) ??
    user?.orgs.find((o) => TEAM_ROLES.includes(o.role)) ??
    user?.orgs[0]

  function switchOrg(id: string) {
    setOrgId(id)
    setRooms(null)
    try {
      localStorage.setItem(ORG_KEY, id)
    } catch {
      /* storage unavailable */
    }
  }
  const [rooms, setRooms] = useState<Room[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!org) return
    try {
      setRooms(await Api.rooms(org.org_id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load rooms')
    }
  }, [org])

  useEffect(() => {
    void load()
  }, [load])

  if (!user) return null
  const isTeam = org ? TEAM_ROLES.includes(org.role) : false
  const deals = rooms?.filter((r) => r.type === 'deal') ?? []
  const lanes = rooms?.filter((r) => r.type === 'paylane') ?? []

  return (
    <Screen>
      <header className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
        <Logo />
        <button onClick={() => void signOut()} className="text-sm text-slate-500 hover:text-ink">
          Sign out
        </button>
      </header>
      {!org ? (
        <CreateOrg />
      ) : (
        <div className="flex-1 space-y-6 px-4 py-5">
          <div className="px-2">
            <p className="text-sm text-slate-500">Hi {user.name}</p>
            <h1 className="flex items-center gap-2 text-2xl font-bold text-ink">
              <span className="truncate">{org.org_name}</span> <Badge>{ROLE_LABEL[org.role]}</Badge>
            </h1>
            {user.orgs.length > 1 && (
              <label className="mt-2 flex items-center gap-2 text-sm text-slate-600">
                Workspace
                <select
                  value={org.org_id}
                  onChange={(e) => switchOrg(e.target.value)}
                  className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm"
                >
                  {user.orgs.map((o) => (
                    <option key={o.org_id} value={o.org_id}>
                      {o.org_name} ({ROLE_LABEL[o.role]})
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {isTeam && <NewRoom orgId={org.org_id} onCreated={(r) => navigate(`/rooms/${r.id}`)} />}
          {isTeam && <AskBox orgId={org.org_id} />}
          {org.role === 'owner' && <TeamSection orgId={org.org_id} />}
          <ErrorText>{error}</ErrorText>
          <section>
            <h2 className="px-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Deal Rooms</h2>
            {rooms === null ? (
              <p className="px-2 py-3 text-sm text-slate-400">Loading…</p>
            ) : deals.length ? (
              deals.map((r) => <RoomRow key={r.id} room={r} />)
            ) : (
              <p className="px-2 py-3 text-sm text-slate-500">No deals yet. Create one and invite your client.</p>
            )}
          </section>
          {(isTeam || lanes.length > 0) && (
            <section>
              <h2 className="px-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Pay Lane</h2>
              {lanes.length ? (
                lanes.map((r) => <RoomRow key={r.id} room={r} />)
              ) : (
                <p className="px-2 py-3 text-sm text-slate-500">
                  Your team's only channel for outgoing payments. Create one to start.
                </p>
              )}
            </section>
          )}
        </div>
      )}
    </Screen>
  )
}
