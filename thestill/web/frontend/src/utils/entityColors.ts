// Spec #28 §5.2 — color tokens for the four entity types.
//
// Spec rule: "Color **by type, not by entity**: four swatches only —
// Person, Company, Product, Topic. Render as a thin type-colored
// underline plus a small dot on hover; never as a filled chip."
//
// Tailwind purges class names it can't statically see. Constructing
// `text-${color}-700` would be stripped, so each property is a fully
// spelled-out class string. If a swatch needs new variants, add them
// here so the JIT picks them up.

import type { EntityType } from '../api/types'

export interface EntityTypeStyle {
  // Inline highlight inside the transcript body. Underline-only; never
  // a background color (spec: no filled chips in the body).
  inlineUnderline: string
  // Strip pill / rail row left-rail accent. The strip pills use a soft
  // tinted background; the rail uses a left-border accent.
  pillBg: string
  pillText: string
  pillBorder: string
  // Small badge / type swatch dot — used in hover cards and rail rows.
  dot: string
  // Single-letter short code rendered in the type-filter toggle and
  // legacy entity badge (P / C / Pr / T).
  shortCode: string
  label: string
}

export const ENTITY_STYLES: Record<EntityType, EntityTypeStyle> = {
  person: {
    inlineUnderline: 'decoration-blue-500 decoration-2 underline-offset-2',
    pillBg: 'bg-blue-50',
    pillText: 'text-blue-800',
    pillBorder: 'border-blue-300',
    dot: 'bg-blue-500',
    shortCode: 'P',
    label: 'Person',
  },
  company: {
    inlineUnderline: 'decoration-emerald-500 decoration-2 underline-offset-2',
    pillBg: 'bg-emerald-50',
    pillText: 'text-emerald-800',
    pillBorder: 'border-emerald-300',
    dot: 'bg-emerald-500',
    shortCode: 'C',
    label: 'Company',
  },
  product: {
    inlineUnderline: 'decoration-violet-500 decoration-2 underline-offset-2',
    pillBg: 'bg-violet-50',
    pillText: 'text-violet-800',
    pillBorder: 'border-violet-300',
    dot: 'bg-violet-500',
    shortCode: 'Pr',
    label: 'Product',
  },
  topic: {
    inlineUnderline: 'decoration-amber-500 decoration-2 underline-offset-2',
    pillBg: 'bg-amber-50',
    pillText: 'text-amber-800',
    pillBorder: 'border-amber-300',
    dot: 'bg-amber-500',
    shortCode: 'T',
    label: 'Topic',
  },
}

export function entityStyle(type: EntityType): EntityTypeStyle {
  return ENTITY_STYLES[type]
}

// Strip the `"{type}:"` prefix from a fully-qualified entity id so it
// can be embedded in a URL path (`/entities/person/elon-musk` rather
// than `/entities/person/person:elon-musk`).
export function entitySlug(entityId: string): string {
  const colon = entityId.indexOf(':')
  return colon === -1 ? entityId : entityId.slice(colon + 1)
}

export function entityHref(type: EntityType, entityId: string): string {
  return `/entities/${type}/${entitySlug(entityId)}`
}

// Confidence floor for inline highlighting (spec §5.2 visual rules:
// "mentions below the extractor confidence threshold or with
// resolution_status='unresolvable' render as plain text"). The
// resolved-status filter happens server-side; this floor is the
// extractor-confidence one. 0.5 matches the ReFinED floor pinned in
// spec §1.13.3 — same threshold everywhere is the simplest contract.
export const INLINE_HIGHLIGHT_CONFIDENCE_FLOOR = 0.5
