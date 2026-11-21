import { useEffect, useState } from 'react'

import { getAccessToken } from '../../lib/api'
import { type AuditCheck, type MemoryEntry, PayApi, type Policy } from '../../lib/paylane'
import { Badge, Button, ErrorText } from '../ui'

const KIND_LABEL: Record<MemoryEntry['kind'], string> = {
  playbook: 'Deal',
  correction: 'Scribe',
  counterparty: 'Client',
  preference: 'Preference',
  payee: 'Payee',
  risk_override: 'Override',
}

function fmt(value: number, key: string): string {
  if (key === 'no_baseline_large_rupees') return `₹${value.toLocaleString('en-IN')}`
  if (key === 'baseline_multiplier') return `${value}×`
  if (key === 'bank_change_window_days') return `${value} days`
  return String(value)
}

function AuditBox({ orgId }: { orgId: string }) {
  const [check, setCheck] = useState<AuditCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function verify() {
    setBusy(true)
    setError(null)
    try {
      setCheck(await PayApi.verifyAudit(orgId))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not verify')
    } finally {
      setBusy(false)
    }
  }

  async function download(format: 'jsonl' | 'csv') {
    setError(null)
    try {
      const res = await fetch(`/api/orgs/${orgId}/audit/export?format=${format}`, {
        headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
      })
      if (!res.ok) throw new Error(`Export failed (${res.status})`)
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = `pakkaa-audit.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed')
    }
  }

  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Audit log</h3>
      <p className="text-sm text-slate-600">Every change is hash-chained. Exports verify on their own with scripts/verify_audit_export.py.</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" busy={busy} onClick={() => void verify()}>Verify chain</Button>
        <Button variant="ghost" onClick={() => void download('jsonl')}>Download JSONL</Button>
        <Button variant="ghost" onClick={() => void download('csv')}>Download CSV</Button>
      </div>
      {check && (
        <p className={`rounded-xl p-2 text-sm ${check.ok ? 'bg-emerald-50 text-emerald-900' : 'bg-red-50 text-red-800'}`}>
          {check.ok
            ? `Intact: ${check.events} events, head ${check.head_hash?.slice(0, 12)}…`
            : `Broken at event ${check.broken_at_seq}: ${check.reason}`}
        </p>
      )}
      <ErrorText>{error}</ErrorText>
    </section>
  )
}

export default function LearningPanel({ orgId, canAudit }: { orgId: string; canAudit: boolean }) {
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [memory, setMemory] = useState<MemoryEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([PayApi.policy(orgId), PayApi.memory(orgId)])
      .then(([p, m]) => {
        setPolicy(p)
        setMemory(m)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load'))
  }, [orgId])

  return (
    <div className="space-y-5">
      <ErrorText>{error}</ErrorText>
      <section className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Risk rules</h3>
        {policy === null ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          <ul className="divide-y divide-slate-100 text-sm">
            {policy.thresholds.map((t) => (
              <li key={t.key} className="flex items-center justify-between py-2">
                <span className="text-slate-700">{t.label}</span>
                <span className="flex items-center gap-2">
                  {t.value !== t.default && <Badge tone="amber">learned</Badge>}
                  <span className="font-medium text-ink">{fmt(t.value, t.key)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {policy && policy.changes.length > 0 && (
          <ul className="space-y-1.5 rounded-2xl bg-slate-50 p-3 text-xs text-slate-600">
            {policy.changes.slice(0, 5).map((c, i) => (
              <li key={i}>
                <strong className={c.direction === 'tighten' ? 'text-emerald-700' : 'text-amber-700'}>
                  {c.direction === 'tighten' ? 'Tightened' : 'Loosened'}
                </strong>{' '}
                {policy.thresholds.find((t) => t.key === c.key)?.label.toLowerCase()} {fmt(c.from, c.key)} → {fmt(c.to, c.key)}
                {' · '}
                {c.source === 'learning' ? 'learned automatically' : `by ${c.by ?? 'a teammate'}`}: {c.reason}
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] text-slate-400">
          Learning only makes checks stricter. Loosening needs the owner and a written reason. {policy?.source}
        </p>
      </section>
      <section className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">What Pakkaa learned</h3>
        {memory === null ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : memory.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing yet. Outcomes, late payments, corrections and payouts show up here.</p>
        ) : (
          <ul className="space-y-2">
            {memory.map((m) => (
              <li key={m.id} className="rounded-2xl border border-slate-100 p-3 text-sm">
                <div className="mb-1 flex items-center gap-2">
                  <Badge tone={m.kind === 'counterparty' ? 'amber' : m.kind === 'risk_override' ? 'red' : 'slate'}>
                    {KIND_LABEL[m.kind]}
                  </Badge>
                  <time className="text-[11px] text-slate-400" dateTime={m.created_at}>
                    {new Date(m.created_at).toLocaleDateString([], { day: 'numeric', month: 'short' })}
                  </time>
                  {m.visibility === 'private' && <span className="text-[11px] text-slate-400">private</span>}
                </div>
                <p className="text-slate-700">{m.text}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
      {canAudit && <AuditBox orgId={orgId} />}
    </div>
  )
}
