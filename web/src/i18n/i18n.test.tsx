import { act, fireEvent, render, screen } from '@testing-library/react'
import { en } from './en'
import { hi } from './hi'
import { EVENT_LABEL } from '../lib/hazards'
import { I18nProvider, LANG_KEY, labelForEventType, makeT, useT } from '.'
import { LangToggle } from '../components/Preferences'

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
const DEVANAGARI = /[ऀ-ॿ]/

describe('dictionaries', () => {
  it('en and hi have identical key sets (a missing Hindi string fails CI)', () => {
    const ek = Object.keys(en).sort()
    const hk = Object.keys(hi).sort()
    expect(hk.filter((k) => !(k in en))).toEqual([]) // no stray Hindi keys
    expect(ek.filter((k) => !(k in hi))).toEqual([]) // no missing Hindi keys
    expect(hk).toEqual(ek)
  })

  it('no empty strings and identical {placeholders} per key', () => {
    for (const k of Object.keys(en) as (keyof typeof en)[]) {
      expect(en[k].trim(), k).not.toBe('')
      expect(hi[k].trim(), k).not.toBe('')
      expect(placeholders(hi[k]), k).toEqual(placeholders(en[k]))
    }
  })

  it('Hindi is actually Devanagari, apart from pure technical tokens', () => {
    // Keys whose value is legitimately the same token in both languages.
    const same = new Set(['link.api', 'link.llm', 'col.md', 'col.npt', 'ui.na', 'rig.rop', 'rig.spp', 'corr.axisTvd',
      'dim.formation_overlap.short', 'dim.target_depth.short', 'dim.valueTitle', 'risk.reasonNpt'])
    const latinOnly = (Object.keys(hi) as (keyof typeof hi)[]).filter((k) => !same.has(k) && !DEVANAGARI.test(hi[k]))
    expect(latinOnly).toEqual([])
  })

  it('every backend event_type has a label in both languages', () => {
    const types = ['mud_loss', 'kick', 'stuck_pipe', 'overpressure', 'torque_spike', 'cementing_issue',
      'fishing_operation', 'wellbore_instability', 'gas_show', 'twist_off', 'lost_bha', 'npt_other']
    expect(Object.keys(EVENT_LABEL).sort()).toEqual([...types].sort())
  })
})

describe('makeT / labelForEventType', () => {
  it('interpolates and falls back', () => {
    expect(makeT('en')('map.meta', { wells: 30, cands: 6, km: '5.0' })).toBe('30 wells · 6 within 5.0 km')
    expect(makeT('hi')('map.meta', { wells: 30, cands: 6, km: '5.0' })).toBe('30 वेल · 5.0 km के भीतर 6')
    expect(makeT('en')('ui.more')).toBe('+{n} more') // missing vars stay visible, never "undefined"
  })

  it('maps event_type enums to bilingual labels', () => {
    const tEn = makeT('en')
    const tHi = makeT('hi')
    expect(labelForEventType(tEn, 'en', 'stuck_pipe')).toBe('Stuck pipe')
    expect(labelForEventType(tHi, 'hi', 'stuck_pipe')).toBe('स्टक पाइप')
    expect(labelForEventType(tHi, 'hi', 'stuck_pipe', { bilingual: true })).toBe('स्टक पाइप (Stuck pipe)')
    expect(labelForEventType(tEn, 'en', 'stuck_pipe', { bilingual: true })).toBe('Stuck pipe')
    expect(labelForEventType(tHi, 'hi', 'mud_loss')).toBe('मड लॉस')
    expect(labelForEventType(tHi, 'hi', 'brand_new_type')).toBe('brand new type')
    expect(labelForEventType(tHi, 'hi', null)).toBe('—')
  })
})

function Probe() {
  const { t, lang } = useT()
  return (
    <div>
      <span data-testid="probe">{t('rig.alerts')}</span>
      <span data-testid="lang">{lang}</span>
    </div>
  )
}

describe('useT + LangToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.lang = 'en'
  })

  it('defaults to English outside a provider', () => {
    render(<Probe />)
    expect(screen.getByTestId('probe')).toHaveTextContent('Alerts')
  })

  it('switches to Hindi, updates <html lang> and persists nwis.lang', () => {
    render(
      <I18nProvider>
        <LangToggle />
        <Probe />
      </I18nProvider>,
    )
    expect(screen.getByTestId('probe')).toHaveTextContent('Alerts')
    const hiBtn = screen.getByRole('radio', { name: 'हिन्दी (Hindi)' })
    expect(hiBtn).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(hiBtn)
    expect(screen.getByTestId('probe')).toHaveTextContent('अलर्ट')
    expect(document.documentElement.lang).toBe('hi')
    expect(localStorage.getItem(LANG_KEY)).toBe('hi')
    expect(hiBtn).toHaveAttribute('aria-checked', 'true')
  })

  it('restores the stored language and supports arrow keys', () => {
    localStorage.setItem(LANG_KEY, 'hi')
    render(
      <I18nProvider>
        <LangToggle />
        <Probe />
      </I18nProvider>,
    )
    expect(screen.getByTestId('lang')).toHaveTextContent('hi')
    expect(document.documentElement.lang).toBe('hi')
    act(() => {
      fireEvent.keyDown(screen.getByRole('radiogroup'), { key: 'ArrowLeft' })
    })
    expect(screen.getByTestId('lang')).toHaveTextContent('en')
    expect(localStorage.getItem(LANG_KEY)).toBe('en')
  })
})
