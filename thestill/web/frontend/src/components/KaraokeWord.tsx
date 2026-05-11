import { memo } from 'react'
import type { WordTimestamp } from '../api/types'

interface Props {
  word: WordTimestamp
  // True for words the audio has reached or passed (inclusive of the
  // currently-spoken word). The visual is purely a text-colour swap —
  // read words use the body colour, unread words use a muted tint that
  // reads as "still to come".
  read: boolean
  // Marks the word currently being spoken for assistive tech. Visually
  // indistinguishable from other read words by design — the spec is
  // explicit that the active word should not be more emphasised than
  // the rest of the read run.
  isActive: boolean
}

export const KaraokeWord = memo(function KaraokeWord({ word, read, isActive }: Props) {
  return (
    <span
      data-karaoke-word=""
      aria-current={isActive ? 'true' : undefined}
      className={read ? 'text-gray-900' : 'text-gray-400'}
    >
      {word.w}
    </span>
  )
})

export default KaraokeWord
