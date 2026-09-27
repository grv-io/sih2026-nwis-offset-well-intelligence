import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { en, type MsgKey } from './en'
import { hi } from './hi'

// Lightweight i18n: two flat dictionaries, {placeholder} interpolation, no deps.
// The choice lives in localStorage (nwis.lang) and on <html lang>, which the
// CSS uses to drop letter-spacing / loosen line-height for Devanagari.

export type Lang = 'en' | 'hi'
export type { MsgKey }
export const LANG_KEY = 'nwis.lang'
export const DICTS: Record<Lang, Record<MsgKey, string>> = { en, hi }

export type Vars = Record<string, string | number>
export type TFn = (key: MsgKey, vars?: Vars) => string

export function hasKey(k: string): k is MsgKey {
  return Object.prototype.hasOwnProperty.call(en, k)
}

export function makeT(lang: Lang): TFn {
  const dict = DICTS[lang]
  return (key, vars) => {
    const s = dict[key] ?? en[key] ?? key
    return vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : s
  }
}

export function readStoredLang(): Lang {
  try {
    return localStorage.getItem(LANG_KEY) === 'hi' ? 'hi' : 'en'
  } catch {
    return 'en'
  }
}

interface I18nCtx {
  lang: Lang
  t: TFn
  setLang: (l: Lang) => void
}

const T_EN = makeT('en')
const T_HI = makeT('hi')
const Ctx = createContext<I18nCtx>({ lang: 'en', t: T_EN, setLang: () => {} })

export function I18nProvider({ children, initial }: { children: ReactNode; initial?: Lang }) {
  const [lang, set] = useState<Lang>(() => initial ?? readStoredLang())
  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])
  const setLang = useCallback((l: Lang) => {
    try {
      localStorage.setItem(LANG_KEY, l)
    } catch {
      /* private mode: choice won't persist */
    }
    document.documentElement.lang = l
    set(l)
  }, [])
  const value = useMemo(() => ({ lang, t: lang === 'hi' ? T_HI : T_EN, setLang }), [lang, setLang])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

/** `const { t, lang, setLang } = useT()`; outside a provider it is English. */
export const useT = () => useContext(Ctx)

/** Bilingual label for a backend `event_type` enum value. Unknown values fall
 *  back to the enum with spaces. `bilingual` appends the English label in
 *  brackets in Hindi ("स्टक पाइप (Stuck pipe)") for headline positions. */
export function labelForEventType(
  t: TFn,
  lang: Lang,
  type: string | null | undefined,
  opts: { bilingual?: boolean } = {},
): string {
  if (!type) return '—'
  const key = `event.${type}`
  if (!hasKey(key)) return type.replace(/_/g, ' ')
  const label = t(key)
  return opts.bilingual && lang === 'hi' ? `${label} (${en[key]})` : label
}

/** Hook form: `const ev = useEventLabel(); ev('stuck_pipe')`. */
export function useEventLabel() {
  const { t, lang } = useT()
  return useCallback(
    (type: string | null | undefined, opts?: { bilingual?: boolean }) => labelForEventType(t, lang, type, opts),
    [t, lang],
  )
}

/** Look up a dynamic key (e.g. `rule.${rule}`), falling back to `fallback`. */
export function tMaybe(t: TFn, key: string, fallback: string, vars?: Vars): string {
  return hasKey(key) ? t(key, vars) : fallback
}
