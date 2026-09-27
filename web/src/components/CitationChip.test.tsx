import { fireEvent, render, screen } from '@testing-library/react'
import { parseCitation, splitAnswer } from '../lib/citations'
import { CitationChip } from './CitationChip'

describe('parseCitation', () => {
  it('parses an inline answer tag', () => {
    const c = parseCitation('[DUL-003 | DUL-003_DDR_2019-01-02.pdf#p2]')
    expect(c.kind).toBe('document')
    expect(c.wellId).toBe('DUL-003')
    expect(c.pageRef).toBe('DUL-003_DDR_2019-01-02.pdf#p2')
    expect(c.page).toBe(2)
    expect(c.label).toBe('DUL-003 | DDR_2019-01-02 p2')
  })

  it('parses an alert citation with document id and quote (nwis/live/recommend.py shape)', () => {
    const c = parseCitation(
      'DUL-006 | 0ba2ade7-de9b-5cf0-a94a-a43add4ca3ff | DUL-006_DDR_2020-01-02.txt#p1 — "Pipe stuck at 2431 m, jarred free"',
    )
    expect(c.kind).toBe('document')
    expect(c.wellId).toBe('DUL-006')
    expect(c.documentId).toBe('0ba2ade7-de9b-5cf0-a94a-a43add4ca3ff')
    expect(c.quote).toBe('Pipe stuck at 2431 m, jarred free')
  })

  it('parses a bare page ref and infers the well from the file name', () => {
    const c = parseCitation('MOR-004_WCR.pdf#p3')
    expect(c.kind).toBe('document')
    expect(c.wellId).toBe('MOR-004')
    expect(c.page).toBe(3)
  })

  it('treats live-sensor evidence as non-document', () => {
    const c = parseCitation('DUL-005 live sensors @ t=812s (no document precedent)')
    expect(c.kind).toBe('sensor')
    expect(c.pageRef).toBeNull()
  })

  it('treats model reasons as plain text', () => {
    expect(parseCitation('offset density 0.8 in Girujan').kind).toBe('text')
  })
})

describe('splitAnswer', () => {
  it('keeps prose and chips in order', () => {
    const segs = splitAnswer('Jarred free after 9.5 h [DUL-001 | DUL-001_DDR_2017-06-13.pdf#p1]; spotted pill [DUL-003 | DUL-003_WCR.txt#p1].')
    expect(segs.map((s) => s.type)).toEqual(['text', 'cite', 'text', 'cite', 'text'])
    const cites = segs.filter((s) => s.type === 'cite')
    expect(cites.map((s) => (s.type === 'cite' ? s.citation.wellId : null))).toEqual(['DUL-001', 'DUL-003'])
  })

  it('returns a single text segment when uncited', () => {
    expect(splitAnswer('no tags here')).toEqual([{ type: 'text', text: 'no tags here' }])
  })
})

describe('CitationChip', () => {
  it('renders a document chip as a button that opens the source', () => {
    const onOpen = vi.fn()
    render(<CitationChip citation="[DUL-003 | DUL-003_DDR_2019-01-02.pdf#p1]" onOpen={onOpen} />)
    const chip = screen.getByRole('button')
    expect(chip).toHaveTextContent('[DUL-003 | DDR_2019-01-02 p1]')
    fireEvent.click(chip)
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ pageRef: 'DUL-003_DDR_2019-01-02.pdf#p1' }))
  })

  it('renders sensor evidence as a static chip', () => {
    render(<CitationChip citation="DUL-005 live sensors @ t=812s (no document precedent)" />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByTestId('citation-chip')).toHaveAttribute('data-kind', 'sensor')
  })
})
