"use client";

import Spline from "@splinetool/react-spline";
import { useState } from "react";

// Self-hosted from public/hero/ rather than pulled from prod.spline.design, so
// a CDN outage or a deleted scene cannot blank the hero.
const SCENE = "/hero/galaxy.splinecode";

export default function GalaxyHeroBackground() {
  const [loaded, setLoaded] = useState(false);

  // The scene is authored as glowing particles on an opaque black background,
  // so a white page cannot simply show through it. Inverting turns the glow
  // into ink on paper; the paired hue-rotate puts the magenta/violet back where
  // it started, which a bare invert would otherwise swing to green.
  return (
    <div
      className="h-full w-full transition-opacity duration-[1200ms] ease-out [filter:invert(1)_hue-rotate(180deg)]"
      style={{ opacity: loaded ? 1 : 0 }}
    >
      <Spline
        scene={SCENE}
        onLoad={() => setLoaded(true)}
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  );
}
