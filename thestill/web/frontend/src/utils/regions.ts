// Region presentation helpers shared between the Top Podcasts page and the
// Add Podcast modal. Regions are stored as ISO 3166-1 alpha-2 lowercase codes;
// only the display side cares about flags, so the map lives here in /utils
// rather than next to the data layer.

const FLAG: Record<string, string> = {
  us: '🇺🇸',
  gb: '🇬🇧',
  ca: '🇨🇦',
  au: '🇦🇺',
  ie: '🇮🇪',
  de: '🇩🇪',
  fr: '🇫🇷',
  es: '🇪🇸',
  it: '🇮🇹',
  nl: '🇳🇱',
  se: '🇸🇪',
  no: '🇳🇴',
  dk: '🇩🇰',
  fi: '🇫🇮',
  jp: '🇯🇵',
  br: '🇧🇷',
  mx: '🇲🇽',
  in: '🇮🇳',
}

export function flagFor(code: string): string {
  return FLAG[code] ?? '🌐'
}
