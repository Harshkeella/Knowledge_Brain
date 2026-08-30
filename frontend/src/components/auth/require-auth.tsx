"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { setUnauthorizedHandler } from "@/lib/api";

/**
 * Client-side gate on the authenticated area.
 *
 * It is a redirect, not a security boundary: the boundary is the API, which
 * verifies the token on every request and would answer 401 to a page that
 * bypassed this. What this buys is that a signed-out visitor sees the login
 * page instead of a dashboard full of failed requests.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { session, loading, enabled } = useAuth();

  useEffect(() => {
    // A token that expires mid-session shows up as a 401 from the API rather
    // than as an auth event, so the client tells us and we bounce.
    setUnauthorizedHandler(() => router.replace("/login"));
    return () => setUnauthorizedHandler(null);
  }, [router]);

  useEffect(() => {
    if (enabled && !loading && !session) router.replace("/login");
  }, [enabled, loading, session, router]);

  // No Supabase configured: a local single-user backend, where there is
  // nothing to sign in to and the dashboard is the whole app.
  if (!enabled) return <>{children}</>;

  if (loading || !session) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  return <>{children}</>;
}
