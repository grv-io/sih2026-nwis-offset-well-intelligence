import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellOff, CloudOff, Pause, Play, Radio, RotateCcw, Square } from 'lucide-react'
import { ApiError, api } from '../../lib/api'
import { fmtAgo, fmtNum, fmtRigClock } from '../../lib/format'
import { SEVERITY_META, normaliseSeverity } from '../../lib/hazards'
import type { Alert, LiveSample } from '../../lib/types'
import { Sparkline } from '../../components/Sparkline'
import { WellPicker } from '../../components/WellPicker'
import { Empty, Segmented } from '../../components/ui'
import { AlertCard } from './AlertCard'
import { useT, type MsgKey } from '../../i18n'

const SPEEDS = ['50', '200', '500'] as const
type Speed = (typeof SPEEDS)[number]

function useLiveLink() {
  // Poll every second while a replay is running; back off when paused or idle.
  const [interval, setIntervalMs] = useState(1000)
  const state = useQuery({
    queryKey: ['live', 'state'],
    queryFn: () => api.live.state(60),
    refetchInterval: interval,
    retry: 0,
    refetchOnWindowFocus: true,
  })
  const alerts = useQuery({
    queryKey: ['live', 'alerts'],
    queryFn: api.live.alerts,
    refetchInterval: interval,
    retry: 0,
    enabled: state.isSuccess || (state.isError && !(state.error instanceof ApiError && state.error.status === 409)),
  })
  const noSession = state.error instanceof ApiError && state.error.status === 409
  const running = !!state.data?.running && !state.data?.paused && !state.data?.finished
  useEffect(() => setIntervalMs(running ? 1000 : noSession ? 5000 : 3000), [running, noSession])
  return { state, alerts, noSession }
}

function ParamTile({ label, unit, values, stat, digits = 1 }: {
  label: string
  unit: string
  values: (number | undefined)[]
  stat?: { mean: number; std: number }
  digits?: number
}) {
  const last = [...values].reverse().find((v) => typeof v === 'number')
  return (
    <div className="panel px-3 pt-2 pb-1.5 flex flex-col gap-1 min-w-0">
      <div className="flex items-baseline gap-2">
        <span className="label">{label}</span>
        <span className="ml-auto num text-xl font-semibold text-ink">{fmtNum(last ?? null, digits)}</span>
        <span className="text-xs text-dim w-[34px]">{unit}</span>
      </div>
      <Sparkline values={values} height={32} band={stat} />
    </div>
  )
}

function stats(vals: (number | undefined)[]) {
  const v = vals.filter((x): x is number => typeof x === 'number')
  if (v.length < 5) return undefined
  const mean = v.reduce((s, x) => s + x, 0) / v.length
  const std = Math.sqrt(v.reduce((s, x) => s + (x - mean) ** 2, 0) / v.length)
  return { mean, std }
}

