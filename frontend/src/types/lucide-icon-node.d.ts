/**
 * lucide-react ships each icon's raw geometry alongside the component, as
 * `__iconNode`. The canvas renderer needs that geometry (an SVG component
 * cannot be drawn with ctx.arc), and taking it from lucide itself is what
 * keeps the glyph on a node identical to the glyph in the legend -- a
 * hand-copied path table would drift the first time lucide redraws an icon.
 *
 * The package publishes no `exports` map, so the deep import resolves. If a
 * future version renames `__iconNode` this fails loudly at build time, which
 * is the failure mode to want.
 */
declare module "lucide-react/dist/esm/icons/*.mjs" {
  export const __iconNode: [string, Record<string, string | number>][];
}
