/**
 * The symbol taxonomy: one icon (and, for the structural labels, one fixed
 * colour) per node type. The single source of truth for both consumers --
 * the legend/panels, which want a React component, and the graph canvas,
 * which wants raw geometry.
 *
 * Keys are the backend's `entity_type` values verbatim -- `graph_schema.py`
 * runs every label through `canonical_label()`, which lowercases and strips
 * separators, so `CodeFile` arrives here as `codefile`. Keep this table in the
 * same order as the taxonomy table in the build doc so the two can be diffed.
 *
 * Nothing is stored on the node itself. `entity_type` already determines the
 * icon, so a `symbol` property would be a denormalised copy of a pure function
 * -- one that drifts the first time a label is added on one side only. This is
 * the single source of truth; the backend has no counterpart to keep in sync.
 *
 * `iconNode` is lucide's own geometry for the same icon, taken from lucide
 * rather than hand-copied so a node's glyph can never diverge from the
 * legend's. See `src/types/lucide-icon-node.d.ts`.
 *
 * Icon names are lucide-react v1's, which renamed several of the v0 names:
 * PlayCircle -> CirclePlay, FileCode2 -> FileCode, FunctionSquare ->
 * SquareFunction.
 */

import {
  Box,
  Building2,
  Calendar,
  CalendarClock,
  Circle,
  CirclePlay,
  Columns3,
  Cpu,
  ExternalLink,
  FileCode,
  FileText,
  FileType,
  Folder,
  FolderCog,
  Globe,
  Image as ImageIcon,
  Lightbulb,
  MapPin,
  Package,
  Sheet,
  SquareFunction,
  Table2,
  User,
  type LucideIcon,
} from "lucide-react";

import { __iconNode as boxNode } from "lucide-react/dist/esm/icons/box.mjs";
import { __iconNode as building2Node } from "lucide-react/dist/esm/icons/building-2.mjs";
import { __iconNode as calendarNode } from "lucide-react/dist/esm/icons/calendar.mjs";
import { __iconNode as calendarClockNode } from "lucide-react/dist/esm/icons/calendar-clock.mjs";
import { __iconNode as circleNode } from "lucide-react/dist/esm/icons/circle.mjs";
import { __iconNode as circlePlayNode } from "lucide-react/dist/esm/icons/circle-play.mjs";
import { __iconNode as columns3Node } from "lucide-react/dist/esm/icons/columns-3.mjs";
import { __iconNode as cpuNode } from "lucide-react/dist/esm/icons/cpu.mjs";
import { __iconNode as externalLinkNode } from "lucide-react/dist/esm/icons/external-link.mjs";
import { __iconNode as fileCodeNode } from "lucide-react/dist/esm/icons/file-code.mjs";
import { __iconNode as fileTextNode } from "lucide-react/dist/esm/icons/file-text.mjs";
import { __iconNode as fileTypeNode } from "lucide-react/dist/esm/icons/file-type.mjs";
import { __iconNode as folderNode } from "lucide-react/dist/esm/icons/folder.mjs";
import { __iconNode as folderCogNode } from "lucide-react/dist/esm/icons/folder-cog.mjs";
import { __iconNode as globeNode } from "lucide-react/dist/esm/icons/globe.mjs";
import { __iconNode as imageNode } from "lucide-react/dist/esm/icons/image.mjs";
import { __iconNode as lightbulbNode } from "lucide-react/dist/esm/icons/lightbulb.mjs";
import { __iconNode as mapPinNode } from "lucide-react/dist/esm/icons/map-pin.mjs";
import { __iconNode as packageNode } from "lucide-react/dist/esm/icons/package.mjs";
import { __iconNode as sheetNode } from "lucide-react/dist/esm/icons/sheet.mjs";
import { __iconNode as squareFunctionNode } from "lucide-react/dist/esm/icons/square-function.mjs";
import { __iconNode as table2Node } from "lucide-react/dist/esm/icons/table-2.mjs";
import { __iconNode as userNode } from "lucide-react/dist/esm/icons/user.mjs";

export type IconNode = [string, Record<string, string | number>][];

export interface SymbolSpec {
  /** Legend key, stable across the app. */
  key: string;
  /** Human label shown in the legend. */
  label: string;
  icon: LucideIcon;
  /** The same icon's geometry, for the canvas renderer. */
  iconNode: IconNode;
  /**
   * Fixed colour, light/dark. Only the structural labels this feature added
   * take one -- document and tabular entity types stay on the frequency-ranked
   * categorical palette they already use, so existing graphs render unchanged.
   */
  light?: string;
  dark?: string;
}

const SUPERNODE_BLUE = { light: "#1b4f9c", dark: "#6aa9f5" };

