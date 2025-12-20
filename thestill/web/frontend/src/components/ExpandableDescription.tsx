import { useState, useRef, useEffect } from 'react'
import DOMPurify from 'dompurify'

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
  const [needsTruncation, setNeedsTruncation] = useState(false)
  const [measured, setMeasured] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)

  // Sanitize HTML - allow safe tags only
  const cleanHtml = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'b', 'em', 'i', 'a', 'ul', 'ol', 'li'],
    ALLOWED_ATTR: ['href', 'target', 'rel'],
  })

  useEffect(() => {
    // Reset measurement when content changes
    setMeasured(false)
    setNeedsTruncation(false)
  }, [cleanHtml])

  useEffect(() => {
    if (contentRef.current && !measured) {
      // Measure the full height before applying clamp
      const lineHeight = parseInt(getComputedStyle(contentRef.current).lineHeight) || 24
      const maxHeight = lineHeight * maxLines
      const fullHeight = contentRef.current.scrollHeight

      setNeedsTruncation(fullHeight > maxHeight + 5)
      setMeasured(true)
    }
  }, [cleanHtml, maxLines, measured])

  // Only apply clamp after we've measured and determined truncation is needed
  const shouldClamp = measured && needsTruncation && !isExpanded

  return (
    <div className={className}>
      <div
        ref={contentRef}
        className={`prose prose-sm max-w-none text-gray-600 prose-a:text-primary-600 prose-a:no-underline hover:prose-a:underline ${
          shouldClamp ? 'line-clamp-3' : ''
        }`}
        dangerouslySetInnerHTML={{ __html: cleanHtml }}
      />
      {needsTruncation && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="mt-2 text-sm font-medium text-primary-600 hover:text-primary-700 focus:outline-none"
        >
          {isExpanded ? '← Show less' : 'Show more →'}
        </button>
      )}
    </div>
  )
}
