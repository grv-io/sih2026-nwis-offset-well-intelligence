import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FileText, ScanLine, X } from 'lucide-react'
import { api } from '../lib/api'
import type { Citation } from '../lib/citations'
import { citationRef } from '../lib/citations'
import { tMaybe, useT } from '../i18n'

export interface EvidenceRequest {
  ref: string // document_id, page ref, or citation string
  page?: number
  quote?: string | null
  highlight?: string[] // fallback terms when no verbatim quote
  title?: string
  context?: ReactNode
}

const Ctx = createContext<(r: EvidenceRequest) => void>(() => {})

export const useEvidence = () => useContext(Ctx)

export function openCitation(open: (r: EvidenceRequest) => void, c: Citation, title?: string) {
  open({ ref: citationRef(c), page: c.page, quote: c.quote, title })
}

function normalise(s: string) {
  return s.replace(/\s+/g, ' ').trim().toLowerCase()
}

/** Wrap the verbatim quote (whitespace-insensitive) or, failing that, lines with any highlight term. */
function Highlighted({ text, quote, terms }: { text: string; quote?: string | null; terms?: string[] }) {
  const markRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    markRef.current?.scrollIntoView({ block: 'center' })
  }, [text, quote])

  if (quote && quote.length > 8) {
    // Build a regex that tolerates whitespace differences between quote and page text.
    const pattern = quote
      .trim()
      .slice(0, 400)
      .split(/\s+/)
      .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
      .join('\\s+')
    try {
      const m = new RegExp(pattern, 'i').exec(text)
      if (m) {
        return (
          <>
            {text.slice(0, m.index)}
            <mark ref={markRef} className="bg-accent/25 text-ink rounded-sm outline outline-1 outline-accent/60">
              {m[0]}
            </mark>
            {text.slice(m.index + m[0].length)}
          </>
        )
      }
    } catch {
      /* fall through to term highlighting */
    }
  }
  const t = (terms ?? []).map(normalise).filter((x) => x.length > 2)
  if (!t.length) return <>{text}</>
  let first = true
  return (
    <>
      {text.split('\n').map((line, i) => {
        // a line must carry at least two of the query terms, else every "pipe" lights up
        const hit = t.filter((x) => normalise(line).includes(x)).length >= Math.min(2, t.length)
        const el = hit ? (
          <mark
            key={i}
            ref={first ? (n) => { markRef.current = n } : undefined}
            className="bg-accent/15 text-ink rounded-sm"
          >
            {line}
          </mark>
        ) : (
          <span key={i}>{line}</span>
        )
        if (hit) first = false
        return (
          <span key={i}>
            {el}
            {'\n'}
          </span>
        )
      })}
    </>
  )
}

/** Resolves a citation/document ref and renders that page's text with the quote highlighted. */
export function DocumentViewer({ req, header = true }: { req: EvidenceRequest; header?: boolean }) {
  const { t } = useT()
  const resolved = useQuery({
    queryKey: ['resolve', req.ref],
    queryFn: () => api.resolveDocument(req.ref),
    retry: 0,
  })
  const docId = resolved.data?.document_id
  const page = req.page ?? resolved.data?.page ?? 1
  const doc = useQuery({
    queryKey: ['doc', docId, page],
    queryFn: () => api.documentText(docId!, page),
    enabled: !!docId,
  })
  const d = doc.data
  return (
    <div className="flex flex-col min-h-0 h-full">
      {header && (
        <div className="px-5 py-3 border-b border-rule">
          <div className="font-mono text-sm text-ink truncate">{d?.file_name ?? req.ref}</div>
          {d && (
            <div className="flex flex-wrap items-center gap-2 mt-2 text-xs text-dim">
              <span className="chip">{d.well_id}</span>
              <span className="chip">{d.doc_type}</span>
              {d.report_date && <span className="chip num">{d.report_date}</span>}
              <span className="chip num">{t('doc.page', { p: d.page, n: d.n_pages })}</span>
              {d.is_scanned && (
                <span className="chip" title={t('doc.scannedTitle')}>
                  <ScanLine size={12} /> {t('doc.scanned')}
                </span>
              )}
            </div>
          )}
        </div>
      )}
      <div className="flex-1 overflow-auto px-5 py-4">
        {resolved.isLoading || doc.isLoading ? (
          <div className="text-dim text-sm">{t('doc.loading')}</div>
        ) : resolved.isError ? (
          <div className="text-sm text-ink2">
            {t('doc.notStored')}
            <div className="font-mono text-xs text-dim mt-2 break-all">{req.ref}</div>
          </div>
        ) : doc.isError ? (
          <div className="text-sm text-ink2">{t('doc.loadError')}</div>
        ) : d && d.text ? (
          <>
            <div className="label mb-2">{tMaybe(t, `doc.src.${d.text_source}`, d.text_source)}</div>
            <pre className="font-mono text-[12.5px] leading-[20px] text-ink2 whitespace-pre-wrap break-words" data-testid="document-text">
              <Highlighted text={d.text} quote={req.quote} terms={req.highlight} />
            </pre>
          </>
        ) : (
          <div className="text-sm text-ink2">{t('doc.noText')}</div>
        )}
      </div>
      {d && (
        <footer className="px-5 py-2.5 border-t border-rule text-micro text-faint font-mono truncate" title={d.path}>
          {d.path}
        </footer>
      )}
    </div>
  )
}

export function Drawer({ title, onClose, children, width = 640 }: { title: ReactNode; onClose: () => void; children: ReactNode; width?: number }) {
  const { t } = useT()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-[1000] flex justify-end" role="dialog" aria-modal="true">
      <button className="scrim absolute inset-0 cursor-default" aria-label={t('ui.closePanel')} onClick={onClose} />
      <aside className="pop-shadow relative h-full bg-s1 border-l border-line flex flex-col" style={{ width: `min(${width}px, 100vw)` }}>
        <header className="flex items-center gap-3 px-5 h-12 border-b border-rule shrink-0">
          <FileText size={16} className="text-dim shrink-0" />
          <div className="label flex-1 truncate">{title}</div>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label={t('ui.close')} title={t('ui.close')}>
            <X size={16} />
          </button>
        </header>
        <div className="flex-1 min-h-0 flex flex-col">{children}</div>
      </aside>
    </div>
  )
}

function EvidenceDrawer({ req, onClose }: { req: EvidenceRequest; onClose: () => void }) {
  const { t } = useT()
  return (
    <Drawer title={req.title ?? t('doc.sourceEvidence')} onClose={onClose}>
      {req.context && <div className="px-5 py-3 border-b border-rule text-sm text-ink2">{req.context}</div>}
      <DocumentViewer req={req} />
    </Drawer>
  )
}

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [req, setReq] = useState<EvidenceRequest | null>(null)
  const open = useCallback((r: EvidenceRequest) => setReq(r), [])
  const close = useCallback(() => setReq(null), [])
  const value = useMemo(() => open, [open])
  return (
    <Ctx.Provider value={value}>
      {children}
      {req && <EvidenceDrawer req={req} onClose={close} />}
    </Ctx.Provider>
  )
}
