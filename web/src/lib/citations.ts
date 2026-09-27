// Citation parsing. The backend emits citations in three shapes:
//   1. inline answer tags            "[DUL-003 | DUL-003_DDR_2019-01-02.pdf#p1]"
//   2. alert citation strings        'DUL-003 | <doc id> | DUL-003_DDR_...pdf#p1 — "quote"'
//                                    or a bare page ref "DUL-006_DDR_2020-01-02.pdf#p1"
//   3. non-document evidence         "DUL-005 live sensors @ t=812s (no document precedent)"
// Every shape becomes a Citation the UI can render as a chip and, when it points
// at a document, open in the evidence drawer.

export interface Citation {
  raw: string
  kind: 'document' | 'sensor' | 'text'
  wellId: string | null
  pageRef: string | null
  documentId: string | null
  page: number
  quote: string | null
  label: string
}

export type AnswerSegment = { type: 'text'; text: string } | { type: 'cite'; citation: Citation }

// no \b: "_" is a word char, so \b never fires inside "MOR-004_WCR.pdf"
const WELL_RE = /(?<![A-Za-z])([A-Z]{2,4}-\d{2,4})(?!\d)/
const PAGE_REF_RE = /([\w.-]+\.(?:pdf|txt))#p(\d+)/i
const STEM_RE = /(?<![A-Za-z])([A-Z]{2,4}-\d{2,4}_[A-Z]{2,4}(?:_[\w-]+)?)/
const UUID_RE = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i
const INLINE_TAG_RE = /\[\s*([^|\]]+?)\s*\|\s*([^\]]+?)\s*\]/g

export function parseCitation(raw: string): Citation {
  const text = raw.trim()
  let quote: string | null = null
  let head = text
  const q = text.match(/\s[—-]{1,2}\s*"([\s\S]*)"\s*$/)
  if (q && q.index !== undefined) {
    quote = q[1]
    head = text.slice(0, q.index)
  }
  const inner = head.replace(/^\[|\]$/g, '')
  const wellId = inner.match(WELL_RE)?.[1] ?? null
  const pr = inner.match(PAGE_REF_RE)
  const pageRef = pr ? `${pr[1]}#p${pr[2]}` : null
  const page = pr ? parseInt(pr[2], 10) : 1
  const documentId = inner.match(UUID_RE)?.[0] ?? (pageRef ? null : inner.match(STEM_RE)?.[1] ?? null)

  if (/live sensors|no document precedent/i.test(inner)) {
    return { raw, kind: 'sensor', wellId, pageRef: null, documentId: null, page: 1, quote, label: inner }
  }
  if (pageRef || documentId) {
    const file = pageRef ? pageRef.replace(/#p\d+$/, '') : documentId!
    const short = file.replace(/\.(pdf|txt)$/i, '').replace(/^[A-Z]{2,4}-\d{2,4}_/, '')
    const label = `${wellId ?? file.match(WELL_RE)?.[1] ?? ''} | ${short} p${page}`.replace(/^ \| /, '')
    return { raw, kind: 'document', wellId: wellId ?? file.match(WELL_RE)?.[1] ?? null, pageRef, documentId, page, quote, label }
  }
  return { raw, kind: 'text', wellId, pageRef: null, documentId: null, page: 1, quote, label: inner }
}

/** Split an answer into prose and citation chips, preserving order. */
export function splitAnswer(text: string): AnswerSegment[] {
  const out: AnswerSegment[] = []
  let last = 0
  for (const m of text.matchAll(INLINE_TAG_RE)) {
    const idx = m.index ?? 0
    if (idx > last) out.push({ type: 'text', text: text.slice(last, idx) })
    out.push({ type: 'cite', citation: parseCitation(`[${m[1]} | ${m[2]}]`) })
    last = idx + m[0].length
  }
  if (last < text.length) out.push({ type: 'text', text: text.slice(last) })
  return out
}

/** Reference string the backend's /documents/resolve accepts. */
export const citationRef = (c: Citation) => c.documentId ?? c.pageRef ?? c.raw
