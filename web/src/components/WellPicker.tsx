import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Search } from 'lucide-react'
import type { Well } from '../lib/types'
import { fmtNum } from '../lib/format'
import { useT } from '../i18n'

/** Searchable combobox over wells (id, name, field). Keyboard: arrows, Enter, Esc. */
export function WellPicker({ wells, value, onChange, label, wide = false }: {
  wells: Well[]
  value: string | null
  onChange: (id: string) => void
  label?: string
  wide?: boolean
}) {
  const { t } = useT()
  label = label ?? t('picker.activeWell')
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

  const matches = useMemo(() => {
    const s = q.trim().toLowerCase()
    const list = [...wells].sort((a, b) => a.well_id.localeCompare(b.well_id))
    return s ? list.filter((w) => `${w.well_id} ${w.name} ${w.field}`.toLowerCase().includes(s)) : list
  }, [wells, q])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  useEffect(() => {
    if (open) {
      setQ('')
      setHi(0)
      setTimeout(() => input.current?.focus(), 0)
    }
  }, [open])

  const current = wells.find((w) => w.well_id === value)
  const pick = (id: string) => {
    onChange(id)
    setOpen(false)
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        className={'h-8 flex items-center gap-2 rounded border border-line bg-bg px-3 text-left hover:border-ink2/40 ' + (wide ? 'min-w-[240px]' : 'min-w-[200px]')}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        data-testid="well-picker"
      >
        <span className="label !text-[10.5px]">{label}</span>
        <span className="font-mono text-sm text-ink">{current?.well_id ?? t('picker.select')}</span>
        {current && <span className="text-xs text-dim truncate">{current.field}</span>}
        <ChevronDown size={14} className="ml-auto text-dim" />
      </button>
      {open && (
        <div className="absolute z-[1100] mt-1 w-[320px] rounded border border-line bg-s2 pop-shadow">
          <div className="flex items-center gap-2 px-3 h-9 border-b border-rule">
            <Search size={14} className="text-dim" />
            <input
              ref={input}
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setHi(0)
              }}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, matches.length - 1)) }
                if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
                if (e.key === 'Enter' && matches[hi]) pick(matches[hi].well_id)
                if (e.key === 'Escape') setOpen(false)
              }}
              placeholder={t('picker.search')}
              className="flex-1 bg-transparent text-sm text-ink placeholder:text-faint outline-none"
              aria-label={t('picker.searchAria')}
              data-testid="well-search"
            />
          </div>
          <ul role="listbox" className="max-h-[320px] overflow-auto py-1">
            {matches.length === 0 && <li className="px-3 py-2 text-sm text-dim">{t('picker.noMatch', { q })}</li>}
            {matches.map((w, i) => (
              <li
                key={w.well_id}
                role="option"
                aria-selected={w.well_id === value}
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => { e.preventDefault(); pick(w.well_id) }}
                className={'flex items-center gap-3 px-3 h-8 cursor-pointer text-sm ' + (i === hi ? 'bg-s3' : '') + (w.well_id === value ? ' text-accent' : ' text-ink2')}
              >
                <span className="font-mono w-[72px]">{w.well_id}</span>
                <span className="text-xs text-dim w-[88px] truncate">{w.field}</span>
                <span className="text-xs text-faint">{w.trajectory_type}</span>
                <span className="num text-xs text-faint ml-auto">TD {fmtNum(w.td_md_m)} m</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
