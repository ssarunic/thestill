// Region presentation helpers shared between the Top Podcasts page and the
// Add Podcast modal. Regions are stored as ISO 3166-1 alpha-2 lowercase codes;
// only the display side cares about flags, so the map lives here in /utils
// rather than next to the data layer.

// Keep this covering the same codes as the REGIONS list in pages/Settings.tsx
// (FM-6: the two must not drift). Liechtenstein (li) is intentionally absent —
// Apple ships no Liechtenstein storefront chart.
const FLAG: Record<string, string> = {
  // Primary anchors.
  us: '🇺🇸',
  gb: '🇬🇧',
  // EEA (EU 27 + Iceland, Norway), alphabetical by country name.
  at: '🇦🇹',
  be: '🇧🇪',
  bg: '🇧🇬',
  hr: '🇭🇷',
  cy: '🇨🇾',
  cz: '🇨🇿',
  dk: '🇩🇰',
  ee: '🇪🇪',
  fi: '🇫🇮',
  fr: '🇫🇷',
  de: '🇩🇪',
  gr: '🇬🇷',
  hu: '🇭🇺',
  is: '🇮🇸',
  ie: '🇮🇪',
  it: '🇮🇹',
  lv: '🇱🇻',
  lt: '🇱🇹',
  lu: '🇱🇺',
  mt: '🇲🇹',
  nl: '🇳🇱',
  no: '🇳🇴',
  pl: '🇵🇱',
  pt: '🇵🇹',
  ro: '🇷🇴',
  sk: '🇸🇰',
  si: '🇸🇮',
  es: '🇪🇸',
  se: '🇸🇪',
  // Other common markets.
  ca: '🇨🇦',
  au: '🇦🇺',
  jp: '🇯🇵',
  br: '🇧🇷',
  mx: '🇲🇽',
  in: '🇮🇳',
}

export function flagFor(code: string): string {
  return FLAG[code] ?? '🌐'
}
