/**
 * EpisodeNumber - Displays season/episode numbers in compact format.
 *
 * Formats:
 * - Both: "S1 E5"
 * - Episode only: "E5"
 * - Season only: "S1"
 * - Neither: returns null
 */

interface EpisodeNumberProps {
  seasonNumber?: number | null
  episodeNumber?: number | null
  className?: string
}

export function EpisodeNumber({
  seasonNumber,
  episodeNumber,
  className = '',
}: EpisodeNumberProps) {
  if (!seasonNumber && !episodeNumber) return null

  let label = ''
  if (seasonNumber && episodeNumber) {
    label = `S${seasonNumber} E${episodeNumber}`
  } else if (episodeNumber) {
    label = `E${episodeNumber}`
  } else if (seasonNumber) {
    label = `S${seasonNumber}`
  }

  return (
    <span
      className={`px-1.5 py-0.5 bg-gray-100 text-gray-600 text-xs font-mono rounded flex-shrink-0 ${className}`}
    >
      {label}
    </span>
  )
}
