"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import {
  useEffect,
  useRef,
  useSyncExternalStore,
  type CSSProperties,
} from "react";

// The Spline runtime is WebGL + a multi-megabyte bundle, so it must never be
// server-rendered. `ssr: false` is only legal inside a Client Component.
const GalaxyHeroBackground = dynamic(
  () => import("@/components/ui/galaxy-hero-background"),
  { ssr: false },
);

const TITLE_WORDS = ["Build", "Your", "Second", "Brain"];
const SUBTITLE =
  "One knowledge base for every document, sheet, and idea you have.";

const REDUCED = "(prefers-reduced-motion: reduce)";
const WIDE = "(min-width: 48rem)"; // Tailwind md

function subscribe(onChange: () => void) {
  const lists = [REDUCED, WIDE].map((q) => window.matchMedia(q));
  lists.forEach((l) => l.addEventListener("change", onChange));
  return () => lists.forEach((l) => l.removeEventListener("change", onChange));
}

function hasWebGL() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(
      canvas.getContext("webgl2") ?? canvas.getContext("webgl"),
    );
  } catch {
    return false;
  }
}

function canRunSpline() {
  const reduced = window.matchMedia(REDUCED).matches;
  const wide = window.matchMedia(WIDE).matches;
  // Phones fall back to the static hero rather than downloading the Spline
  // runtime and rendering a continuous WebGL scene — a decorative background
  // has not earned that much data or battery.
  return !reduced && wide && hasWebGL();
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" && window.matchMedia(REDUCED).matches
  );
}

export function Hero() {
  // Server always renders the static hero; the client upgrades if the machine
  // can afford it. Re-evaluates on resize and on motion-preference change.
  const spline = useSyncExternalStore(subscribe, canRunSpline, () => false);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = contentRef.current;
    if (!el || prefersReducedMotion()) return;

    // Hero copy drifts up and dissolves as the storage section takes over, so
    // the two sections read as one continuous move rather than a hard cut.
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const p = Math.min(window.scrollY / 420, 1);
        el.style.opacity = String(1 - p);
        el.style.transform = `translate3d(0, ${p * -48}px, 0)`;
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  const delay = (i: number) => ({ "--delay": `${i * 0.13}s` }) as CSSProperties;

  return (
    <section className="relative isolate min-h-svh overflow-hidden bg-white text-neutral-950">
      <div className="absolute inset-0 -z-20">
        {spline ? (
          <GalaxyHeroBackground />
        ) : (
          <div className="hero-static h-full w-full" />
        )}
      </div>

      {/* Edge falloff, plus a bottom fade that hands off to the storage section
          below. Also guarantees text contrast regardless of how dense the
          particles happen to be behind the copy. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 bg-[linear-gradient(to_right,rgba(255,255,255,0.92),rgba(255,255,255,0.4)_38%,transparent_65%),linear-gradient(to_bottom,rgba(255,255,255,0.55),transparent_35%,rgba(255,255,255,0.96))]"
      />

      {/* pointer-events-none so the Spline scene stays interactive underneath;
          the CTA re-enables them for itself. */}
      <div
        ref={contentRef}
        className="pointer-events-none relative mx-auto flex min-h-svh max-w-6xl flex-col justify-center px-6 will-change-[opacity,transform] sm:px-10"
      >
        {/* Word gap in em, not rem: a fixed 1rem reads as justified text at
            mobile sizes and too tight at 7xl. */}
        <h1 className="landing-title flex max-w-3xl flex-wrap gap-x-[0.26em] gap-y-1 text-4xl font-bold tracking-tight sm:text-6xl xl:text-7xl">
          {TITLE_WORDS.map((word, i) => (
            <span key={word} style={delay(i)}>
              {word}
            </span>
          ))}
        </h1>

        <p
          className="landing-reveal mt-6 max-w-lg text-base text-balance text-neutral-600 sm:text-xl"
          style={delay(TITLE_WORDS.length)}
        >
          {SUBTITLE}
        </p>

        <div className="landing-reveal mt-10" style={delay(TITLE_WORDS.length + 1)}>
          <Link
            href="/dashboard/chat"
            className="pointer-events-auto inline-flex items-center rounded-full bg-neutral-950 px-7 py-3 text-sm font-semibold text-white transition-transform duration-300 hover:scale-[1.04] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-neutral-950"
          >
            Let&apos;s start
          </Link>
        </div>
      </div>

      <a
        href="#how-its-stored"
        className="landing-reveal absolute inset-x-0 bottom-8 z-10 mx-auto flex w-fit flex-col items-center gap-1 text-xs tracking-widest text-neutral-400 uppercase transition-colors hover:text-neutral-700"
        style={delay(TITLE_WORDS.length + 3)}
      >
        Scroll to explore
        <svg
          className="landing-arrow"
          width="22"
          height="22"
          viewBox="0 0 22 22"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="M11 5V17"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
          <path
            d="M6 12L11 17L16 12"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      </a>
    </section>
  );
}
