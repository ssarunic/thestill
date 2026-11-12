/**
 * Spec #116 §6.1 — shown beside the time in the mini player and the Now
 * Playing sheet while an ad that only the listener's copy carries is
 * playing. The transcript has no words for it, so the clock stands still;
 * the tag says why. It marks the ad and offers no skip.
 */
export default function AdTag({ className = '' }: { className?: string }) {
  return (
    <span
      data-testid="player-ad-tag"
      title="An ad in your copy of the audio is playing"
      className={`inline-flex shrink-0 items-center rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase leading-none tracking-wider text-amber-800 ${className}`}
    >
      Ad
    </span>
  )
}
