import type { ReactNode } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { useT } from '../i18n'

export function Panel({ title, meta, actions, children, className = '', bodyClass = '' }: {
  title: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClass?: string
}) {
  return (
    <section className={'panel flex flex-col min-h-0 ' + className}>
      <header className="panel-head shrink-0">
        <h2 className="panel-title">{title}</h2>
        {meta && <div className="text-xs text-dim truncate">{meta}</div>}
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      </header>
      <div className={'min-h-0 flex-1 ' + bodyClass}>{children}</div>
    </section>
  )
}

export function Segmented<T extends string>({ value, options, onChange, label, size = 'md' }: {
  value: T
  options: { value: T; label: ReactNode; title?: string }[]
  onChange: (v: T) => void
  label: string
  size?: 'sm' | 'md'
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded border border-line bg-bg p-0.5">
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            role="radio"
            aria-checked={on}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={
              'rounded-sm font-medium transition-colors whitespace-nowrap ' +
              (size === 'sm' ? 'h-6 px-2 text-xs ' : 'h-7 px-3 text-xs ') +
              (on ? 'seg-on text-ink' : 'text-dim hover:text-ink')
            }
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

export function Switch({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: string }) {
  return (
    <label className="inline-flex items-center gap-2 cursor-pointer select-none text-xs text-ink2" title={hint}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={'relative h-4 w-7 rounded-full border transition-colors ' + (checked ? 'bg-accent border-accent' : 'bg-s3 border-line')}
      >
        <span className={'knob absolute top-[1px] h-3 w-3 rounded-full transition-all ' + (checked ? 'left-[13px]' : 'left-[1px]')} />
      </button>
      {label}
    </label>
  )
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="h-full min-h-[160px] flex flex-col items-center justify-center text-center px-8 py-8 gap-2">
      {icon && <div className="text-faint mb-1">{icon}</div>}
      <div className="text-sm font-semibold text-ink2">{title}</div>
      {children && <div className="text-xs text-dim max-w-md leading-5">{children}</div>}
    </div>
  )
}

export function Loading({ label }: { label?: string }) {
  const { t } = useT()
  return (
    <div className="h-full min-h-[120px] flex items-center justify-center gap-2 text-dim text-sm">
      <Loader2 size={16} className="animate-spin" /> {label ?? t('ui.loading')}…
    </div>
  )
}

export function ErrorNote({ error, what }: { error: unknown; what: string }) {
  const { t } = useT()
  return (
    <div className="m-4 flex items-start gap-2 rounded border border-high/40 bg-high/10 px-3 py-2 text-sm text-ink2">
      <AlertTriangle size={16} className="text-high mt-0.5 shrink-0" />
      <div>
        <div className="font-semibold text-ink">{t('ui.couldNotLoad', { what })}</div>
        <div className="text-xs text-dim mt-0.5">{error instanceof Error ? error.message : String(error)}</div>
      </div>
    </div>
  )
}

export function Stat({ label, value, unit, tone }: { label: string; value: ReactNode; unit?: string; tone?: 'high' | 'ok' | 'dim' }) {
  return (
    <div className="kv">
      <span className="label">{label}</span>
      <span className={'num text-lg font-semibold ' + (tone === 'high' ? 'text-high' : tone === 'ok' ? 'text-ok' : 'text-ink')}>
        {value}
        {unit && <span className="text-xs font-normal text-dim ml-1">{unit}</span>}
      </span>
    </div>
  )
}
