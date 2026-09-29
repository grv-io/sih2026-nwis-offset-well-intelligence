// TypeScript port of the archive assistant's scope guard (nwis/search/answer.py:
// _query_kind, _guard_answer). Same rules, same wording, both languages, so the
// static demo answers greetings and off-topic questions exactly like the server.
import type { Answer } from '../types'

export const EXAMPLE_QUESTIONS = [
  'Girujan stuck pipe remedy',
  'Tipam losses LCM',
  'Barail kick mud weight',
  'what happened at 2,400 m near DUL-005',
]

export const HINDI_EXAMPLE = 'Girujan में stuck pipe कैसे छुड़ाया?'

const GREETING_RE = new RegExp(
  '^\\s*(hi+|hello+|hey+|namaste|namaskar|good (morning|afternoon|evening)|thanks?( you)?|thank u|' +
    'ok(ay)?|how are you|who are you|what can you do|help|kya haal|kaise ho)[^\\p{L}\\p{N}_]*$',
  'iu',
)

const DRILLING_LEXICON = new Set([
  'well', 'wells', 'offset', 'drill', 'drilling', 'drilled', 'bit', 'trip', 'tripping', 'pooh', 'rih',
  'mud', 'weight', 'ppg', 'losses', 'loss', 'kick', 'influx', 'gain', 'stuck', 'pipe', 'overpull',
  'torque', 'drag', 'spp', 'pressure', 'overpressure', 'casing', 'cement', 'cementing', 'fishing',
  'fish', 'bha', 'twist', 'instability', 'tight', 'hole', 'caving', 'gas', 'show', 'npt', 'rig',
  'formation', 'depth', 'md', 'tvd', 'top', 'lcm', 'pill', 'jar', 'jarring', 'shut', 'bop',
  'circulate', 'circulated', 'kill', 'returns', 'pit', 'flow', 'rop', 'wob', 'rpm', 'survey',
  'girujan', 'tipam', 'barail', 'kopili', 'sylhet', 'namsang', 'dhekiajuli', 'alluvium', 'basement',
  'duliajan', 'moran', 'naharkatiya', 'assam', 'volve', 'ddr', 'wcr', 'report', 'archive', 'incident',
  'event', 'events', 'problem', 'problems', 'remedy', 'cause', 'hours', 'lost', 'happened', 'history',
  'freed', 'jarred', 'worked', 'spotted', 'pumped', 'fracture', 'shale', 'sand', 'clay', 'coal',
])

// Python's \b is Unicode-aware; spell the word boundary out so Devanagari behaves the same.
const WB_BEFORE = '(?<![\\p{L}\\p{N}_])'
const WB_AFTER = '(?![\\p{L}\\p{N}_])'
const WELL_ID_RE = new RegExp(`${WB_BEFORE}[A-Z]{3}-\\p{Nd}{3}${WB_AFTER}|${WB_BEFORE}15[/_]9-F-\\p{Nd}+`, 'iu')
const DEPTH_RE = new RegExp(`${WB_BEFORE}\\p{Nd}{3,5}\\s*(m|ft|md|tvd)?${WB_AFTER}`, 'iu')

export type GuardKind = 'greeting' | 'off_topic'

/** null = a drilling-archive question; 'greeting' | 'off_topic' otherwise. */
export function queryKind(query: string): GuardKind | null {
  const q = (query ?? '').trim()
  if (!q || GREETING_RE.test(q)) return 'greeting'
  if (WELL_ID_RE.test(q) || DEPTH_RE.test(q)) return null
  const tokens = (q.match(/[A-Za-z]+/g) ?? []).map((t) => t.toLowerCase())
  return tokens.some((t) => DRILLING_LEXICON.has(t)) ? null : 'off_topic'
}

export function guardAnswer(kind: GuardKind, lang: 'en' | 'hi' | null = null): Answer {
  const examples = EXAMPLE_QUESTIONS.slice(0, 3).map((q) => `"${q}"`).join(' · ')
  let en: string, hiLatn: string, hi: string
  if (kind === 'greeting') {
    en = 'Hello. I am the NWIS archive assistant for the drilling desk. I answer questions from the ' +
      `offset-well reports and cite the page for every claim. Try: ${examples}.`
    hiLatn = 'Namaste. Main sirf drilling archive ke sawalon ka jawab deta hoon, har jawab ke saath report ka page.'
    hi = 'नमस्ते। मैं ड्रिलिंग डेस्क का NWIS आर्काइव सहायक हूँ। ऑफ़सेट वेल की रिपोर्ट से जवाब देता हूँ और ' +
      `हर बात के साथ रिपोर्ट का पेज बताता हूँ। पूछकर देखें: "${HINDI_EXAMPLE}" · ${examples}.`
  } else {
    en = 'I only answer questions about the drilling archive (offset-well events, depths, formations, ' +
      `remedies, NPT). Try: ${examples}.`
    hiLatn = 'Main sirf drilling archive ke sawalon ka jawab deta hoon, jaise: Girujan me stuck pipe kaise chhudaya?'
    hi = 'मैं सिर्फ़ ड्रिलिंग आर्काइव के सवालों का जवाब देता हूँ (ऑफ़सेट वेल के इवेंट, डेप्थ, फ़ॉर्मेशन, ' +
      `उपाय, NPT)। जैसे: "${HINDI_EXAMPLE}" · ${examples}.`
  }
  const text = lang === 'hi' ? `${hi}\n${en.split(' Try:')[0]}` : `${en}\n${hiLatn}`
  return { text, citations: [], structured: [], refused: false, degraded: false, guard: kind, suggestions: [...EXAMPLE_QUESTIONS] }
}

/** Reply for an archive question that has no recording in the static demo: say so
 *  plainly and offer the recorded questions. Never a made-up answer. */
export function staticUnknownAnswer(lang: 'en' | 'hi' | null, recorded: string[]): Answer {
  const en = 'This static demo answers the listed example questions; the full system answers any question from the archive.'
  const hi = 'यह स्टैटिक डेमो सिर्फ़ नीचे दिए उदाहरण सवालों का जवाब देता है; पूरा सिस्टम आर्काइव से कोई भी सवाल का जवाब देता है।'
  return {
    text: lang === 'hi' ? `${hi}\n${en}` : en,
    citations: [],
    structured: [],
    refused: false,
    degraded: false,
    guard: 'static_demo',
    suggestions: recorded,
  }
}
