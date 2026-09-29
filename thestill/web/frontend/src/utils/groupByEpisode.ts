/** Rows grouped by episode, episodes in the order of their first row. */
export function groupByEpisode<T extends { episode_id: string }>(rows: T[]): T[][] {
  const groups = new Map<string, T[]>()
  for (const row of rows) {
    const group = groups.get(row.episode_id)
    if (group) group.push(row)
    else groups.set(row.episode_id, [row])
  }
  return [...groups.values()]
}
