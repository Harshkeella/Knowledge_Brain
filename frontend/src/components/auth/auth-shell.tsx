"use client";

import Image from "next/image";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

// A canvas animating on every frame inside an iframe: never server-rendered,
// and never downloaded at all on a device that will not show it.
const GlobeStudy = dynamic(() => import("@/components/ui/globe-study"), {
  ssr: false,
  loading: () => null,
});

const REDUCED = "(prefers-reduced-motion: reduce)";

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(REDUCED);
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

/**
 * The visual half of every auth page: a black globe standing on white, lit
 * from behind in pink, purple and blue.
 *
 * The colour is not painted into the globe -- the study renders near-black
 * type on a transparent surface, and the gradients below show through its sea
 * and the space around the sphere. That keeps the supplied component's
 * behaviour untouched and the accent palette adjustable in CSS.
 */
function GlobeVisual() {
  const reduced = usePrefersReducedMotion();

  return (
    <div className="relative isolate hidden aspect-square w-full max-w-[34rem] select-none lg:block">
      {/* The energy. Three soft lobes, deliberately low-opacity: the globe is
          the subject and this is the light behind it, not a neon backdrop. */}
      <div
        aria-hidden
        className="absolute inset-0 -z-10 rounded-full blur-3xl"
        style={{
          background:
            "radial-gradient(38% 38% at 30% 28%, rgba(168,85,247,.34), transparent 70%)," +
            "radial-gradient(40% 40% at 72% 38%, rgba(59,130,246,.30), transparent 70%)," +
            "radial-gradient(42% 42% at 50% 78%, rgba(236,72,153,.30), transparent 72%)",
        }}
      />
      <div
        aria-hidden
        className="absolute inset-[14%] -z-10 rounded-full opacity-70 blur-2xl"
        style={{
          background:
            "conic-gradient(from 210deg, rgba(236,72,153,.22), rgba(168,85,247,.28), rgba(59,130,246,.22), rgba(236,72,153,.22))",
        }}
      />

      {reduced ? (
        // Reduced motion gets the same composition without the animation: a
        // still sphere, not a blank column that shifts the whole layout.
        <div
          aria-hidden
          className="absolute inset-[8%] rounded-full border border-neutral-900/10 bg-neutral-950/[.04]"
        />
      ) : (
        <GlobeStudy mode="light" surface="transparent" className="absolute inset-0" />
      )}
    </div>
  );
}

/**
 * Shared frame for sign in / sign up / password reset.
 *
 * On small screens the globe is dropped entirely rather than stacked above the
 * form: a decorative sphere must never push the password field below the fold.
 */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-white text-neutral-950">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-6 py-8 sm:px-8">
        <Link href="/" className="flex w-fit items-center gap-2 font-semibold">
          <Image
            src="/logo.png"
            alt=""
            width={28}
            height={28}
            priority
            className="rounded-md"
          />
          nodeRels
        </Link>

        <div className="grid flex-1 items-center gap-12 py-10 lg:grid-cols-2 lg:gap-16">
          <div className="order-2 flex justify-center lg:order-1">
            <GlobeVisual />
          </div>

          <div className="order-1 mx-auto w-full max-w-sm lg:order-2">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              {title}
            </h1>
            <p className="mt-2 text-sm text-neutral-500">{subtitle}</p>
            <div className="mt-8">{children}</div>
            {footer ? (
              <div className="mt-6 text-sm text-neutral-500">{footer}</div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
