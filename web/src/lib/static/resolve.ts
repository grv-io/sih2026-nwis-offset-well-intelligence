// TypeScript port of the citation -> document resolver (api/ui_router.py:
// _find_doc, resolve_document), run over the recorded document index.

export interface DocIndexEntry {
  document_id: string
  well_id: string
  file_name: string
}

const PAGE_RE = /#p(\d+)/
const STEM_RE = /([A-Z]{2,4}-\d{2,4}_[A-Z]{2,4}_[\w-]+?)(?:\.(?:pdf|txt))?(?:#p\d+)?(?=$|[\s|"'\]])/g

const stemOf = (name: string) => name.replace(/\.[^.]+$/, '')

/** document_id exact, else a page_ref / filename stem ("DUL-001_DDR_2017-06-12.pdf#p1"). */
export function findDoc(docs: DocIndexEntry[], ref: string): DocIndexEntry | null {
  const byId = new Map(docs.map((d) => [d.document_id, d]))
  const exact = byId.get(ref)
  if (exact) return exact
  let stem = ref.trim().replace(/#p\d+$/, '')
  const nameHit = docs.find((d) => d.file_name === stem)
  if (nameHit) return nameHit
  stem = stem.replace(/\.(pdf|txt)$/, '')
  const idHit = byId.get(stem)
  if (idHit) return idHit
  const stemHits = docs.filter((d) => stemOf(d.file_name) === stem)
  // prefer the canonical (generator) record, then a text document
  stemHits.sort(
    (a, b) =>
      Number(a.document_id !== stem) - Number(b.document_id !== stem) ||
      Number(!a.file_name.endsWith('.txt')) - Number(!b.file_name.endsWith('.txt')),
  )
  return stemHits[0] ?? null
}

/** Citation string -> {document_id, well_id, page}; null when nothing matches. */
export function resolveRef(docs: DocIndexEntry[], ref: string): { document_id: string; well_id: string; page: number; ref: string } | null {
  const candidates = [ref.trim()]
  candidates.push(...ref.split(/[|[\]—]/).map((p) => p.trim()).filter(Boolean))
  for (const m of ref.matchAll(STEM_RE)) candidates.push(m[0])
  for (const c of candidates) {
    const doc = findDoc(docs, c)
    if (doc) {
      const m = ref.match(PAGE_RE)
      return { document_id: doc.document_id, well_id: doc.well_id, page: m ? parseInt(m[1], 10) : 1, ref }
    }
  }
  return null
}
