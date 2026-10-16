// Terms and Privacy name the operator through {{key}} placeholders filled
// from public/legal/operator.json, so a change of operator (an individual
// today, a company later) is one JSON edit rather than a rewrite of both
// documents. An unknown key stays visible as {{key}} so a missing value is
// obvious on the page instead of silently blank.
export type LegalOperator = Record<string, string | boolean>

export function fillLegalPlaceholders(markdown: string, operator: LegalOperator): string {
  return markdown.replace(/\{\{\s*([a-z_]+)\s*\}\}/g, (token, key: string) => {
    const value = operator[key]
    return typeof value === 'string' ? value : token
  })
}
