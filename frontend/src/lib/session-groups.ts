/**
 * Recency buckets for the chat sidebar.
 *
 * Lives on the client, not the API: the buckets are relative to the *viewer's*
 * midnight, so a server that grouped them would be baking one timezone into
 * the response.
 */

export const GROUP_ORDER = [
  "Today",
  "Yesterday",
  "Previous 7 Days",
  "Older",
] as const;

export type GroupName = (typeof GROUP_ORDER)[number];

export interface Grouped<T> {
  name: GroupName;
  items: T[];
}

const DAY = 86_400_000;

export function groupByRecency<T extends { updated_at: string }>(
  items: T[],
  now: number = Date.now()
): Grouped<T>[] {
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const today = midnight.getTime();

  const buckets = new Map<GroupName, T[]>(GROUP_ORDER.map((g) => [g, []]));

  for (const item of items) {
    const at = new Date(item.updated_at).getTime();
    // An unparseable timestamp still has to land somewhere rather than
    // vanishing from the sidebar.
    const name: GroupName = Number.isNaN(at)
      ? "Older"
      : at >= today
        ? "Today"
        : at >= today - DAY
          ? "Yesterday"
          : at >= now - 7 * DAY
            ? "Previous 7 Days"
            : "Older";
    buckets.get(name)!.push(item);
  }

  return GROUP_ORDER.map((name) => ({ name, items: buckets.get(name)! })).filter(
    (g) => g.items.length > 0
  );
}
