import { describe, expect, it } from 'vitest'
import type { Element, ElementContent, Root } from 'hast'
import type { EpisodeEntity } from '../../api/types'
import { buildEntityTermIndex } from './entityTerms'
import { rehypeEntityMentions } from './rehypeEntityMentions'

function entity(id: string, name: string): EpisodeEntity {
  return {
    entity: { id, type: 'person', canonical_name: name, wikidata_qid: null },
    mention_count: 1,
    first_mention_ms: 0,
    speaker_kind: 'unknown',
    salience: 1,
    mentions: [],
  }
}

const INDEX = buildEntityTermIndex([entity('person:zach', 'Zach Lloyd'), entity('company:warp', 'Warp')])

function el(tagName: string, children: ElementContent[], properties: Element['properties'] = {}): Element {
  return { type: 'element', tagName, properties, children }
}
function text(value: string): ElementContent {
  return { type: 'text', value }
}
function cite(id: string, label = '12:34'): Element {
  return el('a', [text(label)], { href: `?t=754&cite=${id}` })
}
function root(...children: Element[]): Root {
  return { type: 'root', children }
}

// A compact print of the tree: `text|span(entity,cite)|…`.
function flat(node: Root | Element): string {
  return node.children
    .map((c) => {
      if (c.type === 'text') return c.value
      if (c.type !== 'element') return ''
      if (c.tagName === 'span' && c.properties['data-entity-id']) {
        return `[${c.properties['data-term']}→${c.properties['data-entity-id']}${
          c.properties['data-cite-id'] ? `@${c.properties['data-cite-id']}` : ''
        }]`
      }
      return `<${c.tagName}>${flat(c)}</${c.tagName}>`
    })
    .join('')
}

function run(tree: Root): Root {
  rehypeEntityMentions(INDEX)(tree)
  return tree
}

describe('rehypeEntityMentions', () => {
  it('splits a text node around each match', () => {
    const tree = run(root(el('p', [text('Zach Lloyd, CEO of Warp, on Warp.')])))
    expect(flat(tree)).toBe('<p>[Zach Lloyd→person:zach], CEO of [Warp→company:warp], on [Warp→company:warp].</p>')
  })

  it('leaves links, code and headings alone', () => {
    const tree = run(
      root(
        el('h2', [text('Zach Lloyd on Warp')]),
        el('p', [el('a', [text('Warp')], { href: 'https://warp.dev' }), text(' and '), el('code', [text('Warp')])]),
        el('pre', [el('code', [text('Warp')])]),
      ),
    )
    expect(flat(tree)).toBe('<h2>Zach Lloyd on Warp</h2><p><a>Warp</a> and <code>Warp</code></p><pre><code>Warp</code></pre>')
  })

  it('reaches text nested in inline formatting inside a list item', () => {
    const tree = run(root(el('ul', [el('li', [el('p', [el('strong', [text('Warp')]), text(' by Zach Lloyd')])])])))
    expect(flat(tree)).toBe('<ul><li><p><strong>[Warp→company:warp]</strong> by [Zach Lloyd→person:zach]</p></li></ul>')
  })

  it('borrows the nearest citation in the block, before or after', () => {
    const tree = run(
      root(
        el('li', [cite('c0'), text(' '), el('strong', [text('Warp:')]), text(' Zach Lloyd explains.')]),
        el('li', [text('Warp is a terminal, says Zach Lloyd. '), cite('c5')]),
        el('li', [cite('c1'), text(' Warp … a long stretch of text … Zach Lloyd '), cite('c2')]),
      ),
    )
    expect(flat(tree)).toBe(
      '<li><a>12:34</a> <strong>[Warp→company:warp@c0]:</strong> [Zach Lloyd→person:zach@c0] explains.</li>' +
        '<li>[Warp→company:warp@c5] is a terminal, says [Zach Lloyd→person:zach@c5]. <a>12:34</a></li>' +
        '<li><a>12:34</a> [Warp→company:warp@c1] … a long stretch of text … [Zach Lloyd→person:zach@c2] <a>12:34</a></li>',
    )
  })

  it('omits the citation when the block has none, and links inside blockquotes', () => {
    const tree = run(root(el('blockquote', [el('p', [text('"Warp is fast" — Zach Lloyd')])])))
    expect(flat(tree)).toBe('<blockquote><p>"[Warp→company:warp] is fast" — [Zach Lloyd→person:zach]</p></blockquote>')
  })

  it('keeps a nested list out of its parent item, and links table cells', () => {
    const tree = run(
      root(
        el('li', [
          text('Outer '),
          cite('c0'),
          el('ul', [el('li', [text('Inner Warp '), cite('c9')])]),
        ]),
        el('table', [el('tr', [el('td', [text('Zach Lloyd')])])]),
      ),
    )
    expect(flat(tree)).toBe(
      '<li>Outer <a>12:34</a><ul><li>Inner [Warp→company:warp@c9] <a>12:34</a></li></ul></li>' +
        '<table><tr><td>[Zach Lloyd→person:zach]</td></tr></table>',
    )
  })

  it('handles several matchable text nodes under one parent', () => {
    const tree = run(root(el('p', [text('Warp '), el('em', [text('and')]), text(' Warp')])))
    expect(flat(tree)).toBe('<p>[Warp→company:warp] <em>and</em> [Warp→company:warp]</p>')
  })
})
