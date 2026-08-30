"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { getSupabase } from "@/lib/supabase";

/**
 * Where Google (and the email-confirmation link) come back to.
 *
 * The Supabase browser client parses the code out of the URL and exchanges it
 * for a session on load, so this page's only job is to wait for that and then
 * get out of the way. Nothing secret passes through here -- the OAuth client
 * secret stays in the Supabase project.
 */
export default function AuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    getSupabase()
      .auth.getSession()
      .then(({ data }) => {
        if (cancelled) return;
        router.replace(
          data.session
            ? "/dashboard/knowledge"
            : "/login?error=oauth_cancelled",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-white text-sm text-neutral-500">
      Signing you in…
    </div>
  );
}
