import { lazy, Suspense } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Building2, HardHat, Info } from 'lucide-react'
import { STATIC_DEMO, api } from './lib/api'
import { EvidenceProvider } from './components/Evidence'
import { Loading } from './components/ui'
import { LangToggle, ThemeToggle } from './components/Preferences'
import { useT } from './i18n'

const OfficePage = lazy(() => import('./features/office/OfficePage'))
const RigPage = lazy(() => import('./features/rig/RigPage'))

function Brand() {
  const { t } = useT()
  return (
    <div className="flex items-center gap-3 pr-4 border-r border-rule h-full">
      <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
        <path d="M16 4v24M9 28h14" style={{ stroke: 'rgb(var(--accent))' }} strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="16" cy="12" r="4" style={{ fill: 'none', stroke: 'rgb(var(--ink))' }} strokeWidth="2" />
      </svg>
      <div className="leading-tight">
        <div className="text-sm font-bold tracking-[0.08em] text-ink">NWIS</div>
        <div className="text-[10.5px] text-dim">{t('brand.tagline')}</div>
      </div>
    </div>
  )
}

function PersonaNav() {
  const { t } = useT()
  const cls = ({ isActive }: { isActive: boolean }) =>
    'flex items-center gap-2 h-full px-3 text-sm border-b-2 transition-colors ' +
    (isActive ? 'border-accent text-ink' : 'border-transparent text-dim hover:text-ink')
  return (
    <nav className="flex items-stretch h-full" aria-label={t('nav.aria')}>
      <NavLink to="/office" className={cls} data-testid="nav-office">
        <Building2 size={15} /> {t('nav.office')} <span className="hidden lap:inline text-faint">· {t('nav.officeSub')}</span>
      </NavLink>
      <NavLink to="/rig" className={cls} data-testid="nav-rig">
        <HardHat size={15} /> {t('nav.rig')} <span className="hidden lap:inline text-faint">· {t('nav.rigSub')}</span>
      </NavLink>
    </nav>
  )
}

function BasinBadge() {
  // Basin lives in the URL (see useOfficeState); the Rig persona has no basin
  // toggle and always replays the synthetic Assam logs, so it always shows the
  // Assam badge regardless of what the office page's URL happens to carry.
  const { t } = useT()
  const location = useLocation()
  const [sp] = useSearchParams()
  const volve = location.pathname.startsWith('/office') && sp.get('basin') === 'volve'
  return (
    <span
      className="hidden tab:inline-flex items-center h-6 px-2 rounded-sm border border-line bg-s2 text-micro font-semibold tracking-[0.06em] text-ink2"
      data-testid="basin-badge"
      title={volve ? t('badge.volveTitle') : t('badge.assamTitle')}
    >
      {volve ? t('badge.volve') : t('badge.assam')}
    </span>
  )
}

function LinkStatus() {
  const { t } = useT()
  const h = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000, retry: 0 })
  const api_ok = h.isSuccess
  const llm_ok = !!h.data?.llm?.ollama
  const Dot = ({ ok, label, title }: { ok: boolean; label: string; title: string }) => (
    <span className="flex items-center gap-1.5 text-xs text-dim" title={title}>
      <span className={'h-2 w-2 rounded-full ' + (ok ? 'bg-ok' : 'bg-high')} />
      {label}
      <span className={ok ? 'text-ink2' : 'text-high'}>{ok ? t('link.ok') : t('link.down')}</span>
    </span>
  )
  return (
    <div className="flex items-center gap-4">
      <Dot ok={api_ok} label={t('link.api')} title={api_ok ? t('link.apiOk') : t('link.apiDown')} />
      <Dot
        ok={llm_ok}
        label={t('link.llm')}
        title={llm_ok ? t('link.llmOk', { provider: h.data?.llm?.provider ?? 'ollama' }) : t('link.llmDown')}
      />
    </div>
  )
}

// The static GitHub Pages build has no server: instead of API/LLM link lights it
// says so, and links to the README section on running the full live system.
const REPO_URL = 'https://github.com/grv-io/sih2026-nwis-offset-well-intelligence'

function StaticDemoBadge() {
  const { t } = useT()
  return (
    <a
      href={`${REPO_URL}#static-demo-vs-the-full-system`}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1.5 h-6 px-2 rounded-sm border border-line text-micro text-dim hover:text-ink hover:border-ink2/50 transition-colors whitespace-nowrap"
      title={t('static.badgeTitle')}
      data-testid="static-badge"
    >
      <Info size={12} />
      {t('static.badge')}
    </a>
  )
}

export default function App() {
  const { t } = useT()
  return (
    <EvidenceProvider>
      <div className="h-full flex flex-col">
        <header className="h-12 shrink-0 flex items-center gap-4 px-4 bg-s1 border-b border-line">
          <Brand />
          <PersonaNav />
          <div className="ml-auto flex items-center gap-4 h-full">
            <BasinBadge />
            {STATIC_DEMO ? <StaticDemoBadge /> : <LinkStatus />}
            <div className="flex items-center gap-2 pl-4 border-l border-rule h-7" data-testid="prefs">
              <LangToggle />
              <ThemeToggle />
            </div>
          </div>
        </header>
        <main className="flex-1 min-h-0">
          <Suspense fallback={<Loading label={t('loading.workspace')} />}>
            <Routes>
              <Route path="/" element={<Navigate to="/office" replace />} />
              <Route path="/office" element={<OfficePage />} />
              <Route path="/rig" element={<RigPage />} />
              <Route path="*" element={<Navigate to="/office" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </EvidenceProvider>
  )
}
