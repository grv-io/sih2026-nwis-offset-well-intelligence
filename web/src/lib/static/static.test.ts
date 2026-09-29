// Parity with the server's scope guard (nwis/search/answer.py). Expected values were
// produced by the Python implementation; the static demo must answer identically.
import { guardAnswer, queryKind, type GuardKind } from './guard'
import { corrKey, normQuestion } from './demo'
import { resolveRef } from './resolve'

const KINDS: Record<string, GuardKind | null> = {
  "hello": "greeting",
  "Hi!!": "greeting",
  "namaste": "greeting",
  "what is the weather in Jaipur": "off_topic",
  "Girujan stuck pipe remedy": null,
  "what happened at 2,400 m near DUL-005": null,
  "Girujan में stuck pipe कैसे छुड़ाया?": null,
  "tell me a joke": "off_topic",
  "mor-003 status": null,
  "15/9-F-12": null,
  "1500m": null,
  "thank you": "greeting",
  "": "greeting",
  "नमस्ते": "off_topic",
  "क्या हाल है": "off_topic",
  "Barail kick mud weight": null
}

const GUARD: Record<string, string> = {
  "greeting|en": "Hello. I am the NWIS archive assistant for the drilling desk. I answer questions from the offset-well reports and cite the page for every claim. Try: \"Girujan stuck pipe remedy\" · \"Tipam losses LCM\" · \"Barail kick mud weight\".\nNamaste. Main sirf drilling archive ke sawalon ka jawab deta hoon, har jawab ke saath report ka page.",
  "greeting|hi": "नमस्ते। मैं ड्रिलिंग डेस्क का NWIS आर्काइव सहायक हूँ। ऑफ़सेट वेल की रिपोर्ट से जवाब देता हूँ और हर बात के साथ रिपोर्ट का पेज बताता हूँ। पूछकर देखें: \"Girujan में stuck pipe कैसे छुड़ाया?\" · \"Girujan stuck pipe remedy\" · \"Tipam losses LCM\" · \"Barail kick mud weight\".\nHello. I am the NWIS archive assistant for the drilling desk. I answer questions from the offset-well reports and cite the page for every claim.",
  "off_topic|en": "I only answer questions about the drilling archive (offset-well events, depths, formations, remedies, NPT). Try: \"Girujan stuck pipe remedy\" · \"Tipam losses LCM\" · \"Barail kick mud weight\".\nMain sirf drilling archive ke sawalon ka jawab deta hoon, jaise: Girujan me stuck pipe kaise chhudaya?",
  "off_topic|hi": "मैं सिर्फ़ ड्रिलिंग आर्काइव के सवालों का जवाब देता हूँ (ऑफ़सेट वेल के इवेंट, डेप्थ, फ़ॉर्मेशन, उपाय, NPT)। जैसे: \"Girujan में stuck pipe कैसे छुड़ाया?\" · \"Girujan stuck pipe remedy\" · \"Tipam losses LCM\" · \"Barail kick mud weight\".\nI only answer questions about the drilling archive (offset-well events, depths, formations, remedies, NPT)."
}

describe('static scope guard', () => {
  it('classifies questions like nwis/search/answer.py _query_kind', () => {
    for (const [q, kind] of Object.entries(KINDS)) expect(queryKind(q), q).toBe(kind)
  })

  it('answers with the same fixed lines as _guard_answer, in both languages', () => {
    for (const [key, text] of Object.entries(GUARD)) {
      const [kind, lang] = key.split('|') as [GuardKind, 'en' | 'hi']
      const a = guardAnswer(kind, lang)
      expect(a.text, key).toBe(text)
      expect(a.guard).toBe(kind)
      expect(a.citations).toEqual([])
    }
  })
})

describe('static demo keys', () => {
  it('normalises questions like build_static_demo.norm_question', () => {
    expect(normQuestion('  Girujan  Stuck pipe REMEDY? ')).toBe('girujan stuck pipe remedy')
    expect(normQuestion('Girujan में stuck pipe कैसे छुड़ाया?')).toBe('girujan में stuck pipe कैसे छुड़ाया')
  })

  it('keys correlation panels by active well + sorted offsets', () => {
    expect(corrKey(['DUL-005', 'DUL-012', 'DUL-003'])).toBe('DUL-005_DUL-003_DUL-012')
  })

  it('resolves citation strings to recorded documents', () => {
    const docs = [
      { document_id: 'DUL-012_WCR', well_id: 'DUL-012', file_name: 'DUL-012_WCR.pdf' },
      { document_id: 'DUL-003_DDR_2015-09-04', well_id: 'DUL-003', file_name: 'DUL-003_DDR_2015-09-04.pdf' },
    ]
    expect(resolveRef(docs, '[DUL-012 | DUL-012_WCR.pdf#p3]')).toMatchObject({ document_id: 'DUL-012_WCR', page: 3 })
    expect(resolveRef(docs, 'DUL-003_DDR_2015-09-04.pdf#p1')).toMatchObject({ document_id: 'DUL-003_DDR_2015-09-04', page: 1 })
    expect(resolveRef(docs, 'DUL-003_DDR_2015-09-04')).toMatchObject({ document_id: 'DUL-003_DDR_2015-09-04', page: 1 })
    expect(resolveRef(docs, 'nothing here')).toBeNull()
  })
})
