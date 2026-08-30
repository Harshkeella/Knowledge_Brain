"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import type { Session } from "@supabase/supabase-js";

import { setAccessTokenSource } from "@/lib/api";
import { authEnabled, getSupabase } from "@/lib/supabase";

type AuthState = {
  /** null while the session is still being restored from storage. */
  session: Session | null;
  loading: boolean;
  /** False when the app runs against a single-user backend (no Supabase). */
  enabled: boolean;
  email: string | null;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(authEnabled);

  useEffect(() => {
    if (!authEnabled) return;
    const supabase = getSupabase();

    // The API client asks for a token per request rather than being handed
    // one: the SDK refreshes in the background, and a token captured once
    // would be the stale one by the time a long chat stream reconnects.
    setAccessTokenSource(async () => {
      const { data } = await supabase.auth.getSession();
      return data.session?.access_token ?? null;
    });

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });

    const { data: subscription } = supabase.auth.onAuthStateChange(
      (event, next) => {
        setSession(next);
        setLoading(false);
        // A refresh that fails leaves the SDK signed out; bounce to login
        // rather than letting the dashboard 401 on every request.
        if (event === "SIGNED_OUT") router.replace("/login");
      },
    );
    return () => subscription.subscription.unsubscribe();
  }, [router]);

  const signOut = useCallback(async () => {
    if (!authEnabled) return;
    await getSupabase().auth.signOut();
    router.replace("/login");
  }, [router]);

  return (
    <AuthContext.Provider
      value={{
        session,
        loading,
        enabled: authEnabled,
        email: session?.user.email ?? null,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
