import { useRef, type KeyboardEvent } from 'react'
import { Moon, Sun } from 'lucide-react'
import { useTheme } from '../lib/theme'
import { useT, type Lang } from '../i18n'

const LANGS: { value: Lang; short: string; htmlLang: string }[] = [
  { value: 'en', short: 'EN', htmlLang: 'en' },
  { value: 'hi', short: 'हि', htmlLang: 'hi' },
]

/** Segmented "EN | हि" control. A proper radiogroup: one tab stop, arrow keys
 *  move the choice, each option labelled in its own language. */
export function LangToggle() {
  const { lang, setLang, t } = useT()
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const onKey = (e: KeyboardEvent) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return
    e.preventDefault()
    const i = LANGS.findIndex((l) => l.value === lang)
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? LANGS.length - 1 : (i + (e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 1) + LANGS.length) % LANGS.length
    setLang(LANGS[next].value)
    refs.current[next]?.focus()
  }
  return (
    <div
      role="radiogroup"
      aria-label={`${t('lang.aria')} / Language`}
      className="tip inline-flex h-7 items-center rounded border border-line bg-bg p-0.5"
      data-tip="Language · भाषा"
      data-testid="lang-toggle"
      onKeyDown={onKey}
    >
      {LANGS.map((l, i) => {
        const on = l.value === lang
        return (
          <button
            key={l.value}
            ref={(n) => {
              refs.current[i] = n
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            lang={l.htmlLang}
            aria-label={l.value === 'en' ? 'English' : 'हिन्दी (Hindi)'}
            onClick={() => setLang(l.value)}
            className={
              'h-[22px] min-w-[30px] px-2 rounded-sm font-semibold transition-colors ' +
              (l.value === 'hi' ? 'text-[13.5px] leading-[20px] ' : 'text-[11.5px] leading-[20px] tracking-[0.06em] ') +
              (on ? 'seg-on text-ink' : 'text-dim hover:text-ink')
            }
            data-testid={`lang-${l.value}`}
          >
            {l.short}
          </button>
        )
      })}
    </div>
  )
}

/** Sun/moon icon button: shows the theme you will switch TO. */
export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const { t } = useT()
  const label = theme === 'dark' ? t('theme.toLight') : t('theme.toDark')
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      data-tip={label}
      className="tip btn-ghost h-7 w-7 px-0 text-dim hover:text-ink"
      data-testid="theme-toggle"
      data-theme-current={theme}
    >
      <span className="relative block h-4 w-4" aria-hidden>
        <Sun
          size={16}
          className={'absolute inset-0 transition-all duration-200 ' + (theme === 'dark' ? 'opacity-100 rotate-0 scale-100' : 'opacity-0 -rotate-90 scale-50')}
        />
        <Moon
          size={16}
          className={'absolute inset-0 transition-all duration-200 ' + (theme === 'light' ? 'opacity-100 rotate-0 scale-100' : 'opacity-0 rotate-90 scale-50')}
        />
      </span>
    </button>
  )
}