export default function RigPage() {
  const { t } = useT()
  const qc = useQueryClient()
  const { state, alerts, noSession } = useLiveLink()
  const [wellId, setWellId] = useState('DUL-005')
  const [speed, setSpeed] = useState<Speed>('500')
  const [startMd, setStartMd] = useState('')
  const [seekMd, setSeekMd] = useState('')
  const wells = useQuery({ queryKey: ['wells'], queryFn: api.wells, staleTime: Infinity })

  const s = state.data
  const lastOk = state.dataUpdatedAt
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const stale = !!s && (state.isError || now - lastOk > 5000) && !noSession

  useEffect(() => {
    if (s?.well_id) setWellId(s.well_id)
  }, [s?.well_id])

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['live'] })
  }
  const start = useMutation({
    mutationFn: async () => {
      const md = Number(startMd)
      try {
        await api.live.startAt(wellId, Number(speed), md > 0 ? md : null)
      } catch (e) {
        // The server may be busy computing the first alert's recommendation; the replay
        // is running anyway and polling will pick it up.
        if (!(e instanceof ApiError && e.status === 0)) throw e
      }
    },
    onSuccess: invalidate,
  })
  const ctl = useMutation({
    mutationFn: (fn: () => Promise<unknown>) => fn(),
    onSuccess: invalidate,
  })
  const ack = useMutation({
    mutationFn: (id: string) => api.live.ack(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live', 'alerts'] }),
  })

  // Unacknowledged first (ISA-18.2: acked alarms lose salience), newest first within each; remember which alerts this screen has already shown, to flag arrivals.
  const list: Alert[] = useMemo(() => [...(alerts.data ?? [])].sort((a, b) => Number(a.acknowledged) - Number(b.acknowledged) || b.t_s - a.t_s), [alerts.data])
  const seen = useRef<Set<string>>(new Set())
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  useEffect(() => {
    const newOnes = list.filter((a) => !seen.current.has(a.alert_id)).map((a) => a.alert_id)
    if (newOnes.length && seen.current.size > 0) setFresh(new Set(newOnes))
    newOnes.forEach((id) => seen.current.add(id))
  }, [list])

  const unacked = list.filter((a) => !a.acknowledged)
  const worst = unacked.reduce<string | null>(
    (w, a) => (!w || SEVERITY_META[normaliseSeverity(a.severity)].rank > SEVERITY_META[normaliseSeverity(w)].rank ? a.severity : w),
    null,
  )

  const win: LiveSample[] = s?.window ?? []
  const col = (k: keyof LiveSample) => win.map((x) => x[k] as number | undefined)
  const statusKey: MsgKey | null = noSession ? 'rig.status.none' : !s ? null : s.finished ? 'rig.status.end' : s.paused ? 'rig.status.paused' : s.running ? 'rig.status.live' : 'rig.status.stopped'
  const live = statusKey === 'rig.status.live'

  return (
    <div className="h-full flex flex-col min-h-0" data-testid="rig-page">
      {/* Header: where the bit is, and the replay transport */}
      <div className="shrink-0 bg-s1 border-b border-line px-4 py-3 flex flex-wrap items-center gap-x-8 gap-y-3">
        <div className="flex items-center gap-3">
          <span
            className={'inline-flex items-center gap-1.5 h-6 px-2 rounded-sm text-micro font-semibold tracking-[0.08em] border ' +
              (live ? 'border-ok/50 text-ok' : 'border-line text-dim')}
            data-testid="replay-status"
            data-status={statusKey ?? 'loading'}
          >
            <Radio size={12} /> {statusKey ? t(statusKey) : '…'}
          </span>
          <span className="font-mono text-lg text-ink">{s?.well_id ?? wellId}</span>
        </div>
        <div className="kv">
          <span className="label">{t('rig.bitDepth')}</span>
          <span className="num text-num font-semibold text-ink" data-testid="bit-depth">
            {s?.depth_md_m != null ? fmtNum(s.depth_md_m) : '—'}
            <span className="text-base font-normal text-dim ml-1">m</span>
          </span>
        </div>
        <div className="kv">
          <span className="label">{t('rig.formation')}</span>
          <span className="text-xl font-semibold text-ink">{s?.formation ?? '—'}</span>
        </div>
        <div className="kv">
          <span className="label">{t('rig.rop')}</span>
          <span className="num text-xl font-semibold text-ink">
            {fmtNum(s?.last_sample?.rop_m_hr ?? null, 1)}
            <span className="text-xs font-normal text-dim ml-1">m/h</span>
          </span>
        </div>
        <div className="kv">
          <span className="label">{t('rig.time')}</span>
          <span className="num text-xl text-ink2">{fmtRigClock(s?.t_s)}</span>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {noSession || s?.finished ? (
            <>
              <WellPicker wells={wells.data ?? []} value={wellId} onChange={setWellId} label={t('picker.well')} />
              <input
                className="input w-[128px] num"
                inputMode="numeric"
                placeholder={t('rig.startMd')}
                value={startMd}
                onChange={(e) => setStartMd(e.target.value.replace(/[^\d.]/g, ''))}
                aria-label={t('rig.startMdAria')}
              />
              <Segmented label={t('rig.speed')} size="sm" value={speed} onChange={setSpeed}
                options={SPEEDS.map((v) => ({ value: v, label: `${v}×` }))} />
              <button className="btn-primary h-8" onClick={() => start.mutate()} disabled={start.isPending} data-testid="start-replay">
                <Play size={14} /> {start.isPending ? t('rig.starting') : t('rig.start')}
              </button>
            </>
          ) : (
            <>
              {s?.paused ? (
                <button className="btn-primary h-8" onClick={() => ctl.mutate(api.live.resume)}>
                  <Play size={14} /> {t('rig.resume')}
                </button>
              ) : (
                <button className="btn h-8" onClick={() => ctl.mutate(api.live.pause)}>
                  <Pause size={14} /> {t('rig.pause')}
                </button>
              )}
              <form
                className="flex items-center gap-1"
                onSubmit={(e) => {
                  e.preventDefault()
                  const md = Number(seekMd)
                  if (md > 0) ctl.mutate(() => api.live.seek(md))
                }}
              >
                <input
                  className="input w-[124px] num"
                  inputMode="numeric"
                  placeholder={t('rig.seekMd')}
                  value={seekMd}
                  onChange={(e) => setSeekMd(e.target.value.replace(/[^\d.]/g, ''))}
                  aria-label={t('rig.seekAria')}
                />
                <button className="btn h-8" type="submit" disabled={!seekMd}>
                  <RotateCcw size={14} /> {t('rig.seek')}
                </button>
              </form>
              <span className="chip num" title={t('rig.speedTitle')}>{s?.speed ?? '—'}×</span>
              <button className="btn h-8" onClick={() => ctl.mutate(api.live.stop)}>
                <Square size={13} /> {t('rig.stop')}
              </button>
            </>
          )}
        </div>
      </div>

      {(stale || start.isError || ctl.isError) && (
        <div className="shrink-0 flex items-center gap-2 px-4 h-9 bg-high/10 border-b border-high/40 text-sm text-ink" role="status" data-testid="stale-banner">
          <CloudOff size={15} className="text-high" />
          {stale ? (
            <span>
              <b className="text-high">{t('rig.degraded')}</b> {t('rig.degradedBody', { ago: fmtAgo(now - lastOk) })}
            </span>
          ) : (
            <span><b className="text-high">{t('rig.cmdFailed')}</b> {((start.error ?? ctl.error) as Error)?.message}</span>
          )}
        </div>
      )}

      {/* Live parameter strip: last 60 samples */}
      {s && !noSession && (
        <div className="shrink-0 grid grid-cols-4 gap-2 px-2 pt-2">
          <ParamTile label={t('rig.torque')} unit="kft·lb" values={col('torque_kftlb')} stat={stats(col('torque_kftlb'))} />
          <ParamTile label={t('rig.pit')} unit="bbl" values={col('pit_vol_bbl')} stat={stats(col('pit_vol_bbl'))} />
          <ParamTile label={t('rig.gas')} unit="%" values={col('gas_pct')} stat={stats(col('gas_pct'))} digits={2} />
          <ParamTile label={t('rig.spp')} unit="psi" values={col('spp_psi')} stat={stats(col('spp_psi'))} digits={0} />
        </div>
      )}

      {/* Alerts, newest first */}
      <div className="flex-1 min-h-0 overflow-auto px-2 py-2">
        <div className="flex items-center gap-3 px-2 pb-2">
          <h2 className="text-sm font-semibold text-ink">{t('rig.alerts')}</h2>
          <span className="num text-sm text-dim">
            {t('rig.alertCounts', { u: unacked.length, n: list.length })}
          </span>
          {worst && (
            <span className="text-xs text-dim">
              {t('rig.highest')} <b style={{ color: `rgb(var(${SEVERITY_META[normaliseSeverity(worst)].rgbVar}))` }}>{t(`sev.${normaliseSeverity(worst)}`)}</b>
            </span>
          )}
          {s?.signals_unavailable?.length ? (
            <span className="ml-auto text-xs text-faint" title={t('rig.unavailableTitle')}>
              {t('rig.unavailable', { list: s.signals_unavailable.join(', ') })}
            </span>
          ) : null}
        </div>
        {noSession ? (
          <Empty icon={<Radio size={28} />} title={t('rig.noReplayTitle')}>
            {t('rig.noReplayBody')}
          </Empty>
        ) : list.length === 0 ? (
          <Empty icon={<BellOff size={28} />} title={t('rig.noAlertsTitle')}>
            {t('rig.noAlertsBody')}
          </Empty>
        ) : (
          <div className="flex flex-col gap-2 max-w-[1280px]">
            {list.map((a) => (
              <AlertCard
                key={a.alert_id}
                alert={a}
                isNew={fresh.has(a.alert_id)}
                onAck={() => ack.mutate(a.alert_id)}
                acking={ack.isPending && ack.variables === a.alert_id}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
