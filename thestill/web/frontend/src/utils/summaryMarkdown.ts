// A list item whose text starts with a number and a dot — "- 17. kolovoza
// 1990.: …", the Croatian/German/Nordic ordinal-date style — parses as a
// second, ordered list nested in the bullet, so the item renders as
// "• 17." with the rest of the text pushed onto an indented line.
// Escaping the delimiter ("17\.") keeps the number as plain text. A list
// marker at the start of a line that is not a list item already (a real
// "1. First point" list) is left alone.
const ITEM_LEADING_NUMBER = /^(\s*(?:[-*+]|\d{1,9}[.)])[ \t]+)(\d{1,9})([.)])(?=[ \t])/
const FENCE = /^\s*(`{3,}|~{3,})/

export function escapeListItemLeadingNumbers(markdown: string): string {
  let fence: string | null = null
  return markdown
    .split('\n')
    .map((line) => {
      const marker = FENCE.exec(line)?.[1]
      if (marker) {
        if (fence === null) fence = marker[0]
        else if (marker[0] === fence) fence = null
        return line
      }
      if (fence !== null) return line
      return line.replace(ITEM_LEADING_NUMBER, '$1$2\\$3')
    })
    .join('\n')
}
