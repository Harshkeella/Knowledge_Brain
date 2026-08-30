"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

/**
 * The auth provider, kept behind one module.
 *
 * Only the publishable anon key ever reaches the browser -- it identifies the
 * project, it does not authorise anything. The OAuth client secret lives in
 * the Supabase dashboard and is never shipped here; "Continue with Google"
 * hands off to Supabase's own redirect flow rather than doing OAuth itself.
 */
const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/**
 * True when the app is configured for hosted, multi-user auth. Unset locally,
 * which is what keeps `npm run dev` against a single-user backend working with
 * no Supabase project at all.
 */
export const authEnabled = Boolean(url && anonKey);

let client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (!authEnabled) {
    throw new Error(
      "Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and " +
        "NEXT_PUBLIC_SUPABASE_ANON_KEY, or run against a local backend with " +
        "AUTH_DISABLED=true.",
    );
  }
  // One instance per tab: each carries its own token-refresh timer and auth
  // listener, and a second would race the first on refresh.
  client ??= createBrowserClient(url!, anonKey!);
  return client;
}

/** Supabase's error text is for developers. This is what a person should read. */
export function friendlyAuthError(message: string): string {
  const text = message.toLowerCase();
  if (text.includes("invalid login credentials")) {
    return "Invalid email or password.";
  }
  if (text.includes("email not confirmed")) {
    return "This email is not verified yet. Check your inbox for the link.";
  }
  if (text.includes("already registered") || text.includes("already exists")) {
    return "That account already exists. Try signing in instead.";
  }
  if (text.includes("password") && text.includes("least")) {
    return "Password must be at least 8 characters.";
  }
  if (text.includes("rate limit") || text.includes("too many")) {
    return "Too many attempts. Wait a minute and try again.";
  }
  if (text.includes("failed to fetch") || text.includes("network")) {
    return "Unable to connect to the authentication service.";
  }
  return message;
}