/** Supernode variants, keyed by the Source node's `source_type` property. */
export const SUPERNODE_SYMBOLS: Record<string, SymbolSpec> = {
  folder: { key: "supernode-folder", label: "Folder source", icon: FolderCog, iconNode: folderCogNode, ...SUPERNODE_BLUE },
  web: { key: "supernode-web", label: "Web source", icon: Globe, iconNode: globeNode, ...SUPERNODE_BLUE },
  media: { key: "supernode-media", label: "Media source", icon: CirclePlay, iconNode: circlePlayNode, ...SUPERNODE_BLUE },
  pdf: { key: "supernode-pdf", label: "PDF source", icon: FileText, iconNode: fileTextNode, ...SUPERNODE_BLUE },
  doc: { key: "supernode-doc", label: "Document source", icon: FileType, iconNode: fileTypeNode, ...SUPERNODE_BLUE },
};

/** Backend `source_type` -> which supernode variant to draw. */
const SUPERNODE_BY_SOURCE_TYPE: Record<string, keyof typeof SUPERNODE_SYMBOLS> = {
  folder: "folder",
  article: "web",
  article_zenrows: "web",
  article_clipper: "web",
  youtube: "media",
  pdf: "pdf",
  markdown: "doc",
  text: "doc",
  paste: "doc",
  spreadsheet: "doc",
};

/** Node types, keyed by `entity_type`. */
export const SYMBOLS: Record<string, SymbolSpec> = {
  // Structural labels: fixed colours, because a taxonomy whose colours move
  // between graph loads is not a taxonomy.
  source: SUPERNODE_SYMBOLS.doc,
  folder: { key: "folder", label: "Folder", icon: Folder, iconNode: folderNode, light: "#2a78d6", dark: "#4e9bf0" },
  file: { key: "file", label: "File", icon: FileText, iconNode: fileTextNode, light: "#17916a", dark: "#34c79b" },
  codefile: { key: "file-code", label: "Code file", icon: FileCode, iconNode: fileCodeNode, light: "#0d7a7a", dark: "#2fb8b8" },
  class: { key: "class", label: "Class", icon: Box, iconNode: boxNode, light: "#4a3aa7", dark: "#9085e9" },
  function: { key: "function", label: "Function", icon: SquareFunction, iconNode: squareFunctionNode, light: "#7b4fc9", dark: "#b79cf5" },
  method: { key: "function", label: "Method", icon: SquareFunction, iconNode: squareFunctionNode, light: "#7b4fc9", dark: "#b79cf5" },
  externalsymbol: { key: "external", label: "External symbol", icon: ExternalLink, iconNode: externalLinkNode, light: "#898781", dark: "#a3a199" },
  image: { key: "image", label: "Image", icon: ImageIcon, iconNode: imageNode, light: "#c2571f", dark: "#e88a52" },
  video: { key: "media", label: "Video", icon: CirclePlay, iconNode: circlePlayNode, light: "#7c3aed", dark: "#a78bfa" },

  // Tabular and document labels: icon only. Giving these fixed colours would
  // repaint every graph that already has one of them in it.
  workbook: { key: "workbook", label: "Workbook", icon: Sheet, iconNode: sheetNode },
  worksheet: { key: "worksheet", label: "Worksheet", icon: Table2, iconNode: table2Node },
  column: { key: "column", label: "Column", icon: Columns3, iconNode: columns3Node },

  // The GLiNER ontology (ENTITY_LABELS). Every one needs an icon too -- a
  // taxonomy that only covers the new node types is half a taxonomy.
  person: { key: "person", label: "Person", icon: User, iconNode: userNode },
  organization: { key: "organization", label: "Organization", icon: Building2, iconNode: building2Node },
  location: { key: "location", label: "Location", icon: MapPin, iconNode: mapPinNode },
  product: { key: "product", label: "Product", icon: Package, iconNode: packageNode },
  technology: { key: "technology", label: "Technology", icon: Cpu, iconNode: cpuNode },
  event: { key: "event", label: "Event", icon: CalendarClock, iconNode: calendarClockNode },
  concept: { key: "concept", label: "Concept", icon: Lightbulb, iconNode: lightbulbNode },
  date: { key: "date", label: "Date", icon: Calendar, iconNode: calendarNode },
};

/** A label outside the ontology: a dot, and no claim about what it is. */
export const SUBNODE_SYMBOL: SymbolSpec = {
  key: "subnode",
  label: "Entity",
  icon: Circle,
  iconNode: circleNode,
};

/** Which labels the "code only" view keeps. Mirrors gs.CODE_LABELS. */
export const CODE_ENTITY_TYPES = new Set([
  "source",
  "folder",
  "codefile",
  "class",
  "function",
  "method",
  "externalsymbol",
]);

export function symbolFor(
  entityType: string | null | undefined,
  sourceType?: string | null
): SymbolSpec | null {
  const key = (entityType ?? "").trim().toLowerCase();
  if (key === "source") {
    const variant = SUPERNODE_BY_SOURCE_TYPE[(sourceType ?? "").trim().toLowerCase()];
    return variant ? SUPERNODE_SYMBOLS[variant] : SUPERNODE_SYMBOLS.doc;
  }
  return SYMBOLS[key] ?? null;
}

/** The fixed colour for a type, or null if it belongs to the frequency scale. */
export function fixedColorFor(
  entityType: string | null | undefined,
  isDark: boolean
): string | null {
  const spec = symbolFor(entityType);
  if (!spec?.light || !spec.dark) return null;
  return isDark ? spec.dark : spec.light;
}
