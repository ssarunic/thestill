import type { Element, ElementContent, Root, Text } from 'hast'
import type { EntityTermIndex } from './entityTerms'
import { matchEntityTerms } from './entityTerms'
import { parseCitationId } from '../../utils/citationHref'

// Spec #82 §Stage 2 — a rehype plugin that wraps each entity-term match
// in the summary's text in `span[data-entity-id][data-term]`, leaving
// every other node exactly as it was. The renderer (SummaryEntityMention)
// turns those spans into the transcript's `EntityHighlight`.
//
// The tree is walked block by block (paragraph, list item, table cell).
// Within a block, text under a link, code or heading is never matched —
// the citation buttons are links, and headings are navigation, not
// prose. A nested list inside a list item is its own set of blocks.
//
// Each span also carries `data-cite-id`: the id of the block's nearest
// `?cite=` link, by character distance in either direction. Real
// summaries put the citation before the sentence in the timeline and
// after it in the takeaways, so direction cannot be assumed. The plugin
// only records the id; resolving it to a transcript segment is the
// renderer's job, which has the citations sidecar.

const BLOCK_TAGS = new Set(['p', 'li', 'td', 'th'])
// Under a block: text here is counted for positions but never matched.
const UNMATCHED_TAGS = new Set(['a', 'code', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
// Under a block: a child block context, handed back to the outer walk.
const NESTED_CONTAINER_TAGS = new Set(['ul', 'ol', 'table', 'blockquote'])

interface Landmark {
  citeId: string
  pos: number
}

interface Slot {
  node: Text
  parent: Element
  pos: number
}

interface BlockLayout {
  slots: Slot[]
  landmarks: Landmark[]
  nested: Element[]
}

export function rehypeEntityMentions(index: EntityTermIndex) {
  return function transform(tree: Root): void {
    walk(tree, index)
  }
}

function walk(node: Root | Element, index: EntityTermIndex): void {
  for (const child of node.children) {
    if (child.type !== 'element') continue
    if (BLOCK_TAGS.has(child.tagName)) {
      for (const nested of processBlock(child, index)) walk(nested, index)
    } else {
      walk(child, index)
    }
  }
}

// One pass over the block's phrasing content: where the matchable text
// nodes are, where the citations are, and which children are blocks of
// their own. Positions are character offsets from the block's start,
// counting every text node so a match and a landmark compare directly.
function layoutBlock(block: Element): BlockLayout {
  const layout: BlockLayout = { slots: [], landmarks: [], nested: [] }
  let pos = 0
  function visit(parent: Element, matchable: boolean): void {
    for (const child of parent.children) {
      if (child.type === 'text') {
        if (matchable) layout.slots.push({ node: child, parent, pos })
        pos += child.value.length
      } else if (child.type === 'element') {
        if (NESTED_CONTAINER_TAGS.has(child.tagName)) {
          layout.nested.push(child)
          continue
        }
        if (child.tagName === 'a') {
          const citeId = parseCitationId(
            typeof child.properties.href === 'string' ? child.properties.href : null,
          )
          if (citeId) layout.landmarks.push({ citeId, pos })
        }
        visit(child, matchable && !UNMATCHED_TAGS.has(child.tagName))
      }
    }
  }
  visit(block, true)
  return layout
}

function nearestCitation(landmarks: Landmark[], pos: number): string | undefined {
  let best: Landmark | undefined
  for (const landmark of landmarks) {
    // Ties go to the earlier landmark: a leading citation covers what follows.
    if (!best || Math.abs(landmark.pos - pos) < Math.abs(best.pos - pos)) best = landmark
  }
  return best?.citeId
}

function mentionSpan(entityId: string, text: string, citeId: string | undefined): Element {
  return {
    type: 'element',
    tagName: 'span',
    properties: {
      'data-entity-id': entityId,
      'data-term': text,
      ...(citeId ? { 'data-cite-id': citeId } : {}),
    },
    children: [{ type: 'text', value: text }],
  }
}

// Returns the nested block containers found inside, for the outer walk.
function processBlock(block: Element, index: EntityTermIndex): Element[] {
  const { slots, landmarks, nested } = layoutBlock(block)
  for (const slot of slots) {
    const text = slot.node.value
    const matches = matchEntityTerms(text, index)
    if (matches.length === 0) continue
    const replacement: ElementContent[] = []
    let cursor = 0
    for (const match of matches) {
      if (match.start > cursor) replacement.push({ type: 'text', value: text.slice(cursor, match.start) })
      replacement.push(
        mentionSpan(match.entityId, match.text, nearestCitation(landmarks, slot.pos + match.start)),
      )
      cursor = match.end
    }
    if (cursor < text.length) replacement.push({ type: 'text', value: text.slice(cursor) })
    // Splice by identity: several slots may share a parent, and each
    // splice shifts the indices of the ones after it.
    const at = slot.parent.children.indexOf(slot.node)
    if (at !== -1) slot.parent.children.splice(at, 1, ...replacement)
  }
  return nested
}
