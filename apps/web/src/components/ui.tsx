import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand text-white hover:bg-brand-dark disabled:bg-brand/50',
  secondary: 'bg-white text-ink ring-1 ring-slate-300 hover:bg-slate-50',
  ghost: 'text-ink hover:bg-slate-100',
  danger: 'bg-red-600 text-white hover:bg-red-700',
}

export function Button({
  variant = 'primary',
  className = '',
  busy = false,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
    >
      {busy && <Spinner />}
      {children}
    </button>
  )
}

export function Field({
  label,
  hint,
  id,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode; id: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-ink">
        {label}
      </label>
      <input
        id={id}
        {...props}
        className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-base text-ink placeholder:text-slate-400 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
      />
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent"
    />
  )
}

export function Badge({ tone = 'slate', children }: { tone?: 'slate' | 'green' | 'amber' | 'red' | 'blue'; children: ReactNode }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700',
    green: 'bg-emerald-100 text-emerald-800',
    amber: 'bg-amber-100 text-amber-800',
    red: 'bg-red-100 text-red-800',
    blue: 'bg-sky-100 text-sky-800',
  }
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>
}

export function VerifiedBadge() {
  return (
    <Badge tone="green">
      <svg viewBox="0 0 20 20" fill="currentColor" className="size-3.5" aria-hidden>
        <path
          fillRule="evenodd"
          d="M16.7 5.3a1 1 0 0 1 0 1.4l-7.5 7.5a1 1 0 0 1-1.4 0L3.3 9.7a1 1 0 1 1 1.4-1.4l3.8 3.8 6.8-6.8a1 1 0 0 1 1.4 0Z"
          clipRule="evenodd"
        />
      </svg>
      Verified
    </Badge>
  )
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
      {children}
    </p>
  )
}

export function Logo({ size = 'md' }: { size?: 'md' | 'lg' }) {
  return (
    <span className={`font-extrabold tracking-tight text-ink ${size === 'lg' ? 'text-4xl' : 'text-xl'}`}>
      pakkaa<span className="text-brand">.</span>
    </span>
  )
}

export function Screen({ children }: { children: ReactNode }) {
  return <main className="mx-auto flex min-h-full w-full max-w-md flex-col bg-white sm:my-4 sm:min-h-[calc(100%-2rem)] sm:rounded-3xl sm:shadow-xl">{children}</main>
}
