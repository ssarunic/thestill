/**
 * Spec #28 §4.1 — client-side operator parser for the ⌘K bar.
 *
 * The user types `musk after:2025-01-01` and we split that into
 * residual text (`musk`) plus structured filters that flow into the
 * /api/search/quick query string. Server is dumb about operators —
 * it just consumes plain `q` plus structured filters.
 *
 * Supported operators (Phase 4 — anything fancier is post-spec):
 *   person:<name|id>     — narrows the typeahead to person hits
 *                          matching the value, and pre-filters quote
 *                          rows via has_entity if the value already
 *                          looks like a canonical id.
 *   company:<name|id>    — same shape, company entities.
 *   topic:<name|id>      — same shape, topic entities.
 *   podcast:<slug>       — restricts to one podcast by slug. Resolved
 *                          to podcast_id by the caller (we keep the
 *                          slug here; the caller can swap it).
 *   after:YYYY-MM-DD     — date_from filter (inclusive).
 *   before:YYYY-MM-DD    — date_to filter (inclusive).
 *
 * Quoted phrases are preserved as-is in the residual text so the
 * BM25 lexer sees `"hyperscaler capex"`. Operators inside quotes
 * are not parsed (they're just text).
 */

export interface ParsedQuery {
  /** Residual free-text after operators are stripped. May be empty. */
  text: string
  /** Structured filters extracted from operators. All optional. */
  filters: ParsedFilters
  /** Operators we recognised but couldn't action — kept for the UI to surface. */
  hints: OperatorHint[]
}

export interface ParsedFilters {
  /** Person operator value(s) — name or canonical id. */
  person?: string[]
  company?: string[]
  topic?: string[]
  podcast_slug?: string
  date_from?: string
  date_to?: string
}

export interface OperatorHint {
  operator: string
  value: string
  reason: string
}

const OPERATOR_PATTERN = /(person|company|topic|podcast|after|before):("[^"]+"|\S+)/gi
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

export function parseQuery(input: string): ParsedQuery {
  const filters: ParsedFilters = {}
  const hints: OperatorHint[] = []

  // Walk the string, skipping operator matches that fall inside
  // double-quoted phrases. Cheap heuristic: track whether we're
  // currently inside an unmatched quote at the match index.
  const text = input
    .replace(OPERATOR_PATTERN, (match, op: string, raw: string, offset: number) => {
      if (isInsideQuotedPhrase(input, offset)) return match
      const value = stripQuotes(raw)
      const operator = op.toLowerCase()
      switch (operator) {
        case 'person':
        case 'company':
        case 'topic': {
          const list = filters[operator] ?? (filters[operator] = [])
          list.push(value)
          return ''
        }
        case 'podcast':
          filters.podcast_slug = value
          return ''
        case 'after':
          if (!ISO_DATE.test(value)) {
            hints.push({ operator, value, reason: 'expected YYYY-MM-DD' })
            return match
          }
          filters.date_from = value
          return ''
        case 'before':
          if (!ISO_DATE.test(value)) {
            hints.push({ operator, value, reason: 'expected YYYY-MM-DD' })
            return match
          }
          filters.date_to = value
          return ''
        default:
          return match
      }
    })
    .replace(/\s+/g, ' ')
    .trim()

  return { text, filters, hints }
}

function isInsideQuotedPhrase(input: string, offset: number): boolean {
  let inside = false
  for (let i = 0; i < offset; i++) {
    if (input[i] === '"') inside = !inside
  }
  return inside
}

function stripQuotes(value: string): string {
  if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
    return value.slice(1, -1)
  }
  return value
}
