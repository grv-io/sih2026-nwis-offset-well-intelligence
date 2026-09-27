import { Activity, FileText } from 'lucide-react'
import type { Citation } from '../lib/citations'
import { parseCitation } from '../lib/citations'

export interface CitationChipProps {
  citation: Citation | string
  onOpen?: (c: Citation) => void
}

/** `[well | report pN]` chip. Document citations open the evidence drawer;
 *  sensor/free-text evidence renders as a static chip. */
export function CitationChip({ citation, onOpen }: CitationChipProps) {
  const c = typeof citation === 'string' ? parseCitation(citation) : citation
  if (c.kind !== 'document') {
    return (
      <span className="chip max-w-full text-dim" title={c.raw} data-testid="citation-chip" data-kind={c.kind}>
        {c.kind === 'sensor' && <Activity size={12} />}
        <span className="truncate">{c.label}</span>
      </span>
    )
  }
  return (
    <button
      type="button"
      className="chip font-mono hover:border-accent hover:text-accent transition-colors align-baseline"
      title={c.quote ? `"${c.quote}"` : c.pageRef ?? c.raw}
      onClick={() => onOpen?.(c)}
      data-testid="citation-chip"
      data-kind="document"
    >
      <FileText size={12} />
      [{c.label}]
    </button>
  )
}
