import { useState, type FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { BookOpen, CornerDownLeft, MessageSquareText, Search, ShieldAlert } from 'lucide-react'
import { api } from '../../lib/api'
import { splitAnswer } from '../../lib/citations'
import { fmtNum } from '../../lib/format'
import type { Answer, Basin, SearchHit } from '../../lib/types'
import { CitationChip } from '../../components/CitationChip'
import { openCitation, useEvidence } from '../../components/Evidence'
import { Empty, Loading, Switch } from '../../components/ui'
import { useEventLabel, useT } from '../../i18n'

// Rehearsed questions (nwis/search/demo_cache.py DEFAULT_QUESTIONS). The text
// stays English in both UI languages: the report archive is English.
const DEMO_QUESTIONS = [
  'Girujan stuck pipe remedy',
  'Tipam losses LCM',
  'Barail kick mud weight',
  'cementing problems at 9-5/8 casing',
]

type SearchResult = { hits: SearchHit[]; mode: 'hybrid' | 'keyword'; note?: string }

async function runSearch(q: string): Promise<SearchResult> {
  try {
    return { hits: await api.search(q, 8), mode: 'hybrid' }
  } catch (e) {
    // Embedding service busy/down (VSAT, Ollama loaded): degrade to FTS keyword search.
    const hits = await api.searchKeyword(q, 8)
    return { hits, mode: 'keyword', note: e instanceof Error ? e.message : String(e) }
  }
}

function terms(q: string) {
  return q.split(/\s+/).filter((t) => t.length > 2)
}

function Snippet({ text, q }: { text: string; q: string }) {
  const ts = terms(q).map((t) => t.toLowerCase())
  const flat = text.replace(/\s+/g, ' ').trim()
  // centre the snippet on the first matching term
  const idx = ts.map((t) => flat.toLowerCase().indexOf(t)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0
  const start = Math.max(0, idx - 80)
  const snip = (start > 0 ? '… ' : '') + flat.slice(start, start + 260) + (flat.length > start + 260 ? ' …' : '')
  if (!ts.length) return <>{snip}</>
  const re = new RegExp(`(${ts.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig')
  return (
    <>
      {snip.split(re).map((part, i) =>
        i % 2 === 1 ?<mark key={i} className="bg-transparent text-ink font-semibold underline decoration-accent/70 underline-offset-2">{part}</mark> : <span key={i}>{part}</span>,
      )}
    </>
  )
}

function AnswerCard({ answer, question }: { answer: Answer; question: string }) {
  const { t } = useT()
  const eventLabel = useEventLabel()
  const open = useEvidence()
  const segs = splitAnswer(answer.text)
  const quoteFor = (pageRef: string | null) => answer.citations.find((c) => c.page_ref === pageRef)?.quote ?? null
  return (
    <div className="rounded border border-line bg-s2" data-testid="answer-card">
      <div className="flex items-center gap-2 px-4 h-9 border-b border-rule">
        <MessageSquareText size={14} className="text-dim" />
        <span className="label">{t('answer.cited')}</span>
        {answer.refused && <span className="chip text-high border-high/50"><ShieldAlert size={12} /> {t('answer.refused')}</span>}
        {answer.degraded && !answer.refused && <span className="chip text-high border-high/50">{t('answer.uncited')}</span>}
        <span className="ml-auto text-micro text-faint truncate" lang="en">“{question}”</span>
      </div>
      <div className="px-4 py-3 text-base leading-7 text-ink whitespace-pre-wrap">
        {segs.map((s, i) =>
          s.type === 'text' ? (
            <span key={i}>{s.text}</span>
          ) : (
            <CitationChip
              key={i}
              citation={{ ...s.citation, quote: quoteFor(s.citation.pageRef) }}
              onOpen={(c) => openCitation(open, c, t('answer.source'))}
            />
          ),
        )}
      </div>
      {answer.structured.length > 0 && (
        <details className="border-t border-rule">
          <summary className="px-4 h-9 flex items-center cursor-pointer text-xs text-dim hover:text-ink select-none">
            {t('answer.structured', { n: answer.structured.length })}
          </summary>
          <table className="w-full border-separate border-spacing-0 text-sm">
            <thead className="table-head">
              <tr><th>{t('col.well')}</th><th>{t('col.event')}</th><th className="text-right">{t('col.md')}</th><th>{t('col.formation')}</th><th className="text-right">{t('col.npt')}</th><th>{t('col.remedy')}</th></tr>
            </thead>
            <tbody>
              {answer.structured.slice(0, 12).map((e, i) => (
                <tr key={i} className="table-row">
                  <td className="font-mono">{e.well_id}</td>
                  <td>{eventLabel(e.event_type)}</td>
                  <td className="num text-right">{fmtNum(e.depth_md_m ?? null)}</td>
                  <td>{e.formation ?? '—'}</td>
                  <td className="num text-right">{e.hours_lost_npt != null ? `${fmtNum(e.hours_lost_npt, 1)} h` : '—'}</td>
                  <td className="text-xs text-ink2 max-w-[260px] truncate" title={e.remedy ?? ''}>{e.remedy ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}

export function MemoryPanel({ basin = 'assam' }: { basin?: Basin }) {
  const { t, lang } = useT()
  const open = useEvidence()
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [useCache, setUseCache] = useState(true)
  const search = useQuery({ queryKey: ['search', q], queryFn: () => runSearch(q), enabled: q.length > 1, retry: 0 })
  const ask = useMutation({ mutationFn: (question: string) => api.answer(question, useCache, lang) })
  const [asked, setAsked] = useState('')

  const submit = (e?: FormEvent) => {
    e?.preventDefault()
    const v = draft.trim()
    if (v) setQ(v)
  }
  const doAsk = (question: string) => {
    setDraft(question)
    setQ(question)
    setAsked(question)
    ask.mutate(question)
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      {basin === 'volve' && (
        <div className="px-4 py-1.5 border-b border-rule bg-s2 text-xs text-dim" data-testid="memory-basin-note">
          {t('mem.volveNote')}
        </div>
      )}
      <form onSubmit={submit} className="px-4 py-3 border-b border-rule flex flex-col gap-2">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={t('mem.placeholder')}
              className="input w-full pl-9 h-9"
              aria-label={t('mem.searchAria')}
              data-testid="memory-input"
            />
          </div>
          <button type="submit" className="btn h-9" disabled={!draft.trim()}>
            <CornerDownLeft size={14} /> {t('mem.search')}
          </button>
          <button type="button" className="btn-primary h-9" disabled={!draft.trim() || ask.isPending} onClick={() => doAsk(draft.trim())}>
            <MessageSquareText size={14} /> {t('mem.ask')}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="label mr-1">{t('mem.try')}</span>
          {DEMO_QUESTIONS.map((dq) => (
            <button key={dq} type="button" lang="en" className="chip hover:text-ink hover:border-ink2/50" title={t('mem.tryTitle')} onClick={() => doAsk(dq)} data-testid="demo-question">
              {dq}
            </button>
          ))}
          <span className="ml-auto">
            <Switch
              checked={useCache}
              onChange={setUseCache}
              label={t('mem.cache')}
              hint={t('mem.cacheHint')}
            />
          </span>
        </div>
      </form>

      <div className="flex-1 min-h-0 overflow-auto px-4 py-3 flex flex-col gap-4">
        {(ask.isPending || ask.data || ask.isError) && (
          ask.isPending ? (
            <div className="rounded border border-line bg-s2"><Loading label={useCache ? t('mem.answeringCache') : t('mem.answeringLlm')} /></div>
          ) : ask.isError ? (
            <div className="rounded border border-high/40 bg-high/10 px-4 py-3 text-sm text-ink2">
              {t('mem.answerError', { msg: (ask.error as Error).message })}
            </div>
          ) : (
            ask.data && <AnswerCard answer={ask.data} question={asked} />
          )
        )}

        {q && (
          <section aria-label={t('mem.resultsAria')}>
            <div className="flex items-center gap-2 mb-2">
              <span className="label">{t('mem.passages')}</span>
              {search.data && (
                <span className={'chip ' + (search.data.mode === 'keyword' ? 'text-high border-high/40' : '')} title={search.data.note}>
                  {search.data.mode === 'hybrid' ? t('mem.hybrid') : t('mem.keywordOnly')}
                </span>
              )}
              {search.data && <span className="text-xs text-dim num">{t('mem.hits', { n: search.data.hits.length })}</span>}
            </div>
            {search.isLoading ? (
              <Loading label={t('mem.searching')} />
            ) : search.data && search.data.hits.length === 0 ? (
              <Empty icon={<BookOpen />} title={t('mem.noMatchTitle')}>{t('mem.noMatchBody')}</Empty>
            ) : (
              <ol className="flex flex-col gap-2">
                {search.data?.hits.map((h) => (
                  <li key={h.chunk_id}>
                    <button
                      className="w-full text-left rounded border border-line bg-s1 hover:border-ink2/40 hover:bg-s2 px-3 py-2 transition-colors"
                      onClick={() => open({ ref: h.document_id ?? h.page_ref, page: pageOf(h.page_ref), highlight: terms(q), title: t('mem.passageTitle') })}
                    >
                      <div className="flex items-center gap-2 text-xs">
                        <span className="font-mono text-ink">{h.well_id}</span>
                        <span className="font-mono text-dim">{h.page_ref}</span>
                        <span className="ml-auto text-faint">{h.why}</span>
                      </div>
                      <div className="text-sm text-ink2 mt-1 leading-6"><Snippet text={h.text} q={q} /></div>
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </section>
        )}

        {!q && !ask.data && !ask.isPending && (
          <Empty icon={<BookOpen size={28} />} title={t('mem.emptyTitle')}>
            {t('mem.emptyBody')}
          </Empty>
        )}
      </div>
    </div>
  )
}

function pageOf(ref: string) {
  const m = ref.match(/#p(\d+)/)
  return m ? parseInt(m[1], 10) : 1
}
