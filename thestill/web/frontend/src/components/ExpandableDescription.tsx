import { useState, useRef, useEffect } from 'react'
import { sanitizeUntrustedHtml } from '../utils/sanitize'

interface ExpandableDescriptionProps {
  html: string
  maxLines?: number
  className?: string
}

export default function ExpandableDescription({
  html,
  maxLines = 3,
  className = '',
}: ExpandableDescriptionProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  // Tagged with the HTML it was taken from, so new content is unmeasured
  // by construction — no reset step to forget.
  const [measurement, setMeasurement] = useState<{ html: string; needsTruncation: boolean } | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  // Check if content has HTML tags (excluding just whitespace/newlines)
  const hasHtmlTags = /<[a-z][\s\S]*>/i.test(html)

  // If no HTML tags, convert newlines to <br> for proper rendering
  const processedHtml = hasHtmlTags
    ? html
    : html.replace(/\n\n+/g, '<br><br>').replace(/\n/g, '<br>')

  // Sanitize via the shared helper — strict allowlist + forced
  // noopener/noreferrer on links (spec #25 item 3.2).
  const cleanHtml = sanitizeUntrustedHtml(processedHtml)

  const measured = measurement?.html === cleanHtml
  const needsTruncation = measured && measurement.needsTruncation

  useEffect(() => {
    if (contentRef.current && !measured) {
      // Measure the full height before applying clamp
      const lineHeight = parseInt(getComputedStyle(contentRef.current).lineHeight) || 24
      const maxHeight = lineHeight * maxLines
      const fullHeight = contentRef.current.scrollHeight

      setMeasurement({ html: cleanHtml, needsTruncation: fullHeight > maxHeight + 5 })
    }
  }, [cleanHtml, maxLines, measured])

  // Only apply clamp after we've measured and determined truncation is needed
  const shouldClamp = measured && needsTruncation && !isExpanded

  const toggle = () => {
    const collapsing = isExpanded
    setIsExpanded(!isExpanded)
    // Collapsing a long description from its far end would otherwise leave
    // the reader scrolled past it, looking at whatever slid up underneath.
    // Next frame, so the scroll lands on the collapsed box, not the tall one.
    if (collapsing && rootRef.current && rootRef.current.getBoundingClientRect().top < 0) {
      requestAnimationFrame(() => rootRef.current?.scrollIntoView({ block: 'center' }))
    }
  }

  // The whole text toggles, so a long description does not need a trip to
  // "Less" — the button stays as the keyboard and screen-reader control.
  // Links keep working, and a click that ends a text selection is not a toggle.
  const handleTextClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!needsTruncation) return
    if ((e.target as HTMLElement).closest('a')) return
    if (!window.getSelection()?.isCollapsed) return
    toggle()
  }

  return (
    <div ref={rootRef} className={className}>
      <div
        ref={contentRef}
        onClick={handleTextClick}
        className={`prose prose-sm max-w-none text-gray-600 prose-a:text-primary-600 prose-a:no-underline hover:prose-a:underline ${
          shouldClamp ? 'line-clamp-3' : ''
        } ${needsTruncation ? 'cursor-pointer' : ''}`}
        dangerouslySetInnerHTML={{ __html: cleanHtml }}
      />
      {needsTruncation && (
        <button
          type="button"
          aria-expanded={isExpanded}
          onClick={toggle}
          className="mt-2 text-sm font-medium text-primary-600 hover:text-primary-700 focus:outline-none"
        >
          {isExpanded ? 'Less' : 'More'}
        </button>
      )}
    </div>
  )
}
