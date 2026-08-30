/**
 * Icons for the canvas renderer.
 *
 * The legend can drop a lucide component into JSX; a canvas cannot. `ctx.arc`
 * and `ctx.fillStyle` paint the dot, and nothing paints the glyph -- which is
 * why the symbols only ever showed up in the legend. Canvas can draw an
 * *image*, so each icon is rasterized once from lucide's own geometry and
 * cached, then blitted onto every node of that type.
 *
 * Rasterized rather than stroked path-by-path with Path2D: an ImageBitmap is
 * one drawImage call per node instead of a dozen stroke calls, which matters
 * when the force layout is repainting hundreds of nodes per frame.
 */

import { SUBNODE_SYMBOL, SUPERNODE_SYMBOLS, SYMBOLS, type IconNode } from "@/constants/symbols";

/** Off below this on-screen node radius: a glyph under ~7px is visual noise. */
export const ICON_MIN_RADIUS_PX = 7;
/** Off below this zoom, regardless of radius -- a dense cluster stays dots. */
export const ICON_MIN_ZOOM = 1.1;
/** Rasterization size. 2x the largest on-screen draw, so it stays crisp. */
const RASTER_PX = 64;

const cache = new Map<string, HTMLImageElement>();

function toSvg(iconNode: IconNode): string {
  const body = iconNode
    .map(([tag, attrs]) => {
      const props = Object.entries(attrs)
        .filter(([name]) => name !== "key")
        .map(([name, value]) => `${name}="${value}"`)
        .join(" ");
      return `<${tag} ${props}/>`;
    })
    .join("");
  // White, and thicker than lucide's default 2 -- at 12px on a coloured disc a
  // hairline outline disappears.
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${RASTER_PX}" height="${RASTER_PX}" ` +
    `viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" ` +
    `stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
  );
}

/**
 * The rasterized icon for a symbol key, or null until it has decoded.
 *
 * ponytail: decoding is async and the first frames after mount may skip the
 * glyph. Data URIs decode in a frame or two while the force layout is still
 * settling, so it is never visible -- swap to createImageBitmap + an explicit
 * await if a static graph ever renders before the cache is warm.
 */
export function iconImage(key: string, iconNode: IconNode): HTMLImageElement | null {
  let img = cache.get(key);
  if (!img) {
    img = new Image(RASTER_PX, RASTER_PX);
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(toSvg(iconNode))}`;
    cache.set(key, img);
  }
  return img.complete && img.naturalWidth > 0 ? img : null;
}

/** Decode every icon up front so the first paint already has them. */
export function warmIconCache(): void {
  if (typeof window === "undefined") return;
  for (const spec of [
    ...Object.values(SYMBOLS),
    ...Object.values(SUPERNODE_SYMBOLS),
    SUBNODE_SYMBOL,
  ]) {
    iconImage(spec.key, spec.iconNode);
  }
}
