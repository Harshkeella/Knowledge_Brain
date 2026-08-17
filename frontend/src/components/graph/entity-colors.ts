import type { GraphNode } from "@/lib/api";

// Validated categorical palette (dataviz skill, references/palette.md).
// Fixed hue order — do not reorder or cycle. Slots are assigned to entity
// types by frequency once per graph load and then held stable, so filtering
// or hovering never repaints a type's color.
const LIGHT_SLOTS = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#4a3aa7",
  "#e34948",
];
const DARK_SLOTS = [
  "#3987e5",
  "#d95926",
  "#199e70",
  "#c98500",
  "#d55181",
  "#008300",
  "#9085e9",
  "#e66767",
];
const LIGHT_OTHER = "#898781";
const DARK_OTHER = "#898781";

const UNRESOLVED_TYPES = new Set(["", "unknown", "other", "none", "null"]);

function isDarkMode(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.classList.contains("dark");
}

export interface EntityColorScale {
  colorFor(entityType: string | null | undefined): string;
  legend: { type: string; color: string }[];
  otherColor: string;
}

export function buildEntityColorScale(nodes: GraphNode[]): EntityColorScale {
  const slots = isDarkMode() ? DARK_SLOTS : LIGHT_SLOTS;
  const other = isDarkMode() ? DARK_OTHER : LIGHT_OTHER;

  const counts = new Map<string, number>();
  for (const node of nodes) {
    const raw = (node.entity_type ?? "").trim().toLowerCase();
    if (UNRESOLVED_TYPES.has(raw)) continue;
    const key = node.entity_type!.trim();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const assignment = new Map<string, string>();
  ranked.slice(0, slots.length).forEach(([type], i) => {
    assignment.set(type, slots[i]);
  });

  return {
    colorFor(entityType) {
      const raw = (entityType ?? "").trim();
      if (UNRESOLVED_TYPES.has(raw.toLowerCase())) return other;
      return assignment.get(raw) ?? other;
    },
    legend: [
      ...ranked.slice(0, slots.length).map(([type]) => ({
        type,
        color: assignment.get(type)!,
      })),
      ...(ranked.length > slots.length ? [{ type: "Other", color: other }] : []),
    ],
    otherColor: other,
  };
}
